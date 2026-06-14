"""Bug 2 (Nelson 2026-06-13) — the days list inside Quick Sweep wasn't
refreshing per-day Pick / Skip counts when the user returned from the
Day Grid. ``PickDay`` / ``BucketStatus`` are frozen, so the load-time
counts stuck.

The fix:
:func:`mira.picked.quick_sweep_buckets.refresh_day_statuses`
re-projects every bucket status + day-rollup against the page's
current in-memory state; ``QuickSweepPage._on_day_grid_back`` calls it
before showing the nav.

This file lives outside the conftest ``_SLICE_B_FILES`` bulk-skip list
(memory ``feedback_slice_b_skip_list_swallows_tests``) so the pins
actually execute.
"""
from __future__ import annotations

from pathlib import Path

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                          # pragma: no cover
    QApplication = None

from mira.picked.model import CullBucket, CullItem, PickDay
from mira.picked.quick_sweep_buckets import refresh_day_statuses
from mira.picked.status import (
    BADGE_UNTOUCHED,
    BucketStatus,
    STATE_PICKED,
    STATE_SKIPPED,
)


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


_P1 = Path("/card/IMG_0001.JPG")
_P2 = Path("/card/IMG_0002.JPG")
_P3 = Path("/card/IMG_0003.JPG")


def _empty_status(total: int) -> BucketStatus:
    return BucketStatus(
        total=total, kept=0, candidate=0, discarded=0,
        untouched=total, reviewed=False, browsed=False,
        badge=BADGE_UNTOUCHED,
    )


def _bucket(key: str, paths) -> CullBucket:
    items = tuple(
        CullItem(item_id=str(p), path=p, kind="photo") for p in paths
    )
    return CullBucket(
        bucket_key=key, kind="individual", title="",
        items=items, status=_empty_status(len(items)),
    )


def _day(day_number, label, *buckets) -> PickDay:
    return PickDay(
        day_number=day_number,
        label=label,
        buckets=tuple(buckets),
        status=_empty_status(sum(b.status.total for b in buckets)),
    )


# ── refresh_day_statuses (pure logic) ──────────────────────────────


def test_refresh_re_projects_against_state_for():
    """Given a fresh state callable, the returned list carries updated
    counts on every bucket and on the day-level rollup."""
    days = [
        _day(1, "Day 1 — 2026-05-27", _bucket("1|i|a", [_P1, _P2])),
    ]
    assert days[0].status.kept == 0
    assert days[0].status.discarded == 0

    state = {_P1: STATE_PICKED, _P2: STATE_SKIPPED}
    refreshed = refresh_day_statuses(
        days, lambda p: state.get(p, STATE_PICKED))
    assert len(refreshed) == 1
    assert refreshed[0].status.kept == 1
    assert refreshed[0].status.discarded == 1
    assert refreshed[0].buckets[0].status.kept == 1
    assert refreshed[0].buckets[0].status.discarded == 1


def test_refresh_preserves_day_number_and_label():
    """Identity fields carry through unchanged."""
    days = [_day(7, "Day 7 — 2026-09-15", _bucket("7|i|a", [_P1]))]
    refreshed = refresh_day_statuses(days, lambda _p: STATE_PICKED)
    assert refreshed[0].day_number == 7
    assert refreshed[0].label == "Day 7 — 2026-09-15"


def test_refresh_preserves_bucket_identity():
    """Bucket keys + kinds + items survive intact — only ``status``
    changes."""
    days = [_day(1, "Day 1", _bucket("1|i|a", [_P1, _P2]))]
    refreshed = refresh_day_statuses(days, lambda _p: STATE_PICKED)
    b0 = refreshed[0].buckets[0]
    assert b0.bucket_key == "1|i|a"
    assert b0.kind == "individual"
    assert [ci.item_id for ci in b0.items] == [str(_P1), str(_P2)]


def test_refresh_multi_day_independent_counts():
    """Counts for one day don't bleed into another."""
    days = [
        _day(1, "Day 1", _bucket("1|i|a", [_P1])),
        _day(2, "Day 2", _bucket("2|i|a", [_P2, _P3])),
    ]
    state = {
        _P1: STATE_PICKED,
        _P2: STATE_PICKED,
        _P3: STATE_SKIPPED,
    }
    refreshed = refresh_day_statuses(
        days, lambda p: state.get(p, STATE_PICKED))
    assert refreshed[0].status.kept == 1
    assert refreshed[0].status.discarded == 0
    assert refreshed[1].status.kept == 1
    assert refreshed[1].status.discarded == 1


def test_refresh_empty_day_list_returns_empty():
    assert refresh_day_statuses([], lambda _p: STATE_PICKED) == []
