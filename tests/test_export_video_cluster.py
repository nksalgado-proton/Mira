"""The Export-mode video cluster reshape (spec/56 + Nelson 2026-06-15
type-stamp pass) + the Thumb type stamp.

Pins:

* In Export mode, a source video with picked segments + a snapshot
  becomes ONE synthetic "video" cluster cover in the day grid (not a
  flat video cell).
* Drilling into the cluster surfaces each segment as a GridItem with
  ``stamp == "clip"`` ("Video Clip") and each snapshot with
  ``stamp == "snapshot"`` ("Snapshot").
* Pick mode is NOT reshaped — a video stays a flat cell.
* The Thumb widget paints the type stamp without crashing on either
  theme.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.picked.status import STATE_PICKED
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.design.thumbs import _CLUSTER_LABELS, _STAMP_LABELS, Thumb
from mira.ui.pages.days_grid_page import (
    _CLUSTER_KIND_TO_THUMB,
    DaysGridPage,
    GridItem,
)

FIXED_NOW = "2026-06-15T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


@pytest.fixture(autouse=True)
def _stub_exif(monkeypatch):
    import core.exif_reader as er
    monkeypatch.setattr(er, "read_exif_single", lambda path: None)
    monkeypatch.setattr(er, "read_exif_batch", lambda paths: [])


def _doc_with_video() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-cluster", name="Cluster fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items.append(m.Item(
        id="vidA", kind="video", provenance="captured",
        created_at=FIXED_NOW,
        origin_relpath="Original Media/vidA.mp4",
        sha256="v" * 64, byte_size=128,
        materialized_at=FIXED_NOW, materialized_phase="ingest",
        duration_ms=30_000,
        camera_id="G9", day_number=1,
        capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00",
    ))
    doc.phase_states.append(m.PhaseState(
        item_id="vidA", phase="pick", state="picked"))
    return doc


@pytest.fixture
def event_dir(tmp_path):
    p = tmp_path / "Original Media" / "vidA.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 128)
    return tmp_path


@pytest.fixture
def store_and_gateway(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-cluster")
    store.save_document(_doc_with_video())
    counter = itertools.count(1)
    eg = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield store, eg
    eg.close()


@pytest.fixture
def app_gateway(event_dir, store_and_gateway, monkeypatch, tmp_path):
    store, _ = store_and_gateway
    gw = Gateway(settings=SettingsRepo(tmp_path / "settings.json"))
    counter = itertools.count(100)

    def _open_event(_event_id):
        return EventGateway(
            store, event_root=event_dir, now=_now,
            new_id=lambda: f"app-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    yield gw


def _setup_clips_and_snap(eg: EventGateway) -> tuple:
    """Two markers → three segments; pick the outer two; drop a
    snapshot mid-video (auto-picked)."""
    eg.add_video_marker("vidA", 10_000)
    eg.add_video_marker("vidA", 20_000)
    eg.ensure_video_segments("vidA", default_state="skipped")
    segs = eg.video_segments("vidA")
    eg.set_phase_state(segs[0].item_id, "edit", "picked")
    eg.set_phase_state(segs[2].item_id, "edit", "picked")
    snap_id = eg.create_video_snapshot("vidA", 5_000)
    return segs, snap_id


def _page_with_synthetic_video_cluster(
    app_gateway, event_dir: Path,
) -> DaysGridPage:
    """Open in Export mode and feed the page one flat video GridItem
    via the reshape path. The bucket pipeline needs photo EXIF to put
    the video on the grid; we shortcut to a deterministic input."""
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-cluster", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    # Seed the day_items with the flat video, then run the reshape —
    # the production path runs reshape inside _refresh_from_gateway
    # after _items_from_cells; here we drive it directly so the test
    # doesn't depend on the EXIF/scanner pipeline.
    flat = GridItem(
        item_id="vidA", item_kind="video",
        state=STATE_PICKED,
        _path=event_dir / "Original Media" / "vidA.mp4",
    )
    page._items = page._reshape_for_export(
        [flat], page._eg.phase_states(page._phase))
    page._day_items = list(page._items)
    return page


# ── 1) "video" wired through the cluster grammar ─────────────────────


def test_cluster_kind_to_thumb_carries_video():
    """The day-grid mapper must know "video" → Thumb cluster_type
    "video" so the badge family resolves."""
    assert _CLUSTER_KIND_TO_THUMB.get("video") == "video"


def test_thumb_cluster_labels_carries_video():
    """The Thumb badge label dict must carry a "Video" entry; the
    badge SVG (assets/icons/clusters/badge/video.svg) is on disk."""
    assert _CLUSTER_LABELS.get("video") == "Video"
    from mira.ui.design.thumbs import _BADGE_DIR
    assert (_BADGE_DIR / "video.svg").is_file()


def test_stamp_labels_carry_clip_and_snapshot():
    assert _STAMP_LABELS["clip"] == "Video Clip"
    assert _STAMP_LABELS["snapshot"] == "Snapshot"


# ── 2) Reshape: flat video → synthetic "video" cluster ───────────────


def test_export_reshape_clusters_video_with_clips_and_snapshots(
        qapp, app_gateway, store_and_gateway, event_dir):
    """A source video with workshop-greened children becomes ONE
    "video" cluster cover.

    spec/89 Slice 9 / Block 6 D1.C — only segments + snapshots with
    ``phase_state(edit) == 'picked'`` are members. Workshop-skipped
    ones don't appear. The fixture picks segs[0] + segs[2] + the
    snapshot; segs[1] is left at the system-default 'skipped'."""
    _, eg_setup = store_and_gateway
    segs, snap_id = _setup_clips_and_snap(eg_setup)

    page = _page_with_synthetic_video_cluster(app_gateway, event_dir)
    assert len(page._items) == 1
    cover = page._items[0]
    assert cover.item_kind == "cluster"
    assert cover.cluster_type == "video"
    assert cover._video_item_id == "vidA"
    cluster = cover._cull_cluster
    assert cluster is not None
    assert cluster.kind == "video"
    member_ids = {m.item_id for m in cluster.members}
    # Only the workshop-greened segments + the snapshot are members.
    assert member_ids == {segs[0].item_id, segs[2].item_id, snap_id}
    assert segs[1].item_id not in member_ids        # workshop-skipped
    assert cover.cluster_count == 3
    page.close_event()


def test_export_reshape_hides_pristine_video_with_no_children(
        qapp, app_gateway, event_dir):
    """spec/89 Slice 9 / Block 6 D3.B — a pristine video (no markers,
    no snapshots) drops out of the Export grid entirely. The flat-
    fallback the pre-Slice-9 reshape kept is gone — the user has to
    return to the Workshop and green something to bring it back."""
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-cluster", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    flat = GridItem(
        item_id="vidA", item_kind="video",
        state=STATE_PICKED,
        _path=event_dir / "Original Media" / "vidA.mp4",
    )
    out = page._reshape_for_export(
        [flat], page._eg.phase_states(page._phase))
    assert out == []                                # cell dropped
    page.close_event()


def test_export_reshape_hides_video_with_no_picked_children(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 Slice 9 — a video whose every segment + snapshot is
    workshop-skipped drops out too. The "no workshop touch" hide rule
    is the symmetric variant of D3.B (no picked children = nothing to
    ship)."""
    _, eg_setup = store_and_gateway
    eg_setup.add_video_marker("vidA", 10_000)
    eg_setup.add_video_marker("vidA", 20_000)
    eg_setup.ensure_video_segments("vidA", default_state="skipped")
    # No set_phase_state(picked) calls — every segment stays skipped.

    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-cluster", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    flat = GridItem(
        item_id="vidA", item_kind="video",
        state=STATE_PICKED,
        _path=event_dir / "Original Media" / "vidA.mp4",
    )
    out = page._reshape_for_export(
        [flat], page._eg.phase_states(page._phase))
    assert out == []                                # cell dropped
    page.close_event()


