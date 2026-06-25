"""spec/146 — bulk "set export speed for all video clips".

The single per-clip Video-Editor speed dropdown writes the same value
to both the preview speed AND the baked export speed
(``VideoAdjustment.speed`` → ffmpeg ``setpts`` filter). Setting an
event with ~50 clips one clip at a time is the pain spec/146 fixes —
add ONE event-level action that normalises every clip's export speed
in one transaction.

Five contracts:

* The bulk action writes ``VideoAdjustment.speed = X`` for every
  video item in the event, in ONE transaction.
* Items that had no ``VideoAdjustment`` row get a default one with
  ``speed = X`` (mirrors the per-clip handler at editor_page.py:2636).
* Photos / non-video items are untouched (no Adjustment row written,
  no VideoAdjustment fabricated).
* Segment items born from :meth:`ensure_video_segments` are caught —
  exports walk segments, so the segment's ``VideoAdjustment`` is what
  matters.
* The value flows into :func:`core.video_export.build_export_plan` →
  ``plan.speed`` (the ``setpts=PTS/speed`` input). The returned count
  matches the number of video items touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.video_export import build_export_plan
from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.exported.batch import _SegmentOverride

NOW = "2026-06-25T00:00:00+00:00"


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #


def _photo(item_id: str, *, day: int = 1, classification=None) -> m.Item:
    return m.Item(
        id=item_id, kind="photo", created_at=NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.jpg",
        sha256=f"s-{item_id}", byte_size=1,
        materialized_at=NOW, materialized_phase="ingest",
        camera_id="cam", day_number=day,
        capture_time_raw="2026-06-25T08:00:00",
        capture_time_corrected="2026-06-25T08:00:00",
        classification=classification,
    )


def _video(item_id: str, *, duration_ms: int = 6_000,
           day: int = 1) -> m.Item:
    return m.Item(
        id=item_id, kind="video", created_at=NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.mp4",
        sha256=f"s-{item_id}", byte_size=1_000,
        materialized_at=NOW, materialized_phase="ingest",
        camera_id="cam", day_number=day,
        capture_time_raw="2026-06-25T09:00:00",
        capture_time_corrected="2026-06-25T09:00:00",
        duration_ms=duration_ms,
    )


def _build_event(store: EventStore, *, n_videos: int, n_photos: int):
    """Seed the event with the requested mix of video + photo items.

    Each source video gets exactly one segment via
    :meth:`ensure_video_segments` (the workshop's lazy birth) so the
    bulk action touches BOTH the source item AND the segment item —
    matching what the production codebase sees after the user opens
    the workshop on each clip."""
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-bs", name="Bulk speed",
        created_at=NOW, updated_at=NOW)))
    store.upsert(m.TripDay(day_number=1, date="2026-06-25"))
    store.upsert(m.Camera(camera_id="cam"))
    for i in range(n_videos):
        store.upsert(_video(f"v{i}"))
    for i in range(n_photos):
        store.upsert(_photo(f"p{i}"))


@pytest.fixture
def gw(tmp_path):
    """A gateway over an event with 3 source videos + 2 photos. Each
    video gets exactly one segment via ``ensure_video_segments`` so
    the bulk action targets the items the export pipeline actually
    reads (segment items, via ``video_adjustment(seg.item_id)``)."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-bs")
    _build_event(store, n_videos=3, n_photos=2)
    g = EventGateway(store, event_root=tmp_path, now=lambda: NOW)
    for vid in ("v0", "v1", "v2"):
        g.ensure_video_segments(vid)
    yield g
    g.close()


# --------------------------------------------------------------------- #
# 1. The bulk action sets the speed everywhere it matters
# --------------------------------------------------------------------- #


def test_bulk_set_speed_writes_to_every_video_item(gw):
    """spec/146 — every kind=video item (source AND its derived
    segment) gets ``VideoAdjustment.speed = X``. Source-item rows are
    harmless noise (the export walker reads by segment.item_id), but
    writing them keeps the contract "every video item" honest."""
    video_items = gw.items(kind="video", include_hidden=True)
    n_video_items = len(video_items)
    # Sanity: 3 sources + 3 segments (one per source).
    assert n_video_items == 6

    n = gw.bulk_set_video_speed(1.5)
    assert n == n_video_items

    for item in video_items:
        vadj = gw.video_adjustment(item.id)
        assert vadj is not None, item.id
        assert vadj.speed == 1.5


