"""spec/81 Phase 2 — :class:`CrossEventCutSession` (pin across events).

The cross-event sibling of :mod:`tests.test_cut_session`. Drives the session
against a hand-seeded ``mira.db`` (global_items + saved_filter) and a real
anchor ``event.db`` (the commit target). Confirms the ledger, the budget math,
the commit path (writes a cross-event Cut with ``source_dc_kind = 'user'`` +
member rows carrying their source ``event_id``), and the anchor-event picker.
"""
from __future__ import annotations

import json

import pytest

from core import collection_resolver as cr
from mira.gateway.event_gateway import EventGateway
from mira.gateway.library_gateway import LibraryGateway
from mira.shared.cross_event_cut_session import (
    CrossEventCutSession,
    CrossEventSessionFile,
    pick_anchor_event,
    session_files_from_global_items,
)
from mira.shared.cut_draft import (
    CrossEventCutDraft, PIN_KEEP_ALL, PIN_PICK_IN, PIN_WEED_OUT,
)
from mira.store.repo import EventStore
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-16T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fixtures — a 2-event projection where every member is #exported
# --------------------------------------------------------------------------- #


def _open_user_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db", app_version="test", created_at=NOW,
    )


def _seed_projection(user_store: UserStore) -> None:
    """6 items across 2 events, every one has an export_relpath set so the
    Item-4-scope (#exported) session can pin them all without grab-originals
    (Item 6)."""
    rows = [
        # Event A: 3 exported photos + 1 video.
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            event_name="Costa Rica",
            export_relpath="Exported Media/Day01/a1.jpg",
            capture_time="2026-04-01T10:00:00",
            kind="photo", classification="macro",
            stars=4, has_export=True,
        ),
        um.GlobalItem(
            event_uuid="A", item_id="a2", synced_at=NOW,
            event_name="Costa Rica",
            export_relpath="Exported Media/Day01/a2.jpg",
            capture_time="2026-04-01T14:00:00",
            kind="photo", classification="wildlife",
            stars=5, has_export=True,
        ),
        um.GlobalItem(
            event_uuid="A", item_id="a3", synced_at=NOW,
            event_name="Costa Rica",
            export_relpath="Exported Media/Day02/a3.jpg",
            capture_time="2026-04-02T08:00:00",
            kind="photo", classification="macro",
            stars=3, has_export=True,
        ),
        um.GlobalItem(
            event_uuid="A", item_id="a4", synced_at=NOW,
            event_name="Costa Rica",
            export_relpath="Exported Media/Day02/a4.mp4",
            capture_time="2026-04-02T15:00:00",
            kind="video", duration_ms=30_000,
            classification="landscape",
            stars=5, has_export=True,
        ),
        # Event B: 1 exported photo + 1 collected-only (no export_relpath).
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            event_name="Nepal",
            export_relpath="Exported Media/Day01/b1.jpg",
            capture_time="2025-10-15T07:00:00",
            kind="photo", classification="portrait",
            stars=5, has_export=True,
        ),
        # Collected-only — no export_relpath. Item 4 session DROPS this.
        um.GlobalItem(
            event_uuid="B", item_id="b2", synced_at=NOW,
            event_name="Nepal",
            export_relpath=None,
            capture_time="2025-10-16T17:00:00",
            kind="photo", classification="landscape",
            stars=2, pick_state="picked",
        ),
    ]
    for r in rows:
        user_store.upsert(r)


def _make_lg(user_store: UserStore, *, new_ids=("dc-1", "dc-2")):
    iter_ids = iter(new_ids)
    return LibraryGateway(user_store, now=lambda: NOW,
                          new_id=lambda: next(iter_ids))


def _make_anchor_event(tmp_path, *, event_id: str = "A") -> EventStore:
    """A minimal anchor event.db with the v8 schema — the cross-event Cut's
    cut row will land in this store."""
    store = EventStore.create(
        tmp_path / f"{event_id}.db",
        event_id=event_id, app_version="test", created_at=NOW,
    )
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, 'anchor', ?, ?)", (event_id, NOW, NOW))
    return store


