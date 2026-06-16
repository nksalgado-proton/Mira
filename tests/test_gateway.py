"""Tests for the gateway — the hard interface (spec/08, spec/30 §7; charter §4 step 6).

Logic-only (no Qt). Three tiers:

* ``EventsIndex`` — protected round-trip, relpath vs abs-fallback resolution, tolerant
  load (missing → empty, corrupt → backup + empty), upsert/remove.
* ``Gateway`` — ``materialise_event`` (restore == migration, one reader) over the
  ``test_store`` rich document; ``list_events`` / ``open_event`` resolution; the
  ``photos_base_path`` anchor written to settings + index together.
* ``EventGateway`` — the §4.1 queries over a materialised rich event (SQL-pushed
  aggregates) and the load-bearing mutators (phase state stamping + dirty, buckets,
  classification, set_closed, the clip-as-item video flow, subsets).
"""
from __future__ import annotations

from pathlib import PureWindowsPath

import pytest

from mira.gateway import EventsIndex, EventsListing, EventsQuery, Gateway, make_entry
from mira.gateway.index import _resolve_root
from mira.settings.repo import SettingsRepo
from mira.store import json_dump, models as m

# reuse the canonical rich document the store gate is built on
from tests.test_store import _rich_document


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

FIXED_NOW = "2026-06-01T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _entry_for(root, base):
    """An index row for the rich event rooted at ``root`` under ``base``."""
    return make_entry(
        event_id="evt-1", name="Costa Rica 2026", start_date="2026-04-01",
        end_date="2026-04-14", is_closed=False, event_root=root, photos_base_path=base,
    )


def _gateway(tmp_path, base):
    """A Gateway whose settings + index live under tmp_path, anchored at ``base``."""
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index, now=_now)
    gw.set_photos_base_path(str(base))
    return gw


# --------------------------------------------------------------------------- #
# EventsIndex
# --------------------------------------------------------------------------- #


def test_make_entry_relpath_under_base():
    entry = make_entry(
        event_id="e", name="N", start_date=None, end_date=None, is_closed=False,
        event_root=PureWindowsPath("D:/Photos/_mira/2026 - CR"),
        photos_base_path=PureWindowsPath("D:/Photos/_mira"),
    )
    assert entry["event_relpath"] == "2026 - CR"
    assert entry["event_root_abs"] is None


def test_make_entry_cross_volume_abs_fallback():
    entry = make_entry(
        event_id="e", name="N", start_date=None, end_date=None, is_closed=False,
        event_root=PureWindowsPath("E:/elsewhere/CR"),
        photos_base_path=PureWindowsPath("D:/Photos/_mira"),
    )
    assert entry["event_relpath"] is None
    assert entry["event_root_abs"] == "E:\\elsewhere\\CR"


def test_resolve_root_prefers_abs_then_relpath(tmp_path):
    base = tmp_path / "base"
    assert _resolve_root({"event_root_abs": str(tmp_path / "x"), "event_relpath": "y"}, base) == tmp_path / "x"
    assert _resolve_root({"event_root_abs": None, "event_relpath": "y"}, base) == base / "y"
    assert _resolve_root({"event_root_abs": None, "event_relpath": None}, base) is None


def test_index_upsert_remove_and_protected(tmp_path):
    idx = EventsIndex(tmp_path / "events_index.json")
    assert idx.entries() == []  # missing file → empty
    idx.upsert(_entry_for(PureWindowsPath("D:/b/CR"), PureWindowsPath("D:/b")))
    idx.upsert({"id": "evt-2", "name": "B", "start_date": "2026-01-01",
                "end_date": None, "is_closed": True, "event_relpath": "B", "event_root_abs": None})
    assert {e["id"] for e in idx.entries()} == {"evt-1", "evt-2"}
    # sorted by start_date then name
    assert [e["id"] for e in idx.entries()] == ["evt-2", "evt-1"]
    # protection sidecar exists
    assert (tmp_path / "events_index.json.sha256").exists()
    # replace, not duplicate
    idx.upsert({**idx.get("evt-1"), "name": "Renamed"})
    assert idx.get("evt-1")["name"] == "Renamed"
    assert len(idx.entries()) == 2
    idx.remove("evt-2")
    assert {e["id"] for e in idx.entries()} == {"evt-1"}


def test_index_tolerant_of_corrupt_file(tmp_path):
    path = tmp_path / "events_index.json"
    path.write_text("{ this is not json", encoding="utf-8")
    idx = EventsIndex(path)
    assert idx.entries() == []                      # corrupt → empty, no raise
    assert (tmp_path / "events_index.json.bak").exists()  # bad bytes preserved


def test_index_set_base_mirror(tmp_path):
    idx = EventsIndex(tmp_path / "events_index.json")
    idx.set_base("D:/Photos/_mira")
    assert idx.base_path() == "D:/Photos/_mira"


# --------------------------------------------------------------------------- #
# Gateway — anchor, materialise, list, open
# --------------------------------------------------------------------------- #


def test_set_photos_base_path_writes_both(tmp_path):
    gw = _gateway(tmp_path, tmp_path / "lib")
    assert gw.photos_base_path() == tmp_path / "lib"
    assert SettingsRepo(tmp_path / "settings.json").load().photos_base_path == str(tmp_path / "lib")
    assert EventsIndex(tmp_path / "events_index.json").base_path() == str(tmp_path / "lib")


# --- base-change guard (verify-then-allow, charter §5.9, Nelson 2026-06-01) --- #


