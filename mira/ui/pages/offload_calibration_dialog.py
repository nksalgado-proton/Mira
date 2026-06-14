"""Offload calibration dialog — single-camera offset prompt (spec/13).

PORTED verbatim from legacy ``ui/pages/offload_calibration_dialog.py`` (charter §0/§5.2 —
pure UI; only the ``tr`` import is rebased). ``calibration_offset_for_offload`` reads its
``settings`` dict (calibration_mode / saved_camera_offsets) — the caller passes the
gateway settings as a dict. Only fires on the sidebar "Back up SD card" path; the Capture
phase always supplies a precomputed offset (F-019), so this dialog is skipped there.

Captures the timezone offset for the camera in a single-camera
offload, right after verify and before the wipe gate. Drives the
bake step that rewrites EXIF in ``Original Media/`` to TZ-correct
local times.

Why a single-camera dialog (vs. the multi-camera CameraClockDialog
the culler uses): each "Back up this card" invocation copies ONE
camera_id's files — the offload UI already enforces this. So
calibration here only needs to deal with one offset, not a per-camera
roster. Keeps the surface light.

UX semantics:

* Pre-fills from ``settings.saved_camera_offsets[camera_id]`` when
  ``calibration_mode == "saved"`` and a value exists for this camera.
* Numeric spinbox (-23.0 to +23.0 hours, step 0.25). Suffix is "h".
* "Remember this offset for next time" checkbox — writes the offset
  to ``saved_camera_offsets`` on OK.
* Offset 0 = "camera time is already correct" — bake step is a no-op
  (skipped to avoid pointless EXIF writes).
* Cancel = "I'll calibrate later via Adjust event TZ" — bake skipped,
  but the offload still completes (the files are in 00-Captured with
  uncorrected EXIF; the user knows what they're doing).

This dialog is for live-card offload only. Past-photos imports keep
their existing pair-picker (sync_pair_picker) because they may have
multiple cameras in one ingest. A future unification can fold both
into a single calibration surface.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from mira.ui.i18n import tr

log = logging.getLogger(__name__)


class OffloadCalibrationDialog(QDialog):
    """Single-camera offset capture for the live-card offload flow.

    Public API:
      :meth:`offset_hours` — the numeric offset the user chose
        (post-OK only).
      :meth:`remember` — True if the user ticked "remember this".
    """

    def __init__(
        self,
        camera_id: str,
        *,
        initial_offset: float = 0.0,
        prefilled_from_saved: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._camera_id = camera_id
        self._prefilled_from_saved = prefilled_from_saved
        self.setWindowTitle(tr("Calibrate camera clock"))
        self.setModal(True)
        self._build_ui(initial_offset)

    def _build_ui(self, initial_offset: float) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        title = QLabel(tr(
            "Calibrate <b>{cam}</b>"
        ).replace("{cam}", self._camera_id))
        title.setObjectName("PageHeading")
        outer.addWidget(title)

        explanation = QLabel(tr(
            "How many hours is this camera's clock off from the "
            "trip-local time?\n\n"
            "Positive value: camera is BEHIND (add hours to get "
            "correct time).\n"
            "Negative value: camera is AHEAD (subtract hours).\n\n"
            "If the camera's clock is already correct, leave at 0.\n\n"
            "The offset will be baked into your photos' EXIF in "
            "Original Media at this step — once. From then on, the "
            "photos carry correct times and Mira doesn't modify "
            "them again (only the explicit “Adjust event TZ” "
            "operation does)."
        ))
        explanation.setObjectName("PageHint")
        explanation.setWordWrap(True)
        outer.addWidget(explanation)

        spin_row = QHBoxLayout()
        spin_label = QLabel(tr("Offset:"))
        spin_row.addWidget(spin_label)
        self._spin = QDoubleSpinBox()
        self._spin.setRange(-23.0, 23.0)
        self._spin.setDecimals(2)
        self._spin.setSingleStep(0.25)
        self._spin.setSuffix(tr(" h"))
        self._spin.setValue(initial_offset)
        self._spin.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        spin_row.addWidget(self._spin)
        if self._prefilled_from_saved:
            saved_hint = QLabel(tr(
                "(pre-filled from your saved setup)"
            ))
            saved_hint.setObjectName("PageHint")
            spin_row.addWidget(saved_hint)
        spin_row.addStretch(1)
        outer.addLayout(spin_row)

        self._remember = QCheckBox(tr(
            "Remember this offset for {cam} next time"
        ).replace("{cam}", self._camera_id))
        self._remember.setChecked(self._prefilled_from_saved)
        self._remember.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        outer.addWidget(self._remember)

        skip_hint = QLabel(tr(
            "Cancel = skip calibration for now; you can run "
            "“Adjust event TZ” later to apply a correction."
        ))
        skip_hint.setObjectName("PageHint")
        skip_hint.setWordWrap(True)
        outer.addWidget(skip_hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ── Public read API ──────────────────────────────────────────

    def offset_hours(self) -> float:
        """The offset value (in hours) the user entered. Only valid
        post-:meth:`exec` returning Accepted."""
        return float(self._spin.value())

    def remember(self) -> bool:
        """True if the user ticked the remember checkbox."""
        return bool(self._remember.isChecked())


def calibration_offset_for_offload(
    camera_id: str,
    settings: dict,
    parent=None,
) -> Optional[tuple[float, bool]]:
    """Top-level helper — given the user's settings + the camera_id
    being offloaded, return ``(offset_hours, remember)`` per the
    user's ``calibration_mode``:

    * ``"saved"``: silent if ``camera_id`` is in
      ``saved_camera_offsets``; falls through to prompt otherwise.
    * ``"prompt"``: always show the dialog (initialized from saved
      if any).
    * ``"reference_photo"``: TODO — for now falls through to prompt.
      The minimal reference-photo UI is a future sub-task; the
      design is documented in docs/14.

    Returns ``None`` if the user cancelled (the caller should skip
    the bake step and proceed without it).
    """
    mode = str(settings.get("calibration_mode", "prompt"))
    saved = settings.get("saved_camera_offsets", {}) or {}
    initial = float(saved.get(camera_id, 0.0))
    prefilled = camera_id in saved

    # "saved" mode: skip dialog when offset is known. Otherwise we
    # fall through to the dialog (treat unknown camera as a prompt).
    if mode == "saved" and prefilled:
        # We don't return remember=True here even though the user
        # presumably already opted in once — the offset was already
        # saved; we don't need to re-write it. The caller still
        # writes the offset into the run.
        return (initial, False)

    # Default / "prompt" / "reference_photo" (until its UI exists)
    # all use the prompt dialog.
    dlg = OffloadCalibrationDialog(
        camera_id,
        initial_offset=initial,
        prefilled_from_saved=prefilled,
        parent=parent,
    )
    result = dlg.exec()
    if result != QDialog.DialogCode.Accepted:
        return None
    return (dlg.offset_hours(), dlg.remember())