def _make_eg(store: EventStore, *, new_ids=("c-1", "c-2")) -> EventGateway:
    iter_ids = iter(new_ids)
    return EventGateway(store, now=lambda: NOW,
                        new_id=lambda: next(iter_ids))


# --------------------------------------------------------------------------- #
# session_files_from_global_items — drops un-exported, preserves order
# --------------------------------------------------------------------------- #


def test_session_files_drops_un_exported(tmp_path):
    """Items with no ``export_relpath`` (collected/picked/edited rungs) drop
    out — Item 4 supports #exported only; grab-originals (Item 6) handles
    the rest."""
    store = _open_user_store(tmp_path)
    _seed_projection(store)
    lg = _make_lg(store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_COLLECTED]])
    rows = store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    files = session_files_from_global_items(rows, keys)
    # b2 has no export_relpath → dropped. All others survive.
    keys_in = {f.key for f in files}
    assert "B::b2" not in keys_in
    assert {f.event_uuid for f in files} == {"A", "B"}
    assert {f.item_id for f in files} == {"a1", "a2", "a3", "a4", "b1"}
    store.close()


def test_session_files_preserves_resolver_order(tmp_path):
    """The session cells follow the resolver's chronological key order."""
    store = _open_user_store(tmp_path)
    _seed_projection(store)
    lg = _make_lg(store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_EXPORTED]])
    rows = store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    files = session_files_from_global_items(rows, keys)
    # Chronological across events: b1 (2025-10) → a1, a2, a3, a4 (2026-04).
    assert [(f.event_uuid, f.item_id) for f in files] == [
        ("B", "b1"), ("A", "a1"), ("A", "a2"), ("A", "a3"), ("A", "a4"),
    ]
    store.close()


def test_session_files_carry_event_id_and_relpath(tmp_path):
    """Each cell knows its source event + the export_relpath — the commit
    path turns these into ``(event_id, export_relpath)`` cut_member rows."""
    store = _open_user_store(tmp_path)
    _seed_projection(store)
    lg = _make_lg(store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_EXPORTED]])
    rows = store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    by_key = {f.key: f for f in session_files_from_global_items(rows, keys)}
    a4 = by_key["A::a4"]
    assert a4.event_uuid == "A"
    assert a4.export_relpath == "Exported Media/Day02/a4.mp4"
    assert a4.kind == "video" and a4.duration_ms == 30_000
    store.close()


# --------------------------------------------------------------------------- #
# Ledger — Pick/Skip on packed keys, undo
# --------------------------------------------------------------------------- #


def _session(tmp_path, *, separators_on=False,
             pin_mode=PIN_WEED_OUT) -> CrossEventCutSession:
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    draft = CrossEventCutDraft(
        name="test_cut", tag="test_cut",
        expr=tuple([("+", "exported")]),
        filters={},
        pin_mode=pin_mode,
        separators=separators_on,
        photo_s=6.0,
    )
    return CrossEventCutSession.from_draft(lg, draft)


def test_pin_weed_out_starts_all_picked(tmp_path):
    s = _session(tmp_path, pin_mode=PIN_WEED_OUT)
    assert s.picked_count() == 5
    assert all(s.is_picked(f.key) for f in s.files)


def test_pin_pick_in_starts_all_skipped(tmp_path):
    s = _session(tmp_path, pin_mode=PIN_PICK_IN)
    assert s.picked_count() == 0
    assert not any(s.is_picked(f.key) for f in s.files)


def test_pin_keep_all_marks_keep_all_property(tmp_path):
    s = _session(tmp_path, pin_mode=PIN_KEEP_ALL)
    assert s.keep_all is True and s.picked_count() == 5


def test_set_state_and_undo(tmp_path):
    s = _session(tmp_path)
    target = s.files[0].key
    assert s.is_picked(target) is True
    s.set_state(target, False)
    assert s.is_picked(target) is False
    s.undo()
    assert s.is_picked(target) is True


def test_toggle(tmp_path):
    s = _session(tmp_path)
    target = s.files[0].key
    new = s.toggle(target)
    assert new is False and s.is_picked(target) is False


def test_set_state_ignores_unknown_key(tmp_path):
    s = _session(tmp_path)
    s.set_state("Z::not-a-key", True)
    # No-op; ledger unchanged, no undo recorded.
    assert s.undo() is None


