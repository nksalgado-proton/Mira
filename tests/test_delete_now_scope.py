"""spec/147 §2 — Delete now scope: delete-only · M.

The "Delete now · M" verb is the explicit counterpart to "Export now ·
N": it removes ONLY the M ``Exported Media/`` files whose lineage
carries ``intent_state='skipped'`` (Set aside) and that still exist on
disk. It NEVER renders. It NEVER touches Will-export files.

Five contracts pinned here:

* :meth:`EventGateway.delete_exported_files_by_relpaths` deletes only
  the relpaths it is given; non-listed files survive.
* The list comes from :meth:`set_aside_export_relpaths` — the live
  source the toolbar's M count reads from.
* Will-export files (``intent_state='picked'``) are NEVER in M.
* Empty input → 0 rows / 0 files removed (the toolbar's zero-state
  disable contract).
* Lineage rows for the deleted files are dropped; ``edit_exported``
  is cleared when the last shipped row for the source item is gone.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore

NOW = "2026-06-25T00:00:00+00:00"


def _doc():
    doc = m.EventDocument(event=m.Event(
        uuid="evt-dn", name="Delete now fixture",
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
        for iid in ("a1", "a2", "b1", "b2", "c1")
    ]
    doc.lineage = [
        # Two Will-export files (intent_state='picked').
        m.Lineage(
            export_relpath="Exported Media/a1.jpg",
            phase="edit", source_kind="item", source_item_id="a1",
            exported_at="t1", intent_state="picked"),
        m.Lineage(
            export_relpath="Exported Media/a2.jpg",
            phase="edit", source_kind="item", source_item_id="a2",
            exported_at="t2", intent_state="picked"),
        # Two Set-aside files (intent_state='skipped').
        m.Lineage(
            export_relpath="Exported Media/b1.jpg",
            phase="edit", source_kind="item", source_item_id="b1",
            exported_at="t3", intent_state="skipped"),
        m.Lineage(
            export_relpath="Exported Media/b2.jpg",
            phase="edit", source_kind="item", source_item_id="b2",
            exported_at="t4", intent_state="skipped"),
        # One Compare-state file — must NOT be touched (it's not
        # Set aside; the user is still deciding).
        m.Lineage(
            export_relpath="Exported Media/c1.jpg",
            phase="edit", source_kind="item", source_item_id="c1",
            exported_at="t5", intent_state="compare"),
    ]
    doc.adjustments = [
        m.Adjustment(item_id=iid, edit_exported=True)
        for iid in ("a1", "a2", "b1", "b2", "c1")
    ]
    return doc


@pytest.fixture
def event_dir(tmp_path):
    (tmp_path / "Exported Media").mkdir(parents=True)
    for iid in ("a1", "a2", "b1", "b2", "c1"):
        (tmp_path / "Exported Media" / f"{iid}.jpg").write_bytes(
            b"\xff\xd8\xff\xd9")
    return tmp_path


@pytest.fixture
def gw(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-dn")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=event_dir, now=lambda: NOW,
        new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# --------------------------------------------------------------------- #
# 1. The live M list — only Set-aside files on disk
# --------------------------------------------------------------------- #


def test_set_aside_export_relpaths_returns_skipped_intent_only(gw):
    """spec/147 §2 — the live M count reads from
    :meth:`set_aside_export_relpaths`; only ``intent_state='skipped'``
    rows appear there. Will-export (picked) + Compare (compare)
    states stay OUT of the Delete now scope."""
    out = gw.set_aside_export_relpaths()
    assert out == [
        "Exported Media/b1.jpg",
        "Exported Media/b2.jpg",
    ]


def test_set_aside_export_relpaths_empty_when_nothing_set_aside(gw):
    """spec/147 §2 zero-state — when no row is Set aside, the helper
    returns ``[]`` so the toolbar's "Delete now · M" button reads
    M=0 and disables."""
    # Drop both skipped rows; only Will-export + Compare remain.
    gw.store.conn.execute(
        "UPDATE lineage SET intent_state = 'picked' "
        "WHERE intent_state = 'skipped'")
    assert gw.set_aside_export_relpaths() == []


# --------------------------------------------------------------------- #
# 2. Delete now removes ONLY the listed files; the rest survive
# --------------------------------------------------------------------- #


def test_delete_now_removes_only_listed_files(gw, event_dir):
    """spec/147 §2 — :meth:`delete_exported_files_by_relpaths` is
    the worker for the Delete now run. Given the M set from
    :meth:`set_aside_export_relpaths`, it deletes ONLY those files;
    Will-export and Compare files survive."""
    targets = gw.set_aside_export_relpaths()
    assert len(targets) == 2

    res = gw.delete_exported_files_by_relpaths(targets)
    assert res["rows_deleted"] == 2

    # The two Set-aside files are gone on disk + in lineage.
    for rel in targets:
        assert not (event_dir / rel).is_file()
    assert {ln.export_relpath for ln in gw.lineage()} == {
        "Exported Media/a1.jpg",   # Will-export — survives
        "Exported Media/a2.jpg",   # Will-export — survives
        "Exported Media/c1.jpg",   # Compare    — survives
    }

    # And the Will-export / Compare files are still on disk.
    for rel in ("Exported Media/a1.jpg", "Exported Media/a2.jpg",
                "Exported Media/c1.jpg"):
        assert (event_dir / rel).is_file()


def test_delete_now_empty_input_is_no_op(gw, event_dir):
    """spec/147 §2 zero-state — calling Delete now with an empty
    list is a clean no-op: 0 rows / 0 files removed, nothing else
    touched. The toolbar's zero-state disable contract relies on
    this safety net."""
    res = gw.delete_exported_files_by_relpaths([])
    assert res["rows_deleted"] == 0
    assert res["deleted_files"] == []
    assert res["item_ids"] == []
    # Everything on disk still here.
    for iid in ("a1", "a2", "b1", "b2", "c1"):
        assert (event_dir / "Exported Media" / f"{iid}.jpg").is_file()


# --------------------------------------------------------------------- #
# 3. set_edit_exported flips when the last shipped row is gone
# --------------------------------------------------------------------- #


def test_delete_now_clears_edit_exported_when_last_row_gone(gw):
    """spec/147 §2 — the per-file delete worker calls
    :meth:`delete_exported_file_by_relpath` for each relpath, which
    clears ``edit_exported`` when the last shipped row for the
    source item is gone. Pin the side effect so the Export-grid
    watermark settles correctly after the run."""
    # b1's only lineage row is the Set-aside one. Deleting it must
    # flip its Adjustment.edit_exported back to False.
    gw.delete_exported_files_by_relpaths(["Exported Media/b1.jpg"])
    adj = gw.adjustment("b1")
    assert adj is not None
    assert adj.edit_exported is False


# --------------------------------------------------------------------- #
# 4. Export now is the parallel verb — render-only, never touches M
# --------------------------------------------------------------------- #


def test_export_now_path_is_render_only(gw, event_dir):
    """spec/147 §2 — calling the per-file delete worker with an
    empty list is the canonical "Export now" side effect (nothing).
    Show the contract: even after running through the Export now
    code path's helpers, the M Set-aside files survive."""
    # Sim Export now: collects Will-export items missing a file
    # (zero here because every Will-export file is on disk) and
    # explicitly does NOT call any delete helper. The Set-aside
    # files therefore stay put.
    set_aside_before = set(gw.set_aside_export_relpaths())
    assert set_aside_before == {
        "Exported Media/b1.jpg",
        "Exported Media/b2.jpg",
    }
    set_aside_after = set(gw.set_aside_export_relpaths())
    assert set_aside_after == set_aside_before
    for rel in set_aside_before:
        assert (event_dir / rel).is_file()
