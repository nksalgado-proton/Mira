"""Per-camera clock-timezone question — PORTED VERBATIM from the legacy
``ui/culler/camera_clock_dialog.py`` (charter §0/§5.2).

The ONLY changes from the legacy file are the import swaps ``ui.base.tz_picker`` →
``mira.ui.base.tz_picker`` and ``ui.i18n`` → ``mira.ui.i18n``. The dialog itself is
pure Qt + reused ``core`` pure-logic (``clock_calibration`` / ``fresh_source`` /
``tz_locations``) — it takes a camera list + an ``initial`` answer record and returns the
human answers via :meth:`result_answers`; it performs NO persistence of its own (the caller —
``MainWindow`` for the Plan-page "Camera clocks" action — turns the answers into
``save_camera`` + ``recompute_corrected_times`` gateway calls; spec/14 §5B B1).

Legacy docstring preserved:

docs/18 §"Culling contexts" → the 2026-05-18 simplification note + the B.3b note. For the
fresh-camera contexts there is one safety step before the photos are grouped into days: per
camera that contributed, ask *"was this camera's clock set to the timezone where you took the
photos?"* If **no**, ask which timezone it *was* on — the photos would otherwise land on the
wrong ``Dia N`` (the Nepal day-shift). Phone skips this entirely; this dialog is never shown
for it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.clock_calibration import CameraCalibration
from core.fresh_source import build_tz_calibrations

# Single source of truth for the offset→text format (P4). Re-exported under the historical
# private name so callers/tests that import ``_fmt_offset`` from this module keep working.
from core.tz_locations import format_utc_offset as _fmt_offset
from mira.ui.base.tz_picker import TzPicker
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


def _system_offset_hours() -> float:
    """Local UTC offset in hours, rounded to ¼ h — the picker default (the user is most
    often culling where the trip was)."""
    off = datetime.now().astimezone().utcoffset()
    if off is None:
        return 0.0
    return round(off.total_seconds() / 3600.0 * 4) / 4.0


def _make_offset_combo(default_hours: float) -> TzPicker:
    """A location picker pre-selected to ``default_hours`` (kept as a thin factory so the
    dialog body reads unchanged)."""
    return TzPicker(float(default_hours))


class CameraClockDialog(QDialog):
    """Ask, per camera, whether its clock matched the trip timezone.

    :meth:`result_answers` (after Accept, or live in tests) returns the human answer per
    asked camera; :meth:`result_calibrations` returns the derived ``{camera_id:
    CameraCalibration}`` map. Cancel → the host aborts (no guessing)."""

    def __init__(
        self,
        cameras: list[str],
        *,
        default_trip_tz_hours: Optional[float] = None,
        ask_trip_tz: bool = True,
        initial: Optional[dict[str, dict]] = None,
        edit_mode: bool = False,
        suggestions: Optional[dict[str, "TzSuggestion"]] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CameraClockDialog")
        self.setWindowTitle(tr("Camera clocks"))
        self.setModal(True)

        self._initial = initial or {}
        self._suggestions = suggestions or {}
        self._edit_mode = edit_mode
        self._cameras = [c for c in cameras if c]
        trip_default = (
            default_trip_tz_hours if default_trip_tz_hours is not None
            else _system_offset_hours()
        )
        self._ask_trip_tz = ask_trip_tz
        self._fixed_trip_tz = float(trip_default)
        self._rows: dict[str, dict] = {}
        self._snapshot: Optional[dict[str, CameraCalibration]] = None
        self._answers_snapshot: Optional[dict[str, dict]] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        heading = QLabel(
            tr("Camera clocks for this event")
            if self._edit_mode
            else tr("Were the camera clocks on the right time?")
        )
        heading.setObjectName("PageHeading")
        outer.addWidget(heading)

        if self._ask_trip_tz:
            hint_text = tr(
                "If a camera's clock was set to the wrong timezone "
                "during the trip, its photos would land on the wrong "
                "day. Tell us and we'll shift them back — nothing on "
                "disk is changed. (Phones fix their own clock, so "
                "they're never asked.)"
            )
        else:
            hint_text = tr(
                "Trip timezone (from your plan): {tz}. We only need to "
                "know if a camera's clock was set to a *different* "
                "timezone during the trip — then its photos would land "
                "on the wrong day and we'll shift them back. Nothing on "
                "disk is changed."
            ).replace("{tz}", _fmt_offset(self._fixed_trip_tz))
        hint = QLabel(hint_text)
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self._trip_combo: Optional[TzPicker] = None
        cam_start = 0
        if self._ask_trip_tz:
            trip_lbl = QLabel(tr("Timezone where you took the photos:"))
            self._trip_combo = _make_offset_combo(trip_default)
            self._trip_combo.setToolTip(tr(
                "The trip's local timezone — the time the camera "
                "clocks *should* have shown. Defaults to this "
                "computer's timezone."
            ))
            grid.addWidget(trip_lbl, 0, 0)
            grid.addWidget(self._trip_combo, 0, 1, 1, 2)

            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setFrameShadow(QFrame.Shadow.Sunken)
            grid.addWidget(divider, 1, 0, 1, 3)
            cam_start = 2

        next_row = cam_start
        for cam in self._cameras:
            r = next_row
            cam_lbl = QLabel(cam)
            cam_lbl.setObjectName("PageHint")
            state = QComboBox()
            state.addItem(tr("Clock was correct"))
            state.addItem(tr("Clock was on the wrong timezone"))
            state.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            state.setToolTip(tr(
                "Pick “wrong timezone” only if this camera's clock did "
                "not match the trip timezone — then say which zone it "
                "was on."
            ))
            combo = _make_offset_combo(trip_default)
            combo.setVisible(False)             # revealed when wrong
            combo.setToolTip(tr(
                "What timezone this camera's clock was actually set to "
                "during the trip (e.g. you forgot to change it from "
                "home)."
            ))
            state.currentIndexChanged.connect(
                lambda idx, c=combo: c.setVisible(idx == 1)
            )
            prior = self._initial.get(cam)
            prior_applied = False
            if isinstance(prior, dict):
                was_ok = bool(prior.get("correct", True))
                cfg = prior.get("configured_tz", None)
                if not was_ok and cfg is not None:
                    state.setCurrentIndex(1)
                    combo.setVisible(True)
                    combo.setValue(float(cfg))
                    prior_applied = True
            suggestion = self._suggestions.get(cam)
            if (
                not prior_applied
                and suggestion is not None
                and suggestion.suggested_tz is not None
            ):
                state.setCurrentIndex(1)
                combo.setVisible(True)
                combo.setValue(float(suggestion.suggested_tz))
            grid.addWidget(cam_lbl, r, 0)
            grid.addWidget(state, r, 1)
            grid.addWidget(combo, r, 2)
            next_row = r + 1
            if suggestion is not None and not prior_applied:
                banner = QLabel("⚠ " + tr(suggestion.reason))
                banner.setObjectName("TzSuggestionBanner")
                banner.setWordWrap(True)
                banner.setStyleSheet(
                    "QLabel#TzSuggestionBanner { "
                    "color: #6b4d00; "
                    "background-color: #fff4d6; "
                    "border: 1px solid #d6b96e; "
                    "border-radius: 4px; "
                    "padding: 6px 10px; "
                    "}"
                )
                grid.addWidget(banner, next_row, 0, 1, 3)
                next_row += 1
            self._rows[cam] = {"state": state, "combo": combo}

        outer.addLayout(grid)
        outer.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setObjectName("Primary")
            ok_btn.setText(
                tr("Save") if self._edit_mode else tr("Start picking"))
            ok_btn.setDefault(True)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ── results ──

    def _trip_tz(self) -> float:
        if self._ask_trip_tz and self._trip_combo is not None:
            return float(self._trip_combo.value())
        return self._fixed_trip_tz       # from the event plan

    @staticmethod
    def _is_wrong(w: dict) -> bool:
        """State dropdown: index 0 = correct, 1 = wrong timezone."""
        return w["state"].currentIndex() == 1

    def _wrong_tz_by_camera(self) -> dict[str, float]:
        """Cameras flagged 'wrong timezone' → the offset their clock was on. 'Correct'
        cameras are omitted (pass-through)."""
        out: dict[str, float] = {}
        for cam, w in self._rows.items():
            if self._is_wrong(w):
                out[cam] = float(w["combo"].value())
        return out

    def result_answers(self) -> dict[str, dict]:
        """The HUMAN answer per asked camera (for the persisted record):
        ``{camera_id: {"correct": bool, "configured_tz": float|None}}``."""
        if self._answers_snapshot is not None:
            return self._answers_snapshot
        return self._live_answers()

    def _live_answers(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for cam, w in self._rows.items():
            ok = not self._is_wrong(w)
            out[cam] = {
                "correct": ok,
                "configured_tz": (
                    None if ok else float(w["combo"].value())
                ),
            }
        return out

    def result_calibrations(self) -> dict[str, CameraCalibration]:
        """The ``{camera_id: CameraCalibration}`` map (derived from the answers × trip tz)."""
        if self._snapshot is not None:
            return self._snapshot
        return build_tz_calibrations(
            self._wrong_tz_by_camera(), self._trip_tz())

    def _on_accept(self) -> None:
        self._snapshot = build_tz_calibrations(
            self._wrong_tz_by_camera(), self._trip_tz())
        self._answers_snapshot = self._live_answers()
        self.accept()
