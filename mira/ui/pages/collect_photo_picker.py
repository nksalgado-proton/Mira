"""``CollectPhotoPicker`` — dedicated single-photo picker for the Collect
TZ-calibration flow (Nelson 2026-06-09).

The legacy :class:`~mira.ui.base.sync_pair_picker.SyncPairPickerDialog`
uses ``QFileDialog`` to pick each side's photo. That worked when the source
was organised by per-camera subfolders, but the new Collect flow scans a
**flat** source folder and identifies cameras by EXIF Make+Model — there
are no per-camera directories the file dialog can scope to. Picking "one
photo from camera X" out of an unorganised tree is impractical.

This dialog solves that by accepting a pre-filtered per-day photo map and
presenting two short stages:

* **Stage 1 — day list.** One row per day with formatted plan info
  (Day N · date · location · description) when ``day_labels`` is provided,
  else a bare "YYYY-MM-DD — N photo(s)" fallback. Click → stage 2.
* **Stage 2 — thumbnail grid + preview pane.** A horizontal split: small
  thumbnails on the left, a large preview on the right with the highlighted
  photo's filename + EXIF timestamp + "Use this photo" button. Click a
  thumb to preview; double-click OR Enter on the highlight commits.

Single-selection only. There is no multi-pick — that's the explicit
distinction from the multi-select Pick surfaces (the redesigned Picker /
Video Picker / Quick Sweep), all built for batch decisions.

Performance for 2k-photo-day scans (Nelson 2026-06-09 eyeball #2):
* Videos are filtered out (the picker is for stills).
* Files above ``_MAX_FILE_BYTES`` are filtered (oversized RAWs are skipped
  for thumbnail performance — they remain visible in the picker only when
  the size threshold is loosened in a future setting).
* Thumbnails load lazily through a single ``QTimer`` (one batch per tick,
  the FIFO queue pattern from :mod:`mira.ui.picked.quick_sweep_page`)
  so the grid is interactive immediately and decodes scroll in as the user
  scans.

The caller (TZ-calibration in :mod:`mira.ui.shell.main_window`)
filters :attr:`core.scan_source.ScanResult.per_photo_records` to one
``camera_id`` and groups by day before opening — that's where EXIF-based
camera filtering happens, so this dialog stays pure-presentation.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QCursor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.exif_reader import read_exif_single
from mira.ui.base.surface import back_button
from mira.ui.i18n import tr
from mira.ui.media.image_loader import load_pixmap

log = logging.getLogger(__name__)


_THUMB_PX = 180
_PREVIEW_W = 480
_PREVIEW_H = 360
_STAGE_DAYS = 0
_STAGE_THUMBS = 1

# Skip files this large or larger — RAWs above ~30MB take seconds to decode
# and are not what the user is hunting for in a calibration pair-pick (a
# normal JPEG/RAW is well under this threshold).
_MAX_FILE_BYTES = 30 * 1024 * 1024
# Video extensions are excluded from the grid — pair-pick uses stills.
_VIDEO_EXTS = frozenset({
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts",
})

# Lazy-thumbnail loader pacing — mirrors quick_sweep_page constants.
_THUMB_TIMER_MS = 30
_THUMBS_PER_TICK = 4


def _app_style():
    """``QApplication.style()`` guarded so this module imports cleanly in
    headless / test contexts where no QApplication has been built yet."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        return app.style() if app else None
    except Exception:                                       # noqa: BLE001
        return None


def _placeholder_icon() -> QIcon:
    """A neutral file icon used as the thumbnail placeholder until the
    lazy loader replaces it with the decoded preview."""
    style = _app_style()
    if style is None:
        return QIcon()
    return style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)


