"""Tests for the spec/57 §2 Picked Media projection.

Two layers: ``core.picked_media`` (the manifest-guarded link engine —
deterministic names, bracket subdirs, never-touch-real-bytes rebuilds)
and ``mira.picked.edit_model.picked_media_entries`` (the
assembler that mirrors the Edit pool rule).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.picked_media import (
    MANIFEST_NAME,
    PickedEntry,
    bracket_dir_name,
    link_name,
    rebuild_picked_media,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_source(root: Path, rel: str, content: bytes = b"bytes") -> Path:
    p = root / "Original Media" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _entry(root: Path, rel: str, *, day=3, cam="DC-G9M2", bracket=None,
           content: bytes = b"bytes") -> PickedEntry:
    src = _make_source(root, rel, content)
    return PickedEntry(
        source_path=src, filename=src.name, day_number=day,
        camera_id=cam, bracket_group_id=bracket,
    )


# --------------------------------------------------------------------------- #
# Naming
# --------------------------------------------------------------------------- #


def test_link_name_deterministic_prefix():
    e = PickedEntry(source_path=Path("x"), filename="P1000001.RW2",
                    day_number=3, camera_id="DC-G9M2")
    assert link_name(e) == "D03_DC-G9M2_P1000001.RW2"


def test_link_name_undated_and_spacey_camera():
    e = PickedEntry(source_path=Path("x"), filename="GOPR0001.MP4",
                    day_number=None, camera_id="HERO12 Black")
    assert link_name(e) == "D00_HERO12 Black_GOPR0001.MP4"
    e2 = PickedEntry(source_path=Path("x"), filename="a.jpg",
                     day_number=1, camera_id=None)
    assert link_name(e2) == "D01_NOCAM_a.jpg"


def test_link_name_sanitises_invalid_chars():
    e = PickedEntry(source_path=Path("x"), filename="a.jpg",
                    day_number=2, camera_id='Cam<:>"?*')
    name = link_name(e)
    assert not any(ch in name for ch in '<>:"/\\|?*')


# --------------------------------------------------------------------------- #
# Projection shape
# --------------------------------------------------------------------------- #


def test_rebuild_flat_root_and_bracket_subdirs(tmp_path):
    entries = [
        _entry(tmp_path, "d3/p1.rw2"),
        _entry(tmp_path, "d3/b1.rw2", bracket="focus-abc"),
        _entry(tmp_path, "d3/b2.rw2", bracket="focus-abc"),
    ]
    result = rebuild_picked_media(tmp_path, entries)
    assert result.ok and result.linked == 3 and result.bracket_dirs == 1
    root = tmp_path / "Picked Media"
    assert (root / "D03_DC-G9M2_p1.rw2").exists()
    sub = root / bracket_dir_name("focus-abc")
    # spec/57 §2.1: bracket members ONLY in the subdir, never at root.
    assert (sub / "D03_DC-G9M2_b1.rw2").exists()
    assert (sub / "D03_DC-G9M2_b2.rw2").exists()
    assert not (root / "D03_DC-G9M2_b1.rw2").exists()
    # The links are hardlinks to the originals (same file).
    assert os.path.samefile(root / "D03_DC-G9M2_p1.rw2",
                            tmp_path / "Original Media" / "d3" / "p1.rw2")


def test_rebuild_drops_unpicked_and_prunes_empty_bracket_dir(tmp_path):
    e_flat = _entry(tmp_path, "d3/p1.rw2")
    e_br = _entry(tmp_path, "d3/b1.rw2", bracket="focus-abc")
    rebuild_picked_media(tmp_path, [e_flat, e_br])
    # Re-pick: the bracket member is no longer picked.
    result = rebuild_picked_media(tmp_path, [e_flat])
    assert result.ok and result.linked == 1 and result.removed == 2
    root = tmp_path / "Picked Media"
    assert (root / "D03_DC-G9M2_p1.rw2").exists()
    assert not (root / bracket_dir_name("focus-abc")).exists()  # pruned


def test_rebuild_skips_missing_sources(tmp_path):
    e = _entry(tmp_path, "d3/p1.rw2")
    e.source_path.unlink()
    result = rebuild_picked_media(tmp_path, [e])
    assert result.linked == 0 and result.skipped_missing == 1


# --------------------------------------------------------------------------- #
# The never-touch-real-bytes rules (spec/57 §2.3)
# --------------------------------------------------------------------------- #


def test_rebuild_preserves_tool_output_at_root(tmp_path):
    """A stacker's merged result at the root survives every rebuild."""
    rebuild_picked_media(tmp_path, [_entry(tmp_path, "d3/p1.rw2")])
    root = tmp_path / "Picked Media"
    output = root / "merged_stack.tif"
    output.write_bytes(b"REAL MERGE BYTES")
    result = rebuild_picked_media(tmp_path, [_entry(tmp_path, "d3/p1.rw2")])
    assert output.exists() and output.read_bytes() == b"REAL MERGE BYTES"
    assert result.ok


