"""Tests for spec/111 — Cut aspect ratio + aspect-matched cards.

The slideshow canvas shape belongs to the Cut. Three contracts:

* The ``core.cut_aspect`` map pins ``aspect → (pte_string, w, h)`` so
  spec/107's PTE override and Mira's own renderers stay in lock-step.
* Both the per-event and the cross-event Cut persist + migrate the
  ``aspect`` column.
* A rendered separator / opener card has the **Cut's** dimensions —
  for the per-event AND cross-event lanes. Tomorrow's "16:9 cards in a
  4:3 show" bug never returns."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.cut_aspect import (
    ASPECT_1_1, ASPECT_3_2, ASPECT_4_3, ASPECT_16_9,
    DEFAULT_ASPECT, all_aspects, aspect_dimensions, aspect_pte_string,
    aspect_spec, normalise,
)
from mira.store import models as m
from mira.store import schema as event_schema
from mira.store.repo import EventStore
from mira.user_store import models as um
from mira.user_store import schema as user_schema
from mira.user_store.repo import UserStore


NOW = "2026-06-22T00:00:00+00:00"


# --------------------------------------------------------------------- #
# 1. The (aspect → (pte_string, w, h)) map
# --------------------------------------------------------------------- #


def test_aspect_map_covers_the_four_canonical_aspects():
    """The closed enum is exactly the four canonical aspects spec/111 §2
    names. ``all_aspects`` carries them in render order (16:9 default
    first)."""
    assert tuple(all_aspects()) == (
        ASPECT_16_9, ASPECT_4_3, ASPECT_3_2, ASPECT_1_1,
    )


def test_aspect_map_dimensions_and_pte_strings():
    """spec/107 — PTE writes ``AspectRatio`` with a hyphen separator
    (``16-9``). spec/111 — Mira renders cards at the matching pixel
    dimensions. Pin both sides so the show canvas and the card
    canvas can't drift apart."""
    assert aspect_pte_string(ASPECT_16_9) == "16-9"
    assert aspect_dimensions(ASPECT_16_9) == (1920, 1080)
    assert aspect_pte_string(ASPECT_4_3) == "4-3"
    assert aspect_dimensions(ASPECT_4_3) == (1024, 768)
    assert aspect_pte_string(ASPECT_3_2) == "3-2"
    assert aspect_dimensions(ASPECT_3_2) == (1620, 1080)
    assert aspect_pte_string(ASPECT_1_1) == "1-1"
    assert aspect_dimensions(ASPECT_1_1) == (1080, 1080)


def test_dimensions_match_ratio_within_one_pixel():
    """Every entry's (w, h) ratio must equal its aspect string's ratio
    so a card painted at the canvas dims fills the slideshow canvas
    without letterboxing."""
    expected = {
        ASPECT_16_9: 16 / 9,
        ASPECT_4_3: 4 / 3,
        ASPECT_3_2: 3 / 2,
        ASPECT_1_1: 1.0,
    }
    for aspect, ratio in expected.items():
        w, h = aspect_dimensions(aspect)
        assert abs(w / h - ratio) < 0.01, (
            f"aspect map {aspect} → ({w},{h}) ratio {w/h:.4f} "
            f"does not match canonical {ratio:.4f}")


def test_normalise_falls_back_to_default_for_unknown_values():
    """A bogus / legacy row never crashes the renderer — it reads as the
    canonical default (16:9). Belt-and-braces for the schema CHECK
    (which ALTER TABLE could not add to migrated rows)."""
    assert normalise(None) == DEFAULT_ASPECT
    assert normalise("") == DEFAULT_ASPECT
    assert normalise("garbage") == DEFAULT_ASPECT
    assert normalise("16:10") == DEFAULT_ASPECT
    # Canonical values pass through unchanged.
    assert normalise(ASPECT_4_3) == ASPECT_4_3
    assert normalise(ASPECT_3_2) == ASPECT_3_2


def test_aspect_spec_carries_all_four_fields():
    """The ``AspectSpec`` exposes the wire string + canvas dims under
    named fields the renderers can read directly."""
    spec = aspect_spec(ASPECT_4_3)
    assert spec.aspect == ASPECT_4_3
    assert spec.pte_aspect == "4-3"
    assert spec.width == 1024
    assert spec.height == 768