# --------------------------------------------------------------------------- #
# Budget math + day buckets — per-(event, day)
# --------------------------------------------------------------------------- #


def test_totals_counts_photos_and_video_duration(tmp_path):
    s = _session(tmp_path)
    totals = s.totals()
    assert totals.photo_count == 4              # b1, a1, a2, a3
    assert totals.video_count == 1              # a4
    assert totals.video_ms_total == 30_000


def test_totals_separator_count_per_event_day(tmp_path):
    """spec/81 §3.1 — separators orient ONE event's timeline. Day bucket
    key is ``(event_uuid, ISO date)`` so the same calendar day in two events
    earns two separators (when separators ON)."""
    s = _session(tmp_path, separators_on=True)
    totals = s.totals()
    # Buckets: (A, 2026-04-01), (A, 2026-04-02), (B, 2025-10-15) → 3.
    assert totals.separator_count == 3


def test_totals_separators_off_zeros_count(tmp_path):
    """Cross-event default is OFF; the field reads 0 even when the day
    buckets exist."""
    s = _session(tmp_path, separators_on=False)
    assert s.totals().separator_count == 0


def test_picked_members_returns_commit_ready_dicts(tmp_path):
    """The commit-ready shape (schema v9): each picked member is a dict
    carrying ``event_id`` + kind + the right relpath for its kind. Export
    members get ``export_relpath``; grabs get ``origin_relpath`` (spec/81
    Phase 2 Item 6)."""
    s = _session(tmp_path)
    members = s.picked_members()
    assert {
        "event_id": "B", "kind": "export",
        "export_relpath": "Exported Media/Day01/b1.jpg",
    } in members
    assert {
        "event_id": "A", "kind": "export",
        "export_relpath": "Exported Media/Day02/a4.mp4",
    } in members
    assert len(members) == 5


# --------------------------------------------------------------------------- #
# anchor_event picker
# --------------------------------------------------------------------------- #


def test_pick_anchor_event_chooses_top_contributor(tmp_path):
    """Anchor default = the event contributing the most files. Ties break
    on event_uuid ascending."""
    s = _session(tmp_path)
    # Event A contributes 4, B contributes 1.
    assert pick_anchor_event(s.files) == "A"


def test_pick_anchor_event_returns_none_for_empty(tmp_path):
    assert pick_anchor_event([]) is None


def test_pick_anchor_event_breaks_ties_alphabetically(tmp_path):
    """When two events contribute the same count, pick the alphabetically
    first event_uuid — deterministic, no random."""
    files = [
        CrossEventSessionFile(event_uuid="Z", item_id="x", export_relpath="r"),
        CrossEventSessionFile(event_uuid="A", item_id="y", export_relpath="r"),
    ]
    assert pick_anchor_event(files) == "A"


# --------------------------------------------------------------------------- #
# Commit — writes cut row + member rows in mira.db (spec/93 §3, spec/94 4a-ii)
# --------------------------------------------------------------------------- #


def test_commit_creates_cut_with_user_kind(tmp_path):
    """Cross-event Cut's ``source_dc_kind`` = 'user' lands on the
    ``cut`` row in mira.db. spec/94 Phase 4a-ii: the Cut is in the
    library store now (spec/93 §3); the source_dc_id always points to
    a mira.db ``saved_filter`` row."""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    sf = lg.create_dc("five_star", expr=[["+", cr.BASE_EXPORTED]],
                      filters={"stars_min": 5})
    keys = lg.resolve_dc_keys(sf.expr_json and json.loads(sf.expr_json),
                              json.loads(sf.filters_json))
    rows = user_store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    files = session_files_from_global_items(rows, keys)
    session = CrossEventCutSession(
        name="five_star_cut",
        expr=tuple([("+", "exported")]),
        filters={"stars_min": 5},
        pin_mode=PIN_WEED_OUT,
        target_s=None, max_s=None, photo_s=6.0,
        music_category=None,
        files=tuple(files),
        anchor_event_id="A",
        source_dc_id=sf.id,
        separators_on=False,
    )

    cut = session.commit(lg)

    # Cut row lives in mira.db; shape matches spec/93 §3.
    assert cut.tag == "five_star_cut"
    assert cut.source_dc_kind == "user"
    assert cut.source_dc_id == sf.id
    assert json.loads(cut.expr_snapshot_json) == [["+", "exported"]]
    assert cut.separators is False                 # cross-event default OFF
    # And visible on the library gateway's list.
    assert any(c.id == cut.id for c in lg.cross_event_cuts())
    user_store.close()


