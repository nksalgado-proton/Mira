"""Tests for the video WORKSHOP — ``mira.ui.edited.edit_video_page``
(spec/56 data model + spec/59 surface: the cursor IS the selection).

The player + ffmpeg pipeline stay out of scope (``_probe_and_mount`` is
stubbed); these tests pin the workshop's data wires against a REAL
gateway: segment lazy-birth on load, cursor-scoped adjustment writes
(clip → VideoAdjustment, snapshot → photo Adjustment), the marker rules
riding the gateway ops, status writes, the Stop model, and the
navigation contract.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mira.gateway.event_gateway import EventGateway
from mira.picked import BucketStatus, CullBucket, CullItem
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.edited.edit_video_page import (
    EditVideoPage, mark_jump_target,
)

NOW = "2026-06-10T20:00:00+00:00"
DUR = 20000


def _make_eg(tmp_path) -> EventGateway:
    store = EventStore.create(tmp_path / "event.db", event_id="evt-ws")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-ws", name="WS", created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="GP12"))
    store.upsert(m.TripDay(day_number=1, date="2026-04-01"))
    store.upsert(m.Item(
        id="m1", kind="video", origin_relpath="d/m1.mp4",
        sha256="sha-m1", byte_size=2,
        materialized_at=NOW, materialized_phase="ingest",
        camera_id="GP12",
        capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00",
        duration_ms=DUR,
        created_at=NOW, day_number=1, provenance="captured",
    ))
    return EventGateway(store, event_root=tmp_path, now=lambda: NOW)


def _bucket(base: Path) -> CullBucket:
    items = (CullItem(
        item_id="m1", path=base / "d" / "m1.mp4", kind="video",
        capture_time_corrected="2026-04-01T08:00:00", duration_ms=DUR),)
    return CullBucket(
        bucket_key="1|video|m1", kind="video", title="m1", items=items,
        status=BucketStatus(
            total=1, kept=0, candidate=0, discarded=0, untouched=1,
            reviewed=False, browsed=False, badge="untouched"))


def _stub_probe(self, ci) -> None:
    """Player/ffprobe stand-in — duration from the fixture, player idle."""
    self._frame_ms = 33
    self._src_fps = 30.0
    self._duration_ms = DUR
    self._video_w = 1920
    self._video_h = 1080
    self._poster_shown = True


def _page(tmp_path, monkeypatch, eg) -> EditVideoPage:
    monkeypatch.setattr(EditVideoPage, "_probe_and_mount", _stub_probe)
    page = EditVideoPage()
    page.load(eg, _bucket(tmp_path))
    return page


# --------------------------------------------------------------------------- #
# Load — segment lazy-birth + selection
# --------------------------------------------------------------------------- #


def test_load_births_one_segment_and_targets_it(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        assert page._workshop_ready
        segs = eg.segment_items("m1")
        assert len(segs) == 1                      # marker-less = ONE segment
        target = page._adj_target_id()
        assert target == segs[0].id and target != "m1"
        # Born with the explicit edit/skipped row (spec/56 default-Skip).
        assert eg.phase_state(target, "edit").state == "skipped"
    finally:
        eg.close()


def test_persist_tool_writes_segment_adjustment(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        target = page._adj_target_id()
        page._persist_tool(speed=0.5, audio_volume=0.8)
        assert eg.video_adjustment("m1") is None   # never the source video
        adj = eg.video_adjustment(target)
        assert adj is not None
        assert adj.speed == 0.5 and adj.audio_volume == 0.8
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# Cut / move / remove-cut — the marker rules through the page handlers
# --------------------------------------------------------------------------- #


def test_cut_splits_and_both_halves_inherit(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        first = page._adj_target_id()
        eg.set_phase_state(first, "edit", "picked")
        eg.save_video_adjustment(m.VideoAdjustment(item_id=first, speed=2.0))
        page._reload_model(keep_selection=True)

        monkeypatch.setattr(EditVideoPage, "_playhead_ms", lambda self: 8000)
        page._on_cut()
        segs = eg.segment_items("m1")
        assert len(segs) == 2
        assert page._seg_bounds == [(0, 8000), (8000, DUR)]
        for seg in segs:                            # verbatim inheritance
            assert eg.phase_state(seg.id, "edit").state == "picked"
            adj = eg.video_adjustment(seg.id)
            assert adj is not None and adj.speed == 2.0
        # The cursor's containing clip is the one STARTING at the marker.
        assert page._segment_at(page._playhead_ms()) == 1
    finally:
        eg.close()


def test_marker_move_keeps_identity(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        monkeypatch.setattr(EditVideoPage, "_playhead_ms", lambda self: 8000)
        page._on_cut()
        left = page._segment_items[0].id
        eg.set_phase_state(left, "edit", "picked")
        page._reload_model(keep_selection=True)

        mk = page._markers[0]
        page._on_marker_moved(mk.id, 12000)
        assert page._seg_bounds == [(0, 12000), (12000, DUR)]
        assert page._segment_items[0].id == left          # identity = order
        assert page._seg_states[left] == "picked"          # state rides along
    finally:
        eg.close()


def test_remove_cut_merges_left_survives(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        monkeypatch.setattr(EditVideoPage, "_playhead_ms", lambda self: 8000)
        page._on_cut()
        left, right = (s.id for s in page._segment_items)
        eg.set_phase_state(left, "edit", "picked")
        page._reload_model(keep_selection=True)

        # The cursor sits at 8000 — exactly on the marker; Remove takes it.
        page._on_remove_at_playhead()
        segs = eg.segment_items("m1")
        assert [s.id for s in segs] == [left]              # LEFT survives
        assert eg.phase_state(left, "edit").state == "picked"
        assert eg.item(right) is None                      # right half gone
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# P/D — the same phase-state grammar as everything else
# --------------------------------------------------------------------------- #


def test_pd_keys_write_status_at_cursor(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        target = page._adj_target_id()
        page._set_status_at_cursor("picked")
        assert eg.phase_state(target, "edit").state == "picked"
        page._on_toggle_status()                           # flips back
        assert eg.phase_state(target, "edit").state == "skipped"
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# Snapshots — photo-shaped children, auto-picked, photo Adjustment writes
# --------------------------------------------------------------------------- #


def test_snapshot_places_autopicks_and_selects(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        monkeypatch.setattr(EditVideoPage, "_playhead_ms", lambda self: 5000)
        page._on_snapshot()
        snaps = eg.video_snapshots("m1")
        assert len(snaps) == 1 and snaps[0].at_ms == 5000
        sid = snaps[0].item_id
        assert eg.phase_state(sid, "edit").state == "picked"   # auto-Pick
        # The cursor sits at the new snapshot — it IS the target.
        assert page._adj_target_id() == sid
    finally:
        eg.close()


def test_snapshot_surface_save_writes_photo_adjustment(
    qapp, tmp_path, monkeypatch,
):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        monkeypatch.setattr(EditVideoPage, "_playhead_ms", lambda self: 5000)
        page._on_snapshot()
        sid = page._adj_target_id()
        page._surface.get_state = lambda: SimpleNamespace(
            params=None, crop_norm=(0.1, 0.2, 0.6, 0.5), box_angle=4.5,
            style="landscape", aspect_label="16:9", look="brighter",
            creative_filter="vivid", auto_on=True)
        page._save_surface_state_to_adjustment()
        assert eg.video_adjustment(sid) is None        # photo row, not video
        padj = eg.adjustment(sid)
        assert padj is not None
        assert padj.look == "brighter" and padj.creative_filter == "vivid"
        assert padj.style == "landscape"
        assert padj.crop_angle == 4.5 and padj.aspect_label == "16:9"
        assert padj.crop_x == 0.1 and padj.crop_w == 0.6
        assert padj.edit_exported is False
    finally:
        eg.close()


def test_segment_surface_save_writes_video_adjustment(
    qapp, tmp_path, monkeypatch,
):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        target = page._adj_target_id()
        page._pending_rep_ms = 1234
        page._surface.get_state = lambda: SimpleNamespace(
            params=None, crop_norm=(0.1, 0.2, 0.6, 0.5), box_angle=4.5,
            style="landscape", aspect_label="16:9", look="deeper",
            creative_filter=None, auto_on=True)
        page._save_surface_state_to_adjustment()
        adj = eg.video_adjustment(target)
        assert adj is not None
        assert adj.look == "deeper" and adj.style == "landscape"
        assert adj.rep_frame_ms == 1234
        assert adj.box_angle == 4.5 and adj.aspect_ratio_label == "16:9"
        assert adj.crop_x == 0.1 and adj.crop_w == 0.6
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# Navigation contract (unchanged from the pre-workshop page)
# --------------------------------------------------------------------------- #


def test_edge_emits_navigate_at_edge_in_day_grid(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        monkeypatch.setattr(EditVideoPage, "_probe_and_mount", _stub_probe)
        page = EditVideoPage()
        page.load(eg, _bucket(tmp_path), nav_context="day_grid")
        edges = []
        page.navigate_at_edge.connect(edges.append)
        page._emit_edge(+1)
        page._emit_edge(-1)
        assert edges == [+1, -1]
    finally:
        eg.close()


def test_edge_stops_in_cluster_context(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        monkeypatch.setattr(EditVideoPage, "_probe_and_mount", _stub_probe)
        page = EditVideoPage()
        page.load(eg, _bucket(tmp_path), nav_context="cluster")
        edges = []
        page.navigate_at_edge.connect(edges.append)
        page._emit_edge(+1)
        assert edges == []
    finally:
        eg.close()


def test_style_decision_routes_segment_to_video_snapshot_to_own_row(
        qapp, tmp_path, monkeypatch):
    """spec/58 §2 — picking a style while a SEGMENT is selected decides
    the SOURCE video's genre; while a SNAPSHOT is selected, its own row
    (inherited at creation) — the video's stays untouched."""
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        # Cursor mid-clip (no snapshot under it) → the video's row flips.
        page._on_style_decided("landscape")
        vid = eg.item("m1")
        assert vid.classification == "landscape"
        assert vid.classification_source == "user"
        # A snapshot under the cursor decides its own row.
        sid = eg.create_video_snapshot("m1", 1000, item_id="i-snap-r")
        page._reload_model(keep_selection=False)
        monkeypatch.setattr(page, "_playhead_ms", lambda: 1000)
        # Inherited at creation — the video's USER decision travelled.
        assert eg.item(sid).classification == "landscape"
        assert eg.item(sid).classification_source == "user"
        page._on_style_decided("portrait")
        assert eg.item(sid).classification == "portrait"
        assert eg.item(sid).classification_source == "user"
        assert eg.item("m1").classification == "landscape"
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# The Stop model + middle line (spec/59, 2026-06-11)
# --------------------------------------------------------------------------- #


