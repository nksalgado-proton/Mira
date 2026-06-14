"""Drag-to-position crop overlay for the Process phase.

Sits over the photo display in :class:`MediaCanvas` and lets the user
move the crop rectangle around or resize it from the four corners.
The overlay is aspect-ratio-locked — corner drags preserve the ratio
chosen on the toolbar, so the user can compose without ever worrying
about "is this still 16:9?". Switching the ratio resets the rect to
the centered maximal crop for the new shape.

Coordinate spaces
-----------------
* **norm**: the persisted ``(x, y, w, h)`` is normalized in ``[0, 1]``
  over the source image. This is what gets stored in the journal —
  independent of the viewer's current size.
* **widget**: at paint / hit-test time we map norm → widget pixels
  through ``image_rect`` (the rectangle inside the overlay where the
  letterboxed photo is actually drawn). The overlay paints only
  inside ``image_rect``; outside is fully transparent so the letter-
  box underneath shows through.

The overlay does not own the image — it strictly handles the rect.
The host (``IngestEditPage``) calls ``set_image_geometry()`` after
each photo paint, ``set_aspect_ratio()`` when the toolbar combo
changes, and ``set_rect()`` when restoring a journaled decision.
Every user-initiated move emits ``rect_changed`` on mouse release so
the host can write the journal and re-render.

Ported from the prototype's ``ui/process/crop_overlay.py`` (Costa
Rica / Pantanal field-tested 2026-04-30 / 2026-05-01); the window-
mask code for the video player is dropped — Mira's Process page
only deals with photos so the simpler "transparent widget with
strokes" version suffices.
"""

from __future__ import annotations

import logging
import math
from enum import Enum, auto
from typing import Optional

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from core.aspect_ratio import AspectRatio, get_aspect_ratio
from core.photo_render import compute_default_crop

log = logging.getLogger(__name__)


# Pixels around each handle that count as "grab this handle". Generous
# so the user doesn't have to pixel-hunt with the mouse.
_HANDLE_HIT_RADIUS = 12

# Drawn handle size — slightly smaller than the hit radius so the
# visible square is comfortably inside the touchable area.
_HANDLE_DRAW_RADIUS = 6

# Minimum rect size in normalised units. Below this the rect reads as
# a tiny dot the user can't grab back — guard against degenerate
# clicks-without-drag.
_MIN_RECT_NORM = 0.05

# Rotation handle (Nelson 2026-06-10 — free-angle crop by dragging, not
# by stepping buttons): a lollipop above the top edge's midpoint. The
# stem keeps the grab zone clear of the top-edge corner handles.
_ROTATE_STEM_PX = 18

# Magnetic snap while rotating — within this many degrees of a cardinal
# angle the box clicks level (the horizon-straightening use case).
_SNAP_DEG = 2.0
_SNAP_TARGETS = (0.0, 90.0, -90.0, 180.0)


class _DragMode(Enum):
    NONE = auto()
    MOVE = auto()
    ROTATE = auto()
    RESIZE_TL = auto()
    RESIZE_TR = auto()
    RESIZE_BL = auto()
    RESIZE_BR = auto()


# Cursor lookup keyed by drag mode — declared once so hover handling
# and active drag share the same shape.
_CURSOR_BY_MODE: dict[_DragMode, Qt.CursorShape] = {
    _DragMode.MOVE: Qt.CursorShape.SizeAllCursor,
    _DragMode.ROTATE: Qt.CursorShape.CrossCursor,
    _DragMode.RESIZE_TL: Qt.CursorShape.SizeFDiagCursor,
    _DragMode.RESIZE_BR: Qt.CursorShape.SizeFDiagCursor,
    _DragMode.RESIZE_TR: Qt.CursorShape.SizeBDiagCursor,
    _DragMode.RESIZE_BL: Qt.CursorShape.SizeBDiagCursor,
}


