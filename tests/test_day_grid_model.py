"""Tests for the Day Grid model (spec/32 §2).

Exercises ``day_grid_cells`` on the same fixture-event used by
``tests/test_cull_model.py`` — flattening artificial moment/individual
groupings to standalone cells, preserving real clusters (burst /
focus_bracket / exposure_bracket) as cluster cells, end-time chronological
ordering (videos shifted by duration_ms), and the per-cell border colours
that drive the grid's visual status.

Logic-only — no Qt, no real EXIF.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.bucket_scanner import (
    BucketScannerConfig,
    BucketScanResult,
    BurstSequence,
    IndividualPhoto,
    SourceKind,
    VideoFile,
)
from mira.picked import (
    CellColor,
    CullCell,
    CullCluster,
    cell_color_for_item,
    cluster_color,
    day_grid_cells,
)
from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore

FIXED_NOW = "2026-06-01T12:00:00+00:00"


# --------------------------------------------------------------------------- #
# CellColor: per-item + per-cluster (spec/32 §2.4)
# --------------------------------------------------------------------------- #


def _ps(item_id, state):
    return m.PhaseState(item_id=item_id, phase="pick", state=state)


def test_cell_color_photo_maps_phase_state():
    states = {
        "k": _ps("k", "picked"),
        "d": _ps("d", "skipped"),
        "c": _ps("c", "candidate"),
    }
    assert cell_color_for_item("k", "photo", "pick", states) is CellColor.KEPT
    assert cell_color_for_item("d", "photo", "pick", states) is CellColor.DISCARDED
    assert cell_color_for_item("c", "photo", "pick", states) is CellColor.COMPARE
    # No row → resolved to default_state (Nelson 2026-06-04 — UNTOUCHED is
    # gone as a user-visible state; every cell looks decided).
    assert cell_color_for_item(
        "missing", "photo", "pick", states,
    ) is CellColor.DISCARDED
    assert cell_color_for_item(
        "missing", "photo", "pick", states, default_state="picked",
    ) is CellColor.KEPT


def test_cell_color_video_has_no_compare():
    """Videos don't support Compare per spec/32 §2.6 — a stray candidate row
    on a video folds to the default_state (Nelson 2026-06-04 — was UNTOUCHED)."""
    states = {"v": _ps("v", "candidate")}
    assert cell_color_for_item(
        "v", "video", "pick", states,
    ) is CellColor.DISCARDED
    assert cell_color_for_item(
        "v", "video", "pick", states, default_state="picked",
    ) is CellColor.KEPT


def test_cell_color_video_shows_its_own_state():
    """spec/56: the yellow 'has kept extracts' override RETIRED with
    Pick-time clip creation — a video cell shows its own whole-video P/D
    state, exactly like a photo."""
    states = {"v": _ps("v", "picked")}
    assert cell_color_for_item("v", "video", "pick", states) is CellColor.KEPT
    states = {"v": _ps("v", "skipped")}
    assert cell_color_for_item("v", "video", "pick", states) is CellColor.DISCARDED


def test_cell_color_unknown_state_folds_to_default():
    """Unknown wire values fold to the default_state (Nelson 2026-06-04 — UNTOUCHED
    gone). A garbage row still renders a decided-looking cell instead of a neutral
    ring the user can't act on."""
    states = {"x": _ps("x", "bogus")}
    assert cell_color_for_item("x", "photo", "pick", states) is CellColor.DISCARDED
    assert cell_color_for_item(
        "x", "photo", "pick", states, default_state="picked",
    ) is CellColor.KEPT


def test_cluster_color_uniform_members():
    assert cluster_color([CellColor.KEPT, CellColor.KEPT]) is CellColor.KEPT
    assert cluster_color([CellColor.DISCARDED] * 3) is CellColor.DISCARDED
    assert cluster_color([CellColor.COMPARE, CellColor.COMPARE]) is CellColor.COMPARE
    assert cluster_color([CellColor.UNTOUCHED] * 4) is CellColor.UNTOUCHED


def test_cluster_color_mixed_any_split():
    """Spec/32 §2.4: 'any partial decision' = MIXED. A cluster with one kept and
    one untouched member is mixed (the user hasn't finished it)."""
    assert cluster_color([CellColor.KEPT, CellColor.UNTOUCHED]) is CellColor.MIXED
    assert cluster_color([CellColor.KEPT, CellColor.DISCARDED]) is CellColor.MIXED
    assert cluster_color([CellColor.KEPT, CellColor.COMPARE]) is CellColor.MIXED