def test_mark_jump_target_math():
    """◀/▶ Stop over the union of markers + snapshots + endpoints, one
    frame of tolerance so a repeat press leaves the current stop."""
    marks = [0, 4_000, 6_000, 9_000, 20_000]
    assert mark_jump_target(5_000, -1, marks, 33) == 4_000
    assert mark_jump_target(5_000, +1, marks, 33) == 6_000
    # Sitting ON a mark: the tolerance skips it both ways.
    assert mark_jump_target(4_000, -1, marks, 33) == 0
    assert mark_jump_target(4_000, +1, marks, 33) == 6_000
    # Nothing further that way → None.
    assert mark_jump_target(20, -1, marks, 33) is None
    assert mark_jump_target(19_990, +1, marks, 33) is None
    assert mark_jump_target(5_000, +1, [], 33) is None


def test_remove_at_playhead_routes_snapshot_then_marker(
        qapp, tmp_path, monkeypatch):
    """Remove takes the snapshot under the cursor first; a second press
    takes the marker at the same position."""
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        eg.add_video_marker("m1", 5_000)
        eg.create_video_snapshot("m1", 5_000, item_id="i-snap-rm")
        page._reload_model(keep_selection=False)
        monkeypatch.setattr(page, "_playhead_ms", lambda: 5_000)
        page._on_remove_at_playhead()
        assert eg.video_snapshots("m1") == []
        assert len(eg.video_markers("m1")) == 1
        page._on_remove_at_playhead()
        assert eg.video_markers("m1") == []
    finally:
        eg.close()


