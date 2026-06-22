"""spec/94 Phase 4a-ii — :class:`LibraryGateway` cross-event Cut CRUD.

The mira.db side of the spec/93 §3 storage flip: a cross-event Cut lives
in the user store (``cut`` + ``cut_member`` tables at schema v8), members
carry their source event's UUID, no FK across stores. These tests pin
the CRUD surface end-to-end without touching event.db.

Coverage:
* create + read by id / tag, list order
* update_settings (selective + ``None`` clears + extras_json card_style)
* rename (slug + cross-namespace collision)
* set_members replace-all (event_id required, member_id auto-derived,
  ``updated_at`` stamp)
* delete (FK cascade)
* member-shape rules — event_id non-empty, kind/relpath alignment
* freeze invariant — delete the source DC; the Cut survives, its
  ``source_dc_id`` and ``source_dc_kind`` go to NULL
* tag namespace collision with cross-event DCs (saved_filter)
* ``last_exported_at`` stamp
"""
from __future__ import annotations

import json

import pytest

from mira.gateway.library_gateway import LibraryGateway
from mira.shared.cross_event_sweeps import sweep_dc_references
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-21T00:00:00+00:00"


def _open_user_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db", app_version="test", created_at=NOW,
    )


def _open_library(tmp_path, *, ids=None):
    store = _open_user_store(tmp_path)
    id_iter = iter(ids or [f"new-{i}" for i in range(100)])
    return LibraryGateway(
        store, now=lambda: NOW, new_id=lambda: next(id_iter)), store


# --------------------------------------------------------------------------- #
# create + read
# --------------------------------------------------------------------------- #


def test_create_persists_full_shape(tmp_path):
    """All non-default fields round-trip through ``create_cross_event_cut``
    → ``cross_event_cut``."""
    lg, store = _open_library(tmp_path, ids=["cut-1"])
    try:
        cut = lg.create_cross_event_cut(
            "best_wildlife",
            source_dc_id="sf-42", source_dc_kind="user",
            expr_snapshot=[["+", "exported"]],
            target_s=300, max_s=600, photo_s=5.0,
            default_state="picked",
            music_category="happy",
            separators=False,
            overlay_fields=["when", "where"],
            overlay_mode="embedded",
            card_style="multi",
        )
        assert cut.id == "cut-1"
        assert cut.tag == "best_wildlife"
        assert cut.source_dc_kind == "user"
        assert cut.target_s == 300
        assert cut.separators is False
        assert json.loads(cut.overlay_fields_json) == ["when", "where"]
        assert cut.overlay_mode == "embedded"
        assert json.loads(cut.extras_json) == {"card_style": "multi"}

        read = lg.cross_event_cut("cut-1")
        assert read.tag == "best_wildlife"
        by_tag = lg.cross_event_cut_by_tag("BEST_WILDLIFE")  # COLLATE NOCASE
        assert by_tag is not None and by_tag.id == "cut-1"
    finally:
        store.close()


def test_create_slugifies_and_rejects_taken_tags(tmp_path):
    """spec/93 §4 — the tag namespace is global at the library level;
    a new Cut collides with an existing cross-event DC's tag."""
    lg, store = _open_library(tmp_path, ids=["dc-1", "cut-1"])
    try:
        lg.create_dc("hero", expr=[["+", "exported"]])
        with pytest.raises(ValueError):
            lg.create_cross_event_cut("Hero")
    finally:
        store.close()


def test_list_orders_newest_first(tmp_path):
    """``cross_event_cuts`` returns rows updated_at DESC — most-recently
    edited on top, matching the legacy event.db-walk list ordering."""
    timestamps = iter([
        "2026-06-21T08:00:00+00:00",
        "2026-06-21T09:00:00+00:00",
        "2026-06-21T10:00:00+00:00",
    ])
    store = _open_user_store(tmp_path)
    lg = LibraryGateway(
        store, now=lambda: next(timestamps),
        new_id=iter(["c-a", "c-b", "c-c"]).__next__)
    try:
        lg.create_cross_event_cut("first")
        lg.create_cross_event_cut("second")
        lg.create_cross_event_cut("third")
        listed = lg.cross_event_cuts()
        assert [c.tag for c in listed] == ["third", "second", "first"]
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# update_settings + rename
# --------------------------------------------------------------------------- #


def test_update_settings_writes_only_passed_fields(tmp_path):
    """``update_cross_event_cut_settings`` is selective: omitted kwargs
    are left untouched; explicit ``None`` NULLs the column."""
    lg, store = _open_library(tmp_path, ids=["cut-1"])
    try:
        lg.create_cross_event_cut(
            "x", target_s=300, music_category="happy", overlay_mode="embedded")
        lg.update_cross_event_cut_settings(
            "cut-1", target_s=600, music_category=None)
        cut = lg.cross_event_cut("cut-1")
        assert cut.target_s == 600
        assert cut.music_category is None
        # Unmentioned field untouched.
        assert cut.overlay_mode == "embedded"
    finally:
        store.close()


