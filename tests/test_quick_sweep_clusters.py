"""Quick Sweep cluster expansion + batch ops (spec/52 slice C, Nelson 2026-06-09).

Covers the slice-C wiring on top of the slice-B cluster cells:

* Centre-click on a cluster cell opens the cluster sub-grid (a second
  :class:`DayGridView` instance) with the cluster's members.
* Sub-grid Pick all / Skip all bulk-mutates every member.
* Day-grid Pick all / Skip all bulk-mutates every item in the current day.
* Day-grid border-click on a cluster cell bulk-cycles the cluster's K/D/C.
* Centre-click on a sub-grid member opens the single-item viewer scoped
  to the cluster's members; Back returns to the sub-grid, not the day grid.

Tests instantiate ``QuickSweepPage`` against the session-scoped ``qapp``
fixture (conftest) and stub ``build_fast_days`` so the day model is
deterministic (no scanner / EXIF reading required).

NOTE: this file is intentionally separate from ``test_quick_sweep_buckets``
because the latter sits on the legacy Slice-B bulk-skip list in
``tests/conftest.py``. The fixtures + helpers here are duplicated rather
than imported so the file stands alone.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.cull_state import (
    STATE_CANDIDATE,
    STATE_DISCARDED as STATE_SKIPPED,
    STATE_KEPT as STATE_PICKED,
)
from core.fresh_source import SourceItem
from mira.picked.model import CullBucket, CullItem, PickDay
from mira.picked.status import BADGE_UNTOUCHED, BucketStatus
from mira.ui.picked.quick_sweep_page import QuickSweepPage


# ─── fixture paths ───────────────────────────────────────────────────────────


_P1 = Path("/src/p1.jpg")
_P2 = Path("/src/p2.jpg")
_P3 = Path("/src/p3.jpg")
_SOLO = Path("/src/solo.jpg")


def _empty_status(n: int) -> BucketStatus:
    return BucketStatus(
        total=n, kept=0, candidate=0, discarded=0, untouched=n,
        reviewed=False, browsed=False, badge=BADGE_UNTOUCHED,
    )


def _cluster_bucket(key: str, paths: list[Path], kind: str = "burst") -> CullBucket:
    items = tuple(
        CullItem(
            item_id=p.as_posix(), path=p, kind="photo",
            capture_time_corrected=f"2026-05-27T09:0{i}:00",
        )
        for i, p in enumerate(paths)
    )
    return CullBucket(
        bucket_key=key, kind=kind,
        title=f"{kind.title()} · {len(paths)}",
        items=items, status=_empty_status(len(items)),
    )


def _flat_bucket(key: str, paths: list[Path]) -> CullBucket:
    items = tuple(
        CullItem(
            item_id=p.as_posix(), path=p, kind="photo",
            capture_time_corrected="2026-05-27T09:30:00",
        )
        for p in paths
    )
    return CullBucket(
        bucket_key=key, kind="individual", title=f"Individual · {len(paths)}",
        items=items, status=_empty_status(len(items)),
    )


def _day(buckets: list[CullBucket]) -> PickDay:
    return PickDay(
        day_number=1,
        label="Day 1 — 2026-05-27",
        buckets=tuple(buckets),
        status=_empty_status(sum(b.status.total for b in buckets)),
    )


@pytest.fixture
def stub_burst_day(monkeypatch):
    """One day: one burst cluster (3 frames) + one solo individual cell."""
    burst = _cluster_bucket("1|burst|b1", [_P1, _P2, _P3], kind="burst")
    solo = _flat_bucket("1|i|s", [_SOLO])
    days = [_day([burst, solo])]
    monkeypatch.setattr(
        "mira.ui.picked.quick_sweep_page.build_fast_days",
        lambda items, **kw: days,
    )
    return days


def _items_for_burst_day() -> list[SourceItem]:
    """SourceItems matching ``stub_burst_day`` paths."""
    d1 = datetime(2026, 5, 27, 9, 0, 0)
    return [
        SourceItem(path=_P1, timestamp=d1.replace(minute=0), camera_id="G9"),
        SourceItem(path=_P2, timestamp=d1.replace(minute=1), camera_id="G9"),
        SourceItem(path=_P3, timestamp=d1.replace(minute=2), camera_id="G9"),
        SourceItem(path=_SOLO, timestamp=d1.replace(minute=30), camera_id="G9"),
    ]


def _cluster_idx(page: QuickSweepPage) -> int:
    """Index of the (one) cluster cell in the current day's cells."""
    return next(
        i for i, c in enumerate(page._current_day_cells) if c.is_cluster)


