"""Capture orchestration — the ported legacy ``MainWindow._on_capture_phase`` chain (spec/13).

PORTED from legacy ``ui/main_window.py`` (charter §0/§5.2). The user clicks the Capture phase
tile; this drives, in the exact legacy order:

    pick source → scan EXIF (busy) → **PreingestPlanConfirmDialog**
    ("Confirm trip plan and timezone for N day(s)") → **CaptureActionDialog**
    (copy-all vs cull-first) → [**Quick Sweep** for Mode B] → **BackUpCardDialog**
    (offload + verify + record-to-DB + wipe gate).

The only changes from legacy are the data seam (charter §0): the dialogs are fed a
legacy-shaped ``Event`` adapter built from the gateway, persistence goes through the gateway,
and the offset/source threading is unchanged. Housed in a function (not on ``MainWindow``) so
the shell wiring is a one-liner; the flow itself is the legacy flow.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date as _date
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QEventLoop
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
    QVBoxLayout,
)

from core.models import Event as LegacyEvent, TripDay as LegacyTripDay
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


def run_capture(parent, gateway, event_id: str) -> bool:
    """Drive the Capture flow for ``event_id``. Returns True if photos were added (so the
    caller can refresh), False on any cancel / failure."""
    eg = gateway.open_event(event_id)
    try:
        ev = eg.event()
        store_days = eg.trip_days()
        event_root = eg.event_root
    finally:
        eg.close()
    if event_root is None:
        QMessageBox.warning(
            parent, tr("Event location unavailable"),
            tr("This event's folder couldn't be resolved (relocated?)."),
        )
        return False

    # Legacy-shaped Event adapter — the reused dialogs render + mutate this; persistence goes
    # through the gateway (charter §0). trip_days carry the legacy float ``tz_offset`` hours.
    adapter = LegacyEvent(
        id=event_id,
        name=ev.name,
        start_date=_date.fromisoformat(ev.start_date) if ev.start_date else None,
        trip_days=[
            LegacyTripDay(
                day_number=d.day_number,
                date=_date.fromisoformat(d.date) if d.date else None,
                description=d.description or "",
                tz_offset=(d.tz_minutes / 60.0) if d.tz_minutes is not None else None,
                location=d.location,
            )
            for d in store_days
        ],
    )

    # 1. Source pick.
    source_path = _pick_capture_source(parent, gateway)
    if source_path is None:
        return False

    # 2. EXIF scan (busy).
    items = _scan_capture_source(parent, source_path)
    if items is None:
        return False
    camera_make, camera_model = _dominant_camera_identity(items)

    # 3. F-019 — confirm trip plan + timezone.
    from mira.ui.pages.preingest_dialog import PreingestPlanConfirmDialog
    preingest = PreingestPlanConfirmDialog(
        adapter, items, camera_make=camera_make, camera_model=camera_model,
        gateway=gateway, event_id=event_id, parent=parent,
    )
    try:
        if preingest.exec() != QDialog.DialogCode.Accepted:
            log.info("Capture: cancelled at the pre-ingest dialog")
            return False
        adapter = preingest.updated_event()
        precomputed_offset = preingest.offset_hours()
        # spec/44 Slice C — drop files whose day was unticked. The user picks
        # which day(s) to actually copy in the pre-ingest dialog; we filter
        # `items` here so BackUpCardDialog's verbatim byte-copy step never
        # writes excluded days to Original Media.
        included_paths = preingest.included_source_paths()
        items = [it for it in items if Path(it.path) in included_paths]
        if not items:
            log.info("Capture: no items left after day-include filter")
            return False
    finally:
        preingest.deleteLater()

    # 4. Mode chooser.
    from mira.ui.pages.capture_action_dialog import CaptureActionDialog, CaptureMode
    action_dlg = CaptureActionDialog(parent)
    action_dlg.exec()
    choice = action_dlg.choice()
    action_dlg.deleteLater()
    if choice is CaptureMode.CANCEL:
        log.info("Capture: cancelled at the action dialog")
        return False

    # 5. Mode B — Quick Sweep over the already-scanned items → kept basenames.
    included_names: Optional[frozenset] = None
    if choice is CaptureMode.CULL_THEN_COPY:
        included_names = _run_capture_cull(parent, items)
        if included_names is None:
            log.info("Capture: cancelled during the cull step")
            return False
        log.info("Capture Mode B: user kept %d file(s)", len(included_names))

    # 6. BackUpCardDialog — offload + verify + record-to-DB + wipe.
    from mira.ui.pages.back_up_card_dialog import BackUpCardDialog
    dlg = BackUpCardDialog(
        adapter, parent,
        included_names=included_names,
        prepicked_source=source_path,
        precomputed_offset=precomputed_offset,
        gateway=gateway,
        event_root=event_root,
    )
    landed = {"ok": False}
    dlg.backup_done.connect(lambda _eid: landed.__setitem__("ok", True))
    dlg.exec()
    dlg.deleteLater()
    return landed["ok"]


# ── Helpers (ported verbatim from MainWindow, parameterised on parent/gateway) ──────────


def _pick_capture_source(parent, gateway) -> Optional[Path]:
    base = gateway.photos_base_path()
    start_dir = str(base) if base else str(Path.home())
    chosen = QFileDialog.getExistingDirectory(
        parent, tr("Pick the SD card / source folder"), str(start_dir),
    )
    if not chosen:
        return None
    return Path(chosen)


def _scan_capture_source(parent, source: Path) -> Optional[list]:
    """Read EXIF off the source behind a busy overlay; None on failure / empty."""
    from core.fresh_source import read_source_items

    parent.statusBar().showMessage(tr("Reading photo metadata…"))
    QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
    busy = QProgressDialog("", "", 0, 0, parent)
    busy.setLabelText(tr(
        "Reading photo metadata.\nThis usually takes a few seconds — please wait."
    ))
    busy.setWindowTitle(tr("Loading picker…"))
    busy.setMinimumWidth(420)
    busy.setModal(True)
    busy.setCancelButton(None)
    busy.setMinimumDuration(0)
    busy.show()
    QApplication.processEvents()
    try:
        try:
            items = read_source_items(source)
        except Exception as exc:  # noqa: BLE001
            log.exception("Capture: source scan failed")
            QMessageBox.critical(
                parent, tr("Could not read source"),
                tr("The source folder could not be scanned: {err}").replace(
                    "{err}", str(exc)),
            )
            return None
    finally:
        QApplication.restoreOverrideCursor()
        parent.statusBar().clearMessage()
        busy.close()
        busy.deleteLater()
    if not items:
        QMessageBox.information(
            parent, tr("No photos found"),
            tr("No readable photo or video files were found under the chosen source."),
        )
        return None
    return items


def _dominant_camera_identity(items) -> tuple[str, str]:
    """Most-frequent camera_id's (make, model). One exiftool re-read to recover Make."""
    ct: Counter = Counter(it.camera_id for it in items if it.camera_id)
    if not ct:
        return ("", "")
    top_id, _count = ct.most_common(1)[0]
    rep = next((it for it in items if it.camera_id == top_id), None)
    if rep is None:
        return ("", top_id)
    try:
        from core.exif_reader import read_exif_single
        exif = read_exif_single(rep.path)
        raw = getattr(exif, "raw", None) or {}
        return (str(raw.get("Make", "")).strip(), top_id)
    except Exception:  # noqa: BLE001
        log.exception("Capture: failed to recover Make for %s", rep.path)
        return ("", top_id)


