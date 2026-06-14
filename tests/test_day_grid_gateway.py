"""Gateway-layer tests for the Day Grid foundations (spec/32 §8).

Covers:
  * schema v2 → v3 migration adds ``day_resume`` (and existing v2 data is
    preserved, per feedback_schema_evolution_policy).
  * :meth:`EventGateway.set_day_resume_cell` / :meth:`get_day_resume_cell` —
    per-(phase, day) cell-index cursor (spec/32 §8.5).
  * :meth:`EventGateway.reset_compare_in_day` — the "Reset all Compare"
    button (spec/32 §2.8).

Logic-only, no Qt.
"""
from __future__ import annotations

import sqlite3

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store import schema
from mira.store.repo import EventStore

FIXED_NOW = "2026-06-01T12:00:00+00:00"


def _seed(tmp_path):
    """Fresh event with one day + four items, mixed phase_state."""
    db = tmp_path / "event.db"
    store = EventStore.create(db, event_id="evt")
    items = [
        m.Item(
            id=iid, kind="photo", origin_relpath=f"d1/{iid}.jpg", sha256=f"sha-{iid}",
            byte_size=100, materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="cam", capture_time_raw="2026-04-01T08:00:00",
            capture_time_corrected="2026-04-01T08:00:00",
            created_at=FIXED_NOW, day_number=1, provenance="captured",
        )
        for iid in ("a", "b", "c", "d")
    ]
    phase_states = [
        m.PhaseState(item_id="a", phase="pick", state="picked"),
        m.PhaseState(item_id="b", phase="pick", state="candidate"),
        m.PhaseState(item_id="c", phase="pick", state="candidate"),
        m.PhaseState(item_id="d", phase="pick", state="skipped"),
    ]
    store.save_document(m.EventDocument(
        event=m.Event(uuid="evt", name="T", created_at=FIXED_NOW, updated_at=FIXED_NOW),
        items=items, phase_states=phase_states,
        trip_days=[m.TripDay(day_number=1, date="2026-04-01")],
        cameras=[m.Camera(camera_id="cam")],
    ))
    store.close()
    return EventGateway.open(db, event_root=tmp_path, now=lambda: FIXED_NOW)


# --------------------------------------------------------------------------- #
# Schema v2 → v3 migration
# --------------------------------------------------------------------------- #


def test_schema_version_self_consistent():
    """Pin SCHEMA_VERSION + MIGRATIONS together. Post-2026-06-08 the schema is
    greenfield v1 with an empty MIGRATIONS list (charter §3 + spec/30) — pre-
    release events get wiped instead of migrated. The invariant becomes:
    MIGRATIONS length == SCHEMA_VERSION - 1 (v1 ⇒ 0 entries)."""
    assert schema.SCHEMA_VERSION >= 1
    assert len(schema.MIGRATIONS) == schema.SCHEMA_VERSION - 1


def test_day_resume_table_exists_on_fresh_db(tmp_path):
    """A fresh v3 event.db has day_resume created from the main DDL."""
    db = tmp_path / "fresh.db"
    store = EventStore.create(db, event_id="evt")
    try:
        names = {
            r["name"] for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "day_resume" in names
    finally:
        store.close()


# Greenfield-while-pre-release (charter §3 + spec/30): the v2→v3 and v3→v4
# migration tests retired with the schema reset to v1. ``day_resume`` and
# ``item_visit`` are part of the fresh-DB DDL now, exercised by the
# ``_table_exists_on_fresh_db`` tests below.


# --------------------------------------------------------------------------- #
# day_resume cursor — set / get
# --------------------------------------------------------------------------- #


def test_day_resume_get_returns_zero_when_unset(tmp_path):
    gw = _seed(tmp_path)
    assert gw.get_day_resume_cell("pick", 1) == 0
    assert gw.get_day_resume_cell("pick", 1) == 0
    # Unknown day → still 0 (no row).
    assert gw.get_day_resume_cell("pick", 99) == 0


def test_day_resume_set_then_get_roundtrip(tmp_path):
    gw = _seed(tmp_path)
    gw.set_day_resume_cell("pick", 1, 7)
    assert gw.get_day_resume_cell("pick", 1) == 7
    # Other phase / other day independent.
    assert gw.get_day_resume_cell("edit", 1) == 0


def test_day_resume_upsert_updates_in_place(tmp_path):
    """spec/30 + feedback_never_insert_or_replace_with_fks: ON CONFLICT DO
    UPDATE (no delete-and-reinsert). Calling set twice keeps the row, updates
    cell_index and updated_at."""
    gw = _seed(tmp_path)
    gw.set_day_resume_cell("pick", 1, 3)
    gw.set_day_resume_cell("pick", 1, 11)
    assert gw.get_day_resume_cell("pick", 1) == 11
    # One row, not two.
    rows = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM day_resume WHERE phase = ? AND day_number = ?",
        ("pick", 1),
    ).fetchone()
    assert rows["n"] == 1


