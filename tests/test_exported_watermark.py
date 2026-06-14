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


def test_exported_item_ids_covers_both_writer_shapes(tmp_path):
    eg = _make_eg(tmp_path)
    try:
        # In-app export shape (recipe snapshot present).
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))
        # Return-scan / backfill shape (recipe NULL).
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p2-edit.jpg", phase="edit",
            source_kind="item", source_item_id="p2"))
        assert eg.exported_item_ids() == {"p1", "p2"}
    finally:
        eg.store.close()


def test_exported_item_ids_distinct_and_edit_only(tmp_path):
    eg = _make_eg(tmp_path)
    try:
        # Two exports of the same photo → one id.
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1"))
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/Dia 1/p1 (2).jpg", phase="edit",
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
