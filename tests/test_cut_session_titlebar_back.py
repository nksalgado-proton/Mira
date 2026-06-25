"""spec/142 — title-bar Back in a Cut session steps levels, not closes.

The bug: ``ShareCutsPage.on_titlebar_back`` emitted the current sub-
page's ``back_requested`` signal blindly. ``CutSessionPage`` wires
``back_requested`` to ``_on_cancel`` (leave-with-confirm), so Back
from a day grid (the user wanted to pick another day) closed the
whole session and lost the in-progress draft.

The fix: prefer the sub-page's own ``on_titlebar_back`` dispatcher
when it has one. ``CutSessionPage`` already had a level-stepping
dispatcher (single → grid → days panel → leave); it just was never
called from the title bar.

Sub-pages WITHOUT ``on_titlebar_back`` (list / detail / pool) keep
firing ``back_requested`` exactly as before — no regression.
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


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def gw(tmp_path):
    """Event-DB gateway over the same fixture the cut_session tests use."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def session_page(qapp, gw, tmp_path):
    session = CutSession.from_draft(gw, _draft())
    page = CutSessionPage(gw, session, event_root=tmp_path)
    yield page
    page.deleteLater()


# ── CutSessionPage's own dispatcher steps levels correctly ─────────


def test_cut_session_on_titlebar_back_steps_single_to_grid(session_page):
    """Stack at single (idx 2) → on_titlebar_back drops to grid (idx 1)."""
    session_page._open_day(0)
    session_page._stack.setCurrentIndex(2)
    cancel_calls: list = []
    with patch.object(session_page, "_on_cancel",
                      side_effect=lambda: cancel_calls.append(True)):
        session_page.on_titlebar_back()
    assert session_page._stack.currentIndex() == 1
    assert cancel_calls == [], (
        "spec/142: stepping single→grid must NOT trigger cancel"
    )


def test_cut_session_on_titlebar_back_steps_grid_to_days(session_page):
    """Stack at grid (idx 1) → on_titlebar_back drops to the days
    panel (idx 0). This IS the user's "pick another day" path that
    the bug was closing the session on."""
    session_page._open_day(0)
    assert session_page._stack.currentIndex() == 1
    cancel_calls: list = []
    with patch.object(session_page, "_on_cancel",
                      side_effect=lambda: cancel_calls.append(True)):
        session_page.on_titlebar_back()
    assert session_page._stack.currentIndex() == 0
    assert cancel_calls == [], (
        "spec/142: stepping grid→days panel must NOT cancel — the "
        "user is just going to pick another day"
    )


def test_cut_session_on_titlebar_back_at_days_panel_emits_back(session_page):
    """Stack at days panel (idx 0) → on_titlebar_back fires the
    ``back_requested`` signal (which ShareCutsPage's
    ``cancel-with-confirm`` wiring acts on). Assert the EMISSION
    rather than the slot — the existing wiring of the slot to
    ``_on_cancel`` is what spec/142 leaves unchanged."""
    assert session_page._stack.currentIndex() == 0
    emitted: list[bool] = []
    session_page.back_requested.connect(lambda: emitted.append(True))
    try:
        session_page.on_titlebar_back()
    finally:
        try:
            session_page.back_requested.disconnect()
        except (TypeError, RuntimeError):
            pass
    assert emitted == [True], (
        "spec/142: Back at the days panel still emits back_requested "
        "(ShareCutsPage's connected slot then runs the existing "
        "cancel-with-confirm flow)"
    )


# ── ShareCutsPage prefers the sub-page's dispatcher ────────────────


class _SubWithDispatcher:
    """Stand-in for a sub-page that exposes ``on_titlebar_back`` —
    mimics :class:`CutSessionPage`'s contract."""

    def __init__(self):
        self.calls = 0
        self.emit_called = False
        # Duck signal so the legacy fall-through path can be detected.
        class _Sig:
            def __init__(_self):
                _self.emitted = 0
            def emit(_self):
                _self.emitted += 1
        self.back_requested = _Sig()

    def on_titlebar_back(self) -> None:
        self.calls += 1


class _SubWithoutDispatcher:
    """List / detail / pool stand-in: only ``back_requested``."""

    def __init__(self):
        class _Sig:
            def __init__(_self):
                _self.emitted = 0
            def emit(_self):
                _self.emitted += 1
        self.back_requested = _Sig()


class _StackStub:
    """Tiny duck for ``self._stack.currentWidget`` so we don't have
    to spin a real QStackedWidget for this contract."""
    def __init__(self, current):
        self._current = current
    def currentWidget(self):
        return self._current


def test_share_cuts_on_titlebar_back_prefers_subpage_dispatcher():
    """spec/142 §2 — when the current sub-page exposes
    ``on_titlebar_back`` (CutSessionPage), ShareCutsPage MUST call it
    and MUST NOT emit ``back_requested`` (which would route to
    ``_on_cancel`` and close the session)."""
    from mira.ui.pages.share_cuts_page import ShareCutsPage
    sub = _SubWithDispatcher()
    fake_self = type("_FakeShare", (), {"_stack": _StackStub(sub)})()
    ShareCutsPage.on_titlebar_back(fake_self)
    assert sub.calls == 1
    assert sub.back_requested.emitted == 0, (
        "spec/142: when on_titlebar_back exists it owns the Back "
        "gesture; back_requested MUST NOT also fire (that's the "
        "bug — _on_cancel would close the session)"
    )


def test_share_cuts_on_titlebar_back_falls_back_to_back_requested():
    """Sub-pages without ``on_titlebar_back`` (list / detail / pool)
    still get their ``back_requested`` emitted — no regression."""
    from mira.ui.pages.share_cuts_page import ShareCutsPage
    sub = _SubWithoutDispatcher()
    fake_self = type("_FakeShare", (), {"_stack": _StackStub(sub)})()
    ShareCutsPage.on_titlebar_back(fake_self)
    assert sub.back_requested.emitted == 1, (
        "spec/142: a sub-page without on_titlebar_back keeps the "
        "legacy back_requested route — no regression for list / "
        "detail / pool"
    )


def test_share_cuts_titlebar_back_at_grid_does_not_trigger_session_cancel(
    qapp, gw, tmp_path,
):
    """Headline regression — pin the actual fix end-to-end. With a
    real CutSessionPage open in the stack at the GRID level, calling
    the (now-fixed) ShareCutsPage.on_titlebar_back MUST step to the
    days panel and NOT touch ``_on_cancel``."""
    from mira.ui.pages.share_cuts_page import ShareCutsPage
    session = CutSession.from_draft(gw, _draft())
    page = CutSessionPage(gw, session, event_root=tmp_path)
    try:
        page._open_day(0)
        assert page._stack.currentIndex() == 1     # at grid

        fake_self = type("_FakeShare", (), {"_stack": _StackStub(page)})()

        cancel_calls: list = []
        with patch.object(page, "_on_cancel",
                          side_effect=lambda: cancel_calls.append(True)):
            ShareCutsPage.on_titlebar_back(fake_self)

        assert page._stack.currentIndex() == 0, (
            "spec/142: Back from grid must land on the days panel"
        )
        assert cancel_calls == [], (
            "spec/142: Back from grid must NOT trigger session cancel "
            "(the spec/142 bug)"
        )
    finally:
        page.deleteLater()