def test_day_resume_undated_day_uses_is_null(tmp_path):
    """day_number=None means the undated day (IS NULL) — both set and get must
    route through the IS NULL clause rather than = NULL."""
    gw = _seed(tmp_path)
    gw.set_day_resume_cell("pick", None, 4)
    assert gw.get_day_resume_cell("pick", None) == 4
    # Setting twice on the undated day still updates (not double-insert).
    gw.set_day_resume_cell("pick", None, 9)
    assert gw.get_day_resume_cell("pick", None) == 9


def test_day_resume_clamps_negative_to_zero(tmp_path):
    """CHECK (cell_index >= 0) + defensive clamp in the gateway."""
    gw = _seed(tmp_path)
    gw.set_day_resume_cell("pick", 1, -5)
    assert gw.get_day_resume_cell("pick", 1) == 0


# --------------------------------------------------------------------------- #
# reset_compare_in_day (spec/32 §2.8)
# --------------------------------------------------------------------------- #


def test_reset_compare_in_day_returns_zero_when_none_in_compare(tmp_path):
    gw = _seed(tmp_path)
    # Clear b and c first so no candidates remain.
    gw.set_phase_state("b", "pick", "picked")
    gw.set_phase_state("c", "pick", "picked")
    assert gw.reset_compare_in_day("pick", 1, "skipped") == 0


def test_reset_compare_in_day_resets_only_compare(tmp_path):
    """Spec/32 §2.8 — only Compare items in the day flip. Kept / Discarded /
    Untouched are not touched."""
    gw = _seed(tmp_path)
    # Sanity: a=kept, b=candidate, c=candidate, d=discarded.
    n = gw.reset_compare_in_day("pick", 1, "skipped")
    assert n == 2
    states = gw.phase_states("pick")
    assert states["a"].state == "picked"          # untouched
    assert states["b"].state == "skipped"     # reset
    assert states["c"].state == "skipped"     # reset
    assert states["d"].state == "skipped"     # already


def test_reset_compare_in_day_target_keep(tmp_path):
    """Target state can be 'picked' when the phase default is keep."""
    gw = _seed(tmp_path)
    n = gw.reset_compare_in_day("pick", 1, "picked")
    assert n == 2
    states = gw.phase_states("pick")
    assert states["b"].state == "picked"
    assert states["c"].state == "picked"


def test_reset_compare_in_day_scoped_to_day(tmp_path):
    """A Compare item on day 2 must not be touched when resetting day 1."""
    gw = _seed(tmp_path)
    # Move c to day 2 (deliberately reaching past the gateway to keep this test
    # local — gateway has no public 'move item to day' yet).
    with gw.store.transaction() as conn:
        conn.execute("INSERT INTO trip_day (day_number, date) VALUES (2, '2026-04-02')")
        conn.execute("UPDATE item SET day_number = 2 WHERE id = ?", ("c",))
    n = gw.reset_compare_in_day("pick", 1, "skipped")
    assert n == 1                               # only b
    states = gw.phase_states("pick")
    assert states["c"].state == "candidate"     # untouched (different day)


def test_reset_compare_in_day_rejects_bad_target(tmp_path):
    gw = _seed(tmp_path)
    with pytest.raises(ValueError):
        gw.reset_compare_in_day("pick", 1, "candidate")
    with pytest.raises(ValueError):
        gw.reset_compare_in_day("pick", 1, "")


def test_reset_compare_in_day_preserves_committed_at(tmp_path):
    """A row that was already committed earlier keeps its committed_at after
    the reset (spec/30: committed_at is the phase-exit stamp, separate from
    decided_at)."""
    gw = _seed(tmp_path)
    # Manually stamp b's committed_at to simulate a prior phase exit.
    with gw.store.transaction() as conn:
        conn.execute(
            "UPDATE phase_state SET committed_at = ? "
            "WHERE item_id = ? AND phase = 'pick'",
            ("2026-05-01T10:00:00+00:00", "b"),
        )
    gw.reset_compare_in_day("pick", 1, "skipped")
    row = gw.phase_state("b", "pick")
    assert row.committed_at == "2026-05-01T10:00:00+00:00"
    assert row.state == "skipped"


