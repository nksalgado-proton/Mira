"""Cross-event Cut export — bytes flow tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.shared.cross_event_cut_export import (
    CrossEventExportError,
    export_cross_event_cut,
)
from mira.store.repo import EventStore


NOW = "2026-06-16T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_umbrella(tmp_path):
    from mira.gateway.gateway import Gateway
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo

    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    photos_base = tmp_path / "photos"
    photos_base.mkdir()
    gw = Gateway(
        settings=settings, index=index,
        user_store_path=tmp_path / "mira.db",
        now=lambda: NOW, installation_profile="XMC")
    _ = gw.user_store
    settings.update(photos_base_path=str(photos_base))
    return gw, photos_base


def _build_event_with_files(photos_base: Path, *, eid: str, name: str,
                            exported_files: dict = None,
                            original_files: dict = None) -> Path:
    """Create an event_root with optional pre-staged Exported Media/ and
    Original Media/ files. Returns the root."""
    root = photos_base / name
    root.mkdir(exist_ok=True)
    store = EventStore.create(
        root / "event.db", event_id=eid, app_version="test",
        created_at=NOW)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, name, NOW, NOW))
    store.close()
    if exported_files:
        exp = root / "Exported Media"
        exp.mkdir(exist_ok=True)
        for relpath, content in exported_files.items():
            full = exp / relpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(content)
    if original_files:
        orig = root / "Original Media"
        orig.mkdir(exist_ok=True)
        for relpath, content in original_files.items():
            full = orig / relpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(content)
    return root


def _register(gw, photos_base, root: Path, *, eid: str, name: str) -> None:
    from mira.gateway.index import make_entry
    gw.index.upsert(make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root, photos_base_path=photos_base))


def _seed_anchor_cut(gw, anchor_event_id: str, cut_id: str,
                     members: list) -> None:
    """Seed a cut + members in **mira.db** (spec/94 Phase 4a-ii — the
    storage move; cross-event Cuts live in the library store, spec/93
    §3). ``anchor_event_id`` is kept as a parameter name for back-compat
    with the older shape; the value is ignored — every member names
    its own ``event_id``.

    ``members`` is a list of dicts with keys: kind, export_relpath,
    origin_relpath, event_id."""
    lg = gw.library_gateway()
    # Insert the cut row directly so the test controls the id.
    with lg.user_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, "
            "                 created_at, updated_at) "
            "VALUES (?, ?, 'user', ?, ?)",
            (cut_id, "test_cut", NOW, NOW))
    lg.set_cross_event_cut_members(cut_id, members)


# --------------------------------------------------------------------------- #
# Failures
# --------------------------------------------------------------------------- #


def test_missing_cut_raises(tmp_path):
    """spec/94 Phase 4a-ii: the export raises when the Cut isn't in
    mira.db (was: 'anchor event gone' in the pre-flip code)."""
    gw, _ = _make_umbrella(tmp_path)
    with pytest.raises(CrossEventExportError):
        export_cross_event_cut(
            gw, "anchor-ignored", "cut-x", target=tmp_path / "out")
    gw.close()


def test_unwritable_target_raises(tmp_path):
    gw, photos_base = _make_umbrella(tmp_path)
    r = _build_event_with_files(photos_base, eid="e1", name="E1")
    _register(gw, photos_base, r, eid="e1", name="E1")
    # Seed a cut so the lookup succeeds; the target is what fails.
    _seed_anchor_cut(gw, "e1", "cut-x", [])
    # Use an obviously bad target (a file, not a directory).
    target_file = tmp_path / "not_a_dir.txt"
    target_file.write_text("blocker")
    with pytest.raises(CrossEventExportError):
        export_cross_event_cut(
            gw, "e1", "cut-x", target=target_file / "child")
    gw.close()


# --------------------------------------------------------------------------- #
# Export-kind members: hardlink (or copy) from source Exported Media/
# --------------------------------------------------------------------------- #


def test_export_kind_member_links_from_source_exported(tmp_path):
    """An 'export' member links the source event's Exported Media file
    into the output."""
    gw, photos_base = _make_umbrella(tmp_path)
    src = _build_event_with_files(
        photos_base, eid="src", name="Source",
        exported_files={"Day01/photo.jpg": b"the bytes"})
    anchor = _build_event_with_files(
        photos_base, eid="anchor", name="Anchor")
    _register(gw, photos_base, src, eid="src", name="Source")
    _register(gw, photos_base, anchor, eid="anchor", name="Anchor")
    _seed_anchor_cut(gw, "anchor", "cut-x", [
        {"kind": "export",
         "export_relpath": "Exported Media/Day01/photo.jpg",
         "event_id": "src"},
    ])

    target = tmp_path / "out"
    summary = export_cross_event_cut(
        gw, "anchor", "cut-x", target=target)
    # Flattened filename (slashes → underscores).
    assert (target / "Exported Media_Day01_photo.jpg").is_file()
    assert (target / "Exported Media_Day01_photo.jpg").read_bytes() == b"the bytes"
    assert summary["member_count"] == 1
    assert summary["missing"] == 0
    assert summary["linked"] + summary["copied"] == 1
    gw.close()


def test_export_kind_anchor_event_member_works(tmp_path):
    """Anchor-event member (event_id=anchor) — same routing, source root
    is the anchor's."""
    gw, photos_base = _make_umbrella(tmp_path)
    anchor = _build_event_with_files(
        photos_base, eid="anchor", name="Anchor",
        exported_files={"Day01/my.jpg": b"anchor bytes"})
    _register(gw, photos_base, anchor, eid="anchor", name="Anchor")
    _seed_anchor_cut(gw, "anchor", "cut-x", [
        {"kind": "export",
         "export_relpath": "Exported Media/Day01/my.jpg",
         "event_id": "anchor"},
    ])
    target = tmp_path / "out"
    summary = export_cross_event_cut(
        gw, "anchor", "cut-x", target=target)
    assert (target / "Exported Media_Day01_my.jpg").is_file()
    assert summary["missing"] == 0
    gw.close()


