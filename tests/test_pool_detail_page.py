"""The PoolDetailPage delete flow — multi-select, cascade-aware
confirm, single-cell undo (Nelson 2026-06-15 task).

Pins the page-level behaviour that builds on the engine guarantees
in ``test_pool_delete_cascade.py``:

* Click a cell → toggles the deletion mark; the delete button only
  appears when ≥1 cell is marked.
* Single-cell delete is quick (no confirm); Ctrl+Z restores the
  file bytes + lineage row.
* Multi-cell delete fires the confirm dialog whose body names the
  file count AND the unique Cut count via ``cuts_containing_any``.
* On confirmed batch delete: every selected lineage row drops + the
  FK CASCADE handles cut_member cleanup; the page refreshes its
  ``_files`` from the live ``exported_files()`` query.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.shared.pool_detail_page import PoolDetailPage

FIXED_NOW = "2026-06-15T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-pdp", name="Pool page fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        m.Item(
            id=f"p{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/p{i}.jpg",
            sha256=str(i) * 64, byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ) for i in range(1, 4)
    ]
    doc.lineage = [
        m.Lineage(
            export_relpath=f"Exported Media/Dia 1/p{i}.jpg",
            phase="edit", source_kind="item",
            source_item_id=f"p{i}", exported_at=f"t{i}")
        for i in range(1, 4)
    ]
    doc.cuts = [m.Cut(id="cut-1", tag="cut_one",
                      created_at=FIXED_NOW, updated_at=FIXED_NOW)]
    doc.cut_members = [
        m.CutMember(cut_id="cut-1",
                    export_relpath="Exported Media/Dia 1/p1.jpg",
                    added_at=FIXED_NOW),
        m.CutMember(cut_id="cut-1",
                    export_relpath="Exported Media/Dia 1/p2.jpg",
                    added_at=FIXED_NOW),
    ]
    doc.adjustments = [
        m.Adjustment(item_id=f"p{i}", edit_exported=True)
        for i in range(1, 4)
    ]
    return doc


@pytest.fixture
def event_dir(tmp_path):
    (tmp_path / "Exported Media" / "Dia 1").mkdir(parents=True)
    for i in range(1, 4):
        (tmp_path / "Exported Media" / "Dia 1" / f"p{i}.jpg").write_bytes(
            b"\xff\xd8" + bytes([i]) + b"\xff\xd9")
    return tmp_path


@pytest.fixture
def gw(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-pdp")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def page(qapp, gw):
    p = PoolDetailPage()
    p.open_pool(gw)
    yield p
    p.close_event()


# ── selection toggling ───────────────────────────────────────────────


def test_click_toggles_selection_and_reveals_delete_button(page):
    """The 'Delete' button is hidden until ≥1 cell is marked, and
    re-hides when the user clears the selection. Uses
    ``isVisibleTo(parent)`` so the visibility flag is read off the
    widget itself (the page never gets shown in this offscreen
    Qt test session)."""
    assert page._delete_btn.isVisibleTo(page) is False
    page._on_cell_activated(0)
    assert page._selected == {"Exported Media/Dia 1/p1.jpg"}
    assert page._delete_btn.isVisibleTo(page) is True
    assert "1" in page._delete_btn.text()
    page._on_cell_activated(0)
    assert page._selected == set()
    assert page._delete_btn.isVisibleTo(page) is False


def test_clear_selection_drops_every_mark(page):
    page._on_cell_activated(0)
    page._on_cell_activated(1)
    assert len(page._selected) == 2
    page._clear_selection()
    assert page._selected == set()
    assert page._delete_btn.isVisibleTo(page) is False


# ── single-cell quick delete + undo ──────────────────────────────────


def test_single_cell_delete_is_quick_no_confirm(page, gw, event_dir):
    """One selection → quick delete, no confirm dialog. The file +
    its lineage row are gone after."""
    target = event_dir / "Exported Media" / "Dia 1" / "p1.jpg"
    assert target.is_file()
    page._on_cell_activated(0)
    page._on_delete_clicked()
    assert not target.is_file()
    rels = {ln.export_relpath for ln in gw.exported_files()}
    assert "Exported Media/Dia 1/p1.jpg" not in rels


def test_single_cell_delete_pushes_an_undo_entry(page):
    page._on_cell_activated(0)
    assert len(page._undo_stack) == 0
    page._on_delete_clicked()
    assert len(page._undo_stack) == 1
    snap = page._undo_stack[-1]
    assert snap.export_relpath == "Exported Media/Dia 1/p1.jpg"
    assert snap.file_bytes != b""        # captured before unlink


def test_ctrl_z_restores_file_lineage_and_flag(page, gw, event_dir):
    """The undo restores file bytes + lineage row + edit_exported."""
    target = event_dir / "Exported Media" / "Dia 1" / "p1.jpg"
    original = target.read_bytes()
    page._on_cell_activated(0)
    page._on_delete_clicked()
    assert not target.is_file()
    page._on_undo()
    assert target.is_file()
    assert target.read_bytes() == original
    rels = {ln.export_relpath for ln in gw.exported_files()}
    assert "Exported Media/Dia 1/p1.jpg" in rels
    adj = gw.adjustment("p1")
    assert adj is not None and adj.edit_exported is True


def test_undo_with_empty_stack_is_a_noop(page, gw):
    """A Ctrl+Z with nothing to undo doesn't crash."""
    assert page._undo_stack == []
    page._on_undo()
    assert page._undo_stack == []
    assert len(gw.exported_files()) == 3


