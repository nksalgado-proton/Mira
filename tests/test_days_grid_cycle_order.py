"""DaysGridPage._next_state cycle order (Nelson 2026-06-18).

The locked spec/63 §4 cycle changed from ``Pick → Skip → Compare`` to
``Skip → Pick → Compare`` so a border click on a default-Skip cell
(red) advances to Pick (green) on the FIRST press — matching the
"red → green → compare → red" mental model.

Mirrors the already-existing ``core.cull_state.cycle_state`` order; this
file pins the new behavior on the days-grid path so a future cleanup
can't quietly revert it.
"""
from __future__ import annotations

import pytest

from mira.picked.status import (
    STATE_CANDIDATE,
    STATE_PICKED,
    STATE_SKIPPED,
)
from mira.ui.pages.days_grid_page import DaysGridPage


def _ns(cur, *, item_kind="photo", verb="cycle"):
    return DaysGridPage._next_state(item_kind, cur, verb)


@pytest.mark.parametrize("start,expected", [
    (None, STATE_PICKED),                  # default-Skip → Pick on first click
    (STATE_SKIPPED, STATE_PICKED),         # red → green
    (STATE_PICKED, STATE_CANDIDATE),       # green → compare
    (STATE_CANDIDATE, STATE_SKIPPED),      # compare → red (wrap)
])
def test_cycle_follows_skip_pick_compare_order(start, expected):
    assert _ns(start) == expected


def test_video_cycle_still_binary():
    """Videos remain a binary ledger (spec/63 §4 rule): cycle on a video
    degrades to Pick⇄Skip. The new tri-state order applies only to
    photos / snapshots."""
    assert _ns(STATE_PICKED, item_kind="video") == STATE_SKIPPED
    assert _ns(STATE_SKIPPED, item_kind="video") == STATE_PICKED
    assert _ns(None, item_kind="video") == STATE_PICKED


def test_explicit_pick_and_skip_verbs_unchanged():
    """The P (pick) and X (skip) verbs are absolute, not relative to the
    current state. Same before and after the cycle-order change."""
    assert _ns(STATE_SKIPPED, verb="pick") == STATE_PICKED
    assert _ns(STATE_PICKED, verb="skip") == STATE_SKIPPED
    assert _ns(None, verb="pick") == STATE_PICKED
    assert _ns(None, verb="skip") == STATE_SKIPPED


def test_space_toggle_binary_pick_skip():
    """Space (binary toggle) flips Pick ⇄ Skip; never goes through
    Compare (different from C-cycle). Pre-existing behavior."""
    assert _ns(STATE_PICKED, verb="toggle") == STATE_SKIPPED
    assert _ns(STATE_SKIPPED, verb="toggle") == STATE_PICKED
    assert _ns(STATE_CANDIDATE, verb="toggle") == STATE_PICKED