# --------------------------------------------------------------------------- #
# Grab-kind members: always copy from source Original Media/
# --------------------------------------------------------------------------- #


def test_grab_kind_member_copies_from_source_original(tmp_path):
    """A 'grab' member copies the source event's Original Media file into
    the output. Never linked (Original Media is byte-pristine)."""
    gw, photos_base = _make_umbrella(tmp_path)
    src = _build_event_with_files(
        photos_base, eid="src", name="Source",
        original_files={"Day01/raw.raw": b"original bytes"})
    anchor = _build_event_with_files(
        photos_base, eid="anchor", name="Anchor")
    _register(gw, photos_base, src, eid="src", name="Source")
    _register(gw, photos_base, anchor, eid="anchor", name="Anchor")
    _seed_anchor_cut(gw, "anchor", "cut-x", [
        {"kind": "grab",
         "origin_relpath": "Original Media/Day01/raw.raw",
         "event_id": "src"},
    ])

    target = tmp_path / "out"
    summary = export_cross_event_cut(
        gw, "anchor", "cut-x", target=target)
    assert (target / "Original Media_Day01_raw.raw").is_file()
    assert (target / "Original Media_Day01_raw.raw").read_bytes() == b"original bytes"
    assert summary["copied"] == 1
    assert summary["linked"] == 0
    gw.close()


# --------------------------------------------------------------------------- #
# Mixed-kind member: both export and grab in one Cut
# --------------------------------------------------------------------------- #


def test_mixed_kind_members_export_routes_per_kind(tmp_path):
    """A Cut with both export AND grab members: each routes through its
    own source path."""
    gw, photos_base = _make_umbrella(tmp_path)
    src = _build_event_with_files(
        photos_base, eid="src", name="Source",
        exported_files={"a.jpg": b"a-exported"},
        original_files={"b.raw": b"b-original"})
    anchor = _build_event_with_files(
        photos_base, eid="anchor", name="Anchor")
    _register(gw, photos_base, src, eid="src", name="Source")
    _register(gw, photos_base, anchor, eid="anchor", name="Anchor")
    _seed_anchor_cut(gw, "anchor", "cut-x", [
        {"kind": "export",
         "export_relpath": "Exported Media/a.jpg",
         "event_id": "src"},
        {"kind": "grab",
         "origin_relpath": "Original Media/b.raw",
         "event_id": "src"},
    ])

    target = tmp_path / "out"
    summary = export_cross_event_cut(
        gw, "anchor", "cut-x", target=target)
    assert (target / "Exported Media_a.jpg").read_bytes() == b"a-exported"
    assert (target / "Original Media_b.raw").read_bytes() == b"b-original"
    assert summary["member_count"] == 2
    assert summary["copied"] >= 1                    # grab always copies
    gw.close()


# --------------------------------------------------------------------------- #
# Missing members are reported but don't crash
# --------------------------------------------------------------------------- #


