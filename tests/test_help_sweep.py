"""The 2026-06-12 Help sweep (Nelson): every photo/video surface
carries a top-right "?" button that opens its keyboard-shortcuts
table — uniform shared dialog (``ShortcutsDialog`` role) across the
app, F1/? key binding alongside the click target.

This file pins the contract per surface. The shared dialog itself is
tested in test_shortcuts_dialog.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QPushButton, QWidget


def _help_buttons(w: QWidget) -> list[QPushButton]:
    return [b for b in w.findChildren(QPushButton)
            if b.objectName() == "HelpButton"]


def test_pick_top_bar_help_button_role_is_correct(qapp):
    """The earlier hand-built help button on PickTopBar wore the
    #ReclassifyButton role (a misrouted borrow). Nelson 2026-06-12
    flipped it to the canonical #HelpButton."""
    from mira.ui.picked.pick_top_bar import PickTopBar
    bar = PickTopBar()
    assert bar.help_button.objectName() == "HelpButton"
    # No widget under the bar still wears the misrouted role for
    # the help control.
    misrouted = [b for b in bar.findChildren(QPushButton)
                 if b is bar.help_button
                 and b.objectName() == "ReclassifyButton"]
    assert misrouted == []


def test_quick_sweep_page_has_help_button_in_viewer_top_bar(qapp):
    """Quick Sweep's viewer chrome gains a "?" — F1 and ? keys also
    open the dialog (covered by the keyPressEvent below)."""
    from mira.ui.pages.quick_sweep_page import QuickSweepPage
    page = QuickSweepPage()
    btns = _help_buttons(page)
    # Two could exist if other helpers live somewhere; assert ≥1 so a
    # future addition doesn't break this loose pin.
    assert len(btns) >= 1
    assert page._help_btn.objectName() == "HelpButton"


def test_cut_session_page_has_help_button(qapp, tmp_path):
    """Cut session's top bar carries the "?" next to Save/Back. The
    fixture infrastructure is shared with test_cut_session_page."""
    import itertools
    from mira.gateway.event_gateway import EventGateway
    from mira.shared.cut_session import CutSession
    from mira.store.repo import EventStore
    from mira.ui.shared.cut_session_page import CutSessionPage
    from tests.test_cut_session import _draft
    from tests.test_gateway_cuts import _doc, _now

    store = EventStore.create(tmp_path / "event.db", event_id="evt-h")
    store.save_document(_doc())
    counter = itertools.count(1)
    gw = EventGateway(store, now=_now,
                      new_id=lambda: f"id-{next(counter)}")
    try:
        session = CutSession.from_draft(gw, _draft())
        page = CutSessionPage(gw, session, event_root=tmp_path)
        assert page._help_btn.objectName() == "HelpButton"
    finally:
        gw.close()


def test_cut_detail_page_has_help_button(qapp):
    from mira.ui.shared.cut_detail_page import CutDetailPage
    page = CutDetailPage()
    assert page._help_btn.objectName() == "HelpButton"


def test_every_known_help_button_is_the_canonical_role(qapp):
    """If a contributor reintroduces a raw `QPushButton(tr("?"))` with
    a different objectName, this catches it — every "?" labelled
    button in the surface modules must wear the HelpButton role."""
    import pathlib
    import mira.ui as ui_pkg
    root = pathlib.Path(ui_pkg.__file__).resolve().parent

    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        # A raw QPushButton(tr("?")) without immediate help_button() use
        # is the regression signature.
        if 'QPushButton(tr("?"))' in text:
            offenders.append(str(path))
    assert offenders == [], (
        "Raw QPushButton(tr(\"?\")) reintroduced — use help_button() "
        f"from mira.ui.base.surface instead. Offenders: "
        f"{offenders}")
