"""Tests for the M3 Day Grid widgets (spec/32 §2.6 + §7).

Exercises:
  * :class:`mira.ui.base.cluster_icons.cluster_icon` — SVG → QPixmap
    rasterisation, per-size cache, count badge.
  * :class:`mira.ui.base.day_grid_cell.DayGridCell` — status QSS
    property, hit-zone math (border vs centre), signal emission on click.
  * :class:`mira.ui.base.day_grid_view.DayGridView` — cell mounting,
    size slider, header text, ``set_cells``/``update_cell`` round-trip,
    Esc → back_requested.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QPushButton

from mira.picked.model import CullCell, CullCluster, CullItem
from mira.picked.status import CellColor
from mira.ui.base import cluster_icons
from mira.ui.base.cluster_icons import cluster_icon
from mira.ui.base.day_grid_cell import (
    BORDER_RATIO,
    MAX_BORDER_PX,
    MIN_BORDER_PX,
    CellRenderData,
    DayGridCell,
)
from mira.ui.base.day_grid_view import (
    DEFAULT_CELL_SIZE,
    MAX_CELL_SIZE,
    MIN_CELL_SIZE,
    DayGridView,
)


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def _photo_cell(color: CellColor = CellColor.KEPT, item_id: str = "p1") -> CullCell:
    return CullCell(
        end_time="2026-04-01T08:00:00",
        color=color, item_id=item_id, item_kind="photo",
    )


def _video_cell(color: CellColor = CellColor.KEPT, item_id: str = "v1") -> CullCell:
    return CullCell(
        end_time="2026-04-01T08:00:00",
        color=color, item_id=item_id, item_kind="video",
    )


def _cluster_cell(
    kind: str = "burst", color: CellColor = CellColor.MIXED,
    count: int = 5,
) -> CullCell:
    members = tuple(
        CullItem(item_id=f"m{i}", path=Path("."), kind="photo")
        for i in range(count)
    )
    cluster = CullCluster(
        bucket_key=f"d1|{kind}|b1", kind=kind,
        title=kind, members=members, color=color,
    )
    return CullCell(
        end_time="2026-04-01T09:00:00",
        color=color, cluster=cluster,
    )


def _color_pixmap(side: int = 100, color: str = "#3478f6") -> QPixmap:
    pm = QPixmap(side, side)
    pm.fill(Qt.GlobalColor.transparent)  # transparent so the inner bg shows
    # Repaint as solid so it renders distinctly from the placeholder "…".
    from PyQt6.QtGui import QColor, QPainter
    painter = QPainter(pm)
    painter.fillRect(pm.rect(), QColor(color))
    painter.end()
    return pm


def _emit_release(widget, x: int, y: int) -> None:
    """Synthesise a left-click release at ``(x, y)`` on ``widget`` —
    bypasses the press handler (which only accepts the event) so the test
    can target the release-zone logic directly."""
    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseReleaseEvent(ev)


# --------------------------------------------------------------------------- #
# cluster_icons
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clear_icon_caches():
    """Wipe the SVG renderer + pixmap cache between tests so cache
    interactions don't leak."""
    cluster_icons.clear_caches()
    yield
    cluster_icons.clear_caches()


def test_cluster_icon_burst_renders_at_default_size(qapp):
    pm = cluster_icon("burst", 140, count=5)
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()
    assert pm.width() == 140 and pm.height() == 140


def test_cluster_icon_supports_all_three_kinds(qapp):
    for kind in ("burst", "focus_bracket", "exposure_bracket"):
        pm = cluster_icon(kind, 80, count=3)
        assert not pm.isNull(), kind
        assert pm.size().width() == 80


def test_cluster_icon_unknown_kind_returns_empty_transparent(qapp):
    """Defensive — unknown kinds yield a transparent pixmap of the requested
    size (the cell will show its border + placeholder, never crash)."""
    pm = cluster_icon("not-a-cluster", 100, count=1)
    assert isinstance(pm, QPixmap)
    assert pm.width() == 100


def test_cluster_icon_cache_returns_same_object(qapp):
    """The pixmap cache key is (kind, size, count) — a hit returns the
    same QPixmap so the slider doesn't re-rasterise mid-drag."""
    pm1 = cluster_icon("burst", 140, count=5)
    pm2 = cluster_icon("burst", 140, count=5)
    assert pm1 is pm2


def test_cluster_icon_different_size_is_different_pixmap(qapp):
    pm1 = cluster_icon("burst", 100, count=5)
    pm2 = cluster_icon("burst", 200, count=5)
    assert pm1 is not pm2
    assert pm1.width() != pm2.width()