def test_cluster_color_empty_is_untouched():
    assert cluster_color([]) is CellColor.UNTOUCHED


# --------------------------------------------------------------------------- #
# day_grid_cells: integration over the bucket model (spec/32 §2.2)
# --------------------------------------------------------------------------- #


# Same shape as tests/test_cull_model._ITEMS — 3 burst + 2 moment + 1 video on
# day 1; one solo (individual) on day 2. The fake scan_fn below routes them by
# filename.
_ITEMS = [
    ("i1", "photo", "d1/burst1.jpg", 1, None),
    ("i2", "photo", "d1/burst2.jpg", 1, None),
    ("i3", "photo", "d1/burst3.jpg", 1, None),
    ("i4", "photo", "d1/moment1.jpg", 1, None),
    ("i5", "photo", "d1/moment2.jpg", 1, None),
    ("i6", "video", "d1/clip.mov",   1, 12000),     # 12s video — end_time = start + 12s
    ("i7", "photo", "d2/solo.jpg",   2, None),
]


def _build_event_doc():
    items = []
    for idx, (iid, kind, rel, day, duration_ms) in enumerate(_ITEMS):
        items.append(m.Item(
            id=iid, kind=kind, origin_relpath=rel, sha256=f"sha{idx}",
            byte_size=100 + idx,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9M2",
            capture_time_raw=f"2026-04-01T08:0{idx}:00",
            capture_time_corrected=f"2026-04-01T08:0{idx}:00",
            duration_ms=duration_ms,
            created_at=FIXED_NOW, day_number=day, provenance="captured",
        ))
    phase_states = [
        _ps("i1", "picked"),
        _ps("i2", "picked"),
        _ps("i3", "skipped"),
        _ps("i4", "candidate"),
        # i5 untouched
        _ps("i6", "picked"),
        # i7 untouched
    ]
    event = m.Event(uuid="evt", name="Test", created_at=FIXED_NOW, updated_at=FIXED_NOW)
    trip_days = [
        m.TripDay(day_number=1, date="2026-04-01", description="Arrival"),
        m.TripDay(day_number=2, date="2026-04-02", description="Hike"),
    ]
    cameras = [m.Camera(camera_id="G9M2")]
    return m.EventDocument(
        event=event, items=items, phase_states=phase_states, trip_days=trip_days,
        cameras=cameras,
    )


def _open_gateway(tmp_path):
    db = tmp_path / "event.db"
    store = EventStore.create(db, event_id="evt")
    store.save_document(_build_event_doc())
    store.close()
    return EventGateway.open(db, event_root=tmp_path, now=lambda: FIXED_NOW)


def _fake_read_exif(paths):
    return [SimpleNamespace(path=Path(p), raw={}, timestamp=None) for p in paths]


def _fake_scan(entries, source_kind, config):
    """Same routing as tests/test_cull_model._fake_scan: burst* → burst,
    moment* → moment cluster, *.mov → video, else individual."""
    by = {Path(e.path).name: Path(e.path) for e in entries}
    res = BucketScanResult(source_kind=source_kind)
    burst = sorted(p for n, p in by.items() if n.startswith("burst"))
    moment = sorted(p for n, p in by.items() if n.startswith("moment"))
    vids = sorted(p for n, p in by.items() if p.suffix.lower() == ".mov")
    solos = sorted(
        p for n, p in by.items()
        if not n.startswith(("burst", "moment")) and p.suffix.lower() != ".mov"
    )
    if burst:
        res.bursts.append(BurstSequence(
            burst_id="b1", photos=list(burst), detection_source="sequence_number",
            representative_timestamp=datetime(2026, 4, 1, 8, 0, 0),
        ))
    for i, p in enumerate(moment):
        res.individuals.append(IndividualPhoto(
            path=p, timestamp=datetime(2026, 4, 1, 8, 5, i),
            cluster_id="c1", cluster_size=len(moment), cluster_position=i + 1,
        ))
    for p in solos:
        res.individuals.append(IndividualPhoto(
            path=p, timestamp=datetime(2026, 4, 2, 9, 0, 0)))
    for p in vids:
        res.videos.append(VideoFile(
            path=p, timestamp=datetime(2026, 4, 1, 8, 30, 0), duration_s=12.0))
    return res


