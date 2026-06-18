"""``run_with_progress`` — the one way to run a long operation (spec/05 §4b).

Every operation that can lag the UI (folder/EXIF scan, ingest copy, export, big card
scan, backup mirror …) runs through this helper so the standard is applied **once**, not
re-implemented per surface (Nelson 2026-05-30: *"I do not want to have to repeat this in
every new surface"*).

It runs the work **on the GUI thread** behind a modal ``QProgressDialog`` with a busy
cursor, and repaints the UI each time the work reports progress (the classic Qt progress
loop — `processEvents` between steps). This is deliberately *not* threaded: a background
thread that touches the dialog deadlocks on Windows, so the work stays on the main thread
and simply yields to the event loop as it reports. The window can't be dragged mid-step,
but the bar advances and the app never hangs.

The work callable takes one argument — ``progress(done, total, message="")`` — and
returns its result. Each ``progress`` call updates the bar (indeterminate until a
positive ``total`` arrives) and pumps the event loop. Returns ``(ok, result_or_error)``;
a raised exception comes back as ``(False, "<traceback>")`` — never re-raised at the call
site. Callers must disable the triggering control first (we pump events, so a second
click could otherwise re-enter).
"""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QProgressDialog, QWidget

from mira.ui.i18n import tr

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, int, str], None]
Work = Callable[[ProgressCb], Any]


def run_with_progress(
    parent: Optional[QWidget],
    title: str,
    work: Work,
    *,
    label: str = "",
) -> Tuple[bool, Any]:
    """Run ``work`` on the GUI thread behind a modal progress dialog. Returns
    ``(True, result)`` on success or ``(False, error_text)`` on failure (never raises)."""
    dlg = QProgressDialog(label or tr("Working…"), "", 0, 0, parent)  # 0,0 → indeterminate
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setCancelButton(None)  # our jobs aren't safely interruptible mid-way
    # ``setLabelText`` after ctor + ``setRange`` are belt-and-braces — when
    # constructed with empty cancel-button text + ``setCancelButton(None)``
    # the dialog's layout recomputes lazily, and on Windows 11 / Qt 6 the
    # first show occasionally races the layout pass and renders blank
    # body content (Nelson 2026-06-18). Re-applying the label/range tickles
    # the layout into recomputing before the show.
    dlg.setLabelText(label or tr("Working…"))
    dlg.setRange(0, 0)
    dlg.show()
    dlg.raise_()

    def report(done: int, total: int = 0, message: str = "") -> None:
        if total > 0:
            dlg.setRange(0, total)
            dlg.setValue(int(done))
        if message:
            dlg.setLabelText(message)
        QApplication.processEvents()

    QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
    # Pump the event loop several times AND force a synchronous repaint so
    # the dialog renders its label + busy indicator BEFORE the (possibly
    # blocking) work starts — otherwise on Windows the user sees an empty
    # frame for the entire scan. processEvents alone isn't enough: it
    # processes ONE round of events; the show-event + layout + paint each
    # post fresh events. Iterating + an explicit repaint covers it.
    for _ in range(3):
        QApplication.processEvents()
    dlg.repaint()
    QApplication.processEvents()
    try:
        return True, work(report)
    except Exception:  # noqa: BLE001 — surface, never crash/raise into the caller
        log.exception("long operation failed")
        return False, traceback.format_exc(limit=4)
    finally:
        QApplication.restoreOverrideCursor()
        dlg.close()