# ── batch delete + cascade-aware confirm ─────────────────────────────


def test_batch_confirm_dialog_names_cut_count(page, monkeypatch):
    """Selecting 2 files where 2 are in 1 Cut → the confirm body
    reads "2 file(s) ... 1 Cut(s)" before the user clicks anything."""
    captured: dict = {}

    class _FakeBox:
        Icon = type("Icon", (), {"Warning": 0})
        StandardButton = type("SB", (), {"Cancel": 1})
        ButtonRole = type("BR", (), {"DestructiveRole": 0})

        def __init__(self, *_a, **_kw):
            self._text = ""
            self._buttons = []
        def setIcon(self, *_a, **_kw): pass
        def setWindowTitle(self, t): captured["title"] = t
        def setText(self, t): captured["body"] = t
        def addButton(self, *a, **kw):
            btn = object()
            self._buttons.append(btn)
            return btn
        def setDefaultButton(self, *_a, **_kw): pass
        def exec(self):
            return None
        def clickedButton(self):
            return None                    # user cancelled

    monkeypatch.setattr(
        "mira.ui.shared.pool_detail_page.QMessageBox", _FakeBox)
    page._on_cell_activated(0)             # p1 → in cut-1
    page._on_cell_activated(1)             # p2 → in cut-1
    page._on_delete_clicked()
    assert "2" in captured["title"]
    assert "2 file(s)" in captured["body"]
    assert "1 Cut(s)" in captured["body"]
    assert "Originals and edits are untouched" in captured["body"]