# ─── cluster expansion ──────────────────────────────────────────────────────


def test_cluster_centre_click_opens_sub_grid(qapp, stub_burst_day):
    """Centre-click on a cluster cell switches to the sub-grid stack page
    with that cluster's members."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_cell_activated(_cluster_idx(page))
        assert page._stack.currentIndex() == page._CLUSTER_GRID
        assert page._current_cluster is not None
        assert page._current_cluster.kind == "burst"
        assert len(page._current_cluster_cells) == 3
    finally:
        page.deleteLater()


def test_cluster_back_returns_to_day_grid(qapp, stub_burst_day):
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_cell_activated(_cluster_idx(page))
        page._on_cluster_back()
        assert page._stack.currentIndex() == page._DAY_GRID
        assert page._current_cluster is None
    finally:
        page.deleteLater()


def test_cluster_centre_click_on_item_cell_still_opens_viewer(
    qapp, stub_burst_day,
):
    """The cluster branch must not break the existing item-cell path —
    clicking the solo's flat cell still opens the single-item viewer."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        flat_idx = next(
            i for i, c in enumerate(page._current_day_cells)
            if not c.is_cluster)
        page._on_day_cell_activated(flat_idx)
        assert page._stack.currentIndex() == page._VIEWER
        assert page._viewer_came_from == page._DAY_GRID
    finally:
        page.deleteLater()


# ─── spec/63 slice 4: the embedded viewport drives the viewer ────────────────


def _open_cluster_viewer(page: QuickSweepPage):
    """Open the burst cluster's sub-grid and land on its first member in
    the single-item viewer (the viewport now owns nav + keys)."""
    page.load(_items_for_burst_day())
    page._on_day_cell_activated(_cluster_idx(page))
    page._on_cluster_cell_activated(0)


def test_viewer_hands_the_member_list_to_the_viewport(qapp, stub_burst_day):
    page = QuickSweepPage()
    try:
        _open_cluster_viewer(page)
        assert page._stack.currentIndex() == page._VIEWER
        # The viewport holds the cluster's three members, current on the
        # one that was clicked, and the surface chrome followed.
        assert len(page._viewport.items()) == 3
        assert page._viewport.current_index() == 0
        assert page._current_path() == _P1
        assert "1 / 3" in page._position_label.text()
    finally:
        page._viewport.shutdown_video()
        page.deleteLater()


