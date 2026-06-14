"""ManageDaysDialog — per-day operations for one event (spec/14 §5D).

A dedicated dialog (opened from the EventPlanPage "Manage days…" button) that lists every
trip day of an event — stable ``day_number``, NOT the plan-editor's renumber-by-date rows —
with its captured photo/video counts, and offers the three day operations:

* **Hide / Unhide** — soft-hide a whole day; its items are disregarded everywhere (phase work
  + completion metrics) but recoverable while the event is open. Data layer: spec/14 §5C.1.
* **Browse…** — open the day's photos/videos in the read-only Quick Sweep (the same browse the
  plan editor's per-row button uses).
* **Delete day…** / **Move to event…** — land in build-order steps 2 + 3 (spec/14 §5D).

The legacy app had none of these; this is genuinely new UI, proposed first in the spec/14 §5D
manifest (charter §0 amendment). It reuses the app's idioms — resizable table (spec/05 §4b),
the read-only Quick Sweep browse, `tr()`, pointing-hand cursor + hints — and never reinvents
them. All data goes through the gateway (the dialog opens/closes a per-event facade per action).
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime as _dt
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mira.ui.i18n import tr

log = logging.getLogger(__name__)

# Column indices.
COL_DAY = 0
COL_DATE = 1
COL_DESC = 2
COL_COUNT = 3
COL_STATUS = 4
COL_ACTIONS = 5
COL_TOTAL = 6


class ManageDaysDialog(QDialog):
    """Per-day Hide / Delete / Move for one event.

    ``changed`` fires after any operation that altered the event's data, so the host
    (MainWindow) can refresh the surfaces that show day-count / progress.
    """

    changed = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        gateway,
        event_id: str,
        day_photos_provider: Optional[Callable[[_date], list]] = None,
    ) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self._event_id = event_id
        self._day_photos_provider = day_photos_provider
        self._summaries: list[dict] = []

        self.setWindowTitle(tr("Manage days"))
        self.setModal(True)
        self.resize(1040, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        intro = QLabel(tr(
            "Hide a day to set it aside (its photos are disregarded everywhere until you "
            "unhide it), delete a day to remove this event's copies of its photos, or move "
            "days to another event to split one trip into several."
        ))
        intro.setWordWrap(True)
        intro.setObjectName("PageHint")
        outer.addWidget(intro)

        self._table = QTableWidget(0, COL_TOTAL)
        self._configure_table()
        outer.addWidget(self._table, stretch=1)

        self._empty_hint = QLabel(tr("This event has no trip days yet."))
        self._empty_hint.setObjectName("PageHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setVisible(False)
        outer.addWidget(self._empty_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_btn.setText(tr("Done"))
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        buttons.rejected.connect(self.accept)
        outer.addWidget(buttons)

        self._reload()

    # ── Construction ───────────────────────────────────────────────

    def _configure_table(self) -> None:
        headers = (
            (tr("Day"), tr("The day's stable number within this event.")),
            (tr("Date"), tr("Calendar date of the day.")),
            (tr("Description"), tr("What the day covers.")),
            (tr("Photos / Videos"), tr("Captured items assigned to this day.")),
            (tr("Status"), tr("Whether the day is currently hidden.")),
            (tr("Actions"), tr("Browse, hide/unhide, delete or move this day.")),
        )
        for col, (title, tip) in enumerate(headers):
            item = QTableWidgetItem(title)
            item.setToolTip(tip)
            self._table.setHorizontalHeaderItem(col, item)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # Fixed, comfortable row height measured from a real (polished) styled button so the
        # action buttons never clip at the row bottom and the value scales with DPI. This is
        # a fixed-content management table (day number / date / count / status pill / action
        # buttons), so we deliberately DON'T use the all-draggable standard — auto-fit columns
        # behave predictably (the Interactive + one-Stretch-not-last mix fought when dragged,
        # Nelson eyeball 2026-06-01). Description stretches to fill; the rest fit content.
        probe = QPushButton("Hg")
        probe.ensurePolished()
        row_h = probe.sizeHint().height() + 14
        probe.deleteLater()
        vheader = self._table.verticalHeader()
        vheader.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vheader.setDefaultSectionSize(row_h)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in (COL_DAY, COL_DATE, COL_COUNT, COL_STATUS, COL_ACTIONS):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DESC, QHeaderView.ResizeMode.Stretch)

    # ── Data ───────────────────────────────────────────────────────

    def _reload(self) -> None:
        """Re-read the day summaries through the gateway and rebuild the rows."""
        eg = self._gateway.open_event(self._event_id)
        try:
            self._summaries = eg.day_summaries()
        finally:
            eg.close()
        self._table.setRowCount(0)
        for summary in self._summaries:
            self._append_row(summary)
        has_days = bool(self._summaries)
        self._table.setVisible(has_days)
        self._empty_hint.setVisible(not has_days)

    def _append_row(self, s: dict) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        hidden = bool(s["hidden"])

        def _cell(text: str) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if hidden:
                it.setForeground(Qt.GlobalColor.gray)
            return it

        self._table.setItem(row, COL_DAY, _cell(str(s["day_number"])))
        self._table.setItem(row, COL_DATE, _cell(s.get("date") or tr("—")))
        self._table.setItem(row, COL_DESC, _cell(s.get("description") or ""))
        self._table.setItem(
            row, COL_COUNT, _cell(f"{s['photos']} / {s['videos']}"))
        self._table.setItem(
            row, COL_STATUS, _cell(tr("Hidden") if hidden else tr("Visible")))
        self._table.setCellWidget(row, COL_ACTIONS, self._make_actions(s))

    def _make_actions(self, s: dict) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        # The fixed row height supplies the vertical breathing room; these margins handle the
        # horizontal inset + a little top/bottom slack so nothing touches the row edges.
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)
        day_number = s["day_number"]
        hidden = bool(s["hidden"])
        has_media = (s["photos"] + s["videos"]) > 0

        def _fit(btn: QPushButton) -> QPushButton:
            # The Actions column auto-fits, but a styled QPushButton's sizeHint omits the QSS
            # padding/border, so the column comes up a touch narrow and the widest button's
            # (centre-aligned) text clips at BOTH ends — the "M…" of "Move to event…" looked
            # like a stray "." (Nelson eyeball). Give each button a min width = its measured
            # text + slack so the column reserves enough room.
            fm = btn.fontMetrics()
            btn.setMinimumWidth(fm.horizontalAdvance(btn.text()) + 34)
            return btn

        browse = QPushButton(tr("Browse…"))
        browse.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        browse.setToolTip(tr("Browse this day's photos and videos (read-only)."))
        browse.setEnabled(has_media and self._day_photos_provider is not None)
        browse.clicked.connect(lambda _=False, n=day_number: self._browse_day(n))
        lay.addWidget(_fit(browse))

        toggle = QPushButton(tr("Unhide") if hidden else tr("Hide"))
        toggle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        # Fix the toggle to the width of its WIDER label so "Hide" and "Unhide" occupy the
        # same space — otherwise the Delete/Move buttons after it shift between rows and the
        # columns look ragged (Nelson eyeball 2026-06-01). Measured (font/DPI-safe) + slack
        # for the QSS button padding/border.
        fm = toggle.fontMetrics()
        toggle.setFixedWidth(
            max(fm.horizontalAdvance(tr("Hide")), fm.horizontalAdvance(tr("Unhide"))) + 34)
        toggle.setToolTip(
            tr("Bring this day back into view everywhere.") if hidden
            else tr("Set this day aside — disregarded everywhere until you unhide it.")
        )
        toggle.clicked.connect(
            lambda _=False, n=day_number, h=hidden: self._set_hidden(n, not h))
        lay.addWidget(toggle)

        delete = QPushButton(tr("Delete day…"))
        # Red TEXT, normal frame — NOT the full red-outline #DangerButton, whose hairline
        # border renders as stray vertical red lines next to the neighbouring buttons at
        # fractional DPI (Nelson eyeball 2026-06-01). See the QSS role comment.
        delete.setObjectName("DangerButtonText")
        delete.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        delete.setToolTip(tr(
            "Permanently remove this event's copies of this day's photos. "
            "Your camera card / original source is not touched."
        ))
        delete.clicked.connect(lambda _=False, n=day_number: self._delete_day(n))
        lay.addWidget(_fit(delete))

        move = QPushButton(tr("Move to event…"))
        move.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        move.setToolTip(tr(
            "Move this whole day — its photos and cull decisions — into another event "
            "(or a brand-new one), to split one trip into several events."
        ))
        move.setEnabled(has_media)
        move.clicked.connect(lambda _=False, n=day_number: self._move_day(n))
        lay.addWidget(_fit(move))
        return cell

    # ── Operations ─────────────────────────────────────────────────

    def _set_hidden(self, day_number: int, hide: bool) -> None:
        eg = self._gateway.open_event(self._event_id)
        try:
            eg.set_day_hidden(day_number, hide)
        finally:
            eg.close()
        log.info("ManageDays: day %s %s for event %s",
                 day_number, "hidden" if hide else "unhidden", self._event_id)
        self.changed.emit()
        self._reload()

    def _delete_day(self, day_number: int) -> None:
        """Hard-delete a day after a strong Yes/No confirm showing the file count (spec/14
        §5D Q1). Blocked days (downstream Process/Curate work) surface the gateway's reason."""
        from PyQt6.QtWidgets import QMessageBox

        summary = next(
            (s for s in self._summaries if s["day_number"] == day_number), None)
        if summary is None:
            return
        n_files = summary["photos"] + summary["videos"]
        label = tr("Day {n}").replace("{n}", str(day_number))
        if summary.get("date"):
            label += f" · {summary['date']}"
        reply = QMessageBox.question(
            self,
            tr("Delete this day?"),
            tr(
                "Permanently delete {label} and this event's copies of its {count} "
                "photo/video file(s)?\n\nYour camera card / original source is NOT touched — "
                "only this event's copies. This cannot be undone."
            ).replace("{label}", label).replace("{count}", str(n_files)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        eg = self._gateway.open_event(self._event_id)
        try:
            eg.delete_day(day_number)
        except ValueError as exc:
            QMessageBox.warning(
                self, tr("Can't delete this day"),
                tr(
                    "This day's photos have downstream Process/Curate work, so the day "
                    "can't be deleted yet. Remove that output first.\n\n{err}"
                ).replace("{err}", str(exc)),
            )
            return
        finally:
            eg.close()
        log.info("ManageDays: deleted day %s for event %s", day_number, self._event_id)
        self.changed.emit()
        self._reload()

    def _move_day(self, day_number: int) -> None:
        """Move a day into another event — existing or a new blank one (spec/14 §5D Q2).
        Copy-verify-then-remove happens in ``Gateway.move_days``; blocked days surface their
        reason. The target picker offers every other event plus an inline '＋ New event'."""
        from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

        others = [e for e in self._gateway.list_events() if e.get("id") != self._event_id]
        new_label = tr("＋ New event…")
        choices = [e.get("name") or tr("(unnamed event)") for e in others] + [new_label]
        choice, ok = QInputDialog.getItem(
            self, tr("Move day to…"),
            tr("Move Day {n} into:").replace("{n}", str(day_number)),
            choices, 0, False,
        )
        if not ok:
            return
        if choice == new_label:
            target_id = self._create_blank_event()
            if target_id is None:
                return
        else:
            target_id = others[choices.index(choice)]["id"]

        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            self._gateway.move_days(self._event_id, [day_number], target_id)
        except Exception as exc:  # noqa: BLE001 — surface any move failure to the user
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(
                self, tr("Couldn't move the day"),
                tr(
                    "This day couldn't be moved. Days with video clips/snapshots or "
                    "downstream Process/Curate work aren't movable yet.\n\n{err}"
                ).replace("{err}", str(exc)),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()
        log.info("ManageDays: moved day %s from %s to %s",
                 day_number, self._event_id, target_id)
        self.changed.emit()
        self._reload()

    def _create_blank_event(self) -> Optional[str]:
        """Create a brand-new plan-only event (no days, no items) and return its id, or
        None on cancel/failure — the inline '＋ New event' move target."""
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        from PyQt6.QtWidgets import QInputDialog, QMessageBox

        from core.path_builder import sanitize_folder_name
        from mira.store import models as m

        name, ok = QInputDialog.getText(
            self, tr("New event"), tr("Name for the new event:"))
        if not ok or not name.strip():
            return None
        name = name.strip()
        base = self._gateway.photos_base_path()
        if base is None:
            QMessageBox.warning(
                self, tr("No photo library set"),
                tr("Set the photo library location in Settings before creating an event."))
            return None
        stamp = _dt.now(_tz.utc).isoformat()
        event_id = _uuid.uuid4().hex
        doc = m.EventDocument(
            event=m.Event(uuid=event_id, name=name, created_at=stamp, updated_at=stamp))
        event_root = base / sanitize_folder_name(name)
        try:
            self._gateway.create_event(doc, event_root).close()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, tr("Couldn't create the event"),
                tr("The new event couldn't be created.\n\n{err}").replace("{err}", str(exc)))
            return None
        return event_id

    def _browse_day(self, day_number: int) -> None:
        if self._day_photos_provider is None:
            return
        summary = next(
            (s for s in self._summaries if s["day_number"] == day_number), None)
        if summary is None or not summary.get("date"):
            return
        try:
            row_date = _date.fromisoformat(summary["date"])
        except ValueError:
            return
        # Guard the whole browse: an exception in the provider or the Quick Sweep must NOT
        # abort the app (PyQt's default for an unhandled slot exception) — log it and show
        # the reason, like the Move/Delete actions do.
        try:
            items = self._day_photos_provider(row_date)
            if not items:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self, tr("Nothing to browse"),
                    tr("No photos or videos were found on disk for this day."))
                return
            self._open_day_browser(items)
        except Exception as exc:  # noqa: BLE001 — never crash the app on a browse error
            log.exception("ManageDays: browse failed for day %s", day_number)
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, tr("Couldn't open the browser"),
                tr("This day's photos couldn't be opened for browsing.\n\n{err}")
                .replace("{err}", f"{type(exc).__name__}: {exc}"),
            )

    def _open_day_browser(self, items) -> None:
        """Host the read-only Quick Sweep (browse mode) over the day's items."""
        from mira.ui.picked.quick_sweep_page import QuickSweepPage

        host = QDialog(self)
        host.setWindowTitle(tr("Browse day"))
        host.setModal(True)
        host.resize(1100, 740)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        page = QuickSweepPage(browse_mode=True)
        page.cancelled.connect(host.accept)
        lay.addWidget(page)
        if page.load(items):
            page.setFocus()
            host.exec()