# --------------------------------------------------------------------------- #
# Schema v3 → v4 migration + item_visit gateway (spec/32 §2.10, §8.6)
# --------------------------------------------------------------------------- #


def test_item_visit_table_exists_on_fresh_db(tmp_path):
    """A fresh v4 event.db has item_visit + its phase index from the main DDL."""
    db = tmp_path / "fresh.db"
    store = EventStore.create(db, event_id="evt")
    try:
        names = {
            r["name"] for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        idx = {
            r["name"] for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "item_visit" in names
        assert "ix_item_visit_phase_visited" in idx
    finally:
        store.close()


def test_set_item_visited_writes_and_reads_back(tmp_path):
    gw = _seed(tmp_path)
    gw.set_item_visited("a", "pick", True)
    assert gw.items_visited_for_day(1, "pick") == {"a"}
    # Other phase independent.
    assert gw.items_visited_for_day(1, "edit") == set()


def test_set_item_visited_upsert_no_duplicate_rows(tmp_path):
    """ON CONFLICT DO UPDATE — never INSERT OR REPLACE (no cascade fire)."""
    gw = _seed(tmp_path)
    gw.set_item_visited("a", "pick", True)
    gw.set_item_visited("a", "pick", True)
    rows = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM item_visit WHERE item_id = ? AND phase = ?",
        ("a", "pick"),
    ).fetchone()
    assert rows["n"] == 1


def test_set_item_visited_can_clear(tmp_path):
    gw = _seed(tmp_path)
    gw.set_item_visited("a", "pick", True)
    gw.set_item_visited("a", "pick", False)
    assert gw.items_visited_for_day(1, "pick") == set()


def test_items_visited_for_day_scoped_to_day(tmp_path):
    """A visited item on day 2 must not surface when querying day 1."""
    gw = _seed(tmp_path)
    with gw.store.transaction() as conn:
        conn.execute(
            "INSERT INTO trip_day (day_number, date) VALUES (2, '2026-04-02')"
        )
        conn.execute("UPDATE item SET day_number = 2 WHERE id = ?", ("c",))
    gw.set_item_visited("a", "pick", True)   # day 1
    gw.set_item_visited("c", "pick", True)   # day 2
    assert gw.items_visited_for_day(1, "pick") == {"a"}
    assert gw.items_visited_for_day(2, "pick") == {"c"}


def test_items_visited_for_day_undated_uses_is_null(tmp_path):
    """day_number=None routes through IS NULL, not = NULL."""
    gw = _seed(tmp_path)
    with gw.store.transaction() as conn:
        conn.execute("UPDATE item SET day_number = NULL WHERE id = ?", ("d",))
    gw.set_item_visited("d", "pick", True)
    assert gw.items_visited_for_day(None, "pick") == {"d"}
    # The dated day query does NOT include the undated visit.
    assert gw.items_visited_for_day(1, "pick") == set()


def test_item_visit_cascades_on_item_delete(tmp_path):
    """FK ON DELETE CASCADE — removing an item also removes its visit rows."""
    gw = _seed(tmp_path)
    gw.set_item_visited("a", "pick", True)
    with gw.store.transaction() as conn:
        conn.execute("DELETE FROM item WHERE id = ?", ("a",))
    rows = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM item_visit WHERE item_id = ?",
        ("a",),
    ).fetchone()
    assert rows["n"] == 0


def test_item_visit_roundtrips_through_event_document(tmp_path):
    """Durable soft-state — save_document + load_document must round-trip the
    visit rows so backup/restore preserves the tick."""
    gw = _seed(tmp_path)
    gw.set_item_visited("a", "pick", True)
    gw.set_item_visited("b", "pick", True)
    doc = gw.store.load_document()
    keys = {(v.item_id, v.phase, bool(v.visited)) for v in doc.item_visits}
    assert ("a", "pick", True) in keys
    assert ("b", "pick", True) in keys


# --------------------------------------------------------------------------- #
# spec/32 §6.3 + §8 — adjustments_for_day (Process Day Grid cell colour source)
# --------------------------------------------------------------------------- #


def test_adjustments_for_day_returns_empty_when_no_rows(tmp_path):
    gw = _seed(tmp_path)
    assert gw.adjustments_for_day(1) == {}