def test_locked_verbs_drive_state_with_quick_sweep_semantics(
    qapp, stub_burst_day,
):
    """spec/63 §4 on Quick Sweep: P picks, X skips, Space is the binary
    Pick⇄Skip toggle, C runs the full K→D→C cycle. Driven through the
    real key→verb→state chain on the embedded viewport."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    page = QuickSweepPage()
    try:
        _open_cluster_viewer(page)
        vp = page._viewport
        QTest.keyClick(vp, Qt.Key.Key_X)
        assert page._state[_P1] == STATE_SKIPPED
        QTest.keyClick(vp, Qt.Key.Key_P)
        assert page._state[_P1] == STATE_PICKED
        QTest.keyClick(vp, Qt.Key.Key_Space)          # binary toggle → Skip
        assert page._state[_P1] == STATE_SKIPPED
        QTest.keyClick(vp, Qt.Key.Key_Space)          # → Pick
        assert page._state[_P1] == STATE_PICKED
        QTest.keyClick(vp, Qt.Key.Key_C)              # cycle K→D
        assert page._state[_P1] == STATE_SKIPPED
        QTest.keyClick(vp, Qt.Key.Key_C)              # cycle D→C
        assert page._state[_P1] == STATE_CANDIDATE
        QTest.keyClick(vp, Qt.Key.Key_C)              # cycle C→K
        assert page._state[_P1] == STATE_PICKED
    finally:
        page._viewport.shutdown_video()
        page.deleteLater()


def test_compare_button_flow_persists_into_the_in_memory_ledger(
    qapp, stub_burst_day,
):
    """Nelson 2026-06-12: the Picker's Compare button/page, ported. Mark
    cluster members Compare → the Compare button's handler opens the
    side-by-side grid (eg=None — Quick Sweep is pre-ingest), and a tile
    decision persists into the in-memory K/D/C ledger."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_cell_activated(_cluster_idx(page))
        assert page._stack.currentIndex() == page._CLUSTER_GRID
        # Two members to Compare, then reproject the sub-grid cells.
        page._state[_P1] = STATE_CANDIDATE
        page._state[_P2] = STATE_CANDIDATE
        page._current_cluster_cells = page._build_cluster_member_cells(
            page._current_cluster)
        page._on_compare_requested(page._CLUSTER_GRID)
        assert page._stack.currentIndex() == page._COMPARE
        assert len(page._compare_page._items) == 2
        # Finalise tile 0 (candidate → Pick) — persists rebuild→legacy.
        page._compare_page._on_cycle(0)
        finalised = page._compare_page._items[0].path
        assert page._state[finalised] == STATE_PICKED
        page._on_compare_quit()
        assert page._stack.currentIndex() == page._CLUSTER_GRID
    finally:
        page._viewport.shutdown_video()
        page.deleteLater()


def test_compare_skips_videos_and_needs_two_photos(qapp, stub_burst_day):
    """A single Compare photo must not open the grid (defensive <2)."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_cell_activated(_cluster_idx(page))
        page._state[_P1] = STATE_CANDIDATE            # only ONE compare
        page._current_cluster_cells = page._build_cluster_member_cells(
            page._current_cluster)
        page._on_compare_requested(page._CLUSTER_GRID)
        assert page._stack.currentIndex() == page._CLUSTER_GRID  # no-op
    finally:
        page._viewport.shutdown_video()
        page.deleteLater()


def test_browse_mode_ignores_decision_verbs(qapp):
    """Read-only browse mode: the viewport still emits verbs, but the
    surface no-ops them (no K/D/C ledger to touch)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    page = QuickSweepPage(browse_mode=True)
    try:
        page.load(_items_for_burst_day())
        assert page._stack.currentIndex() == page._VIEWER
        before = dict(page._state)
        QTest.keyClick(page._viewport, Qt.Key.Key_X)
        QTest.keyClick(page._viewport, Qt.Key.Key_C)
        assert page._state == before        # untouched
    finally:
        page._viewport.shutdown_video()
        page.deleteLater()


# ─── cluster batch ops ───────────────────────────────────────────────────────


def test_cluster_pick_all_marks_every_member_picked(qapp, stub_burst_day):
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_cell_activated(_cluster_idx(page))
        # Move every member to Skip first so Pick-all has visible effect.
        for p in (_P1, _P2, _P3):
            page._state[p] = STATE_SKIPPED
        page._on_cluster_pick_all()
        for p in (_P1, _P2, _P3):
            assert page._state[p] == STATE_PICKED, p
        # The non-cluster solo is untouched (default Pick).
        assert page._state[_SOLO] == STATE_PICKED
    finally:
        page.deleteLater()


def test_cluster_skip_all_marks_every_member_skipped(qapp, stub_burst_day):
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_cell_activated(_cluster_idx(page))
        page._on_cluster_skip_all()
        for p in (_P1, _P2, _P3):
            assert page._state[p] == STATE_SKIPPED, p
        # Non-cluster items untouched (default Pick).
        assert page._state[_SOLO] == STATE_PICKED
    finally:
        page.deleteLater()