def test_commit_writes_cross_event_member_rows(tmp_path):
    """Each member row in mira.db carries the source ``event_id`` so
    the export pipeline routes the relpath back to the right event's
    Exported Media/."""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_EXPORTED]])
    rows = user_store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    files = session_files_from_global_items(rows, keys)
    session = CrossEventCutSession(
        name="all_exported",
        expr=tuple([("+", "exported")]),
        filters={},
        pin_mode=PIN_WEED_OUT,
        target_s=None, max_s=None, photo_s=6.0,
        music_category=None,
        files=tuple(files),
        anchor_event_id="A",
        separators_on=False,
    )

    cut = session.commit(lg)

    # 5 members in mira.db; each carries its source event_id (REQUIRED
    # NOT NULL per the v8 cut_member schema).
    members = lg.cross_event_cut_members(cut.id)
    by_relpath = {m.export_relpath: m.event_id for m in members}
    assert by_relpath == {
        "Exported Media/Day01/b1.jpg": "B",
        "Exported Media/Day01/a1.jpg": "A",
        "Exported Media/Day01/a2.jpg": "A",
        "Exported Media/Day02/a3.jpg": "A",
        "Exported Media/Day02/a4.mp4": "A",
    }
    assert all(eid for eid in by_relpath.values())
    user_store.close()


def test_commit_replaces_membership_on_re_entry(tmp_path):
    """Re-entering an existing cross-event Cut (cut_id set) → commit
    UPDATES settings + REPLACES membership in mira.db. The frozen
    expr_snapshot tracks the new formula at re-pin time (spec/81 §5)."""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_EXPORTED]])
    rows = user_store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    all_files = session_files_from_global_items(rows, keys)

    # First commit: everything picked.
    s1 = CrossEventCutSession(
        name="evolving", expr=tuple([("+", "exported")]),
        filters={}, pin_mode=PIN_WEED_OUT,
        target_s=None, max_s=None, photo_s=6.0, music_category=None,
        files=tuple(all_files), anchor_event_id="A",
    )
    cut1 = s1.commit(lg)
    assert lg.cross_event_cut_member_count(cut1.id) == 5

    # Re-enter: skip 3 of the 5; commit should leave only 2 members.
    s2 = CrossEventCutSession(
        name="evolving", expr=tuple([("+", "exported")]),
        filters={}, pin_mode=PIN_WEED_OUT,
        target_s=None, max_s=None, photo_s=6.0, music_category=None,
        files=tuple(all_files), anchor_event_id="A",
        cut_id=cut1.id,
    )
    for f in all_files[:3]:
        s2.set_state(f.key, False)
    s2.commit(lg)
    assert lg.cross_event_cut_member_count(cut1.id) == 2
    user_store.close()


def test_commit_works_when_anchor_is_also_a_source_event(tmp_path):
    """spec/94 Phase 4a-ii: there is no "anchor event" anymore — every
    cross-event member is named explicitly by ``event_id``. A member
    from event A and one from event B sit side-by-side in mira.db's
    ``cut_member``."""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_EXPORTED]])
    rows = user_store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    files = session_files_from_global_items(rows, keys)
    session = CrossEventCutSession(
        name="anchor_cut", expr=tuple([("+", "exported")]),
        filters={}, pin_mode=PIN_WEED_OUT,
        target_s=None, max_s=None, photo_s=6.0, music_category=None,
        files=tuple(files), anchor_event_id="A",
    )
    cut = session.commit(lg)
    members = lg.cross_event_cut_members(cut.id)
    a_members = sum(1 for m in members if m.event_id == "A")
    assert a_members >= 1
    # Every member carries an explicit event_id — there is no NULL
    # "anchor" fallback in mira.db's cut_member shape.
    assert all(m.event_id for m in members)
    user_store.close()
    assert a_members == 4
