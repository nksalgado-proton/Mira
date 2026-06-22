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
                          cross_event_cut_count: int = 0,
                          gateway=None) -> Path:
    """Build an event.db with some event-scope cuts AND seed
    cross-event cuts in mira.db (spec/94 Phase 4a-ii). The event-scope
    cuts stay per-event; the cross-event ones live in the library
    store.

    ``gateway`` (the umbrella) must be passed when ``cross_event_cut_count
    > 0`` — that's the library store the cross-event rows go into. Each
    cross-event cut is given one member from this event so the umbrella
    list path attributes them a representative "anchor".

    Returns the event_root."""
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
    store.close()
    if cross_event_cut_count and gateway is not None:
        lg = gateway.library_gateway()
        for i in range(cross_event_cut_count):
            cut_id = f"{eid}-cross-cut-{i}"
            with lg.user_store.transaction() as conn:
                conn.execute(
                    "INSERT INTO cut (id, tag, source_dc_kind, "
                    "source_dc_id, created_at, updated_at) "
                    "VALUES (?, ?, 'user', ?, ?, ?)",
                    (cut_id, f"cross_{eid}_{i}", "sf-1", NOW, NOW))
            lg.set_cross_event_cut_members(cut_id, [
                {"event_id": eid, "kind": "export",
                 "export_relpath": f"Exported Media/p{i}.jpg"}])
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
    """spec/94 Phase 4a-ii: cross-event Cuts live in mira.db, so the
    umbrella ``cross_event_cuts`` reads only from there. event.db
    event-scope cuts are never visited."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event_with_cuts(
        photos_base, eid="e1", name="E1",
        event_cut_count=2, cross_event_cut_count=3,
        gateway=gw)
    _register(gw, photos_base, r, eid="e1", name="E1")
    rows = gw.cross_event_cuts()
    assert len(rows) == 3
    assert all(isinstance(r, CrossEventCutRow) for r in rows)
    # The first member's event_id becomes the row's "anchor" display.
    assert all(r.anchor_event_id == "e1" for r in rows)
    gw.close()


def test_cross_event_cuts_spans_every_event(tmp_path):
    """spec/94 Phase 4a-ii: cross-event Cuts live in mira.db; members
    from multiple events all sit side-by-side. The list returns every
    Cut once, regardless of how many events it spans."""
    gw, photos_base = _make_umbrella(tmp_path)
    r1 = _seed_event_with_cuts(
        photos_base, eid="e1", name="E1",
        cross_event_cut_count=2, gateway=gw)
    r2 = _seed_event_with_cuts(
        photos_base, eid="e2", name="E2",
        cross_event_cut_count=1, gateway=gw)
    _register(gw, photos_base, r1, eid="e1", name="E1")
    _register(gw, photos_base, r2, eid="e2", name="E2")
    rows = gw.cross_event_cuts()
    assert {r.anchor_event_id for r in rows} == {"e1", "e2"}
    assert len(rows) == 3
    gw.close()


def test_cross_event_cuts_skips_unopenable_events(tmp_path):
    """spec/94 Phase 4a-ii: cross-event Cuts live in mira.db, so an
    unopenable event.db never blocks the list. The Cuts still
    enumerate; their "anchor" attribution may fall back to the raw
    uuid when the entry's missing."""
    gw, photos_base = _make_umbrella(tmp_path)
    # Register an event whose root doesn't exist on disk.
    ghost = photos_base / "Gone"
    ghost.mkdir()
    _register(gw, photos_base, ghost, eid="gone", name="Gone")
    # Also a real event.
    r = _seed_event_with_cuts(
        photos_base, eid="e1", name="E1",
        cross_event_cut_count=1, gateway=gw)
    _register(gw, photos_base, r, eid="e1", name="E1")
    rows = gw.cross_event_cuts()
    assert len(rows) == 1
    assert rows[0].anchor_event_id == "e1"
    gw.close()


def test_cross_event_cuts_member_count(tmp_path):
    """Each row reports the Cut's member count from mira.db. The
    LibraryGateway query is one SELECT — no event.db walks."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event_with_cuts(
        photos_base, eid="e1", name="E1",
        cross_event_cut_count=1, gateway=gw)
    # Replace the single seeded member with 3.
    lg = gw.library_gateway()
    lg.set_cross_event_cut_members("e1-cross-cut-0", [
        {"event_id": "e1", "kind": "export",
         "export_relpath": f"Exported Media/p{i}.jpg"}
        for i in range(3)
    ])
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
            cross_event_cut_count=num_cuts, gateway=gw)
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