def test_batch_confirm_body_no_cuts_branch(page, monkeypatch):
    """Selecting 1 file that's in NO Cut → the confirm body uses
    the no-Cut branch (no '... AND from M Cut(s)' clause)."""
    captured: dict = {}

    class _FakeBox:
        Icon = type("Icon", (), {"Warning": 0})
        StandardButton = type("SB", (), {"Cancel": 1})
        ButtonRole = type("BR", (), {"DestructiveRole": 0})
        def __init__(self, *_a, **_kw): self._buttons = []
        def setIcon(self, *_a, **_kw): pass
        def setWindowTitle(self, t): captured["title"] = t
        def setText(self, t): captured["body"] = t
        def addButton(self, *a, **kw):
            btn = object()
            self._buttons.append(btn)
            return btn
        def setDefaultButton(self, *_a, **_kw): pass
        def exec(self): return None
        def clickedButton(self): return None

    monkeypatch.setattr(
        "mira.ui.shared.pool_detail_page.QMessageBox", _FakeBox)
    # p3 is in NO cut + a duplicate to make it a batch (≥2).
    page._on_cell_activated(2)
    page._on_cell_activated(0)             # also p1 to keep it batch
    # Drop p1 to leave only p3 — but the page needs ≥2 to enter the
    # batch branch. Use p3 + a no-op route: pick a second non-cut
    # relpath by clearing then selecting two non-cut entries. The
    # fixture has only p3 outside cut-1, so monkeypatch
    # cuts_containing_any to return [] for our selection.
    monkeypatch.setattr(
        page._eg, "cuts_containing_any", lambda rels: [])
    page._on_delete_clicked()
    assert "aren't in any Cut" in captured["body"]


def test_batch_delete_cascades_cut_member_via_fk(
        page, gw, event_dir, monkeypatch):
    """On confirm, the loop runs through every selection — the FK
    CASCADE clears every cut_member row referencing the deleted
    lineage rows."""

    class _FakeBox:
        Icon = type("Icon", (), {"Warning": 0})
        StandardButton = type("SB", (), {"Cancel": 1})
        ButtonRole = type("BR", (), {"DestructiveRole": 0})
        def __init__(self, *_a, **_kw):
            self._delete_btn = object()
        def setIcon(self, *_a, **_kw): pass
        def setWindowTitle(self, *_a, **_kw): pass
        def setText(self, *_a, **_kw): pass
        def addButton(self, *a, **kw):
            return self._delete_btn      # ALWAYS the destructive button
        def setDefaultButton(self, *_a, **_kw): pass
        def exec(self): return None
        def clickedButton(self): return self._delete_btn

    monkeypatch.setattr(
        "mira.ui.shared.pool_detail_page.QMessageBox", _FakeBox)
    page._on_cell_activated(0)             # p1
    page._on_cell_activated(1)             # p2
    page._on_delete_clicked()
    # Both files unlinked.
    for rel in ("Exported Media/Dia 1/p1.jpg",
                "Exported Media/Dia 1/p2.jpg"):
        assert not (event_dir / rel).is_file()
    # Cut-1's members for those two files cleared via cascade; the
    # cut definition itself stays.
    members = gw.store.conn.execute(
        "SELECT export_relpath FROM cut_member WHERE cut_id = 'cut-1'"
    ).fetchall()
    assert members == []
    cuts_left = gw.cuts()
    assert len(cuts_left) == 1
    assert cuts_left[0].id == "cut-1"


def test_batch_delete_clears_undo_stack(page, monkeypatch):
    """The confirm IS the safety — a batch delete does NOT push onto
    the undo stack. (Single-cell delete pushes; batch does not.)"""

    class _FakeBox:
        Icon = type("Icon", (), {"Warning": 0})
        StandardButton = type("SB", (), {"Cancel": 1})
        ButtonRole = type("BR", (), {"DestructiveRole": 0})
        def __init__(self, *_a, **_kw):
            self._delete_btn = object()
        def setIcon(self, *_a, **_kw): pass
        def setWindowTitle(self, *_a, **_kw): pass
        def setText(self, *_a, **_kw): pass
        def addButton(self, *a, **kw):
            return self._delete_btn
        def setDefaultButton(self, *_a, **_kw): pass
        def exec(self): return None
        def clickedButton(self): return self._delete_btn

    monkeypatch.setattr(
        "mira.ui.shared.pool_detail_page.QMessageBox", _FakeBox)
    # First, single delete to seed an undo entry.
    page._on_cell_activated(2)
    page._on_delete_clicked()
    assert len(page._undo_stack) == 1
    # Then a batch delete — should clear the stack.
    page._on_cell_activated(0)
    page._on_cell_activated(1)
    page._on_delete_clicked()
    assert page._undo_stack == []
