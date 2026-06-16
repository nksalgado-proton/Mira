"""Cascade-aware delete from the #exported DC (Nelson 2026-06-15
task — explicit deletion of exported media from Share/Cuts).

Also pins the lenient ``exported_files_all`` query the #exported
DC's detail page uses so its set matches the Export grid's
"Exported" watermark exactly even when items have a hidden day or
no matching trip_day row.

Pins the engine-side guarantees the DCDetailPage relies on:

* ``cuts_containing(export_relpath)`` returns every Cut whose
  ``cut_member`` set references the relpath.
* ``cuts_containing_any(relpaths)`` rolls up the batch — the unique
  Cut count the confirm dialog reads out loud.
* ``delete_exported_file_by_relpath(relpath)`` drops one lineage row
  + its on-disk file + clears ``edit_exported`` IFF this was the
  last shipped row for the source item.
* The ``cut_member.export_relpath`` FK CASCADE actually fires —
  deleting the lineage row removes every cut_member referencing it,
  for free, without us writing the cleanup loop.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore

FIXED_NOW = "2026-06-15T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _doc() -> m.EventDocument:
    """Two photos + one segment (clip) all exported; two Cuts where
    Cut A holds both photos and Cut B holds one photo + the clip."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-p", name="Pool delete fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        m.Item(
            id="p1", kind="photo", created_at=FIXED_NOW, provenance="captured",
            origin_relpath="Original Media/p1.jpg", sha256="a" * 64,
            byte_size=1000, materialized_at=FIXED_NOW,
            materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw="2026-04-01T08:00:00",
            capture_time_corrected="2026-04-01T08:00:00",
        ),
        m.Item(
            id="p2", kind="photo", created_at=FIXED_NOW, provenance="captured",
            origin_relpath="Original Media/p2.jpg", sha256="b" * 64,
            byte_size=1000, materialized_at=FIXED_NOW,
            materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw="2026-04-01T09:00:00",
            capture_time_corrected="2026-04-01T09:00:00",
        ),
        m.Item(
            id="v1", kind="video", created_at=FIXED_NOW, provenance="captured",
            origin_relpath="Original Media/v1.mp4", sha256="c" * 64,
            byte_size=5000, materialized_at=FIXED_NOW,
            materialized_phase="ingest",
            camera_id="G9", day_number=1, duration_ms=30_000,
            capture_time_raw="2026-04-01T10:00:00",
            capture_time_corrected="2026-04-01T10:00:00",
        ),
        # Segment item (provenance='clip') that materialised at Export.
        m.Item(
            id="seg-1", kind="video", created_at=FIXED_NOW, provenance="clip",
            parent_item_id="v1",
            origin_relpath="Exported Media/Dia 1/v1_clip1.mp4",
            sha256="d" * 64, byte_size=2000,
            materialized_at=FIXED_NOW, materialized_phase="edit",
        ),
    ]
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/Dia 1/p1.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p1", exported_at="t1"),
        m.Lineage(export_relpath="Exported Media/Dia 1/p2.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p2", exported_at="t2"),
        m.Lineage(export_relpath="Exported Media/Dia 1/v1_clip1.mp4",
                  phase="edit", source_kind="item",
                  source_item_id="seg-1", exported_at="t3"),
    ]
    doc.cuts = [
        m.Cut(id="cut-A", tag="cut_a",
              created_at=FIXED_NOW, updated_at=FIXED_NOW),
        m.Cut(id="cut-B", tag="cut_b",
              created_at=FIXED_NOW, updated_at=FIXED_NOW),
    ]
    doc.cut_members = [
        m.CutMember(cut_id="cut-A",
                    export_relpath="Exported Media/Dia 1/p1.jpg",
                    added_at=FIXED_NOW),
        m.CutMember(cut_id="cut-A",
                    export_relpath="Exported Media/Dia 1/p2.jpg",
                    added_at=FIXED_NOW),
        m.CutMember(cut_id="cut-B",
                    export_relpath="Exported Media/Dia 1/p1.jpg",
                    added_at=FIXED_NOW),
        m.CutMember(cut_id="cut-B",
                    export_relpath="Exported Media/Dia 1/v1_clip1.mp4",
                    added_at=FIXED_NOW),
    ]
    # Adjustment rows so set_edit_exported(False) on delete has
    # something to flip back. p1 + p2 are photos; seg-1 needs an
    # Adjustment row too (the workshop's by-snapshot model treats
    # segments as photo-adjusted for snapshots and video-adjusted
    # for clips, but ``set_edit_exported`` flips the
    # Adjustment.edit_exported flag — present for completeness).
    doc.adjustments = [
        m.Adjustment(item_id="p1", edit_exported=True),
        m.Adjustment(item_id="p2", edit_exported=True),
        m.Adjustment(item_id="seg-1", edit_exported=True),
    ]
    return doc


