"""spec/142 — picked items survive Back from a day grid.

The reported user bug: while building a Cut in
:class:`CutSessionPage`, they pick items in Day 1, hit title-bar Back
to return to the day panel and pick another day — and the in-progress
draft is lost because the title-bar Back was closing the session.

After the spec/142 fix, title-bar Back from a day grid steps to the
days panel (not leaves the session), so the ``CutSession``'s
in-memory ledger is untouched and the user can open another day with
the first day's picks still in place. Pin that.
"""
from __future__ import annotations

import itertools
from unittest.mock import patch

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import CutSession
from mira.store.repo import EventStore
from mira.ui.shared.cut_session_page import CutSessionPage

from tests.test_cut_session import _draft
from tests.test_gateway_cuts import _doc, _now


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


def test_picks_survive_titlebar_back_from_grid(qapp, gw, tmp_path):
    """Pick something on Day 1, title-bar Back to the days panel,
    open Day 2 — the Day 1 pick MUST still be in the draft. This is
    the headline bug: the session_page level-step is what keeps the
    ledger alive."""
    from mira.ui.pages.share_cuts_page import ShareCutsPage

    session = CutSession.from_draft(gw, _draft())
    page = CutSessionPage(gw, session, event_root=tmp_path)
    try:
        # Stand-in for ShareCutsPage's stack — its on_titlebar_back
        # reads ``self._stack.currentWidget()``.
        class _StackStub:
            def __init__(_self, current):
                _self._current = current
            def currentWidget(_self):
                return _self._current
        fake_share = type(
            "_FakeShare", (), {"_stack": _StackStub(page)})()

        # Open Day 1 + pick the first file there.
        page._open_day(0)
        files_d1 = page._files_of_open_group()
        assert files_d1, "fixture: day 1 must have at least one file"
        d1_relpath = files_d1[0].export_relpath
        page._toggle_cell(0)
        assert session.is_picked(d1_relpath), (
            "precondition: the toggle picked the file"
        )
        baseline_picked = session.picked_count()
        assert baseline_picked >= 1

        # Title-bar Back from the grid — the spec/142 fix steps back
        # to the days panel WITHOUT firing back_requested / cancel.
        cancel_calls: list = []
        with patch.object(page, "_on_cancel",
                          side_effect=lambda: cancel_calls.append(True)):
            ShareCutsPage.on_titlebar_back(fake_share)
        assert cancel_calls == [], (
            "spec/142: title-bar Back from the grid must NOT cancel "
            "the session (that's the bug — picks would be lost)"
        )
        assert page._stack.currentIndex() == 0, (
            "spec/142: title-bar Back from the grid lands at the "
            "days panel so the user can pick another day"
        )
        # The ledger survived.
        assert session.is_picked(d1_relpath), (
            "spec/142: the Day 1 pick must survive the level step — "
            "the in-progress draft lives in CutSession the whole time"
        )
        assert session.picked_count() == baseline_picked

        # Open Day 2 — same draft, the Day 1 pick is still there.
        # (Fixture has 2 day groups; group index 1 is Day 2.)
        if len(session.days()) > 1:
            page._open_day(1)
            assert page._stack.currentIndex() == 1
            assert session.is_picked(d1_relpath), (
                "spec/142: opening another day must NOT clear earlier "
                "picks — the ledger is per-session, not per-day"
            )
    finally:
        page.deleteLater()


def test_round_trip_picks_then_step_then_pick_more(qapp, gw, tmp_path):
    """End-to-end: pick on Day 1, step back, open Day 2, pick on Day 2
    — both picks coexist in the session."""
    from mira.ui.pages.share_cuts_page import ShareCutsPage

    session = CutSession.from_draft(gw, _draft())
    page = CutSessionPage(gw, session, event_root=tmp_path)
    try:
        class _StackStub:
            def __init__(_self, current):
                _self._current = current
            def currentWidget(_self):
                return _self._current
        fake_share = type(
            "_FakeShare", (), {"_stack": _StackStub(page)})()

        # Day 1 pick.
        page._open_day(0)
        d1_rel = page._files_of_open_group()[0].export_relpath
        page._toggle_cell(0)
        # Step back via title-bar Back.
        ShareCutsPage.on_titlebar_back(fake_share)
        assert page._stack.currentIndex() == 0

        # Open Day 2 (if the fixture has one).
        if len(session.days()) > 1:
            page._open_day(1)
            d2_rel = page._files_of_open_group()[0].export_relpath
            page._toggle_cell(0)
            # Both picks live.
            assert session.is_picked(d1_rel)
            assert session.is_picked(d2_rel)
            assert session.picked_count() >= 2
    finally:
        page.deleteLater()
