"""Edit pool regression (spec/66 §1.1).

Pin EditorPage's ``_picked_keepers_filter`` so a future cleanup can't
silently re-leak Pick-discarded photos into Edit's prev/next
navigation and cluster sub-grid. Nelson surfaced this as a recurring
complaint ("I see discarded photos in Edit" — 10× before the bug
landed) on 2026-06-18.

The filter is pure logic over ``phase_states('pick')`` and
``self._eg.items()``; we bind the unbound method to a SimpleNamespace
double so the test never spins up the full EditorPage (whose
background workers + QTimer make teardown flaky in unit tests).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from mira.picked.status import (
    STATE_CANDIDATE,
    STATE_PICKED,
    STATE_SKIPPED,
)
from mira.ui.pages.editor_page import EditorPage


def _phase_state(item_id: str, state: str):
    return SimpleNamespace(item_id=item_id, phase="pick", state=state)


def _item(item_id: str, *, provenance: str = "captured"):
    return SimpleNamespace(id=item_id, provenance=provenance)


_UNSET = object()


def _filter_for(*, pick_states, pick_default, items=None, eg=_UNSET,
                phase: str = "edit"):
    """Bind ``EditorPage._picked_keepers_filter`` to a plain
    ``SimpleNamespace`` carrying the attributes the method touches.
    Returns the filter's result directly.

    Pass ``eg=None`` to simulate "no event gateway" (the filter must
    bail with ``None``); omit ``eg`` to get a default MagicMock that
    returns the given ``pick_states`` and ``items``."""
    ns = SimpleNamespace()
    if eg is _UNSET:
        ns._eg = MagicMock()
        ns._eg.phase_states = lambda phase_arg: (
            pick_states if phase_arg == "pick" else {})
        ns._eg.items = lambda: items or []
    else:
        ns._eg = eg
    ns._phase = phase
    ns.gateway = SimpleNamespace(
        settings=SimpleNamespace(
            load=lambda: SimpleNamespace(pick_default_state=pick_default)))
    return EditorPage._picked_keepers_filter(ns)


def test_keepers_filter_returns_only_explicit_picked_under_default_skip():
    """Default-Skip mode: only items with an explicit ``picked`` row
    survive. Skipped/candidate/no-row items are dropped — even though
    they exist in the gateway's items list."""
    states = {
        "kept": _phase_state("kept", STATE_PICKED),
        "discarded": _phase_state("discarded", STATE_SKIPPED),
        "compared": _phase_state("compared", STATE_CANDIDATE),
    }
    items = [_item("kept"), _item("discarded"), _item("compared"),
             _item("untouched")]
    keepers = _filter_for(
        pick_states=states, pick_default=STATE_SKIPPED, items=items,
    )
    assert keepers == frozenset({"kept"})


def test_keepers_filter_includes_implicit_under_default_pick():
    """Default-Pick mode (the power-user override spec/66 §1.1
    contemplates): items with no row are implicitly picked, so they
    join the explicit picks. Items with an explicit non-picked row
    (skip/compare) are still excluded."""
    states = {
        "kept": _phase_state("kept", STATE_PICKED),
        "discarded": _phase_state("discarded", STATE_SKIPPED),
        "compared": _phase_state("compared", STATE_CANDIDATE),
    }
    items = [_item("kept"), _item("discarded"), _item("compared"),
             _item("untouched")]
    keepers = _filter_for(
        pick_states=states, pick_default=STATE_PICKED, items=items,
    )
    assert keepers == frozenset({"kept", "untouched"})


def test_keepers_filter_returns_none_when_not_in_edit_phase():
    """Pick / Export keep the full pool — the filter is Edit-only. A
    Pick-phase Editor open (videos route this way) shouldn't drop any
    items."""
    assert _filter_for(
        pick_states={}, pick_default=STATE_SKIPPED, phase="pick",
    ) is None


def test_keepers_filter_returns_none_when_no_event_gateway():
    """No event open → no filter; callers fall back to the unfiltered
    list."""
    assert _filter_for(
        pick_states={}, pick_default=STATE_SKIPPED, eg=None,
    ) is None


def test_keepers_filter_swallows_phase_states_error():
    """A gateway probe failure mustn't crash the Edit surface — the
    filter returns ``None`` (fall back to unfiltered)."""
    bad_eg = MagicMock()
    bad_eg.phase_states = MagicMock(side_effect=RuntimeError("db gone"))
    assert _filter_for(
        pick_states={}, pick_default=STATE_SKIPPED, eg=bad_eg,
    ) is None


def test_keepers_filter_skips_derivative_items_under_default_pick():
    """Only ``captured`` items count as implicit picks under Default-
    Pick mode — derivative ``clip`` / ``snapshot`` items must NOT be
    swept into the pool by their default-Pick membership, since their
    parent video's Pick decision drives whether they exist at all
    (spec/56)."""
    states = {"kept": _phase_state("kept", STATE_PICKED)}
    items = [
        _item("kept"),
        _item("snap1", provenance="snapshot"),
        _item("clip1", provenance="clip"),
        _item("captured_no_row"),
    ]
    keepers = _filter_for(
        pick_states=states, pick_default=STATE_PICKED, items=items,
    )
    assert keepers == frozenset({"kept", "captured_no_row"})