class CropOverlay(QWidget):
    """Aspect-ratio-locked, draggable crop rectangle.

    Signals:
        rect_changed: emitted with the normalized ``(x, y, w, h)``
            after every committed user gesture (mouse release).
        angle_changed: emitted with the Box Rotation angle (clockwise
            degrees, snapped) when a rotation-handle drag commits on
            mouse release. The host owns persistence, same as
            ``rect_changed``.
    """

    rect_changed = pyqtSignal(tuple)
    angle_changed = pyqtSignal(float)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        # The overlay must let mouse events through to the host when
        # we're not over the rect interior, so the host can keep
        # responding to clicks elsewhere on the canvas. We achieve
        # this via per-event acceptance in mousePressEvent (see below).
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, False,
        )
        # Allow paint to skip the bg fill — leaves the photo visible.
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True,
        )

        self._aspect_ratio: AspectRatio = get_aspect_ratio("Original")
        self._rect_norm: Optional[tuple[float, float, float, float]] = None
        # Where the photo actually paints inside this widget. Updated
        # by the host on every refresh / resize so hit-testing maps
        # mouse pixels onto the photo's domain, not the widget's.
        self._image_rect: QRect = QRect()
        # Source-image pixel size (e.g. 5184×3888 for a Lumix RAW).
        self._image_pixel_size: tuple[int, int] = (0, 0)

        self._drag_mode: _DragMode = _DragMode.NONE
        self._drag_anchor: Optional[QPointF] = None  # in widget pixels
        # Box Rotation (docs/25 §4): the crop box spins about its OWN
        # centre by this angle (degrees, clockwise), keeping its size +
        # centre. 0 = axis-aligned (the classic crop). While rotated,
        # the box is drawn rotated and can be MOVED; resize is done at
        # 0° (a v1 simplification — note in the page). Any angle is
        # reachable by dragging the rotation handle (the lollipop above
        # the top edge); the 90° buttons remain as coarse steps.
        self._angle: float = 0.0
        # Grab offset for a rotation drag: angle − mouse-bearing at
        # press, so the handle follows the cursor without jumping.
        self._rotate_offset: float = 0.0

    # ── Public API ────────────────────────────────────────────────

    def set_box_angle(self, degrees: float) -> None:
        """Set the Box Rotation angle (clockwise degrees). Rotates about
        the box centre, size unchanged. Does not emit — the host owns
        the angle (the rotation buttons) + its persistence."""
        self._angle = float(degrees)
        self.update()

    def current_box_angle(self) -> float:
        return self._angle

    def set_aspect_ratio(self, ratio_label: str) -> None:
        """Switch the locked aspect ratio.

        Defers rect computation — :meth:`set_image_geometry` sees the
        real photo size and decides: full bounds for Original, centered
        maximal crop for any locked ratio. Callers should re-emit the
        rect to the host after the next paint so the new state is
        journaled."""
        self._aspect_ratio = get_aspect_ratio(ratio_label)
        # Force a recompute on the next geometry update.
        self._rect_norm = None
        self.update()

    def set_rect(
        self,
        rect_norm: Optional[tuple[float, float, float, float]],
    ) -> None:
        """Replace the rect (e.g. when navigating to a new photo whose
        decision has a journaled rect, or after Reset). ``None``
        triggers fallback to the centered maximal crop on the next
        :meth:`set_image_geometry` call."""
        self._rect_norm = (
            tuple(rect_norm) if rect_norm is not None else None  # type: ignore[assignment]
        )
        self.update()

    def set_image_geometry(
        self,
        image_rect: QRect,
        image_size: tuple[int, int],
    ) -> None:
        """Tell the overlay where in widget pixels the photo paints
        and what the photo's pixel size is. Size determines the
        default crop's shape; ``image_rect`` is the painting domain."""
        self._image_rect = QRect(image_rect)
        self._image_pixel_size = (int(image_size[0]), int(image_size[1]))
        if self._rect_norm is None:
            if self._aspect_ratio.is_original:
                self._rect_norm = (0.0, 0.0, 1.0, 1.0)
            else:
                iw, ih = image_size
                self._rect_norm = compute_default_crop(
                    iw, ih, self._aspect_ratio,
                )
                if self._rect_norm is None:                 # safety net
                    self._rect_norm = (0.0, 0.0, 1.0, 1.0)
        self.update()

    def current_rect_norm(
        self,
    ) -> Optional[tuple[float, float, float, float]]:
        return self._rect_norm

    # ── Painting ──────────────────────────────────────────────────

    def paintEvent(self, _event):                # noqa: N802
        if self._rect_norm is None or self._image_rect.isEmpty():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        rect_widget = self._norm_to_widget(self._rect_norm)

        # Box Rotation (docs/25 §4): rotate the painter about the box
        # centre so the whole frame (outline + thirds + handles) draws
        # rotated over the static photo, keeping its size + centre.
        # QPainter.rotate(+) is clockwise — matches the CW box angle.
        painter.save()
        if self._angle:
            c = rect_widget.center()
            painter.translate(c)
            painter.rotate(self._angle)
            painter.translate(-c)

        # Black-outlined white rect → high-contrast on both bright AND
        # dark backgrounds. Costa Rica field test 2026-04-30 (per the
        # prototype): the original "dimmed cropped-out area" merged
        # visually with dark photo content (skies, shadows) and made
        # the crop area look like a bordered photo. The frame alone is
        # the cleanest signal of what's inside vs. outside.
        outer_w = 3
        inner_w = 1
        outline_inset = max(1, outer_w // 2)
        outline_rect = rect_widget.adjusted(
            outline_inset, outline_inset, -outline_inset, -outline_inset,
        )
        outline_pen = QPen(QColor(0, 0, 0, 200))
        outline_pen.setWidth(outer_w)
        painter.setPen(outline_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(outline_rect)
        inner_pen = QPen(QColor(255, 255, 255, 240))
        inner_pen.setWidth(inner_w)
        painter.setPen(inner_pen)
        painter.drawRect(outline_rect)

        # Rule-of-thirds grid — classic composition aid, dim so it
        # doesn't fight with the photo.
        thirds_pen = QPen(QColor(255, 255, 255, 90))
        thirds_pen.setWidth(1)
        painter.setPen(thirds_pen)
        third_w = rect_widget.width() / 3
        third_h = rect_widget.height() / 3
        for i in (1, 2):
            x = int(rect_widget.left() + i * third_w)
            painter.drawLine(
                x, rect_widget.top(), x, rect_widget.bottom(),
            )
            y = int(rect_widget.top() + i * third_h)
            painter.drawLine(
                rect_widget.left(), y, rect_widget.right(), y,
            )

        # Corner handles — drawn INSIDE the rect by the handle radius
        # so they're always fully visible (even when rect sits at the
        # widget boundary). Hit-testing still uses the geometric
        # corners with a wide _HANDLE_HIT_RADIUS so the user can grab
        # the corner OR the painted handle interchangeably.
        # Corner handles only when un-rotated (resize is available at
        # 0° only in this v1; a rotated box shows just its outline).
        if not self._angle:
            painter.setPen(QPen(QColor(0, 0, 0, 180), 1))
            painter.setBrush(QColor(255, 255, 255, 230))
            for cx, cy in self._handle_draw_centers(rect_widget):
                painter.drawRect(QRect(
                    cx - _HANDLE_DRAW_RADIUS, cy - _HANDLE_DRAW_RADIUS,
                    _HANDLE_DRAW_RADIUS * 2, _HANDLE_DRAW_RADIUS * 2,
                ))

        # Rotation handle — the lollipop above the top edge's midpoint,
        # drawn inside the rotated transform so it rides the box.
        hl = self._rotate_handle_local(rect_widget)
        stem_pen = QPen(QColor(0, 0, 0, 200))
        stem_pen.setWidth(3)
        painter.setPen(stem_pen)
        painter.drawLine(
            int(hl.x()), rect_widget.top(), int(hl.x()), int(hl.y()))
        stem_pen = QPen(QColor(255, 255, 255, 240))
        stem_pen.setWidth(1)
        painter.setPen(stem_pen)
        painter.drawLine(
            int(hl.x()), rect_widget.top(), int(hl.x()), int(hl.y()))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(0, 0, 0, 200), 2))
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawEllipse(hl, _HANDLE_DRAW_RADIUS, _HANDLE_DRAW_RADIUS)
        painter.restore()

        # Live angle readout while rotating — horizontal text (outside
        # the rotated transform) beside the handle's WIDGET position.
        if self._drag_mode == _DragMode.ROTATE:
            hw = self._rotate_handle_widget(rect_widget)
            label = f"{self._angle:+.1f}°"
            painter.setPen(QPen(QColor(0, 0, 0, 220)))
            painter.drawText(
                int(hw.x()) + 13, int(hw.y()) + 5, label)
            painter.setPen(QPen(QColor(255, 255, 255, 240)))
            painter.drawText(
                int(hw.x()) + 12, int(hw.y()) + 4, label)

    @staticmethod
    def _corner_centers(rect: QRect) -> list[tuple[int, int]]:
        """Geometric corners — used for hit-testing. The user grabs a
        wide ``_HANDLE_HIT_RADIUS`` zone around each corner."""
        return [
            (rect.left(), rect.top()),
            (rect.right(), rect.top()),
            (rect.left(), rect.bottom()),
            (rect.right(), rect.bottom()),
        ]

    @staticmethod
    def _handle_draw_centers(rect: QRect) -> list[tuple[int, int]]:
        """Where to PAINT the corner handles — inset from the
        geometric corners by ``_HANDLE_DRAW_RADIUS`` so the squares
        sit fully inside the rect."""
        r = _HANDLE_DRAW_RADIUS
        return [
            (rect.left() + r, rect.top() + r),
            (rect.right() - r, rect.top() + r),
            (rect.left() + r, rect.bottom() - r),
            (rect.right() - r, rect.bottom() - r),
        ]

    # ── Rotation-handle geometry + math ──────────────────────────

    @staticmethod
    def _rotate_handle_local(rect: QRect) -> QPointF:
        """The handle centre in the box's UN-rotated frame: above the
        top edge's midpoint by the stem length."""
        return QPointF(rect.center().x(), rect.top() - _ROTATE_STEM_PX)

    def _rotate_handle_widget(self, rect: QRect) -> QPointF:
        """The handle centre in WIDGET coordinates (the local point
        rotated about the box centre by the current angle)."""
        hl = self._rotate_handle_local(rect)
        if not self._angle:
            return hl
        c = rect.center()
        a = math.radians(self._angle)
        cos, sin = math.cos(a), math.sin(a)
        dx, dy = hl.x() - c.x(), hl.y() - c.y()
        return QPointF(
            c.x() + dx * cos - dy * sin,
            c.y() + dx * sin + dy * cos,
        )

    def _bearing_about_center(self, pos: QPointF) -> float:
        """Clockwise degrees of ``pos`` about the box centre (screen
        coordinates are y-down, so atan2(dy, dx) IS clockwise)."""
        c = self._norm_to_widget(self._rect_norm).center()
        return math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))

    @staticmethod
    def _normalize_angle(angle: float) -> float:
        """Wrap to (-180, 180]."""
        a = float(angle) % 360.0
        if a > 180.0:
            a -= 360.0
        return a

    @staticmethod
    def _snap_angle(angle: float) -> float:
        """Magnetic snap to the cardinal angles within ``_SNAP_DEG`` —
        the horizon clicks level instead of resting at 0.4°."""
        for target in _SNAP_TARGETS:
            if abs(angle - target) <= _SNAP_DEG:
                return target
        return angle

    # ── Mouse handling ────────────────────────────────────────────

    def mousePressEvent(self, event):            # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self._rect_norm is None:
            event.ignore()
            return

        pos = event.position()
        mode = self._hit_test(pos)
        if mode == _DragMode.NONE:
            # Click outside the rect — let the host see the event so
            # global keyboard focus / canvas interactions still work.
            event.ignore()
            return
        self._drag_mode = mode
        rect_widget = self._norm_to_widget(self._rect_norm)
        if mode == _DragMode.ROTATE:
            # Grab offset: the handle follows the cursor without a jump,
            # wherever inside the grab zone the press landed.
            self._rotate_offset = (
                self._angle - self._bearing_about_center(pos))
        elif mode == _DragMode.MOVE:
            # Centre-relative anchor → moving translates the box centre,
            # which works the same whether or not the box is rotated.
            c = rect_widget.center()
            self._drag_anchor = QPointF(pos.x() - c.x(), pos.y() - c.y())
        elif mode == _DragMode.RESIZE_TL:
            self._drag_anchor = QPointF(
                rect_widget.right(), rect_widget.bottom(),
            )
        elif mode == _DragMode.RESIZE_TR:
            self._drag_anchor = QPointF(
                rect_widget.left(), rect_widget.bottom(),
            )
        elif mode == _DragMode.RESIZE_BL:
            self._drag_anchor = QPointF(
                rect_widget.right(), rect_widget.top(),
            )
        elif mode == _DragMode.RESIZE_BR:
            self._drag_anchor = QPointF(
                rect_widget.left(), rect_widget.top(),
            )
        self.setCursor(_CURSOR_BY_MODE.get(
            mode, Qt.CursorShape.ArrowCursor))
        event.accept()

    def mouseMoveEvent(self, event):             # noqa: N802
        """Nelson 2026-05-22 bug fix: when the overlay isn't
        actively engaged (no rect AND no drag), we must
        ``event.ignore()`` so MediaCanvas's box-zoom pan handler
        below can receive the move. Without this, hover events
        get silently swallowed and the photo cannot be panned in
        zoom mode."""
        if self._rect_norm is None:
            # No crop rect = nothing for the overlay to do. Let
            # canvas pan / hover receive the event.
            event.ignore()
            return

        if self._drag_mode == _DragMode.NONE:
            # Hover (have a rect but not actively dragging): update
            # the cursor IF inside one of the rect's hit zones; if
            # not, ignore() so canvas hover handlers still work.
            mode = self._hit_test(event.position())
            if mode == _DragMode.NONE:
                event.ignore()
                return
            self.setCursor(_CURSOR_BY_MODE.get(
                mode, Qt.CursorShape.ArrowCursor))
            event.accept()
            return

        if self._drag_mode == _DragMode.ROTATE:
            self._do_rotate(event.position())
        elif self._drag_mode == _DragMode.MOVE:
            self._do_move(event.position())
        else:
            self._do_resize(event.position())
        event.accept()

    def mouseReleaseEvent(self, event):          # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self._drag_mode == _DragMode.NONE:
            # Wasn't dragging — let the canvas's release handler
            # finish whatever pan/zoom drag it had going.
            event.ignore()
            return
        was_rotate = self._drag_mode == _DragMode.ROTATE
        self._drag_mode = _DragMode.NONE
        self._drag_anchor = None
        if was_rotate:
            # The host owns the angle (persistence + render), same
            # contract as rect_changed.
            self.update()                  # drop the live readout
            self.angle_changed.emit(self._angle)
        elif self._rect_norm is not None:
            self.rect_changed.emit(self._rect_norm)
        event.accept()

    # ── Drag implementations ─────────────────────────────────────

    def _do_move(self, mouse_pos: QPointF) -> None:
        if self._rect_norm is None or self._drag_anchor is None:
            return
        ir = self._image_rect
        _, _, w, h = self._rect_norm
        # Translate the box CENTRE to follow the cursor (anchor is
        # centre-relative). Rotation-agnostic.
        new_cx = mouse_pos.x() - self._drag_anchor.x()
        new_cy = mouse_pos.y() - self._drag_anchor.y()
        cx_norm = (new_cx - ir.left()) / max(ir.width(), 1)
        cy_norm = (new_cy - ir.top()) / max(ir.height(), 1)
        x_norm = cx_norm - w / 2.0
        y_norm = cy_norm - h / 2.0
        # Clamp so the rect stays entirely within the image.
        x_norm = max(0.0, min(1.0 - w, x_norm))
        y_norm = max(0.0, min(1.0 - h, y_norm))
        self._rect_norm = (x_norm, y_norm, w, h)
        self.update()

    def _do_rotate(self, mouse_pos: QPointF) -> None:
        """Free-angle Box Rotation by handle drag (Nelson 2026-06-10):
        the new angle is the mouse bearing plus the grab offset, wrapped
        to (-180, 180] and magnetically snapped to the cardinals. Live
        visual only — the commit (signal) happens on release."""
        if self._rect_norm is None:
            return
        self._angle = self._snap_angle(self._normalize_angle(
            self._bearing_about_center(mouse_pos) + self._rotate_offset))
        self.update()

    def _do_resize(self, mouse_pos: QPointF) -> None:
        """Resize anchored at the opposite corner.

        All modes lock the candidate w/h to a target aspect ratio: the
        named ratio for 16:9 / 3:2 / etc., and the SOURCE image's own
        w/h for Original (Nelson 2026-06-09 — Original means "keep the
        photo's aspect but still crop"). Final clamp keeps the dragged
        corner within ``[0, 1]``.
        """
        if self._rect_norm is None or self._drag_anchor is None:
            return
        ir = self._image_rect
        if ir.width() <= 0 or ir.height() <= 0:
            return

        anchor_norm_x = (self._drag_anchor.x() - ir.left()) / ir.width()
        anchor_norm_y = (self._drag_anchor.y() - ir.top()) / ir.height()
        mouse_norm_x = (mouse_pos.x() - ir.left()) / ir.width()
        mouse_norm_y = (mouse_pos.y() - ir.top()) / ir.height()

        anchor_norm_x = max(0.0, min(1.0, anchor_norm_x))
        anchor_norm_y = max(0.0, min(1.0, anchor_norm_y))
        mouse_norm_x = max(0.0, min(1.0, mouse_norm_x))
        mouse_norm_y = max(0.0, min(1.0, mouse_norm_y))

        cand_w = abs(mouse_norm_x - anchor_norm_x)
        cand_h = abs(mouse_norm_y - anchor_norm_y)

        # Resolve the lock ratio. For Original, fall back to the source
        # image's own w/h so resize keeps composition rather than
        # wandering off-shape. Defensive: if image_pixel_size hasn't
        # been wired yet, skip the lock (freeform).
        if self._aspect_ratio.is_original:
            src_w, src_h = self._image_pixel_size
            target_ratio = (
                (src_w / src_h) if (src_w > 0 and src_h > 0) else None)
        else:
            target_ratio = self._aspect_ratio.value

        if target_ratio is not None:
            # Lock to ratio. Aspect ratio is in *image pixels*, so
            # convert the candidate w/h back to pixel ratio before
            # comparing: cand_w * iw vs cand_h * ih.
            iw = max(1, ir.width())                 # widget rect as proxy
            ih = max(1, ir.height())
            cand_pw = cand_w * iw
            cand_ph = cand_h * ih
            if cand_ph * target_ratio >= cand_pw:
                cand_pw = cand_ph * target_ratio
                cand_w = cand_pw / iw
            else:
                cand_ph = cand_pw / target_ratio
                cand_h = cand_ph / ih

        cand_w = max(_MIN_RECT_NORM, cand_w)
        cand_h = max(_MIN_RECT_NORM, cand_h)

        # New top-left depends on which corner is being dragged.
        if self._drag_mode == _DragMode.RESIZE_TL:
            new_x = anchor_norm_x - cand_w
            new_y = anchor_norm_y - cand_h
        elif self._drag_mode == _DragMode.RESIZE_TR:
            new_x = anchor_norm_x
            new_y = anchor_norm_y - cand_h
        elif self._drag_mode == _DragMode.RESIZE_BL:
            new_x = anchor_norm_x - cand_w
            new_y = anchor_norm_y
        else:                                       # RESIZE_BR
            new_x = anchor_norm_x
            new_y = anchor_norm_y

        new_x = max(0.0, min(1.0 - cand_w, new_x))
        new_y = max(0.0, min(1.0 - cand_h, new_y))
        self._rect_norm = (new_x, new_y, cand_w, cand_h)
        self.update()

    # ── Geometry helpers ──────────────────────────────────────────

    def _norm_to_widget(
        self, rect_norm: tuple[float, float, float, float],
    ) -> QRect:
        ir = self._image_rect
        x, y, w, h = rect_norm
        return QRect(
            int(round(ir.left() + x * ir.width())),
            int(round(ir.top() + y * ir.height())),
            int(round(w * ir.width())),
            int(round(h * ir.height())),
        )

    def _to_local(self, pos: QPointF) -> QPointF:
        """Map a widget point into the box's UN-rotated frame (inverse
        of the paint-time rotation about the box centre). Identity when
        not rotated."""
        if not self._angle:
            return QPointF(pos)
        c = self._norm_to_widget(self._rect_norm).center()
        sx = pos.x() - c.x()
        sy = pos.y() - c.y()
        a = math.radians(self._angle)
        cos, sin = math.cos(a), math.sin(a)
        lx = sx * cos + sy * sin
        ly = -sx * sin + sy * cos
        return QPointF(lx + c.x(), ly + c.y())

    def _hit_test(self, pos) -> _DragMode:
        """Return which interactive zone is under ``pos`` — the rotation
        handle first (it floats outside the rect), then corners (so they
        win over the interior when overlapping), then the rect interior.
        Anywhere else is NONE.

        When the box is rotated, ROTATE + MOVE are offered (resize is a
        0° operation in this v1); the point is mapped into the box's
        un-rotated frame first so every zone tracks the rotated outline."""
        if self._rect_norm is None:
            return _DragMode.NONE
        rect_widget = self._norm_to_widget(self._rect_norm)
        local = self._to_local(pos)
        hl = self._rotate_handle_local(rect_widget)
        if (abs(local.x() - hl.x()) <= _HANDLE_HIT_RADIUS
                and abs(local.y() - hl.y()) <= _HANDLE_HIT_RADIUS):
            return _DragMode.ROTATE
        if self._angle:
            if rect_widget.contains(QPoint(int(local.x()), int(local.y()))):
                return _DragMode.MOVE
            return _DragMode.NONE
        px, py = pos.x(), pos.y()
        for mode, (cx, cy) in zip(
            (_DragMode.RESIZE_TL, _DragMode.RESIZE_TR,
             _DragMode.RESIZE_BL, _DragMode.RESIZE_BR),
            self._corner_centers(rect_widget),
        ):
            if (
                abs(px - cx) <= _HANDLE_HIT_RADIUS
                and abs(py - cy) <= _HANDLE_HIT_RADIUS
            ):
                return mode
        if rect_widget.contains(QPoint(int(px), int(py))):
            return _DragMode.MOVE
        return _DragMode.NONE