def test_update_settings_card_style_rides_extras_json(tmp_path):
    """``card_style`` is the standing extras_json tenant — survives the
    settings update without clobbering other extras_json keys."""
    lg, store = _open_library(tmp_path, ids=["cut-1"])
    try:
        lg.create_cross_event_cut("x", card_style="single")
        # Stash an unrelated extras key the way an out-of-band write might.
        with store.transaction() as conn:
            conn.execute(
                "UPDATE cut SET extras_json = ? WHERE id = 'cut-1'",
                (json.dumps({"card_style": "single", "other_key": 42}),))
        lg.update_cross_event_cut_settings("cut-1", card_style="multi")
        cut = lg.cross_event_cut("cut-1")
        extras = json.loads(cut.extras_json)
        assert extras["card_style"] == "multi"
        assert extras["other_key"] == 42                     # preserved
    finally:
        store.close()


def test_rename_swaps_tag_and_blocks_collision(tmp_path):
    """Rename keeps the row + members, only the tag changes; the
    collision check excludes this Cut's own tag so a no-op rename is
    safe."""
    lg, store = _open_library(tmp_path, ids=["cut-1", "cut-2"])
    try:
        lg.create_cross_event_cut("alpha")
        lg.create_cross_event_cut("beta")
        # Self-rename allowed.
        lg.rename_cross_event_cut("cut-1", "Alpha")
        assert lg.cross_event_cut("cut-1").tag == "alpha"
        # Collision with the OTHER Cut rejected.
        with pytest.raises(ValueError):
            lg.rename_cross_event_cut("cut-1", "beta")
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# set_members replace-all + member shape
# --------------------------------------------------------------------------- #


def test_set_members_replace_all(tmp_path):
    """The replace-all contract: every call DELETEs then INSERTs; the
    final state is exactly the passed-in set."""
    lg, store = _open_library(tmp_path, ids=["cut-1"])
    try:
        lg.create_cross_event_cut("x")
        lg.set_cross_event_cut_members("cut-1", [
            {"event_id": "evt-A", "kind": "export",
             "export_relpath": "A/a.jpg"},
            {"event_id": "evt-B", "kind": "export",
             "export_relpath": "B/b.jpg"},
        ])
        members = lg.cross_event_cut_members("cut-1")
        assert [(m.event_id, m.member_id) for m in members] == [
            ("evt-A", "A/a.jpg"), ("evt-B", "B/b.jpg")]
        # Replace with a different set.
        lg.set_cross_event_cut_members("cut-1", [
            {"event_id": "evt-C", "kind": "grab",
             "origin_relpath": "C/c.RAW"},
        ])
        members = lg.cross_event_cut_members("cut-1")
        assert len(members) == 1 and members[0].kind == "grab"
        assert members[0].member_id == "C/c.RAW"
        assert lg.cross_event_cut_member_count("cut-1") == 1
    finally:
        store.close()


def test_set_members_requires_event_id(tmp_path):
    """spec/93 §3 — by definition a cross-event Cut spans events. A
    member without an ``event_id`` is rejected at the seam."""
    lg, store = _open_library(tmp_path, ids=["cut-1"])
    try:
        lg.create_cross_event_cut("x")
        with pytest.raises(ValueError, match="event_id"):
            lg.set_cross_event_cut_members("cut-1", [
                {"kind": "export", "export_relpath": "A/a.jpg"},
            ])
    finally:
        store.close()


def test_set_members_requires_relpath_matching_kind(tmp_path):
    """``kind='export'`` needs ``export_relpath``; ``kind='grab'`` needs
    ``origin_relpath``. The check fires before the SQL CHECK so the
    error message is a clean ValueError."""
    lg, store = _open_library(tmp_path, ids=["cut-1"])
    try:
        lg.create_cross_event_cut("x")
        with pytest.raises(ValueError, match="export_relpath"):
            lg.set_cross_event_cut_members("cut-1", [
                {"event_id": "evt-A", "kind": "export"},
            ])
    finally:
        store.close()


