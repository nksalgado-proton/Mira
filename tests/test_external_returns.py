"""Tests for the spec/57 §3 return seams — stacker adoption at the
Picked Media root, editor-return association under Edited Media, the
starts-with matching rule, and the derived unmerged-brackets fact."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.picked_media import rebuild_picked_media
from mira.gateway.event_gateway import EventGateway
from mira.picked.edit_model import picked_media_entries
from mira.picked.external_returns import scan_for_returns
from mira.store import models as m
from mira.store.repo import EventStore


# --------------------------------------------------------------------------- #
# Fixture — an event with a picked focus bracket + a loose picked photo
# --------------------------------------------------------------------------- #


def _make_event(tmp_path) -> EventGateway:
    store = EventStore.create(tmp_path / "event.db", event_id="evt-rt")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-rt", name="RT", created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="G9"))
    store.upsert(m.TripDay(day_number=3, date="2026-04-03"))

    def _item(iid, rel, ts):
        src = tmp_path / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"bytes-" + iid.encode())
        store.upsert(m.Item(
            id=iid, kind="photo", created_at="t", provenance="captured",
            origin_relpath=rel, sha256="s" + iid, byte_size=src.stat().st_size,
            materialized_at="t", materialized_phase="ingest",
            camera_id="G9", day_number=3,
            capture_time_raw=ts, capture_time_corrected=ts,
        ))
        store.upsert(m.PhaseState(item_id=iid, phase="pick", state="picked"))

    _item("i-b1", "Original Media/_cameras/d3/G9/b1.rw2", "2026-04-03T08:00:00")
    _item("i-b2", "Original Media/_cameras/d3/G9/b2.rw2", "2026-04-03T08:00:01")
    _item("i-solo", "Original Media/_cameras/d3/G9/p1.rw2", "2026-04-03T09:00:00")
    # The cached scanner cluster — the bracket the user saw in the grid.
    store.upsert(m.BucketCache(
        bucket_key="d3|focus|abc", phase="pick", kind="focus_bracket",
        day_number=3))
    store.upsert(m.BucketMember(
        bucket_key="d3|focus|abc", phase="pick", item_id="i-b1", ordinal=0))
    store.upsert(m.BucketMember(
        bucket_key="d3|focus|abc", phase="pick", item_id="i-b2", ordinal=1))
    return EventGateway(store, event_root=tmp_path,
                        now=lambda: "2026-06-10T15:00:00+00:00")


def _project(eg, tmp_path):
    rebuild_picked_media(tmp_path, picked_media_entries(eg, "skipped"))


# --------------------------------------------------------------------------- #
# Leg A — stacker adoption
# --------------------------------------------------------------------------- #


def test_stacker_output_adopts_as_bracket_master(tmp_path):
    eg = _make_event(tmp_path)
    try:
        _project(eg, tmp_path)
        sub = tmp_path / "Picked Media" / "d3-focus-abc"
        assert (sub / "D03_G9_b1.rw2").exists()      # bracket inputs ready
        # The stacker consumed the subdir and wrote its result at the ROOT,
        # named from its input links (so the stem carries the prefix).
        out = tmp_path / "Picked Media" / "D03_G9_b1-merged.tif"
        out.write_bytes(b"MERGED")

        report = scan_for_returns(eg, "skipped")
        assert report.adopted == ["D03_G9_b1-merged.tif"]
        assert not report.errors and not report.unmatched
        # Bytes moved into the sanctioned carve-out; root original gone.
        merged = tmp_path / "Original Media" / "Merged" / "D03_G9_b1-merged.tif"
        assert merged.read_bytes() == b"MERGED"
        assert not out.exists()
        # The DB has the bracket's final master, picked-by-construction.
        stacks = eg.stacks()
        assert len(stacks) == 1
        sb = stacks[0]
        assert sb.kind == "focus" and sb.action == "stacked"
        master = eg.item(sb.output_item_id)
        assert master is not None and master.provenance == "stack_output"
        assert master.day_number == 3 and master.camera_id == "G9"
        assert master.origin_relpath == "Original Media/Merged/D03_G9_b1-merged.tif"
        assert eg.phase_state(master.id, "pick").state == "picked"
        assert [sm.item_id for sm in eg.stack_members(sb.bracket_id)] == ["i-b1", "i-b2"]
        # The reminder fact clears once the bracket has its result.
        assert report.unmerged_bracket_count == 0
        # Seamless rider: the next rebuild links the master at the root.
        _project(eg, tmp_path)
        link = tmp_path / "Picked Media" / "D03_G9_D03_G9_b1-merged.tif"
        assert link.exists() and os.path.samefile(link, merged)
    finally:
        eg.close()


def test_adopted_master_inherits_anchor_classification(tmp_path):
    """spec/58 (Nelson 2026-06-11): merged masters sit outside the
    captured-only background pass — they inherit the anchor member's
    classification at adoption so Edit's Style badge is honest."""
    eg = _make_event(tmp_path)
    try:
        eg.set_classification(
            "i-b1", "macro", "auto", rules_version="r1", confidence=0.91)
        _project(eg, tmp_path)
        out = tmp_path / "Picked Media" / "D03_G9_b1-merged.dng"
        out.write_bytes(b"MERGED2")
        report = scan_for_returns(eg, "skipped")
        assert report.adopted == ["D03_G9_b1-merged.dng"]
        master = eg.item(eg.stacks()[0].output_item_id)
        assert master.classification == "macro"
        assert master.classification_source == "auto"
        assert master.classification_rules_version == "r1"
        assert master.classification_confidence == 0.91
    finally:
        eg.close()


