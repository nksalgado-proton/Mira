"""Tests for the crop overlay's rotation handle (Nelson 2026-06-10 —
free-angle Box Rotation by dragging the lollipop, not stepping buttons).

Pins: the snap/normalize math, handle hit-testing at 0° AND while
rotated (the handle rides the box), the press→move→release gesture
producing the right angle, and the commit signal contract (release
emits ``angle_changed``, not ``rect_changed``).
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QEvent, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent

from mira.ui.edited.crop_overlay import (
    CropOverlay,
    _DragMode,
    _ROTATE_STEM_PX,
)


def _overlay(qapp) -> CropOverlay:
    ov = CropOverlay()
    ov.resize(400, 300)
    # Photo paints the full widget; a centred half-size crop rect.
    ov.set_image_geometry(QRect(0, 0, 400, 300), (4000, 3000))
    ov.set_rect((0.25, 0.25, 0.5, 0.5))   # widget rect (100,75)-(300,225)
    return ov


def _mouse(kind, pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        kind, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)


def _handle_pos(ov: CropOverlay) -> QPointF:
    rect = ov._norm_to_widget(ov.current_rect_norm())
    return ov._rotate_handle_widget(rect)


# --------------------------------------------------------------------------- #
# Math
# --------------------------------------------------------------------------- #


def test_normalize_wraps_to_half_open_range():
    assert CropOverlay._normalize_angle(190.0) == -170.0
    assert CropOverlay._normalize_angle(-190.0) == 170.0
    assert CropOverlay._normalize_angle(180.0) == 180.0
    assert CropOverlay._normalize_angle(360.0) == 0.0


def test_snap_clicks_to_cardinals_within_two_degrees():
    assert CropOverlay._snap_angle(1.4) == 0.0
    assert CropOverlay._snap_angle(-1.9) == 0.0
    assert CropOverlay._snap_angle(88.5) == 90.0
    assert CropOverlay._snap_angle(-91.2) == -90.0
    assert CropOverlay._snap_angle(178.4) == 180.0
    assert CropOverlay._snap_angle(5.0) == 5.0          # free angle survives


# --------------------------------------------------------------------------- #
# Hit-testing
# --------------------------------------------------------------------------- #


def test_handle_hit_at_zero_degrees(qapp):
    ov = _overlay(qapp)
    hp = _handle_pos(ov)                   # (~200, 75 - stem); QRect's
    # integer center() sits at (l + r) // 2 → one pixel left of true mid.
    assert abs(hp.x() - 200.0) <= 1.0
    assert hp.y() == 75.0 - _ROTATE_STEM_PX
    assert ov._hit_test(hp) == _DragMode.ROTATE
    # The old zones survive around it.
    assert ov._hit_test(QPointF(200, 150)) == _DragMode.MOVE
    assert ov._hit_test(QPointF(100, 75)) == _DragMode.RESIZE_TL


def test_handle_rides_the_rotated_box(qapp):
    ov = _overlay(qapp)
    ov.set_box_angle(30.0)
    hp = _handle_pos(ov)                   # rotated widget position
    assert ov._hit_test(hp) == _DragMode.ROTATE
    # The un-rotated handle spot is no longer a hit.
    assert ov._hit_test(
        QPointF(200.0, 75.0 - _ROTATE_STEM_PX)) != _DragMode.ROTATE


# --------------------------------------------------------------------------- #
# The gesture
# --------------------------------------------------------------------------- #


def test_drag_rotates_to_mouse_bearing_and_commits_on_release(qapp):
    ov = _overlay(qapp)
    committed = []
    ov.angle_changed.connect(committed.append)
    rect_commits = []
    ov.rect_changed.connect(rect_commits.append)

    ov.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, _handle_pos(ov)))
    assert ov._drag_mode == _DragMode.ROTATE

    # Drag to 30° clockwise: the handle's bearing starts at -90° (straight
    # up); the grab offset keeps angle = bearing + 90, so a point at
    # bearing -60° puts the box at +30°.
    c = ov._norm_to_widget(ov.current_rect_norm()).center()
    r = 120.0
    target = QPointF(
        c.x() + r * math.cos(math.radians(-60)),
        c.y() + r * math.sin(math.radians(-60)))
    ov.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, target))
    assert abs(ov.current_box_angle() - 30.0) < 0.01

    ov.mouseReleaseEvent(
        _mouse(QEvent.Type.MouseButtonRelease, target))
    assert committed and abs(committed[0] - 30.0) < 0.01
    assert rect_commits == []              # a rotation is not a rect edit
    assert ov._drag_mode == _DragMode.NONE


def test_drag_near_level_snaps_to_zero(qapp):
    ov = _overlay(qapp)
    ov.set_box_angle(10.0)
    committed = []
    ov.angle_changed.connect(committed.append)

    ov.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, _handle_pos(ov)))
    # Move so the raw angle would be ~1.2° — inside the magnetic zone.
    c = ov._norm_to_widget(ov.current_rect_norm()).center()
    bearing = math.radians(-90 + 1.2)
    target = QPointF(
        c.x() + 120.0 * math.cos(bearing),
        c.y() + 120.0 * math.sin(bearing))
    ov.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, target))
    assert ov.current_box_angle() == 0.0
    ov.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, target))
    assert committed == [0.0]


def test_resize_with_mismatched_aspect_clamps_rect_in_unit_box(qapp):
    """Regression: dragging a corner with an aspect lock that doesn't
    match the image rect's own aspect — e.g. 16:9 on a portrait photo
    — used to grow ``cand_w`` / ``cand_h`` past 1.0 because the lock
    always GREW the un-anchored dimension to satisfy the ratio. The
    OOB rect then tripped the adjustment row's schema CHECK
    (``crop_h IS NULL OR (crop_h > 0 AND crop_h <= 1)``) when the
    surface tried to save and Mira crashed.

    Pin: a far-corner drag on a portrait image rect with 16:9 lock
    commits a rect that still fits inside ``[0, 1]``."""
    ov = CropOverlay()
    ov.resize(400, 800)
    # Portrait image rect (200 wide × 400 tall) inside a portrait widget.
    ov.set_image_geometry(QRect(0, 0, 200, 400), (2000, 4000))
    ov.set_aspect_ratio("16:9")
    ov.set_rect((0.4, 0.4, 0.2, 0.2))

    committed: list[tuple] = []
    ov.rect_changed.connect(committed.append)

    rect_widget = ov._norm_to_widget(ov.current_rect_norm())
    br = QPointF(rect_widget.right(), rect_widget.bottom())
    ov.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, br))
    ov.mouseMoveEvent(_mouse(
        QEvent.Type.MouseMove, QPointF(10_000.0, 10_000.0)))
    ov.mouseReleaseEvent(_mouse(
        QEvent.Type.MouseButtonRelease, QPointF(10_000.0, 10_000.0)))

    assert committed, "release should commit a rect"
    x, y, w, h = committed[-1]
    assert 0.0 <= x <= 1.0
    assert 0.0 <= y <= 1.0
    assert 0.0 < w <= 1.0
    assert 0.0 < h <= 1.0
    assert x + w <= 1.0 + 1e-9
    assert y + h <= 1.0 + 1e-9