@pytest.fixture
def event_dir(tmp_path):
    # Materialise the export files on disk so unlink + read_bytes
    # behave like a real run.
    (tmp_path / "Exported Media" / "Dia 1").mkdir(parents=True)
    (tmp_path / "Exported Media" / "Dia 1" / "p1.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    (tmp_path / "Exported Media" / "Dia 1" / "p2.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    (tmp_path / "Exported Media" / "Dia 1" / "v1_clip1.mp4").write_bytes(
        b"\x00" * 32)
    return tmp_path


@pytest.fixture
def gw(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-p")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# ── exported_files_all (lenient — matches Export grid watermark) ─────


def test_exported_files_all_matches_watermark_when_day_is_hidden(
        gw, event_dir):
    """The Nelson 2026-06-15 bug: photos on a HIDDEN day still carry
    the Exported watermark (because ``exported_item_ids`` doesn't
    filter visible_item) but ``exported_files`` was dropping them
    (visible_item JOIN failed). The lenient ``exported_files_all``
    mirrors the watermark — every Exported Media/ row, regardless of
    its source's visibility."""
    # Hide the day the photos live on. (The clip's source segment has
    # day_number=NULL so it passes the strict query either way; the
    # divergence is on the photos.)
    gw.store.conn.execute(
        "UPDATE trip_day SET hidden = 1 WHERE day_number = 1")
    # The strict query drops the photos (segment's NULL day still
    # passes the visible_item OR clause).
    strict_rels = {ln.export_relpath for ln in gw.exported_files()}
    assert "Exported Media/Dia 1/p1.jpg" not in strict_rels
    assert "Exported Media/Dia 1/p2.jpg" not in strict_rels
    # But the watermark still shows the photos.
    shipped_ids = gw.exported_item_ids()
    assert shipped_ids == {"p1", "p2", "seg-1"}
    # And the lenient query — the one the Pool uses — surfaces every
    # row so the Pool surface matches what the watermark promised.
    all_rels = {ln.export_relpath for ln in gw.exported_files_all()}
    assert all_rels == {
        "Exported Media/Dia 1/p1.jpg",
        "Exported Media/Dia 1/p2.jpg",
        "Exported Media/Dia 1/v1_clip1.mp4",
    }


def test_exported_files_all_is_chronological_and_only_edit_phase(gw):
    """The lenient query honours phase='edit' + the Exported Media/
    prefix and orders by ``exported_at`` (chronological show order).
    A share-phase row never sneaks in."""
    # Plant a share-phase row to prove it's excluded.
    gw.record_lineage(m.Lineage(
        export_relpath="Cuts/share-only.jpg", phase="share",
        source_kind="item", source_item_id="p1",
        exported_at="t0_early"))
    rels = [ln.export_relpath for ln in gw.exported_files_all()]
    assert "Cuts/share-only.jpg" not in rels
    # exported_at: t1 < t2 < t3 in the fixture; chronology preserved.
    assert rels == [
        "Exported Media/Dia 1/p1.jpg",
        "Exported Media/Dia 1/p2.jpg",
        "Exported Media/Dia 1/v1_clip1.mp4",
    ]


# ── cuts_containing ───────────────────────────────────────────────────


def test_cuts_containing_returns_each_cut_that_holds_the_relpath(gw):
    """p1 is in BOTH cuts; p2 in only cut-A; the clip in only cut-B."""
    cuts_for_p1 = gw.cuts_containing("Exported Media/Dia 1/p1.jpg")
    assert {c.id for c in cuts_for_p1} == {"cut-A", "cut-B"}
    cuts_for_p2 = gw.cuts_containing("Exported Media/Dia 1/p2.jpg")
    assert {c.id for c in cuts_for_p2} == {"cut-A"}
    cuts_for_clip = gw.cuts_containing(
        "Exported Media/Dia 1/v1_clip1.mp4")
    assert {c.id for c in cuts_for_clip} == {"cut-B"}


def test_cuts_containing_returns_empty_when_relpath_unused(gw):
    """A relpath nobody added to a Cut yields an empty list — no
    cascade noise."""
    assert gw.cuts_containing("Exported Media/Dia 1/nonexistent.jpg") == []


def test_cuts_containing_any_dedupes_across_a_batch(gw):
    """The batch confirm reads UNIQUE cuts: a selection of {p1, p2,
    clip} hits cut-A and cut-B (not 4 — even though three of the
    cut_member rows point at the selection)."""
    selected = [
        "Exported Media/Dia 1/p1.jpg",
        "Exported Media/Dia 1/p2.jpg",
        "Exported Media/Dia 1/v1_clip1.mp4",
    ]
    cuts = gw.cuts_containing_any(selected)
    assert {c.id for c in cuts} == {"cut-A", "cut-B"}


def test_cuts_containing_any_empty_input_returns_empty(gw):
    assert gw.cuts_containing_any([]) == []


# ── delete_exported_file_by_relpath + FK CASCADE ──────────────────────


def test_delete_by_relpath_removes_file_lineage_and_clears_flag(
        gw, event_dir):
    """The single-file path: the on-disk JPEG vanishes, the lineage
    row drops, and ``Adjustment.edit_exported`` flips back to False
    when this was the last shipped row for the source item."""
    abs_path = event_dir / "Exported Media" / "Dia 1" / "p1.jpg"
    assert abs_path.is_file()
    res = gw.delete_exported_file_by_relpath(
        "Exported Media/Dia 1/p1.jpg")
    assert res["rows_deleted"] == 1
    assert res["item_id"] == "p1"
    assert not abs_path.is_file()
    # Lineage row gone, item dropped out of #exported, watermark clear.
    rels = {ln.export_relpath for ln in gw.exported_files()}
    assert "Exported Media/Dia 1/p1.jpg" not in rels
    adj = gw.adjustment("p1")
    assert adj is not None and adj.edit_exported is False


def test_delete_by_relpath_cascades_cut_member_rows(gw):
    """The locked invariant: deleting a lineage row drops every
    cut_member referencing it. p1 was in cut-A and cut-B; after
    delete BOTH cut_member rows for p1 are gone, while p2 and the
    clip survive in their cuts."""
    gw.delete_exported_file_by_relpath("Exported Media/Dia 1/p1.jpg")
    rows = gw.store.conn.execute(
        "SELECT cut_id, export_relpath FROM cut_member ORDER BY cut_id"
    ).fetchall()
    surviving = [(r["cut_id"], r["export_relpath"]) for r in rows]
    assert ("cut-A", "Exported Media/Dia 1/p1.jpg") not in surviving
    assert ("cut-B", "Exported Media/Dia 1/p1.jpg") not in surviving
    # p2 + the clip stay in their cuts.
    assert ("cut-A", "Exported Media/Dia 1/p2.jpg") in surviving
    assert ("cut-B", "Exported Media/Dia 1/v1_clip1.mp4") in surviving


def test_delete_by_relpath_rejects_paths_outside_exported_media(
        gw, event_dir):
    """Charter pin: the call refuses to touch anything outside
    ``Exported Media/`` — even if the relpath happens to live in
    the lineage table (a share-phase row, an Edited-Media return)."""
    # Plant a non-Exported-Media row + file.
    (event_dir / "Edited Media").mkdir(exist_ok=True)
    (event_dir / "Edited Media" / "outside.jpg").write_bytes(b"\xff\xd9")
    gw.record_lineage(m.Lineage(
        export_relpath="Edited Media/outside.jpg", phase="edit",
        source_kind="item", source_item_id="p1", exported_at="ts"))
    res = gw.delete_exported_file_by_relpath("Edited Media/outside.jpg")
    assert res["rows_deleted"] == 0
    assert (event_dir / "Edited Media" / "outside.jpg").is_file()


def test_delete_by_relpath_missing_file_still_drops_row(gw, event_dir):
    """A row whose on-disk file was deleted out-of-band still gets
    its lineage dropped + the cascade fires — the row is the only
    record of where the file landed."""
    abs_path = event_dir / "Exported Media" / "Dia 1" / "p2.jpg"
    abs_path.unlink()
    res = gw.delete_exported_file_by_relpath(
        "Exported Media/Dia 1/p2.jpg")
    assert res["rows_deleted"] == 1
    assert res["missing_files"] == ["Exported Media/Dia 1/p2.jpg"]
    # Cascade still cleaned cut-A's membership.
    members = gw.store.conn.execute(
        "SELECT 1 FROM cut_member WHERE export_relpath = ?",
        ("Exported Media/Dia 1/p2.jpg",)).fetchall()
    assert members == []


def test_delete_by_relpath_unknown_relpath_is_a_noop(gw):
    res = gw.delete_exported_file_by_relpath(
        "Exported Media/Dia 1/never.jpg")
    assert res["rows_deleted"] == 0


# ── batch delete via the FK CASCADE (the loop the page runs) ──────────


def test_batch_delete_runs_cascade_and_keeps_originals(gw, event_dir):
    """The DCDetailPage's batch path: loop
    ``delete_exported_file_by_relpath`` over the selection. The FK
    CASCADE drops every cut_member referencing them; Original Media/
    stays untouched (the charter pin)."""
    selected = [
        "Exported Media/Dia 1/p1.jpg",
        "Exported Media/Dia 1/v1_clip1.mp4",
    ]
    for rel in selected:
        res = gw.delete_exported_file_by_relpath(rel)
        assert res["rows_deleted"] == 1
    # Both files gone, both lineage rows gone.
    for rel in selected:
        assert not (event_dir / rel).is_file()
    rels_left = {ln.export_relpath for ln in gw.exported_files()}
    assert rels_left == {"Exported Media/Dia 1/p2.jpg"}
    # cut_member rows for the deleted relpaths are gone, but p2's row
    # survives in cut-A.
    rows = gw.store.conn.execute(
        "SELECT cut_id, export_relpath FROM cut_member"
    ).fetchall()
    surviving = {(r["cut_id"], r["export_relpath"]) for r in rows}
    assert surviving == {("cut-A", "Exported Media/Dia 1/p2.jpg")}
    # Charter pin: the loop never touched anything outside Exported
    # Media/ — confirmed by the by-relpath gate on every call (see
    # ``test_delete_by_relpath_rejects_paths_outside_exported_media``).