def test_adjustments_for_day_returns_only_matching_day(tmp_path):
    """An adjustment on day 2 must not surface for a day-1 query."""
    gw = _seed(tmp_path)
    with gw.store.transaction() as conn:
        conn.execute(
            "INSERT INTO trip_day (day_number, date) VALUES (2, '2026-04-02')"
        )
        conn.execute("UPDATE item SET day_number = 2 WHERE id = ?", ("c",))
    from mira.store import models as m
    gw.save_adjustment(m.Adjustment(item_id="a"))  # day 1
    gw.save_adjustment(m.Adjustment(item_id="c"))  # day 2
    day1 = gw.adjustments_for_day(1)
    day2 = gw.adjustments_for_day(2)
    assert set(day1.keys()) == {"a"}
    assert set(day2.keys()) == {"c"}


def test_adjustments_for_day_carries_edit_exported_flag(tmp_path):
    """The flag is what the Day Grid colour rule reads — must round-trip."""
    gw = _seed(tmp_path)
    gw.set_edit_exported("a", True)
    adjs = gw.adjustments_for_day(1)
    assert "a" in adjs
    assert adjs["a"].edit_exported is True


def test_adjustments_for_day_undated_uses_is_null(tmp_path):
    """day_number=None routes through IS NULL."""
    gw = _seed(tmp_path)
    with gw.store.transaction() as conn:
        conn.execute("UPDATE item SET day_number = NULL WHERE id = ?", ("d",))
    gw.set_edit_exported("d", True)
    undated = gw.adjustments_for_day(None)
    assert set(undated.keys()) == {"d"}
    assert gw.adjustments_for_day(1) == {}


# --------------------------------------------------------------------------- #
# clear_visited_for_phase — "Start a new pass…" backing
# --------------------------------------------------------------------------- #


def test_clear_visited_for_phase_wipes_item_visits(tmp_path):
    """All item_visit rows for the given phase are deleted; other phases'
    rows are untouched."""
    gw = _seed(tmp_path)
    gw.set_item_visited("a", "pick", True)
    gw.set_item_visited("b", "pick", True)
    gw.set_item_visited("a", "edit", True)
    n = gw.clear_visited_for_phase("pick")
    assert n == 2
    assert gw.items_visited_for_day(1, "pick") == set()
    assert gw.items_visited_for_day(1, "edit") == {"a"}


def test_clear_visited_for_phase_resets_bucket_browsed(tmp_path):
    """bucket.browsed for the given phase is reset to 0; other state on
    the bucket row (default_state, reviewed, current_index) is preserved."""
    gw = _seed(tmp_path)
    gw.set_bucket_browsed("k1", "pick", True)
    gw.set_bucket_reviewed("k1", "pick", True)
    gw.set_bucket_current_index("k1", "pick", 5)
    gw.set_bucket_browsed("k1", "edit", True)
    gw.clear_visited_for_phase("pick")
    b_pick = gw.bucket("k1", "pick")
    assert b_pick.browsed is False
    # Other fields preserved.
    assert b_pick.reviewed is True
    assert b_pick.current_index == 5
    # Other phase untouched.
    b_edit = gw.bucket("k1", "edit")
    assert b_edit.browsed is True


def test_clear_visited_for_phase_does_not_touch_phase_state(tmp_path):
    """clear_visited_for_phase only wipes visited / browsed — Keep/Discard
    decisions (phase_state) are untouched."""
    gw = _seed(tmp_path)
    states = gw.phase_states("pick")
    assert states["a"].state == "picked"
    gw.set_item_visited("a", "pick", True)
    gw.clear_visited_for_phase("pick")
    after = gw.phase_states("pick")
    assert after["a"].state == "picked"


def test_clear_visited_for_phase_does_not_touch_adjustments(tmp_path):
    """clear_visited_for_phase("edit") leaves Adjustment.edit_exported
    intact — exported items stay exported."""
    gw = _seed(tmp_path)
    gw.set_item_visited("a", "edit", True)
    gw.set_edit_exported("a", True)
    gw.clear_visited_for_phase("edit")
    adj = gw.adjustment("a")
    assert adj is not None and adj.edit_exported is True


def test_clear_visited_for_phase_returns_zero_when_nothing_to_clear(tmp_path):
    gw = _seed(tmp_path)
    assert gw.clear_visited_for_phase("pick") == 0
