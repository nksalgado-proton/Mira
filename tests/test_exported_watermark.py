"""spec/59 §8 — the Exported watermark.

Pins: the gateway driver (edit-phase lineage rows, NOT the
``edit_exported`` freshness flag; bracket/share rows ignored); the
``day_grid_cells`` Edit projection stamping PHOTO item cells only;
``CullCell.exported`` defaulting False (Pick untouched); the
DayGridCell overlay showing for exported photos and never for
videos/clusters; the MediaCanvas host API; the widget never taking the
mouse; the setting shipping ON.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

from mira.gateway.event_gateway import EventGateway
from mira.picked.model import (
    CullBucket,
    CullCell,
    CullItem,
    PickDay,
    day_grid_cells,
)
from mira.picked.status import BucketStatus, CellColor
from mira.settings.model import Settings
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.base.day_grid_cell import CellRenderData, DayGridCell
from mira.ui.base.exported_watermark import ExportedWatermark


# --------------------------------------------------------------------------- #
# The gateway driver — edit-phase lineage, all writer shapes, nothing else
# --------------------------------------------------------------------------- #


def _make_eg(tmp_path) -> EventGateway:
    store = EventStore.create(tmp_path / "event.db", event_id="evt-wm")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-wm", name="WM", created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="C1"))
    store.upsert(m.TripDay(day_number=1, date="2026-04-01"))
    for iid in ("p1", "p2", "p3"):
        store.upsert(m.Item(
            id=iid, kind="photo", origin_relpath=f"d/{iid}.jpg",
            sha256=iid, byte_size=2, materialized_at="t",
            materialized_phase="ingest", camera_id="C1",
            capture_time_raw="2026-04-01T08:00:00",
            capture_time_corrected="2026-04-01T08:00:00",
            created_at="t", day_number=1, provenance="captured",
        ))
    return EventGateway(store, event_root=tmp_path, now=lambda: "t")


def test_exported_item_ids_empty_without_lineage(tmp_path):
    eg = _make_eg(tmp_path)
    try:
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def test_exported_item_ids_counts_only_exported_media_rows(tmp_path):
    """spec/66 §1.2 — ``exported_item_ids()`` returns the SHIPPED set
    (rows under ``Exported Media/``). Third-party returns sitting in
    ``Edited Media/`` are mere edit candidates and do NOT count until
    the Export run hardlinks them into ``Exported Media/``."""
    eg = _make_eg(tmp_path)
    try:
        # Shipped (Mira-rendered, in-app export) → counts.
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))
        # Third-party return inbox (LRC/Helicon) → does NOT count.
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p2-edit.jpg", phase="edit",
            source_kind="item", source_item_id="p2"))
        # Shipped via hardlink-from-return → counts.
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p3.jpg", phase="edit",
            source_kind="item", source_item_id="p3",
            recipe_json="{}", exported_at="t"))
        assert eg.exported_item_ids() == {"p1", "p3"}
    finally:
        eg.store.close()


def test_exported_item_ids_distinct_and_edit_only(tmp_path):
    eg = _make_eg(tmp_path)
    try:
        # Two exports of the same photo → one id.
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1"))
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1 (2).jpg", phase="edit",
            source_kind="item", source_item_id="p1"))
        # A share-phase row is NOT an Edit export.
        eg.record_lineage(m.Lineage(
            export_relpath="cut/p3.jpg", phase="share",
            source_kind="item", source_item_id="p3"))
        assert eg.exported_item_ids() == {"p1"}
    finally:
        eg.store.close()


def test_edit_exported_flag_is_not_the_driver(tmp_path):
    """The freshness flag must not feed the watermark (spec/59 §8)."""
    eg = _make_eg(tmp_path)
    try:
        eg.set_edit_exported("p1", True)
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def _make_eg_picked(tmp_path) -> EventGateway:
    """:func:`_make_eg` plus a Pick-kept phase_state row per item — the
    rescan needs the picked pool to do its stem match."""
    eg = _make_eg(tmp_path)
    for iid in ("p1", "p2", "p3"):
        eg.store.upsert(
            m.PhaseState(item_id=iid, phase="pick", state="picked"))
    return eg


# --------------------------------------------------------------------------- #
# rescan_exported_media — the self-heal for lost-commit Exports
# --------------------------------------------------------------------------- #


def test_rescan_backfills_orphan_files(tmp_path):
    """A JPEG sitting under ``Exported Media/`` with no lineage row is
    backfilled: the rescan matches it back to its source ``Item`` by
    source-filename stem, writes the row, and flips
    ``Adjustment.edit_exported``.

    Mirrors the spec/57 §3 returns scan: the next Edit / Share / Export
    entry catches files the Export run dropped on disk but forgot to
    commit a row for. The Inseto na Varanda silent fail (2026-06-15)
    would have left orphans here — this test guards the recovery path
    so a future regression doesn't reintroduce a lost-row gap."""
    eg = _make_eg_picked(tmp_path)
    try:
        # Drop a JPEG matching ``p1``'s origin stem (= ``p1`` for
        # ``origin_relpath="d/p1.jpg"``) into the ship tree. No lineage
        # row yet — the simulated lost commit.
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "p1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        # Pre-rescan: the watermark / Share view is empty.
        assert eg.exported_item_ids() == set()

        n = eg.rescan_exported_media()
        assert n == 1
        # Post-rescan: the source item now reads as shipped.
        assert eg.exported_item_ids() == {"p1"}
        # ``Adjustment.edit_exported`` flipped — the freshness chip lights.
        adj = eg.adjustment("p1")
        assert adj is not None and adj.edit_exported is True
        # The lineage row carries the correct relpath under Exported Media/.
        files = eg.exported_files()
        assert [f.export_relpath for f in files] == [
            "Exported Media/Dia 1/p1.jpg"]
    finally:
        eg.store.close()


