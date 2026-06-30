"""spec/159 — per-version ratings + the to_delete batch-delete flag.

Pins the gateway surface the Exported Collection review surface (closed-
event Cut page) writes through:

  * ``set_lineage_stars`` / ``set_lineage_color_label`` /
    ``set_lineage_flag`` / ``set_lineage_to_delete`` round-trip the
    value to the new ``lineage`` columns added in schema v23. Each
    mutator validates input (stars 1..5 or None; colour label one
    of the five LRC values or None; the two booleans accept truthy
    inputs).
  * ``lineage_ratings`` reads all four fields in one query into the
    new ``LineageRatings`` NamedTuple.
  * ``exported_marked_for_deletion`` returns every Exported Media/
    lineage row whose ``to_delete = 1``, in deterministic order.
  * ``delete_marked_exported_files`` commits the batch — uses the
    existing ``delete_exported_file_by_relpath`` cascade (file
    unlink + lineage row drop + edit_exported flip + cut_member
    cleanup), so this test asserts only the loop semantics + count
    return; the per-row cascade is pinned by ``test_pool_delete_
    cascade.py``.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway, LineageRatings
from mira.store import models as m
from mira.store.repo import EventStore

FIXED_NOW = "2026-06-30T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _doc() -> m.EventDocument:
    """Three photos all shipped; two of them are a versions cluster on
    one source item (p1 has two lineage rows — Mira render + LR
    return)."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-r", name="Ratings fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        m.Item(
            id="p1", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath="Original Media/p1.jpg", sha256="a" * 64,
            byte_size=1000, materialized_at=FIXED_NOW,
            materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw="2026-04-01T08:00:00",
            capture_time_corrected="2026-04-01T08:00:00",
        ),
        m.Item(
            id="p2", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath="Original Media/p2.jpg", sha256="b" * 64,
            byte_size=1000, materialized_at=FIXED_NOW,
            materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw="2026-04-01T09:00:00",
            capture_time_corrected="2026-04-01T09:00:00",
        ),
    ]
    # Three Exported Media/ lineage rows — two are versions of p1
    # (Mira render + LR return), one is the only version of p2.
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/Dia 1/p1.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p1", exported_at="t1",
                  provenance="mira_render"),
        m.Lineage(export_relpath="Exported Media/Dia 1/p1_LRC.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p1", exported_at="t2",
                  provenance="third_party"),
        m.Lineage(export_relpath="Exported Media/Dia 1/p2.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p2", exported_at="t3"),
    ]
    doc.adjustments = [
        m.Adjustment(item_id="p1", edit_exported=True),
        m.Adjustment(item_id="p2", edit_exported=True),
    ]
    return doc


@pytest.fixture
def event_dir(tmp_path):
    (tmp_path / "Exported Media" / "Dia 1").mkdir(parents=True)
    for name in ("p1.jpg", "p1_LRC.jpg", "p2.jpg"):
        (tmp_path / "Exported Media" / "Dia 1" / name).write_bytes(
            b"\xff\xd8\xff\xd9")
    return tmp_path


