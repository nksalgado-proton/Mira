"""``ThumbGrid`` — the shared scrolling thumbnail grid (Brief B).

Pins the contract every consumer (Days Grid, Cut detail, Cut session,
Pool detail) relies on:

* ``set_items`` builds chunked cells; ``count()`` / ``items()`` /
  ``cells()`` report the right state.
* ``update_item`` repaints one cell without rebuilding the rest;
  ``set_pixmap`` is the lazy-thumb convenience.
* Single-zone clicks fire ``cell_activated(index)``.
* Two-zone clicks (``two_zone_clicks=True``) route border presses to
  ``cell_border_clicked(index)`` and center presses to
  ``cell_activated(index)`` — the locked Cut-session grammar.
* Esc emits ``back_requested``.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QSize, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent

from mira.ui.design import ThumbGrid, ThumbGridItem


def _item(**over) -> ThumbGridItem:
    kw = dict(state=None, payload=None)
    kw.update(over)
    return ThumbGridItem(**kw)


# --------------------------------------------------------------------------- #
# set_items / update_item / set_pixmap
# --------------------------------------------------------------------------- #


def test_set_items_builds_cells_and_carries_payloads(qapp):
    g = ThumbGrid()
    g.set_items([_item(payload=f"k{i}") for i in range(4)])
    assert g.count() == 4
    assert len(g.cells()) == 4
    payloads = [c.payload() for c in g.cells()]
    assert payloads == ["k0", "k1", "k2", "k3"]


def test_set_items_replaces_previous_contents(qapp):
    g = ThumbGrid()
    g.set_items([_item(payload="a"), _item(payload="b")])
    g.set_items([_item(payload="x")])
    assert g.count() == 1
    assert g.cell_at(0).payload() == "x"


def test_set_items_keeps_visible_state_on_round_trip(qapp):
    g = ThumbGrid()
    g.set_items([_item(state="picked"), _item(state="skipped")])
    assert g.cell_at(0)._state == "picked"
    assert g.cell_at(1)._state == "skipped"


def test_update_item_repaints_one_cell_without_rebuilding(qapp):
    g = ThumbGrid()
    g.set_items([_item(state="skipped", payload="a"),
                 _item(state="skipped", payload="b")])
    cell_b_before = g.cell_at(1)
    g.update_item(0, _item(state="picked", payload="a"))
    assert g.cell_at(0)._state == "picked"
    assert g.cell_at(1) is cell_b_before        # b stayed the same widget
    assert g.items()[0].state == "picked"       # the stored item updated too


def test_set_pixmap_just_swaps_the_image_on_one_cell(qapp):
    from PyQt6.QtGui import QPixmap
    g = ThumbGrid()
    g.set_items([_item(payload="a"), _item(payload="b")])
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.red)
    g.set_pixmap(1, pm)
    assert g.cell_at(1)._pixmap is pm
    # the stored item picks up the pixmap so a rebuild keeps it.
    assert g.items()[1].pixmap is pm


def test_set_pixmap_survives_deleted_cell(qapp):
    """spec/89 §11.3 lifecycle fix (Nelson 2026-06-19) — an async
    thumb decode can land after the host has rebuilt the grid OR
    after the cell's underlying C++ widget has been destroyed. The
    Python wrapper in ``self._cells[index]`` then raises
    ``RuntimeError: wrapped C/C++ object … has been deleted`` when
    we touch it. ``set_pixmap`` must swallow that — the late
    pixmap is meant for a stale cell, dropping it is correct."""
    from PyQt6.QtGui import QPixmap
    g = ThumbGrid()
    g.set_items([_item(payload="a"), _item(payload="b")])
    # Simulate the Qt zombie state: the Python list still holds the
    # cell but the C++ widget has been destroyed (re-parent + delete).
    cell = g.cell_at(0)
    cell.setParent(None)
    cell.deleteLater()
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.green)
    # Pre-fix this raised RuntimeError and crashed the app. Post-fix
    # the call returns cleanly + the stored item still picks up the
    # pixmap (so a future rebuild gets the right pixels).
    g.set_pixmap(0, pm)
    assert g.items()[0].pixmap is pm


def test_update_item_survives_deleted_cell(qapp):
    """spec/89 §11.3 lifecycle fix — same guard as
    :func:`test_set_pixmap_survives_deleted_cell` but for
    ``update_item`` (used for state repaints, not just pixmaps).
    """
    g = ThumbGrid()
    g.set_items([_item(state="skipped", payload="a"),
                 _item(state="skipped", payload="b")])
    cell = g.cell_at(0)
    cell.setParent(None)
    cell.deleteLater()
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    g.update_item(0, _item(state="picked", payload="a"))
    # The stored item updated even though the cell was a zombie.
    assert g.items()[0].state == "picked"


# --------------------------------------------------------------------------- #
# Single-zone clicks
# --------------------------------------------------------------------------- #


def _press(widget, x: int, y: int) -> None:
    widget.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))


def test_single_zone_click_fires_cell_activated(qapp):
    g = ThumbGrid()
    g.set_items([_item(payload="a"), _item(payload="b"), _item(payload="c")])
    seen: list[int] = []
    g.cell_activated.connect(seen.append)
    _press(g.cell_at(1), 10, 10)
    assert seen == [1]


def test_single_zone_does_not_emit_border_clicked(qapp):
    g = ThumbGrid(two_zone_clicks=False)
    g.set_items([_item(payload="a")])
    border = []
    activated = []
    g.cell_border_clicked.connect(border.append)
    g.cell_activated.connect(activated.append)
    # Click right at the edge — single-zone treats every press the same.
    _press(g.cell_at(0), 2, 2)
    assert border == [] and activated == [0]


# --------------------------------------------------------------------------- #
# Two-zone clicks (the Cut-session grammar)
# --------------------------------------------------------------------------- #


def test_two_zone_border_press_emits_cell_border_clicked(qapp):
    g = ThumbGrid(two_zone_clicks=True, cell_size=QSize(100, 100))
    g.set_items([_item(payload="a"), _item(payload="b")])
    border = []
    activated = []
    g.cell_border_clicked.connect(border.append)
    g.cell_activated.connect(activated.append)
    # A press at (2, 50) is well inside the 10 % border ring.
    _press(g.cell_at(1), 2, 50)
    assert border == [1] and activated == []


def test_two_zone_center_press_emits_cell_activated(qapp):
    g = ThumbGrid(two_zone_clicks=True, cell_size=QSize(100, 100))
    g.set_items([_item(payload="a")])
    border = []
    activated = []
    g.cell_border_clicked.connect(border.append)
    g.cell_activated.connect(activated.append)
    # A press at (50, 50) is deep in the centre zone.
    _press(g.cell_at(0), 50, 50)
    assert border == [] and activated == [0]


def test_two_zone_hit_zone_helper_returns_the_right_label(qapp):
    g = ThumbGrid(two_zone_clicks=True, cell_size=QSize(100, 100))
    g.set_items([_item(payload="a")])
    cell = g.cell_at(0)
    assert cell.hit_zone(2, 50) == "border"
    assert cell.hit_zone(50, 50) == "center"
    assert cell.hit_zone(200, 200) == "outside"


# --------------------------------------------------------------------------- #
# Keyboard
# --------------------------------------------------------------------------- #


def test_esc_emits_back_requested(qapp):
    g = ThumbGrid()
    g.set_items([_item(payload="a")])
    seen = []
    g.back_requested.connect(lambda: seen.append("back"))
    ev = QKeyEvent(
        QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
    )
    g.keyPressEvent(ev)
    assert seen == ["back"]


# --------------------------------------------------------------------------- #
# State paint — the locked §5a 3px border rides Thumb directly, so
# every consumer inherits the same look without forking the chrome.
# --------------------------------------------------------------------------- #


def test_state_token_lands_on_the_underlying_thumb(qapp):
    g = ThumbGrid()
    g.set_items([
        _item(state="picked"),
        _item(state="skipped"),
        _item(state="compare"),
        _item(state="mixed"),
        _item(state=None),
    ])
    states = [c._state for c in g.cells()]
    assert states == ["picked", "skipped", "compare", "mixed", None]


def test_exported_flag_lands_on_the_underlying_thumb(qapp):
    """Spec/59 §8 — the corner Exported badge survives the migration."""
    g = ThumbGrid()
    g.set_items([_item(exported=True), _item(exported=False)])
    assert g.cell_at(0)._exported is True
    assert g.cell_at(1)._exported is False


def test_focusable_items_are_tab_focusable(qapp):
    """DaysGridPage relies on the locked §63 keys acting on the focused
    Thumb — the ``focusable`` flag must enable StrongFocus."""
    g = ThumbGrid()
    g.set_items([_item(focusable=True), _item(focusable=False)])
    assert g.cell_at(0).focusPolicy() == Qt.FocusPolicy.StrongFocus
    # The default Thumb has NoFocus; focusable=False keeps that.
    assert g.cell_at(1).focusPolicy() != Qt.FocusPolicy.StrongFocus