def test_rescan_is_idempotent(tmp_path):
    """Running the rescan twice writes the row once — the second call
    is a no-op. Lets every Edit / Share / Export entry call it without
    risk of duplicating lineage."""
    eg = _make_eg_picked(tmp_path)
    try:
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "p1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        assert eg.rescan_exported_media() == 1
        # Second pass — the row exists, nothing new.
        assert eg.rescan_exported_media() == 0
        # And the ship pool stays a single row, no duplication.
        assert len(eg.exported_files()) == 1
    finally:
        eg.store.close()


def test_rescan_skips_files_with_existing_rows(tmp_path):
    """A file that already has a lineage row pointing at it is left
    alone (the row keeps its original recipe / exported_at / etc.)."""
    eg = _make_eg_picked(tmp_path)
    try:
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "p1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json='{"look": "natural"}',
            exported_at="2026-06-01T00:00:00"))
        assert eg.rescan_exported_media() == 0
        # The original row's recipe + timestamp survive.
        files = eg.exported_files()
        assert len(files) == 1
        assert files[0].recipe_json == '{"look": "natural"}'
        assert files[0].exported_at == "2026-06-01T00:00:00"
    finally:
        eg.store.close()


def test_rescan_skips_ambiguous_stems(tmp_path):
    """Two Pick-kept photos with the same filename stem (``DSC0001.cr3``
    on day 1 AND day 2) make the stem map ambiguous — the rescan
    refuses to guess and leaves both unresolved files alone."""
    eg = _make_eg(tmp_path)
    # _make_eg gives day 1 only — the second item lives on day 2.
    eg.store.upsert(m.TripDay(day_number=2, date="2026-04-02"))
    # Two items share the same source filename stem ``dup`` — different
    # paths, but a stem-keyed map can only point at one of them.
    eg.store.upsert(m.Item(
        id="d1", kind="photo", origin_relpath="day1/dup.jpg",
        sha256="d1", byte_size=2, materialized_at="t",
        materialized_phase="ingest", camera_id="C1",
        capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00",
        created_at="t", day_number=1, provenance="captured"))
    eg.store.upsert(m.Item(
        id="d2", kind="photo", origin_relpath="day2/dup.jpg",
        sha256="d2", byte_size=2, materialized_at="t",
        materialized_phase="ingest", camera_id="C1",
        capture_time_raw="2026-04-02T08:00:00",
        capture_time_corrected="2026-04-02T08:00:00",
        created_at="t", day_number=2, provenance="captured"))
    eg.store.upsert(m.PhaseState(item_id="d1", phase="pick", state="picked"))
    eg.store.upsert(m.PhaseState(item_id="d2", phase="pick", state="picked"))
    try:
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "dup.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        # Ambiguous → skip. The user re-runs Export from the surface
        # to disambiguate; the rescan does not guess between days.
        assert eg.rescan_exported_media() == 0
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def test_rescan_no_op_when_no_exported_media_folder(tmp_path):
    """Nothing on disk → nothing to do. The rescan never creates the
    folder; it only observes."""
    eg = _make_eg_picked(tmp_path)
    try:
        assert not (tmp_path / "Exported Media").exists()
        assert eg.rescan_exported_media() == 0
        # No phantom row writes.
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def test_edit_candidate_helpers_for_third_party_returns(tmp_path):
    """spec/66 §1.2 — ``edit_candidate_item_ids`` returns items with a
    third-party return sitting in ``Edited Media/`` (and not yet shipped);
    ``edit_candidate_relpath`` returns the newest return relpath for an
    item (the Export hardlink path consumes both)."""
    eg = _make_eg(tmp_path)
    try:
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p1-edit.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            exported_at="2026-06-14T08:00:00"))
        # A newer return for the same item — the helper takes the latest.
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p1-edit-v2.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            exported_at="2026-06-14T09:00:00"))
        # A shipped (Exported Media/) row should NOT register as a
        # candidate — it's already past the inbox stage.
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p2.jpg", phase="edit",
            source_kind="item", source_item_id="p2",
            exported_at="2026-06-14T10:00:00"))
        assert eg.edit_candidate_item_ids() == {"p1"}
        assert eg.edit_candidate_relpath("p1") == (
            "Edited Media/LRC/p1-edit-v2.jpg")
        assert eg.edit_candidate_relpath("p2") is None
        assert eg.edit_candidate_relpath("never-imported") is None
    finally:
        eg.store.close()