def test_export_video_cover_color_green_when_all_picked(
        qapp, app_gateway, store_and_gateway, event_dir):
    """spec/89 Block 6 §6.3 — cluster cover state machine: all
    members picked → cover green."""
    _, eg_setup = store_and_gateway
    _setup_clips_and_snap(eg_setup)
    page = _page_with_synthetic_video_cluster(app_gateway, event_dir)
    cover = page._items[0]
    assert cover.state == STATE_PICKED
    page.close_event()


def test_export_video_cover_color_yellow_when_mixed_in_subgrid(
        qapp, app_gateway, store_and_gateway, event_dir):
    """spec/89 Block 6 §6.3 — flipping a member to skipped inside the
    cluster sub-grid (no full refresh yet) produces a mixed cover.

    Mixed paints when the cover is rebuilt against a member-state list
    that carries both picked + skipped — exercising the machine
    directly proves the branch is wired even though, post-full-
    refresh, the workshop-greened filter drops the skipped member and
    the cover reverts to green with N-1."""
    from mira.picked.status import STATE_SKIPPED as _STATE_SKIPPED
    _, eg_setup = store_and_gateway
    _setup_clips_and_snap(eg_setup)
    page = _page_with_synthetic_video_cluster(app_gateway, event_dir)
    # All-picked: cover green.
    assert page._video_cover_color([STATE_PICKED] * 3) == "picked"
    # All-skipped: cover red.
    assert page._video_cover_color(
        [_STATE_SKIPPED] * 3) == "skipped"
    # Mixed: cover yellow (distinct from Edit's amber per Block 4 D3.A).
    assert page._video_cover_color(
        [STATE_PICKED, _STATE_SKIPPED]) == "mixed"
    # No Compare leg (Block 6 — members can never be Compare).
    page.close_event()


