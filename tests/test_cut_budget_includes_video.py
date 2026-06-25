"""spec/144 — the Cut budget sums real clip-segment durations.

Before spec/144 the budget SQL summed ``COALESCE(si.duration_ms,
oi.duration_ms, 0)`` for video members — the source item's WHOLE
duration. That was wrong: cut members are **segments** (so the budget
overstated the source contribution); for un-probed source items it
was ``0`` (so the budget understated the show entirely). Nelson's
specific symptom — a clip-heavy show clocked at 25 min for a 1 h+
render — followed from the un-probed/0 case dominating.

The fix: ``ShowTotals.video_ms_total`` sums ``lineage.duration_ms``
(the segment's TRUE on-disk length recorded at export). Tests pin:

* per-Cut totals (``cut_show_totals``) sum segment durations,
* DC totals (``dc_show_totals``) over a Cut's universe sum the same,
* a multi-clip cut budgets correctly (the 25-min regression),
* photos contribute 0 (sanity),
* a session-side ``CutSession.totals()`` matches the gateway path.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_draft import CutDraft, PIN_WEED_OUT
from mira.shared.cut_session import CutSession
from mira.store import models as m
from mira.store.repo import EventStore

FIXED_NOW = "2026-06-25T00:00:00+00:00"


# --------------------------------------------------------------------- #
# Fixture — a Cut with 3 video clips at known lengths + 2 photos
# --------------------------------------------------------------------- #


def _now() -> str:
    return FIXED_NOW


def _build_doc(clip_durations_ms: list[int]) -> m.EventDocument:
    """An event document with N video clip-segment lineage rows whose
    persisted ``duration_ms`` matches ``clip_durations_ms``. The source
    item carries a DELIBERATELY LARGE ``duration_ms`` (90_000) so a
    regression that falls back to the source value would clearly
    over-count."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-b", name="Budget fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [
        m.TripDay(day_number=1, date="2026-06-25"),
    ]
    doc.cameras = [m.Camera(camera_id="cam")]
    doc.items = [
        m.Item(
            id="p1", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath="Original Media/p1.jpg",
            sha256="a" * 64, byte_size=1, materialized_at=FIXED_NOW,
            materialized_phase="ingest", camera_id="cam", day_number=1,
            capture_time_raw="2026-06-25T08:00:00",
            capture_time_corrected="2026-06-25T08:00:00",
        ),
        m.Item(
            id="p2", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath="Original Media/p2.jpg",
            sha256="b" * 64, byte_size=1, materialized_at=FIXED_NOW,
            materialized_phase="ingest", camera_id="cam", day_number=1,
            capture_time_raw="2026-06-25T08:05:00",
            capture_time_corrected="2026-06-25T08:05:00",
        ),
        m.Item(
            id="v_src", kind="video", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath="Original Media/long_video.mp4",
            sha256="c" * 64, byte_size=10_000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="cam", day_number=1,
            capture_time_raw="2026-06-25T09:00:00",
            capture_time_corrected="2026-06-25T09:00:00",
            # The whole source is 90 s — the clip segments below carry
            # MUCH shorter values per their lineage rows.
            duration_ms=90_000,
        ),
    ]
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/000_p1.jpg",
                  phase="edit", source_kind="item", source_item_id="p1",
                  exported_at="t0"),
        m.Lineage(export_relpath="Exported Media/001_p2.jpg",
                  phase="edit", source_kind="item", source_item_id="p2",
                  exported_at="t1"),
    ]
    # N clip-segment lineage rows, all sourced from v_src, each with
    # its persisted segment duration.
    for idx, dms in enumerate(clip_durations_ms):
        doc.lineage.append(m.Lineage(
            export_relpath=f"Exported Media/00{2 + idx}_clip{idx + 1}.mp4",
            phase="edit", source_kind="item", source_item_id="v_src",
            exported_at=f"t{2 + idx}",
            duration_ms=dms,
        ))
    doc.cuts = [m.Cut(
        id="cut-b", tag="budget_show",
        created_at=FIXED_NOW, updated_at=FIXED_NOW)]
    doc.cut_members = [
        m.CutMember(cut_id="cut-b",
                    export_relpath=ln.export_relpath,
                    added_at=FIXED_NOW)
        for ln in doc.lineage
    ]
    return doc