def test_exported_files_excludes_edit_candidates(tmp_path):
    """spec/66 §1.2 + spec/61 §1.1 — the ``#exported`` Cut universe
    (returned by ``exported_files``) shows only shipped rows. A
    third-party return that hasn't been promoted is invisible to Cuts."""
    eg = _make_eg(tmp_path)
    try:
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            exported_at="2026-06-14T08:00:00"))
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p2-edit.jpg", phase="edit",
            source_kind="item", source_item_id="p2",
            exported_at="2026-06-14T08:00:00"))
        relpaths = [ln.export_relpath for ln in eg.exported_files()]
        assert "Exported Media/Dia 1/p1.jpg" in relpaths
        assert "Edited Media/LRC/p2-edit.jpg" not in relpaths
    finally:
        eg.store.close()


# --------------------------------------------------------------------------- #
# The model projection — photos stamped, videos/Pick untouched
# --------------------------------------------------------------------------- #


def test_cullcell_exported_defaults_false():
    cell = CullCell(end_time="", color=CellColor.KEPT, item_id="x")
    assert cell.exported is False


def _fake_day():
    items = (
        CullItem(item_id="p1", path=Path("p1.jpg"), kind="photo",
                 capture_time_corrected="2026-04-01T08:00:00"),
        CullItem(item_id="v1", path=Path("v1.mp4"), kind="video",
                 capture_time_corrected="2026-04-01T08:01:00",
                 duration_ms=1000),
    )
    status = BucketStatus(
        total=2, kept=0, candidate=0, discarded=0, untouched=2,
        reviewed=False, browsed=False, badge="untouched")
    bucket = CullBucket(
        bucket_key="1|individual|k", kind="individual", title="b",
        items=items, status=status)
    return [PickDay(day_number=1, label="Dia 1", buckets=(bucket,),
                    status=status)]