def test_rebuild_preserves_owned_path_replaced_by_tool(tmp_path):
    """If a tool deleted our link and wrote ITS file under the same name
    (new inode), the inode guard refuses the delete."""
    e = _entry(tmp_path, "d3/p1.rw2")
    rebuild_picked_media(tmp_path, [e])
    link = tmp_path / "Picked Media" / "D03_DC-G9M2_p1.rw2"
    link.unlink()
    link.write_bytes(b"TOOL OUTPUT NOW")          # same name, new inode
    result = rebuild_picked_media(tmp_path, [])    # nothing picked any more
    assert link.exists() and link.read_bytes() == b"TOOL OUTPUT NOW"
    assert result.preserved >= 1 and result.removed == 0


def test_rebuild_never_overwrites_foreign_name_collision(tmp_path):
    """A real file occupying a projection name blocks the link (error),
    and its bytes stay intact."""
    root = tmp_path / "Picked Media"
    root.mkdir(parents=True)
    blocker = root / "D03_DC-G9M2_p1.rw2"
    blocker.write_bytes(b"FOREIGN")
    result = rebuild_picked_media(tmp_path, [_entry(tmp_path, "d3/p1.rw2")])
    assert blocker.read_bytes() == b"FOREIGN"
    assert not result.ok and result.preserved == 1