def test_base_change_blockers_allows_when_files_present_under_new_base(tmp_path):
    """A relative-anchored event whose event.db is already at new_base/relpath (a genuine
    whole-library move) is NOT a blocker — verify-then-allow."""
    gw = _gateway(tmp_path, tmp_path / "old")
    gw.index.upsert({"id": "rel-here", "name": "Trip A",
                     "event_relpath": "Trip A", "event_root_abs": None})
    new_base = tmp_path / "new"
    (new_base / "Trip A").mkdir(parents=True)
    (new_base / "Trip A" / "event.db").write_text("x", encoding="utf-8")
    assert gw.base_change_blockers(str(new_base)) == []


def test_base_change_blockers_flags_orphaned_event(tmp_path):
    """A relative-anchored event with no event.db under the new base would be orphaned."""
    gw = _gateway(tmp_path, tmp_path / "old")
    gw.index.upsert({"id": "rel-missing", "name": "Trip B",
                     "event_relpath": "Trip B", "event_root_abs": None})
    blockers = gw.base_change_blockers(str(tmp_path / "new"))
    assert [b["id"] for b in blockers] == ["rel-missing"]
    assert blockers[0]["relpath"] == "Trip B"


def test_base_change_blockers_ignores_abs_anchored(tmp_path):
    """Cross-volume (abs-anchored) events never depend on the base, so are never blockers."""
    gw = _gateway(tmp_path, tmp_path / "old")
    gw.index.upsert({"id": "abs", "name": "Trip C", "event_relpath": None,
                     "event_root_abs": str(tmp_path / "elsewhere" / "Trip C")})
    assert gw.base_change_blockers(str(tmp_path / "new")) == []


def test_base_change_blockers_empty_base_orphans_relative_events(tmp_path):
    """Clearing the base leaves relative-anchored events unresolvable → all are blockers."""
    gw = _gateway(tmp_path, tmp_path / "old")
    gw.index.upsert({"id": "rel", "name": "Trip D",
                     "event_relpath": "Trip D", "event_root_abs": None})
    assert [b["id"] for b in gw.base_change_blockers("")] == ["rel"]


def test_materialise_event_is_restore_equals_migration(tmp_path):
    """The load-bearing gate: a JSON dump → an event.db that load_document-equals it."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    doc = _rich_document()
    event_json = json_dump.to_json(doc)
    entry = _entry_for(base / "2026 - Costa Rica", base)

    db_path = gw.materialise_event(event_json, entry)
    assert db_path == base / "2026 - Costa Rica" / "event.db"

    events = gw.list_events()
    assert len(events) == 1 and events[0]["event_root"] == base / "2026 - Costa Rica"

    # restore == migration: materialise yields the same store content as a direct save.
    from mira.store.repo import EventStore
    ref = EventStore.create(tmp_path / "ref.db", event_id="evt-1")
    ref.save_document(doc)
    canonical = ref.load_document()
    ref.close()

    eg = gw.open_event("evt-1")
    assert eg.store.load_document() == canonical
    eg.close()


def test_materialise_event_births_spec57_tree(tmp_path):
    """spec/57 §1: every creation/restore path births the same skeleton —
    Nelson's first create-from-files eyeball (2026-06-10) caught the gateway
    path making only the bare root."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    entry = _entry_for(base / "2026 - Costa Rica", base)
    gw.materialise_event(json_dump.to_json(_rich_document()), entry)
    root = base / "2026 - Costa Rica"
    for sub in ("Original Media/_cameras", "Original Media/_phones",
                "Original Media/_other", "Edited Media", "Cuts"):
        assert (root / sub).is_dir(), sub
    # The lazy dirs are NOT pre-created (Picked Media on entering Edit;
    # Merged on the first stack adoption).
    assert not (root / "Picked Media").exists()
    assert not (root / "Original Media" / "Merged").exists()


def test_open_unknown_event_raises(tmp_path):
    gw = _gateway(tmp_path, tmp_path / "lib")
    with pytest.raises(KeyError):
        gw.open_event("ghost")


# --------------------------------------------------------------------------- #
# EventGateway — queries
# --------------------------------------------------------------------------- #