# --------------------------------------------------------------------- #
# 2. Persistence — per-event Cut.aspect
# --------------------------------------------------------------------- #


def _make_event_store(tmp_path) -> EventStore:
    """Fresh event.db at the current SCHEMA_VERSION (v15+)."""
    store = EventStore.create(
        tmp_path / "event.db", event_id="evt-asp", app_version="t",
        created_at=NOW)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", ("evt-asp", "E", NOW, NOW))
    return store


def test_per_event_cut_aspect_persists_default(tmp_path):
    """A freshly-created Cut row carries the schema's default ``'16:9'``
    so a legacy creator that never sets the field still renders cards
    at the canonical default canvas."""
    store = _make_event_store(tmp_path)
    try:
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO cut (id, tag, source_dc_kind, created_at, "
                "                 updated_at) "
                "VALUES ('c1', 'tag1', 'user', ?, ?)", (NOW, NOW))
            row = conn.execute(
                "SELECT aspect FROM cut WHERE id = 'c1'").fetchone()
        assert row["aspect"] == "16:9"
    finally:
        store.close()


def test_per_event_cut_aspect_round_trips_via_dataclass(tmp_path):
    """Writing a :class:`m.Cut` with ``aspect='4:3'`` reads back through
    the EventStore.upsert/get round trip exactly."""
    store = _make_event_store(tmp_path)
    try:
        cut = m.Cut(
            id="c-4-3", tag="tag43", created_at=NOW, updated_at=NOW,
            aspect="4:3",
        )
        store.upsert(cut)
        loaded = store.get(m.Cut, "c-4-3")
        assert loaded is not None
        assert loaded.aspect == "4:3"
    finally:
        store.close()