@pytest.fixture
def gw(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-r")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# ── stars ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [1, 2, 3, 4, 5, None])
def test_set_lineage_stars_round_trip(gw, value):
    rel = "Exported Media/Dia 1/p1.jpg"
    gw.set_lineage_stars(rel, value)
    assert gw.lineage_ratings(rel).stars == value


@pytest.mark.parametrize("bad", [0, 6, -1, 10])
def test_set_lineage_stars_rejects_out_of_range(gw, bad):
    rel = "Exported Media/Dia 1/p1.jpg"
    with pytest.raises(ValueError):
        gw.set_lineage_stars(rel, bad)


def test_set_lineage_stars_only_affects_target_row(gw):
    """Setting stars on one version of p1 doesn't bleed into the
    other version. Per spec/159 §2.1 ratings are per-version."""
    a = "Exported Media/Dia 1/p1.jpg"
    b = "Exported Media/Dia 1/p1_LRC.jpg"
    gw.set_lineage_stars(a, 5)
    gw.set_lineage_stars(b, 2)
    assert gw.lineage_ratings(a).stars == 5
    assert gw.lineage_ratings(b).stars == 2


# ── colour label ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "label", ["red", "yellow", "green", "blue", "purple", None])
def test_set_lineage_color_label_round_trip(gw, label):
    rel = "Exported Media/Dia 1/p1.jpg"
    gw.set_lineage_color_label(rel, label)
    assert gw.lineage_ratings(rel).color_label == label


@pytest.mark.parametrize("bad", ["orange", "RED", "", "lightyellow"])
def test_set_lineage_color_label_rejects_unknown(gw, bad):
    rel = "Exported Media/Dia 1/p1.jpg"
    with pytest.raises(ValueError):
        gw.set_lineage_color_label(rel, bad)


# ── flag + to_delete ────────────────────────────────────────────────


def test_set_lineage_flag_round_trip(gw):
    rel = "Exported Media/Dia 1/p1.jpg"
    assert gw.lineage_ratings(rel).flag is False
    gw.set_lineage_flag(rel, True)
    assert gw.lineage_ratings(rel).flag is True
    gw.set_lineage_flag(rel, False)
    assert gw.lineage_ratings(rel).flag is False


def test_set_lineage_to_delete_round_trip(gw):
    rel = "Exported Media/Dia 1/p1.jpg"
    assert gw.lineage_ratings(rel).to_delete is False
    gw.set_lineage_to_delete(rel, True)
    assert gw.lineage_ratings(rel).to_delete is True
    gw.set_lineage_to_delete(rel, False)
    assert gw.lineage_ratings(rel).to_delete is False


# ── lineage_ratings ────────────────────────────────────────────────


def test_lineage_ratings_reads_all_four(gw):
    rel = "Exported Media/Dia 1/p1.jpg"
    gw.set_lineage_stars(rel, 4)
    gw.set_lineage_color_label(rel, "green")
    gw.set_lineage_flag(rel, True)
    gw.set_lineage_to_delete(rel, True)
    assert gw.lineage_ratings(rel) == LineageRatings(
        stars=4, color_label="green", flag=True, to_delete=True)


def test_lineage_ratings_returns_defaults_for_missing_row(gw):
    assert gw.lineage_ratings("Exported Media/does/not/exist.jpg") == \
        LineageRatings(None, None, False, False)


def test_unrated_row_reads_clean(gw):
    """A row that was never touched by spec/159 mutators reads as
    unrated / unflagged / not-marked — confirms the v23 migration's
    defaults round-trip via the Lineage dataclass."""
    rel = "Exported Media/Dia 1/p2.jpg"
    assert gw.lineage_ratings(rel) == LineageRatings(
        None, None, False, False)


# ── exported_marked_for_deletion ───────────────────────────────────


def test_exported_marked_for_deletion_empty_by_default(gw):
    assert gw.exported_marked_for_deletion() == []


def test_exported_marked_for_deletion_returns_only_flagged_rows(gw):
    a = "Exported Media/Dia 1/p1.jpg"
    b = "Exported Media/Dia 1/p1_LRC.jpg"
    gw.set_lineage_to_delete(a, True)
    gw.set_lineage_to_delete(b, True)
    # p2.jpg is not flagged — must not appear.
    rels = [row.export_relpath
            for row in gw.exported_marked_for_deletion()]
    assert rels == sorted([a, b])


# ── delete_marked_exported_files ───────────────────────────────────


def test_delete_marked_exported_files_unlinks_and_drops_rows(
        gw, event_dir):
    a = "Exported Media/Dia 1/p1.jpg"
    b = "Exported Media/Dia 1/p2.jpg"
    gw.set_lineage_to_delete(a, True)
    gw.set_lineage_to_delete(b, True)
    n = gw.delete_marked_exported_files()
    assert n == 2
    # Files are gone on disk.
    assert not (event_dir / a).exists()
    assert not (event_dir / b).exists()
    # Lineage rows for those relpaths are gone.
    rels = {row.export_relpath for row in gw.exported_files_all()}
    assert a not in rels
    assert b not in rels
    # The unflagged p1_LRC version survives — its row was never marked.
    assert "Exported Media/Dia 1/p1_LRC.jpg" in rels


def test_delete_marked_exported_files_zero_when_nothing_marked(gw):
    assert gw.delete_marked_exported_files() == 0


def test_delete_marked_exported_files_only_targets_flagged_versions(
        gw, event_dir):
    """One of p1's two versions marked; the other version survives,
    keeping the Mira-render + LRC-return split per spec/159 §2.1."""
    mira_version = "Exported Media/Dia 1/p1.jpg"
    lr_version = "Exported Media/Dia 1/p1_LRC.jpg"
    gw.set_lineage_to_delete(mira_version, True)
    n = gw.delete_marked_exported_files()
    assert n == 1
    assert not (event_dir / mira_version).exists()
    assert (event_dir / lr_version).exists()
    rels = {row.export_relpath for row in gw.exported_files_all()}
    assert mira_version not in rels
    assert lr_version in rels


# ── preferred-version flag (§6+) ────────────────────────────────────


def test_lineage_ratings_carries_preferred(gw):
    """The bag read should include ``is_preferred=False`` by default
    and flip when ``set_lineage_preferred`` writes it."""
    rel = "Exported Media/Dia 1/p1.jpg"
    assert gw.lineage_ratings(rel).is_preferred is False
    gw.set_lineage_preferred(rel, True)
    assert gw.lineage_ratings(rel).is_preferred is True


def test_set_lineage_preferred_clears_siblings(gw):
    """Setting one of p1's versions preferred clears any sibling row
    that previously held the flag — at-most-one-per-source invariant."""
    mira_v = "Exported Media/Dia 1/p1.jpg"
    lrc_v = "Exported Media/Dia 1/p1_LRC.jpg"
    gw.set_lineage_preferred(mira_v, True)
    assert gw.lineage_ratings(mira_v).is_preferred is True
    assert gw.lineage_ratings(lrc_v).is_preferred is False
    # Flip the preferred to the LRC version — the Mira flag clears.
    gw.set_lineage_preferred(lrc_v, True)
    assert gw.lineage_ratings(mira_v).is_preferred is False
    assert gw.lineage_ratings(lrc_v).is_preferred is True


def test_set_lineage_preferred_clear_only_affects_this_row(gw):
    """Toggling preferred=False on one row never touches sibling
    rows — the gateway doesn't auto-promote another version."""
    mira_v = "Exported Media/Dia 1/p1.jpg"
    lrc_v = "Exported Media/Dia 1/p1_LRC.jpg"
    gw.set_lineage_preferred(mira_v, True)
    gw.set_lineage_preferred(mira_v, False)
    assert gw.lineage_ratings(mira_v).is_preferred is False
    assert gw.lineage_ratings(lrc_v).is_preferred is False


def test_preferred_for_item_returns_the_preferred_row(gw):
    """``preferred_for_item`` is the downstream lookup Cuts compose
    will use."""
    mira_v = "Exported Media/Dia 1/p1.jpg"
    assert gw.preferred_for_item("p1") is None
    gw.set_lineage_preferred(mira_v, True)
    pref = gw.preferred_for_item("p1")
    assert pref is not None
    assert pref.export_relpath == mira_v
    # Another source with no preferred reads None.
    assert gw.preferred_for_item("p2") is None


def test_set_lineage_preferred_independent_across_sources(gw):
    """Marking p1's Mira version preferred has zero effect on p2's
    row (different source_item_id, separate uniqueness scope)."""
    gw.set_lineage_preferred("Exported Media/Dia 1/p1.jpg", True)
    gw.set_lineage_preferred("Exported Media/Dia 1/p2.jpg", True)
    assert gw.lineage_ratings(
        "Exported Media/Dia 1/p1.jpg").is_preferred is True
    assert gw.lineage_ratings(
        "Exported Media/Dia 1/p2.jpg").is_preferred is True


def test_set_lineage_preferred_noop_when_row_missing(gw):
    """An UPDATE against a missing row matches zero rows — the gateway
    stays silent."""
    gw.set_lineage_preferred("Exported Media/missing.jpg", True)
    assert gw.preferred_for_item("p1") is None
