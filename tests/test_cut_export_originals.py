"""spec/105 §3 — `include_originals=True` places each member's
`origin_relpath` under `<dest>/Original Media/` (deduped on collision).

The per-event path resolves origin via the gateway's `item()` (default
`OriginalResolver`); a member with no `source_item_id` (separators /
opener) skips this stage; a member whose origin file is missing on
disk lands in `missing_originals`, never a crash.

The cross-event path builds a one-shot `{export_relpath →
origin_relpath}` index per source event so the per-member resolve is
O(1). Grab-kind members are NOT duplicated into `Original Media/` (they
already ARE originals by definition).
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_export import export_cut
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now


MEMBERS = ["Exported Media/e1.jpg", "Exported Media/e3a.jpg", "Exported Media/v1.mp4"]


@pytest.fixture
def gw(tmp_path):
    """Mirror of tests/test_cut_export.py::gw — same event doc, same
    members, plus Original Media files for the `include_originals`
    path to find."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    for rel in ("e1.jpg", "e2.jpg", "e3a.jpg", "e3b.jpg", "v1.mp4"):
        p = tmp_path / "Exported Media" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"EXP:" + rel.encode())
    # Items p1/p2/p3/v1 each have origin_relpath = Original Media/<id>.jpg
    # (per _doc()). Stage matching bytes.
    for origin_rel in (
        "Original Media/p1.jpg", "Original Media/p2.jpg",
        "Original Media/p3.jpg", "Original Media/v1.mp4",
    ):
        p = tmp_path / origin_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RAW:" + origin_rel.encode())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    g.set_cut_members("cut-s", MEMBERS)
    yield g
    g.close()


def _sep_writer(target: Path, day) -> None:
    target.write_bytes(b"SEP:" + str(day).encode())


def _names(folder: Path) -> list:
    # Ignore the spec/158 ``.mira-cut-export.json`` sidecar manifest.
    return sorted(
        p.name for p in folder.iterdir()
        if p.is_file() and not p.name.startswith("."))


# ── Happy path: originals land in Original Media/


def test_originals_landed_for_each_member(gw, tmp_path):
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path,
        separators_on=False, include_originals=True,
    )
    originals = result.folder / "Original Media"
    assert originals.is_dir(), "Original Media/ subdir must exist"
    # Three members → three originals (p1, p3, v1 — e1 → p1, e3a → p3,
    # v1 → v1).
    names = _names(originals)
    assert "p1.jpg" in names
    assert "p3.jpg" in names
    assert "v1.mp4" in names
    # Counts in result reflect link-or-copy outcomes.
    assert result.originals_linked + result.originals_copied == 3
    assert result.missing_originals == []


def test_show_files_not_duplicated_into_originals(gw, tmp_path):
    """Originals are a SEPARATE subdir — the numbered show files at the
    folder root are unchanged."""
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path,
        separators_on=False, include_originals=True,
    )
    # Three numbered show files at the root, audio/originals as
    # subdirs, no duplicate `NNN_` files in originals.
    root_files = _names(result.folder)
    assert root_files == ["001_e1.jpg", "002_e3a.jpg", "003_v1.mp4"]
    originals = _names(result.folder / "Original Media")
    assert all(not n.startswith("00") for n in originals)


def test_separators_and_opener_not_duplicated_into_originals(gw, tmp_path):
    """Opener + day-separator slides have no source_item — they must
    NOT land in Original Media/ (they're rendered, not photographed)."""
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path,
        separators_on=True, separator_writer=_sep_writer,
        opener_writer=lambda t: t.write_bytes(b"OPENER"),
        include_originals=True,
    )
    originals = result.folder / "Original Media"
    names = _names(originals)
    # No opener.jpg or dayN.jpg in originals.
    assert "opener.jpg" not in names
    assert not any(n.startswith("day") for n in names)
    # Separators DO land at the show root, as before.
    assert any("opener" in n for n in _names(result.folder))


def test_missing_origin_file_reported_not_crash(gw, tmp_path):
    """A source file deleted out-of-band lands in `missing_originals`
    and the export keeps going."""
    (tmp_path / "Original Media" / "p3.jpg").unlink()
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path,
        separators_on=False, include_originals=True,
    )
    assert "Original Media/p3.jpg" in result.missing_originals
    # The other two still land.
    assert result.originals_linked + result.originals_copied == 2
    # Show files unaffected (the export-tier file is still there).
    assert (result.folder / "002_e3a.jpg").is_file()


def test_originals_dedup_on_name_collision(tmp_path):
    """Two members with origins sharing a basename (e.g. across
    subfolders) get a `_2` suffix on the second. Pin the helper
    directly — the gateway fixture doesn't trigger this naturally."""
    from mira.shared.cut_export import _dedup_filename
    parent = tmp_path / "dedup_target"
    parent.mkdir()
    a = _dedup_filename(parent, "x.jpg")
    a.write_bytes(b"a")
    b = _dedup_filename(parent, "x.jpg")
    assert b.name == "x_2.jpg"
    b.write_bytes(b"b")
    c = _dedup_filename(parent, "x.jpg")
    assert c.name == "x_3.jpg"


def test_include_originals_false_skips_subdir_entirely(gw, tmp_path):
    """Default is off — when omitted, Original Media/ never appears.
    The regression guard for the legacy contract."""
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=False,
    )
    assert not (result.folder / "Original Media").exists()
    assert result.originals_linked == 0
    assert result.originals_copied == 0
    assert result.missing_originals == []


def test_custom_original_resolver_is_honoured(gw, tmp_path):
    """An injected `original_resolver` overrides the gateway default —
    the test seam that keeps the module Qt/gateway-agnostic. Here we
    rename every origin to a stub path so the test can assert the
    resolver IS the one consulted."""
    # Create a single stub origin and route every member through it.
    stub_rel = "Original Media/STUB.jpg"
    (tmp_path / stub_rel).write_bytes(b"STUB")
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path,
        separators_on=False, include_originals=True,
        original_resolver=lambda sid: stub_rel,
    )
    originals = _names(result.folder / "Original Media")
    # Three members → three stub copies, deduped to STUB.jpg /
    # STUB_2.jpg / STUB_3.jpg.
    assert sorted(originals) == ["STUB.jpg", "STUB_2.jpg", "STUB_3.jpg"]
