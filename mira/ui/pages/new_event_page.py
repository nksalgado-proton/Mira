"""New Event — the plan-only event creator (charter §4 step 7, build-order #2).

**Reused from the legacy ``ui/pages/new_event.py`` (`NewEventPage`)** (Nelson —
[[feedback_reuse_legacy_ui_dont_recreate]]): the surface shape is the legacy's, verbatim —
name + start date, a plan-status line, *Edit plan…* (the ported
:class:`~mira.ui.base.plan_editor_dialog.PlanEditorDialog`), *Import plan from folder…*,
the TZ-mismatch heads-up, and the "where the event lives" message. The **only change is the
data seam** (charter §5.2):

- the **create commit**: legacy ``save_event`` + ``core.event_service.create_folder_structure``
  → build an :class:`~mira.store.models.EventDocument` (``Event`` + ``trip_days``, **no
  items**) and call **``Gateway.create_event(doc, event_root)``** — the same call ingest uses.
  It materialises the ``event.db`` and registers the index row; the pipeline folder tree is a
  rebuildable projection (charter §3), so it is *not* eagerly created here (an empty plan-only
  event has nothing to project yet);
- ``photos_base_path`` reads → ``Gateway.photos_base_path()``;
- ``home_timezone`` reads → ``Gateway.settings``;
- name-collision matches → ``Gateway.list_events()`` (the helper is pure-UI);
- the ``data.event_store`` + legacy-settings calls are gone.

The plan-text path (``Edit plan…`` / ``Import plan from folder…``) reuses the Qt-free legacy
logic verbatim (``parse_trip_plan`` / ``generate_plan_skeleton_from_per_day``); the plan editor
works in the legacy ``core.models.TripDay`` shape, converted to the store ``TripDay`` only when
the document is assembled.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Optional
from pathlib import Path

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.models import TripDay
from core.path_builder import sanitize_folder_name
from core.trip_plan_parser import parse_trip_plan
from core.trip_plan_skeleton import generate_plan_skeleton_from_per_day
from mira import event_classification
from mira.gateway import Gateway
from mira.store import models as m
from mira.ui.base.classification_panel import ClassificationPanel
from mira.ui.base.plan_editor_dialog import PlanEditorDialog
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_tz(offset: float) -> str:
    """Render a UTC offset as ``UTC±H:MM`` for user-facing messages.
    Whole-hour offsets drop the minutes (``UTC-3``); fractional ones like Nepal +5:45
    render as ``UTC+5:45``."""
    sign = "+" if offset >= 0 else "-"
    abs_off = abs(offset)
    hours = int(abs_off)
    minutes = int(round((abs_off - hours) * 60))
    if minutes == 0:
        return f"UTC{sign}{hours}"
    return f"UTC{sign}{hours}:{minutes:02d}"


class NewEventPage(QWidget):
    """Plan-only event creation page (reused legacy `NewEventPage`, data seam rewired).

    Signals:
      * ``event_created(event_id)`` — fired after a new event is materialised.
      * ``cancelled()`` — fired when the user clicks Cancel.
    """

    event_created = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, gateway: Gateway, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.gateway = gateway

        # Parsed days from the most recent plan-import (or from opening the Edit-plan
        # dialog). Legacy ``core.models.TripDay`` shape — the plan editor works in it; we
        # convert to the store ``TripDay`` at create time. Carried into Create so the new
        # event lands with its plan already populated.
        self._pending_trip_days: list[TripDay] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        self._heading = QLabel(tr("Create a new event"))
        self._heading.setObjectName("PageHeading")
        layout.addWidget(self._heading)

        self._hint = QLabel(tr(
            "Type a name and start date, or import a plan text file with day-by-day "
            "lines like 'Dia 1 - La Fortuna [LOC:La Fortuna] [TZ:-6]'. Plan import fills "
            "the form and populates the trip days."
        ))
        self._hint.setObjectName("PageHint")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(tr("e.g. 2026 - Pantanal"))
        self._name_edit.setToolTip(tr(
            "Name of the event. Also the folder name under your Photos base where the "
            "event lives."
        ))
        form.addRow(tr("Event name") + ":", self._name_edit)

        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        # Qt's default minimum (1752-09-14, Gregorian adoption) shows as initial text
        # before any interaction, which reads like a bug — clamp to a sane modern floor.
        self._date_edit.setMinimumDate(QDate(2000, 1, 1))
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setToolTip(tr(
            "Day the event starts (or started). Used as the anchor for trip-plan day "
            "numbering."
        ))
        form.addRow(tr("Start date") + ":", self._date_edit)

        layout.addWidget(form_host)

        # spec/44 — classification editor (replaces the legacy free-text Type
        # textbox). Reusable widget; reappears in Slice C (pre-ingest dialog)
        # and Slice D (Edit-info on EventPlanPage).
        self._classification = ClassificationPanel()
        layout.addWidget(self._classification)

        # Plan-import status line: hidden until an import succeeds.
        self._plan_status = QLabel("")
        self._plan_status.setObjectName("PageHint")
        self._plan_status.setWordWrap(True)
        self._plan_status.setVisible(False)
        layout.addWidget(self._plan_status)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        self._edit_plan_button = QPushButton(tr("Edit plan…"))
        self._edit_plan_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._edit_plan_button.setToolTip(tr(
            "Open the trip-plan editor — table of days with Date / TZ / Location / "
            "Description per row. Import from file, paste text, or build the plan day by "
            "day. Apply writes the result back to this event."
        ))
        self._edit_plan_button.clicked.connect(self._on_edit_plan)
        button_row.addWidget(self._edit_plan_button)

        self._import_folder_button = QPushButton(tr("Import plan from folder…"))
        self._import_folder_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._import_folder_button.setToolTip(tr(
            "Point at a folder that already has “Dia N - description” day subfolders (a "
            "past trip you organised by hand). The plan is read from the folder names + "
            "photo dates so you can refine it and create the event — nothing on disk is "
            "changed."
        ))
        self._import_folder_button.clicked.connect(self._on_import_plan_from_folder)
        button_row.addWidget(self._import_folder_button)

        button_row.addStretch(1)

        self._cancel_button = QPushButton(tr("Cancel"))
        self._cancel_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._cancel_button.clicked.connect(self._on_cancel)
        button_row.addWidget(self._cancel_button)

        self._create_button = QPushButton(tr("Create"))
        self._create_button.setObjectName("Primary")
        self._create_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._create_button.setDefault(True)
        self._create_button.clicked.connect(self._on_create)
        button_row.addWidget(self._create_button)

        layout.addLayout(button_row)

    # ── Public API ──────────────────────────────────────────────────

    def clear_for_create(self) -> None:
        """Reset the page to a clean create state. Called when navigating in from the
        sidebar "New Event" entry — guarantees a fresh form."""
        self._reset_form()

    # ── Slots ──────────────────────────────────────────────────────

    def _on_create(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(
                self,
                tr("Event name required"),
                tr("Please enter a name for the event before creating it."),
            )
            self._name_edit.setFocus()
            return

        # Name-collision guard: two events sharing a name resolve to the same on-disk
        # folder. Matches come from the gateway index (the data seam, charter §5.2).
        from mira.ui.base.name_collision import confirm_name_collision
        matches = [
            e for e in self.gateway.list_events()
            if (e.get("name") or "").strip().lower() == name.lower()
        ]
        if not confirm_name_collision(self, name, matches):
            self._name_edit.setFocus()
            return

        base = self.gateway.photos_base_path()
        if base is None:
            QMessageBox.warning(
                self,
                tr("Photos folder not configured"),
                tr("Set the Photos folder in Settings first — that's where Mira "
                   "writes new events."),
            )
            return

        start_qdate = self._date_edit.date()
        start = date(start_qdate.year(), start_qdate.month(), start_qdate.day())

        pending = list(self._pending_trip_days or [])
        stamp = _utc_now_iso()
        event_id = uuid.uuid4().hex
        # spec/44 — pull the typed classification snapshot. The panel always
        # returns a normalised event_type (closed enum), so the create_event
        # writes a valid row directly; the per-event extras / subtype /
        # description ride on the Event row in the same EventDocument so the
        # gateway materialise step persists everything in one transaction.
        # spec/52: event-level tags retired (Cuts replace event-tag membership).
        cls = self._classification.values()
        doc = m.EventDocument(
            event=m.Event(
                uuid=event_id, name=name, created_at=stamp, updated_at=stamp,
                start_date=start.isoformat(), end_date=None,
                event_type=cls.event_type,
                event_subtype=cls.event_subtype,
                description=cls.description,
                extras_json=json.dumps(cls.extras) if cls.extras else "{}",
            ),
            trip_days=[
                m.TripDay(
                    day_number=d.day_number,
                    date=d.date.isoformat() if d.date else None,
                    description=d.description or "",
                    location=d.location,
                    tz_minutes=(round(d.tz_offset * 60) if d.tz_offset is not None else None),
                )
                for d in pending
            ],
        )
        event_root = base / sanitize_folder_name(name)
        try:
            eg = self.gateway.create_event(doc, event_root)
            eg.close()
        except OSError as exc:
            log.warning("create_event failed for %s: %s", name, exc)
            QMessageBox.warning(
                self,
                tr("Could not create the event"),
                tr("An OS error occurred while creating the event:\n\n{err}").replace(
                    "{err}", str(exc)),
            )
            return

        log.info(
            "Created event %s (id=%s) on %s with %d day(s) at %s",
            name, event_id, start, len(pending), event_root,
        )
        self._maybe_show_tz_mismatch_alert(pending)

        # Tell the user WHERE the event lives + what to do next (docs/14 §"Event location
        # must be surfaced at creation"). The event root is under the Photos base, separate
        # + empty until photos are ingested; the imported plan folder is the plan source
        # only, not used or copied.
        QMessageBox.information(
            self,
            tr("Event created"),
            tr(
                "“{name}” was created at:\n\n{path}\n\nThis pipeline tree is empty for "
                "now. To bring photos in, use “Create Event from Photos”, or open the "
                "event and run a Cull import.\n\n(The folder you imported the plan from "
                "is the plan source only — it is not used or copied directly.)"
            ).replace("{name}", name).replace("{path}", str(event_root)),
        )

        self._reset_form()
        self.event_created.emit(event_id)

    def _maybe_show_tz_mismatch_alert(self, days: list[TripDay]) -> None:
        """Warn if the plan uses a TZ that differs from the home TZ in settings.

        Why: every wrong-TZ photo lands in the wrong day during cull (or costs a manual
        TZ-shift afterwards). "Prevenir é melhor que remediar" — Nelson 2026-05-14.
        Non-blocking; silent on empty plans or all-home-TZ plans."""
        if not days:
            return
        home_tz = self._home_timezone()
        if home_tz is None:
            return

        away_tzs: list[float] = []
        seen: set[float] = set()
        for day in days:
            if day.tz_offset is None:
                continue
            if abs(day.tz_offset - home_tz) < 0.01:
                continue
            if day.tz_offset in seen:
                continue
            seen.add(day.tz_offset)
            away_tzs.append(day.tz_offset)

        if not away_tzs:
            return

        away_list = ", ".join(_format_tz(tz) for tz in away_tzs)
        home_str = _format_tz(home_tz)
        QMessageBox.information(
            self,
            tr("Heads up — trip uses different time zones"),
            tr(
                "This trip's plan uses {away} (your home time zone is {home}).\n\n"
                "Before traveling, set the clock on each of your cameras to the trip's "
                "time zone. A camera left on the wrong TZ records photos with the wrong "
                "timestamps — they land in the wrong day during cull, and you'd have to "
                "correct each affected camera manually afterwards.\n\n"
                "Prevention is cheaper than correction."
            ).replace("{away}", away_list).replace("{home}", home_str),
        )

    def _on_cancel(self) -> None:
        self._reset_form()
        self.cancelled.emit()

    def _on_edit_plan(self) -> None:
        """Open the PlanEditorDialog seeded with the current pending days.

        On Apply, replace ``_pending_trip_days`` and refresh the status label + the form's
        start date. On Cancel, leave state untouched. This page is create-only, so the
        dialog is never seeded with an Event (no on-disk photos to gate Remove-day against).
        """
        seed = list(self._pending_trip_days or [])
        dlg = PlanEditorDialog(parent=self, trip_days=seed, event=None)
        dlg.exec()
        if not dlg.was_applied():
            return
        days = dlg.get_trip_days()
        self._pending_trip_days = days
        self._refresh_plan_status_label(days)
        self._sync_start_date_to_plan(days)

    def _on_import_plan_from_folder(self) -> None:
        """Pick an already-organised per-day root and derive the plan from it."""
        base = self.gateway.photos_base_path()
        start_dir = str(base) if base else ""
        # Non-native (consistent) Qt dialog — the native Windows folder browser changes
        # view between sessions, which Nelson found confusing (docs/14).
        chosen = QFileDialog.getExistingDirectory(
            self,
            tr("Pick the folder with the Dia-N day subfolders"),
            start_dir,
            QFileDialog.Option.DontUseNativeDialog | QFileDialog.Option.ShowDirsOnly,
        )
        if not chosen:
            return
        self.import_plan_from_folder(Path(chosen))

    def _refresh_plan_status_label(self, days: list[TripDay]) -> None:
        if days:
            self._plan_status.setText(
                tr("{n} day(s) in plan.").replace("{n}", str(len(days)))
            )
            self._plan_status.setVisible(True)
        else:
            self._plan_status.setText("")
            self._plan_status.setVisible(False)

    def _sync_start_date_to_plan(self, days: list[TripDay]) -> None:
        """If the plan has explicit dates, snap the form's start_date to the earliest."""
        if not days:
            return
        dated = [d for d in days if d.date is not None]
        if not dated:
            return
        earliest = min(d.date for d in dated)
        self._date_edit.setDate(QDate(earliest.year, earliest.month, earliest.day))

    # Test-friendly hook: derive a plan from an already-organised ``Dia N - LOC`` folder
    # tree (the revisit-a-past-trip case — docs/14 §"Bootstrapping…"). Brain-only: reads
    # folder names + samples EXIF dates, mutates nothing on disk. Reuses the legacy
    # skeleton generator + the shared text path; the user refines via *Edit plan…* then
    # clicks Create.
    def import_plan_from_folder(self, per_day_root: Path) -> None:
        per_day_root = Path(per_day_root)
        try:
            result = generate_plan_skeleton_from_per_day(
                per_day_root,
                # Folders are already day-correct by construction, so ANY photo's EXIF
                # date is valid — the default "iPhone" reference filter would miss
                # camera-only trips and emit (??/??) (docs/14 §"Folder-import date
                # correctness").
                reference_model_contains=None,
                home_tz_offset=self._home_timezone(),
            )
        except Exception as exc:  # noqa: BLE001 — never crash the page
            log.warning("skeleton generation failed for %s: %s", per_day_root, exc)
            QMessageBox.warning(
                self,
                tr("Couldn't read that folder"),
                tr("Could not derive a plan from “{f}”: {err}").replace(
                    "{f}", per_day_root.name).replace("{err}", str(exc)),
            )
            return
        if not result.plan_text.strip():
            QMessageBox.information(
                self,
                tr("No day folders found"),
                tr(
                    "No “Dia N - description” day folders were found under “{f}”.\n\n"
                    "Point this at the folder that directly contains the per-day folders "
                    "(e.g. “Dia 1 - Kathmandu”, “Dia 2 - Lukla”, …) and try again."
                ).replace("{f}", per_day_root.name),
            )
            return
        # The skeleton emits year-less (DD/MM) lines; result.day_dates holds the
        # authoritative full dates (with year). Pass the earliest as the parse anchor so a
        # *last-year* trip gets the right year (docs/14 §"Folder-import date correctness").
        anchor = min(result.day_dates.values()) if result.day_dates else None
        ok = self._import_plan_from_text(
            result.plan_text,
            name_seed=per_day_root.name,
            source_label=per_day_root.name,
            anchor_date=anchor,
            location_hints=result.folder_hints,
        )
        if result.warnings:
            log.info("plan skeleton warnings for %s: %s",
                     per_day_root, "; ".join(result.warnings))
        if ok:
            n = len(self._pending_trip_days or [])
            QMessageBox.information(
                self,
                tr("Plan imported"),
                tr(
                    "Imported {n} day(s) from “{f}”.\n\nDates and locations were filled "
                    "in from the folders. Click “Edit plan…” to review or adjust, then "
                    "“Create”."
                ).replace("{n}", str(n)).replace("{f}", per_day_root.name),
            )

    def _import_plan_from_text(
        self, text: str, *, name_seed: str, source_label: str,
        anchor_date: date | None = None,
        location_hints: dict[int, str] | None = None,
    ) -> bool:
        """Shared plan-text path: parse → ``_pending_trip_days`` → seed name/date/location
        → status label. Returns True on a successful import (False on parse error / no
        days), so the folder-import caller shows its confirmation only on success.

        ``anchor_date`` (folder import) is the authoritative earliest trip date sampled
        from the photos; when given it anchors the parse so a year-less skeleton lands on
        the correct year. ``location_hints`` maps ``day_number`` → the folder's location
        string; an empty parsed ``location`` is filled from it."""
        home_tz = self._home_timezone()
        # Two-attempt parse: first without a start_date so the parser uses the earliest
        # explicit ``(DD/MM[/YYYY])`` date as the anchor (explicit dates win over the
        # form). Fall back to the form date only when the plan has zero explicit dates. An
        # ``anchor_date`` wins outright (it came from the actual photos).
        start_qdate = self._date_edit.date()
        form_start = date(start_qdate.year(), start_qdate.month(), start_qdate.day())
        try:
            if anchor_date is not None:
                days = parse_trip_plan(text, start_date=anchor_date, home_timezone=home_tz)
            else:
                days = parse_trip_plan(text, home_timezone=home_tz)
        except ValueError:
            try:
                days = parse_trip_plan(
                    text, start_date=anchor_date or form_start, home_timezone=home_tz,
                )
            except ValueError as exc:
                QMessageBox.warning(
                    self,
                    tr("Plan import failed"),
                    tr("Plan parse error: {err}").replace("{err}", str(exc)),
                )
                return False

        if not days:
            QMessageBox.warning(
                self,
                tr("Plan import found no days"),
                tr(
                    "Plan parsed but produced no day lines. Each day needs a prefix like "
                    "'Dia 1 -' or 'Day 1 -' or '1.'."
                ),
            )
            return False

        # Folder import: the Dia-N folder name IS the day's location. Fill an empty parsed
        # location from the folder hint so the user doesn't copy Description→Location.
        if location_hints:
            for d in days:
                if not (d.location or "").strip():
                    hint = (location_hints.get(d.day_number) or "").strip()
                    if hint:
                        d.location = hint

        self._pending_trip_days = days

        # If the name field is empty, seed it from the source label.
        if not self._name_edit.text().strip():
            self._name_edit.setText(name_seed)

        # Jump the form's start-date to the earliest real date: the photo-sampled anchor
        # when we have one (authoritative), else the earliest parsed date.
        dated = [d.date for d in days if d.date is not None]
        earliest = (
            anchor_date if anchor_date is not None
            else (min(dated) if dated else None)
        )
        if earliest is not None:
            self._date_edit.setDate(QDate(earliest.year, earliest.month, earliest.day))

        self._plan_status.setText(
            tr("Plan imported: {n} day(s) from {f}").replace(
                "{n}", str(len(days))).replace("{f}", source_label)
        )
        self._plan_status.setVisible(True)
        log.info("Imported plan %s → %d trip day(s)", source_label, len(days))
        return True

    # ── Helpers ─────────────────────────────────────────────────────

    def _home_timezone(self) -> float | None:
        try:
            value = self.gateway.settings.load().home_timezone
        except Exception:  # noqa: BLE001
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _reset_form(self) -> None:
        self._name_edit.clear()
        self._classification.set_values()    # back to "unclassified" defaults
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setReadOnly(False)
        self._pending_trip_days = None
        self._plan_status.setText("")
        self._plan_status.setVisible(False)

    # ── Convenience for tests ──────────────────────────────────────

    def set_form_values(
        self,
        name: str,
        start_date: date,
        *,
        event_type: str = "unclassified",
        event_subtype: Optional[str] = None,
        description: str = "",
        tags: Optional[list] = None,
        extras: Optional[dict] = None,
    ) -> None:
        """Programmatic field setter for tests. Routes classification fields
        through :meth:`ClassificationPanel.set_values` so tests use the same
        seam the real user does (radio + dropdown + …)."""
        self._name_edit.setText(name)
        self._date_edit.setDate(QDate(start_date.year, start_date.month, start_date.day))
        self._classification.set_values(
            event_type=event_type,
            event_subtype=event_subtype,
            description=description,
            tags=tags,
            extras=extras,
        )
