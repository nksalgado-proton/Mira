"""Unified Camera Clock Correction dialog (spec/127).

The definitive replacement for the two overlapping Collect menu items
"Camera Clocks…" (the per-camera zone question) and "Adjust TZ…" (the
per-camera offset edit + per-day matrix). One dialog, one menu item.

Built on spec/123 (offset = integer seconds; H:M:S throughout;
``recompute_corrected_times`` re-derives ``capture_time_corrected`` from
the pristine ``capture_time_raw``, never bakes EXIF) and the spec/124
sync-pair picker (raw measured delta, no snapping).

Model — per camera, per **trip-TZ segment** (spec/127 §1.1):

* A **segment** is the set of plan days sharing one ``trip_day.tz_minutes``.
  A normal trip = one segment; a TZ-crossing trip (Nepal +5:45 with a day at
  India +5:30) = two. The dialog shows one section per segment.
* The correction is a **base** plus an optional **fine nudge**:
    * base — spec/123 source: Correct (0) / known TZ zone
      (``segment_trip_tz − camera_tz``) / measured pair (raw delta from
      the sync-pair picker).
    * nudge — ``±MM:SS`` for residual clock drift independent of zone
      (the GoPro running 3 min fast after its TZ fix: base ``+8:45``,
      nudge ``−0:03:00`` → ``+8:42:00``).
    * total = base + nudge, persisted as ``applied_offset_seconds`` and
      fed to ``recompute_corrected_times`` scoped to the segment's days
      so a camera spanning two segments gets the right offset in each.

Offset-honest representation (spec/125, folded in): show a real zone
**only** when ``configured_tz_seconds`` is set on the stored correction
row; otherwise show the raw offset. NEVER fabricate a "Custom TZ" from
a measured delta.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.tz_locations import format_utc_offset
from mira.gateway import Gateway
from mira.store import models as m
from mira.ui.base.tz_picker import TzPicker
from mira.ui.i18n import tr

log = logging.getLogger(__name__)

# Re-exported under the historical name so callers/tests that import
# ``_fmt_offset`` from this module keep working (P4 single source of
# truth lives in core.tz_locations).
_fmt_offset = format_utc_offset


# ── H:M:S formatting + parsing (lifted from adjust_event_tz_dialog.py) ─


_HMS_RE = re.compile(
    r"""^\s*
    (?P<sign>[+-])?\s*
    (?P<h>\d+)
    (?::(?P<m>\d{1,2}))?
    (?::(?P<s>\d{1,2}))?
    \s*$""",
    re.VERBOSE,
)


def format_seconds_hms(total: int) -> str:
    """Format integer seconds as ``±H:MM`` or ``±H:MM:SS`` (seconds shown
    only when non-zero). Matches what the user typed when the input was
    minute-aligned (``+8:45``) but keeps seconds visible when they matter
    (``+5:00:02``)."""
    total = int(total)
    sign = "+" if total >= 0 else "-"
    secs = abs(total)
    h, rem = divmod(secs, 3600)
    mm, ss = divmod(rem, 60)
    if ss:
        return f"{sign}{h:d}:{mm:02d}:{ss:02d}"
    return f"{sign}{h:d}:{mm:02d}"


def parse_hms_to_seconds(text: str) -> Optional[int]:
    """Parse ``±H:MM[:SS]`` → integer seconds. Returns ``None`` on bad
    input — the caller decides what to show.

    Tolerated forms (anything else returns None):
      ``5:45``     →  20 700
      ``+5:45``    →  20 700
      ``-3:00``    → -10 800
      ``+5:00:02`` →  18 002
      ``8``        →  28 800   (hours-only)
      ``0:30``     →  1 800
    """
    if text is None:
        return None
    mtch = _HMS_RE.match(str(text))
    if not mtch:
        return None
    sign = -1 if mtch.group("sign") == "-" else 1
    h = int(mtch.group("h"))
    mm = int(mtch.group("m") or 0)
    ss = int(mtch.group("s") or 0)
    if mm > 59 or ss > 59:
        return None
    return sign * (h * 3600 + mm * 60 + ss)


class HmsEntry(QLineEdit):
    """A ``±H:MM:SS`` text entry backed by integer seconds.

    ``value()`` / ``setValue(int_seconds)`` mirror QDoubleSpinBox's
    shape. ``valueChanged(int)`` fires when the typed text parses to a
    different integer. Bad input doesn't move ``_value`` (the editor
    keeps showing the user's text until they fix or revert)."""

    valueChanged = pyqtSignal(int)

    def __init__(self, initial_seconds: int = 0,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HmsEntry")
        self.setPlaceholderText("±H:MM:SS")
        self.setMaximumWidth(140)
        self._value = int(initial_seconds)
        self.setText(format_seconds_hms(self._value))
        self.editingFinished.connect(self._on_editing_finished)

    def value(self) -> int:
        return self._value

    def setValue(self, seconds: int) -> None:
        seconds = int(seconds)
        changed = seconds != self._value
        self._value = seconds
        self.setText(format_seconds_hms(seconds))
        if changed and not self.signalsBlocked():
            self.valueChanged.emit(seconds)

    def _on_editing_finished(self) -> None:
        parsed = parse_hms_to_seconds(self.text())
        if parsed is None:
            self.setText(format_seconds_hms(self._value))
            return
        if parsed != self._value:
            self._value = parsed
            self.setText(format_seconds_hms(parsed))
            self.valueChanged.emit(parsed)


# ── Per-row state model ────────────────────────────────────────────────


# Indices for the state combo. Keep in sync with the addItem order in
# ``_build_camera_row`` — the ints are persisted to ``_rows`` and read
# back in ``_collect_row_state``.
_STATE_CORRECT = 0
_STATE_ZONE = 1
_STATE_MEASURED = 2


class _RowState:
    """One (camera_id, trip_tz_seconds) row's editable state — the
    widgets the user touches plus the ``stored`` baseline they were
    seeded from. The dialog reads this on Apply to decide whether the
    row changed and what to persist.

    ``stored`` is the row from ``camera_tz_correction`` (or a synthetic
    "Correct" row when none exists) used for the change check + for
    "Unchanged → skip" short-circuit."""

    __slots__ = (
        "camera_id", "trip_tz_seconds", "state_combo",
        "zone_picker", "offset_entry", "pick_pair_btn",
        "nudge_entry", "total_label", "container", "stored",
    )

    def __init__(self, camera_id: str, trip_tz_seconds: int,
                 stored: m.CameraTzCorrection):
        self.camera_id = camera_id
        self.trip_tz_seconds = int(trip_tz_seconds)
        self.stored = stored
        # Filled in by _build_camera_row.
        self.state_combo: QComboBox
        self.zone_picker: TzPicker
        self.offset_entry: HmsEntry
        self.pick_pair_btn: QPushButton
        self.nudge_entry: HmsEntry
        self.total_label: QLabel
        self.container: QWidget


# ── Dialog ─────────────────────────────────────────────────────────────


class CameraClockCorrectionDialog(QDialog):
    """spec/127 — one dialog to correct every camera clock against every
    trip-TZ segment.

    Constructor reads the event's tz_segments + the stored
    ``camera_tz_correction`` rows; on Apply, writes the per-(camera,
    segment) rows that changed and runs ``recompute_corrected_times``
    scoped to each segment's days. The legacy single-camera summary
    columns (``camera.applied_offset_seconds`` /
    ``configured_tz_seconds``) are mirrored from the predominant
    segment's row for back-compat (see
    :meth:`EventGateway.save_camera_tz_correction`)."""

    def __init__(self, gateway: Gateway, event_id: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self._event_id = event_id
        self.setWindowTitle(tr("Camera Clock Correction"))
        self.setModal(True)
        self.setMinimumSize(680, 480)
        self.resize(820, 560)

        self._segments: List = []           # core.tz_segments.TzSegment
        # Row state keyed by (camera_id, trip_tz_seconds).
        self._rows: Dict[Tuple[str, int], _RowState] = {}
        # Predominant segment (most plan days) — the row mirrored to the
        # legacy camera columns.
        self._predominant_tz_seconds: Optional[int] = None
        self._last_recomputed = 0

        self._build_ui()
        self._populate()

    # ── UI scaffolding ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        self._title = QLabel(tr("Camera Clock Correction"))
        self._title.setObjectName("PageHeading")
        outer.addWidget(self._title)

        self._summary = QLabel("")
        self._summary.setObjectName("PageHint")
        self._summary.setWordWrap(True)
        outer.addWidget(self._summary)

        self._hint = QLabel(tr(
            "Pick how each camera's clock was set during the trip — "
            "Correct, a known timezone, or a measured offset from a "
            "matched pair. Add a fine nudge (±MM:SS) for residual clock "
            "drift. Mira re-derives the corrected times for just the "
            "affected photos — your original files are never modified."
        ))
        self._hint.setObjectName("PageHint")
        self._hint.setWordWrap(True)
        outer.addWidget(self._hint)

        # Per-segment sections inserted here.
        self._sections_layout = QVBoxLayout()
        self._sections_layout.setSpacing(12)
        outer.addLayout(self._sections_layout)

        self._empty_label = QLabel(tr(
            "No cameras found yet for this event. Run Collect first."
        ))
        self._empty_label.setObjectName("PageHint")
        self._empty_label.setVisible(False)
        outer.addWidget(self._empty_label)

        outer.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Close
        )
        self._apply_btn = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self._apply_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._apply_btn.clicked.connect(self._on_apply)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.reject)
        outer.addWidget(buttons)

    def _populate(self) -> None:
        """Read segments + stored corrections and lay out the dialog."""
        eg = self._gateway.open_event(self._event_id)
        try:
            self._segments = eg.tz_segments()
            stored_by_key: Dict[Tuple[str, int], m.CameraTzCorrection] = {
                (c.camera_id, int(c.trip_tz_seconds)): c
                for c in eg.camera_tz_corrections()
            }
        finally:
            eg.close()

        # Drop empty segments (no cameras present) — nothing to render.
        segments = [s for s in self._segments if s.cameras_present]
        if not segments:
            self._empty_label.setVisible(True)
            self._apply_btn.setEnabled(False)
            self._summary.setText("")
            return

        # Header summary + predominant segment selection.
        seg_summary_parts: List[str] = []
        for seg in segments:
            days = seg.day_numbers
            if len(days) == 1:
                days_label = tr("Day {n}").replace("{n}", str(days[0]))
            else:
                days_label = tr("Days {a}–{b}").replace(
                    "{a}", str(days[0])).replace("{b}", str(days[-1]))
            seg_summary_parts.append(
                f"{days_label}: <b>{_fmt_offset(seg.trip_tz_seconds / 3600.0)}</b>"
            )
        self._summary.setText(" · ".join(seg_summary_parts))
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        # Predominant = the segment with the most days; ties → smallest
        # trip_tz_seconds (deterministic).
        self._predominant_tz_seconds = sorted(
            segments,
            key=lambda s: (-len(s.day_numbers), s.trip_tz_seconds),
        )[0].trip_tz_seconds

        # Skip segment chrome for single-segment trips — spec/127 §3.
        show_chrome = len(segments) > 1

        for seg in segments:
            section = self._build_segment_section(
                seg, stored_by_key, show_chrome=show_chrome)
            self._sections_layout.addWidget(section)

    def _build_segment_section(
        self, segment, stored_by_key, *, show_chrome: bool,
    ) -> QWidget:
        section = QFrame(self)
        section.setObjectName("SectionBox")
        section.setProperty("level", "1")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        if show_chrome:
            days = segment.day_numbers
            if len(days) == 1:
                header_days = tr("Day {n}").replace("{n}", str(days[0]))
            else:
                header_days = tr("Days {a}–{b}").replace(
                    "{a}", str(days[0])).replace("{b}", str(days[-1]))
            header = QLabel(
                f"<b>{header_days}</b> · "
                f"{_fmt_offset(segment.trip_tz_seconds / 3600.0)}"
            )
            header.setTextFormat(Qt.TextFormat.RichText)
            header.setObjectName("PageHint")
            layout.addWidget(header)

        table = QTableWidget(len(segment.cameras_present), 4)
        table.setHorizontalHeaderLabels([
            tr("Camera"),
            tr("State / value"),
            tr("Fine nudge"),
            tr("Resulting offset"),
        ])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        h = table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        table.setMinimumHeight(120)

        for r, cam_id in enumerate(segment.cameras_present):
            key = (cam_id, int(segment.trip_tz_seconds))
            stored = stored_by_key.get(key) or m.CameraTzCorrection(
                camera_id=cam_id,
                trip_tz_seconds=int(segment.trip_tz_seconds),
            )
            self._build_camera_row(
                table, r, cam_id, segment.trip_tz_seconds, stored)

        # Tighten the table height to its rows.
        total_height = (
            table.horizontalHeader().sizeHint().height()
            + sum(table.rowHeight(i) for i in range(table.rowCount()))
            + 8
        )
        table.setMinimumHeight(max(120, total_height))
        layout.addWidget(table)
        return section

    def _build_camera_row(
        self, table: QTableWidget, row: int, camera_id: str,
        trip_tz_seconds: int, stored: m.CameraTzCorrection,
    ) -> None:
        # Camera id cell.
        id_item = QTableWidgetItem(camera_id)
        id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, 0, id_item)

        rs = _RowState(camera_id, trip_tz_seconds, stored)
        self._rows[(camera_id, int(trip_tz_seconds))] = rs

        # State combo + value editor (live in one cell so the editor
        # follows the state without complicating the layout).
        state_combo = QComboBox()
        state_combo.addItem(tr("Clock was correct"))             # _STATE_CORRECT
        state_combo.addItem(tr("Camera was on a known TZ"))      # _STATE_ZONE
        state_combo.addItem(tr("Measured offset"))               # _STATE_MEASURED
        state_combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        state_combo.setToolTip(tr(
            "Correct: this camera's clock matched the trip TZ. "
            "Known TZ: the clock was set to a different zone — pick it. "
            "Measured offset: a raw clock delta in H:M:S (typically from "
            "a matched pair via Pick a pair…)."))

        # Zone picker (used when state = known TZ).
        zone_picker = TzPicker(0.0)
        zone_picker.setVisible(False)

        # Offset entry + "Pick a pair…" button (used when state = measured).
        offset_entry = HmsEntry(0)
        offset_entry.setVisible(False)
        offset_entry.setToolTip(tr(
            "Raw measured clock offset — added straight to the camera's "
            "capture times (spec/123, no snapping)."))
        pick_btn = QPushButton(tr("Pick a pair…"))
        # Compact in-table-cell button — matches the row's combo / line-edit
        # height (see #PlanBrowseCell in redesign.qss). The base QPushButton
        # rule renders ~34 px, which overflows the row next to the H:M:S
        # entry; this role slims it to the cell metrics.
        pick_btn.setObjectName("PlanBrowseCell")
        pick_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        pick_btn.setVisible(False)
        pick_btn.setToolTip(tr(
            "Open the sync-pair picker: choose one photo from this "
            "camera and one from a clock-correct reference taken at the "
            "same moment. The raw delta becomes the base offset."))
        pick_btn.clicked.connect(
            lambda _checked=False, key=(camera_id, int(trip_tz_seconds)):
                self._on_pick_pair(key))

        # Layout for the state cell.
        cell = QWidget()
        cell_layout = QHBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(6)
        cell_layout.addWidget(state_combo)
        cell_layout.addWidget(zone_picker)
        cell_layout.addWidget(offset_entry)
        cell_layout.addWidget(pick_btn)
        cell_layout.addStretch(1)
        table.setCellWidget(row, 1, cell)

        # Nudge cell.
        nudge_entry = HmsEntry(int(stored.nudge_seconds or 0))
        nudge_entry.setToolTip(tr(
            "Fine ±MM:SS adjustment added on top of the base, for "
            "residual clock drift independent of the timezone."))
        nudge_cell = QWidget()
        nudge_hbox = QHBoxLayout(nudge_cell)
        nudge_hbox.setContentsMargins(0, 0, 0, 0)
        nudge_hbox.addWidget(nudge_entry)
        nudge_hbox.addStretch(1)
        table.setCellWidget(row, 2, nudge_cell)

        # Total cell.
        total_label = QLabel("+0:00")
        total_label.setObjectName("PageHint")
        total_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, 3, QTableWidgetItem(""))   # placeholder
        table.setCellWidget(row, 3, total_label)

        rs.state_combo = state_combo
        rs.zone_picker = zone_picker
        rs.offset_entry = offset_entry
        rs.pick_pair_btn = pick_btn
        rs.nudge_entry = nudge_entry
        rs.total_label = total_label
        rs.container = cell

        # Seed widgets from the stored row.
        self._seed_row_from_stored(rs)

        # Wire updates.
        state_combo.currentIndexChanged.connect(
            lambda _idx, rr=rs: self._on_state_changed(rr))
        zone_picker.valueChanged.connect(
            lambda _v, rr=rs: self._refresh_total(rr))
        offset_entry.valueChanged.connect(
            lambda _v, rr=rs: self._refresh_total(rr))
        nudge_entry.valueChanged.connect(
            lambda _v, rr=rs: self._refresh_total(rr))

        self._on_state_changed(rs)        # set initial visibility
        self._refresh_total(rs)

    # ── State seeding + updates ───────────────────────────────────────

    @staticmethod
    def _seed_row_from_stored(rs: _RowState) -> None:
        """Pre-set widgets from ``rs.stored``. Honest representation
        (spec/125): zone only when ``configured_tz_seconds`` is set;
        otherwise the raw offset (or Correct when offset is 0)."""
        stored = rs.stored
        applied = int(stored.applied_offset_seconds or 0)
        nudge = int(stored.nudge_seconds or 0)
        base = applied - nudge
        if stored.configured_tz_seconds is not None:
            rs.state_combo.setCurrentIndex(_STATE_ZONE)
            rs.zone_picker.setValue(
                float(stored.configured_tz_seconds) / 3600.0)
        elif applied != 0 or base != 0:
            rs.state_combo.setCurrentIndex(_STATE_MEASURED)
            rs.offset_entry.setValue(base)
        else:
            rs.state_combo.setCurrentIndex(_STATE_CORRECT)

    @staticmethod
    def _on_state_changed(rs: _RowState) -> None:
        idx = rs.state_combo.currentIndex()
        rs.zone_picker.setVisible(idx == _STATE_ZONE)
        rs.offset_entry.setVisible(idx == _STATE_MEASURED)
        rs.pick_pair_btn.setVisible(idx == _STATE_MEASURED)
        CameraClockCorrectionDialog._refresh_total(rs)

    @staticmethod
    def _base_seconds(rs: _RowState) -> int:
        """The current base (excluding nudge) from the widget state."""
        idx = rs.state_combo.currentIndex()
        if idx == _STATE_CORRECT:
            return 0
        if idx == _STATE_ZONE:
            camera_tz_seconds = int(round(rs.zone_picker.value() * 3600))
            return int(rs.trip_tz_seconds) - camera_tz_seconds
        return int(rs.offset_entry.value())

    @staticmethod
    def _configured_tz_seconds(rs: _RowState) -> Optional[int]:
        """``configured_tz_seconds`` to persist for the current state —
        the spec/125 discriminator. Set only when state = Known TZ."""
        if rs.state_combo.currentIndex() != _STATE_ZONE:
            return None
        return int(round(rs.zone_picker.value() * 3600))

    @classmethod
    def _refresh_total(cls, rs: _RowState) -> None:
        total = cls._base_seconds(rs) + int(rs.nudge_entry.value())
        rs.total_label.setText(format_seconds_hms(total))

    # ── Pair-picker integration ───────────────────────────────────────

    def _on_pick_pair(self, key: Tuple[str, int]) -> None:
        """Open SyncPairPickerDialog for one row; on accept, write the
        raw delta into the row's offset entry. Reference id defaults to
        the first phone in the event, or any camera other than this one
        when no phone is present."""
        from mira.ui.base.sync_pair_picker import SyncPairPickerDialog

        rs = self._rows.get(key)
        if rs is None:
            return
        eg = self._gateway.open_event(self._event_id)
        try:
            cameras = eg.cameras()
            event_root = eg.event_root
        finally:
            eg.close()
        reference_id = self._pick_reference_for(rs.camera_id, cameras)
        if reference_id is None:
            QMessageBox.information(
                self, tr("No reference camera"),
                tr("No second camera/phone was found in this event — "
                   "the sync-pair picker needs both sides."),
            )
            return
        default_dir = str(event_root) if event_root is not None else ""
        dlg = SyncPairPickerDialog(
            camera_id=rs.camera_id,
            reference_id=reference_id,
            camera_default_dir=default_dir,
            reference_default_dir=default_dir,
            trip_tz=float(rs.trip_tz_seconds) / 3600.0,
            configured_tz=None,
            parent=self,
        )
        try:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            pair = dlg.selected_pair()
        finally:
            dlg.deleteLater()
        if pair is None:
            return
        raw_seconds = int(pair.offset.total_seconds())
        rs.state_combo.setCurrentIndex(_STATE_MEASURED)
        rs.offset_entry.setValue(raw_seconds)
        self._refresh_total(rs)

    @staticmethod
    def _pick_reference_for(camera_id: str,
                            cameras: List[m.Camera]) -> Optional[str]:
        """Prefer the first phone (alphabetical) other than ``camera_id``;
        otherwise the first other camera by id."""
        phones = sorted(
            c.camera_id for c in cameras
            if c.is_phone and c.camera_id != camera_id)
        if phones:
            return phones[0]
        others = sorted(
            c.camera_id for c in cameras if c.camera_id != camera_id)
        return others[0] if others else None

    # ── Apply ─────────────────────────────────────────────────────────

    def _collect_changes(
        self,
    ) -> List[Tuple[_RowState, m.CameraTzCorrection, List[int]]]:
        """Per row, build the target ``CameraTzCorrection`` + the
        segment's day_numbers; skip rows whose target matches their
        ``stored`` baseline (the existing short-circuit). Returns
        ``[(row_state, target_correction, day_numbers), ...]``."""
        days_by_tz: Dict[int, List[int]] = {
            int(s.trip_tz_seconds): list(s.day_numbers)
            for s in self._segments
        }
        out: List[Tuple[_RowState, m.CameraTzCorrection, List[int]]] = []
        for rs in self._rows.values():
            base = self._base_seconds(rs)
            nudge = int(rs.nudge_entry.value())
            applied = base + nudge
            cfg = self._configured_tz_seconds(rs)
            stored = rs.stored
            unchanged = (
                int(stored.applied_offset_seconds or 0) == applied
                and int(stored.nudge_seconds or 0) == nudge
                and stored.configured_tz_seconds == cfg
            )
            if unchanged:
                continue
            target = m.CameraTzCorrection(
                camera_id=rs.camera_id,
                trip_tz_seconds=int(rs.trip_tz_seconds),
                configured_tz_seconds=cfg,
                nudge_seconds=nudge,
                applied_offset_seconds=applied,
                applied_at=None,            # stamped at write
            )
            day_nums = days_by_tz.get(int(rs.trip_tz_seconds), [])
            out.append((rs, target, day_nums))
        return out

    def _on_apply(self) -> None:
        changes = self._collect_changes()
        if not changes:
            QMessageBox.information(
                self, tr("Nothing to apply"),
                tr("None of the corrections have been changed."),
            )
            return

        eg = self._gateway.open_event(self._event_id)
        try:
            blast_lines: List[str] = []
            total_files = 0
            for rs, target, day_nums in changes:
                n = sum(
                    len(eg.items(camera_id=rs.camera_id, day=d))
                    for d in day_nums
                )
                total_files += n
                blast_lines.append(
                    tr(
                        "  • {cam} ({tz}): {n} file(s), apply {new}"
                    ).replace("{cam}", rs.camera_id)
                    .replace("{tz}", _fmt_offset(
                        rs.trip_tz_seconds / 3600.0))
                    .replace("{n}", str(n))
                    .replace(
                        "{new}",
                        format_seconds_hms(target.applied_offset_seconds))
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
                self, tr("Confirm clock correction"),
                msg,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
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
                stamp = eg._now()
                for rs, target, day_nums in changes:
                    target.applied_at = stamp
                    # Mirror the predominant segment's row onto the
                    # legacy camera columns (back-compat summary).
                    mirror = (
                        int(rs.trip_tz_seconds)
                        == self._predominant_tz_seconds
                    )
                    eg.save_camera_tz_correction(
                        target, mirror_to_camera=mirror)
                    affected = eg.recompute_corrected_times(
                        rs.camera_id,
                        offset_seconds=int(target.applied_offset_seconds),
                        day_numbers=day_nums,
                    )
                    recomputed += len(affected)
                    QApplication.processEvents()
            finally:
                progress.close()
                progress.deleteLater()
        finally:
            eg.close()

        self._last_recomputed = recomputed
        QMessageBox.information(
            self, tr("Clock correction complete"),
            tr("Re-derived corrected times for {n} file(s).").replace(
                "{n}", str(recomputed)),
        )
        # Repopulate so the dialog shows the new baselines + clears the
        # "changed" state.
        for i in reversed(range(self._sections_layout.count())):
            w = self._sections_layout.itemAt(i).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows.clear()
        self._populate()

    @property
    def last_recomputed(self) -> int:
        """Count of items recomputed in the most recent Apply."""
        return self._last_recomputed