@pytest.fixture
def event_gw(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    entry = _entry_for(base / "ev", base)
    gw.materialise_event(json_dump.to_json(_rich_document()), entry)
    eg = gw.open_event("evt-1")
    yield eg
    eg.close()


def test_query_event_and_roster(event_gw):
    assert event_gw.event().name == "Costa Rica 2026"
    assert event_gw.event().uuid == "evt-1"
    assert len(event_gw.trip_days()) == 2
    assert {c.camera_id for c in event_gw.cameras()} == {"G9M2", "iPhone"}
    assert event_gw.event_root.name == "ev"


def test_items_filtering(event_gw):
    assert [i.id for i in event_gw.items()]  # time-ordered, non-empty
    # segments are video items too, so kind='video' includes the virtual segments
    assert {i.id for i in event_gw.items(kind="video")} == {"i-video", "i-seg0", "i-seg1"}
    assert {i.id for i in event_gw.items(provenance="captured", kind="video")} == {"i-video"}
    assert {i.id for i in event_gw.items(day=2)} == {"i-video"}  # virtual children have no day
    # phase + state joins phase_state explicit rows. Post Slice-0 (cull+select
    # collapsed to "pick"), the fixture has one phase_state for i-photo with
    # state="picked"; the candidate state is no longer present so the query
    # returns empty.
    assert {i.id for i in event_gw.items(phase="pick", state="picked")} == {"i-photo"}
    assert {i.id for i in event_gw.items(phase="pick", state="candidate")} == set()
    assert {i.id for i in event_gw.items(phase="edit", state="picked")} == {"i-seg0", "i-snap"}
    with pytest.raises(ValueError):
        event_gw.items(state="picked")  # state without phase


def test_children_and_day_tree(event_gw):
    assert {i.id for i in event_gw.children("i-video")} == {"i-seg0", "i-seg1", "i-snap"}
    tree = event_gw.day_tree()
    by_day = {g["day_number"]: g for g in tree}
    # day_tree counts captured only (stack output + virtual children excluded)
    assert by_day[1]["total"] == 1 and by_day[1]["photos"] == 1
    assert by_day[2]["total"] == 1 and by_day[2]["videos"] == 1


def test_phase_progress_is_a_query(event_gw):
    prog = event_gw.phase_progress("pick")
    assert prog["counts"] == {"picked": 1} and prog["total"] == 1
    # Post Slice-0: the fixture row is now state="picked" (not dirty).
    # phase x day heatmap aggregate
    pdp = event_gw.phase_day_progress()
    assert pdp["pick"][1]["decided"] == 1 and pdp["pick"][1]["total"] == 1


def test_video_workshop_reads(event_gw):
    """spec/56 reads: markers in at_ms order, segments in seg_index order,
    geometry DERIVED from markers + duration (never stored)."""
    assert [mk.at_ms for mk in event_gw.video_markers("i-video")] == [4000]
    assert [s.item_id for s in event_gw.video_segments("i-video")] == ["i-seg0", "i-seg1"]
    assert [it.id for it in event_gw.segment_items("i-video")] == ["i-seg0", "i-seg1"]
    assert [s.at_ms for s in event_gw.video_snapshots("i-video")] == [3000]
    # one marker at 4000 over a 125 000 ms video → two derived spans
    assert event_gw.segment_bounds("i-video") == [(0, 4000), (4000, 125_000)]
    assert event_gw.video_adjustment("i-seg0").speed == 0.5
    # the Export work-list: picked virtual children, segments in timeline order
    assert [it.id for it in event_gw.unmaterialized_kept_children("edit")] == [
        "i-seg0", "i-snap"]


def test_event_budget_and_stack_members(event_gw):
    """The rump of the old test_curate_and_subset_queries — share_tag / subsets /
    share_maps are retired per spec/52 + spec/51. What's left is what survives:
    budget folded into event, stack_members + lineage as plain spine queries."""
    assert event_gw.event().budget_short_target_s == 300          # budget folded into event
    assert [sm.item_id for sm in event_gw.stack_members("brk1")] == ["i-photo"]
    assert len(event_gw.lineage()) == 2


# --------------------------------------------------------------------------- #
# EventGateway — mutators
# --------------------------------------------------------------------------- #


def test_set_phase_state_stamps_and_clears_dirty(event_gw):
    # Post Slice-0: i-photo starts state="picked" (cull+select collapsed).
    # Setting it again to "picked" is a no-op-style re-stamp; verify timestamps.
    event_gw.set_phase_state("i-photo", "pick", "picked")
    ps = event_gw.phase_state("i-photo", "pick")
    assert ps.state == "picked" and ps.derived_dirty is False and ps.decided_at == FIXED_NOW
    assert event_gw.phase_progress("pick")["dirty"] == 0


def test_mark_derived_dirty_and_commit(event_gw):
    event_gw.mark_derived_dirty("pick", ["i-photo"])
    assert event_gw.phase_state("i-photo", "pick").derived_dirty is True
    event_gw.commit_phase("pick")
    assert event_gw.phase_state("i-photo", "pick").committed_at == FIXED_NOW


def test_bucket_soft_state_mutators(event_gw):
    event_gw.set_bucket_reviewed("new-bucket", "pick", True)
    assert event_gw.bucket("new-bucket", "pick").reviewed is True
    event_gw.set_bucket_current_index("new-bucket", "pick", 7)
    assert event_gw.bucket("new-bucket", "pick").current_index == 7
    event_gw.dismiss_nudge("new-bucket", "pick")
    assert event_gw.bucket("new-bucket", "pick").nudge_dismissed is True
    # bucket_status: histogram over members' phase_state
    assert event_gw.bucket_status("G9M2/01/sunrise", "pick") == {}  # no members cached in fixture


def test_set_classification_user_override(event_gw):
    event_gw.set_classification("i-photo", "macro", "user")
    it = event_gw.item("i-photo")
    assert it.classification == "macro" and it.classification_source == "user"


def test_set_closed_reflects_in_event_and_updates_timestamp(event_gw):
    event_gw.set_closed(True)
    assert event_gw.event().is_closed is True
    assert event_gw.event().updated_at == FIXED_NOW


def test_snapshot_autopicks_and_materializes(event_gw):
    """spec/56: placing a snapshot IS the intent — it is born picked (edit
    phase), virtual, and Export later fills its file identity."""
    s2 = event_gw.create_video_snapshot("i-video", 7000, item_id="i-snap2")
    it = event_gw.item(s2)
    assert it.provenance == "snapshot" and it.kind == "photo" and it.origin_relpath is None
    assert event_gw.phase_state(s2, "edit").state == "picked"
    assert [s.at_ms for s in event_gw.video_snapshots("i-video")] == [3000, 7000]
    # materialize -> no longer virtual
    event_gw.materialize(s2, origin_relpath="03/s2.jpg", sha256="z", byte_size=42, phase="edit")
    ms = event_gw.item(s2)
    assert ms.origin_relpath == "03/s2.jpg" and ms.byte_size == 42 and ms.materialized_phase == "edit"
    # delete_child cascades the point satellite + phase_state
    event_gw.delete_child(s2)
    assert event_gw.item(s2) is None
    assert [s.at_ms for s in event_gw.video_snapshots("i-video")] == [3000]
    # validation: beyond the probed duration
    with pytest.raises(ValueError):
        event_gw.create_video_snapshot("i-video", 999_999_999)


def test_snapshot_inherits_video_classification(event_gw):
    """spec/58 (Nelson 2026-06-11): snapshots sit outside the
    captured-only background pass — they inherit the source video's
    classification at creation so Edit's Style badge is honest."""
    event_gw.set_classification(
        "i-video", "wildlife", "auto",
        rules_version="r9", needs_review=False, confidence=0.83)
    sid = event_gw.create_video_snapshot("i-video", 5000, item_id="i-snap-c")
    it = event_gw.item(sid)
    assert it.classification == "wildlife"
    assert it.classification_source == "auto"
    assert it.classification_rules_version == "r9"
    assert it.classification_confidence == 0.83
    # An unclassified video births an unclassified snapshot — no invention.
    event_gw.set_classification("i-video", None, "auto")
    sid2 = event_gw.create_video_snapshot("i-video", 6000, item_id="i-snap-n")
    it2 = event_gw.item(sid2)
    assert it2.classification is None and it2.classification_confidence is None


def test_whole_video_is_the_single_segment_no_special_case(event_gw):
    """spec/56: whole-video export = the original single segment, picked.
    Lazy birth materialises ONE segment (zero markers) with an explicit
    default-Skip row immune to the settings-driven edit default."""
    # i-photo's day-1 sibling has no markers; use a fresh marker-less video.
    event_gw.store.upsert(
        m.Item(id="i-video2", kind="video", created_at=FIXED_NOW, provenance="captured",
               origin_relpath="00 - Captured/Day02/P2.MP4", sha256="d" * 64, byte_size=9,
               materialized_at=FIXED_NOW, materialized_phase="ingest",
               camera_id="G9M2", capture_time_raw="2026-04-02T10:00:00",
               duration_ms=60_000))
    segs = event_gw.ensure_video_segments("i-video2")
    assert [s.seg_index for s in segs] == [0]
    assert event_gw.segment_bounds("i-video2") == [(0, 60_000)]
    assert event_gw.phase_state(segs[0].item_id, "edit").state == "skipped"
    # ensure is idempotent
    again = event_gw.ensure_video_segments("i-video2")
    assert [s.item_id for s in again] == [s.item_id for s in segs]
    # picking the single segment = "export the whole video"
    event_gw.set_phase_state(segs[0].item_id, "edit", "picked")
    assert event_gw.phase_state(segs[0].item_id, "edit").state == "picked"


def test_clips_and_snapshots_inherit_video_day_and_offset_time(event_gw):
    """spec/56 / spec/61 — segment + snapshot items inherit the source video's
    ``day_number`` and a ``capture_time_corrected`` offset by their START on
    the timeline, so exported clips land in their day in chronological show
    order in a Cut (not bunched under the undated separator). Re-stamps after
    every marker op."""
    event_gw.store.upsert(m.Item(
        id="i-vid-ct", kind="video", created_at=FIXED_NOW, provenance="captured",
        origin_relpath="00 - Captured/Day02/P9.MP4", sha256="e" * 64, byte_size=9,
        materialized_at=FIXED_NOW, materialized_phase="ingest", camera_id="G9M2",
        capture_time_raw="2026-04-02T10:00:00",
        capture_time_corrected="2026-04-02T10:00:00",
        day_number=2, duration_ms=60_000))

    # Lazy birth: the single segment sits at the video's own day + time.
    segs = event_gw.ensure_video_segments("i-vid-ct")
    seg0 = event_gw.item(segs[0].item_id)
    assert seg0.day_number == 2
    assert seg0.capture_time_corrected == "2026-04-02T10:00:00"

    # A marker splits it; the right half starts 20 s in → offset capture time.
    event_gw.add_video_marker("i-vid-ct", 20_000)
    s0, s1 = (event_gw.item(s.item_id)
              for s in event_gw.video_segments("i-vid-ct"))
    assert s0.day_number == 2 and s1.day_number == 2
    assert s0.capture_time_corrected == "2026-04-02T10:00:00"
    assert s1.capture_time_corrected == "2026-04-02T10:00:20"

    # A snapshot at 5 s inherits the day + an exact offset time.
    sid = event_gw.create_video_snapshot("i-vid-ct", 5_000, item_id="i-snap-ct")
    snap = event_gw.item(sid)
    assert snap.day_number == 2
    assert snap.capture_time_corrected == "2026-04-02T10:00:05"

    # None are undated → no collapse under the Cut's undated separator.
    dated = {i.id for i in event_gw.items(day=2)}
    assert {"i-vid-ct", segs[0].item_id, sid}.issubset(dated)


def test_add_marker_splits_and_inherits(event_gw):
    """spec/56 locked rule: a marker inserted INSIDE a segment splits it; both
    halves inherit the parent's state + adjustments; later segments shift up
    with their rows untouched."""
    mk2 = event_gw.add_video_marker("i-video", 2_000)   # splits picked+adjusted seg 0
    segs = event_gw.video_segments("i-video")
    assert [s.seg_index for s in segs] == [0, 1, 2]
    assert event_gw.segment_bounds("i-video") == [
        (0, 2_000), (2_000, 4_000), (4_000, 125_000)]
    left, right, tail = (event_gw.item(s.item_id) for s in segs)
    # left half IS the original row; the right half is new and inherited
    assert left.id == "i-seg0"
    assert event_gw.phase_state(left.id, "edit").state == "picked"
    assert event_gw.phase_state(right.id, "edit").state == "picked"
    assert event_gw.video_adjustment(right.id).speed == 0.5          # inherited copy
    assert event_gw.video_adjustment(right.id).look == "deeper"
    # the old segment 1 slid to index 2, state untouched
    assert tail.id == "i-seg1"
    assert event_gw.phase_state(tail.id, "edit").state == "skipped"
    # validations: duplicate position, out of range
    with pytest.raises(ValueError):
        event_gw.add_video_marker("i-video", 2_000)
    with pytest.raises(ValueError):
        event_gw.add_video_marker("i-video", 0)
    with pytest.raises(ValueError):
        event_gw.add_video_marker("i-video", 125_000)
    # markers target source videos only
    with pytest.raises(ValueError):
        event_gw.add_video_marker("i-seg0", 1_000)
    assert mk2  # returned id is real


def test_move_marker_keeps_segment_identity(event_gw):
    """spec/56 locked rule: a segment keeps its Pick state + adjustments when
    its boundary markers MOVE — identity is marker-order position, not ms."""
    mk = event_gw.video_markers("i-video")[0]
    before = [(s.item_id,
               event_gw.phase_state(s.item_id, "edit").state)
              for s in event_gw.video_segments("i-video")]
    event_gw.move_video_marker(mk.id, 9_000)
    assert event_gw.segment_bounds("i-video") == [(0, 9_000), (9_000, 125_000)]
    after = [(s.item_id,
              event_gw.phase_state(s.item_id, "edit").state)
             for s in event_gw.video_segments("i-video")]
    assert after == before                      # rows, order and states untouched
    assert event_gw.video_adjustment("i-seg0").speed == 0.5
    # a move may not cross (or land on) a neighbour / leave (0, duration)
    event_gw.add_video_marker("i-video", 20_000)
    with pytest.raises(ValueError):
        event_gw.move_video_marker(mk.id, 20_000)
    with pytest.raises(ValueError):
        event_gw.move_video_marker(mk.id, 30_000)
    with pytest.raises(ValueError):
        event_gw.move_video_marker(mk.id, 0)


def test_delete_marker_merges_left_survives(event_gw):
    """spec/56 merge rule: removing the marker at order position p merges
    segments p and p+1 — the LEFT half's row/state/adjustments survive at
    position p, the right half's item is deleted, later segments shift down."""
    mk2 = event_gw.add_video_marker("i-video", 60_000)   # split i-seg1 (skipped)
    segs = event_gw.video_segments("i-video")
    assert [s.seg_index for s in segs] == [0, 1, 2]
    # make the halves distinguishable: pick the middle, leave the tail skipped
    event_gw.set_phase_state(segs[1].item_id, "edit", "picked")
    mk1 = event_gw.video_markers("i-video")[0]           # at 4000
    event_gw.delete_video_marker(mk1.id)
    merged = event_gw.video_segments("i-video")
    assert [s.seg_index for s in merged] == [0, 1]
    # left survivor is the original i-seg0 with its state + adjustments
    assert merged[0].item_id == "i-seg0"
    assert event_gw.phase_state("i-seg0", "edit").state == "picked"
    assert event_gw.video_adjustment("i-seg0").speed == 0.5
    # the right half (old middle) is gone; the tail slid down intact
    assert event_gw.segment_bounds("i-video") == [(0, 60_000), (60_000, 125_000)]
    assert event_gw.phase_state(merged[1].item_id, "edit").state == "skipped"
    # deleting the remaining marker leaves ONE whole-timeline segment
    event_gw.delete_video_marker(mk2)
    last = event_gw.video_segments("i-video")
    assert [s.seg_index for s in last] == [0] and last[0].item_id == "i-seg0"
    assert event_gw.segment_bounds("i-video") == [(0, 125_000)]


def test_set_budget_writes_event(event_gw):
    event_gw.set_budget(short_target_s=200, video_share=0.25)
    ev = event_gw.event()
    assert ev.budget_short_target_s == 200 and ev.budget_video_share == 0.25


def test_set_sharpness_persists_score(event_gw):
    """G10: set_sharpness stores the score via targeted UPDATE (no FK cascade)."""
    assert event_gw.phase_state("i-photo", "pick") is not None
    event_gw.set_sharpness("i-photo", 987.65)
    it = event_gw.item("i-photo")
    assert it.sharpness_score == pytest.approx(987.65)
    assert it.sharpness_metric == "lapvar_wf_v1"
    # Phase state must still be there (the FK cascade did NOT fire).
    assert event_gw.phase_state("i-photo", "pick") is not None


# --------------------------------------------------------------------------- #
# spec/52 + spec/61 retirement note: the entire Curate port (G1 / G2 / G6 —
# share_tag override of phase_day_progress, curate_workflow_with_status,
# discover_curate_items) is gone, and the spec/51-era photo_tag accessors
# retired unused. Cuts (spec/61) keep definitions + FILE-based membership in
# event.db (cut + cut_member → lineage); the cuts gateway facade and its
# tests land with the Cuts slices.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# spec/44 — events index classification cache + refresh_index_entry
# --------------------------------------------------------------------------- #


def test_make_entry_includes_classification_defaults():
    """A bare make_entry call carries the spec/44 defaults so an old caller
    that hasn't been updated yet still produces a well-formed row. spec/52
    retired event-level ``tags`` (Cuts replace event-tag membership)."""
    entry = make_entry(
        event_id="e", name="N", start_date=None, end_date=None, is_closed=False,
        event_root=PureWindowsPath("D:/lib/E"),
        photos_base_path=PureWindowsPath("D:/lib"),
    )
    assert entry["event_type"] == "unclassified"
    assert entry["event_subtype"] is None
    assert entry["description"] == ""
    assert "tags" not in entry


def test_make_entry_carries_classification_fields():
    entry = make_entry(
        event_id="e", name="N", start_date=None, end_date=None, is_closed=False,
        event_root=PureWindowsPath("D:/lib/E"),
        photos_base_path=PureWindowsPath("D:/lib"),
        event_type="trip",
        event_subtype="Two weeks",
        description="Costa Rica with the kids.",
    )
    assert entry["event_type"] == "trip"
    assert entry["event_subtype"] == "Two weeks"
    assert entry["description"] == "Costa Rica with the kids."


def test_create_event_propagates_classification_to_index(tmp_path):
    """The cache the dashboard reads must be populated at create time so the
    first render after create-event shows the right type/subtype/description."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    doc = m.EventDocument(event=m.Event(
        uuid="evt-class", name="Bird session",
        created_at=FIXED_NOW, updated_at=FIXED_NOW,
        start_date="2026-07-01",
        event_type="session",
        event_subtype="Birds",
        description="Hummingbird feeder, golden hour.",
    ))
    eg = gw.create_event(doc, base / "Bird session")
    try:
        entry = gw.index.get("evt-class")
        assert entry["event_type"] == "session"
        assert entry["event_subtype"] == "Birds"
        assert entry["description"] == "Hummingbird feeder, golden hour."
    finally:
        eg.close()


def test_refresh_index_entry_rewrites_classification_cache(tmp_path):
    """The single seam that keeps the dashboard cache current after a mutation
    to event.db (used by EventGateway.set_classification in step 4)."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    doc = m.EventDocument(event=m.Event(
        uuid="evt-rfr", name="Maria's birthday",
        created_at=FIXED_NOW, updated_at=FIXED_NOW,
        start_date="2026-08-01",
        event_type="unclassified",
    ))
    eg = gw.create_event(doc, base / "Maria")
    try:
        # Simulate a downstream mutation: update event.db directly + then
        # refresh the index. (Step 4 wraps this in set_classification.)
        eg.store.conn.execute(
            "UPDATE event SET event_type='occasion', event_subtype='Birthday', "
            "description='Maria turns 40.' WHERE id=1"
        )
    finally:
        eg.close()

    gw.refresh_index_entry("evt-rfr")

    entry = gw.index.get("evt-rfr")
    assert entry["event_type"] == "occasion"
    assert entry["event_subtype"] == "Birthday"
    assert entry["description"] == "Maria turns 40."
    # Path columns preserved across the refresh
    assert entry["event_relpath"] == "Maria"