class CollectPhotoPicker(QDialog):
    """Two-stage single-photo picker filtered by camera.

    ``photos_by_day`` maps a calendar date to the ordered list of source
    paths for that day. Empty days (no paths) are skipped. After
    ``exec()`` returns ``QDialog.DialogCode.Accepted``, ``selected_path``
    holds the chosen ``Path``; ``None`` on cancel.

    ``day_labels`` (optional) — per-date pre-formatted display string for
    the day-list row. When omitted, the dialog falls back to
    ``"YYYY-MM-DD — N photo(s)"``. The caller is expected to render plan
    info (Day number · date · location · description) into the label so
    it stays consistent with the surrounding flow's wording."""

    def __init__(
        self,
        *,
        camera_id: str,
        photos_by_day: Dict[date, List[Path]],
        title: Optional[str] = None,
        day_labels: Optional[Dict[date, str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            title
            or tr("Pick a photo from {cam}").replace("{cam}", camera_id)
        )
        self.setModal(True)
        self.resize(1200, 760)
        self._camera_id = camera_id
        # Drop empty days early so the day-list view stays meaningful.
        self._photos_by_day: Dict[date, List[Path]] = {
            d: list(paths)
            for d, paths in photos_by_day.items() if paths
        }
        self._day_labels: Dict[date, str] = dict(day_labels or {})
        self._selected_path: Optional[Path] = None
        # Stage-2 state. ``_current_thumb_paths[i]`` is the path the i-th
        # grid row represents — used for preview lookups + the lazy
        # thumbnail loader.
        self._current_thumb_paths: List[Path] = []
        self._thumb_pixmap_cache: Dict[Path, QPixmap] = {}
        self._thumb_pending: List[tuple] = []                # (idx, Path)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(_THUMB_TIMER_MS)
        self._thumb_timer.timeout.connect(self._load_some_thumbs)
        self._placeholder = _placeholder_icon()
        self._build_ui()

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def selected_path(self) -> Optional[Path]:
        """Chosen file path, or ``None`` if the dialog was cancelled."""
        return self._selected_path

    # ── UI scaffold ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_day_stage())
        self._stack.addWidget(self._build_thumb_stage())
        layout.addWidget(self._stack, stretch=1)

        if not self._photos_by_day:
            self._stack.hide()
            empty = QLabel(tr(
                "No photos from {cam} in the scan — pick a different "
                "camera or use \"I know the timezone\" instead."
            ).replace("{cam}", self._camera_id))
            empty.setObjectName("PageHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            layout.addWidget(empty, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_day_stage(self) -> QWidget:
        widget = QWidget()
        vbox = QVBoxLayout(widget)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(6)

        header = QLabel(tr(
            "{cam} — pick the day the matching photo was taken."
        ).replace("{cam}", self._camera_id))
        header.setObjectName("PageHint")
        header.setWordWrap(True)
        vbox.addWidget(header)

        self._day_list = QListWidget()
        self._day_list.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._day_list.setWordWrap(True)
        # Bigger rows — multi-line labels need vertical breathing room.
        self._day_list.setSpacing(2)
        for d in sorted(self._photos_by_day.keys()):
            count = len(self._photos_by_day[d])
            text = self._day_labels.get(d) or tr(
                "{date} — {n} photo(s)"
            ).replace("{date}", d.isoformat()).replace("{n}", str(count))
            # Always append a count summary if the caller's label didn't
            # already include one (the caller usually formats it richly).
            if "{n}" in text or "photo" in text.lower():
                final_text = text
            else:
                final_text = tr("{label} · {n} photo(s)") \
                    .replace("{label}", text) \
                    .replace("{n}", str(count))
            item = QListWidgetItem(final_text)
            item.setData(Qt.ItemDataRole.UserRole, d)
            self._day_list.addItem(item)
        self._day_list.itemActivated.connect(self._on_day_chosen)
        self._day_list.itemClicked.connect(self._on_day_chosen)
        vbox.addWidget(self._day_list, stretch=1)

        return widget

    def _build_thumb_stage(self) -> QWidget:
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        head_row = QHBoxLayout()
        self._back_btn = back_button()
        self._back_btn.clicked.connect(self._on_back_to_days)
        head_row.addWidget(self._back_btn)
        self._thumb_header = QLabel("")
        self._thumb_header.setObjectName("PageHint")
        self._thumb_header.setWordWrap(True)
        head_row.addWidget(self._thumb_header, stretch=1)
        outer.addLayout(head_row)

        # Horizontal split: grid + preview.
        self._split = QSplitter(Qt.Orientation.Horizontal)

        # Left — thumbnail grid.
        self._thumb_list = QListWidget()
        self._thumb_list.setViewMode(QListView.ViewMode.IconMode)
        self._thumb_list.setIconSize(QSize(_THUMB_PX, _THUMB_PX))
        self._thumb_list.setGridSize(QSize(_THUMB_PX + 24, _THUMB_PX + 40))
        self._thumb_list.setResizeMode(QListView.ResizeMode.Adjust)
        self._thumb_list.setMovement(QListView.Movement.Static)
        self._thumb_list.setUniformItemSizes(True)
        self._thumb_list.setWordWrap(True)
        self._thumb_list.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor)
        )
        self._thumb_list.currentItemChanged.connect(
            self._on_thumb_highlight_changed
        )
        self._thumb_list.itemActivated.connect(self._on_thumb_chosen)
        # Double-click commits; single-click only updates the preview.
        self._thumb_list.itemDoubleClicked.connect(self._on_thumb_chosen)
        self._split.addWidget(self._thumb_list)

        # Right — preview pane.
        preview_wrap = QWidget()
        pvbox = QVBoxLayout(preview_wrap)
        pvbox.setContentsMargins(12, 0, 0, 0)
        pvbox.setSpacing(8)

        self._preview = QLabel(tr(
            "Click a thumbnail to preview it here."
        ))
        self._preview.setObjectName("PreviewPane")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumSize(_PREVIEW_W, _PREVIEW_H)
        self._preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        pvbox.addWidget(self._preview, stretch=1)

        self._preview_meta = QLabel("")
        self._preview_meta.setObjectName("PageHint")
        self._preview_meta.setWordWrap(True)
        self._preview_meta.setTextFormat(Qt.TextFormat.RichText)
        self._preview_meta.setMinimumHeight(48)
        pvbox.addWidget(self._preview_meta)

        self._use_btn = QPushButton(tr("Use this photo"))
        self._use_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor)
        )
        self._use_btn.setEnabled(False)
        self._use_btn.clicked.connect(self._on_use_current)
        pvbox.addWidget(self._use_btn)

        self._split.addWidget(preview_wrap)
        # Roughly 60/40 split — grid gets the bigger half.
        self._split.setStretchFactor(0, 3)
        self._split.setStretchFactor(1, 2)
        outer.addWidget(self._split, stretch=1)

        return widget

    # ── Stage transitions ───────────────────────────────────────────────

    def _on_day_chosen(self, item: QListWidgetItem) -> None:
        d = item.data(Qt.ItemDataRole.UserRole)
        if d is None:
            return
        self._populate_thumbs(d)
        self._stack.setCurrentIndex(_STAGE_THUMBS)

    def _on_back_to_days(self) -> None:
        # Stop in-flight thumbnail decoding so the user isn't paying for
        # work they no longer see.
        self._thumb_timer.stop()
        self._thumb_pending.clear()
        self._stack.setCurrentIndex(_STAGE_DAYS)

    # ── Stage 2 population + filtering ──────────────────────────────────

    def _populate_thumbs(self, d: date) -> None:
        paths = self._filtered_paths_for_day(d)
        self._current_thumb_paths = paths
        self._thumb_pending.clear()
        self._thumb_timer.stop()
        self._reset_preview()

        self._thumb_header.setText(self._thumb_header_text(d, len(paths)))
        self._thumb_list.clear()

        for idx, p in enumerate(paths):
            item = QListWidgetItem(p.name)
            cached = self._thumb_pixmap_cache.get(p)
            if cached is not None and not cached.isNull():
                item.setIcon(QIcon(cached))
            else:
                item.setIcon(self._placeholder)
                self._thumb_pending.append((idx, p))
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(str(p))
            self._thumb_list.addItem(item)

        if self._thumb_pending:
            self._thumb_timer.start()
        # Highlight the first photo so the preview pane has something to
        # show immediately.
        if self._thumb_list.count() > 0:
            self._thumb_list.setCurrentRow(0)

    def _filtered_paths_for_day(self, d: date) -> List[Path]:
        """Drop videos and oversized files. The caller already filtered
        by camera at the source-data level; this filter is purely for
        grid performance + relevance (pair-pick uses stills)."""
        out: List[Path] = []
        for p in self._photos_by_day.get(d, []):
            if p.suffix.lower() in _VIDEO_EXTS:
                continue
            try:
                if p.stat().st_size >= _MAX_FILE_BYTES:
                    continue
            except OSError:
                # Stat failure (file gone, permission denied) — skip so
                # the user never sees a thumbnail that can't load.
                continue
            out.append(p)
        return out

    def _thumb_header_text(self, d: date, shown_count: int) -> str:
        total_count = len(self._photos_by_day.get(d, []))
        skipped = total_count - shown_count
        if skipped > 0:
            return tr(
                "{cam} — {date} — pick one photo "
                "({shown} shown, {skipped} videos/large files skipped)."
            ).replace("{cam}", self._camera_id) \
             .replace("{date}", d.isoformat()) \
             .replace("{shown}", str(shown_count)) \
             .replace("{skipped}", str(skipped))
        return tr(
            "{cam} — {date} — pick one photo ({n} available)."
        ).replace("{cam}", self._camera_id) \
         .replace("{date}", d.isoformat()) \
         .replace("{n}", str(shown_count))

    # ── Lazy thumbnail loader ───────────────────────────────────────────

    def _load_some_thumbs(self) -> None:
        """Pop up to ``_THUMBS_PER_TICK`` items from the queue, decode,
        cache, and set the icon on the matching grid cell. The
        single-timer FIFO pattern mirrors
        :mod:`mira.ui.picked.quick_sweep_page`."""
        if self._stack.currentIndex() != _STAGE_THUMBS:
            # User navigated away — flush.
            self._thumb_pending.clear()
            self._thumb_timer.stop()
            return
        done = 0
        while self._thumb_pending and done < _THUMBS_PER_TICK:
            idx, path = self._thumb_pending.pop(0)
            cached = self._thumb_pixmap_cache.get(path)
            if cached is None or cached.isNull():
                pm = load_pixmap(path)
                if pm is None or pm.isNull():
                    continue
                scaled = pm.scaled(
                    _THUMB_PX, _THUMB_PX,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._thumb_pixmap_cache[path] = scaled
                cached = scaled
            if idx < self._thumb_list.count():
                item = self._thumb_list.item(idx)
                if item is not None:
                    item.setIcon(QIcon(cached))
            done += 1
        if not self._thumb_pending:
            self._thumb_timer.stop()

    # ── Preview pane ────────────────────────────────────────────────────

    def _reset_preview(self) -> None:
        self._preview.setPixmap(QPixmap())
        self._preview.setText(tr("Click a thumbnail to preview it here."))
        self._preview_meta.setText("")
        self._use_btn.setEnabled(False)

    def _on_thumb_highlight_changed(
        self,
        current: Optional[QListWidgetItem],
        _previous: Optional[QListWidgetItem],
    ) -> None:
        if current is None:
            self._reset_preview()
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(path, Path):
            self._reset_preview()
            return
        self._show_preview(path)

    def _show_preview(self, path: Path) -> None:
        """Render the highlighted photo at the preview pane size + display
        filename and EXIF DateTimeOriginal.

        ``load_pixmap`` is synchronous and on a large RAW costs 10–20s
        (Nelson 2026-06-18). We can't move it off the GUI thread without
        a bigger refactor, but we CAN make the wait legible: show a
        "Loading…" message + wait cursor BEFORE the call so the user
        sees their click registered. A monotonic request id discards
        stale results when the user keeps clicking thumbs during a slow
        decode (a fast click on a JPEG mustn't be overwritten by the
        late-arriving RW2 the user already moved past)."""
        self._preview_request_id = getattr(self, "_preview_request_id", 0) + 1
        request_id = self._preview_request_id

        # Wait state — visible *before* the blocking decode starts. The
        # processEvents call forces the paint to land; without it the
        # label change queues behind the (next) blocking work.
        self._preview.setPixmap(QPixmap())
        self._preview.setText(
            tr("Loading {name}…").replace("{name}", path.name)
        )
        self._preview_meta.setText("")
        self._use_btn.setEnabled(False)
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        QApplication.processEvents()

        try:
            pm = load_pixmap(path)
        finally:
            QApplication.restoreOverrideCursor()

        # User moved on while we were decoding — drop the stale result.
        if request_id != self._preview_request_id:
            return

        if pm is None or pm.isNull():
            self._preview.setPixmap(QPixmap())
            self._preview.setText(tr("(preview unavailable)\n{name}")
                                  .replace("{name}", path.name))
        else:
            size = self._preview.size()
            target_w = max(size.width() - 8, _PREVIEW_W)
            target_h = max(size.height() - 8, _PREVIEW_H)
            scaled = pm.scaled(
                target_w, target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._preview.setPixmap(scaled)
            self._preview.setText("")
        # EXIF read is single-file + cheap; safe to do on every highlight.
        ts_text = ""
        try:
            exif = read_exif_single(path)
            if exif is not None and exif.timestamp is not None:
                ts: datetime = exif.timestamp
                ts_text = ts.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:                                   # noqa: BLE001
            ts_text = ""
        meta_html = f"<b>{path.name}</b>"
        if ts_text:
            meta_html += f"<br>{ts_text}"
        self._preview_meta.setText(meta_html)
        self._use_btn.setEnabled(True)

    # ── Commit ──────────────────────────────────────────────────────────

    def _on_use_current(self) -> None:
        item = self._thumb_list.currentItem()
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, Path):
            self._selected_path = path
            self.accept()

    def _on_thumb_chosen(self, item: QListWidgetItem) -> None:
        """Double-click / Enter on a thumbnail = commit immediately."""
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, Path):
            self._selected_path = path
            self.accept()