def test_cluster_icon_clamps_tiny_size(qapp):
    """Below the 16 px floor the icon still rasterises (defensive)."""
    pm = cluster_icon("burst", 4, count=1)
    assert not pm.isNull()
    assert pm.width() >= 16


# --------------------------------------------------------------------------- #
# DayGridCell — status property + click zones
# --------------------------------------------------------------------------- #


def test_cell_qss_status_property_for_each_color(qapp):
    """Every CellColor maps to the cell's ``status`` dynamic property so
    the QSS rules fire."""
    for color in CellColor:
        cell = DayGridCell(CellRenderData(_photo_cell(color)), size=140)
        try:
            assert cell.property("status") == color.value
        finally:
            cell.deleteLater()


def test_cell_set_data_updates_status_in_place(qapp):
    cell = DayGridCell(CellRenderData(_photo_cell(CellColor.UNTOUCHED)), size=140)
    try:
        cell.set_data(CellRenderData(_photo_cell(CellColor.KEPT)))
        assert cell.property("status") == "picked"
    finally:
        cell.deleteLater()


def test_cell_hit_zone_border_corners_and_centre(qapp):
    cell = DayGridCell(CellRenderData(_photo_cell()), size=140)
    try:
        # Width/height are fixed to 140 — border is ratio×size, ≥ MIN_BORDER_PX.
        b = max(MIN_BORDER_PX, int(140 * BORDER_RATIO))
        # Far inside → centre.
        assert cell.hit_zone(70, 70) == "center"
        # Right at the edge → border.
        assert cell.hit_zone(2, 70) == "border"
        assert cell.hit_zone(70, 2) == "border"
        assert cell.hit_zone(140 - 2, 70) == "border"
        assert cell.hit_zone(70, 140 - 2) == "border"
        # Just inside the inner ring (≥ b) → centre.
        assert cell.hit_zone(b + 1, 70) == "center"
    finally:
        cell.deleteLater()


def test_cell_hit_zone_outside_widget(qapp):
    cell = DayGridCell(CellRenderData(_photo_cell()), size=140)
    try:
        assert cell.hit_zone(-1, 10) == "outside"
        assert cell.hit_zone(10, 200) == "outside"
    finally:
        cell.deleteLater()


def test_cell_emits_border_click_on_edge_release(qapp):
    cell = DayGridCell(CellRenderData(_photo_cell()), size=140)
    fired_border = []
    fired_center = []
    cell.border_clicked.connect(lambda: fired_border.append(True))
    cell.center_clicked.connect(lambda: fired_center.append(True))
    try:
        _emit_release(cell, 2, 70)
        assert fired_border == [True]
        assert fired_center == []
    finally:
        cell.deleteLater()


def test_cell_emits_center_click_on_interior_release(qapp):
    cell = DayGridCell(CellRenderData(_photo_cell()), size=140)
    fired_border = []
    fired_center = []
    cell.border_clicked.connect(lambda: fired_border.append(True))
    cell.center_clicked.connect(lambda: fired_center.append(True))
    try:
        _emit_release(cell, 70, 70)
        assert fired_center == [True]
        assert fired_border == []
    finally:
        cell.deleteLater()


def test_cell_does_not_emit_on_release_outside_widget(qapp):
    cell = DayGridCell(CellRenderData(_photo_cell()), size=140)
    fired = []
    cell.border_clicked.connect(lambda: fired.append("b"))
    cell.center_clicked.connect(lambda: fired.append("c"))
    try:
        _emit_release(cell, -10, 70)
        assert fired == []
    finally:
        cell.deleteLater()


def test_cell_video_gets_play_overlay(qapp):
    """Video item cells get the ▶ overlay; photo cells do not."""
    photo_cell = DayGridCell(CellRenderData(_photo_cell()), size=140)
    video_cell = DayGridCell(CellRenderData(_video_cell()), size=140)
    try:
        assert photo_cell._play is None
        assert video_cell._play is not None
    finally:
        photo_cell.deleteLater()
        video_cell.deleteLater()


def test_cell_visited_tick_hidden_when_not_visited(qapp):
    """spec/32 §2.10 — a fresh cell has the tick widget but it's not visible."""
    cell = DayGridCell(CellRenderData(_photo_cell()), size=140)
    try:
        assert cell._tick is not None
        assert cell._tick.isVisible() is False
    finally:
        cell.deleteLater()


