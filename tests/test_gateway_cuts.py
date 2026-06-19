"""spec/81 / spec/61 — the DC + Cut gateway facade.

Logic-only (no Qt). Exercises the file-based membership model over a real
``event.db``: the #exported live query (edit-phase lineage through
``visible_item``), DC resolution (set algebra +/−/& left to right, nested-DC
grouping, Style/media filters), Cut CRUD with the name transform enforced at
the gateway, the replace-all membership commit, show ordering, totals, and the
FK cascades that make "delete an export → it falls out of every Cut" automatic.
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
    # The exported universe: p1, p2 once; p3 TWICE (two versions = two file
    # entries, spec/61 §1.2); the video once. p4 has no lineage. One
    # share-phase row proves #exported is edit-phase only.
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/e1.jpg", phase="edit",
                  source_kind="item", source_item_id="p1", exported_at="t1"),
        m.Lineage(export_relpath="Exported Media/e2.jpg", phase="edit",
                  source_kind="item", source_item_id="p2", exported_at="t2"),
        m.Lineage(export_relpath="Exported Media/e3a.jpg", phase="edit",
                  source_kind="item", source_item_id="p3", exported_at="t3"),
        m.Lineage(export_relpath="Exported Media/e3b.jpg", phase="edit",
                  source_kind="item", source_item_id="p3", exported_at="t4"),
        m.Lineage(export_relpath="Exported Media/v1.mp4", phase="edit",
                  source_kind="item", source_item_id="v1", exported_at="t5"),
        m.Lineage(export_relpath="Cuts/old/x.jpg", phase="share",
                  source_kind="item", source_item_id="p1", exported_at="t6"),
    ]
    doc.cuts = [m.Cut(id="cut-s", tag="short_version",
                      created_at=FIXED_NOW, updated_at=FIXED_NOW)]
    doc.cut_members = [m.CutMember(
        cut_id="cut-s", export_relpath="Exported Media/e1.jpg", added_at=FIXED_NOW)]
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
    assert rels == [
        "Exported Media/e1.jpg", "Exported Media/e2.jpg",
        "Exported Media/e3a.jpg", "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    ]


def test_exported_files_respects_hidden_days(gw):
    gw.store.conn.execute("UPDATE trip_day SET hidden = 1 WHERE day_number = 2")
    rels = [ln.export_relpath for ln in gw.exported_files()]
    assert rels == ["Exported Media/e1.jpg", "Exported Media/e2.jpg"]


# --------------------------------------------------------------------------- #
# DC resolution — set algebra (+/−/&), nested-DC grouping, filters
# --------------------------------------------------------------------------- #


def test_resolve_dc_exported_minus_cut(gw):
    # #exported − the frozen short_version cut (a terminal operand).
    rows = gw.resolve_dc([["+", "exported"],
                          ["-", {"kind": "cut", "tag": "short_version"}]])
    assert [ln.export_relpath for ln in rows] == [
        "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    ]


def test_resolve_dc_intersection(gw):
    # A macro-only DC ∩ #exported = the macro photos + every video.
    # Style filters are photo-shaped (spec/58 §2); a Style-filtered DC
    # therefore carries videos through, so the intersection does too.
    macro = gw.create_dc("Macro", expr=[["+", "exported"]], styles=["macro"])
    rows = gw.resolve_dc([["+", "exported"],
                          ["&", {"kind": "dc", "id": macro.id}]])
    assert [ln.export_relpath for ln in rows] == [
        "Exported Media/e1.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    ]


def test_resolve_dc_nested_dc_grouping(gw):
    # Grouping by nesting: a sub-DC stands in for parentheses (spec/81 §2).
    sub = gw.create_dc(
        "Sub", expr=[["+", "exported"],
                     ["-", {"kind": "cut", "tag": "short_version"}]])
    rows = gw.resolve_dc([["+", {"kind": "dc", "id": sub.id}]])
    assert [ln.export_relpath for ln in rows] == [
        "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    ]


def test_resolve_dc_unknown_operand_contributes_nothing(gw):
    # A deleted/unknown operand shrinks gracefully instead of raising.
    rows = gw.resolve_dc([["+", {"kind": "cut", "tag": "short_version"}],
                          ["+", {"kind": "cut", "tag": "no_such_cut"}]])
    assert [ln.export_relpath for ln in rows] == ["Exported Media/e1.jpg"]


def test_resolve_dc_bad_operator_raises(gw):
    with pytest.raises(ValueError):
        gw.resolve_dc([["*", "exported"]])


def test_resolve_dc_style_filter(gw):
    rows = gw.resolve_dc([["+", "exported"]], {"styles": ["macro"]})
    # wildlife e2 drops; both macro versions stay; the video rides the
    # Style filter unconditionally (Style is a photo-shaped bucket — a
    # video would otherwise be silently dropped by an unrelated chip
    # selection, reported by Nelson 2026-06-19).
    assert [ln.export_relpath for ln in rows] == [
        "Exported Media/e1.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    ]


def test_style_filter_does_not_drop_videos(gw):
    """Regression (Nelson 2026-06-19): the New Cut dialog's 'Videos'
    checkbox + any Style chip should still surface videos. The video has
    no classification so an ``IN``-only style filter dropped it
    silently. Videos pass through the Style filter regardless."""
    # Two style chips simultaneously: the macro+wildlife union still
    # excludes nothing on the video side.
    rows = gw.resolve_dc([["+", "exported"]],
                         {"styles": ["macro", "wildlife"]})
    assert "Exported Media/v1.mp4" in {ln.export_relpath for ln in rows}
    # A style the user invented that matches nothing — videos still ride.
    rows = gw.resolve_dc([["+", "exported"]], {"styles": ["unknown_style"]})
    assert [ln.export_relpath for ln in rows] == ["Exported Media/v1.mp4"]


def test_resolve_dc_media_type_filter(gw):
    assert [ln.export_relpath for ln in
            gw.resolve_dc([["+", "exported"]], {"media_type": "video"})] == [
        "Exported Media/v1.mp4"]
    photos = gw.resolve_dc([["+", "exported"]], {"media_type": "photo"})
    assert all(not ln.export_relpath.endswith(".mp4") for ln in photos)
    assert len(photos) == 4


# --------------------------------------------------------------------------- #
# DC CRUD + cycle guard + operand inventory
# --------------------------------------------------------------------------- #


def test_create_dc_slugifies_and_persists(gw):
    dc = gw.create_dc("Pássaros do Brasil!",
                      expr=[["+", "exported"]], styles=["macro"],
                      media_type="photo")
    assert dc.tag == "passaros_do_brasil"
    stored = gw.dc_by_tag("passaros_do_brasil")
    assert stored is not None and stored.id == dc.id
    assert gw.dc_expr(stored) == [["+", "exported"]]
    assert gw.dc_filters(stored) == {"styles": ["macro"], "media_type": "photo"}


def test_dc_and_cut_tag_namespaces_are_separate(gw):
    # SEPARATE namespaces (Nelson 2026-06-16): a DC may take a name a Cut owns.
    gw.create_dc("short version", expr=[["+", "exported"]])  # cut-s owns this tag
    assert gw.dc_by_tag("short_version") is not None
    assert gw.cut_by_tag("short_version") is not None        # the cut still there
    # but a DC tag collides only within the DC namespace
    with pytest.raises(ValueError, match="taken"):
        gw.create_dc("Short Version")


def test_update_dc_rejects_self_cycle(gw):
    dc = gw.create_dc("Loop", expr=[["+", "exported"]])
    with pytest.raises(ValueError, match="cycle"):
        gw.update_dc(dc.id, expr=[["+", {"kind": "dc", "id": dc.id}]])


def test_update_dc_rejects_transitive_cycle(gw):
    a = gw.create_dc("A", expr=[["+", "exported"]])
    b = gw.create_dc("B", expr=[["+", {"kind": "dc", "id": a.id}]])
    # Closing the loop A→B→A must be rejected.
    with pytest.raises(ValueError, match="cycle"):
        gw.update_dc(a.id, expr=[["+", {"kind": "dc", "id": b.id}]])


def test_delete_dc_leaves_pinned_cut_and_members(gw):
    # source_dc_id ON DELETE SET NULL — the freeze invariant (spec/81 §5).
    dc = gw.create_dc("Src", expr=[["+", "exported"]])
    cut = gw.create_cut("From Src", source_dc_id=dc.id,
                        expr_snapshot=[["+", "exported"]])
    gw.set_cut_members(cut.id, ["Exported Media/e1.jpg"])
    gw.delete_dc(dc.id)
    refreshed = gw.cut(cut.id)
    assert refreshed is not None and refreshed.source_dc_id is None
    assert [cm.export_relpath for cm in
            gw.store.query_by(m.CutMember, cut_id=cut.id)] == [
        "Exported Media/e1.jpg"]


def test_dc_operand_inventory_lists_base_dcs_and_cuts(gw):
    gw.create_dc("Macro", expr=[["+", "exported"]])
    inv = gw.dc_operand_inventory()
    kinds = [(e["kind"], e["tag"]) for e in inv]
    assert kinds[0] == ("base", "exported")
    assert ("dc", "macro") in kinds
    assert ("cut", "short_version") in kinds


# --------------------------------------------------------------------------- #
# Cut CRUD — the gateway is the name-transform enforcement point
# --------------------------------------------------------------------------- #


def test_create_cut_slugifies_and_freezes_snapshot(gw):
    dc = gw.create_dc("Birds", expr=[["+", "exported"]], styles=["macro"])
    cut = gw.create_cut(
        "Pássaros do Brasil!", target_s=600, max_s=720,
        source_dc_id=dc.id,
        expr_snapshot=[["+", "exported"],
                       ["-", {"kind": "cut", "tag": "short_version"}]],
        music_category="happy")
    assert cut.tag == "passaros_do_brasil"
    stored = gw.cut_by_tag("passaros_do_brasil")
    assert stored is not None and stored.id == cut.id
    assert stored.source_dc_id == dc.id
    # The Cut carries the FROZEN formula; style/media filters live on the DC.
    assert gw.cut_expr_snapshot(stored) == [
        ["+", "exported"], ["-", {"kind": "cut", "tag": "short_version"}]]
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
    with pytest.raises(ValueError):
        gw.update_cut_settings("cut-s", pool_expr_json="[]")


def test_cut_overlay_fields_default_off_and_settable(gw):
    assert gw.cut_overlay_fields(gw.cut("cut-s")) == []
    cut = gw.create_cut("Portfolio", overlay_fields=["when", "where"],
                        overlay_mode="embedded")
    assert gw.cut_overlay_fields(gw.cut(cut.id)) == ["when", "where"]
    assert gw.cut(cut.id).overlay_mode == "embedded"
    assert gw.cut(cut.id).separators is True


def test_card_style_lives_in_extras(gw):
    cut = gw.create_cut("Colorida", card_style="multi")
    assert gw.cut_card_style(gw.cut(cut.id)) == "multi"
    assert gw.cut_card_style(gw.cut("cut-s")) == "black"
    gw.update_cut_settings(cut.id, card_style="single", target_s=60)
    got = gw.cut(cut.id)
    assert gw.cut_card_style(got) == "single" and got.target_s == 60


# ----- membership commit + cascades + totals -----


def test_set_cut_members_replaces_and_dedupes(gw):
    n = gw.set_cut_members("cut-s", [
        "Exported Media/e2.jpg", "Exported Media/v1.mp4", "Exported Media/e2.jpg"])
    assert n == 2
    rels = [ln.export_relpath for ln in gw.cut_member_files("cut-s")]
    assert rels == ["Exported Media/e2.jpg", "Exported Media/v1.mp4"]


def test_delete_cut_cascades_membership(gw):
    gw.delete_cut("cut-s")
    assert gw.cut("cut-s") is None
    rows = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM cut_member WHERE cut_id = 'cut-s'").fetchone()
    assert rows["n"] == 0


def test_deleting_export_record_drops_file_from_every_cut(gw):
    gw.store.conn.execute(
        "DELETE FROM lineage WHERE export_relpath = 'Exported Media/e1.jpg'")
    assert gw.cut_member_files("cut-s") == []


def test_cut_show_totals_counts_days_and_clip_ms(gw):
    gw.set_cut_members("cut-s", [
        "Exported Media/e1.jpg", "Exported Media/e3a.jpg", "Exported Media/v1.mp4"])
    totals = gw.cut_show_totals("cut-s")
    assert totals.photo_count == 2
    assert totals.video_count == 1
    assert totals.video_ms_total == 30_000
    assert totals.separator_count == 2
    assert totals.seconds(photo_s=6.0) == 54.0


def test_mark_cut_exported_stamps_status(gw):
    assert gw.cut("cut-s").last_exported_at is None
    gw.mark_cut_exported("cut-s")
    assert gw.cut("cut-s").last_exported_at == FIXED_NOW


# ----- dialog feeds -----


def test_cut_style_options_distinct_non_null(gw):
    assert gw.cut_style_options() == ["macro", "wildlife"]


def test_dc_show_totals_for_draft_dc(gw):
    totals = gw.dc_show_totals(
        [["+", "exported"], ["-", {"kind": "cut", "tag": "short_version"}]])
    assert totals.photo_count == 3
    assert totals.video_count == 1
    assert totals.video_ms_total == 30_000
    assert totals.separator_count == 2
    empty = gw.dc_show_totals([["+", {"kind": "cut", "tag": "no_such_cut"}]])
    assert empty.photo_count == 0 and empty.video_count == 0


def test_dc_probe_counts_draft_resolution(gw):
    assert gw.dc_probe([["+", "exported"]]) == 5
    assert gw.dc_probe(
        [["+", "exported"], ["-", {"kind": "cut", "tag": "short_version"}]]) == 4


def test_frame_provenance_joins_lineage_to_item_and_day(gw):
    """Spec/81 §3.1 — the export-time overlay resolver. The gateway
    joins the lineage row to its source item (and the trip day) so the
    embedded export can stamp *where* IPTC + the formatter has the
    technical fields for *when* / *how¹* / *how²*."""
    prov = gw.frame_provenance("Exported Media/e1.jpg")     # p1 (day 1, macro)
    assert prov.when == "2026-04-01T08:00:00"
    assert prov.camera == "G9"                              # item.camera_id
    # The fixture's day-1 trip_day has no location; missing fields stay None.
    assert prov.country is None
    # Unknown relpath / missing item → graceful empty FrameProvenance.
    empty = gw.frame_provenance("Exported Media/nope.jpg")
    assert empty.when is None and empty.camera is None