def test_clear_markers_only_keeps_snapshots(qapp, tmp_path, monkeypatch):
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        eg.add_video_marker("m1", 4_000)
        eg.add_video_marker("m1", 9_000)
        eg.create_video_snapshot("m1", 6_000, item_id="i-snap-k")
        page._reload_model(keep_selection=False)
        page._clear_markers(confirm=False)
        assert eg.video_markers("m1") == []
        assert len(eg.video_snapshots("m1")) == 1
        assert len(eg.segment_items("m1")) == 1
    finally:
        eg.close()


def test_jump_stop_walks_markers_and_snapshots(qapp, tmp_path, monkeypatch):
    """◀/▶ Stop walks the union — a snapshot is as much a stop as a
    marker; the cursor landing on it makes it the target."""
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        eg.add_video_marker("m1", 9_000)
        eg.create_video_snapshot("m1", 6_000, item_id="i-snap-j")
        page._reload_model(keep_selection=False)
        seeks = []
        monkeypatch.setattr(page, "_seek_to", seeks.append)
        monkeypatch.setattr(page, "_playhead_ms", lambda: 0)
        page._jump_stop(+1)
        monkeypatch.setattr(page, "_playhead_ms", lambda: 6_000)
        page._jump_stop(+1)
        assert seeks == [6_000, 9_000]
        # Landing on the snapshot makes it the cursor's target.
        assert page._adj_target_id() == "i-snap-j"
    finally:
        eg.close()