def test_unmatched_root_file_is_flagged_and_untouched(tmp_path):
    eg = _make_event(tmp_path)
    try:
        _project(eg, tmp_path)
        stray = tmp_path / "Picked Media" / "random_thing.tif"
        stray.write_bytes(b"WHO KNOWS")
        report = scan_for_returns(eg, "skipped")
        assert report.unmatched == ["random_thing.tif"]
        assert stray.read_bytes() == b"WHO KNOWS"
        assert eg.stacks() == []
    finally:
        eg.close()


def test_unmerged_brackets_reminder_fact(tmp_path):
    eg = _make_event(tmp_path)
    try:
        _project(eg, tmp_path)
        report = scan_for_returns(eg, "skipped")
        assert report.unmerged_bracket_count == 1   # picked bracket, no result yet
        assert report.nothing_happened              # …but nothing to report otherwise
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# Leg B — editor returns
# --------------------------------------------------------------------------- #


def test_editor_return_associates_by_link_stem(tmp_path):
    """spec/72 Model B / spec/89 §1.5 — the Edited Media/ file is
    hardlinked into Exported Media/<filename>; the lineage row's
    export_relpath points at the destination and carries
    provenance='third_party'. The original under Edited Media/ stays
    where the editor wrote it."""
    eg = _make_event(tmp_path)
    try:
        _project(eg, tmp_path)
        ret_dir = tmp_path / "Edited Media" / "LRC"
        ret_dir.mkdir(parents=True)
        src = ret_dir / "D03_G9_p1-Edit.jpg"
        src.write_bytes(b"LRC JPEG")
        (ret_dir / "D03_G9_p1-Edit.xmp").write_text("sidecar")   # ignored silently

        report = scan_for_returns(eg, "skipped")
        assert report.associated == ["Exported Media/D03_G9_p1-Edit.jpg"]
        assert report.unmatched == []
        # The hardlink landed; both paths point to the same bytes.
        dest = tmp_path / "Exported Media" / "D03_G9_p1-Edit.jpg"
        assert dest.exists() and dest.read_bytes() == b"LRC JPEG"
        # LRC's inbox is additive — the original survives.
        assert src.exists()
        lin = eg.lineage()
        assert len(lin) == 1
        row = lin[0]
        assert row.export_relpath == "Exported Media/D03_G9_p1-Edit.jpg"
        assert row.source_item_id == "i-solo" and row.phase == "edit"
        assert row.recipe_json is None              # external — no Mira recipe
        assert row.provenance == "third_party"
        # Idempotent: a second scan does not duplicate the association.
        report2 = scan_for_returns(eg, "skipped")
        assert report2.associated == [] and len(eg.lineage()) == 1
    finally:
        eg.close()


def test_editor_return_unmatched_is_flagged(tmp_path):
    """Unmatched files report the SOURCE relpath under Edited Media/
    (the user reads this to find what's stray), not the would-be
    destination — Model B doesn't materialise unmatched files."""
    eg = _make_event(tmp_path)
    try:
        ret_dir = tmp_path / "Edited Media" / "LRC"
        ret_dir.mkdir(parents=True)
        (ret_dir / "IMG_9999.jpg").write_bytes(b"?")
        report = scan_for_returns(eg, "skipped")
        assert report.unmatched == ["Edited Media/LRC/IMG_9999.jpg"]
        assert eg.lineage() == []
        # Nothing was materialised into Exported Media/.
        assert not (tmp_path / "Exported Media" / "IMG_9999.jpg").exists()
    finally:
        eg.close()


def test_longest_prefix_wins(tmp_path):
    """`D03_G9_p10-Edit` must associate to p10, not to p1 (its stem also
    starts with p1's stem)."""
    eg = _make_event(tmp_path)
    try:
        src = tmp_path / "Original Media/_cameras/d3/G9/p10.rw2"
        src.write_bytes(b"bytes-p10")
        eg.store.upsert(m.Item(
            id="i-p10", kind="photo", created_at="t", provenance="captured",
            origin_relpath="Original Media/_cameras/d3/G9/p10.rw2",
            sha256="sp10", byte_size=9, materialized_at="t",
            materialized_phase="ingest", camera_id="G9", day_number=3,
            capture_time_raw="2026-04-03T09:30:00",
            capture_time_corrected="2026-04-03T09:30:00",
        ))
        ret_dir = tmp_path / "Edited Media" / "LRC"
        ret_dir.mkdir(parents=True)
        (ret_dir / "D03_G9_p10-Edit.jpg").write_bytes(b"x")
        report = scan_for_returns(eg, "skipped")
        assert report.associated == ["Exported Media/D03_G9_p10-Edit.jpg"]
        assert eg.lineage()[0].source_item_id == "i-p10"
        assert eg.lineage()[0].provenance == "third_party"
    finally:
        eg.close()
