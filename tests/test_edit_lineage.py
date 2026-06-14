"""Tests for the Edit-phase lineage helper — the one place lineage rows
get written so Share/Curate can walk back from each processed file to its
source ``Item``.

The helper mirrors the engine's deterministic naming
(``<dest_dir>/<src.stem>.<ext>``) and the four ``ExportResult`` buckets
(``written`` / ``overwritten`` / ``renamed`` / ``already_present``).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.gateway import EventsIndex, Gateway, make_entry
from mira.settings.repo import SettingsRepo
from mira.store import json_dump
from mira.ui.edited._lineage import (
    record_edit_export_lineage,
    record_single_lineage,
)
from tests.test_store import _rich_document


FIXED_NOW = "2026-06-01T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


@pytest.fixture
def event_gw(tmp_path):
    base = tmp_path / "lib"
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
        now=_now,
    )
    gw.set_photos_base_path(str(base))
    entry = make_entry(
        event_id="evt-1", name="Costa Rica 2026", start_date="2026-04-01",
        end_date="2026-04-14", is_closed=False,
        event_root=base / "ev", photos_base_path=base,
    )
    gw.materialise_event(json_dump.to_json(_rich_document()), entry)
    eg = gw.open_event("evt-1")
    # Drop any pre-existing lineage so tests start from a known empty state.
    with eg.store.transaction() as conn:
        conn.execute("DELETE FROM lineage")
    yield eg
    eg.close()


def _result(**kwargs):
    """Stub ExportResult — only the four buckets the helper reads."""
    return SimpleNamespace(
        written=kwargs.get("written", []),
        overwritten=kwargs.get("overwritten", []),
        renamed=kwargs.get("renamed", []),
        already_present=kwargs.get("already_present", []),
    )


# --------------------------------------------------------------------------- #
# record_edit_export_lineage — every ExportResult bucket
# --------------------------------------------------------------------------- #


def test_writes_lineage_for_written_paths(event_gw):
    """The ``written`` bucket — new files — is the common case."""
    root = event_gw.event_root
    dest = root / "03 - Processed" / "Dia 1 — Arenal" / "P1000001.jpg"
    n = record_edit_export_lineage(
        event_gw, root,
        items_with_sources=[
            ("i-photo", Path("00 - Captured/Day01/P1000001.RW2")),
        ],
        result=_result(written=[dest]),
    )
    assert n == 1
    lin = event_gw.lineage()
    assert any(
        l.export_relpath == "03 - Processed/Dia 1 — Arenal/P1000001.jpg"
        and l.phase == "edit"
        and l.source_kind == "item"
        and l.source_item_id == "i-photo"
        for l in lin
    )


def test_writes_lineage_for_renamed_pairs(event_gw):
    """``renamed`` rows are ``(src, dest)`` tuples — use src directly."""
    root = event_gw.event_root
    src = Path("00 - Captured/Day01/P1000001.RW2")
    dest = root / "03 - Processed" / "Dia 1" / "P1000001 (2).jpg"
    n = record_edit_export_lineage(
        event_gw, root,
        items_with_sources=[("i-photo", src)],
        result=_result(renamed=[(src, dest)]),
    )
    assert n == 1


def test_writes_lineage_for_overwritten_and_already_present(event_gw):
    """``overwritten`` and ``already_present`` are both treated as success.
    Uses two real fixture item ids (``i-photo`` and ``i-stk``) so the
    lineage FK to ``item.id`` is satisfied."""
    root = event_gw.event_root
    # Drop the fixture's existing lineage row for i-photo first — we want
    # to write fresh rows here without UPSERT collisions.
    with event_gw.store.transaction() as conn:
        conn.execute("DELETE FROM lineage")
    over = root / "03 - Processed" / "Dia 1" / "P1000001.jpg"
    same = root / "03 - Processed" / "Dia 1" / "stack1.jpg"
    n = record_edit_export_lineage(
        event_gw, root,
        items_with_sources=[
            ("i-photo", Path("00 - Captured/Day01/P1000001.RW2")),
            ("i-stk", Path("00 - Captured/Day01/stack1.tif")),
        ],
        result=_result(overwritten=[over], already_present=[same]),
    )
    assert n == 2


def test_skips_destinations_outside_event_root(event_gw):
    """A dest path outside ``event_root`` (custom destination) is skipped —
    Share can't read those anyway."""
    root = event_gw.event_root
    outside = Path("D:/elsewhere/P1000001.jpg")
    n = record_edit_export_lineage(
        event_gw, root,
        items_with_sources=[
            ("i-photo", Path("00 - Captured/Day01/P1000001.RW2")),
        ],
        result=_result(written=[outside]),
    )
    assert n == 0
    assert event_gw.lineage() == []


def test_skips_when_no_matching_source(event_gw):
    """A dest whose stem isn't in the source map is skipped (defensive — a
    stray file in the engine output we didn't ask for)."""
    root = event_gw.event_root
    dest = root / "03 - Processed" / "Dia 1" / "STRANGE.jpg"
    n = record_edit_export_lineage(
        event_gw, root,
        items_with_sources=[
            ("i-photo", Path("00 - Captured/Day01/P1000001.RW2")),
        ],
        result=_result(written=[dest]),
    )
    assert n == 0


# --------------------------------------------------------------------------- #
# record_single_lineage — video clip path
# --------------------------------------------------------------------------- #


def test_record_single_lineage_writes_video_row(event_gw):
    # spec/56: the rich document's video child is the segment item i-seg0
    # (clips-as-freeform-spans retired); spec/57: exports land in Edited Media.
    root = event_gw.event_root
    dest = root / "Edited Media" / "Dia 2" / "P1000123_processed.mp4"
    ok = record_single_lineage(
        event_gw, root,
        item_id="i-seg0",
        dest_path=dest,
    )
    assert ok is True
    lin = event_gw.lineage()
    assert any(
        l.export_relpath == "Edited Media/Dia 2/P1000123_processed.mp4"
        and l.source_item_id == "i-seg0"
        and l.phase == "edit"
        for l in lin
    )


def test_record_single_lineage_skips_outside_event_root(event_gw):
    root = event_gw.event_root
    outside = Path("D:/elsewhere/clip.mp4")
    ok = record_single_lineage(
        event_gw, root,
        item_id="i-seg0",
        dest_path=outside,
    )
    assert ok is False
    assert event_gw.lineage() == []
