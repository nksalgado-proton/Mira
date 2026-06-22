"""Cross-event Cut detail viewer."""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway.gateway import CrossEventCutRow
from mira.store.repo import EventStore
from mira.ui.pages.cross_event_cut_detail_dialog import (
    CrossEventCutDetailDialog,
)


NOW = "2026-06-16T00:00:00+00:00"


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


def _seed(tmp_path, gw, photos_base, *,
          cut_id="cut-x", source_eid="src",
          source_members=2, anchor_members=1, grab_members=0):
    """spec/94 Phase 4a-ii: cross-event Cuts live in mira.db (spec/93 §3).
    Build a source event + an anchor event in the index (so their roots
    resolve), then seed the cut + members in the library store. Every
    member carries an explicit ``event_id`` — there is no NULL "anchor"
    fallback anymore."""
    from mira.gateway.index import make_entry

    # Source event registered in the index (its bytes don't need to
    # exist for the detail dialog).
    src = photos_base / "Source"
    src.mkdir(exist_ok=True)
    store = EventStore.create(
        src / "event.db", event_id=source_eid,
        app_version="test", created_at=NOW)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)",
            (source_eid, "Source event", NOW, NOW))
    store.close()
    gw.index.upsert(make_entry(
        event_id=source_eid, name="Source event",
        start_date=None, end_date=None, is_closed=False,
        event_root=src, photos_base_path=photos_base))

    # Anchor event in the index too — the row's display attribution
    # falls back to whichever member's event_id it sees first.
    anchor = photos_base / "Anchor"
    anchor.mkdir(exist_ok=True)
    store = EventStore.create(
        anchor / "event.db", event_id="anchor",
        app_version="test", created_at=NOW)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", ("anchor", "Anchor event", NOW, NOW))
    store.close()
    gw.index.upsert(make_entry(
        event_id="anchor", name="Anchor event",
        start_date=None, end_date=None, is_closed=False,
        event_root=anchor, photos_base_path=photos_base))

    # Seed the cut + members in mira.db (the library store).
    lg = gw.library_gateway()
    with lg.user_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, "
            "                 created_at, updated_at) "
            "VALUES (?, ?, 'user', ?, ?)",
            (cut_id, "test_tag", NOW, NOW))
    members: list = []
    for i in range(anchor_members):
        members.append({
            "event_id": "anchor", "kind": "export",
            "export_relpath": f"Exported Media/anchor{i}.jpg",
        })
    for i in range(source_members):
        members.append({
            "event_id": source_eid, "kind": "export",
            "export_relpath": f"Exported Media/src{i}.jpg",
        })
    for i in range(grab_members):
        members.append({
            "event_id": source_eid, "kind": "grab",
            "origin_relpath": f"Original Media/raw{i}.raw",
        })
    if members:
        lg.set_cross_event_cut_members(cut_id, members)


def _row(cut_id="cut-x", *, member_count=3) -> CrossEventCutRow:
    return CrossEventCutRow(
        cut_id=cut_id, tag="test_tag",
        anchor_event_id="anchor",
        anchor_event_name="Anchor event",
        source_dc_id="sf-1",
        member_count=member_count,
        last_exported_at=None,
        created_at=NOW, updated_at=NOW,
    )


# --------------------------------------------------------------------------- #
# Empty cut
# --------------------------------------------------------------------------- #


def test_empty_cut_shows_no_members(qapp, tmp_path):
    gw, photos_base = _make_umbrella(tmp_path)
    _seed(tmp_path, gw, photos_base,
          source_members=0, anchor_members=0)
    d = CrossEventCutDetailDialog(gw, _row(member_count=0))
    groups = d._fetch_member_groups()
    assert groups == []
    d.deleteLater()
    gw.close()


# --------------------------------------------------------------------------- #
# Mixed members — anchor + cross-event
# --------------------------------------------------------------------------- #


def test_groups_anchor_first_then_cross_event(qapp, tmp_path):
    """Members are grouped by source event. spec/94 Phase 4a-ii: every
    member has an explicit ``event_id`` (no NULL "anchor" fallback);
    LibraryGateway returns rows ordered by ``event_id``."""
    gw, photos_base = _make_umbrella(tmp_path)
    _seed(tmp_path, gw, photos_base,
          anchor_members=2, source_members=3)
    d = CrossEventCutDetailDialog(gw, _row(member_count=5))
    groups = d._fetch_member_groups()
    assert len(groups) == 2
    event_ids = [g[0] for g in groups]
    assert set(event_ids) == {"anchor", "src"}
    sizes = {g[0]: len(g[1]) for g in groups}
    assert sizes == {"anchor": 2, "src": 3}
    d.deleteLater()
    gw.close()


def test_grab_members_render_with_origin_relpath(qapp, tmp_path):
    """Grab members surface their origin_relpath in the detail."""
    gw, photos_base = _make_umbrella(tmp_path)
    _seed(tmp_path, gw, photos_base,
          anchor_members=0, source_members=0, grab_members=2)
    d = CrossEventCutDetailDialog(gw, _row(member_count=2))
    groups = d._fetch_member_groups()
    assert groups[0][0] == "src"
    relpaths = [r["origin_relpath"] for r in groups[0][1]]
    assert all("Original Media/" in p for p in relpaths)
    d.deleteLater()
    gw.close()


# --------------------------------------------------------------------------- #
# Missing source event surfaces a label
# --------------------------------------------------------------------------- #


def test_missing_source_event_renders_missing_label(qapp, tmp_path):
    """When event_id points at an event no longer in the index, the
    group's label reads `(missing)`."""
    from mira.ui.pages.cross_event_cut_detail_dialog import _event_label
    gw, photos_base = _make_umbrella(tmp_path)
    label = _event_label(gw, "ghost", "anchor", "Anchor event")
    assert "missing" in label
    gw.close()


def test_anchor_event_label_marked_as_anchor(qapp, tmp_path):
    from mira.ui.pages.cross_event_cut_detail_dialog import _event_label
    gw, photos_base = _make_umbrella(tmp_path)
    label = _event_label(gw, None, "anchor", "Anchor event")
    assert "anchor" in label.lower()
    gw.close()


# --------------------------------------------------------------------------- #
# Unresolvable anchor — empty groups
# --------------------------------------------------------------------------- #


def test_unresolvable_anchor_returns_empty(qapp, tmp_path):
    gw, photos_base = _make_umbrella(tmp_path)
    row = CrossEventCutRow(
        cut_id="cut-x", tag="x",
        anchor_event_id="not-in-index",
        anchor_event_name="Ghost",
        source_dc_id=None,
        member_count=0,
        last_exported_at=None,
        created_at=NOW, updated_at=NOW)
    d = CrossEventCutDetailDialog(gw, row)
    assert d._fetch_member_groups() == []
    d.deleteLater()
    gw.close()