# ── 3) Sub-grid drill-in: members carry their type stamp ─────────────


def test_open_cluster_stamps_clip_and_snapshot_members(
        qapp, app_gateway, store_and_gateway, event_dir):
    """Drilling into the video cluster cover surfaces each clip
    with stamp="clip" and each snapshot with stamp="snapshot" — the
    Thumb renders "Video Clip" / "Snapshot" from these.

    spec/89 Slice 9 / Block 6 D1.C — only the workshop-greened
    members (segs[0] + segs[2] + snap, not the workshop-skipped
    segs[1]) appear in the sub-grid."""
    _, eg_setup = store_and_gateway
    segs, snap_id = _setup_clips_and_snap(eg_setup)
    page = _page_with_synthetic_video_cluster(app_gateway, event_dir)

    cover = page._items[0]
    page._open_cluster(cover._cull_cluster)

    by_id = {g.item_id: g for g in page._items}
    assert by_id[segs[0].item_id].stamp == "clip"
    assert by_id[segs[2].item_id].stamp == "clip"
    assert segs[1].item_id not in by_id              # workshop-skipped
    assert by_id[snap_id].stamp == "snapshot"
    page.close_event()


def test_open_video_cluster_does_not_pollute_bucket_softstate(
        qapp, app_gateway, store_and_gateway, event_dir):
    """The synthetic ``video:<id>`` bucket_key must NOT land in the
    bucket soft-state table when the user drills in — that table is
    for scanner buckets, not UI synthetics."""
    _, eg_setup = store_and_gateway
    _setup_clips_and_snap(eg_setup)
    page = _page_with_synthetic_video_cluster(app_gateway, event_dir)

    eg = page._eg
    calls: list[tuple] = []
    real = eg.set_bucket_browsed
    eg.set_bucket_browsed = (
        lambda key, phase, value=True:
            calls.append((key, phase, value)) or real(key, phase, value))
    page._open_cluster(page._items[0]._cull_cluster)
    assert calls == []                              # never called
    page.close_event()