# --------------------------------------------------------------------------- #
# Draft-driven session construction
# --------------------------------------------------------------------------- #


def test_from_draft_resolves_via_library_gateway(tmp_path):
    """The dialog → session handoff: draft's expr + filters resolve via
    LibraryGateway and build the cells."""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    draft = CrossEventCutDraft(
        name="macro_only", tag="macro_only",
        expr=tuple([("+", "exported")]),
        filters={"styles": ["macro"]},
        photo_s=6.0,
    )
    session = CrossEventCutSession.from_draft(lg, draft, anchor_event_id="A")
    assert {(f.event_uuid, f.item_id) for f in session.files} == {
        ("A", "a1"), ("A", "a3"),
    }
    user_store.close()


def test_from_draft_falls_back_to_saved_dc_when_expr_empty(tmp_path):
    """Empty inline expr → reads the saved DC's stored expr + filters."""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    sf = lg.create_dc(
        "five_star_set",
        expr=[["+", cr.BASE_EXPORTED]],
        filters={"stars_min": 5})
    draft = CrossEventCutDraft(
        name="from_dc", tag="from_dc",
        source_dc_id=sf.id,
        expr=(),                                # empty — fall back to DC
        photo_s=6.0,
    )
    session = CrossEventCutSession.from_draft(lg, draft)
    assert {f.item_id for f in session.files} == {"a2", "a4", "b1"}
    user_store.close()


def test_from_draft_separators_default_off(tmp_path):
    """Cross-event default for separators is OFF (spec/81 §3.1) — the
    session inherits the draft's value (also defaulting to OFF)."""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    draft = CrossEventCutDraft(
        name="x", tag="x", expr=tuple([("+", "exported")]),
        photo_s=6.0)
    session = CrossEventCutSession.from_draft(lg, draft)
    assert session.separators_on is False
    user_store.close()


# --------------------------------------------------------------------------- #
# spec/94 Phase 4a — scope_event_uuids threading
# --------------------------------------------------------------------------- #


def test_from_draft_scope_event_uuids_narrows_pool(tmp_path):
    """``scope_event_uuids`` narrows the resolved pool to the passed-in
    set of events. The seam is end-to-end: dialog composes a Scope
    sentence → events_page resolves it via :meth:`resolve_scope` →
    ``from_draft`` receives the pre-resolved uuids → only members from
    those events appear in the session cells.

    Without scope, the projection has #exported items from A and B.
    Narrowed to {"A"}, only A's items show up — Nepal stays out."""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    draft = CrossEventCutDraft(
        name="x", tag="x", expr=tuple([("+", "exported")]),
        photo_s=6.0)
    full = CrossEventCutSession.from_draft(lg, draft)
    assert {f.event_uuid for f in full.files} == {"A", "B"}

    scoped = CrossEventCutSession.from_draft(
        lg, draft, scope_event_uuids={"A"})
    assert {f.event_uuid for f in scoped.files} == {"A"}
    user_store.close()


def test_from_draft_scope_none_keeps_library_wide(tmp_path):
    """The explicit ``scope_event_uuids=None`` matches the historical
    no-narrowing behaviour — the default behaviour stays intact for
    callers that don't compose a Scope."""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    draft = CrossEventCutDraft(
        name="x", tag="x", expr=tuple([("+", "exported")]),
        photo_s=6.0)
    session = CrossEventCutSession.from_draft(
        lg, draft, scope_event_uuids=None)
    assert {f.event_uuid for f in session.files} == {"A", "B"}
    user_store.close()


def test_from_draft_scope_empty_iterable_narrows_to_nothing(tmp_path):
    """An empty iterable is the "Scope composed, nothing resolved" case
    — the session resolves to zero candidates, not library-wide. (The
    dialog Start path then warns and stops; see events_page._on_start.)"""
    user_store = _open_user_store(tmp_path)
    _seed_projection(user_store)
    lg = _make_lg(user_store)
    draft = CrossEventCutDraft(
        name="x", tag="x", expr=tuple([("+", "exported")]),
        photo_s=6.0)
    session = CrossEventCutSession.from_draft(
        lg, draft, scope_event_uuids=frozenset())
    assert session.files == ()
    user_store.close()