def test_cluster_border_click_bulk_cycles_state(qapp, stub_burst_day):
    """Border-click on the cluster's day-grid cell cycles every member
    together (K → D → C → K)."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        assert page._state[_P1] == STATE_PICKED
        page._on_day_cell_border(_cluster_idx(page))
        for p in (_P1, _P2, _P3):
            assert page._state[p] == STATE_SKIPPED, p
        # The non-cluster solo is unmoved.
        assert page._state[_SOLO] == STATE_PICKED
    finally:
        page.deleteLater()


# ─── day-level batch ops ─────────────────────────────────────────────────────


def test_day_pick_all_marks_every_item_in_day(qapp, stub_burst_day):
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        for p in (_P1, _P2, _P3, _SOLO):
            page._state[p] = STATE_SKIPPED
        page._on_day_pick_all()
        for p in (_P1, _P2, _P3, _SOLO):
            assert page._state[p] == STATE_PICKED, p
    finally:
        page.deleteLater()


def test_day_skip_all_marks_every_item_in_day(qapp, stub_burst_day):
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_skip_all()
        for p in (_P1, _P2, _P3, _SOLO):
            assert page._state[p] == STATE_SKIPPED, p
    finally:
        page.deleteLater()


# ─── viewer routing from cluster sub-grid ───────────────────────────────────


def test_cluster_member_centre_click_opens_viewer_scoped_to_cluster(
    qapp, stub_burst_day,
):
    """Centre-click on a sub-grid member opens the viewer with ``_items``
    scoped to the cluster's members only (so Previous/Next walk inside
    the cluster) and ``_viewer_came_from`` set to the cluster grid."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_cell_activated(_cluster_idx(page))
        page._on_cluster_cell_activated(1)
        assert page._stack.currentIndex() == page._VIEWER
        assert page._viewer_came_from == page._CLUSTER_GRID
        # Viewer items list scoped to the cluster's 3 members, not all 4
        # items in the day.
        assert len(page._items) == 3
        assert page._items[page._index].path == _P2
    finally:
        page.deleteLater()


def test_viewer_back_from_cluster_returns_to_sub_grid(qapp, stub_burst_day):
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_cell_activated(_cluster_idx(page))
        page._on_cluster_cell_activated(0)
        assert page._stack.currentIndex() == page._VIEWER
        page._on_viewer_back()
        assert page._stack.currentIndex() == page._CLUSTER_GRID
    finally:
        page.deleteLater()


def test_viewer_back_from_day_item_returns_to_day_grid(qapp, stub_burst_day):
    """Regression: when the viewer was opened from a day-grid flat cell
    (NOT via a cluster), Back still routes to the day grid."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        flat_idx = next(
            i for i, c in enumerate(page._current_day_cells)
            if not c.is_cluster)
        page._on_day_cell_activated(flat_idx)
        page._on_viewer_back()
        assert page._stack.currentIndex() == page._DAY_GRID
    finally:
        page.deleteLater()


# ─── regression: bug fixes from 2026-06-09 eyeball ─────────────────────────


def test_state_for_translates_legacy_to_rebuild_values(qapp, stub_burst_day):
    """Regression for the state-string mismatch bug: ``_state_for`` must
    return rebuild-vocabulary values (``'picked'``/``'skipped'``/
    ``'candidate'``) so the renderer's ``_phase_state_map`` filter
    accepts them. Without this, every border-click would silently fail
    to update cell colour."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        # Default — legacy STATE_KEPT = 'kept' → rebuild 'picked'.
        assert page._state_for(_P1) == "picked"
        page._state[_P1] = STATE_SKIPPED       # 'discarded' (legacy)
        assert page._state_for(_P1) == "skipped"
    finally:
        page.deleteLater()


def test_item_border_click_actually_repaints_cell(qapp, stub_burst_day):
    """Regression: border-click on a flat photo cell must reproject the
    cell so its colour reflects the new state (not just mutate
    ``self._state`` silently). Verifies via the in-memory CullCell
    rebuilt by ``_refresh_cell``."""
    from mira.picked.status import CellColor
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        flat_idx = next(
            i for i, c in enumerate(page._current_day_cells)
            if not c.is_cluster)
        # Default = Pick → KEPT (green).
        assert page._current_day_cells[flat_idx].color is CellColor.KEPT
        page._on_day_cell_border(flat_idx)
        # First cycle step → Skip → DISCARDED (red).
        assert page._state[_SOLO] == STATE_SKIPPED
        assert page._current_day_cells[flat_idx].color is CellColor.DISCARDED
    finally:
        page.deleteLater()


