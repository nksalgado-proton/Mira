"""Tests for ``mira.ingest.backfill.apply_edited_level`` —
spec/57 §4.3.1, the wizard's "Already edited" landing level: both
phases picked, ``Edited Media/Imported/`` hardlinks, lineage rows in
the external-return shape with ``exported_at`` = the backfill moment.
"""
from __future__ import annotations

import os
from pathlib import Path

from mira.gateway.event_gateway import EventGateway
from mira.ingest.backfill import apply_edited_level
from mira.store import models as m
from mira.store.repo import EventStore

NOW = "2026-06-10T18:00:00+00:00"


def _make_event(tmp_path, *rels) -> EventGateway:
    store = EventStore.create(tmp_path / "event.db", event_id="evt-bf")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-bf", name="BF", created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="G9"))
    store.upsert(m.TripDay(day_number=3, date="2026-04-03"))
    store.upsert(m.TripDay(day_number=4, date="2026-04-04"))
    for i, (rel, day) in enumerate(rels):
        src = tmp_path / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"bytes-%d" % i)
        store.upsert(m.Item(
            id=f"i{i}", kind="photo", created_at="t", provenance="captured",
            origin_relpath=rel, sha256=f"s{i}", byte_size=src.stat().st_size,
            materialized_at="t", materialized_phase="ingest",
            camera_id="G9", day_number=day,
            capture_time_raw=f"2026-04-0{day}T08:00:0{i}",
            capture_time_corrected=f"2026-04-0{day}T08:00:0{i}",
        ))
    return EventGateway(store, event_root=tmp_path, now=lambda: NOW)


def test_both_phases_written_picked(tmp_path):
    eg = _make_event(
        tmp_path, ("Original Media/_cameras/d3/G9/p1.jpg", 3))
    try:
        report = apply_edited_level(eg, tmp_path, now=NOW)
        assert report.ok and report.items == 1
        assert eg.phase_state("i0", "pick").state == "picked"
        assert eg.phase_state("i0", "edit").state == "picked"
    finally:
        eg.close()


def test_links_hardlinked_under_imported_with_lineage(tmp_path):
    eg = _make_event(
        tmp_path, ("Original Media/_cameras/d3/G9/p1.jpg", 3))
    try:
        report = apply_edited_level(eg, tmp_path, now=NOW)
        assert report.linked == 1 and report.lineage_rows == 1
        dest = tmp_path / "Edited Media" / "Imported" / "p1.jpg"
        src = tmp_path / "Original Media/_cameras/d3/G9/p1.jpg"
        assert dest.exists() and os.path.samefile(src, dest)
        rows = [ln for ln in eg.store.all(m.Lineage)]
        assert len(rows) == 1
        ln = rows[0]
        assert ln.export_relpath == "Edited Media/Imported/p1.jpg"
        assert ln.phase == "edit" and ln.source_kind == "item"
        assert ln.source_item_id == "i0"
        assert ln.recipe_json is None
        assert ln.exported_at == NOW
    finally:
        eg.close()


def test_same_name_across_days_diverts(tmp_path):
    eg = _make_event(
        tmp_path,
        ("Original Media/_cameras/d3/G9/p1.jpg", 3),
        ("Original Media/_cameras/d4/G9/p1.jpg", 4),
    )
    try:
        report = apply_edited_level(eg, tmp_path, now=NOW)
        assert report.ok and report.linked == 2
        imported = tmp_path / "Edited Media" / "Imported"
        assert {p.name for p in imported.iterdir()} == {
            "p1.jpg", "p1 (2).jpg"}
        rels = {ln.export_relpath for ln in eg.store.all(m.Lineage)}
        assert rels == {"Edited Media/Imported/p1.jpg",
                        "Edited Media/Imported/p1 (2).jpg"}
    finally:
        eg.close()


def test_rerun_is_idempotent(tmp_path):
    eg = _make_event(
        tmp_path,
        ("Original Media/_cameras/d3/G9/p1.jpg", 3),
        ("Original Media/_cameras/d4/G9/p1.jpg", 4),
    )
    try:
        apply_edited_level(eg, tmp_path, now=NOW)
        second = apply_edited_level(eg, tmp_path, now=NOW)
        assert second.ok
        assert second.lineage_rows == 0                 # nothing re-recorded
        imported = tmp_path / "Edited Media" / "Imported"
        assert len(list(imported.iterdir())) == 2       # no suffix spiral
        assert len(list(eg.store.all(m.Lineage))) == 2
    finally:
        eg.close()


def test_rerun_restores_a_deleted_link(tmp_path):
    eg = _make_event(
        tmp_path, ("Original Media/_cameras/d3/G9/p1.jpg", 3))
    try:
        apply_edited_level(eg, tmp_path, now=NOW)
        dest = tmp_path / "Edited Media" / "Imported" / "p1.jpg"
        dest.unlink()
        second = apply_edited_level(eg, tmp_path, now=NOW)
        assert second.ok and dest.exists()
        assert len(list(eg.store.all(m.Lineage))) == 1  # row unchanged
    finally:
        eg.close()


def test_missing_source_reported_others_proceed(tmp_path):
    eg = _make_event(
        tmp_path,
        ("Original Media/_cameras/d3/G9/p1.jpg", 3),
        ("Original Media/_cameras/d4/G9/p2.jpg", 4),
    )
    try:
        (tmp_path / "Original Media/_cameras/d3/G9/p1.jpg").unlink()
        report = apply_edited_level(eg, tmp_path, now=NOW)
        assert not report.ok and len(report.errors) == 1
        assert report.linked == 1                       # p2 still landed
        assert (tmp_path / "Edited Media" / "Imported" / "p2.jpg").exists()
        # States written for both regardless — the items exist in the DB.
        assert eg.phase_state("i0", "edit").state == "picked"
    finally:
        eg.close()