def test_refresh_index_entry_warns_for_unknown_id(tmp_path, caplog):
    """Refreshing a non-indexed id is a no-op + warning, not an exception."""
    import logging as _logging
    gw = _gateway(tmp_path, tmp_path / "lib")
    with caplog.at_level(_logging.WARNING):
        gw.refresh_index_entry("never-existed")
    assert gw.index.get("never-existed") is None
    assert any("never-existed" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# spec/44 — events_index_filtered + set_classification
# --------------------------------------------------------------------------- #


def _populated_library(tmp_path):
    """A small library with mixed types/subtypes/years for the filter tests.
    spec/52: event-level tags retired; search now indexes name + description."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    seeds = [
        # (uuid, name, start_date, is_closed, type, subtype, description)
        ("e-cr",   "Costa Rica",    "2026-04-01", False, "trip",     "Two weeks", "Birds + monkeys. wildlife tropical"),
        ("e-bds",  "Hummingbirds",  "2026-05-12", False, "session",  "Birds",     "Backyard feeder. birds macro"),
        ("e-wed",  "Maria wedding", "2025-09-21", True,  "occasion", "Wedding",   "Family + friends."),
        ("e-prj",  "Lighthouse",    "2025-06-01", False, "project",  "Series",    "Every Sunday at sunrise."),
        ("e-old",  "Untyped",       "2024-11-30", False, "unclassified", None,    ""),
        ("e-sess2","Macro morning", "2026-03-04", False, "session",  "Macro",     "Dewdrops in the lawn. macro tropical"),
    ]
    for uuid, name, sd, closed, typ, sub, desc in seeds:
        doc = m.EventDocument(event=m.Event(
            uuid=uuid, name=name,
            created_at=FIXED_NOW, updated_at=FIXED_NOW,
            start_date=sd, is_closed=closed,
            event_type=typ, event_subtype=sub,
            description=desc,
        ))
        gw.create_event(doc, base / uuid).close()
    return gw


def test_events_index_filtered_unfiltered_returns_all(tmp_path):
    gw = _populated_library(tmp_path)
    result = gw.events_index_filtered(EventsQuery())
    assert isinstance(result, EventsListing)
    assert {r["id"] for r in result.rows} == {
        "e-cr", "e-bds", "e-wed", "e-prj", "e-old", "e-sess2",
    }


def test_events_index_filtered_type_counts_cover_unfiltered_catalog(tmp_path):
    """Chip labels show "Trip (4)" — counts always over the unfiltered set so
    the chip row stays stable while the user types."""
    gw = _populated_library(tmp_path)
    result = gw.events_index_filtered(EventsQuery(type="trip"))   # filter narrows rows…
    assert result.type_counts["trip"] == 1
    assert result.type_counts["session"] == 2
    assert result.type_counts["occasion"] == 1
    assert result.type_counts["project"] == 1
    assert result.type_counts["unclassified"] == 1
    # …but rows narrow as expected
    assert {r["id"] for r in result.rows} == {"e-cr"}


def test_events_index_filtered_status_open_closed(tmp_path):
    gw = _populated_library(tmp_path)
    closed = gw.events_index_filtered(EventsQuery(status=True))
    assert {r["id"] for r in closed.rows} == {"e-wed"}
    open_ = gw.events_index_filtered(EventsQuery(status=False))
    assert "e-wed" not in {r["id"] for r in open_.rows}


def test_events_index_filtered_subtype_multiselect(tmp_path):
    gw = _populated_library(tmp_path)
    r = gw.events_index_filtered(EventsQuery(type="session", subtypes=["Birds", "Macro"]))
    assert {r["id"] for r in r.rows} == {"e-bds", "e-sess2"}


def test_events_index_filtered_year(tmp_path):
    gw = _populated_library(tmp_path)
    r = gw.events_index_filtered(EventsQuery(year=2025))
    assert {r["id"] for r in r.rows} == {"e-wed", "e-prj"}
    # year_options derived from all rows in the catalog, descending
    assert r.year_options == [2026, 2025, 2024]


def test_events_index_filtered_search_matches_name_and_description(tmp_path):
    """spec/52 retired event-level tags. Search now hits name + description only."""
    gw = _populated_library(tmp_path)
    # name match
    by_name = gw.events_index_filtered(EventsQuery(search="costa"))
    assert {r["id"] for r in by_name.rows} == {"e-cr"}
    # description match
    by_desc = gw.events_index_filtered(EventsQuery(search="dewdrops"))
    assert {r["id"] for r in by_desc.rows} == {"e-sess2"}
    # description match for a former-tag word (now baked into description text)
    by_word = gw.events_index_filtered(EventsQuery(search="wildlife"))
    assert {r["id"] for r in by_word.rows} == {"e-cr"}
    # multi-token AND: each token must appear somewhere across name/description
    by_multi = gw.events_index_filtered(EventsQuery(search="macro tropical"))
    assert {r["id"] for r in by_multi.rows} == {"e-sess2"}


def test_events_index_filtered_sort_newest_default(tmp_path):
    gw = _populated_library(tmp_path)
    r = gw.events_index_filtered(EventsQuery())
    ids_by_start = [(row["id"], row.get("start_date") or "") for row in r.rows]
    starts = [s for _, s in ids_by_start]
    assert starts == sorted(starts, reverse=True)


def test_events_index_filtered_sort_name(tmp_path):
    gw = _populated_library(tmp_path)
    r = gw.events_index_filtered(EventsQuery(sort="name"))
    names = [row["name"] for row in r.rows]
    assert names == sorted(names, key=str.lower)


def test_events_index_filtered_does_not_open_any_event_db(tmp_path, monkeypatch):
    """Filter responsiveness invariant: per-keystroke filter MUST NOT open
    any event.db — that's what the denormalised cache is for (review's
    finding #2 / #6 patterns)."""
    gw = _populated_library(tmp_path)
    opens: list[str] = []
    real_open = gw.open_event
    monkeypatch.setattr(gw, "open_event", lambda eid: opens.append(eid) or real_open(eid))
    gw.events_index_filtered(EventsQuery(search="bird", type="session"))
    assert opens == []


# ── set_classification ─────────────────────────────────────────────────────


def test_set_classification_updates_event_row(tmp_path):
    gw = _populated_library(tmp_path)
    gw.set_classification(
        "e-old",
        event_type="session",
        event_subtype="Lightning",
        description="Storm chasing in November.",
    )
    eg = gw.open_event("e-old")
    try:
        ev = eg.event()
        assert ev.event_type == "session"
        assert ev.event_subtype == "Lightning"
        assert ev.description == "Storm chasing in November."
    finally:
        eg.close()


def test_set_classification_refreshes_index_cache(tmp_path):
    """The dashboard cache must reflect the change immediately so the next
    events_index_filtered call sees the new type/subtype."""
    gw = _populated_library(tmp_path)
    gw.set_classification("e-old", event_type="project", event_subtype="Documentary")
    entry = gw.index.get("e-old")
    assert entry["event_type"] == "project"
    assert entry["event_subtype"] == "Documentary"


def test_set_classification_rejects_unknown_event_type(tmp_path):
    """Validation lives at the gateway boundary — silent coercion would mask
    bugs in the dialog code that produces the value."""
    gw = _populated_library(tmp_path)
    with pytest.raises(ValueError):
        gw.set_classification("e-old", event_type="happening")


def test_set_classification_partial_update_preserves_other_fields(tmp_path):
    """A None kwarg leaves the corresponding field unchanged."""
    gw = _populated_library(tmp_path)
    gw.set_classification("e-cr", description="Updated description only.")
    eg = gw.open_event("e-cr")
    try:
        ev = eg.event()
        assert ev.event_type == "trip"                     # untouched
        assert ev.event_subtype == "Two weeks"              # untouched
        assert ev.description == "Updated description only."
    finally:
        eg.close()


def test_set_classification_empty_subtype_clears(tmp_path):
    gw = _populated_library(tmp_path)
    gw.set_classification("e-cr", event_subtype="")
    eg = gw.open_event("e-cr")
    try:
        assert eg.event().event_subtype is None
    finally:
        eg.close()


def test_set_classification_extras_updates_shallow_merge(tmp_path):
    """Classification extras must not clobber IPTC location facets stored in the
    same extras_json blob (spec/44 §1.6 — shared bag, two namespaces)."""
    import json as _json
    gw = _populated_library(tmp_path)
    # Seed IPTC location extras directly via event.db (the wizard does this in real life).
    eg = gw.open_event("e-cr")
    try:
        eg.store.conn.execute(
            "UPDATE event SET extras_json = ? WHERE id = 1",
            (_json.dumps({"city": "Quepos", "country_code": "CR"}),),
        )
    finally:
        eg.close()
    gw.set_classification(
        "e-cr",
        extras_updates={"countries": ["CR"], "duration_label": "two_weeks", "people": ["Nelson"]},
    )
    eg = gw.open_event("e-cr")
    try:
        extras = _json.loads(eg.event().extras_json)
        # Both namespaces survive
        assert extras["city"] == "Quepos"
        assert extras["country_code"] == "CR"
        assert extras["countries"] == ["CR"]
        assert extras["duration_label"] == "two_weeks"
        assert extras["people"] == ["Nelson"]
    finally:
        eg.close()


def test_set_classification_no_op_when_all_args_none(tmp_path):
    """All-None call is a fast no-op — no UPDATE statement runs, updated_at
    is not bumped, and the index cache is not refreshed."""
    gw = _populated_library(tmp_path)
    before = gw.index.get("e-cr").copy()
    gw.set_classification("e-cr")
    assert gw.index.get("e-cr") == before


# --------------------------------------------------------------------------- #
# Structured event qualifiers (spec/64 — supersedes the spec/52
# Scope/Mood/Transport vocabulary)
# --------------------------------------------------------------------------- #


def test_create_event_carries_new_qualifier_columns(tmp_path):
    """Direct columns round-trip cleanly when set on the EventDocument
    passed to create_event — no extras_json detour needed."""
    import json as _json
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    doc = m.EventDocument(event=m.Event(
        uuid="evt-q", name="Argentina 2025",
        created_at=FIXED_NOW, updated_at=FIXED_NOW,
        start_date="2025-09-27",
        event_type="trip",
        event_subtype="Cultural",
        description="Salta + Jujuy",
        duration_value=2,
        duration_unit="weeks",
        participants=_json.dumps(["Couple", "With Friends"]),
        context="leisure",
        experience_type="expedition_discovery",
        creative_focus=_json.dumps(["wildlife", "landscape"]),
    ))
    eg = gw.create_event(doc, base / "Argentina 2025")
    try:
        ev = eg.event()
        assert ev.duration_value == 2
        assert ev.duration_unit == "weeks"
        assert _json.loads(ev.participants) == ["Couple", "With Friends"]
        assert ev.context == "leisure"
        assert ev.experience_type == "expedition_discovery"
        assert _json.loads(ev.creative_focus) == ["wildlife", "landscape"]
    finally:
        eg.close()


def test_set_classification_round_trips_new_qualifiers(tmp_path):
    """gateway.set_classification accepts each new field and persists it."""
    import json as _json
    gw = _populated_library(tmp_path)
    gw.set_classification(
        "e-cr",
        duration_value=10,
        duration_unit="days",
        participants=["Solo"],
        context="leisure",
        experience_type="slow_down",
        creative_focus=["macro", "birds"],
    )
    eg = gw.open_event("e-cr")
    try:
        ev = eg.event()
        assert ev.duration_value == 10
        assert ev.duration_unit == "days"
        assert _json.loads(ev.participants) == ["Solo"]
        assert ev.context == "leisure"
        assert ev.experience_type == "slow_down"
        assert _json.loads(ev.creative_focus) == ["macro", "birds"]
    finally:
        eg.close()


def test_set_classification_rejects_unknown_enum_values(tmp_path):
    """duration_unit / participants / context / experience_type /
    creative_focus are all closed enums per spec/64. The gateway raises
    instead of silently coercing — bad upstream code surfaces early."""
    gw = _populated_library(tmp_path)
    with pytest.raises(ValueError, match="duration_unit"):
        gw.set_classification("e-cr", duration_unit="fortnights")
    with pytest.raises(ValueError, match="participant"):
        gw.set_classification("e-cr", participants=["With Aliens"])
    with pytest.raises(ValueError, match="context"):
        gw.set_classification("e-cr", context="cosmic_vibes")
    with pytest.raises(ValueError, match="experience_type"):
        gw.set_classification("e-cr", experience_type="time_travel")
    with pytest.raises(ValueError, match="creative_focus"):
        gw.set_classification("e-cr", creative_focus=["birds", "aliens"])


def test_set_classification_accepts_creative_focus_none(tmp_path):
    """``["none"]`` is the explicit "this was not a photo event" answer
    (spec/64 §3.4); it's a valid creative_focus member, not a special
    sentinel at the gateway."""
    import json as _json
    gw = _populated_library(tmp_path)
    gw.set_classification("e-cr", creative_focus=["none"])
    eg = gw.open_event("e-cr")
    try:
        assert _json.loads(eg.event().creative_focus) == ["none"]
    finally:
        eg.close()


def test_set_classification_clears_qualifiers_with_falsy_value(tmp_path):
    """Empty string / 0 / [] clear the field back to NULL / '[]' so the user
    can wipe a previously-set value without dropping all the others."""
    import json as _json
    gw = _populated_library(tmp_path)
    gw.set_classification(
        "e-cr",
        duration_value=10, duration_unit="days",
        participants=["Solo"],
        context="leisure",
        experience_type="slow_down",
        creative_focus=["macro"],
    )
    gw.set_classification(
        "e-cr",
        duration_value=0, duration_unit="",
        participants=[],
        context="",
        experience_type="",
        creative_focus=[],
    )
    eg = gw.open_event("e-cr")
    try:
        ev = eg.event()
        assert ev.duration_value is None
        assert ev.duration_unit is None
        assert _json.loads(ev.participants) == []
        assert ev.context is None
        assert ev.experience_type is None
        assert _json.loads(ev.creative_focus) == []
    finally:
        eg.close()