def test_cell_visited_tick_shown_when_visited(qapp):
    """spec/32 §2.10 — visited=True at construction shows the tick badge."""
    visited_cell = CullCell(
        end_time="2026-04-01T08:00:00",
        color=CellColor.KEPT, item_id="p1", item_kind="photo",
        visited=True,
    )
    cell = DayGridCell(CellRenderData(visited_cell), size=140)
    try:
        cell.show()
        assert cell._tick.isVisible() is True
    finally:
        cell.deleteLater()


def test_cell_visited_tick_toggles_on_set_data(qapp):
    """set_data() flipping visited must show/hide the tick in place — no
    widget rebuild, no flicker."""
    cell = DayGridCell(CellRenderData(_photo_cell()), size=140)
    try:
        cell.show()
        assert cell._tick.isVisible() is False
        visited_cell = CullCell(
            end_time="2026-04-01T08:00:00",
            color=CellColor.KEPT, item_id="p1", item_kind="photo",
            visited=True,
        )
        cell.set_data(CellRenderData(visited_cell))
        assert cell._tick.isVisible() is True
        # Flip back off.
        cell.set_data(CellRenderData(_photo_cell()))
        assert cell._tick.isVisible() is False
    finally:
        cell.deleteLater()


def test_cell_visited_tick_shown_on_cluster_cell(qapp):
    """A visited cluster cell carries the tick just like an item cell."""
    visited_cluster = _cluster_cell()
    # _cluster_cell() builds visited=False; replace.
    visited_cluster = CullCell(
        end_time=visited_cluster.end_time,
        color=visited_cluster.color, cluster=visited_cluster.cluster,
        visited=True,
    )
    cell = DayGridCell(CellRenderData(visited_cluster), size=140)
    try:
        cell.show()
        assert cell._tick.isVisible() is True
    finally:
        cell.deleteLater()


def test_cell_visited_tick_scales_with_set_size(qapp):
    """spec/32 §7.4 + spec/69 — tick badge scales proportionally with
    the cell-size slider. Post-spec/69 the glyph is a line-icon SVG
    pixmap (not a font character), so the pixmap dimensions are what
    must grow as the cell grows. The pill stylesheet's border-radius
    still tracks the cell side."""
    visited_cell = CullCell(
        end_time="2026-04-01T08:00:00",
        color=CellColor.KEPT, item_id="p1", item_kind="photo",
        visited=True,
    )
    cell = DayGridCell(CellRenderData(visited_cell), size=80)
    try:
        small_qss = cell._tick.styleSheet()
        small_glyph = cell._tick.pixmap().width()
        cell.set_size(280)
        large_qss = cell._tick.styleSheet()
        large_glyph = cell._tick.pixmap().width()
        assert small_qss != large_qss            # border-radius changes
        assert large_glyph > small_glyph         # glyph grows with cell
        assert large_glyph >= 24                 # legible at the top end
    finally:
        cell.deleteLater()


def test_cell_resize_updates_size_and_border(qapp):
    cell = DayGridCell(CellRenderData(_photo_cell()), size=140)
    try:
        cell.set_size(240)
        assert cell.width() == 240 and cell.height() == 240
        # Border thickness grows proportionally (clamped between MIN and MAX).
        expected = max(MIN_BORDER_PX, min(MAX_BORDER_PX, int(240 * BORDER_RATIO)))
        assert cell._border_px() == expected
    finally:
        cell.deleteLater()


def test_cell_thumbnail_replacement_does_not_change_status(qapp):
    cell = DayGridCell(
        CellRenderData(_photo_cell(CellColor.COMPARE), thumbnail=None),
        size=140,
    )
    try:
        cell.set_thumbnail(_color_pixmap())
        assert cell.property("status") == "compare"
    finally:
        cell.deleteLater()


def test_cluster_cell_pixmap_is_cluster_icon(qapp):
    """A cluster cell's inner pixmap comes from the cluster icon loader,
    not the (None) thumbnail — verify it renders something non-empty."""
    cell = DayGridCell(CellRenderData(_cluster_cell()), size=160)
    try:
        pm = cell._inner.pixmap()
        assert pm is not None and not pm.isNull()
    finally:
        cell.deleteLater()


# --------------------------------------------------------------------------- #
# DayGridView
# --------------------------------------------------------------------------- #


