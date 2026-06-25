"""spec/147 §4 — Delete confirm warns + cleans up Cut usage in BOTH
DBs (event + library).

When the user fires Delete now · M (or Delete this), the doomed files
might still be in:

* **Event Cuts** — event.db ``cut_member`` rows with
  ``event_id IS NULL``. Already cascade-cleared inside
  :meth:`EventGateway.delete_exported_file_by_relpath` (the per-file
  delete worker).
* **Cross-event Cuts** — library user_store ``cut_member`` rows with
  ``event_id`` = this event's uuid. NEW: a parallel sweep on the
  library DB must drop those rows so no cross-event Cut is left
  with a dangling frame.

The confirm modal sums both warn counts. Tests pinned here:

* :meth:`EventGateway.event_cut_usage_count` counts distinct event
  Cuts referencing any of the doomed relpaths.
* :meth:`LibraryGateway.cross_event_cut_usage_count` counts distinct
  cross-event Cuts referencing any ``(event_uuid, relpath)`` pair.
* On delete, the event-scope ``cut_member`` rows go (existing
  contract); the new
  :meth:`LibraryGateway.delete_cross_event_cut_members` removes the
  cross-event rows so nothing dangles.
* Each helper returns 0 / is a no-op on empty input or unknown
  event_uuid (the toolbar's zero-state disable contract relies on
  this).
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.gateway.library_gateway import LibraryGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.user_store.repo import UserStore

NOW = "2026-06-25T00:00:00+00:00"


def _doc():
    doc = m.EventDocument(event=m.Event(
        uuid="evt-cut", name="Cut-usage fixture",
        created_at=NOW, updated_at=NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-06-25")]
    doc.cameras = [m.Camera(camera_id="cam")]
    doc.items = [
        m.Item(
            id=iid, kind="photo", created_at=NOW, provenance="captured",
            origin_relpath=f"Original Media/{iid}.jpg",
            sha256=f"s-{iid}", byte_size=1,
            materialized_at=NOW, materialized_phase="ingest",
            camera_id="cam", day_number=1,
            capture_time_raw="2026-06-25T08:00:00",
            capture_time_corrected="2026-06-25T08:00:00",
        )
        for iid in ("p1", "p2", "p3")
    ]
    # All Set aside — the Delete now target.
    doc.lineage = [
        m.Lineage(
            export_relpath=f"Exported Media/{iid}.jpg",
            phase="edit", source_kind="item", source_item_id=iid,
            exported_at="t1", intent_state="skipped")
        for iid in ("p1", "p2", "p3")
    ]
    doc.adjustments = [
        m.Adjustment(item_id=iid, edit_exported=True)
        for iid in ("p1", "p2", "p3")
    ]
    # Two event-scope Cuts referencing the doomed files. Cut A
    # contains p1+p2, Cut B contains p1+p3 — distinct cuts = 2,
    # member rows = 4.
    doc.cuts = [
        m.Cut(id="cut-A", tag="cut_a",
              created_at=NOW, updated_at=NOW),
        m.Cut(id="cut-B", tag="cut_b",
              created_at=NOW, updated_at=NOW),
    ]
    doc.cut_members = [
        m.CutMember(cut_id="cut-A",
                    export_relpath="Exported Media/p1.jpg",
                    added_at=NOW),
        m.CutMember(cut_id="cut-A",
                    export_relpath="Exported Media/p2.jpg",
                    added_at=NOW),
        m.CutMember(cut_id="cut-B",
                    export_relpath="Exported Media/p1.jpg",
                    added_at=NOW),
        m.CutMember(cut_id="cut-B",
                    export_relpath="Exported Media/p3.jpg",
                    added_at=NOW),
    ]
    return doc


@pytest.fixture
def event_dir(tmp_path):
    (tmp_path / "Exported Media").mkdir(parents=True)
    for iid in ("p1", "p2", "p3"):
        (tmp_path / "Exported Media" / f"{iid}.jpg").write_bytes(
            b"\xff\xd8\xff\xd9")
    return tmp_path


@pytest.fixture
def eg(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-cut")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=event_dir, now=lambda: NOW,
        new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def lg(tmp_path, eg):
    """A LibraryGateway with two cross-event Cuts that reference the
    event's files. cut-X contains p1+p2 (event evt-cut); cut-Y
    contains p3 (same event) PLUS a file from a different event so
    we can show the helper scopes to event_uuid + relpath."""
    us = UserStore.create(
        tmp_path / "mira.db", app_version="t", created_at=NOW)
    lib = LibraryGateway(us, now=lambda: NOW)
    cut_x = lib.create_cross_event_cut("show_x")
    cut_y = lib.create_cross_event_cut("show_y")
    lib.set_cross_event_cut_members(cut_x.id, [
        {"event_id": "evt-cut", "kind": "export",
         "export_relpath": "Exported Media/p1.jpg"},
        {"event_id": "evt-cut", "kind": "export",
         "export_relpath": "Exported Media/p2.jpg"},
    ])
    lib.set_cross_event_cut_members(cut_y.id, [
        {"event_id": "evt-cut", "kind": "export",
         "export_relpath": "Exported Media/p3.jpg"},
        # A frame from a DIFFERENT event — must NOT count or be touched.
        {"event_id": "evt-other", "kind": "export",
         "export_relpath": "Exported Media/q1.jpg"},
    ])
    yield lib
    us.close()


# --------------------------------------------------------------------- #
# 1. Event-scope warn count
# --------------------------------------------------------------------- #


def test_event_cut_usage_count_distinct_cuts(eg):
    """spec/147 §4 — the warn modal reads the DISTINCT event-Cut
    count (Cut A + Cut B reference p1; only 2, not 4). The number
    matters for honest UX: the user shouldn't see "4 Cuts affected"
    when only 2 exist."""
    rels = [
        "Exported Media/p1.jpg",
        "Exported Media/p2.jpg",
        "Exported Media/p3.jpg",
    ]
    assert eg.event_cut_usage_count(rels) == 2


def test_event_cut_usage_count_zero_on_empty_input(eg):
    """An empty list reads 0 — the toolbar's zero-M state skips the
    warn altogether (no Cut is in danger of going stale)."""
    assert eg.event_cut_usage_count([]) == 0
    assert eg.event_cut_usage_count(()) == 0


# --------------------------------------------------------------------- #
# 2. Cross-event warn count + cleanup
# --------------------------------------------------------------------- #


def test_cross_event_cut_usage_count_distinct_cuts(lg):
    """spec/147 §4 — counts DISTINCT cross-event Cuts that reference
    any ``(event_uuid, relpath)`` pair from the doomed set. cut-X
    contains two; cut-Y contains one — total 2 distinct Cuts."""
    rels = [
        "Exported Media/p1.jpg",
        "Exported Media/p2.jpg",
        "Exported Media/p3.jpg",
    ]
    assert lg.cross_event_cut_usage_count("evt-cut", rels) == 2


def test_cross_event_cut_usage_count_scoped_to_event_uuid(lg):
    """The helper takes the event_uuid + relpath PAIR — relpaths in
    a different event do NOT count. cut-Y's ``evt-other`` member
    must be invisible when we query ``evt-cut``."""
    # Querying evt-cut with a foreign relpath returns 0.
    assert lg.cross_event_cut_usage_count(
        "evt-cut", ["Exported Media/q1.jpg"]) == 0
    # And querying evt-other with the foreign relpath finds the
    # cut-Y row (one distinct Cut).
    assert lg.cross_event_cut_usage_count(
        "evt-other", ["Exported Media/q1.jpg"]) == 1


def test_cross_event_cut_usage_count_zero_on_empty_or_blank(lg):
    """Empty input or blank event_uuid → 0. The toolbar's zero-M
    disable contract relies on this being a clean no-op."""
    assert lg.cross_event_cut_usage_count("evt-cut", []) == 0
    assert lg.cross_event_cut_usage_count("", ["Exported Media/p1.jpg"]) == 0


# --------------------------------------------------------------------- #
# 3. On confirm — event AND library member rows removed
# --------------------------------------------------------------------- #


def test_event_member_rows_cascade_on_delete(eg):
    """spec/147 §4 — event-scope ``cut_member`` rows cascade-clear
    inside :meth:`delete_exported_file_by_relpath` (the existing
    contract). After deleting p1's file, cut-A + cut-B no longer
    reference it."""
    # Sanity: p1 is in both cuts.
    rows_before = eg.store.conn.execute(
        "SELECT cut_id FROM cut_member "
        "WHERE export_relpath = ? AND event_id IS NULL",
        ("Exported Media/p1.jpg",)).fetchall()
    assert {r["cut_id"] for r in rows_before} == {"cut-A", "cut-B"}

    eg.delete_exported_file_by_relpath("Exported Media/p1.jpg")

    rows_after = eg.store.conn.execute(
        "SELECT cut_id FROM cut_member "
        "WHERE export_relpath = ? AND event_id IS NULL",
        ("Exported Media/p1.jpg",)).fetchall()
    assert rows_after == []


def test_library_member_rows_swept_by_delete_cross_event_helper(lg):
    """spec/147 §4 — the LibraryGateway's new
    :meth:`delete_cross_event_cut_members` is the cross-event
    sweep. Calling it after :meth:`delete_exported_file_by_relpath`
    drops every library ``cut_member`` row keyed by
    ``(event_uuid, export_relpath)``."""
    rels = [
        "Exported Media/p1.jpg",
        "Exported Media/p2.jpg",
        "Exported Media/p3.jpg",
    ]
    removed = lg.delete_cross_event_cut_members("evt-cut", rels)
    # Three rows were keyed by evt-cut + a doomed relpath; the
    # foreign evt-other row stays put.
    assert removed == 3

    # Library DB: cross-event Cuts no longer reference the doomed
    # relpaths AT ALL for evt-cut.
    survivors = lg.user_store.conn.execute(
        "SELECT event_id, export_relpath FROM cut_member "
        "WHERE event_id = 'evt-cut'").fetchall()
    assert survivors == []
    # The foreign-event row from cut-Y survives.
    foreign = lg.user_store.conn.execute(
        "SELECT event_id, export_relpath FROM cut_member "
        "WHERE event_id = 'evt-other'").fetchall()
    assert {(r["event_id"], r["export_relpath"]) for r in foreign} == {
        ("evt-other", "Exported Media/q1.jpg"),
    }


def test_library_sweep_is_no_op_on_empty_input(lg):
    """spec/147 §4 — empty input → 0 rows removed, no UPDATE on
    the cut table. The toolbar's zero-M disable contract relies on
    this."""
    assert lg.delete_cross_event_cut_members("evt-cut", []) == 0
    assert lg.delete_cross_event_cut_members("", ["Exported Media/p1.jpg"]) == 0


def test_library_sweep_returns_zero_when_no_match(lg):
    """A relpath that's not in any cross-event Cut returns 0 and
    leaves the cut_member rows untouched."""
    removed = lg.delete_cross_event_cut_members(
        "evt-cut", ["Exported Media/never_existed.jpg"])
    assert removed == 0
    # The original 4 rows are still there.
    total = lg.user_store.conn.execute(
        "SELECT COUNT(*) FROM cut_member").fetchone()[0]
    assert total == 4
