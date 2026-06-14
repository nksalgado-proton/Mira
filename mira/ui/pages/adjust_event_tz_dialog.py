"""Adjust event TZ dialog — PORTED from the legacy ``ui/pages/adjust_event_tz_dialog.py``
(charter §0/§5.2), with the data seam rewired to the gateway and the **EXIF bake replaced by
the virtual-EXIF recompute** (charter §3, §5.4; spec/14 §2.1 / §5B B2).

The UI is the legacy dialog verbatim — the per-camera offset table, the hidden per-day
adjustment matrix, the pre-flight confirmation, the progress dialog, the completion summary.
What changed (only the data calls):

- **Reads:** legacy `all_camera_offsets(event)` / `files_for_camera*` / `day_offset_for_camera`
  + a `Original Media` filesystem walk → the gateway: `cameras()` (`applied_offset_minutes`) and
  `items(camera_id=…, day=…)` counts. No filesystem walk (G2 — the store is the truth).
- **Apply:** legacy `adjust_camera_tz` / `adjust_camera_day_tz` **baked** `DateTimeOriginal`
  into the files in `Original Media` + `save_event`. Here Apply calls
  `EventGateway.recompute_corrected_times(cam, applied_offset_minutes=…, day_number=…)` +
  `save_camera(…)` — it re-derives `capture_time_corrected` from the pristine
  `capture_time_raw` (never touched) and reassigns the day; **no file is written**. The hint /
  confirmation text is reworded to that reality.

Use case unchanged: user discovers after ingest that a camera's clock offset was wrong; they
correct it here and every affected item's corrected time follows.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.ui.i18n import tr

log = logging.getLogger(__name__)

# Sentinel for cameras with no recorded offset yet — offered a row to apply the FIRST
# correction, treating "current" as 0.0 in the delta math.
_NO_OFFSET_RECORDED = "—"


class AdjustEventTzDialog(QDialog):
    """Modal table for per-camera offset editing (gateway-native, virtual-EXIF).

    Construct with the umbrella ``gateway`` + ``event_id``; on Apply it recomputes the
    affected items' corrected times through the gateway (no bake) and persists the new
    per-camera offsets via ``save_camera``."""

    def __init__(self, gateway: Gateway, event_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self._event_id = event_id
        self._cameras: list[str] = []
        self._spinners: dict[str, QDoubleSpinBox] = {}
        self._initial: dict[str, float] = {}
        # Per-day spinners keyed by (camera_id, day_number) — the multi-TZ fix.
        self._day_spinners: dict[tuple[str, int], QDoubleSpinBox] = {}
        self._day_initial: dict[tuple[str, int], float] = {}
        self._last_recomputed = 0
        self.setWindowTitle(tr("Adjust event timezone"))
        self.setModal(True)
        self.resize(680, 480)
        self._build_ui()
        self._populate()

    # ── Build (verbatim from legacy, only the hint text reworded) ──

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        title = QLabel(tr("Adjust event timezone"))
        title.setObjectName("PageHeading")
        outer.addWidget(title)

        hint = QLabel(tr(
            "Each camera in this event has a timezone offset applied "
            "to its photos' capture times. If you later discover the "
            "calibration was wrong, edit the offset below and Apply — "
            "Mira re-derives the corrected times for just the "
            "affected photos. Your original files are never modified; "
            "cameras you don't change are untouched."
        ))
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([
            tr("Camera"),
            tr("Currently applied"),
            tr("New offset (hours)"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self._table, stretch=1)

        self._empty_label = QLabel(tr(
            "No cameras found yet for this event. Run Capture first."
        ))
        self._empty_label.setObjectName("PageHint")
        self._empty_label.setVisible(False)
        outer.addWidget(self._empty_label)

        # Per-day section — hidden by default (single-TZ trip is the common case).
        self._toggle_day_btn = QPushButton(tr("Per-day adjustments ▾"))
        self._toggle_day_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._toggle_day_btn.setToolTip(tr(
            "Show / hide the per-day TZ adjustment matrix. Useful "
            "for trips that cross timezones — one or two days fall "
            "in a different TZ from the rest, and you want to shift "
            "JUST those days without re-applying the whole-trip "
            "calibration."
        ))
        self._toggle_day_btn.setCheckable(True)
        self._toggle_day_btn.toggled.connect(self._on_toggle_day_section)
        outer.addWidget(self._toggle_day_btn)

        self._day_hint = QLabel(tr(
            "Per-day deltas are added to whatever offset you set "
            "above. Use 0.0 (default) to leave a day untouched. "
            "Each non-zero row re-derives that camera-day's photos."
        ))
        self._day_hint.setObjectName("PageHint")
        self._day_hint.setWordWrap(True)
        self._day_hint.setVisible(False)
        outer.addWidget(self._day_hint)

        self._day_table = QTableWidget(0, 4)
        self._day_table.setHorizontalHeaderLabels([
            tr("Camera"), tr("Day"), tr("Files"), tr("Δh (delta)"),
        ])
        self._day_table.verticalHeader().setVisible(False)
        self._day_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._day_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        day_header = self._day_table.horizontalHeader()
        day_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            day_header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self._day_table.setVisible(False)
        outer.addWidget(self._day_table, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Close
        )
        apply_btn = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        apply_btn.clicked.connect(self._on_apply)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.reject)
        outer.addWidget(buttons)
        self._apply_btn = apply_btn

    # ── Populate (rewired to the gateway) ──

    def _populate(self) -> None:
        """One row per camera in the store (`cameras()`); the current applied offset comes
        from `Camera.applied_offset_minutes`."""
        eg = self._gateway.open_event(self._event_id)
        try:
            cams = eg.cameras()
            recorded: dict[str, float] = {
                c.camera_id: c.applied_offset_minutes / 60.0
                for c in cams if c.applied_offset_minutes is not None
            }
            all_ids = sorted(c.camera_id for c in cams)
            self._cameras = all_ids

            if not all_ids:
                self._table.setVisible(False)
                self._empty_label.setVisible(True)
                self._apply_btn.setEnabled(False)
                return

            self._table.setRowCount(len(all_ids))
            for r, cam in enumerate(all_ids):
                id_item = QTableWidgetItem(cam)
                id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(r, 0, id_item)

                if cam in recorded:
                    applied_text = f"{recorded[cam]:+.2f} h"
                    initial_val = float(recorded[cam])
                else:
                    applied_text = _NO_OFFSET_RECORDED
                    initial_val = 0.0
                applied_item = QTableWidgetItem(applied_text)
                applied_item.setFlags(applied_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                applied_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(r, 1, applied_item)

                spin = QDoubleSpinBox()
                spin.setRange(-23.0, 23.0)
                spin.setDecimals(2)
                spin.setSingleStep(0.25)
                spin.setSuffix(tr(" h"))
                spin.setValue(initial_val)
                spin.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                self._table.setCellWidget(r, 2, spin)
                self._spinners[cam] = spin
                self._initial[cam] = initial_val

            self._populate_day_matrix(eg)
        finally:
            eg.close()

    def _populate_day_matrix(self, eg) -> None:
        """One row per (camera, day) where the camera has items on that day. Day numbers
        come from the trip plan; file counts + the current per-day offset come from the
        gateway items."""
        days = eg.trip_days() or []
        if not days or not self._cameras:
            return
        rows: list[tuple[str, int, int]] = []  # (camera, day_num, file_count)
        for cam in self._cameras:
            for d in days:
                n = len(eg.items(camera_id=cam, day=d.day_number))
                if n > 0:
                    rows.append((cam, d.day_number, n))
        self._day_table.setRowCount(len(rows))
        for r, (cam, day_num, n) in enumerate(rows):
            cam_item = QTableWidgetItem(cam)
            cam_item.setFlags(cam_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._day_table.setItem(r, 0, cam_item)
            day_item = QTableWidgetItem(str(day_num))
            day_item.setFlags(day_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            day_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._day_table.setItem(r, 1, day_item)
            files_item = QTableWidgetItem(str(n))
            files_item.setFlags(files_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            files_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._day_table.setItem(r, 2, files_item)
            # Current per-day offset (informational) = the items' current tz offset.
            day_items = eg.items(camera_id=cam, day=day_num)
            cur_min = day_items[0].tz_offset_minutes if day_items else 0
            spin = QDoubleSpinBox()
            spin.setRange(-23.0, 23.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.25)
            spin.setSuffix(tr(" h"))
            spin.setValue(0.0)              # delta starts at 0
            spin.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            spin.setToolTip(tr(
                "Hours to add to this camera-day's photos. "
                "Currently applied offset: {d:+.2f}h."
            ).replace("{d:+.2f}", f"{cur_min / 60.0:+.2f}"))
            self._day_table.setCellWidget(r, 3, spin)
            self._day_spinners[(cam, day_num)] = spin
            self._day_initial[(cam, day_num)] = 0.0

    def _on_toggle_day_section(self, checked: bool) -> None:
        self._day_hint.setVisible(checked)
        self._day_table.setVisible(checked)
        self._toggle_day_btn.setText(
            tr("Per-day adjustments ▴") if checked
            else tr("Per-day adjustments ▾"))

    # ── Apply (rewired: recompute, no bake) ──

    def _changed_cameras(self) -> list[tuple[str, float, float]]:
        """``[(camera_id, current_offset, new_offset)]`` for every row whose spinner
        differs from the initial value."""
        out: list[tuple[str, float, float]] = []
        for cam, spin in self._spinners.items():
            new_val = float(spin.value())
            initial = self._initial.get(cam, 0.0)
            if new_val != initial:
                out.append((cam, initial, new_val))
        return out

    def _changed_day_overrides(self) -> list[tuple[str, int, float]]:
        """[(camera_id, day_number, delta_hours)] for every per-day spinner at non-zero."""
        out: list[tuple[str, int, float]] = []
        for (cam, day), spin in self._day_spinners.items():
            delta = float(spin.value())
            if delta != 0.0:
                out.append((cam, day, delta))
        return out

    def _on_apply(self) -> None:
        changes = self._changed_cameras()
        day_changes = self._changed_day_overrides()
        if not changes and not day_changes:
            QMessageBox.information(
                self, tr("Nothing to apply"),
                tr("None of the offsets have been changed."),
            )
            return

        eg = self._gateway.open_event(self._event_id)
        try:
            # Pre-flight: count files per affected camera so the user sees the blast radius.
            blast_lines: list[str] = []
            total_files = 0
            for cam, current, new_val in changes:
                n = len(eg.items(camera_id=cam))
                total_files += n
                delta = new_val - current
                blast_lines.append(
                    tr(
                        "  • {cam}: {n} file(s), shift to {new}h "
                        "(was {cur}h)"
                    ).replace("{cam}", cam)
                    .replace("{n}", str(n))
                    .replace("{new}", f"{new_val:+.2f}")
                    .replace("{cur}", f"{current:+.2f}")
                )
            for cam, day, delta in day_changes:
                n = len(eg.items(camera_id=cam, day=day))
                total_files += n
                blast_lines.append(
                    tr(
                        "  • {cam} Day {d}: {n} file(s), shift by "
                        "{delta}h (per-day override)"
                    ).replace("{cam}", cam)
                    .replace("{d}", str(day))
                    .replace("{n}", str(n))
                    .replace("{delta}", f"{delta:+.2f}")
                )
            msg = (
                tr("About to re-derive the corrected capture time of "
                   "{n} file(s):")
                .replace("{n}", str(total_files))
                + "\n\n"
                + "\n".join(blast_lines)
                + "\n\n"
                + tr("Your original photo files are NOT modified — only "
                     "Mira's stored corrected times are recomputed. "
                     "Continue?")
            )
            confirm = QMessageBox.question(
                self, tr("Confirm timezone adjustment"),
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

            progress = QProgressDialog(
                tr("Correcting capture timestamps in your photos…"),
                None, 0, 0, self,
            )
            progress.setWindowTitle(tr("Please wait"))
            progress.setMinimumDuration(0)
            progress.setModal(True)
            progress.setCancelButton(None)
            progress.show()
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()

            recomputed = 0
            try:
                # Whole-camera (whole-trip) changes first — set the absolute offset for all
                # the camera's items + persist the new applied offset on the Camera row.
                for cam, _current, new_val in changes:
                    minutes = round(new_val * 60)
                    affected = eg.recompute_corrected_times(
                        cam, applied_offset_minutes=minutes)
                    recomputed += len(affected)
                    eg.save_camera(self._camera_row(eg, cam, minutes))
                    QApplication.processEvents()
                # Per-day overrides — the day's absolute offset = the camera's intended
                # offset (its spinner value) + the per-day delta.
                for cam, day, delta in day_changes:
                    base = float(self._spinners[cam].value()) if cam in self._spinners else 0.0
                    minutes = round((base + delta) * 60)
                    affected = eg.recompute_corrected_times(
                        cam, applied_offset_minutes=minutes, day_number=day)
                    recomputed += len(affected)
                    QApplication.processEvents()
            finally:
                progress.close()
                progress.deleteLater()
        finally:
            eg.close()

        self._last_recomputed = recomputed
        self._show_summary(recomputed)

        # Re-populate so "currently applied" reflects the new state.
        was_expanded = self._toggle_day_btn.isChecked()
        self._table.setRowCount(0)
        self._spinners.clear()
        self._initial.clear()
        self._day_table.setRowCount(0)
        self._day_spinners.clear()
        self._day_initial.clear()
        self._populate()
        if was_expanded:
            self._on_toggle_day_section(True)

    @staticmethod
    def _camera_row(eg, camera_id: str, applied_offset_minutes: int):
        """Build the updated Camera row, preserving the existing flags/calibration and
        stamping the new applied offset."""
        from mira.store import models as m

        existing = next((c for c in eg.cameras() if c.camera_id == camera_id), None)
        if existing is not None:
            existing.applied_offset_minutes = applied_offset_minutes
            existing.applied_at = eg._now()
            return existing
        return m.Camera(
            camera_id=camera_id,
            applied_offset_minutes=applied_offset_minutes,
            applied_at=eg._now(),
        )

    def _show_summary(self, recomputed: int) -> None:
        QMessageBox.information(
            self, tr("Timezone adjustment complete"),
            tr("Re-derived corrected times for {n} file(s).").replace(
                "{n}", str(recomputed)),
        )

    # ── Public read API ──

    @property
    def last_recomputed(self) -> int:
        """Count of items recomputed in the most recent Apply."""
        return self._last_recomputed
