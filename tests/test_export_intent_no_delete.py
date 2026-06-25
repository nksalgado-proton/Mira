"""spec/147 §1 — intent ≠ deletion.

The pre-spec/147 Export surface conflated *Set aside* intent with
actual file deletion: marking an item red would silently delete its
``Exported Media/`` file on the next "Export now" run, and a single
X-on-shipped flat cell would erase the export immediately. spec/147
RETIRES that coupling — "Set aside" is intent only; deletion is the
parallel explicit verb ("Delete now · M").

Three contracts pinned here:

* :meth:`EventGateway.set_items_phase_state` to ``'skipped'`` does
  NOT delete the on-disk file — the lineage row + the bytes survive,
  ``edit_exported`` stays True.
* The DaysGridPage "Set all aside" bulk verb (the renamed Drop-all)
  goes through the same code path — no on-disk blast.
* The "Export now" batch run is RENDER-ONLY; even when the day has
  Set-aside files, Export now does not touch them.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore

NOW = "2026-06-25T00:00:00+00:00"


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-int", name="Intent fixture",
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
        for iid in ("k1", "k2", "k3")
    ]
    doc.lineage = [
        m.Lineage(
            export_relpath=f"Exported Media/{iid}.jpg",
            phase="edit", source_kind="item", source_item_id=iid,
            exported_at="t1", intent_state="picked",
        )
        for iid in ("k1", "k2", "k3")
    ]
    doc.adjustments = [
        m.Adjustment(item_id=iid, edit_exported=True)
        for iid in ("k1", "k2", "k3")
    ]
    return doc


@pytest.fixture
def event_dir(tmp_path):
    (tmp_path / "Exported Media").mkdir(parents=True)
    for iid in ("k1", "k2", "k3"):
        (tmp_path / "Exported Media" / f"{iid}.jpg").write_bytes(
            b"\xff\xd8\xff\xd9")
    return tmp_path


@pytest.fixture
def gw(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-int")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=event_dir, now=lambda: NOW,
        new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# --------------------------------------------------------------------- #
# 1. Set aside (skipped) NEVER deletes the file
# --------------------------------------------------------------------- #


def test_set_phase_state_skipped_does_not_delete_file(gw, event_dir):
    """spec/147 §1 — marking a shipped item Set aside ("skipped")
    leaves the on-disk file in place, leaves the lineage row in
    place, and leaves ``edit_exported`` set. Deletion is a separate
    explicit verb."""
    rel = "Exported Media/k1.jpg"
    file_path = event_dir / rel
    assert file_path.is_file()
    # Move k1 from undecided → skipped (the Set aside intent).
    gw.set_items_phase_state(["k1"], "edit", "skipped")

    # File is still there.
    assert file_path.is_file(), (
        "spec/147 §1 — Set aside MUST NOT delete the file")
    # Lineage row survives.
    rows = list(gw.versions_for_item("k1"))
    assert len(rows) == 1
    assert rows[0].export_relpath == rel
    # And edit_exported stays True — the user can flip the intent
    # back to Will export without re-rendering.
    adj = gw.adjustment("k1")
    assert adj is not None
    assert adj.edit_exported is True


def test_bulk_set_aside_does_not_delete_any_files(gw, event_dir):
    """spec/147 §1 — Set ALL aside (the renamed Drop-all bulk verb)
    in Export mode is intent only. Every shipped file in the bulk
    set stays on disk; the user clears them via Delete now · M."""
    rels = [
        event_dir / "Exported Media" / "k1.jpg",
        event_dir / "Exported Media" / "k2.jpg",
        event_dir / "Exported Media" / "k3.jpg",
    ]
    assert all(p.is_file() for p in rels)
    # Bulk Set aside across the whole day — the underlying gateway
    # call the renamed UI bulk goes through.
    gw.set_items_phase_state(["k1", "k2", "k3"], "edit", "skipped")
    assert all(p.is_file() for p in rels)


# --------------------------------------------------------------------- #
# 2. Export now is render-only — Set-aside files survive
# --------------------------------------------------------------------- #


def test_set_aside_relpaths_helper_lists_only_skipped_intent(gw, event_dir):
    """spec/147 §2 — :meth:`set_aside_export_relpaths` is the Delete
    now · M source: it reads ``intent_state='skipped'`` lineage
    rows whose file still exists on disk. Will-export
    (``intent_state='picked'``) rows MUST NOT appear there, which is
    how Export now stays render-only and Delete now stays
    delete-only."""
    # Move k1 to Set aside; k2 + k3 stay Will export.
    gw.store.conn.execute(
        "UPDATE lineage SET intent_state = ? WHERE export_relpath = ?",
        ("skipped", "Exported Media/k1.jpg"))

    set_aside = gw.set_aside_export_relpaths()
    assert set_aside == ["Exported Media/k1.jpg"]


def test_set_aside_relpaths_helper_skips_missing_files(gw, event_dir):
    """A Set-aside lineage row whose file is gone (manually deleted)
    is NOT a Delete now target — Delete now can't unlink twice. The
    helper filters by ``is_file()`` so the live M count stays honest."""
    # Mark k1 Set aside but delete the file directly.
    gw.store.conn.execute(
        "UPDATE lineage SET intent_state = ? WHERE export_relpath = ?",
        ("skipped", "Exported Media/k1.jpg"))
    (event_dir / "Exported Media" / "k1.jpg").unlink()

    assert gw.set_aside_export_relpaths() == []
