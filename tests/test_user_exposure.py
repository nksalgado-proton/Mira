"""spec/115 §2 — the independent Exposure slider.

The new slider is a per-image USER exposure (EV) that is added to the
resolved ``Params.exposure`` AFTER the Look's strength scaling. It is
independent of both the Look and Strength — a clean per-image EV nudge
on top of whatever the Look already does. Persists to a new
``adjustment.user_exposure`` column (v15→v16 migration); double-click
resets to 0; +1 EV ≈ 2× linear gain in the rendered output (same as
``Params.exposure`` everywhere else)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from core import photo_render
from core.photo_render import Params, apply_params
from mira.store import models as m
from mira.store import schema as event_schema
from mira.store.models import Adjustment
from mira.store.repo import EventStore
from mira.ui.edited.adjustment_surface import AdjustmentSurface


NOW = "2026-06-23T00:00:00+00:00"


# ── Helpers ──────────────────────────────────────────────────────


def _surface() -> AdjustmentSurface:
    s = AdjustmentSurface()
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    img[10, 20] = (200, 120, 40)
    s.load_image(img)
    s.set_look("natural")
    return s


def _make_event_store(tmp_path) -> EventStore:
    """Fresh event.db at the current SCHEMA_VERSION (v16). Stocks one
    minimal captured photo item via :meth:`save_document` so every
    table-level invariant on ``item`` lines up — easier than threading
    each NOT NULL / CHECK by hand from SQL."""
    store = EventStore.create(
        tmp_path / "event.db", event_id="evt-ue", app_version="t",
        created_at=NOW)
    doc = m.EventDocument(event=m.Event(
        uuid="evt-ue", name="UE fixture",
        created_at=NOW, updated_at=NOW))
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [m.Item(
        id="it1", kind="photo", created_at=NOW, provenance="captured",
        origin_relpath="Original Media/it1.jpg", sha256="a" * 64,
        byte_size=1000, materialized_at=NOW, materialized_phase="ingest",
        camera_id="G9",
        capture_time_raw=NOW, capture_time_corrected=NOW,
    )]
    store.save_document(doc)
    return store


# ── Render math: user_exposure rides on top of strength scaling ─


def test_user_exposure_added_after_strength_scaling(qapp):
    """The render pipeline applies Strength to the Look's bias FIRST,
    then adds user_exposure to the result. So the final
    ``Params.exposure`` is ``look_scaled.exposure + user_exposure`` —
    not ``(look.exposure + user_exposure) * strength``."""
    s = _surface()
    # Pick a Look that contributes exposure (Brighter has + bias).
    s.set_look("brighter")
    s._look_strength = 0.5
    base_params = s._params_for_look()      # before user_exposure
    s._user_exposure = 1.0                   # +1 EV nudge
    nudged = s._params_for_look()

    assert nudged.exposure == pytest.approx(base_params.exposure + 1.0)


def test_user_exposure_unscaled_by_strength(qapp):
    """The whole point of being "independent of Strength" — at
    strength=0 the Look contributes nothing, but user_exposure still
    nudges the same EV amount."""
    s = _surface()
    s.set_look("brighter")
    s._look_strength = 0.0                   # Look contributes nothing
    s._user_exposure = 0.7

    params = s._params_for_look()
    # The Look's bias is fully zeroed, but the user_exposure still
    # rides on top.
    assert params.exposure == pytest.approx(0.7)


def test_user_exposure_is_zero_default(qapp):
    """Fresh surface → user_exposure 0 → identical to pre-spec/115."""
    s = _surface()
    assert s._user_exposure == 0.0
    state = s.get_state()
    assert state.user_exposure == 0.0


# ── Render output: +1 EV ≈ 2× linear gain ───────────────────────


def test_plus_one_ev_doubles_linear_gain(qapp):
    """spec/115 §2 — the EV math is the same one ``Params.exposure``
    already implements (linear-light gain, 2^EV). A pure +1 EV nudge
    therefore reads as ~2× brightness in the rendered output (modulo
    clipping at the 8-bit ceiling)."""
    # A mid-gray frame — clipping won't kick in for the +1 EV test.
    base = np.full((20, 20, 3), 64, dtype=np.uint8)     # ~25% gray
    plain = apply_params(base, Params(exposure=0.0))
    brighter = apply_params(base, Params(exposure=1.0))

    # Mean over the frame — pure linear gain doubles mid-gray.
    plain_mean = float(plain.mean())
    brighter_mean = float(brighter.mean())
    assert brighter_mean == pytest.approx(plain_mean * 2.0, rel=0.02)


# ── Double-click resets to 0 ─────────────────────────────────────


def test_exposure_combo_picks_value_and_resets_to_zero(qapp):
    """spec/157 — the Exposure dropdown maps its −5..+5 steps to EV
    values; the +5 step is +2 EV and the middle (0) step resets to 0."""
    s = _surface()
    # +5 step (last item) → +2 EV.
    s._exposure_combo.setCurrentIndex(s._exposure_combo.count() - 1)
    assert s._user_exposure == pytest.approx(2.0)
    # 0 step (middle item) → no nudge.
    s._exposure_combo.setCurrentIndex(s._exposure_combo.count() // 2)
    assert s._user_exposure == 0.0


def test_reset_all_clears_user_exposure(qapp):
    """Reset all — the destructive per-item rollback — must clear
    user_exposure along with the Look, Strength, filter, crop and
    rotation. Otherwise the user sees a "still nudged" frame after
    asking for the original back."""
    s = _surface()
    s._user_exposure = 1.5
    s._on_reset_all()
    assert s._user_exposure == 0.0


# ── set_state / get_state round-trip ─────────────────────────────


def test_set_state_loads_user_exposure(qapp):
    s = _surface()
    s.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        rotation=0, creative_filter=None,
        look_strength=1.0, user_exposure=0.75,
    )
    assert s._user_exposure == pytest.approx(0.75)
    # The dropdown snaps its DISPLAY to the nearest step (0.75 → +2 = 0.8)
    # while the surface keeps the exact value for rendering.
    assert s._exposure_combo.currentData() == pytest.approx(0.8)


def test_set_state_clamps_wild_user_exposure(qapp):
    """A migrated row with a wild value (no CHECK on ALTER) loads
    safely — the surface clamps on read just like Strength does."""
    s = _surface()
    s.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        rotation=0, creative_filter=None,
        look_strength=1.0, user_exposure=42.0,
    )
    assert s._user_exposure == 2.0           # clamped to the upper bound

    s.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        rotation=0, creative_filter=None,
        look_strength=1.0, user_exposure=-42.0,
    )
    assert s._user_exposure == -2.0


def test_get_state_round_trip_preserves_user_exposure(qapp):
    s = _surface()
    s._user_exposure = 0.25
    state = s.get_state()
    assert state.user_exposure == pytest.approx(0.25)


# ── Persistence: column + migration ──────────────────────────────


def test_user_exposure_column_persists_default(tmp_path):
    """A freshly-created Adjustment row carries the schema's default
    user_exposure=0.0 — so a legacy creator that never sets the field
    still renders identically to pre-spec/115."""
    store = _make_event_store(tmp_path)
    try:
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO adjustment (item_id) VALUES ('it1')")
            row = conn.execute(
                "SELECT user_exposure FROM adjustment "
                "WHERE item_id = 'it1'").fetchone()
        assert row["user_exposure"] == 0.0
    finally:
        store.close()


def test_user_exposure_round_trips_via_dataclass(tmp_path):
    store = _make_event_store(tmp_path)
    try:
        adj = Adjustment(item_id="it1", user_exposure=0.5)
        store.upsert(adj)
        loaded = store.get(Adjustment, "it1")
        assert loaded is not None
        assert loaded.user_exposure == pytest.approx(0.5)
    finally:
        store.close()


def test_filter_strength_persists_default_and_round_trips(tmp_path):
    """spec/156 — ``filter_strength`` defaults to 0.0 on both adjustment
    tables (fresh DDL) and round-trips through the generic repo."""
    store = _make_event_store(tmp_path)
    try:
        with store.transaction() as conn:
            conn.execute("INSERT INTO adjustment (item_id) VALUES ('it1')")
            conn.execute(
                "INSERT INTO video_adjustment (item_id) VALUES ('it1')")
            for table in ("adjustment", "video_adjustment"):
                row = conn.execute(
                    f"SELECT filter_strength FROM {table} "
                    "WHERE item_id = 'it1'").fetchone()
                assert row["filter_strength"] == 0.0
        # Dataclass round-trip on both.
        store.upsert(Adjustment(item_id="it1", filter_strength=2.0))
        assert store.get(Adjustment, "it1").filter_strength == pytest.approx(2.0)
        store.upsert(m.VideoAdjustment(item_id="it1", filter_strength=-2.0))
        assert store.get(
            m.VideoAdjustment, "it1").filter_strength == pytest.approx(-2.0)
    finally:
        store.close()


def test_surface_filter_strength_round_trips_and_gates_on_filter(qapp):
    """spec/156 — the surface holds the strength, round-trips it through
    get/set_state, reflects it on the dropdown, and greys the dropdown
    when no filter is chosen (nothing to scale)."""
    s = _surface()
    s.set_state(
        look="natural", crop_norm=None, box_angle=0.0, style="general",
        aspect_label="Original", creative_filter="vivid",
        filter_strength=-1.0)
    assert s.get_state().filter_strength == pytest.approx(-1.0)
    assert s._filter_strength_combo.currentData() == pytest.approx(-1.0)
    assert s._filter_strength_combo.isEnabled()
    # No filter → the strength control disables.
    s.set_state(
        look="natural", crop_norm=None, box_angle=0.0, style="general",
        aspect_label="Original", creative_filter=None, filter_strength=0.0)
    assert not s._filter_strength_combo.isEnabled()


def test_filter_strength_check_constraint_on_fresh_install(tmp_path):
    """Fresh DDL carries CHECK (filter_strength BETWEEN -2 AND 2)."""
    store = _make_event_store(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO adjustment (item_id, filter_strength) "
                    "VALUES ('it1', 3.0)")
    finally:
        store.close()


def test_user_exposure_check_constraint_on_fresh_install(tmp_path):
    """Fresh DDL carries CHECK (user_exposure BETWEEN -2 AND 2). A
    fresh install rejects out-of-range values; the gateway seam's
    clamp is the migrated-row safety net."""
    store = _make_event_store(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO adjustment (item_id, user_exposure) "
                    "VALUES ('it1', 5.0)")
    finally:
        store.close()


def test_migration_v15_to_v16_adds_user_exposure(tmp_path):
    """A pre-spec/115 event.db at v15 migrates cleanly to v16 with the
    new column landing at 0.0 for every existing row."""
    store = _make_event_store(tmp_path)
    try:
        conn = store.conn
        with store.transaction():
            conn.execute(
                "INSERT INTO adjustment (item_id, look_strength) "
                "VALUES ('it1', 1.5)")
            conn.execute("ALTER TABLE adjustment DROP COLUMN user_exposure")
            # spec/127 v17→v18 — drop camera_tz_correction so the
            # v17→v18 CREATE TABLE on the way back up doesn't collide
            # (the v16→v17 rename step skips silently when the columns
            # are already at *_seconds, so we don't need to undo that).
            conn.execute("DROP INDEX IF EXISTS ix_camera_tz_correction_tz")
            conn.execute("DROP TABLE IF EXISTS camera_tz_correction")
            # spec/144 v18→v19 — strip lineage.duration_ms so the
            # ALTER on the way back up doesn't collide.
            conn.execute("ALTER TABLE lineage DROP COLUMN duration_ms")
            # spec/152 v19→v20 — strip cut.transition_ms so the ADD on
            # the way back up doesn't collide.
            conn.execute("ALTER TABLE cut DROP COLUMN transition_ms")
            # spec/156 v20→v21 — strip filter_strength from both
            # adjustment tables so the ADD COLUMN steps don't collide.
            conn.execute("ALTER TABLE adjustment DROP COLUMN filter_strength")
            conn.execute(
                "ALTER TABLE video_adjustment DROP COLUMN filter_strength")
            conn.execute(
                "UPDATE schema_info SET schema_version = 15 WHERE id = 1")

        event_schema.migrate(conn)
        assert event_schema.get_version(conn) == event_schema.SCHEMA_VERSION
        row = conn.execute(
            "SELECT user_exposure FROM adjustment "
            "WHERE item_id = 'it1'").fetchone()
        assert row["user_exposure"] == 0.0
    finally:
        store.close()
