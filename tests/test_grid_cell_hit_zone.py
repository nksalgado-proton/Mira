"""spec/103 — the two-zone grid cell's status-toggle hit zone is the
outer quarter on each axis (central 50%×50% opens; surrounding "L"
toggles status).

The legacy rule capped the BORDER band at 32 px (~11 % of a 280-px
tile), so "click near the border" missed constantly. spec/103 widens
it to the literal "closer to an edge than to the centre line" split —
on a single axis that's exactly `x < w/4`.

These tests pin the helper at small (140 px) and large (280 px) tiles
to prove the quarter rule scales, plus the centre / corner / edge
cases.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QSize

from mira.ui.design import ThumbGrid, ThumbGridItem


def _cell(qapp, size_px: int):
    """A single two-zone cell sized to ``size_px``×``size_px``. The
    parent ``ThumbGrid`` carries `two_zone_clicks=True` so `_GridCell`
    is the spec/103 hit-zone target."""
    grid = ThumbGrid(
        two_zone_clicks=True,
        cell_size=QSize(size_px, size_px),
    )
    grid.set_items([ThumbGridItem(state=None, payload="x")])
    cell = grid.cell_at(0)
    # The grid is offscreen and never shown — force a layout so
    # `cell.width()` / `cell.height()` report the intended size.
    cell.resize(size_px, size_px)
    assert cell.width() == size_px and cell.height() == size_px, (
        f"cell did not size to {size_px}×{size_px} "
        f"(got {cell.width()}×{cell.height()})")
    return grid, cell


# ── Quarter rule pinned at two tile sizes ────────────────────────


@pytest.mark.parametrize("size_px", [140, 280])
def test_center_point_is_center(qapp, size_px):
    """The geometric centre of the tile must always open (drill-in)
    — the simplest sanity case."""
    grid, cell = _cell(qapp, size_px)
    try:
        mid = size_px // 2
        assert cell.hit_zone(mid, mid) == "center"
    finally:
        grid.deleteLater()


@pytest.mark.parametrize("size_px", [140, 280])
def test_twenty_percent_point_is_border(qapp, size_px):
    """A point at 20 % of the tile is inside the outer-quarter band
    (20 % < 25 %), so it toggles. This is exactly the click that the
    legacy 32-px cap missed on a 280-px tile (20 % = 56 px, well past
    the old 32-px border band)."""
    grid, cell = _cell(qapp, size_px)
    try:
        twenty = int(size_px * 0.20)
        assert cell.hit_zone(twenty, twenty) == "border", (
            f"point at 20% ({twenty}px) of a {size_px}px tile must be "
            f"in the border zone (outer quarter rule)")
    finally:
        grid.deleteLater()


@pytest.mark.parametrize("size_px", [140, 280])
def test_forty_percent_point_is_center(qapp, size_px):
    """A point at 40 % of the tile is inside the central half
    (40 % > 25 % AND 60 % < 75 %), so it opens."""
    grid, cell = _cell(qapp, size_px)
    try:
        forty = int(size_px * 0.40)
        assert cell.hit_zone(forty, forty) == "center"
    finally:
        grid.deleteLater()


@pytest.mark.parametrize("size_px", [140, 280])
def test_all_four_corners_are_border(qapp, size_px):
    """The four corners are the natural "border" targets. Test
    inside-the-cell pixels next to each corner (the bottom-right
    corner uses ``size_px - 1`` because the bottom/right exclusive
    bound is ``>= size_px - bx``)."""
    grid, cell = _cell(qapp, size_px)
    last = size_px - 1
    try:
        assert cell.hit_zone(0, 0) == "border"
        assert cell.hit_zone(last, 0) == "border"
        assert cell.hit_zone(0, last) == "border"
        assert cell.hit_zone(last, last) == "border"
    finally:
        grid.deleteLater()


# ── Quarter rule at the edges of each zone ──────────────────────


def test_quarter_boundary_280(qapp):
    """Pin the exact integer split on a 280-px tile: ``bx = 280//4 =
    70``. So x=69 → border (closer to the edge); x=70 → centre (the
    rule is ``x < bx``, which includes x=70 in centre by the literal
    ``<`` test). Same on the right side at width − bx = 210."""
    grid, cell = _cell(qapp, 280)
    try:
        # Vertical strip through the middle to isolate the x-axis.
        assert cell.hit_zone(69, 140) == "border"
        assert cell.hit_zone(70, 140) == "center"
        assert cell.hit_zone(209, 140) == "center"
        assert cell.hit_zone(210, 140) == "border"
    finally:
        grid.deleteLater()


def test_outside_returns_outside(qapp):
    """Anything outside the rect still returns ``"outside"`` — the
    helper's only non-toggle/non-open verdict."""
    grid, cell = _cell(qapp, 200)
    try:
        assert cell.hit_zone(-1, 100) == "outside"
        assert cell.hit_zone(200, 100) == "outside"
        assert cell.hit_zone(100, -1) == "outside"
        assert cell.hit_zone(100, 200) == "outside"
    finally:
        grid.deleteLater()


# ── Single-zone cells are NOT affected ──────────────────────────


def test_single_zone_grid_does_not_route_via_quarter_rule(qapp):
    """spec/103 only widens the two-zone split. A single-zone grid
    (the Editor / Export grid default) still fires `cell_activated`
    for every press, regardless of where in the tile the click lands."""
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    g = ThumbGrid(two_zone_clicks=False, cell_size=QSize(200, 200))
    g.set_items([ThumbGridItem(state=None, payload="x")])
    cell = g.cell_at(0)
    cell.resize(200, 200)

    border_seen = []
    activated_seen = []
    g.cell_border_clicked.connect(border_seen.append)
    g.cell_activated.connect(activated_seen.append)

    # A press in the outer quarter (40 px from the corner) — would
    # have been "border" in a two-zone cell. In single-zone it just
    # activates.
    cell.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(40, 40),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    assert border_seen == []
    assert activated_seen == [0]
    g.deleteLater()