_CFG = BucketScannerConfig(cluster_window_seconds=300.0)


def _cells_for_day(tmp_path, day):
    gw = _open_gateway(tmp_path)
    return gw, day_grid_cells(
        gw, day, phase="pick", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )


def test_day_grid_flattens_moment_and_individual(tmp_path):
    """The artificial 'moment' grouping no longer exists user-facing — its
    members become standalone cells. (Same for 'individual' on day 2.)"""
    gw, cells = _cells_for_day(tmp_path, 1)
    # Day 1 has 3 burst + 2 moment + 1 video. Burst becomes ONE cluster cell,
    # moment becomes TWO standalone cells, video becomes ONE cell → 4 cells.
    assert len(cells) == 4
    n_cluster = sum(1 for c in cells if c.is_cluster)
    n_item = sum(1 for c in cells if not c.is_cluster)
    assert n_cluster == 1
    assert n_item == 3

    day2 = day_grid_cells(
        gw, 2, phase="pick", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )
    # Day 2: one individual → one standalone cell, no cluster.
    assert len(day2) == 1
    assert not day2[0].is_cluster
    assert day2[0].item_id == "i7"


def test_day_grid_preserves_real_clusters(tmp_path):
    """Burst / focus / exposure clusters survive as cluster cells (spec/32 §1)."""
    _gw, cells = _cells_for_day(tmp_path, 1)
    clusters = [c for c in cells if c.is_cluster]
    assert len(clusters) == 1
    burst_cell = clusters[0]
    assert isinstance(burst_cell.cluster, CullCluster)
    assert burst_cell.cluster.kind == "burst"
    assert burst_cell.cluster.member_ids == ("i1", "i2", "i3")
    assert burst_cell.cluster.bucket_key == "1|burst|b1"


def test_day_grid_preserves_focus_and_exposure_bracket_clusters(tmp_path):
    """spec/32 §1 + §8.2 — REAL_CLUSTER_KINDS = {burst, focus_bracket,
    exposure_bracket}. If the scanner emits a focus_bracket or
    exposure_bracket bucket, the Day Grid surfaces it as a cluster cell,
    NOT a flat run of standalone cells.

    Nelson eyeball 2026-06-04 reported focus brackets not showing as
    clusters on Nepal — proving this test passes here pins the bug
    upstream of the Day Grid (in ``core/bucket_scanner``'s brand-profile
    detection: the G9 II's focus-bracket EXIF tag may not match
    ``panasonic.json``'s current ``FocusBracket`` rule, so the scanner
    emits a ``moment`` bucket and the Day Grid then flattens it)."""
    from core.bracket_detector import BracketSequence, BracketType
    from datetime import datetime
    gw = _open_gateway(tmp_path)

    def _focus_scan(entries, source_kind, config):
        by = {Path(e.path).name: Path(e.path) for e in entries}
        res = BucketScanResult(source_kind=source_kind)
        # Route the burst* + moment* + .mov inputs into a focus bracket
        # bucket of three photos so we can observe the kind survives.
        focus = sorted(p for n, p in by.items() if n.startswith("burst"))
        if focus:
            res.focus_brackets.append(BracketSequence(
                sequence_id="f1", sequence_type=BracketType.FOCUS,
                photos=list(focus), confidence=1.0,
                detection_source="exif_tag",
                representative_timestamp=datetime(2026, 4, 1, 8, 0, 0),
            ))
        # Remaining files go into the residual individuals bucket so
        # day_grid_cells has both a real cluster + flat cells in one pass.
        for n, p in by.items():
            if not n.startswith("burst"):
                res.individuals.append(IndividualPhoto(
                    path=p, timestamp=datetime(2026, 4, 1, 8, 10, 0)))
        return res

    cells = day_grid_cells(
        gw, 1, phase="pick", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_focus_scan, config=_CFG,
    )
    clusters = [c for c in cells if c.is_cluster]
    assert len(clusters) == 1
    assert clusters[0].cluster.kind == "focus_bracket"
    assert clusters[0].cluster.member_ids == ("i1", "i2", "i3")


