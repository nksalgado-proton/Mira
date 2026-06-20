"""Sync-pair picker dialog (Nelson 2026-05-20 v7 — supersedes the
two-QFileDialog flow that produced the "+11h" silent failure).

A resizable modal split in half. Each side: a "Pick photo…" button,
a large preview, and overlay text with the EXIF DateTimeOriginal.
Below the split, a live verdict row shows the pair-derived shift,
the declaration-derived expected shift (if the user provided a
``configured_tz``), and a clear pass/fail signal.

Behaviour:

* **Both photos picked** → recompute the verdict.
* **``configured_tz`` declared** → compute ``tz_expected =
  trip_tz − configured_tz``; show disagreement.
    * Within tolerance (30 min) → the pair is **accepted**, but the
      final offset returned is the *declaration-derived* value (not
      the raw pair diff). Reasoning: the user explicitly stated the
      camera's TZ; the pair confirms it; the residual seconds-or-
      minutes drift inside 30 min is best ignored (the camera's
      internal clock isn't synced to NTP, and "same moment" between
      two devices is fundamentally fuzzy).
    * Over tolerance → "Use this pair" button is disabled; the
      panel guides the user to pick different photos.
* **No ``configured_tz``** → snap the raw diff to the nearest
  15-minute multiple (covers every real-world UTC offset including
  +5:45 Nepal, -3:30 Newfoundland, +5:30 India). The final offset
  returned is the snapped value.

The returned ``CalibrationPair`` is *adjusted*: the reference_time
is set to ``camera_time + final_offset`` so the downstream engine's
``reference_time − camera_time`` math yields the chosen offset
verbatim. Loud comment marks this; the original photo files still
carry the unmodified EXIF.

RAW / video previews: ``QPixmap`` won't render them. The panel
falls back to a textual placeholder showing the filename + EXIF
timestamp.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.clock_calibration import CalibrationPair, snap_to_tz_offset
from core.exif_reader import read_exif_single
from core.fresh_source import camera_id_for
from mira.ui.i18n import tr  # ported into mira/ui (charter §4 step 7)

log = logging.getLogger(__name__)


# Default tolerance when a ``configured_tz`` is present: 30 minutes.
# Two devices recorded "same moment" by hand will rarely agree to
# the second; minutes of drift between device clocks (cameras
# aren't NTP-synced) eats this budget. 30 minutes catches obvious
# wrong-pair clicks (the +11h case) while accepting any reasonable
# user intent.
DEFAULT_TOLERANCE = timedelta(minutes=30)

# Media file filter — covers stills + videos. GoPro/Action cams
# routinely have more video than stills; the dialog accepts both.
_MEDIA_FILTER = (
    "Photos & videos (*.jpg *.jpeg *.rw2 *.raf *.arw *.nef *.cr2 "
    "*.cr3 *.dng *.orf *.pef *.heic *.heif *.tif *.tiff "
    "*.mp4 *.mov *.m4v *.lrv)"
)


def _fmt_offset(td: timedelta) -> str:
    """Format a timedelta as ``+H:MM`` / ``-H:MM:SS`` for the verdict
    row. Seconds are included only when non-zero so the user can see
    the full raw diff (e.g. ``+0:15:06`` vs ``+0:15``)."""
    total = int(td.total_seconds())
    sign = "+" if total >= 0 else "-"
    secs = abs(total)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if s:
        return f"{sign}{h:d}:{m:02d}:{s:02d}"
    return f"{sign}{h:d}:{m:02d}"


def _fmt_disagreement(td: timedelta) -> str:
    """Human-readable size of a disagreement (drops sign)."""
    secs = abs(int(td.total_seconds()))
    if secs < 60:
        return tr("{n}s").replace("{n}", str(secs))
    if secs < 3600:
        m, s = divmod(secs, 60)
        return tr("{m}min {s}s").replace("{m}", str(m)).replace("{s}", str(s))
    h, rem = divmod(secs, 3600)
    m = rem // 60
    return tr("{h}h {m}min").replace("{h}", str(h)).replace("{m}", str(m))


class _PhotoPanel(QFrame):
    """One half of the split — a "Pick photo…" button + preview
    area + filename/timestamp overlay text.

    ``picker_callback`` (optional) replaces the default ``QFileDialog``-based
    picker: callers pass a ``Callable[[QWidget], Optional[Path]]`` that
    presents a custom selector and returns the chosen path. When set, the
    ``expected_camera_id`` warning is skipped — the callback is expected
    to have pre-filtered to the right camera (Collect flow,
    Nelson 2026-06-09)."""

    def __init__(
        self,
        title: str,
        default_dir: str,
        expected_camera_id: Optional[str] = None,
        parent: QWidget | None = None,
        picker_callback=None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._default_dir = default_dir
        self._expected_camera_id = expected_camera_id
        self._picker_callback = picker_callback

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("PageHint")
        outer.addWidget(self._title_label)

        # Preview area — a QLabel that holds either a scaled pixmap
        # or a textual placeholder. Centered, expanding.
        self._preview = QLabel(tr("(no photo picked)"))
        self._preview.setObjectName("PreviewPane")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview.setMinimumHeight(220)
        outer.addWidget(self._preview, stretch=1)

        # Overlay text — filename + EXIF DateTimeOriginal.
        self._overlay = QLabel("")
        self._overlay.setObjectName("PageHint")
        self._overlay.setWordWrap(True)
        self._overlay.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self._overlay)

        self._pick_btn = QPushButton(tr("Pick photo…"))
        self._pick_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        outer.addWidget(self._pick_btn)

        self._path: Optional[Path] = None
        self._timestamp: Optional[datetime] = None
        # Hold the pixmap so resize events can re-scale without
        # re-reading disk.
        self._raw_pixmap: Optional[QPixmap] = None

    # ── Public state ──────────────────────────────────────────

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def timestamp(self) -> Optional[datetime]:
        return self._timestamp

    # ── Picker + preview ──────────────────────────────────────

    def open_picker(self, parent: QWidget) -> bool:
        """Open the picker; on selection, read EXIF + update preview.
        Returns True when a valid photo was picked (timestamp readable),
        False otherwise.

        When a ``picker_callback`` is configured (Collect flow), it
        supplies the path and the wrong-camera warning is skipped —
        the callback guarantees the picked photo is from the expected
        camera. Otherwise the default ``QFileDialog`` path runs and
        the warning fires when EXIF Make+Model disagrees with the
        side's expected camera."""
        if self._picker_callback is not None:
            picked = self._picker_callback(parent)
            if picked is None:
                return False
            return self._accept_picked_path(Path(picked), parent,
                                            check_camera=False)
        chosen, _ = QFileDialog.getOpenFileName(
            parent,
            tr("Pick photo or video"),
            self._default_dir,
            _MEDIA_FILTER,
        )
        if not chosen:
            return False
        return self._accept_picked_path(Path(chosen), parent,
                                        check_camera=True)

    def _accept_picked_path(
        self, path: Path, parent: QWidget, *, check_camera: bool,
    ) -> bool:
        """Common path handler — read EXIF, optionally warn on wrong
        camera, update preview + overlay. Returns True on success."""
        exif = read_exif_single(path)
        if exif is None or exif.timestamp is None:
            self._show_error(tr(
                "No readable EXIF timestamp.\n{name}"
            ).replace("{name}", path.name))
            self._path = None
            self._timestamp = None
            self._raw_pixmap = None
            return False
        if check_camera and self._expected_camera_id:
            detected = camera_id_for(exif.raw)
            if detected and detected != self._expected_camera_id:
                from PyQt6.QtWidgets import QMessageBox
                answer = QMessageBox.question(
                    parent,
                    tr("Wrong camera?"),
                    tr(
                        "This photo is from <b>{detected}</b>, but this "
                        "side expects <b>{expected}</b>.\n\n"
                        "Using a photo from the wrong camera will produce "
                        "an incorrect timezone offset.\n\n"
                        "Use it anyway?"
                    ).replace("{detected}", detected)
                     .replace("{expected}", self._expected_camera_id),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False
        self._path = path
        self._timestamp = exif.timestamp
        self._load_preview(path)
        self._overlay.setText(
            f"<b>{path.name}</b><br>"
            f"{exif.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self._overlay.setTextFormat(Qt.TextFormat.RichText)
        self._pick_btn.setText(tr("Pick a different photo…"))
        return True

    def _load_preview(self, path: Path) -> None:
        """Render the file through the shared orientation-correct loader. JPEG/HEIC/RAW
        succeed (upright — EXIF Orientation is applied; the old direct ``QPixmap(path)``
        ignored it, so rotated phone/camera shots showed sideways/upside-down — Nelson
        2026-06-01); video falls back to a textual placeholder."""
        from mira.ui.media.image_loader import load_pixmap

        pm = load_pixmap(path)
        if pm.isNull():
            self._raw_pixmap = None
            self._preview.setPixmap(QPixmap())
            self._preview.setText(tr(
                "(preview unavailable — video)\n"
                "Timestamp comes from EXIF below."
            ))
        else:
            self._raw_pixmap = pm
            self._rescale_preview()

    def resizeEvent(self, event) -> None:                # noqa: N802
        super().resizeEvent(event)
        self._rescale_preview()

    def _rescale_preview(self) -> None:
        if self._raw_pixmap is None or self._raw_pixmap.isNull():
            return
        size = self._preview.size()
        scaled = self._raw_pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview.setPixmap(scaled)

    def _show_error(self, text: str) -> None:
        self._raw_pixmap = None
        self._preview.setPixmap(QPixmap())
        self._preview.setText(text)
        self._overlay.setText("")


class SyncPairPickerDialog(QDialog):
    """Resizable two-panel dialog for picking a synchronisation
    pair. Compare two photos side-by-side with a live verdict on
    whether the pair agrees with the declared ``configured_tz``."""

    def __init__(
        self,
        *,
        camera_id: str,
        reference_id: str,
        camera_default_dir: str,
        reference_default_dir: str,
        trip_tz: float,
        configured_tz: Optional[float],
        tolerance: timedelta = DEFAULT_TOLERANCE,
        parent: QWidget | None = None,
        cam_picker_callback=None,
        ref_picker_callback=None,
    ) -> None:
        """``cam_picker_callback`` / ``ref_picker_callback`` (optional,
        Collect flow) — each a ``Callable[[QWidget], Optional[Path]]``
        that replaces ``QFileDialog`` for that side and is expected to
        have pre-filtered to the right camera (so the wrong-camera
        warning is skipped). Legacy callers omit both and get the
        original QFileDialog behavior."""
        super().__init__(parent)
        self.setWindowTitle(tr("Sync pair — {cam} ↔ {ref}").replace(
            "{cam}", camera_id).replace("{ref}", reference_id))
        self.setMinimumSize(900, 600)
        self.resize(1100, 720)
        self._camera_id = camera_id
        self._reference_id = reference_id
        self._trip_tz = trip_tz
        self._configured_tz = configured_tz
        self._tolerance = tolerance

        outer = QVBoxLayout(self)

        # Intro / context. The 15-minute requirement is a hard rule
        # (Nelson 2026-05-22): without a declared ``configured_tz``,
        # the dialog snaps the raw delta to the nearest 15-minute
        # multiple — so any elapsed time longer than ~7.5 min between
        # the two shots can push the snap into the wrong bucket
        # silently. State it loudly upfront.
        intro = QLabel(tr(
            "<b>Pick one photo (or video) on each side, taken within "
            "15 minutes of each other at most.</b><br>"
            "The closer in time the two shots are, the more accurate "
            "the derived timezone offset. The verdict below shows the "
            "resulting offset and whether it agrees with the timezone "
            "you declared for this camera."
        ))
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # Side-by-side split, resizable mid-divider.
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._cam_panel = _PhotoPanel(
            tr("Camera — {cam}").replace("{cam}", camera_id),
            camera_default_dir,
            expected_camera_id=camera_id,
            parent=splitter,
            picker_callback=cam_picker_callback,
        )
        self._ref_panel = _PhotoPanel(
            tr("Reference — {ref}").replace("{ref}", reference_id),
            reference_default_dir,
            expected_camera_id=reference_id,
            parent=splitter,
            picker_callback=ref_picker_callback,
        )
        splitter.addWidget(self._cam_panel)
        splitter.addWidget(self._ref_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, stretch=1)

        # Wire the panel buttons.
        self._cam_panel._pick_btn.clicked.connect(self._on_cam_pick)
        self._ref_panel._pick_btn.clicked.connect(self._on_ref_pick)

        # Verdict row.
        self._verdict = QLabel("")
        self._verdict.setObjectName("PageHint")
        self._verdict.setTextFormat(Qt.TextFormat.RichText)
        self._verdict.setWordWrap(True)
        self._verdict.setMinimumHeight(56)
        outer.addWidget(self._verdict)

        # Buttons.
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel, parent=self)
        self._use_btn = QPushButton(tr("Use this pair"))
        self._use_btn.setEnabled(False)
        self._use_btn.setDefault(True)
        self._use_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._use_btn.clicked.connect(self.accept)
        self._buttons.addButton(
            self._use_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self._buttons.rejected.connect(self.reject)
        outer.addWidget(self._buttons)

        self._final_offset: Optional[timedelta] = None
        self._update_verdict()

    # ── Pick handlers ─────────────────────────────────────────

    def _on_cam_pick(self) -> None:
        if self._cam_panel.open_picker(self):
            self._update_verdict()

    def _on_ref_pick(self) -> None:
        if self._ref_panel.open_picker(self):
            self._update_verdict()

    # ── Verdict computation ──────────────────────────────────

    def _update_verdict(self) -> None:
        """Recompute the verdict row + enable/disable the Use
        button. The decision tree:

        1. Either photo missing → blank row, button disabled.
        2. Both present + ``configured_tz`` is set:
           * compute raw diff and expected diff;
           * within tolerance → green verdict, final = expected,
             button enabled;
           * over tolerance → red verdict, button disabled.
        3. Both present + no ``configured_tz``:
           * snap raw to nearest 15-min multiple;
           * verdict shows "Snap +H:MM"; button enabled.
        """
        cam_t = self._cam_panel.timestamp
        ref_t = self._ref_panel.timestamp
        if cam_t is None or ref_t is None:
            self._verdict.setText(tr(
                "Pick one photo on each side to see the verdict."
            ))
            self._use_btn.setEnabled(False)
            self._final_offset = None
            return

        raw = ref_t - cam_t

        if self._configured_tz is not None:
            tz_expected = timedelta(
                hours=(self._trip_tz - self._configured_tz))
            disagree = abs(raw - tz_expected)
            within = disagree <= self._tolerance
            color = "#16a34a" if within else "#dc2626"  # green / red
            ok_str = tr("within tolerance ({tol})").replace(
                "{tol}", _fmt_disagreement(self._tolerance))
            bad_str = tr("OVER tolerance ({tol}) — pair likely "
                         "not simultaneous").replace(
                "{tol}", _fmt_disagreement(self._tolerance))
            verdict_label = ok_str if within else bad_str
            self._verdict.setText(
                f"Δ raw: <b>{_fmt_offset(raw)}</b><br>"
                f"Expected ({_fmt_tz(self._configured_tz)} → "
                f"{_fmt_tz(self._trip_tz)}): "
                f"<b>{_fmt_offset(tz_expected)}</b><br>"
                f"Disagreement: "
                f"<b>{_fmt_disagreement(raw - tz_expected)}</b> · "
                f"<span style='color:{color}; font-weight:bold;'>"
                f"{verdict_label}</span><br>"
                + (tr("Final offset: <b>{exp}</b> (from declaration)")
                   .replace("{exp}", _fmt_offset(tz_expected))
                   if within else
                   tr("→ Pick different photos or Cancel."))
            )
            if within:
                self._use_btn.setEnabled(True)
                self._final_offset = tz_expected
            else:
                self._use_btn.setEnabled(False)
                self._final_offset = None
        else:
            snapped = snap_to_tz_offset(raw)
            snap_diff = abs(raw - snapped)
            # If snap_diff is large the user likely picked photos
            # that weren't taken at the same moment — the snap can
            # silently push them into the wrong 15-min bucket. Warn
            # loudly above ~5 minutes (Nelson 2026-05-22: "photos
            # have to be within 15 minutes of each other, at most").
            warn = snap_diff > timedelta(minutes=5)
            verdict_html = (
                tr(
                    "Δ raw: <b>{raw}</b><br>"
                    "No timezone declared for this camera — snapping "
                    "to nearest 15-min multiple.<br>"
                    "Snap: <b>{snap}</b> · "
                    "photos within <b>{diff}</b> of each other in real time"
                ).replace("{raw}", _fmt_offset(raw))
                 .replace("{snap}", _fmt_offset(snapped))
                 .replace("{diff}", _fmt_disagreement(snap_diff))
            )
            if warn:
                verdict_html += (
                    "<br><span style='color:#d97706; font-weight:bold;'>"
                    + tr(
                        "⚠ The two photos are more than 5 minutes "
                        "apart in real time. Pick a closer pair — "
                        "the further apart they are, the greater the "
                        "risk of snapping to the wrong timezone bucket."
                    )
                    + "</span>"
                )
            self._verdict.setText(verdict_html)
            self._use_btn.setEnabled(True)
            self._final_offset = snapped

    # ── Result ─────────────────────────────────────────────────

    def selected_pair(self) -> Optional[CalibrationPair]:
        """Return the accepted pair with ``reference_time``
        **adjusted** so the engine's ``reference_time − camera_time``
        math yields ``self._final_offset`` verbatim. The original
        EXIF times on disk are NOT modified — only the in-memory
        timestamps in the returned dataclass."""
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        if (self._cam_panel.path is None or self._ref_panel.path is None
                or self._cam_panel.timestamp is None
                or self._final_offset is None):
            return None
        # Adjust reference_time so engine math yields final_offset.
        adjusted_ref = self._cam_panel.timestamp + self._final_offset
        return CalibrationPair(
            camera_path=self._cam_panel.path,
            reference_path=self._ref_panel.path,
            camera_time=self._cam_panel.timestamp,
            reference_time=adjusted_ref,
        )


def _fmt_tz(value: float) -> str:
    """Format a TZ float as ``UTC±H:MM``."""
    sign = "+" if value >= 0 else "-"
    abs_v = abs(value)
    h = int(abs_v)
    m = int(round((abs_v - h) * 60))
    if m == 0:
        return f"UTC{sign}{h}"
    return f"UTC{sign}{h}:{m:02d}"