def test_missing_source_event_lands_in_summary(tmp_path):
    """A member whose source event is no longer in the index → summary
    reports it under ``missing``; export continues."""
    gw, photos_base = _make_umbrella(tmp_path)
    anchor = _build_event_with_files(
        photos_base, eid="anchor", name="Anchor",
        exported_files={"a.jpg": b"anchor bytes"})
    _register(gw, photos_base, anchor, eid="anchor", name="Anchor")
    _seed_anchor_cut(gw, "anchor", "cut-x", [
        {"kind": "export",
         "export_relpath": "Exported Media/a.jpg",
         "event_id": "anchor"},                          # in index
        {"kind": "export",
         "export_relpath": "Exported Media/b.jpg",
         "event_id": "ghost"},                           # not in index
    ])

    target = tmp_path / "out"
    summary = export_cross_event_cut(
        gw, "anchor", "cut-x", target=target)
    assert (target / "Exported Media_a.jpg").is_file()
    assert not (target / "Exported Media_b.jpg").exists()
    assert summary["missing"] == 1
    gw.close()


def test_missing_source_bytes_lands_in_summary(tmp_path):
    """Source event resolves but the file isn't on disk → 'missing'."""
    gw, photos_base = _make_umbrella(tmp_path)
    src = _build_event_with_files(
        photos_base, eid="src", name="Source")           # no files
    anchor = _build_event_with_files(
        photos_base, eid="anchor", name="Anchor")
    _register(gw, photos_base, src, eid="src", name="Source")
    _register(gw, photos_base, anchor, eid="anchor", name="Anchor")
    _seed_anchor_cut(gw, "anchor", "cut-x", [
        {"kind": "export",
         "export_relpath": "Exported Media/ghost.jpg",
         "event_id": "src"},
    ])

    summary = export_cross_event_cut(
        gw, "anchor", "cut-x", target=tmp_path / "out")
    assert summary["missing"] == 1
    assert summary["linked"] == 0
    assert summary["copied"] == 0
    gw.close()


# --------------------------------------------------------------------------- #
# Idempotence — re-export overwrites
# --------------------------------------------------------------------------- #


def test_re_export_overwrites_existing_target_file(tmp_path):
    """Re-exporting a Cut to the same target overwrites the existing
    file — the export is idempotent at the directory level."""
    gw, photos_base = _make_umbrella(tmp_path)
    src = _build_event_with_files(
        photos_base, eid="src", name="Source",
        exported_files={"a.jpg": b"v1"})
    anchor = _build_event_with_files(
        photos_base, eid="anchor", name="Anchor")
    _register(gw, photos_base, src, eid="src", name="Source")
    _register(gw, photos_base, anchor, eid="anchor", name="Anchor")
    _seed_anchor_cut(gw, "anchor", "cut-x", [
        {"kind": "export",
         "export_relpath": "Exported Media/a.jpg",
         "event_id": "src"},
    ])
    target = tmp_path / "out"
    export_cross_event_cut(gw, "anchor", "cut-x", target=target)
    assert (target / "Exported Media_a.jpg").read_bytes() == b"v1"
    # Mutate the source, re-export.
    (src / "Exported Media" / "a.jpg").write_bytes(b"v2")
    export_cross_event_cut(gw, "anchor", "cut-x", target=target)
    assert (target / "Exported Media_a.jpg").read_bytes() == b"v2"
    gw.close()


# --------------------------------------------------------------------------- #
# last_exported_at stamping
# --------------------------------------------------------------------------- #


def test_export_stamps_last_exported_at(tmp_path):
    """After a successful export, the cut row's ``last_exported_at`` is
    updated so the list page can show export status."""
    gw, photos_base = _make_umbrella(tmp_path)
    anchor = _build_event_with_files(
        photos_base, eid="anchor", name="Anchor",
        exported_files={"a.jpg": b"x"})
    _register(gw, photos_base, anchor, eid="anchor", name="Anchor")
    _seed_anchor_cut(gw, "anchor", "cut-x", [
        {"kind": "export",
         "export_relpath": "Exported Media/a.jpg",
         "event_id": "anchor"},
    ])
    export_cross_event_cut(
        gw, "anchor", "cut-x", target=tmp_path / "out")
    # Read back from mira.db — the Cut + its stamp live in the library
    # store (spec/93 §3).
    cut = gw.library_gateway().cross_event_cut("cut-x")
    assert cut is not None
    assert cut.last_exported_at is not None
    gw.close()