def test_cluster_border_click_paints_aggregate_colour(qapp, stub_burst_day):
    """Regression: border-click on a cluster cell on the day grid must
    update the cluster cell's aggregate colour. Before the fix the
    cluster branch of ``_refresh_cell`` early-returned, leaving the
    cluster green even after a bulk-cycle moved members to skip."""
    from mira.picked.status import CellColor
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        cidx = _cluster_idx(page)
        assert page._current_day_cells[cidx].color is CellColor.KEPT
        page._on_day_cell_border(cidx)
        # All members → skip → cluster aggregate = DISCARDED.
        assert page._current_day_cells[cidx].color is CellColor.DISCARDED
    finally:
        page.deleteLater()


def test_cluster_cell_shows_mixed_yellow_when_members_disagree(
    qapp, stub_burst_day,
):
    """Regression: when one cluster member is Pick and another is Skip,
    the cluster aggregate must be MIXED (yellow border)."""
    from mira.picked.status import CellColor
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        cidx = _cluster_idx(page)
        # Enter cluster sub-grid, flip one member to Skip, leave others Pick.
        page._on_day_cell_activated(cidx)
        # Sub-grid member 0 → Skip (border-click cycles K → D in 3-state).
        page._on_cluster_cell_border(0)
        # Members now: [_P1=Skip, _P2=Pick, _P3=Pick] — aggregate = MIXED.
        assert page._state[_P1] == STATE_SKIPPED
        assert page._state[_P2] == STATE_PICKED
        # Parent day-grid cluster cell must reflect the mix.
        assert page._current_day_cells[cidx].color is CellColor.MIXED
    finally:
        page.deleteLater()


def test_quick_sweep_default_state_setting_drives_initial_state(
    qapp, stub_burst_day, monkeypatch,
):
    """The user-tunable ``quick_sweep_default_state`` setting decides
    what ``self._state`` is seeded with for un-decided items. Default is
    'picked' (Quick Sweep's permissive contract); 'skipped' flips to a
    stricter "actively pick keepers" flow."""
    from mira.settings.model import Settings

    # Stub SettingsRepo.load() to return a Settings with the strict default.
    class _StubRepo:
        def load(self):
            return Settings(quick_sweep_default_state="skipped")

    monkeypatch.setattr(
        "mira.ui.picked.quick_sweep_page.SettingsRepo",
        lambda: _StubRepo(),
    )
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        # All items seeded with the legacy "discarded" value.
        for p in (_P1, _P2, _P3, _SOLO):
            assert page._state[p] == STATE_SKIPPED, p
        # Renderer default also flips.
        assert page._renderer_default == "skipped"
        assert page._legacy_default == STATE_SKIPPED
    finally:
        page.deleteLater()


def test_quick_sweep_default_state_default_is_picked(
    qapp, stub_burst_day, monkeypatch,
):
    """Out-of-box default seeds every item as Pick (the spec/52
    "preserve on inattention" contract)."""
    from mira.settings.model import Settings

    class _StubRepo:
        def load(self):
            return Settings()    # all defaults

    monkeypatch.setattr(
        "mira.ui.picked.quick_sweep_page.SettingsRepo",
        lambda: _StubRepo(),
    )
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        for p in (_P1, _P2, _P3, _SOLO):
            assert page._state[p] == STATE_PICKED, p
        assert page._renderer_default == "picked"
        assert page._legacy_default == STATE_PICKED
    finally:
        page.deleteLater()


# ─── Back-confirm-and-save flow (Nelson 2026-06-09) ─────────────────────────