def test_visibility_hidden_off_stop_greyed_on_skipped(
        qapp, tmp_path, monkeypatch):
    """spec/59 §2.1: off-stop the top tools hide (space preserved);
    on a Skipped stop they show greyed; creators grey on any stop."""
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        tools = page._surface.tools_widget()
        # Load parks the cursor at 0 — the start marker of a SKIPPED
        # clip → greyed (visible, disabled); creators grey on a stop;
        # the endpoint is permanent so Remove greys too.
        assert tools.isHidden() is False
        assert not page._create_marker_btn.isEnabled()
        assert not page._remove_btn.isEnabled()
        # Mid-clip — no stop → hidden (space kept), creators live.
        monkeypatch.setattr(page, "_playhead_ms", lambda: 3_000)
        page._refresh_cursor_context()
        assert tools.isHidden() is True
        assert page._create_marker_btn.isEnabled()
        assert page._toggle_btn.isEnabled()          # works anywhere
    finally:
        eg.close()


def test_nav_dropdowns_list_markers_and_snapshots(
        qapp, tmp_path, monkeypatch):
    """Nelson 2026-06-11 eyeball: direct access scales via the NAV
    dropdowns (the chip strip died) — one item per stop, timestamped,
    greyed while empty."""
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        assert not page._markers_menu_btn.isEnabled()
        assert not page._snapshots_menu_btn.isEnabled()
        eg.add_video_marker("m1", 4_000)
        eg.create_video_snapshot("m1", 6_000, item_id="i-snap-m")
        page._reload_model(keep_selection=False)
        assert [a.text() for a in page._markers_menu.actions()] == [
            "▼ 0:04.000"]
        assert [a.text() for a in page._snapshots_menu.actions()] == [
            "📷 0:06.000"]
        assert page._markers_menu_btn.isEnabled()
        assert page._snapshots_menu_btn.isEnabled()
    finally:
        eg.close()


def test_reset_everything(qapp, tmp_path, monkeypatch):
    """Reset: markers + snapshots go, ONE clip survives at the default
    Skip; the survivor's development untouched."""
    eg = _make_eg(tmp_path)
    try:
        page = _page(tmp_path, monkeypatch, eg)
        eg.add_video_marker("m1", 4_000)
        eg.add_video_marker("m1", 9_000)
        eg.create_video_snapshot("m1", 6_000, item_id="i-snap-x")
        page._reload_model(keep_selection=False)
        # Pick the first segment so Reset has a state to undo.
        first = eg.segment_items("m1")[0]
        eg.set_phase_state(first.id, "edit", "picked")
        page._reload_model(keep_selection=False)
        monkeypatch.setattr(page, "_confirm_box", lambda *a, **k: True)
        page._reset_everything()
        assert eg.video_markers("m1") == []
        assert eg.video_snapshots("m1") == []
        segs = eg.segment_items("m1")
        assert len(segs) == 1
        assert eg.phase_state(segs[0].id, "edit").state == "skipped"
    finally:
        eg.close()