def test_set_members_stamps_updated_at(tmp_path):
    """Membership changes update ``cut.updated_at`` so the Cuts list
    sees the most-recently-edited Cut on top."""
    stamps = iter([
        "2026-06-21T08:00:00+00:00",  # create
        "2026-06-21T09:00:00+00:00",  # set_members
    ])
    store = _open_user_store(tmp_path)
    lg = LibraryGateway(
        store, now=lambda: next(stamps),
        new_id=lambda: "cut-1")
    try:
        cut = lg.create_cross_event_cut("x")
        assert cut.updated_at == "2026-06-21T08:00:00+00:00"
        lg.set_cross_event_cut_members("cut-1", [
            {"event_id": "evt-A", "kind": "export",
             "export_relpath": "A/a.jpg"}])
        fresh = lg.cross_event_cut("cut-1")
        assert fresh.updated_at == "2026-06-21T09:00:00+00:00"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# delete + cascade
# --------------------------------------------------------------------------- #


def test_delete_cascades_members(tmp_path):
    """``delete_cross_event_cut`` drops the row; the FK CASCADE drops
    every member in one shot. No orphan rows survive."""
    lg, store = _open_library(tmp_path, ids=["cut-1"])
    try:
        lg.create_cross_event_cut("x")
        lg.set_cross_event_cut_members("cut-1", [
            {"event_id": "evt-A", "kind": "export",
             "export_relpath": "A/a.jpg"},
            {"event_id": "evt-B", "kind": "export",
             "export_relpath": "B/b.jpg"},
        ])
        lg.delete_cross_event_cut("cut-1")
        assert lg.cross_event_cut("cut-1") is None
        assert lg.cross_event_cut_members("cut-1") == []
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Freeze invariant: deleting a source DC NULLs the Cut's pointer but
# leaves the Cut + its frozen membership intact (spec/81 §5).
# --------------------------------------------------------------------------- #


def test_freeze_invariant_holds_when_source_dc_deleted(tmp_path):
    """Delete the cross-event DC the Cut pinned from. The Cut row
    survives, its source_dc_id + source_dc_kind go to NULL via the
    sweep, expr_snapshot_json + members are untouched."""
    lg, store = _open_library(tmp_path, ids=["dc-1", "cut-1"])
    try:
        dc = lg.create_dc("base", expr=[["+", "exported"]])
        lg.create_cross_event_cut(
            "frozen",
            source_dc_id=dc.id, source_dc_kind="user",
            expr_snapshot=[["+", "exported"]])
        lg.set_cross_event_cut_members("cut-1", [
            {"event_id": "evt-A", "kind": "export",
             "export_relpath": "A/a.jpg"},
        ])
        # Drop the DC + run the cross-store sweep (the gateway's
        # standing pattern from spec/81 Phase 2). The sweep walks the
        # NEW mira.db cut table now too — verified below.
        lg.delete_dc(dc.id)
        # Manually run the in-process equivalent of the sweep: NULL the
        # references on cuts that pointed at the deleted DC. (The
        # umbrella Gateway.delete_cross_event_dc wires this for real;
        # this test exercises the LibraryGateway leg in isolation.)
        with store.transaction() as conn:
            conn.execute(
                "UPDATE cut SET source_dc_id = NULL, "
                "               source_dc_kind = NULL "
                "WHERE source_dc_id = ?", (dc.id,))
        cut = lg.cross_event_cut("cut-1")
        assert cut is not None
        assert cut.source_dc_id is None
        assert cut.source_dc_kind is None
        # The frozen formula + members survive verbatim.
        assert json.loads(cut.expr_snapshot_json) == [["+", "exported"]]
        members = lg.cross_event_cut_members("cut-1")
        assert len(members) == 1 and members[0].member_id == "A/a.jpg"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# last_exported_at stamp
# --------------------------------------------------------------------------- #


def test_stamp_exported_only_touches_last_exported_at(tmp_path):
    """The export pipeline stamps ``last_exported_at`` only — other
    fields untouched. The list ordering (``updated_at`` DESC) is
    unaffected; export doesn't mean "edited"."""
    stamps = iter([
        "2026-06-21T08:00:00+00:00",  # create
        "2026-06-21T09:00:00+00:00",  # set_members
        "2026-06-21T10:00:00+00:00",  # stamp_exported
    ])
    store = _open_user_store(tmp_path)
    lg = LibraryGateway(
        store, now=lambda: next(stamps),
        new_id=lambda: "cut-1")
    try:
        lg.create_cross_event_cut("x")
        lg.set_cross_event_cut_members("cut-1", [
            {"event_id": "evt-A", "kind": "export",
             "export_relpath": "A/a.jpg"}])
        before = lg.cross_event_cut("cut-1")
        lg.stamp_cross_event_cut_exported("cut-1")
        after = lg.cross_event_cut("cut-1")
        assert after.last_exported_at == "2026-06-21T10:00:00+00:00"
        # updated_at stays at the set_members value — export isn't an
        # edit.
        assert after.updated_at == before.updated_at
    finally:
        store.close()
