"""PlanEditorDialog — table-driven trip-plan editor.

A reusable QDialog that edits a ``list[TripDay]``. Lives at the
foundation level because three different surfaces will host it:

  1. NewEventPage's "Edit plan…" button — initial plan creation
  2. Inside-event culler Save dialog — table pre-filled with EXIF
     dates + plan overlay; edits write back to the event's trip_days
  3. Standalone culler Save dialog — table pre-filled with EXIF
     dates only; edits feed folder names + sidecar metadata

Layout::

    ┌─────────────────────────────────────────────────────────┐
    │ Toolbar: [Import file] [Paste text] [Save as file]     │  (B2)
    ├─────────────────────────────────────────────────────────┤
    │ ┌───────────┬───────┬──────────────┬──────────────────┐│
    │ │ Date      │ TZ    │ Location     │ Description       ││
    │ ├───────────┼───────┼──────────────┼──────────────────┤│
    │ │ 2026-06-15│ -6.00 │ San José     │ Chegada           ││
    │ │ 2026-06-16│ -6.00 │ La Fortuna   │ Drive             ││
    │ │ ...       │ ...   │ ...          │ ...               ││
    │ └───────────┴───────┴──────────────┴──────────────────┘│
    ├─────────────────────────────────────────────────────────┤
    │ [+ Add day]  [- Remove selected]    [Cancel] [Apply]    │
    └─────────────────────────────────────────────────────────┘

Day-number is **derived** from the row order after sort-by-date.
Two rows with the same date keep their insertion order (the parser
allows this for gap-day cases — Nepal's 7-8 share 03/11 with Dia 7
coming first).

This iteration (B1) lands the base widget + add/remove/edit. Import
+ paste + save-as-file land in B2. Persistence of dialog size and
column widths lands in B3 when we have the right settings home.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from pathlib import Path

from PyQt6.QtCore import (
    QByteArray,
    QDate,
    QPoint,
    Qt,
)
from PyQt6.QtGui import QAction, QCloseEvent, QCursor, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.models import TripDay
from core.trip_plan_parser import format_trip_plan, parse_trip_plan
from mira.settings.model import Settings as _Settings
from mira.settings.repo import SettingsRepo as _SettingsRepo
from mira.ui.base.tz_picker import TzPicker
from mira.ui.i18n import tr  # ported into mira/ui (charter §4 step 7)


# Settings shim — the only data-layer rewire in this reused dialog (charter §5.2): its
# geometry / column-width / home_timezone reads + writes go through the new
# ``mira.settings`` (Domain 5) instead of the legacy ``core.settings``. Dict
# round-trip keeps the dialog's ``settings = load_settings(); settings[k]=…;
# save_settings(settings)`` pattern working unchanged (the keys it touches —
# ``plan_editor_geometry`` / ``plan_editor_column_widths`` / ``home_timezone`` — are real
# fields on the new Settings dataclass).
def load_settings() -> dict:
    return _SettingsRepo().load().to_dict()


def save_settings(data: dict) -> None:
    _SettingsRepo().save(_Settings.from_dict(data))


log = logging.getLogger(__name__)


# Column indices — used everywhere a cell is read or written.
# spec/47: COL_COUNTRY inserted between TZ and Location (Nelson 2026-06-06 —
# additional field, distinct from the free-text Location for city/venue/region).
COL_DATE = 0
COL_TZ = 1
COL_COUNTRY = 2
COL_LOC = 3
COL_DESC = 4
COL_BROWSE = 5
COL_COUNT = 6


class PlanEditorDialog(QDialog):
    """Editable table view of a trip plan.

    Usage::

        dlg = PlanEditorDialog(parent, trip_days=current_days)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_days = dlg.get_trip_days()
            # ... write new_days back to event.trip_days

    The Apply path renumbers ``day_number`` according to the final
    sort order, so callers always get a clean 1-based monotonic
    sequence.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        trip_days: Optional[list[TripDay]] = None,
        event=None,
        day_photos_provider=None,
        day_photo_counts=None,
        day_photo_paths: Optional[dict] = None,
        embedded: bool = False,
    ) -> None:
        """Standalone use: ``dlg.exec()`` → if Accepted, ``dlg.get_trip_days()``.

        Embedded use (``EventDialog`` tabs): ``embedded=True`` hides the
        internal Apply/Cancel bar — the host owns the buttons and calls
        :meth:`get_trip_days` directly on its Apply.
        """
        super().__init__(parent)
        self._embedded = embedded
        # When set (existing event), each row gets a "Browse…" button that opens that day's
        # photos+videos in the read-only Quick Sweep. ``day_photos_provider(row_date)`` ->
        # ``list[SourceItem]`` (the caller closes over the gateway). None (New Event) hides
        # the Browse column entirely. ``day_photo_counts`` (dict ``{iso_date: count}``) lets
        # each row show "Browse…" only for days that actually have photos, and a disabled
        # "Empty" button otherwise.
        self._day_photos_provider = day_photos_provider
        self._day_photo_counts = day_photo_counts or {}
        # spec/47 — Past-Photos plan-edit (no event yet) feeds the Browse column with
        # source-folder paths directly. ``day_photo_paths`` is ``{day_number: [Path, …]}``;
        # when set, the Browse column un-hides and clicks open ``DayBrowseDialog`` with
        # the source paths (no QuickSweepPage / no gateway needed).
        self._day_photo_paths = day_photo_paths or {}
        self.setWindowTitle(tr("Trip plan"))
        self.setModal(True)
        # Roomier default (Nelson eyeball: the fields were cramped).
        # User-resized geometry is restored over this in _restore_*.
        self.resize(960, 620)

        self._was_applied = False
        # When the dialog is opened against an existing event, the
        # Remove-day action checks the disk for photos under that
        # day. None = new-event creation, no disk to check (Nelson
        # 2026-05-23, task #108 invariant: every on-disk day folder
        # must have a plan entry, so the user can't remove a day
        # that has photos).
        self._event = event
        # Date cascade — forward-only (docs/14 §"Plan editor —
        # first-day edit shifts the whole trip"). Every row's date
        # editor wires itself as an anchor; moving any row N's date
        # shifts every row BELOW it (N+1..end) by the same delta,
        # rows ABOVE stay put (Nelson 2026-05-21, refining the
        # 2026-05-20 every-row model). Editing row 0 still cascades
        # the whole trip (everything is "below" row 0). TZ still
        # cascades from row 0 only — the trip's zone is absolute,
        # not relative.
        self._anchor_editor: Optional[QDateEdit] = None
        self._anchor_date: Optional[date] = None
        self._tz_anchor_editor: Optional[TzPicker] = None
        self._anchor_tz: Optional[float] = None
        # Re-entry guard for the date cascade — set to True while
        # _on_any_date_changed is rewriting peer rows so their
        # ``setDate`` calls don't re-trigger the handler. blockSignals
        # already covers the basic case, but the flag also protects
        # against any external trigger that bypasses blockSignals.
        self._cascading_dates: bool = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # ── Toolbar (Import / Paste / Save as) ─────────────────────
        toolbar = QHBoxLayout()
        self._import_button = QPushButton(tr("📂 Import file…"))
        self._import_button.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor),
        )
        self._import_button.setToolTip(tr(
            "Load a trip plan from a text file (the format the bundled "
            "plan_template.txt demonstrates). Replaces the current table "
            "contents. Use sidebar > PLAN > Download plan template to get a "
            "starter file."
        ))
        self._import_button.clicked.connect(self._on_import_file)
        toolbar.addWidget(self._import_button)

        self._paste_button = QPushButton(tr("📋 Paste text…"))
        self._paste_button.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor),
        )
        self._paste_button.setToolTip(tr(
            "Paste plan text directly (same format the parser accepts). "
            "Useful when an LLM produced the plan inline and you want "
            "to avoid the round-trip through a file. Replaces the "
            "current table contents."
        ))
        self._paste_button.clicked.connect(self._on_paste_text)
        toolbar.addWidget(self._paste_button)

        self._save_button = QPushButton(tr("💾 Save as file…"))
        self._save_button.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor),
        )
        self._save_button.setToolTip(tr(
            "Write the current table contents to a text file in the "
            "canonical plan format. Useful for backup, sharing, or "
            "editing externally before re-importing."
        ))
        self._save_button.clicked.connect(self._on_save_to_file)
        toolbar.addWidget(self._save_button)
        toolbar.addStretch(1)
        outer.addLayout(toolbar)

        # ── Table ──────────────────────────────────────────────────
        self._table = QTableWidget(0, COL_COUNT)
        self._configure_table()
        outer.addWidget(self._table, stretch=1)

        # ── Row-management buttons ─────────────────────────────────
        row_buttons = QHBoxLayout()
        self._add_button = QPushButton(tr("+ Add day"))
        self._add_button.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor),
        )
        self._add_button.setToolTip(tr(
            "Append a new day at the bottom of the table. The new day "
            "inherits the date (+1) and time zone from the last row, "
            "or sensible defaults if the table is empty."
        ))
        self._add_button.clicked.connect(self._on_add_day)
        row_buttons.addWidget(self._add_button)

        self._remove_button = QPushButton(tr("− Remove selected"))
        self._remove_button.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor),
        )
        self._remove_button.setToolTip(tr(
            "Remove the rows currently selected in the table. Photos "
            "already saved into a removed day's folder become orphans "
            "on disk — there's no auto-cleanup. Use with care."
        ))
        self._remove_button.clicked.connect(self._on_remove_selected)
        row_buttons.addWidget(self._remove_button)
        row_buttons.addStretch(1)
        outer.addLayout(row_buttons)

        # ── Apply / Cancel ─────────────────────────────────────────
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
        )
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText(tr("Apply"))
        ok_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_button = self._buttons.button(
            QDialogButtonBox.StandardButton.Cancel,
        )
        cancel_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._buttons.accepted.connect(self._on_apply)
        self._buttons.rejected.connect(self.reject)
        outer.addWidget(self._buttons)
        if embedded:
            # Host owns the button bar (a single Apply applies Info + Plan).
            self._buttons.hide()

        # Populate rows from the initial trip_days.
        for day in trip_days or []:
            self._append_row(day)
        self._rewire_anchor()

        # Restore the user's previous size + column widths from settings.
        # Always last in __init__ so it overrides the defaults set above.
        self._restore_user_prefs()

        # Focus-drift defence-in-depth lives in the app-wide focus guard
        # now (``mira.ui.base.focus_keeper`` — installed once by
        # ``apply_theme``). The cell editors still carry **no** per-field
        # tooltips (the column *headers* carry the hints, see
        # :meth:`_configure_table`) so no popup churn ever reaches the
        # global guard in the common case.

    # ── Construction helpers ───────────────────────────────────────

    def _configure_table(self) -> None:
        """One-time column header + selection setup."""
        headers = (
            (tr("Date"), tr(
                "Calendar date the photos of this row were taken. "
                "Matched against the EXIF DateTimeOriginal of imported "
                "photos to decide which day folder they go into."
            )),
            (tr("TZ"), tr(
                "UTC offset in hours. Examples: -3 (São Paulo), -6 "
                "(Costa Rica), +5.75 (Nepal). Inherits from the previous "
                "day at parse time when not specified explicitly."
            )),
            (tr("Country"), tr(
                "ISO country for the day (spec/47). Auto-filled from phone "
                "GPS at ingest — arrival country wins on travel days. "
                "Editable: pick from the dropdown of ISO 3166-1 countries. "
                "Distinct from Location, which stays free-text for city / "
                "venue / region detail."
            )),
            (tr("Location"), tr(
                "Geographic / logical location for the day (free text). "
                "Used by the Curate phase to group multiple days that "
                "share a location into a slideshow track.\n\n"
                "Light syntax:\n"
                "  · 'A > B' marks a travel day (origin → destination)\n"
                "  · ' # mode' appends the transport (must have a space "
                "before the #)\n"
                "Examples:  Kathmandu  ·  Kathmandu > Pokhara  ·  "
                "Kathmandu > Pokhara # bus"
            )),
            (tr("Description"), tr(
                "What you'll be doing this day (free text). Used as part "
                "of the day folder name on disk: 'Dia N - {description}/'."
            )),
            (tr("Browse"), tr(
                "Browse this day's photos and videos (read-only)."
            )),
        )
        for col, (title, tip) in enumerate(headers):
            item = QTableWidgetItem(title)
            item.setToolTip(tip)
            self._table.setHorizontalHeaderItem(col, item)

        self._table.verticalHeader().setVisible(False)
        # Rows grow to fit their cell widgets (else the fixed default row height clips taller
        # cells — Nelson eyeball 2026-05-31: the Browse button's bottom was cut off).
        self._table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        # No hover tracking — the focused cell editor must keep focus
        # while the user moves the mouse toward it (Nelson eyeball:
        # focus drifted on hover, breaking paste). The view itself
        # only takes focus on an explicit click, never hover/tab.
        self._table.setMouseTracking(False)
        self._table.viewport().setMouseTracking(False)
        self._table.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        # Cells are persistent embedded widgets — the view's own
        # item-editor is never wanted; disabling it removes any
        # view-driven edit/focus churn (focus-drift mitigation; the
        # root cause is still tracked — see _log_focus_change).
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection,
        )
        # Zebra striping reads from QPalette.AlternateBase via QSS
        # `alternate-background-color`. Doesn't visually affect rows
        # whose cells are all setCellWidget (the widgets paint over),
        # but kept enabled so partial-widget tables (future use) get
        # the striping for free.
        self._table.setAlternatingRowColors(True)
        # All columns user-draggable; Description (last) stretches to fill (the app-wide
        # table standard — spec/05 §4b, Nelson 2026-05-30). Restored widths (B3) override
        # the seeds below. spec/47: Country column inserted between TZ and Location at
        # 140 px (Brazil-class names fit comfortably; longer ones elide).
        from mira.ui.base.tables import make_columns_resizable
        make_columns_resizable(self._table, widths=(150, 96, 140, 220))
        # Browse column: present when either (a) an existing event provides items via
        # ``day_photos_provider`` (legacy path) or (b) Past-Photos plan-edit provides
        # ``day_photo_paths`` (spec/47). When shown, Description stretches; Browse stays narrow.
        header = self._table.horizontalHeader()
        browse_active = (
            self._day_photos_provider is not None or bool(self._day_photo_paths)
        )
        if browse_active:
            header.setStretchLastSection(False)
            header.setSectionResizeMode(COL_DESC, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(COL_BROWSE, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(COL_BROWSE, 100)
        else:
            self._table.setColumnHidden(COL_BROWSE, True)

    def _append_row(self, day: TripDay) -> None:
        """Add one row at the bottom of the table, populated from
        the given TripDay."""
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setCellWidget(row, COL_DATE, self._make_date_editor(day.date))
        self._table.setCellWidget(
            row, COL_TZ, self._make_tz_editor(day.tz_offset),
        )
        self._table.setCellWidget(
            row, COL_COUNTRY,
            self._make_country_editor(getattr(day, "country_code", None)),
        )
        loc = self._make_text_editor(day.location or "", "location")
        desc = self._make_text_editor(day.description or "", "description")
        self._table.setCellWidget(row, COL_LOC, loc)
        self._table.setCellWidget(row, COL_DESC, desc)
        if self._day_photos_provider is not None:
            iso = day.date.isoformat() if day.date else ""
            has_photos = self._day_photo_counts.get(iso, 0) > 0
            self._table.setCellWidget(row, COL_BROWSE, self._make_browse_cell(has_photos))
        elif self._day_photo_paths:
            paths = self._day_photo_paths.get(day.day_number, [])
            self._table.setCellWidget(
                row, COL_BROWSE, self._make_browse_cell(bool(paths)),
            )
        # Mouse-only Cut/Copy/Paste + cross-fill (Nelson eyeball:
        # reaching for the keyboard let the mouse drift and broke
        # Ctrl+V; there was no right-click menu). docs/14 §"Plan
        # editor — mouse-only cell editing".
        self._wire_cell_menu(loc, COL_LOC)
        self._wire_cell_menu(desc, COL_DESC)

    def _make_country_editor(self, code: Optional[str]):
        """The cell editor for the Country column — built via the shared
        :func:`mira.ui.base.country_picker.make_single_country_combo`
        helper (Nelson 2026-06-06 — same picker used in
        PreingestPlanConfirmDialog so the two surfaces stay identical)."""
        from mira.ui.base.country_picker import make_single_country_combo

        combo = make_single_country_combo(code)
        combo.setToolTip("")  # header carries the hint (focus-drift root fix)
        combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return combo

    def _make_date_editor(self, initial: Optional[date]) -> QDateEdit:
        """Build a QDateEdit for a Date cell.

        Defaults to ``initial`` if provided, else today. Calendar
        popup enabled; ISO format. Pointing-hand cursor on the
        dropdown arrow (Qt sets it automatically when popup is on).
        """
        editor = QDateEdit()
        editor.setCalendarPopup(True)
        editor.setDisplayFormat("yyyy-MM-dd")
        # Qt's default minimum is 1752-09-14 (Gregorian adoption);
        # the empty state shows that as initial text before any
        # interaction, which reads as a bug. Clamp to a sane modern
        # floor so every cell always shows a reasonable date.
        editor.setMinimumDate(QDate(2000, 1, 1))
        today = date.today()
        if initial is not None:
            editor.setDate(QDate(initial.year, initial.month, initial.day))
        else:
            editor.setDate(QDate(today.year, today.month, today.day))
        # Stash the last-known date on the widget so we can compute
        # the delta whenever the user changes it (cascade-from-any-row;
        # Nelson 2026-05-20). Any row's edit shifts all the others.
        editor.setProperty("_lastQDate", editor.date())
        editor.dateChanged.connect(
            lambda qd, ed=editor: self._on_any_date_changed(ed, qd))
        # No per-cell tooltip — the column header carries the hint (focus-drift root fix,
        # see __init__). Per-cell freeze policy lands with cull-day tracking; until then
        # every cell stays editable.
        return editor

    def _make_tz_editor(self, initial: Optional[float]) -> TzPicker:
        """Build the shared named-location ``TzPicker`` for a TZ cell
        (P4 — docs/14 §"TZ named-location picker").

        Replaces the old raw ``QDoubleSpinBox``: the user picks a
        place (*Kathmandu (Nepal) — UTC+05:45*) instead of typing a
        decimal, killing the +5:45-vs-+5.45 trap. The picker keeps a
        ``value()`` / ``setValue()`` / ``valueChanged(float)`` API so
        the first-day-TZ propagation wiring is unchanged. Default is
        the user's home TZ from settings when ``initial`` is None.
        """
        if initial is not None:
            picker = TzPicker(float(initial))
        else:
            home_tz = load_settings().get("home_timezone")
            picker = TzPicker(float(home_tz) if home_tz is not None else 0.0)
        picker.setToolTip("")  # no per-cell tooltip — focus-drift root fix (header carries it)
        return picker

    def _make_text_editor(self, initial: str, kind: str) -> QLineEdit:
        """Build a QLineEdit for a free-text cell (Location or
        Description). ``kind`` selects the placeholder + tooltip text
        so the user understands what the column expects."""
        editor = QLineEdit(initial)
        # No per-cell tooltip — the column header carries the hint (focus-drift root fix:
        # cell tooltips' QTipLabel churn was snapping focus to the cell under the cursor).
        # The placeholder still tells the user what the column expects.
        if kind == "location":
            editor.setPlaceholderText(tr("e.g. Kathmandu — or A > B # bus"))
        else:   # description
            editor.setPlaceholderText(tr("Describe the day's activities…"))
        # Strong focus so a click holds it while the hand moves to
        # the keyboard (the focus-drift Nelson reported).
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return editor

    # ── First-day propagation ──────────────────────────────────────

    def _rewire_anchor(self) -> None:
        """(Re)bind the trip anchors to the current row-0 TZ editor.

        Date cascade is now per-row (every row's date editor wires
        itself in :py:meth:`_make_date_editor`), so this only rebinds
        the TZ anchor — TZ still cascades from row 0 only, since the
        trip's zone is absolute, not relative.

        Called after any structural change (populate / replace / add
        / remove). Idempotent: disconnects the previous TZ anchor,
        reads the new row-0 date + TZ as baselines, and connects the
        TZ change signal. No row exists → no anchor."""
        if self._tz_anchor_editor is not None:
            try:
                self._tz_anchor_editor.valueChanged.disconnect(
                    self._on_anchor_tz_changed)
            except (TypeError, RuntimeError):
                pass
            self._tz_anchor_editor = None
        if self._table.rowCount() == 0:
            self._anchor_date = None
            self._anchor_editor = None
            self._anchor_tz = None
            return
        editor = self._table.cellWidget(0, COL_DATE)
        if editor is not None:
            qd = editor.date()
            self._anchor_date = date(qd.year(), qd.month(), qd.day())
            self._anchor_editor = editor
        else:
            self._anchor_date = None
            self._anchor_editor = None
        tz_editor = self._table.cellWidget(0, COL_TZ)
        if tz_editor is not None:
            self._anchor_tz = float(tz_editor.value())
            self._tz_anchor_editor = tz_editor
            tz_editor.valueChanged.connect(self._on_anchor_tz_changed)
        else:
            self._anchor_tz = None

    def _on_anchor_tz_changed(self, value: float) -> None:
        """Row 0's TZ changed → set every later row to the SAME value
        (TZ is absolute — the trip's zone — and the parser inherits it
        forward; no per-day delta as with dates). A non-first row's TZ
        is a deliberate mid-trip border crossing and does not cascade
        (only row 0's editor is bound). docs/14 §"Plan editor —
        first-day TZ propagates too"."""
        if self._anchor_tz is not None and value == self._anchor_tz:
            return
        self._anchor_tz = float(value)
        if self._table.rowCount() <= 1:
            return
        for row in range(1, self._table.rowCount()):
            tz_editor = self._table.cellWidget(row, COL_TZ)
            if tz_editor is None:
                continue
            tz_editor.blockSignals(True)
            tz_editor.setValue(float(value))
            tz_editor.blockSignals(False)

    def _on_any_date_changed(
        self, source_editor: QDateEdit, qd: QDate,
    ) -> None:
        """A row's date moved → shift every row BELOW it (forward
        only) by the same delta, preserving relative spacing.

        Forward-only contract (Nelson 2026-05-21, refining the
        2026-05-20 every-row-cascades model): editing row N keeps
        rows 0..N-1 anchored in place and shifts rows N+1..end by the
        delta. Rationale: the user normally fixes a date they
        mis-typed somewhere in the middle of the trip; the days
        BEFORE that point already happened (or were entered earlier
        and are trusted), the days AFTER that point are the ones
        whose spacing should track. Cascade still works in both
        directions of the delta — pulling a date backward drags later
        days back; pushing it forward pushes later days forward.
        Editing row 0 cascades the whole trip (same effective
        behaviour as the original row-0-only model).

        Reads the previous date from the editor's ``_lastQDate``
        property to compute the delta; rewrites the peer rows with
        ``blockSignals`` around each ``setDate`` plus a
        ``_cascading_dates`` re-entry guard. Row 0 keeps its
        TZ-propagation cascade separately via
        :py:meth:`_on_anchor_tz_changed`."""
        if self._cascading_dates:
            return
        prev = source_editor.property("_lastQDate")
        # Diagnostic — if Nelson reports cascade not firing in the
        # UI but tests pass, the log line below confirms the handler
        # is reached and shows what prev/qd were (so we know whether
        # we hit the early-return branch or the cascade branch).
        log.info(
            "PlanEditor date cascade: src=%s prev=%s new=%s",
            source_editor,
            prev.toString("yyyy-MM-dd") if isinstance(prev, QDate)
            and prev.isValid() else repr(prev),
            qd.toString("yyyy-MM-dd"),
        )
        if not isinstance(prev, QDate) or not prev.isValid():
            source_editor.setProperty("_lastQDate", qd)
            log.info("PlanEditor date cascade: no prev → store + skip")
            return
        delta = prev.daysTo(qd)
        source_editor.setProperty("_lastQDate", qd)
        # Keep the anchor (row-0 date) in sync so TZ-propagation
        # doesn't confuse itself if row 0 happens to be the source.
        if source_editor is self._anchor_editor:
            self._anchor_date = date(qd.year(), qd.month(), qd.day())
        # Resolve which row the source editor lives in — row indices
        # shift on add/remove, so we never cache them.
        source_row = self._row_of_editor(source_editor, COL_DATE)
        if source_row is None:
            return
        total = self._table.rowCount()
        if delta == 0 or source_row >= total - 1:
            log.info(
                "PlanEditor date cascade: delta=%d src_row=%d "
                "rows=%d → no forward rows to shift",
                delta, source_row, total,
            )
            return
        log.info(
            "PlanEditor date cascade: delta=%d src_row=%d, "
            "shifting rows %d..%d forward",
            delta, source_row, source_row + 1, total - 1,
        )
        self._cascading_dates = True
        try:
            for row in range(source_row + 1, total):
                editor = self._table.cellWidget(row, COL_DATE)
                if editor is None:
                    continue
                cur = editor.date()
                shifted = date(
                    cur.year(), cur.month(), cur.day(),
                ) + timedelta(days=delta)
                new_qd = QDate(
                    shifted.year, shifted.month, shifted.day)
                editor.blockSignals(True)
                editor.setDate(new_qd)
                editor.blockSignals(False)
                # Keep each editor's last-known date in sync so its
                # OWN next manual change computes a correct delta.
                editor.setProperty("_lastQDate", new_qd)
                if editor is self._anchor_editor:
                    self._anchor_date = shifted
        finally:
            self._cascading_dates = False

    # ── Mouse-only cell editing ────────────────────────────────────

    def _wire_cell_menu(self, editor: QLineEdit, col: int) -> None:
        """Give a text cell a right-click Cut/Copy/Paste/Select-All
        menu + a one-click Location↔Description cross-fill, so the
        whole edit is mouse-only and immune to the focus drift Nelson
        hit (hand leaves mouse → pointer moves → Ctrl+V missed)."""
        editor.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        editor.customContextMenuRequested.connect(
            lambda pos, e=editor, c=col: self._show_cell_menu(e, c, pos)
        )

    def _row_of_editor(self, editor: QWidget, col: int) -> Optional[int]:
        """Resolve an editor's *current* row (row indices shift on
        add/remove, so we never cache them)."""
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, col) is editor:
                return row
        return None

    def _copy_field_across(
        self, row: int, src_col: int, dst_col: int,
    ) -> None:
        """Copy one row's Location↔Description text. Testable seam the
        context-menu action calls."""
        src = self._table.cellWidget(row, src_col)
        dst = self._table.cellWidget(row, dst_col)
        if isinstance(src, QLineEdit) and isinstance(dst, QLineEdit):
            dst.setText(src.text())

    def _show_cell_menu(
        self, editor: QLineEdit, col: int, pos: QPoint,
    ) -> None:
        """Build + show the cell context menu at ``pos`` (editor-
        local). Mouse-only: every action is a menu click."""
        menu = QMenu(editor)
        has_sel = bool(editor.selectedText())
        clip = QGuiApplication.clipboard()
        can_paste = bool(clip is not None and clip.text())
        has_text = bool(editor.text())

        act_cut = menu.addAction(tr("Cut"))
        act_cut.setEnabled(has_sel and not editor.isReadOnly())
        act_cut.triggered.connect(editor.cut)
        act_copy = menu.addAction(tr("Copy"))
        act_copy.setEnabled(has_sel)
        act_copy.triggered.connect(editor.copy)
        act_paste = menu.addAction(tr("Paste"))
        act_paste.setEnabled(can_paste and not editor.isReadOnly())
        act_paste.triggered.connect(editor.paste)
        act_all = menu.addAction(tr("Select All"))
        act_all.setEnabled(has_text)
        act_all.triggered.connect(editor.selectAll)

        menu.addSeparator()
        row = self._row_of_editor(editor, col)
        if row is not None:
            if col == COL_LOC:
                a = menu.addAction(tr("Copy from Description"))
                a.triggered.connect(
                    lambda _=False, r=row: self._copy_field_across(
                        r, COL_DESC, COL_LOC))
            elif col == COL_DESC:
                a = menu.addAction(tr("Copy from Location"))
                a.triggered.connect(
                    lambda _=False, r=row: self._copy_field_across(
                        r, COL_LOC, COL_DESC))
        # (The day-photo browse moved off this right-click menu to a per-row "Browse…"
        # button — Nelson 2026-05-31. See _make_browse_button / _browse_day_for_row.)
        menu.exec(editor.mapToGlobal(pos))

    # ── Slots ──────────────────────────────────────────────────────

    def _on_add_day(self) -> None:
        """Append a new row with defaults derived from the last row
        when possible. New date = last + 1 day; new TZ = last TZ;
        Location + Description blank."""
        last_row = self._table.rowCount() - 1
        if last_row < 0:
            new_day = TripDay(
                day_number=1,
                date=date.today(),
                description="",
                tz_offset=None,    # editor falls back to home_tz
                location=None,
            )
        else:
            last_date_editor = self._table.cellWidget(last_row, COL_DATE)
            last_tz_editor = self._table.cellWidget(last_row, COL_TZ)
            qd: QDate = last_date_editor.date()
            last_date = date(qd.year(), qd.month(), qd.day())
            new_day = TripDay(
                day_number=last_row + 2,
                date=last_date + timedelta(days=1),
                description="",
                tz_offset=float(last_tz_editor.value()),
                location=None,
            )
        self._append_row(new_day)
        # First add on an empty table creates row 0 → (re)anchor.
        # Later adds append at the bottom (row 0 unchanged) — rewire
        # is idempotent so calling it unconditionally is safe.
        self._rewire_anchor()
        # Scroll to + select the new row so the user lands focus there.
        new_row = self._table.rowCount() - 1
        self._table.scrollToBottom()
        self._table.setCurrentCell(new_row, COL_DESC)

    def _on_remove_selected(self) -> None:
        """Remove the rows currently selected by the user.

        Walks selection ranges in reverse so row indices stay valid
        as removals happen. No-op when nothing is selected.

        Safety guard (Nelson 2026-05-23, task #108): when the
        dialog is opened against an existing event, refuses to
        remove any day that has photos on disk. The invariant is
        "every on-disk day folder must have a plan entry" — if the
        user wants to drop a day, they must move/discard the
        photos first."""
        rows = sorted(
            {idx.row() for idx in self._table.selectedIndexes()},
            reverse=True,
        )
        if not rows:
            return

        # File-presence check — only when the dialog is editing an
        # existing event. For new-event creation (event=None) there
        # are no files on disk yet so the guard is a no-op.
        if self._event is not None:
            from core.day_folder_reconciler import day_has_files
            # Map row's date → event TripDay (the source of the
            # actual day_number on disk). Rows whose date doesn't
            # match any event day are new edits the user added in
            # this session — no files exist for them yet.
            event_days_by_date = {
                d.date: d for d in (self._event.trip_days or [])
                if d.date is not None
            }
            blocked: list[tuple[int, int]] = []   # (day_number, file_count)
            for row in rows:
                row_day = self._read_day_at_row(row)
                if row_day is None or row_day.date is None:
                    continue
                event_day = event_days_by_date.get(row_day.date)
                if event_day is None:
                    continue
                n = day_has_files(self._event, event_day.day_number)
                if n > 0:
                    blocked.append((event_day.day_number, n))
            if blocked:
                from PyQt6.QtWidgets import QMessageBox
                lines = [
                    tr("Day {n} — {c} file(s) on disk")
                    .replace("{n}", str(day_n))
                    .replace("{c}", str(count))
                    for day_n, count in blocked
                ]
                QMessageBox.warning(
                    self, tr("Can't remove day(s) with photos"),
                    tr(
                        "The following day(s) have photos under them. "
                        "Remove or move the photos first, then try "
                        "again.\n\n{rows}\n\n"
                        "Mira requires every day with photos on "
                        "disk to keep its plan entry — otherwise the "
                        "photos would be orphaned."
                    ).replace("{rows}", "\n".join(lines)),
                )
                return

        for row in rows:
            self._table.removeRow(row)
        # Row 0 may have been removed → re-anchor on the new first
        # row (removal itself never shifts the remaining days).
        self._rewire_anchor()

    def _make_browse_cell(self, has_photos: bool) -> QWidget:
        """The Browse-column cell for one row (Nelson 2026-05-31 — replaces the legacy
        right-click "Browse photos for this day…"). A compact button wrapped in a margined
        container so it sits cleanly *inside* the row instead of overflowing it. Days with
        photos get an enabled "Browse…"; days with none get a disabled "Empty"."""
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(0)
        btn = QPushButton(tr("Browse…") if has_photos else tr("Empty"))
        # Compact height lives in QSS (#PlanBrowseCell, both themes) — a styled QPushButton
        # ignores setMaximumHeight (QStyleSheetStyle re-polish), so the role is the fix.
        btn.setObjectName("PlanBrowseCell")
        if has_photos:
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setToolTip(tr("Browse this day's photos and videos (read-only)."))
            btn.clicked.connect(
                lambda _=False, c=cell: self._browse_day_for_row(
                    self._row_of_editor(c, COL_BROWSE))
            )
        else:
            btn.setEnabled(False)
            btn.setToolTip(tr("No photos or videos for this day yet."))
        lay.addWidget(btn)
        return cell

    def _browse_day_for_row(self, row: Optional[int]) -> None:
        """Open the row's day photos+videos in a read-only browser.

        Two code paths:

        * **Existing event** (``day_photos_provider`` set): items come from
          the gateway, keyed on the row's current date; opens the read-only
          Quick Sweep so the user gets the full media-browse experience.
        * **Past-Photos plan-edit** (``day_photo_paths`` set, no event yet):
          source paths come from the scan directly; opens the lighter
          :class:`DayBrowseDialog` over those paths (no gateway, no
          QuickSweepPage — spec/47 Part C).
        """
        if row is None:
            return
        row_day = self._read_day_at_row(row)
        if row_day is None:
            return
        if self._day_photos_provider is not None:
            if row_day.date is None:
                return
            items = self._day_photos_provider(row_day.date)
            if not items:
                QMessageBox.information(
                    self,
                    tr("No photos for this day"),
                    tr(
                        "No photos or videos were found for {d}. (A day you just added or "
                        "re-dated has nothing on disk until you Apply the plan and import "
                        "photos for it.)"
                    ).replace("{d}", row_day.date.isoformat()),
                )
                return
            self._open_day_browser(items)
            return
        if self._day_photo_paths:
            # The row's day_number in the table is the position (1-based) the day
            # had when it was passed in. Match that against day_photo_paths.
            day_number = row + 1
            paths = list(self._day_photo_paths.get(day_number, []))
            if not paths:
                QMessageBox.information(
                    self,
                    tr("No photos for this day"),
                    tr("No photos or videos were scanned for this day."),
                )
                return
            from mira.ui.pages.day_browse_dialog import DayBrowseDialog
            dlg = DayBrowseDialog(paths, parent=self)
            dlg.exec()

    def _open_day_browser(self, items) -> None:
        """Host the read-only Quick Sweep (browse mode) in a modal over the day's items."""
        from mira.ui.picked.quick_sweep_page import QuickSweepPage

        host = QDialog(self)
        host.setWindowTitle(tr("Browse day"))
        host.setModal(True)
        host.resize(1100, 740)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        page = QuickSweepPage(browse_mode=True)
        page.cancelled.connect(host.accept)  # Back / Esc closes the browser
        lay.addWidget(page)
        if page.load(items):
            page.setFocus()
            host.exec()

    def _on_apply(self) -> None:
        self._was_applied = True
        self.accept()

    def _on_import_file(self) -> None:
        """Open a text file via QFileDialog, parse, replace rows.

        Default start location is the user's photos_base_path if set
        (the same dir where they likely keep plan files), else home.
        """
        settings = load_settings()
        default_dir = (
            settings.get("photos_base_path") or str(Path.home())
        )
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            tr("Import trip plan"),
            default_dir,
            tr("Text files (*.txt *.md *.text);;All files (*.*)"),
        )
        if not chosen:
            return
        try:
            text = Path(chosen).read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr("Could not read plan file"),
                tr("Failed to read {path}: {err}").replace(
                    "{path}", chosen,
                ).replace("{err}", str(exc)),
            )
            return
        self._parse_and_replace(text, source=chosen)

    def _on_paste_text(self) -> None:
        """Open a multi-line text input for the user to paste raw
        plan text, then parse + replace rows."""
        text, ok = QInputDialog.getMultiLineText(
            self,
            tr("Paste trip plan"),
            tr(
                "Paste the trip plan text below. Format must match what "
                "the parser accepts (see sidebar > PLAN > Download plan template)."
            ),
            "",
        )
        if not ok or not text.strip():
            return
        self._parse_and_replace(text, source="pasted text")

    def _on_save_to_file(self) -> None:
        """Walk the table, format to canonical plan text, write to a
        user-chosen file. Uses ``format_trip_plan`` so the output
        round-trips perfectly through ``parse_trip_plan`` later."""
        days = self.get_trip_days()
        if not days:
            QMessageBox.information(
                self,
                tr("Nothing to save"),
                tr("Add at least one day to the table before saving."),
            )
            return
        settings = load_settings()
        default_dir = (
            settings.get("photos_base_path") or str(Path.home())
        )
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            tr("Save trip plan"),
            str(Path(default_dir) / "plan.txt"),
            tr("Text files (*.txt);;All files (*.*)"),
        )
        if not chosen:
            return
        try:
            Path(chosen).write_text(
                format_trip_plan(days) + "\n", encoding="utf-8",
            )
            log.info("Plan saved to %s (%d day(s))", chosen, len(days))
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr("Could not save plan file"),
                tr("Failed to write {path}: {err}").replace(
                    "{path}", chosen,
                ).replace("{err}", str(exc)),
            )

    def _parse_and_replace(self, text: str, *, source: str) -> None:
        """Parse plan ``text`` and replace the table contents.

        Common path for both Import and Paste. ``source`` is the
        user-visible origin (file path or "pasted text") used only
        in the error message so the user knows what failed.
        """
        try:
            days = parse_trip_plan(text)
        except ValueError as exc:
            QMessageBox.warning(
                self,
                tr("Plan parse error"),
                tr(
                    "Could not parse the plan from {source}:\n\n{err}"
                ).replace("{source}", source).replace("{err}", str(exc)),
            )
            return
        if not days:
            QMessageBox.warning(
                self,
                tr("Empty plan"),
                tr(
                    "The plan from {source} parsed but produced no day "
                    "lines. Each day needs a prefix like 'Dia 1 -' or "
                    "'Day 1 -' or '1.'."
                ).replace("{source}", source),
            )
            return
        self._replace_rows(days)
        log.info(
            "Loaded %d day(s) into plan editor from %s",
            len(days), source,
        )

    def _replace_rows(self, trip_days: list[TripDay]) -> None:
        """Clear all rows and re-populate from ``trip_days``."""
        self._table.setRowCount(0)
        for day in trip_days:
            self._append_row(day)
        # Fresh load → re-anchor on the new row 0 (no shift; the
        # handler is only connected here, AFTER population, so
        # programmatic setDate during _append_row can't cascade).
        self._rewire_anchor()

    # ── Persistence (dialog geometry + column widths) ──────────────

    def _restore_user_prefs(self) -> None:
        """Apply saved geometry + column widths from settings.

        Falls back to the construction-time defaults on any failure
        (corrupted base64, missing keys, type mismatches). The user's
        Description column always stretches, so we only persist the
        first three widths.
        """
        settings = load_settings()
        geom_str = (settings.get("plan_editor_geometry") or "").strip()
        if geom_str:
            try:
                geom = QByteArray.fromBase64(geom_str.encode("ascii"))
                if not geom.isEmpty():
                    self.restoreGeometry(geom)
            except (ValueError, TypeError) as exc:
                log.debug("plan_editor_geometry restore skipped: %s", exc)
        widths = settings.get("plan_editor_column_widths") or []
        for col, width in zip((COL_DATE, COL_TZ, COL_LOC), widths):
            try:
                w = int(width)
                if w > 0:
                    self._table.setColumnWidth(col, w)
            except (TypeError, ValueError):
                continue

    def _save_user_prefs(self) -> None:
        """Persist current geometry + column widths.

        Fires on close regardless of Apply / Cancel — the user's
        chosen size is a preference independent of whether they
        applied edits.
        """
        settings = load_settings()
        try:
            geom_bytes = self.saveGeometry()
            settings["plan_editor_geometry"] = (
                geom_bytes.toBase64().data().decode("ascii")
            )
        except (RuntimeError, UnicodeDecodeError) as exc:
            log.debug("plan_editor_geometry save skipped: %s", exc)
        settings["plan_editor_column_widths"] = [
            self._table.columnWidth(c) for c in (COL_DATE, COL_TZ, COL_LOC)
        ]
        try:
            save_settings(settings)
        except OSError as exc:
            log.warning("Could not persist plan editor prefs: %s", exc)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        app = getattr(self, "_focus_dbg_app", None)
        keeper = getattr(self, "_focus_keeper", None)
        if app is not None and keeper is not None:
            try:
                app.removeEventFilter(keeper)
            except (TypeError, RuntimeError):
                pass
        self._save_user_prefs()
        super().closeEvent(event)

    # ── Public API ─────────────────────────────────────────────────

    def was_applied(self) -> bool:
        """True when the user clicked Apply (not Cancel / closed)."""
        return self._was_applied

    def _country_code_at_row(self, row: int) -> Optional[str]:
        """The Country column's current selection for ``row``, or None when
        blank / no editor present. Delegates to the shared
        :func:`country_code_from_combo` helper so the parsing rules match
        the preingest dialog and any future country-combo surface."""
        combo = self._table.cellWidget(row, COL_COUNTRY)
        if combo is None:
            return None
        from mira.ui.base.country_picker import country_code_from_combo
        return country_code_from_combo(combo)

    def _read_day_at_row(self, row: int) -> Optional[TripDay]:
        """Read one row's current editor state into a TripDay-like
        (day_number=0 placeholder). Used by the Remove-day guard
        to identify which day a row represents without renumbering.
        Returns None when the row is out of range."""
        if row < 0 or row >= self._table.rowCount():
            return None
        date_editor: QDateEdit = self._table.cellWidget(row, COL_DATE)
        tz_editor: TzPicker = self._table.cellWidget(row, COL_TZ)
        loc_editor: QLineEdit = self._table.cellWidget(row, COL_LOC)
        desc_editor: QLineEdit = self._table.cellWidget(row, COL_DESC)
        if not all([date_editor, tz_editor, loc_editor, desc_editor]):
            return None
        qd = date_editor.date()
        py_date = date(qd.year(), qd.month(), qd.day())
        return TripDay(
            day_number=0,
            date=py_date,
            description=desc_editor.text().strip(),
            tz_offset=float(tz_editor.value()),
            location=loc_editor.text().strip() or None,
            country_code=self._country_code_at_row(row),
        )

    def get_trip_days(self) -> list[TripDay]:
        """Walk the table, build a TripDay per row, return sorted by
        date with ``day_number`` renumbered 1..N in the final order.

        Rows with the same date keep their table-insertion order
        (parser also tolerates this — Nepal Dia 7 + Dia 8 sharing
        03/11 is a real case)."""
        rows: list[tuple[int, TripDay]] = []
        for row in range(self._table.rowCount()):
            date_editor: QDateEdit = self._table.cellWidget(row, COL_DATE)
            tz_editor: TzPicker = self._table.cellWidget(row, COL_TZ)
            loc_editor: QLineEdit = self._table.cellWidget(row, COL_LOC)
            desc_editor: QLineEdit = self._table.cellWidget(row, COL_DESC)
            qd = date_editor.date()
            py_date = date(qd.year(), qd.month(), qd.day())
            location = loc_editor.text().strip() or None
            description = desc_editor.text().strip()
            tz_value = float(tz_editor.value())
            country = self._country_code_at_row(row)
            rows.append((row, TripDay(
                day_number=0,    # renumbered below
                date=py_date,
                description=description,
                tz_offset=tz_value,
                location=location,
                country_code=country,
            )))
        # Sort by date; rows with the same date keep their original
        # insertion order via the (row, ...) tuple's second sort key.
        rows.sort(key=lambda r: (r[1].date, r[0]))
        result = []
        for n, (_orig_row, day) in enumerate(rows, start=1):
            day.day_number = n
            result.append(day)
        return result
