"""Cross-event Cuts list — gateway walk + browser dialog."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mira.gateway.gateway import CrossEventCutRow
from mira.store.repo import EventStore
from mira.ui.pages.cross_event_cuts_dialog import (
    CrossEventCutsDialog,
    _CutRow,
)


NOW = "2026-06-16T00:00:00+00:00"


def _make_umbrella(tmp_path):
    """Same fixture as test_phase2_wiring — umbrella with primed user_store."""
    from mira.gateway.gateway import Gateway
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo

    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    photos_base = tmp_path / "photos"
    photos_base.mkdir(exist_ok=True)
    gw = Gateway(
        settings=settings, index=index,
        user_store_path=tmp_path / "mira.db",
        now=lambda: NOW,
        installation_profile="XMC",
    )
    _ = gw.user_store
    settings.update(photos_base_path=str(photos_base))
    return gw, photos_base


def _seed_event_with_cuts(photos_base: Path, *, eid: str, name: str,
                          event_cut_count: int = 0,
                          cross_event_cut_count: int = 0) -> Path:
    """Build an event.db with some cuts split between event-scope and
    cross-event (source_dc_kind = 'user'). Returns the event_root."""
    root = photos_base / name
    root.mkdir(exist_ok=True)
    store = EventStore.create(
        root / "event.db", event_id=eid, app_version="test",
        created_at=NOW,
    )
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, name, NOW, NOW))
        for i in range(event_cut_count):
            conn.execute(
                "INSERT INTO cut (id, tag, source_dc_kind, created_at, "
                "updated_at) VALUES (?, ?, 'event', ?, ?)",
                (f"{eid}-evt-cut-{i}", f"evt_{eid}_{i}", NOW, NOW))
        for i in range(cross_event_cut_count):
            conn.execute(
                "INSERT INTO cut (id, tag, source_dc_kind, "
                "source_dc_id, created_at, updated_at) "
                "VALUES (?, ?, 'user', ?, ?, ?)",
                (f"{eid}-cross-cut-{i}", f"cross_{eid}_{i}",
                 "sf-1", NOW, NOW))
    store.close()
    return root


def _register(gw, photos_base, root: Path, *, eid: str, name: str) -> None:
    from mira.gateway.index import make_entry
    gw.index.upsert(make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root, photos_base_path=photos_base,
    ))


# --------------------------------------------------------------------------- #
# Gateway.cross_event_cuts — multi-event walk
# --------------------------------------------------------------------------- #


def test_cross_event_cuts_lists_only_user_kind_cuts(tmp_path):
    """Only ``source_dc_kind = 'user'`` cuts appear — event-scope cuts
    don't leak into the cross-event surface."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event_with_cuts(
        photos_base, eid="e1", name="E1",
        event_cut_count=2, cross_event_cut_count=3)
    _register(gw, photos_base, r, eid="e1", name="E1")
    rows = gw.cross_event_cuts()
    assert len(rows) == 3
    assert all(isinstance(r, CrossEventCutRow) for r in rows)
    assert all(r.anchor_event_id == "e1" for r in rows)
    gw.close()


def test_cross_event_cuts_spans_every_event(tmp_path):
    """The walk visits every event.db in the index."""
    gw, photos_base = _make_umbrella(tmp_path)
    r1 = _seed_event_with_cuts(
        photos_base, eid="e1", name="E1", cross_event_cut_count=2)
    r2 = _seed_event_with_cuts(
        photos_base, eid="e2", name="E2", cross_event_cut_count=1)
    _register(gw, photos_base, r1, eid="e1", name="E1")
    _register(gw, photos_base, r2, eid="e2", name="E2")
    rows = gw.cross_event_cuts()
    assert {r.anchor_event_id for r in rows} == {"e1", "e2"}
    assert len(rows) == 3
    gw.close()


def test_cross_event_cuts_skips_unopenable_events(tmp_path):
    """An event whose store can't open is skipped + logged, never raised."""
    gw, photos_base = _make_umbrella(tmp_path)
    # Register an event whose root doesn't exist on disk.
    ghost = photos_base / "Gone"
    ghost.mkdir()
    _register(gw, photos_base, ghost, eid="gone", name="Gone")
    # Also a real event.
    r = _seed_event_with_cuts(
        photos_base, eid="e1", name="E1", cross_event_cut_count=1)
    _register(gw, photos_base, r, eid="e1", name="E1")
    rows = gw.cross_event_cuts()
    assert len(rows) == 1
    assert rows[0].anchor_event_id == "e1"
    gw.close()


def test_cross_event_cuts_member_count(tmp_path):
    """Each row reports the cut's member count."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event_with_cuts(
        photos_base, eid="e1", name="E1", cross_event_cut_count=1)
    # Seed 3 cut_member rows directly.
    store = EventStore.open(r / "event.db")
    with store.transaction() as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO cut_member (cut_id, member_id, kind, "
                "export_relpath, added_at) "
                "VALUES (?, ?, 'export', ?, ?)",
                ("e1-cross-cut-0", f"Exported Media/p{i}.jpg",
                 f"Exported Media/p{i}.jpg", NOW))
    store.close()
    _register(gw, photos_base, r, eid="e1", name="E1")
    rows = gw.cross_event_cuts()
    assert rows[0].member_count == 3
    gw.close()


# --------------------------------------------------------------------------- #
# CrossEventCutsDialog — list view, refresh, delete
# --------------------------------------------------------------------------- #


def _open_dialog(qapp, tmp_path, *, num_cuts: int = 0):
    gw, photos_base = _make_umbrella(tmp_path)
    if num_cuts:
        r = _seed_event_with_cuts(
            photos_base, eid="e1", name="E1",
            cross_event_cut_count=num_cuts)
        _register(gw, photos_base, r, eid="e1", name="E1")
    dialog = CrossEventCutsDialog(gw)
    return dialog, gw


def test_dialog_empty_shows_hint(qapp, tmp_path):
    d, gw = _open_dialog(qapp, tmp_path)
    assert not d._empty_label.isHidden()
    gw.close()
    d.deleteLater()


def test_dialog_lists_each_cut(qapp, tmp_path):
    d, gw = _open_dialog(qapp, tmp_path, num_cuts=2)
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _CutRow)]
    assert len(rows) == 2
    assert d._empty_label.isHidden()
    gw.close()
    d.deleteLater()


def test_dialog_delete_removes_row(qapp, tmp_path, monkeypatch):
    d, gw = _open_dialog(qapp, tmp_path, num_cuts=2)
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes)
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _CutRow)]
    d._on_delete(rows[0]._row)
    refreshed = [d._rows_layout.itemAt(i).widget()
                 for i in range(d._rows_layout.count())
                 if isinstance(d._rows_layout.itemAt(i).widget(), _CutRow)]
    assert len(refreshed) == 1
    gw.close()
    d.deleteLater()


def test_dialog_export_emits_signal(qapp, tmp_path):
    d, gw = _open_dialog(qapp, tmp_path, num_cuts=1)
    fired: list = []
    d.export_requested.connect(lambda row: fired.append(row))
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _CutRow)]
    rows[0].export_requested.emit(rows[0]._row)
    assert len(fired) == 1
    assert isinstance(fired[0], CrossEventCutRow)
    gw.close()
    d.deleteLater()
