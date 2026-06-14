"""Fast Culler — days-panel + DayGridView navigation (Nelson 2026-06-05 redesign).

The Fast Culler now reuses the main Cull's days panel
(:class:`mira.ui.base.bucket_navigator.BucketNavigator`, configured
``day_grid_mode=True``) and :class:`mira.ui.base.day_grid_view.DayGridView`.

* **Single-day bypass** — if the source has photos from one calendar day, the
  days panel is skipped and the Day Grid opens directly on ``load()``.
* **Multi-day** — days panel opens first; clicking a day card opens its Day
  Grid; Back from the grid returns to the days panel.
* **Border-click cycle** — K → D → C → K (Compare is in scope per Nelson
  2026-06-05; counts as Keep in the ``saved`` set).
* **Centre-click opens the single-item viewer**, positioned on the clicked
  cell's item in the current day's sequence.

``build_fast_days`` is stubbed for a deterministic structure (the adapter
itself is covered by ``test_quick_sweep_buckets``).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.fresh_source import SourceItem
from mira.picked.model import CullBucket, PickDay, CullItem
from mira.picked.status import (
    BADGE_UNTOUCHED,
    BucketStatus,
    STATE_CANDIDATE,
    STATE_SKIPPED,
    STATE_PICKED,
)
from mira.ui.picked.quick_sweep_page import QuickSweepPage

_P1 = Path("/src/p1.jpg")
_P2 = Path("/src/p2.jpg")
_V1 = Path("/src/v1.mp4")
_P3 = Path("/src/p3.jpg")


def _items_multi_day():
    d1 = datetime(2026, 5, 27, 9)
    d2 = datetime(2026, 5, 28, 9)
    return [
        SourceItem(path=_P1, timestamp=d1, camera_id="G9"),
        SourceItem(path=_P2, timestamp=d1.replace(hour=10), camera_id="G9"),
        SourceItem(path=_V1, timestamp=d1.replace(hour=11), camera_id="G9"),
        SourceItem(path=_P3, timestamp=d2, camera_id="G9"),
    ]


def _items_single_day():
    d1 = datetime(2026, 5, 27, 9)
    return [
        SourceItem(path=_P1, timestamp=d1, camera_id="G9"),
        SourceItem(path=_P2, timestamp=d1.replace(hour=10), camera_id="G9"),
        SourceItem(path=_V1, timestamp=d1.replace(hour=11), camera_id="G9"),
    ]


def _empty_status(n: int) -> BucketStatus:
    return BucketStatus(
        total=n, kept=0, candidate=0, discarded=0, untouched=n,
        reviewed=False, browsed=False, badge=BADGE_UNTOUCHED,
    )


def _bucket(key, paths, kind="individual"):
    items = tuple(
        CullItem(item_id=p.as_posix(), path=p,
                 kind="video" if p.suffix.lower() == ".mp4" else "photo",
                 capture_time_corrected=None)
        for p in paths
    )
    return CullBucket(
        bucket_key=key, kind=kind, title="",
        items=items, status=_empty_status(len(items)),
    )


def _day(day_number, label, *buckets):
    return PickDay(
        day_number=day_number,
        label=label,
        buckets=tuple(buckets),
        status=_empty_status(sum(b.status.total for b in buckets)),
    )


@pytest.fixture
def stub_multi_day(monkeypatch):
    """Deterministic 2-day structure: day1 has p1,p2,v1; day2 has p3."""
    days = [
        _day(1, "Day 1 — 2026-05-27", _bucket("1|i|a", [_P1, _P2, _V1])),
        _day(2, "Day 2 — 2026-05-28", _bucket("2|i|b", [_P3])),
    ]
    monkeypatch.setattr(
        "mira.ui.picked.quick_sweep_page.build_fast_days",
        lambda items, **kw: days,
    )
    return days


@pytest.fixture
def stub_single_day(monkeypatch):
    """One-day structure with three items (p1, p2, v1)."""
    days = [_day(1, "Day 1 — 2026-05-27", _bucket("1|i|a", [_P1, _P2, _V1]))]
    monkeypatch.setattr(
        "mira.ui.picked.quick_sweep_page.build_fast_days",
        lambda items, **kw: days,
    )
    return days


# --------------------------------------------------------------------------- #
# Single-day bypass
# --------------------------------------------------------------------------- #


def test_single_day_bypasses_navigator(qapp, stub_single_day):
    """If load sees one day, the days panel is skipped and the Day Grid opens
    immediately (Nelson 2026-06-05)."""
    page = QuickSweepPage()
    try:
        assert page.load(_items_single_day()) is True
        assert page._stack.currentIndex() == page._DAY_GRID
        # The flat items list is scoped to the current day in cell order.
        assert [it.path for it in page._items] == [_P1, _P2, _V1]
        assert page._current_day_number == 1
    finally:
        page.deleteLater()


def test_single_day_back_from_grid_cancels(qapp, stub_single_day):
    """Back from the Day Grid with only one day == cancel (no days panel
    to fall back to)."""
    page = QuickSweepPage()
    try:
        page.load(_items_single_day())
        cancelled: list = []
        page.cancelled.connect(lambda: cancelled.append(True))
        page._on_day_grid_back()
        assert cancelled == [True]
    finally:
        page.deleteLater()


# --------------------------------------------------------------------------- #
# Multi-day navigation
# --------------------------------------------------------------------------- #


def test_multi_day_opens_navigator(qapp, stub_multi_day):
    page = QuickSweepPage()
    try:
        assert page.load(_items_multi_day()) is True
        assert page._stack.currentIndex() == page._NAV
    finally:
        page.deleteLater()


def test_day_click_opens_day_grid(qapp, stub_multi_day):
    page = QuickSweepPage()
    try:
        page.load(_items_multi_day())
        page._on_day_activated(1)
        assert page._stack.currentIndex() == page._DAY_GRID
        assert page._current_day_number == 1
        assert [it.path for it in page._items] == [_P1, _P2, _V1]
        # Switch to day 2.
        page._on_day_activated(2)
        assert page._current_day_number == 2
        assert [it.path for it in page._items] == [_P3]
    finally:
        page.deleteLater()


def test_back_from_grid_returns_to_nav_in_multi_day(qapp, stub_multi_day):
    page = QuickSweepPage()
    try:
        page.load(_items_multi_day())
        page._on_day_activated(1)
        page._on_day_grid_back()
        assert page._stack.currentIndex() == page._NAV
    finally:
        page.deleteLater()


def test_nav_return_button_cancels(qapp, stub_multi_day):
    page = QuickSweepPage()
    try:
        page.load(_items_multi_day())
        cancelled: list = []
        page.cancelled.connect(lambda: cancelled.append(True))
        page._on_nav_back()
        assert cancelled == [True]
    finally:
        page.deleteLater()


# --------------------------------------------------------------------------- #
# State cycle: K → D → C → K
# --------------------------------------------------------------------------- #


def test_border_click_cycles_state(qapp, stub_single_day):
    page = QuickSweepPage()
    try:
        page.load(_items_single_day())
        # Default Keep — first border-click → Discard.
        assert page._state[_P1] == STATE_PICKED
        page._on_day_cell_border(0)
        assert page._state[_P1] == STATE_SKIPPED
        page._on_day_cell_border(0)
        assert page._state[_P1] == STATE_CANDIDATE
        page._on_day_cell_border(0)
        assert page._state[_P1] == STATE_PICKED     # wrap
    finally:
        page.deleteLater()


def test_viewer_pill_cycles_state(qapp, stub_single_day):
    """K/D pill click in the viewer cycles K → D → C → K."""
    page = QuickSweepPage()
    try:
        page.load(_items_single_day())
        page._on_day_cell_activated(0)            # open p1 in viewer
        assert page._state[_P1] == STATE_PICKED
        page._toggle_state()
        assert page._state[_P1] == STATE_SKIPPED
        page._toggle_state()
        assert page._state[_P1] == STATE_CANDIDATE
        page._toggle_state()
        assert page._state[_P1] == STATE_PICKED
    finally:
        page.deleteLater()


def test_compare_counts_as_kept_in_saved(qapp, stub_single_day):
    """kept_paths() includes Compare-marked items (Nelson 2026-06-05).
    The offload pipeline copies them through; the real decision lands in
    main Cull."""
    page = QuickSweepPage()
    try:
        page.load(_items_single_day())
        # P1 -> Compare, P2 -> Discard, V1 -> Keep (default).
        page._state[_P1] = STATE_CANDIDATE
        page._state[_P2] = STATE_SKIPPED
        kept = page.kept_paths()
        assert _P1 in kept                        # Compare counts
        assert _V1 in kept                        # Keep
        assert _P2 not in kept                    # Discard
    finally:
        page.deleteLater()


# --------------------------------------------------------------------------- #
# Cell centre-click → viewer
# --------------------------------------------------------------------------- #


def test_centre_click_opens_viewer_at_cell_index(qapp, stub_single_day):
    page = QuickSweepPage()
    try:
        page.load(_items_single_day())
        page._on_day_cell_activated(1)            # open p2
        assert page._stack.currentIndex() == page._VIEWER
        assert page._items[page._index].path == _P2
    finally:
        page.deleteLater()


def test_viewer_back_returns_to_day_grid(qapp, stub_single_day):
    page = QuickSweepPage()
    try:
        page.load(_items_single_day())
        page._on_day_cell_activated(1)
        assert page._stack.currentIndex() == page._VIEWER
        page._on_viewer_back()
        assert page._stack.currentIndex() == page._DAY_GRID
    finally:
        page.deleteLater()


# --------------------------------------------------------------------------- #
# Empty / cancelled load
# --------------------------------------------------------------------------- #


def test_load_empty_returns_false(qapp):
    page = QuickSweepPage()
    try:
        assert page.load([]) is False
        assert page._items == []
        assert page._all_items == []
        assert page._state == {}
    finally:
        page.deleteLater()