def test_back_at_single_day_pops_confirm_and_emits_saved(
    qapp, stub_burst_day, monkeypatch,
):
    """Single-day case: Back from the Day Grid IS the outermost back —
    fire the confirmation dialog; on Confirm emit ``saved`` with the
    kept paths (capture orchestrator commits the copy)."""
    page = QuickSweepPage()
    saved_emissions: list = []
    cancelled_emissions: list = []
    try:
        page.load(_items_for_burst_day())
        page.saved.connect(lambda kept: saved_emissions.append(set(kept)))
        page.cancelled.connect(lambda: cancelled_emissions.append(True))

        monkeypatch.setattr(page, "_confirm_done", lambda: True)
        page._on_day_grid_back()

        assert len(saved_emissions) == 1
        # Default Quick Sweep is Pick → every item kept.
        assert saved_emissions[0] == {_P1, _P2, _P3, _SOLO}
        assert cancelled_emissions == []
    finally:
        page.deleteLater()


def test_back_at_single_day_cancel_stays_in_page(
    qapp, stub_burst_day, monkeypatch,
):
    """Confirm=Cancel keeps the user in Quick Sweep — no signals, no
    navigation change."""
    page = QuickSweepPage()
    saved_emissions: list = []
    cancelled_emissions: list = []
    try:
        page.load(_items_for_burst_day())
        page.saved.connect(lambda kept: saved_emissions.append(set(kept)))
        page.cancelled.connect(lambda: cancelled_emissions.append(True))

        monkeypatch.setattr(page, "_confirm_done", lambda: False)
        page._on_day_grid_back()

        assert saved_emissions == []
        assert cancelled_emissions == []
    finally:
        page.deleteLater()


def test_nav_back_pops_confirm_and_emits_saved(
    qapp, stub_burst_day, monkeypatch,
):
    """Multi-day case: nav-back (days panel return button) → confirm
    dialog → emit saved on Confirm. Re-uses the burst day fixture as a
    single-day stand-in for the days panel — the handler doesn't care
    how many days, only that it's the outermost level."""
    page = QuickSweepPage()
    saved_emissions: list = []
    try:
        page.load(_items_for_burst_day())
        page.saved.connect(lambda kept: saved_emissions.append(set(kept)))

        monkeypatch.setattr(page, "_confirm_done", lambda: True)
        page._on_nav_back()

        assert len(saved_emissions) == 1
        assert saved_emissions[0] == {_P1, _P2, _P3, _SOLO}
    finally:
        page.deleteLater()


def test_browse_mode_back_skips_confirm_and_cancels(qapp, stub_burst_day):
    """Browse mode is read-only — Back must NOT pop the confirmation
    (there's nothing to copy); it cancels cleanly."""
    page = QuickSweepPage(browse_mode=True)
    cancelled_emissions: list = []
    saved_emissions: list = []
    try:
        page.load(_items_for_burst_day())
        page.cancelled.connect(lambda: cancelled_emissions.append(True))
        page.saved.connect(lambda kept: saved_emissions.append(set(kept)))
        page._on_nav_back()
        assert cancelled_emissions == [True]
        assert saved_emissions == []
    finally:
        page.deleteLater()


def test_thumb_cache_is_unbounded_across_session(qapp, stub_burst_day):
    """Regression for the thumb-loader thrash: the cache must NOT
    evict old entries — it's session-scoped, cleared only by ``load()``.
    The prior LRU bounded at 24 caused cells beyond cell ~24 in the
    cluster sub-grid to never get a thumbnail."""
    from PyQt6.QtGui import QPixmap
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        # Seed the cache past the prior 24-entry LRU bound.
        for i in range(50):
            page._thumb_pixmap_cache[Path(f"/fake/{i}.jpg")] = QPixmap(8, 8)
        assert len(page._thumb_pixmap_cache) == 50
        # All entries still present (no LRU eviction).
        for i in range(50):
            assert Path(f"/fake/{i}.jpg") in page._thumb_pixmap_cache
    finally:
        page.deleteLater()


# ── Visited ticks + "Start a new pass…" (Nelson 2026-06-09 — PickPage port) ──


def _solo_idx(page: QuickSweepPage) -> int:
    """Index of the (one) non-cluster item cell in the current day's cells."""
    return next(
        i for i, c in enumerate(page._current_day_cells)
        if not c.is_cluster and c.item_id is not None
    )


