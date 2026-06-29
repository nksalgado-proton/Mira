"""Attach / replace / remove the day or event map (spec/155).

A small modal opened by the schedule-row chip and the event-header chip.
Shows the currently-attached image (or a "no map yet" placeholder) at
the top, the on-disk path + dimensions + size below, and a footer with
``Replace…`` / ``Remove`` / ``Close`` (or ``Pick image…`` / ``Close``
when empty).

The dialog owns the gateway interaction: ``Replace…`` opens a file
picker, calls ``EventGateway.attach_{day,event}_map``, refreshes the
preview, and emits :attr:`mapChanged`. ``Remove`` confirms then calls
``clear_{day,event}_map`` and emits the same signal. Callers listen to
:attr:`mapChanged` to re-polish the chip in place — no full page
reload.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.path_builder import (
    MAP_MEDIA_EXTENSIONS,
    MAP_VIDEO_THUMB_SUFFIX,
    is_video_map_path,
    maps_dir,
)
from mira.ui.design.buttons import (
    danger_ghost_button,
    ghost_button,
    primary_button,
)
from mira.ui.design.dialogs import confirm_destructive


def tr(s: str) -> str:
    """Pass-through translator hook — replace with the real ``tr()``
    once the i18n harness reaches this surface."""
    return QApplication.translate("MapAttachDialog", s)


_PREVIEW_MAX_W = 420
_PREVIEW_MAX_H = 260


class MapAttachDialog(QDialog):
    """One modal, two modes:

    * **day mode** — pass ``day_number``; binds to ``trip_day.map_image_path``
    * **event mode** — pass ``day_number=None``; binds to ``event.map_image_path``

    The gateway must have ``event_root`` set (the dialog reads files
    directly from disk for the preview, not through the gateway).
    """

    mapChanged = pyqtSignal()

    def __init__(
        self,
        gateway,
        *,
        day_number: Optional[int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self._day_number = day_number
        if gateway.event_root is None:
            raise RuntimeError(
                "MapAttachDialog needs a gateway with a resolvable event_root")
        self._event_root = Path(gateway.event_root)
        self.setObjectName("MapAttachDialog")
        self.setWindowTitle(self._title_text())
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        self._preview = QLabel(self)
        self._preview.setObjectName("MapAttachPreview")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumSize(_PREVIEW_MAX_W, _PREVIEW_MAX_H)
        self._preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._preview)

        self._meta = QLabel(self)
        self._meta.setObjectName("MapAttachMeta")
        self._meta.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._meta)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._pick_button = primary_button(tr("Pick image…"), self)
        self._pick_button.clicked.connect(self._on_pick)
        self._replace_button = ghost_button(tr("Replace…"), self)
        self._replace_button.clicked.connect(self._on_pick)
        self._remove_button = danger_ghost_button(tr("Remove"), self)
        self._remove_button.clicked.connect(self._on_remove)
        self._close_button = ghost_button(tr("Close"), self)
        self._close_button.clicked.connect(self.accept)

        footer.addWidget(self._pick_button)
        footer.addWidget(self._replace_button)
        footer.addWidget(self._remove_button)
        footer.addStretch(1)
        footer.addWidget(self._close_button)
        layout.addLayout(footer)

        self._refresh()

    # ── public API used by tests / callers ────────────────────────

    def current_relpath(self) -> Optional[str]:
        if self._day_number is None:
            return self._gateway.get_event_map_path()
        return self._gateway.get_day_map_path(self._day_number)

    # ── internals ─────────────────────────────────────────────────

    def _title_text(self) -> str:
        if self._day_number is None:
            return tr("Event map")
        return tr("Day {n} map").replace("{n}", str(self._day_number))

    def _refresh(self) -> None:
        """Re-render preview + meta + button visibility for the current state."""
        rel = self.current_relpath()
        attached = rel is not None
        if attached:
            self._render_preview(rel)  # type: ignore[arg-type]
            self._pick_button.setVisible(False)
            self._replace_button.setVisible(True)
            self._remove_button.setVisible(True)
        else:
            self._render_empty()
            self._pick_button.setVisible(True)
            self._replace_button.setVisible(False)
            self._remove_button.setVisible(False)

    def _render_empty(self) -> None:
        self._preview.setPixmap(QPixmap())
        self._preview.setText(tr("No map attached."))
        self._preview.setProperty("attached", False)
        self._meta.setText("")
        self._reapply_style(self._preview)
        self._reapply_style(self._meta)

    def _render_preview(self, rel: str) -> None:
        abs_path = self._event_root / rel
        is_video = is_video_map_path(rel)
        if is_video:
            # MP4 preview source = the first-frame sidecar the gateway
            # wrote on attach; cheaper + sync vs. running ffmpeg.
            sidecar = abs_path.with_suffix(
                abs_path.suffix + MAP_VIDEO_THUMB_SUFFIX)
            pix = QPixmap(str(sidecar))
        else:
            pix = QPixmap(str(abs_path))
        if pix.isNull():
            self._preview.setText(
                tr("Preview unavailable for {p}.").replace("{p}", rel))
            self._meta.setText("")
            return
        scaled = pix.scaled(
            _PREVIEW_MAX_W, _PREVIEW_MAX_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview.setPixmap(scaled)
        self._preview.setText("")
        self._preview.setProperty("attached", True)
        self._reapply_style(self._preview)
        # Meta line for images: "Maps/day-02.jpg · 1280 × 720 · 184 KB"
        # For MP4: "Maps/day-02.mp4 · MP4 · 4 s · 184 KB"
        if is_video:
            try:
                from core.video_extract import probe_video
                meta = probe_video(abs_path)
                duration_s = meta.duration_ms // 1000
            except Exception:                                       # noqa: BLE001
                duration_s = 0
            size_kb = max(1, abs_path.stat().st_size // 1024) if abs_path.exists() else 0
            self._meta.setText(
                f"{rel} · {tr('MP4')} · {duration_s} s · {size_kb:,} KB")
        else:
            size_kb = max(1, abs_path.stat().st_size // 1024) if abs_path.exists() else 0
            self._meta.setText(
                f"{rel} · {pix.width()} × {pix.height()} · {size_kb:,} KB")

    @staticmethod
    def _reapply_style(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def _on_pick(self) -> None:
        # Filter string Qt expects: "JPEG / PNG (*.jpg *.jpeg *.png)"
        filt = tr("Map (image or video)") + " (" + " ".join(
            f"*{ext}" for ext in MAP_MEDIA_EXTENSIONS) + ")"
        chosen, _ = QFileDialog.getOpenFileName(
            self, self._title_text(), str(self._suggested_start_dir()), filt)
        if not chosen:
            return
        try:
            if self._day_number is None:
                self._gateway.attach_event_map(chosen)
            else:
                self._gateway.attach_day_map(self._day_number, chosen)
        except ValueError as exc:
            # Wrong extension etc. — surface a clean message.
            from mira.ui.design.dialogs import show_error
            show_error(self, tr("Couldn't attach map"), str(exc))
            return
        self._refresh()
        self.mapChanged.emit()

    def _on_remove(self) -> None:
        ok = confirm_destructive(
            self,
            tr("Remove map?"),
            (tr("Remove the map for day {n}?").replace(
                "{n}", str(self._day_number))
             if self._day_number is not None
             else tr("Remove the event map?")),
            primary_text=tr("Remove"),
        )
        if not ok:
            return
        if self._day_number is None:
            self._gateway.clear_event_map()
        else:
            self._gateway.clear_day_map(self._day_number)
        self._refresh()
        self.mapChanged.emit()

    def _suggested_start_dir(self) -> Path:
        """Open the picker in the Maps/ folder if it exists, otherwise
        the event root — both are more useful starting points than the
        user's Documents folder."""
        d = maps_dir(self._event_root)
        return d if d.is_dir() else self._event_root
