"""spec/61 slice 3 — the cuts gateway facade.

Logic-only (no Qt). Exercises the file-based membership model over a real
``event.db``: the #exported live query (edit-phase lineage through
``visible_item``), pool algebra (+/− left to right), style/type filters,
Cut CRUD with the name transform enforced at the gateway, the replace-all
membership commit, show ordering, totals, and the FK cascades that make
"delete an export → it falls out of every Cut" automatic.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore

FIXED_NOW = "2026-06-11T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _photo(item_id, day, t, classification=None):
    return m.Item(
        id=item_id, kind="photo", created_at=FIXED_NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.jpg", sha256="a" * 64,
        byte_size=1000, materialized_at=FIXED_NOW, materialized_phase="ingest",
        camera_id="G9", day_number=day,
        capture_time_raw=t, capture_time_corrected=t,
        classification=classification,
    )


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-c", name="Cuts fixture", created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [
        m.TripDay(day_number=1, date="2026-04-01"),
        m.TripDay(day_number=2, date="2026-04-02"),
    ]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        _photo("p1", 1, "2026-04-01T08:00:00", "macro"),
        _photo("p2", 1, "2026-04-01T09:00:00", "wildlife"),
        _photo("p3", 2, "2026-04-02T10:00:00", "macro"),
        m.Item(
            id="v1", kind="video", created_at=FIXED_NOW, provenance="captured",
            origin_relpath="Original Media/v1.mp4", sha256="b" * 64,
            byte_size=5000, materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=2,
            capture_time_raw="2026-04-02T11:00:00",
            capture_time_corrected="2026-04-02T11:00:00",
            duration_ms=30_000,
        ),
        _photo("p4", 2, "2026-04-02T12:00:00", "macro"),  # never exported
    ]
    # The exported universe: p1, p2 once; p3 TWICE (two versions = two pool
    # entries, spec/61 §1.2); the video once. p4 has no lineage. One
    # share-phase row proves #exported is edit-phase only.
    doc.lineage = [
        m.Lineage(export_relpath="Edited Media/e1.jpg", phase="edit",
                  source_kind="item", source_item_id="p1", exported_at="t1"),
        m.Lineage(export_relpath="Edited Media/e2.jpg", phase="edit",
                  source_kind="item", source_item_id="p2", exported_at="t2"),
        m.Lineage(export_relpath="Edited Media/e3a.jpg", phase="edit",
                  source_kind="item", source_item_id="p3", exported_at="t3"),
        m.Lineage(export_relpath="Edited Media/e3b.jpg", phase="edit",
                  source_kind="item", source_item_id="p3", exported_at="t4"),
        m.Lineage(export_relpath="Edited Media/v1.mp4", phase="edit",
                  source_kind="item", source_item_id="v1", exported_at="t5"),
        m.Lineage(export_relpath="Cuts/old/x.jpg", phase="share",
                  source_kind="item", source_item_id="p1", exported_at="t6"),
    ]
    doc.cuts = [m.Cut(id="cut-s", tag="short_version",
                      created_at=FIXED_NOW, updated_at=FIXED_NOW)]
    doc.cut_members = [m.CutMember(
        cut_id="cut-s", export_relpath="Edited Media/e1.jpg", added_at=FIXED_NOW)]
    return doc


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# --------------------------------------------------------------------------- #
# #exported — the built-in live query
# --------------------------------------------------------------------------- #


def test_exported_files_edit_phase_only_in_show_order(gw):
    rels = [ln.export_relpath for ln in gw.exported_files()]
    # share-phase row and never-exported p4 are absent; both versions of p3
    # present (file-based universe); chronological by source capture time,
    # relpath breaks the equal-time tie between p3's two versions.
    assert rels == [
        "Edited Media/e1.jpg", "Edited Media/e2.jpg",
        "Edited Media/e3a.jpg", "Edited Media/e3b.jpg", "Edited Media/v1.mp4",
    ]


def test_exported_files_respects_hidden_days(gw):
    gw.store.conn.execute("UPDATE trip_day SET hidden = 1 WHERE day_number = 2")
    rels = [ln.export_relpath for ln in gw.exported_files()]
    assert rels == ["Edited Media/e1.jpg", "Edited Media/e2.jpg"]


# --------------------------------------------------------------------------- #
# Pool algebra + filters
# --------------------------------------------------------------------------- #


def test_resolve_pool_exported_minus_cut(gw):
    rows = gw.resolve_pool([("+", "exported"), ("-", "short_version")])
    assert [ln.export_relpath for ln in rows] == [
        "Edited Media/e2.jpg", "Edited Media/e3a.jpg",
        "Edited Media/e3b.jpg", "Edited Media/v1.mp4",
    ]


def test_resolve_pool_unknown_tag_contributes_nothing(gw):
    # Recipes are a record of intent — a deleted/unknown tag shrinks
    # gracefully instead of raising.
    rows = gw.resolve_pool([("+", "short_version"), ("+", "no_such_cut")])
    assert [ln.export_relpath for ln in rows] == ["Edited Media/e1.jpg"]


def test_resolve_pool_bad_operator_raises(gw):
    with pytest.raises(ValueError):
        gw.resolve_pool([("*", "exported")])


def test_resolve_pool_style_filter(gw):
    rows = gw.resolve_pool([("+", "exported")], style_filter=["macro"])
    # wildlife e2 and the unclassified video drop; both macro versions stay.
    assert [ln.export_relpath for ln in rows] == [
        "Edited Media/e1.jpg", "Edited Media/e3a.jpg", "Edited Media/e3b.jpg",
    ]


def test_resolve_pool_type_filter(gw):
    assert [ln.export_relpath for ln in
            gw.resolve_pool([("+", "exported")], type_filter="video")] == [
        "Edited Media/v1.mp4"]
    photos = gw.resolve_pool([("+", "exported")], type_filter="photo")
    assert all(not ln.export_relpath.endswith(".mp4") for ln in photos)
    assert len(photos) == 4


# --------------------------------------------------------------------------- #
# CRUD — the gateway is the name-transform enforcement point
# --------------------------------------------------------------------------- #


def test_create_cut_slugifies_and_persists(gw):
    cut = gw.create_cut(
        "Pássaros do Brasil!", target_s=600, max_s=720,
        pool_expr=[("+", "exported"), ("-", "short_version")],
        style_filter=["macro"], music_category="happy")
    assert cut.tag == "passaros_do_brasil"
    stored = gw.cut_by_tag("passaros_do_brasil")
    assert stored is not None and stored.id == cut.id
    assert gw.cut_pool_expr(stored) == [("+", "exported"), ("-", "short_version")]
    assert gw.cut_style_filter(stored) == ["macro"]
    assert stored.music_category == "happy"


def test_create_cut_rejects_taken_reserved_empty(gw):
    with pytest.raises(ValueError, match="taken"):
        gw.create_cut("Short Version")          # case-blind collision
    with pytest.raises(ValueError, match="reserved"):
        gw.create_cut("Exported")               # built-in live query
    with pytest.raises(ValueError, match="empty"):
        gw.create_cut("★ ♥")


def test_rename_cut_excludes_self_from_taken(gw):
    renamed = gw.rename_cut("cut-s", "Versão Curta")
    assert renamed.tag == "versao_curta"
    assert gw.cut_by_tag("short_version") is None
    # renaming to its own (new) name is a no-op collision-wise
    assert gw.rename_cut("cut-s", "versão curta").tag == "versao_curta"
    other = gw.create_cut("other")
    with pytest.raises(ValueError, match="taken"):
        gw.rename_cut(other.id, "Versao_Curta")


def test_update_cut_settings_whitelist(gw):
    gw.update_cut_settings("cut-s", target_s=300, music_category="calm")
    cut = gw.cut("cut-s")
    assert cut.target_s == 300 and cut.music_category == "calm"
    with pytest.raises(ValueError):
        gw.update_cut_settings("cut-s", tag="nope")


def test_card_style_lives_in_extras(gw):
    """card_style (Nelson round 3) rides extras_json — created with the
    cut, updatable via update_cut_settings (merge, not clobber)."""
    cut = gw.create_cut("Colorida", card_style="multi")
    assert gw.cut_card_style(gw.cut(cut.id)) == "multi"
    assert gw.cut_card_style(gw.cut("cut-s")) == "black"   # default
    gw.update_cut_settings(cut.id, card_style="single", target_s=60)
    got = gw.cut(cut.id)
    assert gw.cut_card_style(got) == "single" and got.target_s == 60


# --------------------------------------------------------------------------- #
# Membership commit + cascades + totals
# --------------------------------------------------------------------------- #


def test_set_cut_members_replaces_and_dedupes(gw):
    n = gw.set_cut_members("cut-s", [
        "Edited Media/e2.jpg", "Edited Media/v1.mp4", "Edited Media/e2.jpg"])
    assert n == 2
    rels = [ln.export_relpath for ln in gw.cut_member_files("cut-s")]
    assert rels == ["Edited Media/e2.jpg", "Edited Media/v1.mp4"]  # show order


def test_delete_cut_cascades_membership(gw):
    gw.delete_cut("cut-s")
    assert gw.cut("cut-s") is None
    rows = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM cut_member WHERE cut_id = 'cut-s'").fetchone()
    assert rows["n"] == 0


def test_deleting_export_record_drops_file_from_every_cut(gw):
    # spec/61 §1.4 — the relational win: the lineage row IS the file's
    # identity, so deleting it sweeps the file out of all memberships.
    gw.store.conn.execute(
        "DELETE FROM lineage WHERE export_relpath = 'Edited Media/e1.jpg'")
    assert gw.cut_member_files("cut-s") == []


def test_cut_show_totals_counts_days_and_clip_ms(gw):
    gw.set_cut_members("cut-s", [
        "Edited Media/e1.jpg", "Edited Media/e3a.jpg", "Edited Media/v1.mp4"])
    totals = gw.cut_show_totals("cut-s")
    assert totals.photo_count == 2
    assert totals.video_count == 1
    assert totals.video_ms_total == 30_000
    assert totals.separator_count == 2   # member days 1 + 2
    # (2 photos + 2 separators) × 6 s + 30 s of clip = 54 s
    assert totals.seconds(photo_s=6.0) == 54.0


def test_mark_cut_exported_stamps_status(gw):
    assert gw.cut("cut-s").last_exported_at is None
    gw.mark_cut_exported("cut-s")
    assert gw.cut("cut-s").last_exported_at == FIXED_NOW


# --------------------------------------------------------------------------- #
# Dialog feeds — style vocabulary + draft-pool totals (slice 4)
# --------------------------------------------------------------------------- #


def test_cut_style_options_distinct_non_null(gw):
    # macro (p1, p3) + wildlife (p2); the unclassified video contributes
    # nothing; alphabetical.
    assert gw.cut_style_options() == ["macro", "wildlife"]


def test_pool_show_totals_for_draft_pool(gw):
    totals = gw.pool_show_totals(
        [("+", "exported"), ("-", "short_version")])
    # e2, e3a, e3b photos + the 30 s video; days 1 (e2) + 2 (e3*, v1)
    assert totals.photo_count == 3
    assert totals.video_count == 1
    assert totals.video_ms_total == 30_000
    assert totals.separator_count == 2
    empty = gw.pool_show_totals([("+", "no_such_cut")])
    assert empty.photo_count == 0 and empty.video_count == 0