def test_view_mounts_cells(qapp):
    view = DayGridView(cell_size=120)
    try:
        view.set_cells([
            CellRenderData(_photo_cell(CellColor.KEPT, "a")),
            CellRenderData(_video_cell(CellColor.DISCARDED, "b")),
            CellRenderData(_cluster_cell(count=4)),
        ])
        assert view.cell_count() == 3
        assert isinstance(view.cell_at(0), DayGridCell)
    finally:
        view.deleteLater()


def test_view_set_cells_clears_previous(qapp):
    view = DayGridView()
    try:
        view.set_cells([CellRenderData(_photo_cell())] * 3)
        view.set_cells([CellRenderData(_photo_cell())])
        assert view.cell_count() == 1
    finally:
        view.deleteLater()


def test_view_header_text_round_trip(qapp):
    view = DayGridView()
    try:
        view.set_header("Day 3 · 2026-04-03 · Hike")
        assert view.header_text() == "Day 3 · 2026-04-03 · Hike"
    finally:
        view.deleteLater()


def test_view_back_button_emits_back_requested(qapp):
    view = DayGridView()
    fired = []
    view.back_requested.connect(lambda: fired.append(True))
    try:
        btn = view.findChild(QPushButton, "DayGridBackButton")
        assert btn is not None
        btn.click()
        assert fired == [True]
    finally:
        view.deleteLater()


def test_view_escape_key_emits_back_requested(qapp):
    view = DayGridView()
    fired = []
    view.back_requested.connect(lambda: fired.append(True))
    try:
        ev = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        view.keyPressEvent(ev)
        assert fired == [True]
    finally:
        view.deleteLater()


def test_view_size_slider_resizes_all_cells_and_emits(qapp):
    view = DayGridView(cell_size=140)
    sizes_seen = []
    view.cell_size_changed.connect(lambda px: sizes_seen.append(px))
    try:
        view.set_cells([CellRenderData(_photo_cell())] * 4)
        view.set_cell_size(200)
        # Programmatic set does NOT emit (avoids double-resize loops with
        # the host) — the host's caller persists the pref externally.
        assert sizes_seen == []
        # All cells took the new size.
        for c in view.cells():
            assert c.width() == 200 and c.height() == 200

        # Now simulate the user dragging the slider — emission happens.
        view._slider.setValue(160)
        assert sizes_seen == [160]
        assert view.cell_size() == 160
    finally:
        view.deleteLater()


def test_view_set_cell_size_clamps_to_range(qapp):
    view = DayGridView()
    try:
        view.set_cell_size(99999)
        assert view.cell_size() == MAX_CELL_SIZE
        view.set_cell_size(1)
        assert view.cell_size() == MIN_CELL_SIZE
    finally:
        view.deleteLater()


def test_view_default_cell_size(qapp):
    view = DayGridView()
    try:
        assert view.cell_size() == DEFAULT_CELL_SIZE
    finally:
        view.deleteLater()


def test_view_emits_cell_signals_with_correct_index(qapp):
    view = DayGridView()
    activated_idx = []
    border_idx = []
    view.cell_activated.connect(lambda i: activated_idx.append(i))
    view.cell_border_clicked.connect(lambda i: border_idx.append(i))
    try:
        view.set_cells([
            CellRenderData(_photo_cell(item_id="a")),
            CellRenderData(_photo_cell(item_id="b")),
            CellRenderData(_photo_cell(item_id="c")),
        ])
        # Fire the second cell's signals directly — emulates a centre /
        # border click without depending on layout geometry.
        view.cell_at(1).center_clicked.emit()
        view.cell_at(1).border_clicked.emit()
        assert activated_idx == [1]
        assert border_idx == [1]
    finally:
        view.deleteLater()


def test_view_update_cell_swaps_data_at_index(qapp):
    view = DayGridView()
    try:
        view.set_cells([
            CellRenderData(_photo_cell(CellColor.UNTOUCHED, "a")),
            CellRenderData(_photo_cell(CellColor.UNTOUCHED, "b")),
        ])
        view.update_cell(1, CellRenderData(_photo_cell(CellColor.KEPT, "b")))
        assert view.cell_at(1).property("status") == "picked"
        # The other cell is untouched (still UNTOUCHED).
        assert view.cell_at(0).property("status") == "untouched"
    finally:
        view.deleteLater()


def test_view_update_cell_ignores_out_of_range(qapp):
    view = DayGridView()
    try:
        view.set_cells([CellRenderData(_photo_cell())])
        view.update_cell(99, CellRenderData(_photo_cell(CellColor.KEPT)))
        # Original still there, no crash.
        assert view.cell_count() == 1
    finally:
        view.deleteLater()
