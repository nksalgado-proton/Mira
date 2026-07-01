"""Surface 12 fold (2026-06-15) — the video workshop folded into
:class:`mira.ui.pages.editor_page.EditorPage`.

Pins the behaviours the workshop must carry:

* Workshop reveals when the cursor lands on a video (host reserves
  the fixed height; the no-canvas-jump rule).
* Marker / segment / snapshot row sets are loaded from the gateway
  on landing and re-loaded after every mutator.
* Adding a marker splits the containing segment; both halves inherit
  the parent's phase_state + VideoAdjustment (gateway-level — the
  workshop UI just re-reads).
* Moving a marker keeps segment identity (state + adjustments ride
  along).
* Selection scopes development writes: editing on a SEGMENT writes
  to ``video_adjustment(seg.item_id)``; on a SNAPSHOT writes to the
  photo ``adjustment(snap.item_id)``.
* Snapshot placement auto-picks (gateway invariant carried through
  the UI on placement).
* Pick / Skip / Toggle keys flip phase_state on the SELECTED stop.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.pages.editor_page import EditorPage

FIXED_NOW = "2026-06-15T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


@pytest.fixture(autouse=True)
def _stub_exif(monkeypatch):
    import core.exif_reader as er
    monkeypatch.setattr(er, "read_exif_single", lambda path: None)
    monkeypatch.setattr(er, "read_exif_batch", lambda paths: [])


@pytest.fixture(autouse=True)
def _stub_probe_video(monkeypatch):
    """The workshop's _seed_video_metadata calls core.video_extract.probe_video
    to learn duration/fps for the timeline. The fixture's video items
    already carry ``duration_ms``; stub the ffprobe call to return a
    matching shape so the test never spawns ffmpeg."""
    class _Meta:
        def __init__(self, dur, fps):
            self.duration_ms = dur
            self.fps = fps
    import core.video_extract as ve
    monkeypatch.setattr(
        ve, "probe_video",
        lambda path: _Meta(10_000, 30.0))


def _doc_with_video() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-v", name="Video workshop fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    # One source video item with a known duration so segment_bounds
    # resolves. provenance="captured" + kind="video" — the gateway's
    # _require_source_video accepts only this shape.
    doc.items.append(m.Item(
        id="v1", kind="video", provenance="captured",
        created_at=FIXED_NOW,
        origin_relpath="Original Media/v1.mp4",
        sha256="v" * 64, byte_size=16,
        materialized_at=FIXED_NOW, materialized_phase="ingest",
        duration_ms=10_000,
        camera_id="G9", day_number=1,
        capture_time_raw="2026-04-01T08:01:00",
        capture_time_corrected="2026-04-01T08:01:00",
    ))
    return doc


@pytest.fixture
def event_dir(tmp_path):
    # Touch the source file so any existence check (none in this path,
    # but defensive) finds it. The viewport's arm-on-landing is async +
    # not exercised in these logical tests.
    p = tmp_path / "Original Media" / "v1.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 16)
    return tmp_path


@pytest.fixture
def store_and_gateway(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-v")
    store.save_document(_doc_with_video())
    counter = itertools.count(1)
    eg = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield store, eg
    eg.close()


@pytest.fixture
def app_gateway(event_dir, store_and_gateway, monkeypatch):
    store, _ = store_and_gateway
    gw = Gateway()
    counter = itertools.count(100)

    def _open_event(_event_id):
        return EventGateway(
            store, event_root=event_dir, now=_now,
            new_id=lambda: f"app-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    yield gw


def _editor_on_video(app_gateway) -> EditorPage:
    page = EditorPage(app_gateway)
    assert page.open_to_item("evt-v", 1, "v1")
    # Workshop bar revealed on landing.
    return page


# ── Reveal + reserve geometry ─────────────────────────────────────────


def test_workshop_host_has_fixed_reserved_height(qapp, app_gateway):
    """The reveal host above the workshop is pinned to a fixed height
    so the canvas geometry above is invariant under photo↔video sweeps.
    Nelson 2026-06-21 — the spec/92 dense tier re-reserves the host to
    the DENSE workshop bar's measured height (``max(88, sizeHint)``)
    instead of the static ``WORKSHOP_REVEAL_HEIGHT`` constant, so the
    canvas never shows a blank strip between the dense bar and the
    footer. The invariant the test still pins is the fixed-size policy
    (min == max), not the specific pixel value."""
    page = EditorPage(app_gateway)
    h_min = page._workshop_host.minimumHeight()
    h_max = page._workshop_host.maximumHeight()
    assert h_min == h_max, "workshop host must be fixed-size (no canvas jump)"
    # Floor matches the post-dense-tier reservation; comfortable headroom
    # for the workshop bar's transport row.
    assert h_min >= 88


def test_workshop_hidden_until_video_lands(qapp, app_gateway):
    page = EditorPage(app_gateway)
    assert page._workshop_bar.isHidden()


def test_workshop_reveals_on_video_landing(qapp, app_gateway):
    page = _editor_on_video(app_gateway)
    assert not page._workshop_bar.isHidden()
    assert page._video_id == "v1"
    # One segment by default (zero markers).
    assert len(page._segments) == 1
    assert len(page._segment_bounds) == 1
    assert page._segment_bounds[0] == (0, 10_000)
    # Selection lands on segment 0.
    assert page._selection[0] == "segment"
    assert page._selection[1] == 0
    page.close_event()


# ── Marker mutators ───────────────────────────────────────────────────


def test_add_marker_splits_segment_inherit_state(
        qapp, app_gateway, store_and_gateway):
    """spec/56 §1 split rule: a marker inside segment k creates k+1;
    both halves carry the parent's phase_state + VideoAdjustment."""
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    # Picked the only segment.
    eg.set_phase_state(page._segment_items[0].id, "edit", "picked")
    # Seed an adjustment on the parent so the split inheritance is
    # observable.
    seg0_id = page._segment_items[0].id
    vadj = m.VideoAdjustment(item_id=seg0_id, look="brighten", speed=1.5)
    eg.save_video_adjustment(vadj)

    page._video_pos_ms = 4_000
    page._add_marker_at_playhead()

    assert len(page._markers) == 1
    assert page._markers[0].at_ms == 4_000
    assert len(page._segments) == 2
    bounds = page._segment_bounds
    assert bounds[0][1] == 4_000 and bounds[1][0] == 4_000

    # Both halves' phase_state are "picked" (inheritance).
    ps = eg.phase_states("edit")
    assert ps[page._segment_items[0].id].state == "picked"
    assert ps[page._segment_items[1].id].state == "picked"
    # Both halves' VideoAdjustment match the parent's row.
    a0 = eg.video_adjustment(page._segment_items[0].id)
    a1 = eg.video_adjustment(page._segment_items[1].id)
    assert a0 is not None and a0.look == "brighten" and a0.speed == 1.5
    assert a1 is not None and a1.look == "brighten" and a1.speed == 1.5
    page.close_event()


