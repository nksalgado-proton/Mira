"""spec/159 — the DCDetailPage's review-grid behaviours.

Pins the surface delta:

* Border-click on a cell flips ``to_delete`` on the lineage row
  (writes through the gateway), updates the cell's badge, and
  reveals the toolbar "⌫ Delete N marked…" affordance.
* Center-click emits ``review_requested`` carrying the export
  relpath (Session B will bind it to the editor; Session A only
  pins the signal).
* "Clear marks" releases ``to_delete`` on every visible row in one
  shot.
* The toolbar's delete confirm fires
  :meth:`EventGateway.delete_marked_exported_files` once.

Loads the same lineage / file fixture the gateway tests use to keep
the cascade-side behaviour realistic (an actual ``Exported Media/``
tree exists on disk so the unlink path doesn't no-op).
"""
from __future__ import annotations

import itertools
from pathlib import Path
from unittest.mock import patch

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.shared.dc_detail_page import DCDetailPage

FIXED_NOW = "2026-06-30T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-d", name="DC review fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        m.Item(id="p1", kind="photo", created_at=FIXED_NOW,
               provenance="captured",
               origin_relpath="Original Media/p1.jpg", sha256="a" * 64,
               byte_size=1000, materialized_at=FIXED_NOW,
               materialized_phase="ingest",
               camera_id="G9", day_number=1,
               capture_time_raw="2026-04-01T08:00:00",
               capture_time_corrected="2026-04-01T08:00:00"),
        m.Item(id="p2", kind="photo", created_at=FIXED_NOW,
               provenance="captured",
               origin_relpath="Original Media/p2.jpg", sha256="b" * 64,
               byte_size=1000, materialized_at=FIXED_NOW,
               materialized_phase="ingest",
               camera_id="G9", day_number=1,
               capture_time_raw="2026-04-01T09:00:00",
               capture_time_corrected="2026-04-01T09:00:00"),
    ]
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/Dia 1/p1.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p1", exported_at="t1"),
        m.Lineage(export_relpath="Exported Media/Dia 1/p2.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p2", exported_at="t2"),
    ]
    doc.adjustments = [
        m.Adjustment(item_id="p1", edit_exported=True),
        m.Adjustment(item_id="p2", edit_exported=True),
    ]
    return doc


@pytest.fixture
def event_dir(tmp_path):
    (tmp_path / "Exported Media" / "Dia 1").mkdir(parents=True)
    for name in ("p1.jpg", "p2.jpg"):
        (tmp_path / "Exported Media" / "Dia 1" / name).write_bytes(
            b"\xff\xd8\xff\xd9")
    return tmp_path


@pytest.fixture
def gw(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-d")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def page(qapp, gw):
    p = DCDetailPage()
    p.open_pool(gw)
    yield p
    p.close_event()


# ── border-click toggles to_delete ───────────────────────────────────


def test_border_click_toggles_to_delete(page, gw):
    """The lineage row's ``to_delete`` flag is the source of truth;
    the cell's badge follows it. Border-click flips, second click
    flips back."""
    rel = "Exported Media/Dia 1/p1.jpg"
    assert gw.lineage_ratings(rel).to_delete is False

    page._on_cell_border_clicked(0)
    assert gw.lineage_ratings(rel).to_delete is True

    page._on_cell_border_clicked(0)
    assert gw.lineage_ratings(rel).to_delete is False


def test_border_click_only_affects_target_row(page, gw):
    a = "Exported Media/Dia 1/p1.jpg"
    b = "Exported Media/Dia 1/p2.jpg"
    page._on_cell_border_clicked(0)
    assert gw.lineage_ratings(a).to_delete is True
    assert gw.lineage_ratings(b).to_delete is False


# ── chrome reflects the marked count ────────────────────────────────


def test_delete_button_hidden_when_no_marks(page):
    # Check the explicit visibility flag (isHidden tracks setVisible
    # independent of whether the parent widget has been shown).
    assert page._delete_btn.isHidden() is True
    assert page._clear_btn.isHidden() is True


def test_delete_button_visible_with_count_after_mark(page):
    page._on_cell_border_clicked(0)
    page._update_chrome()
    assert page._delete_btn.isHidden() is False
    assert "1" in page._delete_btn.text()
    page._on_cell_border_clicked(1)
    page._update_chrome()
    assert "2" in page._delete_btn.text()


def test_delete_button_label_carries_the_marked_emoji_glyph(page):
    page._on_cell_border_clicked(0)
    page._update_chrome()
    assert page._delete_btn.text().startswith("⌫")


# ── clear marks ─────────────────────────────────────────────────────


def test_clear_marks_releases_every_visible_row(page, gw):
    page._on_cell_border_clicked(0)
    page._on_cell_border_clicked(1)
    assert len(page._marked_relpaths()) == 2
    page._clear_marks()
    assert page._marked_relpaths() == []
    # And in the gateway too.
    for rel in ("Exported Media/Dia 1/p1.jpg",
                "Exported Media/Dia 1/p2.jpg"):
        assert gw.lineage_ratings(rel).to_delete is False


# ── center-click emits review_requested ─────────────────────────────


def test_center_click_emits_review_requested(page):
    received = []
    page.review_requested.connect(received.append)
    page._on_cell_activated(0)
    assert received == ["Exported Media/Dia 1/p1.jpg"]


def test_center_click_does_not_toggle_to_delete(page, gw):
    rel = "Exported Media/Dia 1/p1.jpg"
    page._on_cell_activated(0)
    assert gw.lineage_ratings(rel).to_delete is False


# ── confirm dialog runs the batch delete ────────────────────────────


def test_delete_clicked_runs_batch_when_user_accepts(
        page, gw, event_dir, monkeypatch):
    """Stub the confirm dialog to ACCEPT and check the batch helper
    fired. The dialog's exact UX is tested elsewhere; this pins the
    wiring."""
    page._on_cell_border_clicked(0)
    page._on_cell_border_clicked(1)

    # Stub _delete_batch_with_confirm to call the gateway helper
    # directly (the confirm modal can't run in a non-interactive
    # test environment).
    def _accept(relpaths):
        gw.delete_marked_exported_files()
        page._refresh()

    monkeypatch.setattr(page, "_delete_batch_with_confirm", _accept)

    page._on_delete_clicked()

    # Both files are gone.
    assert not (event_dir / "Exported Media/Dia 1/p1.jpg").exists()
    assert not (event_dir / "Exported Media/Dia 1/p2.jpg").exists()
    assert page._marked_relpaths() == []


def test_delete_clicked_noop_when_nothing_marked(page, monkeypatch):
    """No mark → no confirm dialog → no-op. The handler returns
    early before reaching the batch helper."""
    called = []
    monkeypatch.setattr(
        page, "_delete_batch_with_confirm",
        lambda relpaths: called.append(relpaths))
    page._on_delete_clicked()
    assert called == []
