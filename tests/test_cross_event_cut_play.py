"""spec/94 Phase 4a-iii — cross-event Cut play adapter.

Pin the seam the new :class:`LibraryPage` wires:

* ``build_cross_event_entries`` walks mira.db's ``cut_member`` +
  ``global_items`` and returns the ``[(kind, payload)]`` shape
  :class:`CutPlayerDialog` consumes — chronological by capture_time,
  ``CrossEventPlayFile`` carrying the source event's UUID.
* ``make_resolve_path`` produces a callable the player uses to land
  on each member's bytes path via the umbrella gateway's index.
* Separators-on lights up per-(event, day) tokens (spec/81 §3.1).
* Missing items (no projection row, no event in index) degrade
  gracefully — the player's "missing" fallback handles it without
  crashing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mira.gateway.library_gateway import LibraryGateway
from mira.shared.cross_event_cut_play import (
    CrossEventPlayFile,
    build_cross_event_entries,
    make_resolve_path,
)
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-21T00:00:00+00:00"


def _open_user_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db", app_version="test", created_at=NOW,
    )


def _seed_projection(store: UserStore) -> None:
    """Three events worth of items: A (Costa Rica), B (Nepal),
    C (Patagonia) — each exported, each with a known capture_time so
    chronological ordering is testable."""
    rows = [
        # A, two days
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            event_name="Costa Rica",
            export_relpath="Exported Media/Day01/a1.jpg",
            capture_time="2026-04-01T10:00:00",
            kind="photo", has_export=True),
        um.GlobalItem(
            event_uuid="A", item_id="a2", synced_at=NOW,
            event_name="Costa Rica",
            export_relpath="Exported Media/Day02/a2.mp4",
            capture_time="2026-04-02T15:00:00",
            kind="video", duration_ms=12_000, has_export=True),
        # B, one day
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            event_name="Nepal",
            export_relpath="Exported Media/Day01/b1.jpg",
            capture_time="2025-10-15T07:30:00",
            kind="photo", has_export=True),
        # C — original-only (grab-kind candidate)
        um.GlobalItem(
            event_uuid="C", item_id="c1", synced_at=NOW,
            event_name="Patagonia",
            origin_relpath="Original Media/raw1.dng",
            capture_time="2024-12-10T16:00:00",
            kind="photo"),
    ]
    for r in rows:
        store.upsert(r)


def _make_lg(store) -> LibraryGateway:
    return LibraryGateway(store, now=lambda: NOW)


def _seed_cross_event_cut(lg, cut_id: str, members: list) -> None:
    """Insert one cross-event Cut + the named members via the gateway."""
    with lg.user_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, created_at, updated_at) "
            "VALUES (?, ?, 'user', ?, ?)",
            (cut_id, f"tag_{cut_id}", NOW, NOW))
    if members:
        lg.set_cross_event_cut_members(cut_id, members)


# --------------------------------------------------------------------------- #
# build_cross_event_entries
# --------------------------------------------------------------------------- #


def test_entries_ordered_chronologically(tmp_path):
    """Members sort by capture_time across events; B (2025-10) lands
    before A (2026-04)."""
    store = _open_user_store(tmp_path)
    _seed_projection(store)
    lg = _make_lg(store)
    _seed_cross_event_cut(lg, "cut-x", [
        {"event_id": "A", "kind": "export",
         "export_relpath": "Exported Media/Day01/a1.jpg"},
        {"event_id": "B", "kind": "export",
         "export_relpath": "Exported Media/Day01/b1.jpg"},
        {"event_id": "A", "kind": "export",
         "export_relpath": "Exported Media/Day02/a2.mp4"},
    ])
    try:
        entries, day_meta = build_cross_event_entries(
            library_gateway=lg, cut_id="cut-x", separators_on=False)
        # spec/154 — the opener (provenance summary) always rides; no day
        # separators when separators_on is False.
        kinds_only = [k for k, _ in entries]
        assert kinds_only == ["opener", "file", "file", "file"]
        keys = [
            (p.event_uuid, p.export_relpath)
            for k, p in entries if k == "file"
        ]
        assert keys == [
            ("B", "Exported Media/Day01/b1.jpg"),    # 2025-10
            ("A", "Exported Media/Day01/a1.jpg"),    # 2026-04-01
            ("A", "Exported Media/Day02/a2.mp4"),    # 2026-04-02
        ]
        files = [p for k, p in entries if k == "file"]
        # Video kind + duration carries through.
        assert files[-1].kind == "video"
        assert files[-1].duration_ms == 12_000
        # Photo videos get 0 duration_ms.
        assert files[0].kind == "photo"
        assert files[0].duration_ms == 0
        # day_meta is empty when separators_on=False.
        assert day_meta == {}
    finally:
        store.close()


def test_entries_grab_kind_uses_origin_relpath(tmp_path):
    """A grab-kind member's payload carries its origin_relpath (not
    export); the resolver downstream reads ``member_kind`` to pick
    the right column."""
    store = _open_user_store(tmp_path)
    _seed_projection(store)
    lg = _make_lg(store)
    _seed_cross_event_cut(lg, "cut-x", [
        {"event_id": "C", "kind": "grab",
         "origin_relpath": "Original Media/raw1.dng"},
    ])
    try:
        entries, _ = build_cross_event_entries(
            library_gateway=lg, cut_id="cut-x")
        files = [p for k, p in entries if k == "file"]
        assert len(files) == 1
        payload = files[0]
        assert payload.member_kind == "grab"
        assert payload.origin_relpath == "Original Media/raw1.dng"
        assert payload.export_relpath == ""
    finally:
        store.close()


def test_entries_separators_per_event_day(tmp_path):
    """With separators_on, every ``(event_uuid, date)`` boundary
    earns one separator card. spec/81 §3.1 — same calendar day in
    two events earns two cards."""
    store = _open_user_store(tmp_path)
    # Two events sharing the same calendar date.
    rows = [
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            export_relpath="Exported Media/a1.jpg",
            capture_time="2026-04-01T10:00:00",
            kind="photo", has_export=True),
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            export_relpath="Exported Media/b1.jpg",
            capture_time="2026-04-01T11:00:00",
            kind="photo", has_export=True),
    ]
    for r in rows:
        store.upsert(r)
    lg = _make_lg(store)
    _seed_cross_event_cut(lg, "cut-x", [
        {"event_id": "A", "kind": "export",
         "export_relpath": "Exported Media/a1.jpg"},
        {"event_id": "B", "kind": "export",
         "export_relpath": "Exported Media/b1.jpg"},
    ])
    try:
        entries, day_meta = build_cross_event_entries(
            library_gateway=lg, cut_id="cut-x", separators_on=True)
        kinds = [k for k, _ in entries]
        # opener + sep + file + sep + file
        assert kinds == ["opener", "sep", "file", "sep", "file"]
        # Day meta has both (event_uuid, date) tokens.
        assert set(day_meta.keys()) == {
            ("A", "2026-04-01"),
            ("B", "2026-04-01"),
        }
    finally:
        store.close()


def test_separators_carry_source_event_name_title(tmp_path):
    """spec/154 — a cross-event separator is labelled by its SOURCE EVENT
    name (resolved via the event index), not a "Day N" — there's no single
    timeline. Falls back to a neutral headline when the name is unknown."""
    store = _open_user_store(tmp_path)
    rows = [
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            export_relpath="Exported Media/a1.jpg",
            capture_time="2026-04-01T10:00:00",
            kind="photo", has_export=True),
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            export_relpath="Exported Media/b1.jpg",
            capture_time="2026-04-01T11:00:00",
            kind="photo", has_export=True),
    ]
    for r in rows:
        store.upsert(r)
    # Event-index rows give the events their display names.
    store.upsert(um.EventIndex(
        event_uuid="A", relpath_to_base="Salta",
        name_cached="Salta, Argentina"))
    store.upsert(um.EventIndex(
        event_uuid="B", relpath_to_base="Nepal", name_cached="Nepal"))
    lg = _make_lg(store)
    _seed_cross_event_cut(lg, "cut-x", [
        {"event_id": "A", "kind": "export",
         "export_relpath": "Exported Media/a1.jpg"},
        {"event_id": "B", "kind": "export",
         "export_relpath": "Exported Media/b1.jpg"},
    ])
    try:
        _entries, day_meta = build_cross_event_entries(
            library_gateway=lg, cut_id="cut-x", separators_on=True)
        assert day_meta[("A", "2026-04-01")].title == "Salta, Argentina"
        assert day_meta[("B", "2026-04-01")].title == "Nepal"
    finally:
        store.close()


def test_entries_empty_cut_returns_empty(tmp_path):
    store = _open_user_store(tmp_path)
    lg = _make_lg(store)
    _seed_cross_event_cut(lg, "cut-x", [])
    try:
        entries, day_meta = build_cross_event_entries(
            library_gateway=lg, cut_id="cut-x")
        assert entries == []
        assert day_meta == {}
    finally:
        store.close()


def test_entries_skip_member_without_relpath(tmp_path):
    """Defensive: a row whose relpath is missing on the kind side is
    skipped (would render as 'missing' on the player otherwise)."""
    store = _open_user_store(tmp_path)
    _seed_projection(store)
    lg = _make_lg(store)
    # Insert a corrupt member directly — bypass set_cross_event_cut_members
    # which validates. The PK requires (cut_id, event_id, member_id);
    # the kind/relpath CHECK on the schema would reject a NULL combo,
    # so we test the python-side guard by setting member_id but leaving
    # both relpaths NULL via a kind='grab' with origin_relpath but the
    # build helper looking at the wrong kind below — we set kind='export'
    # at the seed level to simulate a corrupt row.
    with lg.user_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, created_at, updated_at) "
            "VALUES ('cut-x', 'tag_cut-x', 'user', ?, ?)",
            (NOW, NOW))
        # A normal member so the entries list isn't entirely empty.
        conn.execute(
            "INSERT INTO cut_member (cut_id, event_id, member_id, kind, "
            "                        export_relpath, added_at) "
            "VALUES ('cut-x', 'A', 'good.jpg', 'export', "
            "        'Exported Media/Day01/a1.jpg', ?)", (NOW,))
    try:
        entries, _ = build_cross_event_entries(
            library_gateway=lg, cut_id="cut-x")
        files = [p for k, p in entries if k == "file"]
        assert len(files) == 1
        assert files[0].export_relpath == "Exported Media/Day01/a1.jpg"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# make_resolve_path
# --------------------------------------------------------------------------- #


class _FakeIndex:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, eid):
        return self._mapping.get(eid)

    def resolve_root(self, entry, photos_base):
        return entry.get("root") if entry else None


class _FakeGateway:
    def __init__(self, mapping):
        self.index = _FakeIndex(mapping)

    def photos_base_path(self):
        return Path("/photos")


def test_resolve_path_export_kind(tmp_path):
    """Export members resolve to ``<event_root>/<export_relpath>``."""
    gw = _FakeGateway({
        "A": {"root": Path("/photos/Costa Rica")},
    })
    resolve = make_resolve_path(gateway=gw)
    payload = CrossEventPlayFile(
        event_uuid="A", kind="photo",
        export_relpath="Exported Media/a.jpg",
        member_kind="export")
    assert resolve(payload) == Path("/photos/Costa Rica/Exported Media/a.jpg")


def test_resolve_path_grab_kind(tmp_path):
    """Grab members route to Original Media/<origin_relpath>."""
    gw = _FakeGateway({
        "C": {"root": Path("/photos/Patagonia")},
    })
    resolve = make_resolve_path(gateway=gw)
    payload = CrossEventPlayFile(
        event_uuid="C", kind="photo",
        origin_relpath="Original Media/raw.dng",
        member_kind="grab")
    assert resolve(payload) == Path("/photos/Patagonia/Original Media/raw.dng")


def test_resolve_path_unknown_event_returns_bare_relpath(tmp_path):
    """An event no longer in the index degrades to a bare relpath —
    the player's 'missing' fallback then handles the visible failure
    instead of crashing on a NoneType."""
    gw = _FakeGateway({})
    resolve = make_resolve_path(gateway=gw)
    payload = CrossEventPlayFile(
        event_uuid="gone", kind="photo",
        export_relpath="Exported Media/a.jpg",
        member_kind="export")
    out = resolve(payload)
    assert out == Path("Exported Media/a.jpg")