def test_per_event_check_constraint_rejects_unknown_aspect(tmp_path):
    """The DDL CHECK on fresh installs guards the closed enum. Migrated
    rows skip the constraint (ALTER TABLE can't add a CHECK), so the
    gateway seam's :func:`normalise` is the runtime safety net."""
    store = _make_event_store(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO cut (id, tag, source_dc_kind, aspect, "
                    "                 created_at, updated_at) "
                    "VALUES ('c-bad', 'tagbad', 'user', '16:10', ?, ?)",
                    (NOW, NOW))
    finally:
        store.close()


def test_per_event_migration_adds_aspect_column_to_legacy_db(tmp_path):
    """A pre-spec/111 event.db at v14 migrates to v15+ with the
    ``cut.aspect`` column landing at default ``'16:9'`` for every
    existing row (the legacy ``settings.separator_aspect`` default the
    new field replaces)."""
    store = _make_event_store(tmp_path)
    try:
        conn = store.conn
        # Roll back to v14: drop the aspect column + reset the version.
        with store.transaction():
            conn.execute(
                "INSERT INTO cut (id, tag, source_dc_kind, aspect, "
                "                 created_at, updated_at) "
                "VALUES ('c-legacy', 'legacy', 'user', '16:9', ?, ?)",
                (NOW, NOW))
            conn.execute("ALTER TABLE cut DROP COLUMN aspect")
            conn.execute(
                "UPDATE schema_info SET schema_version = 14 WHERE id = 1")

        event_schema.migrate(conn)
        assert event_schema.get_version(conn) == event_schema.SCHEMA_VERSION
        row = conn.execute(
            "SELECT aspect FROM cut WHERE id = 'c-legacy'").fetchone()
        assert row["aspect"] == "16:9"
    finally:
        store.close()


# --------------------------------------------------------------------- #
# 3. Persistence — cross-event Cut.aspect (mira.db)
# --------------------------------------------------------------------- #


def _make_user_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db", app_version="t", created_at=NOW)


def test_cross_event_cut_aspect_persists_default(tmp_path):
    """The mira.db cross-event Cut row defaults the new column to
    ``'16:9'`` — same posture as the per-event side."""
    us = _make_user_store(tmp_path)
    try:
        with us.transaction() as conn:
            conn.execute(
                "INSERT INTO cut (id, tag, source_dc_kind, created_at, "
                "                 updated_at) "
                "VALUES ('xc1', 'xtag1', 'user', ?, ?)", (NOW, NOW))
            row = conn.execute(
                "SELECT aspect FROM cut WHERE id = 'xc1'").fetchone()
        assert row["aspect"] == "16:9"
    finally:
        us.close()


def test_cross_event_cut_aspect_round_trips_via_dataclass(tmp_path):
    us = _make_user_store(tmp_path)
    try:
        cut = um.Cut(
            id="xc-3-2", tag="xtag32", created_at=NOW, updated_at=NOW,
            aspect="3:2",
        )
        us.upsert(cut)
        loaded = us.get(um.Cut, "xc-3-2")
        assert loaded is not None
        assert loaded.aspect == "3:2"
    finally:
        us.close()


def test_cross_event_migration_adds_aspect_column(tmp_path):
    """mira.db v8 → v9 ALTER TABLE adds aspect at default 16:9 across
    every existing row."""
    us = _make_user_store(tmp_path)
    try:
        conn = us.conn
        with us.transaction():
            conn.execute(
                "INSERT INTO cut (id, tag, source_dc_kind, created_at, "
                "                 updated_at) "
                "VALUES ('xc-legacy', 'xlegacy', 'user', ?, ?)",
                (NOW, NOW))
            conn.execute("ALTER TABLE cut DROP COLUMN aspect")
            conn.execute(
                "UPDATE schema_info SET schema_version = 8 WHERE id = 1")
        user_schema.migrate(conn)
        assert user_schema.get_version(conn) == user_schema.SCHEMA_VERSION
        row = conn.execute(
            "SELECT aspect FROM cut WHERE id = 'xc-legacy'").fetchone()
        assert row["aspect"] == "16:9"
    finally:
        us.close()


# --------------------------------------------------------------------- #
# 4. Rendered cards at the Cut's aspect — both exporters
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("aspect", [
    ASPECT_16_9, ASPECT_4_3, ASPECT_3_2, ASPECT_1_1,
])
def test_separator_card_renders_at_cut_aspect_dimensions(qapp, aspect):
    """spec/111 §3 — the day-separator card must paint at the Cut's
    canvas (W, H) so cards, photos and the show all agree. Calling the
    shared renderer with the Cut's aspect produces a QImage of EXACTLY
    those dimensions (height passes through verbatim; width = h ×
    aspect, rounded)."""
    from mira.ui.shared.separator_card import render_separator_image
    expected_w, expected_h = aspect_dimensions(aspect)
    img = render_separator_image(
        day_number=3, date="2026-04-03",
        location="Tokyo", description="city",
        aspect=aspect, height=expected_h,
        card_style="black", seed_key="cut-1:3")
    assert img.height() == expected_h
    # Width is rounded from height × ratio; allow a single-pixel slack
    # for the integer round.
    assert abs(img.width() - expected_w) <= 1, (
        f"separator at {aspect}: expected ~{expected_w}x{expected_h}, "
        f"got {img.width()}x{img.height()}")


@pytest.mark.parametrize("aspect", [
    ASPECT_16_9, ASPECT_4_3, ASPECT_3_2, ASPECT_1_1,
])
def test_opener_card_renders_at_cut_aspect_dimensions(qapp, aspect):
    """The opener slide (the show's first frame) takes the same canvas
    treatment — the Cut owns its aspect; the opener inherits."""
    from mira.ui.shared.separator_card import render_cut_opener_image
    expected_w, expected_h = aspect_dimensions(aspect)
    img = render_cut_opener_image(
        tag_text="#sunday_walk",
        lines=["12 items · 1:12", "music: happy"],
        aspect=aspect, height=expected_h,
        card_style="black", seed_key="cut-1")
    assert img.height() == expected_h
    assert abs(img.width() - expected_w) <= 1


def test_per_event_separator_writer_uses_cut_aspect(qapp, tmp_path):
    """The per-event writer factory in share_cuts_page reads
    ``cut.aspect`` and threads it into the renderer. We exercise the
    same code path the export pipeline uses: the writer captures a
    QImage on disk; the saved image must carry the Cut's pixel dims."""
    from core.cut_aspect import aspect_dimensions, normalise
    from mira.ui.shared.separator_card import render_separator_image

    # Stand in for the share_cuts_page factory — same pattern as
    # ``ShareCutsPage._separator_writer`` but without instantiating the
    # full page (the factory's contract is "build a writer at the Cut's
    # aspect"; we replicate that contract in one line of glue).
    cut = m.Cut(
        id="cut-4-3", tag="t43", created_at=NOW, updated_at=NOW,
        aspect="4:3")
    aspect = normalise(cut.aspect)
    expected_w, expected_h = aspect_dimensions(aspect)

    def write(target_path: Path, day) -> None:
        img = render_separator_image(
            day_number=day, date="2026-04-03",
            location="Tokyo", description="",
            aspect=aspect, height=expected_h,
            card_style="black", seed_key=f"{cut.id}:{day}")
        if not img.save(str(target_path), "JPG", 92):
            raise OSError(f"could not write {target_path}")

    out = tmp_path / "sep_day3.jpg"
    write(out, 3)
    assert out.is_file()

    from PyQt6.QtGui import QImage
    loaded = QImage(str(out))
    assert loaded.height() == expected_h
    assert abs(loaded.width() - expected_w) <= 1


def test_cross_event_export_accepts_aspect_aware_opener_writer(tmp_path):
    """spec/111 acceptance — both exporters honour the Cut's aspect.
    ``export_cross_event_cut`` now accepts the same
    ``opener_writer``/``separator_writer`` kwargs the per-event side
    does, and runs the opener once at the show's head when wired. The
    test asserts the writer is invoked with a path under the target
    folder so a host that builds the writer at ``cut.aspect`` actually
    delivers an aspect-matched card to disk."""
    from core import audio_library  # noqa: F401  — keeps imports honest
    from core.cut_aspect import aspect_dimensions, normalise
    from mira.gateway.gateway import Gateway
    from mira.gateway.index import EventsIndex, make_entry
    from mira.settings.repo import SettingsRepo
    from mira.shared.cross_event_cut_export import export_cross_event_cut
    from mira.ui.shared.separator_card import render_cut_opener_image

    # Build a minimal umbrella + one source event with one Exported
    # Media file so the cross-event walk has a member to place.
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

    src = photos_base / "Source"
    src.mkdir()
    src_store = EventStore.create(
        src / "event.db", event_id="src", app_version="t",
        created_at=NOW)
    with src_store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", ("src", "Source", NOW, NOW))
    src_store.close()
    (src / "Exported Media" / "Day01").mkdir(parents=True)
    (src / "Exported Media" / "Day01" / "p1.jpg").write_bytes(b"bytes")
    gw.index.upsert(make_entry(
        event_id="src", name="Source",
        start_date=None, end_date=None, is_closed=False,
        event_root=src, photos_base_path=photos_base))

    # Cross-event Cut at 1:1, one member from the source event.
    lg = gw.library_gateway()
    with lg.user_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, aspect, "
            "                 created_at, updated_at) "
            "VALUES ('xcut', 'xcut', 'user', '1:1', ?, ?)", (NOW, NOW))
    lg.set_cross_event_cut_members("xcut", [
        {"kind": "export",
         "export_relpath": "Exported Media/Day01/p1.jpg",
         "event_id": "src"},
    ])
    cut = lg.cross_event_cut("xcut")
    assert cut is not None and cut.aspect == "1:1"

    aspect = normalise(cut.aspect)
    expected_w, expected_h = aspect_dimensions(aspect)

    captured: list = []

    def opener_writer(target_path: Path) -> None:
        captured.append(Path(target_path))
        img = render_cut_opener_image(
            tag_text="#xcut", lines=["one item"],
            aspect=aspect, height=expected_h,
            card_style="black", seed_key=cut.id)
        if not img.save(str(target_path), "JPG", 92):
            raise OSError(f"could not write {target_path}")

    target = tmp_path / "out"
    summary = export_cross_event_cut(
        gw, "anchor-ignored", "xcut", target=target,
        opener_writer=opener_writer)
    assert summary["separators"] == 1
    assert len(captured) == 1
    assert captured[0].parent == target
    # The card actually landed at the Cut's aspect dims.
    from PyQt6.QtGui import QImage
    img = QImage(str(captured[0]))
    assert img.height() == expected_h
    assert abs(img.width() - expected_w) <= 1
    gw.close()