def _fake_gateway():
    return SimpleNamespace(
        phase_states=lambda _phase: {},
        items_visited_for_day=lambda _d, _p: set(),
        video_segments=lambda _vid: [],
        video_snapshots=lambda _vid: [],
    )


def test_day_grid_cells_stamps_exported_photos_only():
    cells = day_grid_cells(
        _fake_gateway(), 1, phase="edit", days=_fake_day(),
        default_state="picked", exported_ids={"p1", "v1"},
    )
    by_id = {c.item_id: c for c in cells}
    assert by_id["p1"].exported is True
    # Videos never wear the watermark, even when their id has lineage.
    assert by_id["v1"].exported is False


def test_day_grid_cells_default_stamps_nothing():
    """No ``exported_ids`` (every Pick caller) → all False."""
    cells = day_grid_cells(
        _fake_gateway(), 1, phase="edit", days=_fake_day(),
        default_state="picked",
    )
    assert all(c.exported is False for c in cells)


# --------------------------------------------------------------------------- #
# The widgets
# --------------------------------------------------------------------------- #


def _cell(kind: str, exported: bool) -> CullCell:
    return CullCell(
        end_time="", color=CellColor.KEPT, item_id="i1",
        item_kind=kind, exported=exported)


def test_day_grid_cell_watermark_visible_for_exported_photo(qapp):
    w = DayGridCell(CellRenderData(cell=_cell("photo", True)))
    assert w._watermark.isVisibleTo(w)
    assert "exported version" in w.toolTip().lower()


def test_day_grid_cell_watermark_hidden_otherwise(qapp):
    assert not DayGridCell(
        CellRenderData(cell=_cell("photo", False)))._watermark.isVisibleTo(
        DayGridCell(CellRenderData(cell=_cell("photo", False))))
    # Videos never (display rule, belt-and-braces over the projection).
    w = DayGridCell(CellRenderData(cell=_cell("video", True)))
    assert not w._watermark.isVisibleTo(w)


def test_day_grid_cell_set_data_flips_watermark(qapp):
    w = DayGridCell(CellRenderData(cell=_cell("photo", False)))
    assert not w._watermark.isVisibleTo(w)
    w.set_data(CellRenderData(cell=_cell("photo", True)))
    assert w._watermark.isVisibleTo(w)


def test_watermark_widget_never_takes_the_mouse(qapp):
    wm = ExportedWatermark()
    assert wm.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert not wm.isVisible()


def test_media_canvas_watermark_host_api(qapp):
    from mira.ui.media.media_canvas import MediaCanvas
    canvas = MediaCanvas()
    # No image loaded → stays hidden even when asked.
    canvas.set_exported_watermark(True)
    assert not canvas._exported_watermark.isVisibleTo(canvas)
    # Deterministic pre-show geometry so the displayed-pixmap path runs
    # (it early-outs on a zero-sized label).
    canvas.resize(400, 300)
    canvas._photo_label.resize(300, 200)
    # Image present → shows; off → hides.
    pm = QPixmap(40, 40)
    pm.fill(Qt.GlobalColor.darkGray)
    canvas.set_preview_pixmap(pm)
    canvas.set_exported_watermark(True)
    assert canvas._exported_watermark.isVisibleTo(canvas)
    canvas.set_exported_watermark(False)
    assert not canvas._exported_watermark.isVisibleTo(canvas)


# --------------------------------------------------------------------------- #
# The single-export commit order contract moves to the Export surface
# (spec/66 Slice 5) — the equivalent pin will live in tests for that
# surface once it lands. The old EditPage-side contract is gone with
# Slice 4 (spec/66 §1.1 — Edit no longer triggers export).
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# The setting
# --------------------------------------------------------------------------- #


def test_watermark_setting_ships_on():
    assert Settings().show_exported_watermark is True