# ── 4) Ship logic: cluster covers AND sub-grid members both work ─────


def test_collect_ship_cells_expands_video_cluster_in_day_mode(
        qapp, app_gateway, store_and_gateway, event_dir):
    """When the user clicks Export from the day grid, a "video"
    cluster cover expands into its picked segments + snapshots — the
    user shouldn't have to drill in to ship."""
    _, eg_setup = store_and_gateway
    segs, snap_id = _setup_clips_and_snap(eg_setup)
    page = _page_with_synthetic_video_cluster(app_gateway, event_dir)

    photo_cells, segment_rows, snapshot_cells = page._collect_ship_cells()
    assert photo_cells == []
    assert {sr.item_id for sr in segment_rows} == {
        segs[0].item_id, segs[2].item_id}
    assert [sc.item_id for sc in snapshot_cells] == [snap_id]
    page.close_event()


def test_collect_ship_cells_from_subgrid_uses_stamps(
        qapp, app_gateway, store_and_gateway, event_dir):
    """After drilling into a video cluster, Export ships the picked
    members. The translation reads ``stamp`` to route each member to
    its lane (clip → segment_row, snapshot → snapshot_cell)."""
    _, eg_setup = store_and_gateway
    segs, snap_id = _setup_clips_and_snap(eg_setup)
    page = _page_with_synthetic_video_cluster(app_gateway, event_dir)
    page._open_cluster(page._items[0]._cull_cluster)

    photo_cells, segment_rows, snapshot_cells = page._collect_ship_cells()
    assert photo_cells == []
    assert {sr.item_id for sr in segment_rows} == {
        segs[0].item_id, segs[2].item_id}
    assert [sc.item_id for sc in snapshot_cells] == [snap_id]
    page.close_event()


# ── 5) Pick + Edit grids unchanged — gating proof ────────────────────


def test_pick_mode_reshape_is_not_applied(
        qapp, app_gateway, store_and_gateway, event_dir):
    """In Pick mode, a flat video stays flat — the reshape only fires
    when ``_export_mode`` is True. (The reshape function is still
    callable, but ``_refresh_from_gateway`` will not invoke it.)"""
    _, eg_setup = store_and_gateway
    _setup_clips_and_snap(eg_setup)
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-cluster", 1, title="Day", date_iso="2026-04-01",
        phase="pick")
    assert page._export_mode is False
    # Driving the reshape would still produce a cluster, but the
    # refresh path doesn't call it in Pick mode — pin both halves.
    flat = GridItem(
        item_id="vidA", item_kind="video",
        state=STATE_PICKED,
        _path=event_dir / "Original Media" / "vidA.mp4",
    )
    # The reshape itself still works (engines stay engines); what
    # matters is the gate.
    assert page._reshape_for_export(
        [flat], page._eg.phase_states("edit")
    )[0].item_kind == "cluster"
    # But Pick's _refresh_from_gateway never calls the reshape:
    # confirm the gate via the public bool.
    assert page._export_mode is False
    page.close_event()


# ── 6) Thumb paints both stamps without crashing on either theme ─────


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("stamp", ["clip", "snapshot"])
def test_thumb_paints_type_stamp_in_both_themes(qapp, theme, stamp):
    """Smoke: a Thumb with a stamp paints without raising on either
    theme. (The chip + tinted glyph use cached palettes; this guards
    against a missing-asset / wrong-import regression.)

    The app-wide ``theme`` property is restored at the end so the test
    doesn't leak a light-theme state into other suites whose pixmap
    cache-key equality assumes the default dark theme."""
    prior = qapp.property("theme")
    qapp.setProperty("theme", theme)
    try:
        t = Thumb(state=STATE_PICKED, stamp=stamp)
        # Realise the widget so paintEvent runs.
        t.show()
        t.repaint()
        t.close()
    finally:
        qapp.setProperty("theme", prior)