def test_rebuild_self_heals_after_corrupt_manifest(tmp_path):
    """A corrupt manifest costs nothing: an existing path that already IS
    the right link (samefile) is kept and re-owned; a real foreign file
    stays protected by the name-collision rule (separate test above)."""
    rebuild_picked_media(tmp_path, [_entry(tmp_path, "d3/p1.rw2")])
    root = tmp_path / "Picked Media"
    (root / MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
    result = rebuild_picked_media(tmp_path, [_entry(tmp_path, "d3/p1.rw2")])
    assert result.ok and result.linked == 1
    assert (root / "D03_DC-G9M2_p1.rw2").exists()
    data = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert [e["relpath"] for e in data["entries"]] == ["D03_DC-G9M2_p1.rw2"]


def test_copy_fallback_when_hardlinks_unavailable(tmp_path, monkeypatch):
    """Cross-volume events fall back to copies; the manifest still owns
    them so the next rebuild can replace them."""
    def _no_link(src, dst, **kwargs):
        raise OSError("cross-device")
    monkeypatch.setattr(os, "link", _no_link)
    e = _entry(tmp_path, "d3/p1.rw2")
    result = rebuild_picked_media(tmp_path, [e])
    assert result.linked == 1 and result.copied == 1
    link = tmp_path / "Picked Media" / "D03_DC-G9M2_p1.rw2"
    assert link.exists() and not os.path.samefile(link, e.source_path)
    # Owned copy is swept on the next rebuild like any link.
    result2 = rebuild_picked_media(tmp_path, [])
    assert result2.removed == 1 and not link.exists()


def test_manifest_round_trips_ownership(tmp_path):
    rebuild_picked_media(tmp_path, [_entry(tmp_path, "d3/p1.rw2")])
    data = json.loads(
        (tmp_path / "Picked Media" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert [e["relpath"] for e in data["entries"]] == ["D03_DC-G9M2_p1.rw2"]


# --------------------------------------------------------------------------- #
# The assembler — mirrors the Edit pool rule
# --------------------------------------------------------------------------- #


def _store_with(tmp_path):
    from mira.gateway.event_gateway import EventGateway
    from mira.store import models as m
    from mira.store.repo import EventStore

    store = EventStore.create(tmp_path / "event.db", event_id="evt-pm")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-pm", name="PM", created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="G9"))
    store.upsert(m.TripDay(day_number=3, date="2026-04-03"))

    def _item(iid, rel, *, bracket=None, day=3):
        store.upsert(m.Item(
            id=iid, kind="photo", created_at="t", provenance="captured",
            origin_relpath=rel, sha256="s" + iid, byte_size=1,
            materialized_at="t", materialized_phase="ingest",
            camera_id="G9", day_number=day,
            capture_time_raw="2026-04-03T08:00:00",
            capture_time_corrected="2026-04-03T08:00:00",
            bracket_group_id=bracket,
            bracket_role="member" if bracket else None,
        ))

    _item("i-pick", "Original Media/_cameras/d3/G9/p1.rw2")
    _item("i-skip", "Original Media/_cameras/d3/G9/p2.rw2")
    _item("i-undecided", "Original Media/_cameras/d3/G9/p3.rw2")
    _item("i-br", "Original Media/_cameras/d3/G9/b1.rw2", bracket="focus-x")
    store.upsert(m.PhaseState(item_id="i-pick", phase="pick", state="picked"))
    store.upsert(m.PhaseState(item_id="i-skip", phase="pick", state="skipped"))
    store.upsert(m.PhaseState(item_id="i-br", phase="pick", state="picked"))
    # A picked VIDEO with a picked but VIRTUAL spec/56 snapshot child:
    # the virtual child has no bytes (never linked), and the video is
    # excluded by edit_pool_ids' master rule — the projection mirrors
    # the Edit pool EXACTLY, whatever that rule says. (The master rule
    # itself is spec/56 slice-3 territory; when it changes there, this
    # fixture documents that the projection follows automatically.)
    store.upsert(m.Item(
        id="i-video", kind="video", created_at="t", provenance="captured",
        origin_relpath="Original Media/_cameras/d3/G9/v1.mp4",
        sha256="sv", byte_size=1, materialized_at="t",
        materialized_phase="ingest", camera_id="G9", day_number=3,
        capture_time_raw="2026-04-03T09:00:00",
        capture_time_corrected="2026-04-03T09:00:00", duration_ms=10_000,
    ))
    store.upsert(m.PhaseState(item_id="i-video", phase="pick", state="picked"))
    store.upsert(m.Item(
        id="i-virtual", kind="photo", created_at="t", provenance="snapshot",
        parent_item_id="i-video",
    ))
    store.upsert(m.VideoSnapshot(
        item_id="i-virtual", video_item_id="i-video", at_ms=0, created_at="t"))
    store.upsert(m.PhaseState(item_id="i-virtual", phase="edit", state="picked"))
    store.upsert(m.PhaseState(item_id="i-virtual", phase="pick", state="picked"))
    return EventGateway(store, event_root=tmp_path)


def test_picked_media_entries_mirror_edit_pool(tmp_path):
    from mira.picked.edit_model import picked_media_entries

    eg = _store_with(tmp_path)
    try:
        entries = picked_media_entries(eg, "skipped")
        by_name = {e.filename: e for e in entries}
        # Explicit picks with bytes are in; skipped + undecided (default
        # skip) + virtual children are out.
        assert set(by_name) == {"p1.rw2", "b1.rw2"}
        assert by_name["b1.rw2"].bracket_group_id == "focus-x"
        assert by_name["p1.rw2"].day_number == 3
        assert by_name["p1.rw2"].camera_id == "G9"
        # With the pick default flipped to picked, undecided items flow.
        entries_dp = picked_media_entries(eg, "picked")
        assert {e.filename for e in entries_dp} == {"p1.rw2", "p3.rw2", "b1.rw2"}
    finally:
        eg.close()


def test_picked_media_entries_requires_event_root(tmp_path):
    from mira.gateway.event_gateway import EventGateway
    from mira.picked.edit_model import picked_media_entries
    from mira.store import models as m
    from mira.store.repo import EventStore

    store = EventStore.create(":memory:", event_id="e")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="e", name="x", created_at="t", updated_at="t")))
    eg = EventGateway(store)
    with pytest.raises(RuntimeError):
        picked_media_entries(eg)
    eg.close()