def test_bulk_set_speed_returns_zero_when_no_videos(tmp_path):
    """An event with only photos: nothing is touched, the count is 0.
    Pin the no-op contract so a future caller can rely on ``n > 0``
    to decide whether to surface a confirm dialog."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-bs2")
    _build_event(store, n_videos=0, n_photos=3)
    g = EventGateway(store, event_root=tmp_path, now=lambda: NOW)
    try:
        assert g.bulk_set_video_speed(1.0) == 0
    finally:
        g.close()


# --------------------------------------------------------------------- #
# 2. Items lacking a VideoAdjustment get a default one with speed=X
# --------------------------------------------------------------------- #


def test_bulk_set_speed_creates_default_row_for_items_without_one(gw):
    """spec/146 — mirrors the per-clip handler in editor_page.py:2636.
    Items that never had a ``VideoAdjustment`` row land one keyed by
    their item id with ``speed = X`` (everything else stays at the
    dataclass default)."""
    for item in gw.items(kind="video", include_hidden=True):
        assert gw.video_adjustment(item.id) is None

    gw.bulk_set_video_speed(0.75)

    for item in gw.items(kind="video", include_hidden=True):
        vadj = gw.video_adjustment(item.id)
        assert vadj is not None
        assert vadj.speed == 0.75
        # Default-row pin — every other field at the dataclass baseline.
        assert vadj.look == "natural"
        assert vadj.include_audio is True
        assert vadj.audio_volume == 1.0
        assert vadj.stabilise == 0.0


def test_bulk_set_speed_preserves_other_fields_on_existing_rows(gw):
    """spec/146 — when a clip already has a VideoAdjustment (Look,
    crop, stabilise, audio fade, etc.), the bulk action only flips
    speed. Pin every other field so the bulk write doesn't reset
    work the user already did."""
    seg = gw.segment_items("v0")[0]
    pre = m.VideoAdjustment(
        item_id=seg.id,
        look="brighten", creative_filter="warm",
        crop_x=0.1, crop_y=0.1, crop_w=0.8, crop_h=0.8,
        box_angle=2.5, aspect_ratio_label="16:9",
        style="wildlife", rep_frame_ms=1500,
        include_audio=False, rotation_degrees=90,
        audio_volume=0.5, audio_fade_ms=300,
        speed=1.0, stabilise=0.3,
    )
    gw.save_video_adjustment(pre)

    gw.bulk_set_video_speed(2.0)

    post = gw.video_adjustment(seg.id)
    assert post is not None
    assert post.speed == 2.0                              # only this changes
    assert post.look == "brighten"
    assert post.creative_filter == "warm"
    assert post.crop_x == 0.1 and post.crop_h == 0.8
    assert post.box_angle == 2.5
    assert post.aspect_ratio_label == "16:9"
    assert post.style == "wildlife"
    assert post.rep_frame_ms == 1500
    assert post.include_audio is False
    assert post.rotation_degrees == 90
    assert post.audio_volume == 0.5
    assert post.audio_fade_ms == 300
    assert post.stabilise == 0.3


# --------------------------------------------------------------------- #
# 3. Non-video items are untouched
# --------------------------------------------------------------------- #


def test_bulk_set_speed_does_not_touch_photo_items(gw):
    """A photo item must not gain a VideoAdjustment row (or any other
    side effect) when the bulk action runs. The writer filters on
    ``kind == 'video'``; this test guards against a regression that
    drops the filter."""
    photo_ids = {p.id for p in gw.items(kind="photo", include_hidden=True)}
    assert photo_ids == {"p0", "p1"}

    gw.bulk_set_video_speed(1.5)

    for photo_id in photo_ids:
        # No VideoAdjustment row was fabricated for a photo.
        assert gw.video_adjustment(photo_id) is None
        # And the photo's own Adjustment row (if any) is untouched.
        assert gw.adjustment(photo_id) is None


# --------------------------------------------------------------------- #
# 4. The value flows into the export plan (setpts)
# --------------------------------------------------------------------- #


def test_bulk_set_speed_flows_into_export_plan(gw):
    """spec/146 — after the bulk write, building an ExportPlan for any
    segment (via the canonical ``_SegmentOverride`` shim the spec/60
    batch uses) lands ``plan.speed = X``. ``video_export_run`` then
    emits ``setpts=PTS/X`` and the audio ``atempo`` chain — the
    on-disk clip plays at X×."""
    gw.bulk_set_video_speed(1.25)
    seg = gw.segment_items("v0")[0]
    vadj = gw.video_adjustment(seg.id)
    assert vadj is not None

    override = _SegmentOverride(vadj, params=None)
    plan = build_export_plan(
        override, clip_start_ms=0, clip_end_ms=6_000, src_fps=30.0)
    assert plan.speed == 1.25


@pytest.mark.parametrize("speed", [0.5, 0.75, 1.0, 1.25, 1.5, 2.0])
def test_bulk_set_speed_supports_every_dropdown_value(gw, speed):
    """The six dropdown values land in the export plan verbatim. Pin
    the contract for the whole submenu so a change to the supported
    range surfaces here first."""
    gw.bulk_set_video_speed(speed)
    seg = gw.segment_items("v1")[0]
    vadj = gw.video_adjustment(seg.id)
    assert vadj is not None
    override = _SegmentOverride(vadj, params=None)
    plan = build_export_plan(
        override, clip_start_ms=0, clip_end_ms=4_000, src_fps=30.0)
    assert plan.speed == speed


# --------------------------------------------------------------------- #
# 5. Guard rails — invalid input rejected
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [0, -1, -0.5, "1.5", None])
def test_bulk_set_speed_rejects_non_positive_input(gw, bad):
    """The dropdown only emits 0.5–2.0; a non-positive value would
    produce a degenerate ``setpts=PTS/0`` filter (ffmpeg refuses) or
    a reversed clip. The gateway rejects the call instead of writing
    a broken row."""
    with pytest.raises(ValueError):
        gw.bulk_set_video_speed(bad)
    # No row was fabricated as a side effect.
    for item in gw.items(kind="video", include_hidden=True):
        assert gw.video_adjustment(item.id) is None


# --------------------------------------------------------------------- #
# 6. One transaction (the spec's "in one transaction" promise)
# --------------------------------------------------------------------- #


def test_bulk_set_speed_uses_one_transaction(gw, monkeypatch):
    """spec/146 — the writes ride ONE transaction. Pin this with the
    ``transaction`` context-manager count: 50 clips → 1 BEGIN, not 50.
    A regression that loops with per-item commits would surface here
    as ``calls > 1``."""
    calls = {"n": 0}
    real_transaction = gw.store.transaction

    def _tracking_transaction(*a, **kw):
        calls["n"] += 1
        return real_transaction(*a, **kw)

    monkeypatch.setattr(gw.store, "transaction", _tracking_transaction)
    gw.bulk_set_video_speed(1.25)
    assert calls["n"] == 1, (
        "spec/146 — the bulk write must use exactly one transaction; "
        f"got {calls['n']}"
    )