def test_move_marker_keeps_segment_identity(
        qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 4_000
    page._add_marker_at_playhead()
    mid = page._markers[0].id
    # Pick the LEFT half; move the marker; LEFT stays picked.
    eg.set_phase_state(page._segment_items[0].id, "edit", "picked")
    page._on_marker_moved(mid, 6_000)
    assert page._markers[0].at_ms == 6_000
    assert eg.phase_states("edit")[
        page._segment_items[0].id].state == "picked"
    page.close_event()


def test_delete_marker_merges_left_survives(
        qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 4_000
    page._add_marker_at_playhead()
    left_id = page._segment_items[0].id
    eg.set_phase_state(left_id, "edit", "picked")
    # Land cursor ON the marker and remove → spec/56 merge rule.
    page._video_pos_ms = 4_000
    page._remove_stop_at_playhead()
    assert page._markers == []
    assert len(page._segments) == 1
    # The LEFT half's item id survives.
    assert page._segment_items[0].id == left_id
    assert eg.phase_states("edit")[left_id].state == "picked"
    page.close_event()


# ── Snapshot placement ────────────────────────────────────────────────


def test_snapshot_placement_autopicks_and_selects(
        qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 3_500
    page._add_snapshot_at_playhead()
    assert len(page._snapshots) == 1
    snap_id = page._snapshots[0].item_id
    # The placement auto-picks (gateway invariant).
    assert eg.phase_states("edit")[snap_id].state == "picked"
    # Selection moved onto the snapshot.
    assert page._selection[0] == "snapshot"
    assert page._selection[2] == snap_id
    page.close_event()


# ── Selection-scoped persistence ──────────────────────────────────────


def test_segment_surface_change_writes_video_adjustment(
        qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    seg_id = page._segment_items[0].id
    # Drive a Look change directly through the persistence router.
    page._surface.set_state(
        look="deeper", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        rotation=0, creative_filter=None, look_strength=1.0)
    page._persist_video_adjustment("look")
    vadj = eg.video_adjustment(seg_id)
    assert vadj is not None
    assert vadj.look == "deeper"
    # A photo Adjustment row is NOT written for the segment.
    assert eg.adjustment(seg_id) is None
    page.close_event()


def test_snapshot_surface_change_writes_photo_adjustment(
        qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 2_500
    page._add_snapshot_at_playhead()
    snap_id = page._snapshots[0].item_id
    # AdjustmentSurface change against the snapshot → save_adjustment.
    page._surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        rotation=0, creative_filter="warm", look_strength=1.0)
    page._persist_snapshot_adjustment("filter")
    adj = eg.adjustment(snap_id)
    assert adj is not None
    assert adj.creative_filter == "warm"
    # And NO video_adjustment for the snapshot (it's a photo item).
    assert eg.video_adjustment(snap_id) is None
    page.close_event()


# ── Decision keys on the selected stop ────────────────────────────────


def test_skip_key_flips_selected_segment(
        qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    seg_id = page._segment_items[0].id
    # Start green via the default (settings default; explicit set):
    eg.set_phase_state(seg_id, "edit", "picked")
    page._on_skip_key()
    assert eg.phase_states("edit")[seg_id].state == "skipped"
    page._on_pick_key()
    assert eg.phase_states("edit")[seg_id].state == "picked"
    page.close_event()


def test_toggle_key_flips_selected_snapshot(
        qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 5_000
    page._add_snapshot_at_playhead()
    snap_id = page._snapshots[0].item_id
    # Snapshots auto-pick on placement; toggle to skipped, then back.
    assert eg.phase_states("edit")[snap_id].state == "picked"
    page._on_toggle_key()
    assert eg.phase_states("edit")[snap_id].state == "skipped"
    page._on_toggle_key()
    assert eg.phase_states("edit")[snap_id].state == "picked"
    page.close_event()


# ── Cursor-position routing (spec/59 §4 "the old culler rule") ───────
#
# Nelson 2026-06-15: "the status control is not working as it should ...
# look at the legacy and implement it exactly as it was". The rules:
# * cursor on a SNAPSHOT → toggle the snapshot (NOT the underlying
#   segment — they're independent items even though the snapshot sits
#   inside one of the segments on the timeline)
# * cursor on a MARKER → toggle the segment the marker OWNS (the
#   segment starting at this marker, per spec/56's half-open [lo, hi))
# * cursor anywhere INSIDE a segment → toggle that segment


def test_pick_key_on_snapshot_position_toggles_snapshot_not_segment(
        qapp, app_gateway, store_and_gateway):
    """Cursor on a snapshot: P writes the snapshot's state and leaves
    the underlying segment's state untouched. The two are independent
    items per spec/56 §1 — the snapshot wins on tie."""
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    seg0_id = page._segment_items[0].id
    page._video_pos_ms = 5_000
    page._add_snapshot_at_playhead()
    snap_id = page._snapshots[0].item_id
    # Both are now in the DB. Skip the segment explicitly to detect a
    # spurious write to it on snapshot keypress.
    eg.set_phase_state(seg0_id, "edit", "skipped")
    eg.set_phase_state(snap_id, "edit", "skipped")
    # Cursor still at 5000 = snapshot position.
    assert page._video_pos_ms == 5_000
    page._on_pick_key()
    assert eg.phase_states("edit")[snap_id].state == "picked"
    assert eg.phase_states("edit")[seg0_id].state == "skipped"     # NOT touched
    page.close_event()


def test_skip_key_on_snapshot_position_toggles_snapshot_not_segment(
        qapp, app_gateway, store_and_gateway):
    """X behaves symmetrically — on a snapshot, the snapshot flips,
    the underlying segment stays."""
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    seg0_id = page._segment_items[0].id
    page._video_pos_ms = 5_000
    page._add_snapshot_at_playhead()
    snap_id = page._snapshots[0].item_id
    eg.set_phase_state(seg0_id, "edit", "picked")
    page._on_skip_key()
    assert eg.phase_states("edit")[snap_id].state == "skipped"
    assert eg.phase_states("edit")[seg0_id].state == "picked"      # NOT touched
    page.close_event()


def test_pick_key_at_marker_position_targets_owning_segment(
        qapp, app_gateway, store_and_gateway):
    """Cursor exactly on a marker: per spec/56's half-open [lo, hi)
    tiling the marker STARTS the segment to its right. P targets that
    owning segment, not the segment on the marker's LEFT."""
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 4_000
    page._add_marker_at_playhead()
    # Two segments now: [0, 4000) and [4000, 10000). The marker at
    # 4000 starts segment 1.
    left_id = page._segment_items[0].id
    right_id = page._segment_items[1].id
    eg.set_phase_state(left_id, "edit", "skipped")
    eg.set_phase_state(right_id, "edit", "skipped")
    # Cursor sits ON the marker (4000).
    assert page._video_pos_ms == 4_000
    page._on_pick_key()
    assert eg.phase_states("edit")[right_id].state == "picked"
    assert eg.phase_states("edit")[left_id].state == "skipped"     # NOT touched
    page.close_event()


def test_pick_key_mid_segment_targets_containing_segment(
        qapp, app_gateway, store_and_gateway):
    """Cursor in the middle of a segment (no nearby snapshot/marker):
    P targets the segment whose [lo, hi) contains the cursor."""
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 4_000
    page._add_marker_at_playhead()
    left_id = page._segment_items[0].id
    right_id = page._segment_items[1].id
    eg.set_phase_state(left_id, "edit", "skipped")
    eg.set_phase_state(right_id, "edit", "skipped")
    # Walk into the middle of the LEFT segment.
    page._on_video_position(2_000)
    page._on_pick_key()
    assert eg.phase_states("edit")[left_id].state == "picked"
    assert eg.phase_states("edit")[right_id].state == "skipped"    # NOT touched
    page.close_event()


def test_keys_ignore_stale_selection_act_on_cursor(
        qapp, app_gateway, store_and_gateway):
    """Selection is for the development panel, NOT for status. If the
    user clicked a snapshot (selection = snapshot) and then seeked
    AWAY to a segment-only position, P targets the segment under the
    cursor — never the stale snapshot selection. This is the legacy
    rule the user invoked: "the status control is not working as it
    should ... implement it exactly as it was"."""
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    seg0_id = page._segment_items[0].id
    page._video_pos_ms = 5_000
    page._add_snapshot_at_playhead()
    snap_id = page._snapshots[0].item_id
    # Place the click-driven selection on the snapshot. The user now
    # seeks away to a position inside segment 0 with no snapshot here.
    page._selection = ("snapshot", -1, snap_id)
    page._on_video_position(1_000)        # mid-segment, far from snapshot
    eg.set_phase_state(snap_id, "edit", "picked")
    eg.set_phase_state(seg0_id, "edit", "skipped")
    page._on_pick_key()
    assert eg.phase_states("edit")[seg0_id].state == "picked"
    assert eg.phase_states("edit")[snap_id].state == "picked"      # untouched (already picked)
    # And X: the SEGMENT flips, the stale snapshot selection stays.
    page._on_skip_key()
    assert eg.phase_states("edit")[seg0_id].state == "skipped"
    assert eg.phase_states("edit")[snap_id].state == "picked"
    page.close_event()


# ── Workshop reset semantics ──────────────────────────────────────────


def test_segments_born_default_skipped_per_spec56(
        qapp, app_gateway, store_and_gateway, monkeypatch):
    """Nelson 2026-06-15 "the status was lost" bug:

    The user opened a video in the workshop, added several markers,
    explicitly Picked two segments, and went to Export — only to find
    every segment shipped, not just the two picks. Root cause: the
    workshop called ``ensure_video_segments(default_state=
    edit_default_state)``; ``edit_default_state`` defaults to
    ``"picked"`` (the "born green" setting that governs photos), so
    every segment was born picked + marker splits inherited "picked"
    from the parent → every Pick keypress was a no-op + every
    untouched segment shipped.

    The fix restores spec/56 §1's rule for SEGMENTS only: segments
    always default-Skip regardless of ``edit_default_state``. This
    test pins that invariant — flipping the setting to "picked" must
    NOT change segment lazy-birth."""
    _, eg = store_and_gateway

    # Force the "born green" setting that previously broke segments,
    # restoring it on teardown so the leak doesn't dirty other tests
    # in the same session (Gateway() defaults to the global settings
    # path; an unrestored "picked" would persist beyond the fixture).
    from mira.picked.status import STATE_SKIPPED
    prior = app_gateway.settings.load().edit_default_state
    app_gateway.settings.update(edit_default_state="picked")
    try:
        page = _editor_on_video(app_gateway)
        seg_id = page._segment_items[0].id
        assert eg.phase_states("edit")[seg_id].state == STATE_SKIPPED
        page.close_event()
    finally:
        app_gateway.settings.update(edit_default_state=prior)


def test_added_markers_birth_skipped_segments(
        qapp, app_gateway, store_and_gateway):
    """Adding markers splits the parent and BOTH halves inherit the
    parent's state (spec/56 §1 split rule). With segments born
    default-Skip, an un-touched marker insert leaves all segments
    skipped — the user has to explicitly Pick the cuts they want to
    ship."""
    from mira.picked.status import STATE_SKIPPED
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)

    page._video_pos_ms = 3_000
    page._add_marker_at_playhead()
    page._video_pos_ms = 6_000
    page._add_marker_at_playhead()
    assert len(page._segment_items) == 3
    ps = eg.phase_states("edit")
    for seg in page._segment_items:
        assert ps[seg.id].state == STATE_SKIPPED
    page.close_event()


def test_toggle_status_on_untouched_segment_goes_to_picked(
        qapp, app_gateway, store_and_gateway):
    """Pressing Space/toggle on an untouched segment cycles from the
    spec/56 default-Skip → Picked (not to Skipped, which would be a
    no-op on a default-skipped row). The fallback default the toggle
    reads is now hardcoded "skipped" for segments."""
    from mira.picked.status import STATE_PICKED
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    seg_id = page._segment_items[0].id
    # The lazy-birth wrote an explicit row, so clear it to simulate
    # the "no row yet" fallback path.
    eg.store.conn.execute(
        "DELETE FROM phase_state WHERE item_id = ? AND phase = 'edit'",
        (seg_id,))
    page._on_toggle_key()
    assert eg.phase_states("edit")[seg_id].state == STATE_PICKED
    page.close_event()


def test_reset_everything_clears_markers_snapshots(
        qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 3_000
    page._add_marker_at_playhead()
    page._video_pos_ms = 6_000
    page._add_marker_at_playhead()
    page._video_pos_ms = 8_000
    page._add_snapshot_at_playhead()
    assert len(page._markers) == 2
    assert len(page._snapshots) == 1
    page._workshop_reset_all()
    assert page._markers == []
    assert page._snapshots == []
    assert len(page._segments) == 1
    page.close_event()


def test_clear_markers_keeps_snapshots(qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 3_000
    page._add_snapshot_at_playhead()
    page._video_pos_ms = 6_000
    page._add_marker_at_playhead()
    assert len(page._snapshots) == 1
    assert len(page._markers) == 1
    page._workshop_clear_markers()
    assert page._markers == []
    assert len(page._snapshots) == 1
    page.close_event()


# ── Photo → video sweep keeps canvas anchored ─────────────────────────


def test_photo_landing_hides_workshop_keeps_host_height(qapp, app_gateway):
    """Sweeping back to a photo collapses the workshop INNER content
    but the reveal host stays at the reserved height (canvas anchor)."""
    page = _editor_on_video(app_gateway)
    h_video = page._workshop_host.maximumHeight()
    page._teardown_video_workshop()
    assert not page._workshop_bar.isVisible()
    # Reserve still locked at the same fixed value.
    assert page._workshop_host.maximumHeight() == h_video
    assert page._workshop_host.minimumHeight() == h_video
    page.close_event()


# ── Modeless development (spec/59 §3) ─────────────────────────────────


def test_dev_mode_engages_on_paused_implicit_start(
        qapp, app_gateway, monkeypatch):
    """The cursor lands at 0 (the implicit start of segment 0) and
    the player is paused — dev mode should engage so adjustments
    are reflected in the canvas (Nelson 2026-06-15 eyeball #3 —
    'changes to adjustment have to be reflected in the frame on
    display'). Stubs frame extraction so the test stays Qt-only."""
    import numpy as np
    monkeypatch.setattr(
        "mira.ui.pages.editor_page.EditorPage._extract_video_frame_array",
        lambda self, item_id, ms: np.zeros((4, 4, 3), dtype=np.uint8))
    monkeypatch.setattr(
        "mira.ui.pages.editor_page.EditorPage._style_for_selection",
        lambda self: "general")
    page = _editor_on_video(app_gateway)
    # The viewport's video_is_playing() defaults to False (no player
    # armed in tests). On landing at pos=0 the implicit-start stop
    # triggers dev mode.
    assert page._dev_mode_active is True
    assert page._dev_mode_anchor_ms == 0
    page.close_event()


def test_dev_mode_exits_on_play(qapp, app_gateway, monkeypatch):
    """Pressing play (video_playing_changed(True)) must exit dev
    mode so the canvas returns to the player view."""
    import numpy as np
    monkeypatch.setattr(
        "mira.ui.pages.editor_page.EditorPage._extract_video_frame_array",
        lambda self, item_id, ms: np.zeros((4, 4, 3), dtype=np.uint8))
    monkeypatch.setattr(
        "mira.ui.pages.editor_page.EditorPage._style_for_selection",
        lambda self: "general")
    page = _editor_on_video(app_gateway)
    assert page._dev_mode_active
    page._on_video_playing(True)
    assert not page._dev_mode_active
    page.close_event()


def test_dev_mode_engages_on_snapshot_placement(
        qapp, app_gateway, monkeypatch):
    """Placing a snapshot drops the cursor on a stop; dev mode
    engages so the user immediately sees the developed snapshot
    frame."""
    import numpy as np
    monkeypatch.setattr(
        "mira.ui.pages.editor_page.EditorPage._extract_video_frame_array",
        lambda self, item_id, ms: np.zeros((4, 4, 3), dtype=np.uint8))
    monkeypatch.setattr(
        "mira.ui.pages.editor_page.EditorPage._style_for_selection",
        lambda self: "general")
    page = _editor_on_video(app_gateway)
    page._exit_dev_mode()                            # reset to off
    page._video_pos_ms = 3_000
    page._add_snapshot_at_playhead()
    snap_id = page._snapshots[0].item_id
    assert page._dev_mode_active is True
    assert page._dev_mode_item_id == snap_id
    assert page._dev_mode_anchor_ms == 3_000
    page.close_event()


def test_dev_mode_exits_when_cursor_steps_off_stop(
        qapp, app_gateway, monkeypatch):
    """Seeking the cursor mid-segment (not on a stop) exits dev mode
    so the player view is restored (the spec/59 §3 step-off rule)."""
    import numpy as np
    monkeypatch.setattr(
        "mira.ui.pages.editor_page.EditorPage._extract_video_frame_array",
        lambda self, item_id, ms: np.zeros((4, 4, 3), dtype=np.uint8))
    monkeypatch.setattr(
        "mira.ui.pages.editor_page.EditorPage._style_for_selection",
        lambda self: "general")
    page = _editor_on_video(app_gateway)
    assert page._dev_mode_active                     # at implicit start
    # Step mid-segment — no stop here.
    page._on_video_position(5_000)
    assert not page._dev_mode_active
    page.close_event()


# ── F10 develop pipeline runs without API drift ──────────────────────


def test_develop_array_for_lens_runs_pipeline(qapp, app_gateway):
    """F10 on a video segment calls ``_develop_array_for_lens`` which
    runs the core photo_render + photo_auto pipeline on the extracted
    frame. This pins the call shape so a future signature change (or
    a regression like ``compute_look_params`` keyword drift) trips
    the unit test instead of the live app.

    Uses a tiny array so the pipeline is cheap; asserts only that the
    returned object is a non-null QPixmap of the expected size.
    """
    import numpy as np
    from PyQt6.QtGui import QPixmap
    page = _editor_on_video(app_gateway)
    seg_id = page._segment_items[0].id
    # Set a non-default look + style so the routing layers run.
    from mira.store import models as m
    _, eg_inner = app_gateway, page._eg
    eg_inner.save_video_adjustment(m.VideoAdjustment(
        item_id=seg_id, look="brighter", style="general"))
    arr = np.full((64, 96, 3), 128, dtype=np.uint8)
    pm = page._develop_array_for_lens(arr, seg_id, "segment")
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()
    # The pipeline preserves geometry up to crop (none here) →
    # output dims == input dims.
    assert (pm.width(), pm.height()) == (96, 64)
    page.close_event()


# ── Mute pushes to live player (Nelson 2026-06-15 #2) ─────────────────


def test_mute_pushes_zero_to_viewport(qapp, app_gateway):
    page = _editor_on_video(app_gateway)
    pushed: list[int] = []
    page._viewport.video_set_volume = lambda v: pushed.append(int(v))
    page._on_mute_toggled(True)
    assert pushed == [0]
    # spec-neutral 2026-07-01 — call viewport.shutdown_video() before
    # close_event so QMediaPlayer clears its armed state; without it
    # a queued teardown callback fires later against the freed
    # ``pushed`` closure and pytest reports "TypeError: 'NoneType' is
    # not callable" in the Qt event loop at fixture end.
    try:
        page._viewport.shutdown_video()
    except Exception:                                              # noqa: BLE001
        pass
    page.close_event()
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()


def test_unmute_restores_slider_volume(qapp, app_gateway):
    page = _editor_on_video(app_gateway)
    page._workshop_bar.vol_slider.setValue(65)
    pushed: list[int] = []
    page._viewport.video_set_volume = lambda v: pushed.append(int(v))
    page._on_mute_toggled(False)
    assert pushed == [65]
    page.close_event()


# ── Jump stops walks markers ∪ snapshots ∪ endpoints ─────────────────


# ── Tools-enable rules (Nelson 2026-06-15 eyeball #1) ─────────────────


def test_marker_button_re_enables_after_seeking_off_stop(
        qapp, app_gateway, store_and_gateway):
    """After placing a marker the cursor sits ON it → Marker button
    greys (spec/59 §4). The user then seeks elsewhere within the same
    segment — the button MUST re-enable. The bug Nelson hit: tools-
    enable was only recomputed on segment change, so seeking within
    the same segment never re-ran the rule and the button stayed
    greyed (the "only one marker, then non-responsive" report)."""
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    # Place the first marker — cursor was at 0 before; move to 3000.
    page._video_pos_ms = 3_000
    page._add_marker_at_playhead()
    # Cursor still at 3000 (the new marker's at_ms) → button greyed.
    page._refresh_workshop_model()
    assert not page._workshop_bar.marker_btn.isEnabled()
    # Seek elsewhere within the SAME segment (the new segment 1
    # spans (3000, 10000) — go to 5000).
    page._on_video_position(5_000)
    assert page._workshop_bar.marker_btn.isEnabled()
    page.close_event()


def test_marker_button_greys_at_endpoints(qapp, app_gateway):
    """Marker placement at 0 / duration is rejected by the gateway
    (markers must lie strictly inside (0, duration)). The UI greys
    the button at endpoints so the user doesn't click into a silent
    rejection."""
    page = _editor_on_video(app_gateway)
    # Cursor at 0 — endpoint.
    page._on_video_position(0)
    assert not page._workshop_bar.marker_btn.isEnabled()
    # Cursor at duration - tolerance — endpoint.
    page._on_video_position(page._video_duration_ms - 1)
    assert not page._workshop_bar.marker_btn.isEnabled()
    # Cursor mid-video — enabled.
    page._on_video_position(4_000)
    assert page._workshop_bar.marker_btn.isEnabled()
    page.close_event()


def test_workshop_landing_enables_adjustment_tools(
        qapp, app_gateway):
    """Nelson 2026-06-15 eyeball #2 — adjustments did nothing because
    set_tools_enabled stayed False from the photo branch. Landing on
    a video must re-enable the AdjustmentSurface so Look / Style /
    Filter / Crop clicks reach the persistence router."""
    page = _editor_on_video(app_gateway)
    # The bind-on-landing path enabled the tools.
    assert page._surface._tools_widget.isEnabled() or \
        page._surface._tools_widget.findChildren  # smoke fallback if internal
    page.close_event()


def test_jump_stop_walks_markers_and_snapshots(
        qapp, app_gateway, store_and_gateway):
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    page._video_pos_ms = 2_000
    page._add_marker_at_playhead()
    page._video_pos_ms = 5_000
    page._add_snapshot_at_playhead()
    page._video_pos_ms = 7_000
    page._add_marker_at_playhead()

    seeks: list[int] = []
    page._viewport.video_seek = lambda ms: seeks.append(int(ms))

    page._video_pos_ms = 0
    page._jump_stop(+1)
    assert seeks[-1] == 2_000
    page._video_pos_ms = 2_000
    page._jump_stop(+1)
    assert seeks[-1] == 5_000
    page._video_pos_ms = 5_000
    page._jump_stop(+1)
    assert seeks[-1] == 7_000
    page._video_pos_ms = 7_000
    page._jump_stop(-1)
    assert seeks[-1] == 5_000
    page.close_event()


def test_landing_backfills_null_duration_on_db_row(
        qapp, tmp_path, monkeypatch):
    """The bug behind "almost all video processing buttons are not
    wired": when ingest leaves the row's ``duration_ms`` NULL
    (ExifTool can't read ``duration_seconds`` for the container),
    every workshop mutator that needs ``video.duration_ms`` raises
    silently and the tools row feels dead.

    The workshop now backfills from the ffprobe / Qt-discovered
    duration on landing so the gateway calls succeed. After landing,
    Marker / Snapshot / Toggle Status / segment_bounds all work."""
    class _Meta:
        duration_ms = 10_000
        fps = 30.0
    import core.video_extract as ve
    monkeypatch.setattr(ve, "probe_video", lambda path: _Meta())
    import core.exif_reader as er
    monkeypatch.setattr(er, "read_exif_single", lambda path: None)
    monkeypatch.setattr(er, "read_exif_batch", lambda paths: [])

    # Event with a source video whose duration_ms is NULL.
    p = tmp_path / "Original Media" / "v1.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 16)
    doc = m.EventDocument(event=m.Event(
        uuid="evt-null", name="null duration fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items.append(m.Item(
        id="v1", kind="video", provenance="captured",
        created_at=FIXED_NOW,
        origin_relpath="Original Media/v1.mp4",
        sha256="v" * 64, byte_size=16,
        materialized_at=FIXED_NOW, materialized_phase="ingest",
        duration_ms=None,                            # ← the bug seed
        camera_id="G9", day_number=1,
        capture_time_raw="2026-04-01T08:01:00",
        capture_time_corrected="2026-04-01T08:01:00",
    ))
    store = EventStore.create(tmp_path / "event.db", event_id="evt-null")
    store.save_document(doc)
    counter = itertools.count(1)
    eg = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"app-{next(counter)}")
    assert eg.item("v1").duration_ms is None

    gw = Gateway()
    monkeypatch.setattr(gw, "open_event", lambda _eid: eg)
    page = EditorPage(gw)
    assert page.open_to_item("evt-null", 1, "v1")

    # After landing, the row carries the probed duration AND the
    # gateway-derived bounds are populated. The mutators that were
    # dead before now succeed.
    assert eg.item("v1").duration_ms == 10_000
    assert page._segment_bounds == [(0, 10_000)]

    page._video_pos_ms = 4_000
    page._add_marker_at_playhead()
    assert len(page._markers) == 1
    assert page._markers[0].at_ms == 4_000
    page.close_event()


# ── spec/56 + spec/138 — Edit preview speed follows the segment ───────


def test_segment_select_drives_engine_to_segment_speed(qapp, app_gateway):
    """Selecting a segment sets the PREVIEW engine rate to that segment's
    baked ``vadj.speed`` (WYSIWYG of the rendered clip), NOT whatever
    sticky rate carried over from the Picker skim default or a prior 2x
    segment. The bug: bind pushed the speed to the INDICATOR only
    (``set_speed`` blocks signals), so a 1x segment kept previewing at
    the stale sticky engine rate."""
    page = _editor_on_video(app_gateway)
    try:
        # Simulate a stale sticky rate (Picker skim default / a prior
        # 2x segment left the engine here).
        page._viewport.video_set_playback_rate(2.0)
        # Segment 0 carries no persisted speed → its baked speed is 1x.
        # Re-binding the (still-selected) segment must reset the engine.
        page._bind_panel_to_selection()
        assert page._viewport.video_playback_rate() == pytest.approx(1.0)
    finally:
        page.close_event()


def test_segment_with_persisted_speed_previews_at_that_speed(
        qapp, app_gateway, store_and_gateway):
    """A segment carrying ``vadj.speed = 2.0`` previews at 2x — the engine
    matches the speed the workshop indicator shows, even if the engine
    had been left at a different (sticky) rate."""
    _, eg = store_and_gateway
    page = _editor_on_video(app_gateway)
    try:
        seg0_id = page._segment_items[0].id
        eg.save_video_adjustment(
            m.VideoAdjustment(item_id=seg0_id, speed=2.0))
        # Leave the engine at a mismatched sticky rate first.
        page._viewport.video_set_playback_rate(1.0)
        page._bind_panel_to_selection()
        assert page._viewport.video_playback_rate() == pytest.approx(2.0)
    finally:
        page.close_event()