# --------------------------------------------------------------------------- #
# BracketSequence import is used by the focus_bracket test above; this comment
# is here to surface why the import exists for future readers.
# --------------------------------------------------------------------------- #


def test_day_grid_exposure_bracket_recognised_too(tmp_path):
    """Symmetry check for exposure_bracket — the third of REAL_CLUSTER_KINDS."""
    from core.bracket_detector import BracketSequence, BracketType
    from datetime import datetime
    gw = _open_gateway(tmp_path)

    def _exposure_scan(entries, source_kind, config):
        by = {Path(e.path).name: Path(e.path) for e in entries}
        res = BucketScanResult(source_kind=source_kind)
        exp = sorted(p for n, p in by.items() if n.startswith("burst"))
        if exp:
            res.exposure_brackets.append(BracketSequence(
                sequence_id="e1", sequence_type=BracketType.EXPOSURE,
                photos=list(exp), confidence=1.0,
                detection_source="exif_tag",
                representative_timestamp=datetime(2026, 4, 1, 8, 0, 0),
            ))
        return res

    cells = day_grid_cells(
        gw, 1, phase="pick", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_exposure_scan, config=_CFG,
    )
    clusters = [c for c in cells if c.is_cluster]
    assert len(clusters) == 1
    assert clusters[0].cluster.kind == "exposure_bracket"


def test_day_grid_video_has_its_own_cell(tmp_path):
    """A video bucket is one cell (the master video), not flattened."""
    _gw, cells = _cells_for_day(tmp_path, 1)
    videos = [c for c in cells if not c.is_cluster and c.item_kind == "video"]
    assert len(videos) == 1
    assert videos[0].item_id == "i6"


def test_day_grid_end_time_sort_videos_shifted_by_duration(tmp_path):
    """End-time = start for photos, start + duration_ms for videos (spec/32 §2.3).
    The fixture video i6 starts at 08:05:00, runs 12s → ends 08:05:12, AFTER the
    last photo at 08:06 (i6 ordinal = 5)."""
    _gw, cells = _cells_for_day(tmp_path, 1)
    end_times = [c.end_time for c in cells]
    # Ascending — last cell ends latest.
    assert end_times == sorted(end_times)
    # The video's end_time is start (08:05) + 12s = 08:05:12.
    video_cell = next(c for c in cells if c.item_kind == "video")
    assert video_cell.end_time == "2026-04-01T08:05:12"


def test_day_grid_cluster_end_time_is_max_member(tmp_path):
    """A cluster cell's end_time = max(end_time of members) per spec/32 §2.3."""
    _gw, cells = _cells_for_day(tmp_path, 1)
    burst_cell = next(c for c in cells if c.is_cluster)
    # Burst members are i1/i2/i3 at 08:00/08:01/08:02 → cluster ends at 08:02.
    assert burst_cell.end_time == "2026-04-01T08:02:00"


def test_day_grid_cell_colors_match_phase_state(tmp_path):
    """Each cell's colour is derived once at build from phase_states.
    Default state for un-decided items is 'skipped' (Nelson 2026-06-04 —
    UNTOUCHED removed; untouched cells render as the default colour)."""
    _gw, cells = _cells_for_day(tmp_path, 1)
    by_item = {c.item_id: c for c in cells if c.item_id}
    # i4 candidate → COMPARE; i5 untouched → DISCARDED (default);
    # i6 video kept (no extracts) → KEPT.
    assert by_item["i4"].color is CellColor.COMPARE
    assert by_item["i5"].color is CellColor.DISCARDED
    assert by_item["i6"].color is CellColor.KEPT


def test_day_grid_burst_cluster_color_is_mixed_kept_plus_discarded(tmp_path):
    """The burst has i1/i2 kept + i3 discarded → MIXED (yellow), even though no
    explicit Compare is involved (spec/32 §2.4)."""
    _gw, cells = _cells_for_day(tmp_path, 1)
    burst_cell = next(c for c in cells if c.is_cluster)
    assert burst_cell.color is CellColor.MIXED
    assert burst_cell.cluster.color is CellColor.MIXED