def test_item_cell_marked_visited_on_centre_click(qapp, stub_burst_day):
    """Centre-click on a flat item cell adds its item_id to the visited
    set and the cell repaints with ``visited=True``."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        idx = _solo_idx(page)
        assert page._current_day_cells[idx].visited is False
        page._on_day_cell_activated(idx)
        assert _SOLO.as_posix() in page._visited_paths
        assert page._current_day_cells[idx].visited is True
    finally:
        page.deleteLater()


def test_cluster_cell_marked_visited_on_centre_click(qapp, stub_burst_day):
    """Centre-click on a cluster cell adds its bucket_key to the cluster
    visited set and the day-grid cluster cell repaints visited."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        c_idx = _cluster_idx(page)
        cluster = page._current_day_cells[c_idx].cluster
        assert page._current_day_cells[c_idx].visited is False
        page._on_day_cell_activated(c_idx)
        assert cluster.bucket_key in page._visited_clusters
        assert page._current_day_cells[c_idx].visited is True
    finally:
        page.deleteLater()


def test_cluster_member_marked_visited_on_sub_grid_click(qapp, stub_burst_day):
    """Centre-click on a cluster member cell in the sub-grid adds that
    member's item_id to the visited set and the sub-grid cell shows the
    tick."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._on_day_cell_activated(_cluster_idx(page))
        # Open the first member.
        page._on_cluster_cell_activated(0)
        member_id = _P1.as_posix()
        assert member_id in page._visited_paths
        assert page._current_cluster_cells[0].visited is True
    finally:
        page.deleteLater()


def test_on_clear_marks_wipes_visited_sets_and_repaints_cells(
    qapp, stub_burst_day, monkeypatch,
):
    """The "Start a new pass…" handler clears both visited sets +
    reprojects every open cell so the ticks disappear immediately. State
    (Pick/Skip/Compare decisions) is preserved."""
    from PyQt6.QtWidgets import QMessageBox
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        # Seed some visits + a pick.
        page._visited_paths.add(_SOLO.as_posix())
        page._visited_clusters.add(page._current_day_cells[_cluster_idx(page)]
                                   .cluster.bucket_key)
        page._state[_SOLO] = STATE_PICKED
        # Auto-accept the QMessageBox confirmation.
        def _auto_yes(self):
            self.setResult(0)
            for btn in self.buttons():
                if self.standardButton(btn) == QMessageBox.StandardButton.Yes:
                    self._auto_click = btn
                    break
        monkeypatch.setattr(QMessageBox, "exec",
                            lambda self: setattr(self, "_clicked_btn", None)
                            or 0)
        monkeypatch.setattr(QMessageBox, "clickedButton",
                            lambda self: next(
                                (b for b in self.buttons()
                                 if self.standardButton(b)
                                 == QMessageBox.StandardButton.Yes),
                                None,
                            ))
        page._on_clear_marks()
        assert page._visited_paths == set()
        assert page._visited_clusters == set()
        # Solo cell's decision (PICKED) is preserved.
        assert page._state[_SOLO] == STATE_PICKED
        # All day-grid cells now show visited=False.
        for cell in page._current_day_cells:
            assert cell.visited is False
    finally:
        page.deleteLater()


def test_on_clear_marks_no_op_when_nothing_visited(qapp, stub_burst_day):
    """No visits + no clusters seen → the handler returns immediately
    without showing the confirmation dialog."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        # Should not throw, no confirmation needed.
        page._on_clear_marks()
        assert page._visited_paths == set()
        assert page._visited_clusters == set()
    finally:
        page.deleteLater()


def test_load_resets_visited_state(qapp, stub_burst_day):
    """Loading a fresh card clears any visited state from the prior pass."""
    page = QuickSweepPage()
    try:
        page.load(_items_for_burst_day())
        page._visited_paths.add(_SOLO.as_posix())
        page._visited_clusters.add("stale-bucket-key")
        # Reload the same card → state resets.
        page.load(_items_for_burst_day())
        assert page._visited_paths == set()
        assert page._visited_clusters == set()
    finally:
        page.deleteLater()