@pytest.fixture
def gw_three_clips(tmp_path):
    """A Cut with 3 clip segments at 18_000 / 22_000 / 15_000 ms +
    2 photos. Sum-of-segments = 55_000 ms (≈ 55 s)."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-b")
    store.save_document(_build_doc([18_000, 22_000, 15_000]))
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# --------------------------------------------------------------------- #
# 1. Per-Cut totals sum segment durations
# --------------------------------------------------------------------- #


def test_cut_show_totals_sums_segment_durations(gw_three_clips):
    """spec/144 — ``cut_show_totals`` reads ``lineage.duration_ms`` for
    each video member and sums the SEGMENT values, not the source
    item's 90_000 ms."""
    totals = gw_three_clips.cut_show_totals("cut-b")
    assert totals.photo_count == 2
    assert totals.video_count == 3
    assert totals.video_ms_total == 18_000 + 22_000 + 15_000
    # Sanity: the source item's 90_000 ms never leaks through (a
    # 3-clip sum of 90_000 each would be 270_000).
    assert totals.video_ms_total != 90_000 * 3


def test_dc_show_totals_sums_segment_durations_too(gw_three_clips):
    """spec/144 — ``dc_show_totals`` (the dialog's live probe) walks
    the SAME lineage column. A DC that resolves to the same
    membership budgets identically."""
    totals = gw_three_clips.dc_show_totals(
        [["+", "exported"]])
    assert totals.video_count == 3
    assert totals.video_ms_total == 18_000 + 22_000 + 15_000


# --------------------------------------------------------------------- #
# 2. The 25-min regression — a real-world-sized show
# --------------------------------------------------------------------- #


def test_long_show_budget_does_not_undercount(tmp_path):
    """Nelson's lived symptom: a clip-heavy show clocked at ~25 min
    while the actual render was 1 h+. Pin the contract — a Cut whose
    video segments sum to ~50 min must budget that, not the pre-fix
    0 or the source-item bleed-through."""
    # 35 clips averaging ~85 s each = ~2_975 s = ~49.6 min. Per-photo
    # 6 s × 0 photos = 0 s. Total ≈ 49.6 min.
    clip_ms = [85_000 + (i * 113) % 2_000 for i in range(35)]   # 85–87 s
    expected_total = sum(clip_ms)
    store = EventStore.create(tmp_path / "event.db", event_id="evt-l")
    store.save_document(_build_doc(clip_ms))
    counter = itertools.count(1)
    gw = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    try:
        # Drop the 2 photos to focus the regression on clip ms.
        gw.store.conn.execute(
            "DELETE FROM cut_member "
            "WHERE export_relpath IN ('Exported Media/000_p1.jpg', "
            "                         'Exported Media/001_p2.jpg')")
        totals = gw.cut_show_totals("cut-b")
        assert totals.video_count == 35
        assert totals.video_ms_total == expected_total
        # The pre-fix budget for un-probed sources was 0 ms; this
        # contract pins the new floor at "well above 25 min".
        assert totals.video_ms_total > 25 * 60 * 1000
    finally:
        gw.close()


# --------------------------------------------------------------------- #
# 3. CutSession.totals() matches the gateway path
# --------------------------------------------------------------------- #


def test_session_totals_match_gateway_totals(gw_three_clips):
    """spec/144 — ``CutSession.totals()`` sums the SessionFile's
    ``duration_ms`` (which now reads from lineage); the two paths
    must agree so the dialog's live probe + the gateway's read both
    show the same number."""
    cut = gw_three_clips.cut("cut-b")
    session = CutSession.for_cut(gw_three_clips, cut)
    session_totals = session.totals()
    gateway_totals = gw_three_clips.cut_show_totals(cut.id)
    assert session_totals.video_ms_total == gateway_totals.video_ms_total
    assert session_totals.video_ms_total == 18_000 + 22_000 + 15_000