def _run_capture_cull(parent, items) -> Optional[frozenset]:
    """Show the Quick Sweep over the scanned items (default-Keep triage); return the kept
    file basenames (the OffloadConfig.included_names contract), or None if cancelled."""
    from mira.ui.picked.quick_sweep_page import QuickSweepPage

    # Pre-load BEFORE wrapping in a QDialog so the host doesn't flash
    # empty under the load-progress modal. ``page.load(items)`` shows a
    # WindowModal progress dialog parented to ``page``; if we'd already
    # added the page to a not-yet-shown host, Qt would surface the host
    # to satisfy the modal, and the user sees a blank "Cull before
    # copying" window for the duration of the scan.
    page = QuickSweepPage()
    if not page.load(items):
        page.deleteLater()
        return None

    host = QDialog(parent)
    host.setWindowTitle(tr("Pick before copying"))
    host.setModal(True)
    host.resize(1100, 740)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(page)

    result: dict = {"picked": None}
    page.saved.connect(lambda kept: (result.__setitem__("picked", set(kept)), host.accept()))
    page.cancelled.connect(lambda: (result.__setitem__("picked", None), host.reject()))
    page.setFocus()
    host.exec()

    kept = result["picked"]
    if kept is None:
        return None
    # The offload engine filters by file basename, not full path (legacy contract).
    return frozenset(p.name for p in kept)