def test_day_grid_video_cell_shows_whole_video_state(tmp_path):
    """spec/56: the yellow kept-extracts override RETIRED — even if stray
    child items exist (e.g. Edit-phase segments), the Day Grid video cell
    shows the master's OWN whole-video P/D state."""
    gw = _open_gateway(tmp_path)
    # An Edit-phase segment child (the spec/56 workshop shape) must NOT
    # influence the Pick-phase day grid.
    with gw.store.transaction():
        gw.store.upsert(m.Item(id="i6c", kind="video", provenance="clip",
                               parent_item_id="i6", created_at="t"))
        gw.store.upsert(m.VideoSegment(
            item_id="i6c", video_item_id="i6", seg_index=0, created_at="t"))
        gw.store.upsert(m.PhaseState(item_id="i6c", phase="edit", state="picked"))
    cells = day_grid_cells(
        gw, 1, phase="pick", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )
    video_cell = next(c for c in cells if c.item_kind == "video")
    assert video_cell.color is CellColor.KEPT      # the master's own state


def test_day_grid_empty_day_returns_empty_list(tmp_path):
    """Asking for a day with no items returns []."""
    _gw, cells = _cells_for_day(tmp_path, 99)
    assert cells == []


# --------------------------------------------------------------------------- #
# Perf — day_grid_cells with prebuilt days= skips pick_days entirely
# --------------------------------------------------------------------------- #


def test_day_grid_cells_with_prebuilt_days_skips_pick_days(tmp_path):
    """spec/32 + Nelson 2026-06-04 eyeball: pre-built ``days=`` lets the
    caller skip the pick_days walk on every day click — the open-Day-Grid
    perf fix."""
    from mira.picked import pick_days
    gw = _open_gateway(tmp_path)

    # First pass: build the days the regular way.
    days = pick_days(
        gw, phase="pick", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )

    # Second pass: a poisoned scan + read_exif so we fail loud if either
    # gets called when ``days=`` is provided.
    def _boom_read_exif(_paths):
        raise AssertionError("read_exif must not run when days= is provided")

    def _boom_scan(*_a, **_kw):
        raise AssertionError("scan_fn must not run when days= is provided")

    cells = day_grid_cells(
        gw, 1, phase="pick",
        read_exif=_boom_read_exif, scan_fn=_boom_scan, config=_CFG,
        days=days,
    )
    # Must still produce the expected cell shape for day 1.
    assert any(c.is_cluster for c in cells)
    assert any(c.item_id == "i6" for c in cells if not c.is_cluster)


# --------------------------------------------------------------------------- #
# spec/32 §2.10 — visited tick stamping (cluster.browsed + item_visit)
# --------------------------------------------------------------------------- #


def test_day_grid_cells_visited_defaults_false(tmp_path):
    """Fresh event with no clicks → no ticks anywhere."""
    _gw, cells = _cells_for_day(tmp_path, 1)
    for c in cells:
        assert c.visited is False


def test_day_grid_cells_stamps_visited_on_item_cells(tmp_path):
    """set_item_visited on a Day Grid item cell → the cell renders visited=True."""
    gw, _ = _cells_for_day(tmp_path, 1)
    # i6 is the video master on day 1 — pick it because it's a standalone
    # cell (not part of any cluster).
    gw.set_item_visited("i6", "pick", True)
    cells = day_grid_cells(
        gw, 1, phase="pick", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )
    video_cell = next(c for c in cells if c.item_id == "i6")
    assert video_cell.visited is True
    # The cluster cell (containing i1/i2/i3) is still untouched.
    cluster = next(c for c in cells if c.is_cluster)
    assert cluster.visited is False


def test_day_grid_cells_stamps_visited_on_cluster_cells(tmp_path):
    """set_bucket_browsed on the cluster's bucket_key → cluster cell visited."""
    gw, cells_before = _cells_for_day(tmp_path, 1)
    cluster = next(c for c in cells_before if c.is_cluster)
    gw.set_bucket_browsed(cluster.cluster.bucket_key, "pick", True)
    cells = day_grid_cells(
        gw, 1, phase="pick", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )
    cluster_after = next(c for c in cells if c.is_cluster)
    assert cluster_after.visited is True
    # Item cells unaffected by a cluster's browsed flip.
    for c in cells:
        if not c.is_cluster:
            assert c.visited is False


