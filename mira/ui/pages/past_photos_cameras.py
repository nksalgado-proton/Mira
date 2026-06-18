"""Past-Photos — Step 3 (Nelson 2026-05-20 v8): per-camera TZ
configuration. Only shown when step 2's TZ-problem question was
answered "Yes". Three-column table with visible grid lines:

* **Camera** — read-only name (the subfolder name under root)
* **Mode** — combo: "I know the timezone" / "I don't know"
* **Value** — conditional on Mode:
  * "I know" → TzPicker (defaults to trip_tz)
  * "I don't know" → "Pick sync pair…" button (opens
    SyncPairPickerDialog against the auto-detected reference
    camera)

A column-3 ``QStackedWidget`` per row swaps between TZ picker and
pair button; the cell widget on the table stays stable.

The reference camera (used as the same-moment anchor in sync
pairs) is auto-detected: first row whose name contains a phone-
substring (iphone / pixel / celular / …), else the first row.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.clock_calibration import CalibrationPair
from core.fresh_source import SourceItem
from mira.ui.base.tz_picker import TzPicker
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


# Phone-name substrings used to auto-detect the reference camera.
# Match: "iPhone_13", "Nelson_celular", "Galaxy_S22", etc.
_PHONE_SUBSTRINGS = (
    "phone", "iphone", "android",
    "celular", "telefone", "móvel", "movel",
    "pixel", "samsung", "galaxy", "redmi", "xiaomi", "huawei",
)


def _looks_like_phone(name: str) -> bool:
    n = name.lower()
    return any(s in n for s in _PHONE_SUBSTRINGS)


def _fmt_tz(value: float) -> str:
    """Format a TZ float as ``UTC±H:MM`` for the heading text."""
    sign = "+" if value >= 0 else "-"
    abs_v = abs(value)
    h = int(abs_v)
    m = int(round((abs_v - h) * 60))
    if m == 0:
        return f"UTC{sign}{h}"
    return f"UTC{sign}{h}:{m:02d}"


# Minimum width for column 3 widgets (TzPicker + pair button) so
# the two render identically wide regardless of mode (Nelson
# 2026-05-20 v9). Tuned to comfortably fit
# "<Location> — UTC±HH:MM" labels in the TzPicker.
_VALUE_COL_MIN_WIDTH = 240


# ── Row state ─────────────────────────────────────────────────


class _CamRow:
    """Per-camera widget bundle held on PastPhotosCamerasDialog.

    The combo's data carries the mode key ("know" / "unknown").
    The stack's current index mirrors that — index 0 = TZ picker;
    index 1 = pair button. The pair (when set) lives here so the
    final commit can read it back."""

    __slots__ = (
        "camera_id", "mode_combo", "tz_picker",
        "pair_button", "_pair", "col3_stack",
    )

    def __init__(
        self, camera_id: str, trip_tz: float,
        *, pair_pick_available: bool = True,
    ) -> None:
        self.camera_id = camera_id

        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("I know the timezone"), "know")
        if pair_pick_available:
            self.mode_combo.addItem(
                tr("I don't know — pick a sync pair"), "unknown",
            )
        self.mode_combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self.tz_picker = TzPicker()
        self.tz_picker.setValue(trip_tz)
        # Lock a minimum width so the picker + the pair button line
        # up at the same visual size in column 3 (Nelson 2026-05-20
        # v9). 240px is comfortable for "Location — UTC±HH:MM".
        self.tz_picker.setMinimumWidth(_VALUE_COL_MIN_WIDTH)

        # Pair button — same width as the TZ picker so the column
        # has a stable visual size regardless of which mode the row
        # is in. Text doubles as the pair-status readout: when a
        # pair is set the button reads "Δ+8:45" + tooltip-explained
        # change semantics; no separate label needed.
        self.pair_button = QPushButton(tr("Pick a pair of photos"))
        self.pair_button.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self.pair_button.setMinimumWidth(_VALUE_COL_MIN_WIDTH)
        self.pair_button.setToolTip(tr(
            "Open the side-by-side picker: choose one photo on "
            "this camera + one on the reference camera (the same "
            "moment in real life). Once set, click again to change "
            "the pair."
        ))

        # Stack swaps between TZ picker and pair UI based on mode.
        self.col3_stack = QStackedWidget()
        self.col3_stack.addWidget(self.tz_picker)
        self.col3_stack.addWidget(self.pair_button)
        # Wire the combo to the stack — single source of truth.
        self.mode_combo.currentIndexChanged.connect(
            self.col3_stack.setCurrentIndex)

        self._pair: Optional[CalibrationPair] = None

    # ── Pair state ────────────────────────────────────────────

    def pair(self) -> Optional[CalibrationPair]:
        return self._pair

    def set_pair(self, pair: Optional[CalibrationPair]) -> None:
        self._pair = pair
        if pair is None:
            self.pair_button.setText(tr("Pick a pair of photos"))
            return
        # Show the resulting offset directly on the button so a row
        # in "unknown" mode has a single visible status indicator at
        # the same width as the TZ picker (Nelson 2026-05-20 v9).
        delta = pair.reference_time - pair.camera_time
        secs = int(delta.total_seconds())
        sign = "+" if secs >= 0 else "-"
        h, rem = divmod(abs(secs), 3600)
        m = rem // 60
        self.pair_button.setText(
            tr("Δ{sign}{h}:{m:02d} (click to change)").replace(
                "{sign}", sign).replace(
                "{h}", str(h)).replace("{m:02d}", f"{m:02d}"))

    def mode(self) -> str:
        return self.mode_combo.currentData() or "know"

    def configured_tz(self) -> float:
        return float(self.tz_picker.value())


# ── Dialog ────────────────────────────────────────────────────


class PastPhotosCamerasDialog(QDialog):
    """Step-3 modal: per-camera TZ / sync-pair configuration.

    Emits :attr:`accepted_with_inputs(per_camera)` where
    ``per_camera`` is a ``dict[camera_id, dict]`` with keys:
    * ``mode`` — "know" or "unknown"
    * ``configured_tz`` — float UTC offset (always present, even in
      "unknown" mode; defaults to ``trip_tz``)
    * ``pair`` — Optional[CalibrationPair] (only set in "unknown"
      mode after the user picked a pair)
    """

    accepted_with_inputs = pyqtSignal(dict)

    def __init__(
        self,
        *,
        camera_ids: list[str] | None = None,
        source_index=None,                     # core.source_index.SourceIndex
        root_dir: str,
        trip_tz: float,
        ordinal: tuple[int, int] = (1, 1),
        day_numbers: list[int] | None = None,
        parent: QWidget | None = None,
        phone_reference_id: Optional[str] = None,
        picker_factory=None,
        recognition_items: Optional[Sequence[SourceItem]] = None,
    ) -> None:
        """Two entry points (Nelson 2026-05-21):

        * ``source_index=<SourceIndex>`` — EXIF-scan-first path. The
          caller has already scanned the source root and grouped
          files by EXIF Make+Model; we display one row per camera
          with the file count + date range as a hint, and use the
          scan's ``is_phone`` flag to pick the reference.

        * ``camera_ids=[...]`` — legacy folder-name path (kept for
          existing tests and any caller that hasn't migrated yet).
          We fall back to the ``_looks_like_phone`` substring
          heuristic on the folder names.

        ``ordinal`` + ``day_numbers`` (Nelson 2026-05-22, multi-TZ
        refactor): when the trip plan has more than one TZ, the
        caller (PastPhotosDialog) opens this dialog ONCE PER TZ. The
        ordinal is the (step, total) so the user sees "Step 2 of 3"
        in the heading; day_numbers lists the days this TZ covers so
        the user knows exactly which slice of the trip the
        calibration applies to. Both default to single-TZ semantics
        (1 of 1, no day list) for back-compat.

        ``phone_reference_id`` (Collect flow, Nelson 2026-06-09): when
        set, the named camera is the pair-pick reference and is NOT
        shown as a row — phones are never targets of calibration; they
        carry TZ in EXIF. Caller is responsible for excluding it from
        ``camera_ids``. When ``None``, the legacy phone-substring
        auto-detect runs; if it finds no phone AND ``picker_factory``
        is not provided, the "I don't know" pair-pick mode is hidden
        per row (Path A only).

        ``picker_factory`` (Collect flow, Nelson 2026-06-09): a
        ``Callable[[str], Callable[[QWidget], Optional[Path]]]`` —
        given a camera_id, returns the picker callback to wire into
        :class:`SyncPairPickerDialog`. Replaces the legacy QFileDialog
        when the source layout doesn't have per-camera subfolders.
        """
        super().__init__(parent)
        self._ordinal = ordinal
        self._day_numbers = sorted(day_numbers or [])
        self._picker_factory = picker_factory
        title = (
            tr("Per-camera timezones — Step {n} of {tot}")
            .replace("{n}", str(ordinal[0]))
            .replace("{tot}", str(ordinal[1]))
        )
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(720)
        self.setMinimumHeight(360)
        self._trip_tz = trip_tz
        self._root_dir = root_dir
        self._source_index = source_index
        # Explicit per-photo items for the recognition flow (spec/88 §3).
        # Callers with EXIF in hand but no full SourceIndex pass them here —
        # the Collect flow does this so the recognition surface fires even
        # though it opens this dialog without a SourceIndex.
        self._recognition_items: list[SourceItem] = list(
            recognition_items or []
        )
        # ``_camera_meta[camera_id]`` → (file_count, date_range, is_phone)
        # for the row-label hint. Empty in the legacy camera_ids path.
        self._camera_meta: dict[str, tuple[int, object, bool]] = {}
        if source_index is not None:
            scanned = source_index.cameras_sorted()
            camera_ids = [c.camera_id for c in scanned]
            for c in scanned:
                self._camera_meta[c.camera_id] = (
                    c.file_count, c.date_range, c.is_phone,
                )
            # Reference: first phone (per scan), else first camera.
            self._reference_id = next(
                (c.camera_id for c in scanned if c.is_phone),
                scanned[0].camera_id if scanned else "",
            )
        else:
            camera_ids = list(camera_ids or [])
            if phone_reference_id is not None:
                # Collect flow: caller declared the reference explicitly
                # and pre-filtered it out of camera_ids — every row in
                # this dialog is a calibration target.
                self._reference_id = phone_reference_id
            else:
                # Legacy: first phone by substring, else first camera.
                self._reference_id = next(
                    (c for c in camera_ids if _looks_like_phone(c)),
                    camera_ids[0] if camera_ids else "",
                )
        # Pair-pick mode is available iff there's a phone reference (either
        # explicit or substring-detected) OR a custom picker_factory is
        # provided (some callers might want pair-pick against an arbitrary
        # reference). When NO phone is detectable AND no factory, Path A
        # (TZ pick) is the only option, so we suppress the "I don't know"
        # combo item entirely so the user can't get stuck on an unusable
        # mode.
        pair_pick_available = (
            phone_reference_id is not None
            or any(_looks_like_phone(c) for c in camera_ids)
            or picker_factory is not None
        )
        self._rows: dict[str, _CamRow] = {}
        for cam in camera_ids:
            row = _CamRow(
                cam, trip_tz, pair_pick_available=pair_pick_available,
            )
            row.pair_button.clicked.connect(
                lambda _checked=False, c=cam: self._pick_pair(c))
            # spec/88 — switching the mode combo to "I don't know" fires
            # the recognition flow immediately, no extra click on the
            # stale "Pick a pair" button. We pass the cam_id through the
            # default-arg trick so each row's lambda binds the right
            # camera (Python closure-in-loop trap).
            row.mode_combo.currentIndexChanged.connect(
                lambda _idx, c=cam, r=row: self._on_mode_changed(c, r))
            self._rows[cam] = row
        self._build_ui()

    def _on_mode_changed(self, camera_id: str, row: "_CamRow") -> None:
        """Auto-open the pair-pick flow when the row enters "I don't know"
        mode for the first time. Re-clicks of the same mode (or going
        back to "I know") do nothing — the button stays available for
        rebinding the pair later, but the user shouldn't have to chase a
        second click to start picking."""
        if row.mode() != "unknown":
            return
        if row.pair() is not None:
            return                      # already has a pair — don't re-open
        self._pick_pair(camera_id)

    # ── UI ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(14)

        step_n, step_total = self._ordinal
        if step_total > 1:
            heading_text = tr(
                "Per-camera timezone — Step {n} of {tot}"
            ).replace("{n}", str(step_n)).replace(
                "{tot}", str(step_total))
        else:
            heading_text = tr("Per-camera timezone configuration")
        heading = QLabel(heading_text)
        heading.setObjectName("PageHeading")
        outer.addWidget(heading)

        # When the plan has multiple TZs the caller passes day_numbers
        # so the heading + intro make clear WHICH days this dialog's
        # answer applies to. Single-TZ case prints the simpler intro.
        if self._day_numbers and step_total > 1:
            days_str = ", ".join(
                f"Dia {n}" for n in self._day_numbers)
            tz_str = _fmt_tz(self._trip_tz)
            intro = QLabel(tr(
                "This calibration covers the days in timezone "
                "<b>{tz}</b>: <b>{days}</b>.<br>"
                "For each camera, either pick the timezone its clock "
                "was set to (default = the trip's timezone, no shift) "
                "or — if you don't know — pick two photos taken at "
                "the same moment, one on the camera and one on the "
                "reference."
            ).replace("{tz}", tz_str).replace("{days}", days_str))
            intro.setTextFormat(Qt.TextFormat.RichText)
        else:
            intro = QLabel(tr(
                "For each camera, tell Mira how to determine the "
                "timezone its clock was set to during the trip. If you "
                "know the timezone, pick it directly. If you don't, "
                "select two photos taken at the same moment — one on "
                "the camera, one on the reference."
            ))
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # Three-column table with visible grid lines.
        self._table = QTableWidget(len(self._rows), 3, self)
        self._table.setHorizontalHeaderLabels([
            tr("Camera"), tr("Mode"), tr("Value"),
        ])
        self._table.setShowGrid(True)
        self._table.setGridStyle(Qt.PenStyle.SolidLine)
        self._table.setStyleSheet(
            "QTableWidget { gridline-color: #9ca3af; }"
        )
        self._table.verticalHeader().setVisible(False)
        # All columns user-draggable; the last (Value) stretches to fill — the app-wide
        # table standard (spec/05 §4b, Nelson 2026-05-30: always use resizable headers).
        from mira.ui.base.tables import make_columns_resizable
        make_columns_resizable(self._table, widths=(240, 200))
        self._table.horizontalHeader().setMinimumSectionSize(120)
        # Row height — TZ picker is ~24px after the v8 slim-down.
        self._table.verticalHeader().setDefaultSectionSize(34)

        for r, (cam_id, row) in enumerate(self._rows.items()):
            # Column 0: camera name + (EXIF-scan path) file count +
            # date range hint, plus a "(reference)" marker on the
            # auto-detected reference row so the user knows which
            # one anchors sync pairs.
            label = cam_id
            meta = self._camera_meta.get(cam_id)
            if meta is not None:
                file_count, date_range, _ = meta
                bits = [f"{file_count} file(s)"]
                if date_range is not None:
                    earliest, latest = date_range
                    if earliest == latest:
                        bits.append(earliest.strftime("%Y-%m-%d"))
                    else:
                        bits.append(
                            f"{earliest.strftime('%b %d')} – "
                            f"{latest.strftime('%b %d, %Y')}"
                        )
                label = f"{cam_id}  ·  {', '.join(bits)}"
            if cam_id == self._reference_id:
                label = f"{label}  ({tr('reference')})"
            name_item = QTableWidgetItem(label)
            name_item.setFlags(
                name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 0, name_item)
            # Column 1: mode combo.
            self._table.setCellWidget(r, 1, row.mode_combo)
            # Column 2: stacked widget (TZ picker / pair button).
            self._table.setCellWidget(r, 2, row.col3_stack)

        outer.addWidget(self._table, stretch=1)

        # Buttons.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel, parent=self)
        self._next_btn = QPushButton(tr("Next  →"))
        self._next_btn.setDefault(True)
        self._next_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._next_btn.clicked.connect(self._on_next)
        buttons.addButton(
            self._next_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ── Sync-pair handler ─────────────────────────────────────

    # Recognition-flow outcomes (spec/88).
    _REC_CONFIRMED = "confirmed"   # user recognized a pair → row.set_pair done
    _REC_FALLBACK = "fallback"     # user opted into the manual picker
    _REC_CANCEL = "cancel"         # user dismissed the dialog
    _REC_UNAVAILABLE = "unavailable"  # no source EXIF / no phone overlap

    def _pick_pair(self, camera_id: str) -> None:
        """Open the recognition flow first (spec/88), falling back to the
        legacy hand-picked :class:`SyncPairPickerDialog` when recognition
        is unavailable or the user opts out.

        When ``picker_factory`` is configured (Collect flow), the
        factory is called with each camera_id to build a custom picker
        callback — replaces the legacy QFileDialog-based picker since
        the new Collect source layout doesn't have per-camera
        subfolders for QFileDialog to scope to."""
        if camera_id == self._reference_id:
            QMessageBox.information(
                self, tr("Reference camera"),
                tr("This is the reference camera — it can't be "
                   "paired against itself. Pick its timezone "
                   "directly (or change the Mode column)."),
            )
            return

        outcome = self._try_recognition(camera_id)
        if outcome in (self._REC_CONFIRMED, self._REC_CANCEL):
            # Recognition handled the call — either the user confirmed a
            # pair or they explicitly cancelled. Don't show the manual
            # picker behind their back.
            return

        # Either recognition wasn't available (no EXIF source / no phone
        # overlap — spec/88 §5) or the user clicked "Use manual pair…":
        # last-resort manual picker.
        self._pick_pair_manual(camera_id)

    def _try_recognition(self, camera_id: str) -> str:
        """spec/88 propose-and-confirm flow. Returns one of
        :data:`_REC_CONFIRMED` / :data:`_REC_FALLBACK` / :data:`_REC_CANCEL` /
        :data:`_REC_UNAVAILABLE` — the caller decides whether to open the
        legacy manual picker based on the outcome.

        The per-photo data comes from ``recognition_items`` (Collect flow)
        when supplied, else from the SourceIndex (Past Photos EXIF-scan path).
        Without either, recognition is UNAVAILABLE and the caller falls back
        to the manual picker."""
        log.info("spec/88 recognition: enter for %r (ref=%r, recog_items=%d, "
                 "source_index=%s)",
                 camera_id, self._reference_id,
                 len(self._recognition_items),
                 "set" if self._source_index is not None else "None")
        if not self._reference_id:
            log.info("spec/88 recognition: UNAVAILABLE — no reference_id")
            return self._REC_UNAVAILABLE

        if self._recognition_items:
            items = self._recognition_items
        elif self._source_index is not None:
            items = self._source_index.items
        else:
            log.info("spec/88 recognition: UNAVAILABLE — no recognition data")
            return self._REC_UNAVAILABLE

        cam_items = [it for it in items if it.camera_id == camera_id]
        phone_items = [
            it for it in items if it.camera_id == self._reference_id
        ]
        log.info("spec/88 recognition: cam_items=%d phone_items=%d "
                 "(distinct camera_ids in pool: %s)",
                 len(cam_items), len(phone_items),
                 sorted({it.camera_id for it in items}))
        if not cam_items or not phone_items:
            log.info("spec/88 recognition: UNAVAILABLE — empty cam or phone "
                     "items (cam_id %r vs ref_id %r)",
                     camera_id, self._reference_id)
            return self._REC_UNAVAILABLE

        from core.clock_recognition import find_candidate_pairs
        clusters = find_candidate_pairs(cam_items, phone_items)
        log.info("spec/88 recognition: %d cluster(s) generated", len(clusters))
        if not clusters:
            phone_with_tz = sum(
                1 for it in phone_items
                if it.tz_offset_minutes is not None
            )
            log.info("spec/88 recognition: UNAVAILABLE — no clusters formed "
                     "(phone items with tz_offset_minutes: %d/%d)",
                     phone_with_tz, len(phone_items))
            # Sparse overlap / no plausible cluster — spec/88 §5 routes to
            # manual rather than show the user an empty recognition page.
            return self._REC_UNAVAILABLE
        log.info("spec/88 recognition: opening RecognitionDialog with "
                 "%d cluster(s)", len(clusters))

        from mira.ui.pages.clock_recognition_dialog import (
            ApplyImpact,
            RecognitionDialog,
        )

        def impact_for(pair) -> ApplyImpact:
            shift = pair.to_calibration_pair().offset
            moves = sum(
                1 for it in cam_items
                if it.timestamp is not None
                and (it.timestamp + shift).date() != it.timestamp.date()
            )
            return ApplyImpact(
                photo_count=len(cam_items),
                shift=shift,
                day_moves=moves,
            )

        dlg = RecognitionDialog(
            camera_id=camera_id,
            reference_id=self._reference_id,
            clusters=clusters,
            impact_for=impact_for,
            parent=self,
        )
        accepted = (dlg.exec() == QDialog.DialogCode.Accepted)
        cal_pair = dlg.selected_pair()
        fallback = dlg.fallback_to_manual
        dlg.deleteLater()

        if accepted and cal_pair is not None and not fallback:
            self._rows[camera_id].set_pair(cal_pair)
            return self._REC_CONFIRMED
        if fallback:
            return self._REC_FALLBACK
        return self._REC_CANCEL

    def _pick_pair_manual(self, camera_id: str) -> None:
        """The legacy hand-picked sync-pair picker — kept as the
        last-resort fallback per spec/88 §1 ("Manual is the last resort").
        Sibling to :meth:`_try_recognition`."""
        from mira.ui.base.sync_pair_picker import SyncPairPickerDialog

        row = self._rows[camera_id]
        cam_dir = str(Path(self._root_dir) / camera_id)
        ref_dir = str(Path(self._root_dir) / self._reference_id)
        cam_callback = None
        ref_callback = None
        if self._picker_factory is not None:
            cam_callback = self._picker_factory(camera_id)
            ref_callback = self._picker_factory(self._reference_id)
        dlg = SyncPairPickerDialog(
            camera_id=camera_id,
            reference_id=self._reference_id,
            camera_default_dir=cam_dir,
            reference_default_dir=ref_dir,
            trip_tz=self._trip_tz,
            # No configured_tz in this dialog — the user is here
            # because they DON'T know. Snap-to-15-min path.
            configured_tz=None,
            parent=self,
            cam_picker_callback=cam_callback,
            ref_picker_callback=ref_callback,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            row.set_pair(dlg.selected_pair())
        dlg.deleteLater()

    # ── Result ─────────────────────────────────────────────────

    def _on_next(self) -> None:
        # Nelson 2026-05-20 v9: every "unknown" non-reference row
        # MUST have a pair picked before the user can leave the
        # screen. (TzPicker is always populated — it defaults to
        # trip_tz on populate — so "know" rows are always valid.)
        missing: list[str] = []
        for cam_id, row in self._rows.items():
            if cam_id == self._reference_id:
                continue
            if row.mode() == "unknown" and row.pair() is None:
                missing.append(cam_id)
        if missing:
            QMessageBox.warning(
                self,
                tr("Sync pair missing"),
                tr(
                    "These cameras are set to 'I don't know' but "
                    "haven't had a sync pair picked yet:\n\n"
                    "  • {names}\n\n"
                    "Either pick a sync pair for each, or switch "
                    "the Mode column to 'I know the timezone' and "
                    "set the TZ directly."
                ).replace("{names}", "\n  • ".join(missing)),
            )
            return                                     # stay on dialog
        result: dict[str, dict] = {}
        for cam_id, row in self._rows.items():
            mode = row.mode()
            # Reference camera always uses "know" with trip_tz —
            # it can't pair against itself, so its only meaningful
            # output is its TZ (defaulting to trip_tz).
            if cam_id == self._reference_id:
                mode = "know"
            result[cam_id] = {
                "mode": mode,
                "configured_tz": row.configured_tz(),
                "pair": row.pair(),
                "is_reference": cam_id == self._reference_id,
            }
        self.accepted_with_inputs.emit(result)
        self.accept()

    def per_camera(self) -> dict[str, dict]:
        """Read-back API mirroring the signal payload."""
        out: dict[str, dict] = {}
        for cam_id, row in self._rows.items():
            mode = row.mode() if cam_id != self._reference_id else "know"
            out[cam_id] = {
                "mode": mode,
                "configured_tz": row.configured_tz(),
                "pair": row.pair(),
                "is_reference": cam_id == self._reference_id,
            }
        return out

    @property
    def reference_id(self) -> str:
        return self._reference_id