def test_day_grid_cells_visited_is_per_phase(tmp_path):
    """A pick-phase tick does NOT leak into the edit-phase view (spec/48
    collapsed cull + select into one 'pick' phase, so cross-phase
    independence is now pick vs edit)."""
    gw, _ = _cells_for_day(tmp_path, 1)
    gw.set_item_visited("i6", "pick", True)
    edit_cells = day_grid_cells(
        gw, 1, phase="edit", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )
    # Some events may emit no edit cells (Edit filters to Pick-Kept) —
    # but if there IS an i6 cell it must NOT be visited.
    for c in edit_cells:
        if c.item_id == "i6":
            assert c.visited is False


# --------------------------------------------------------------------------- #
# spec/32 §6.3 — Process Day Grid uses Adjustment.edit_exported, not
# phase_state. No Compare at Process.
# --------------------------------------------------------------------------- #


def test_process_day_grid_unprocessed_items_render_phase_default(tmp_path):
    """Fresh event with NO Adjustment rows → every cell renders the phase
    default colour (Nelson 2026-06-05 — "untouched is gone").  With
    ``default_state="picked"`` the cells are KEPT (green); with ``"skipped"``
    (the day_grid_cells default), they're DISCARDED (red)."""
    gw, _ = _cells_for_day(tmp_path, 1)
    # Default day_grid_cells default_state="skipped" → un-exported cells
    # render DISCARDED.
    cells_discard = day_grid_cells(
        gw, 1, phase="edit", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )
    assert cells_discard
    for c in cells_discard:
        assert c.color is CellColor.DISCARDED
    # Pass default_state="picked" → un-exported cells render KEPT (the actual
    # Process default in Settings).
    cells_keep = day_grid_cells(
        gw, 1, phase="edit", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
        default_state="picked",
    )
    assert cells_keep
    for c in cells_keep:
        assert c.color is CellColor.KEPT


def test_edit_day_grid_marked_item_renders_green(tmp_path):
    """spec/59 §8: the edit ``phase_state`` IS the cell colour at Edit —
    green = marked for export, red = not. (Supersedes the retired
    edit_exported-flag border these tests used to pin.)"""
    gw, _ = _cells_for_day(tmp_path, 1)
    gw.set_phase_state("i4", "edit", "picked")
    cells = day_grid_cells(
        gw, 1, phase="edit", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
        default_state="skipped",
    )
    by_id = {c.item_id: c for c in cells if c.item_id}
    assert by_id["i4"].color is CellColor.KEPT
    # The un-marked sibling renders in the phase default (red here).
    assert by_id["i5"].color is CellColor.DISCARDED


def test_edit_day_grid_cluster_mixed_when_only_some_members_marked(tmp_path):
    """Cluster cell at Edit aggregates members' export status: any mix →
    MIXED (yellow). spec/59 §8 + spec/32 §2.4."""
    gw, _ = _cells_for_day(tmp_path, 1)
    # Burst cluster has i1, i2, i3. Mark only i1 for export.
    gw.set_phase_state("i1", "edit", "picked")
    cells = day_grid_cells(
        gw, 1, phase="edit", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )
    cluster_cell = next(c for c in cells if c.is_cluster)
    assert cluster_cell.color is CellColor.MIXED


def test_edit_day_grid_cluster_green_when_all_members_marked(tmp_path):
    gw, _ = _cells_for_day(tmp_path, 1)
    for iid in ("i1", "i2", "i3"):
        gw.set_phase_state(iid, "edit", "picked")
    cells = day_grid_cells(
        gw, 1, phase="edit", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
    )
    cluster_cell = next(c for c in cells if c.is_cluster)
    assert cluster_cell.color is CellColor.KEPT


def test_edit_day_grid_exported_flag_does_not_colour(tmp_path):
    """The inverse hard rule of the retired design: the edit_exported
    freshness flag must NOT colour Edit cells — its grid signal moved
    to the Exported watermark (spec/59 §8, lineage-driven)."""
    gw, _ = _cells_for_day(tmp_path, 1)
    gw.set_edit_exported("i4", True)
    cells = day_grid_cells(
        gw, 1, phase="edit", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
        default_state="skipped",
    )
    photo_cell = next(c for c in cells if c.item_id == "i4")
    # No phase_state row → renders the phase default, flag or no flag.
    assert photo_cell.color is CellColor.DISCARDED
    # And the flag is not the watermark driver either (no lineage row).
    assert photo_cell.exported is False
