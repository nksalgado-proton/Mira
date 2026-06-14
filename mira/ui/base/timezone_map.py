"""TimezoneMapWidget — flattened world map with the trip's TZ band.

**Reused verbatim from the legacy ``ui/base/timezone_map.py`` (charter §0/§5.2).** Pure
Qt render — no ``core``/``ui`` data dependencies — so this is a byte-for-byte copy (no
import swaps needed). Consumed by the ported ``TwoByTwoOverview`` top-left quadrant.

Used on the Plan PhaseButton card. Per Nelson 2026-05-21: "In the
plan just draw a flattened word map coloring the trip timezone".

Implementation
--------------
A Plate-Carrée-style rectangle (2:1 longitude:latitude) with:

* An ocean-blue background
* Hand-drawn continent rectangles in earth-green — deliberately
  rough; the visual purpose is "this is a world map", not
  cartographic accuracy
* A vertical band 15° wide (one timezone hour) at the trip's TZ
  longitude, filled in Gulf orange and stroked so it stands out
* Centre text showing ``UTC±N`` in bold

Coordinate system: longitude maps -180..+180 → 0..widget_width
(left = -180, right = +180). The timezone offset in hours
multiplies by 15° to get the longitude (e.g. UTC+3 = +45°; the
band centre lands at 60% of the width).

The widget is a child of the PhaseButton, sized the same way as
the existing :class:`_DonutWidget` (square aspect via
``heightForWidth``) so the layout doesn't need special casing.
The 2:1 map is rendered inside that square with letterboxing.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget


# Palette — distinct from the donut's progress colours so the map
# reads as a different KIND of visualisation. Ocean blue + earth
# green + a Gulf-orange highlight band lift it out of the chart-
# colours conversation.
_OCEAN = QColor("#A9D6E5")        # light teal
_LAND = QColor("#80B17B")         # earthy green
_TZ_BAND = QColor("#F37021")      # Gulf orange — same accent as
                                   # PrimaryButton + EventCard CTAs
_TZ_BAND_STROKE = QColor("#C75A1C")
_LAT_LON_LINE = QColor(255, 255, 255, 100)


# Crude continent outlines as ``(x_pct, y_pct, w_pct, h_pct)``
# rectangles in [0, 1] map coordinates (left = -180, right = +180,
# top = +90, bottom = -90). These are NOT geographically accurate
# — they're "this looks like Earth at a glance". Visual purpose
# only.
_CONTINENT_RECTS: tuple[tuple[float, float, float, float], ...] = (
    # North America: roughly -130..-65 W, 10..70 N
    (0.14, 0.18, 0.21, 0.30),
    # South America: roughly -82..-35 W, -55..12 N
    (0.27, 0.45, 0.15, 0.35),
    # Africa: roughly -18..52 E, -35..37 N
    (0.45, 0.30, 0.20, 0.40),
    # Europe (small block): roughly -10..40 E, 36..70 N
    (0.47, 0.10, 0.14, 0.20),
    # Asia: roughly 30..150 E, 5..70 N
    (0.58, 0.10, 0.30, 0.40),
    # Australia: roughly 113..155 E, -40..-10 N
    (0.78, 0.58, 0.12, 0.15),
    # Antarctica: bottom strip
    (0.0, 0.90, 1.0, 0.10),
    # Greenland: small cap
    (0.31, 0.06, 0.06, 0.10),
)


class TimezoneMapWidget(QWidget):
    """Flattened world map highlighting one timezone band.

    Public API:
      * :meth:`set_timezone` — TZ offset in hours (-12..+14). A
        fractional offset like ``-3.5`` (Newfoundland) is honoured
        by half-shifting the band centre.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tz_offset: float | None = None
        # F-025 v3 (Nelson 2026-05-26): label is optional. The Plan
        # PhaseButton still wants it (the chart's caption is
        # "UTC±N"); the closed EventCard's recap suppresses it so
        # the map fills the column without 38% of the height stolen
        # by centre text.
        self._show_label: bool = True
        self.setMinimumSize(120, 80)
        self.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.MinimumExpanding,
        )

    # ── Public API ────────────────────────────────────────────────

    def set_timezone(self, tz_offset: float | None) -> None:
        """Update the highlighted band. ``None`` = no plan / no TZ
        known yet → renders the map without a highlight + a centre
        dash."""
        self._tz_offset = (
            float(tz_offset) if tz_offset is not None else None
        )
        self._refresh_tooltip()
        self.update()

    def set_show_label(self, show: bool) -> None:
        """Toggle the centre ``UTC±N`` label. When ``False`` the
        map fills the whole widget area (no 38% reserved for text).
        Defaults to ``True`` — Plan PhaseButton's existing use is
        unchanged."""
        if bool(show) != self._show_label:
            self._show_label = bool(show)
            self.update()

    # ── Qt overrides ──────────────────────────────────────────────

    def heightForWidth(self, w: int) -> int:    # noqa: N802
        # The map is 2:1 (lon:lat). Inside a square widget the map
        # is letterboxed; ``heightForWidth`` still returns w so the
        # widget stays square-friendly to the parent layout.
        return w

    def hasHeightForWidth(self) -> bool:        # noqa: N802
        return True

    def paintEvent(self, _event) -> None:       # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # Letterbox a 2:1 map centred in the widget.
            # When the label is hidden (F-025 v3) the map fills
            # the whole available area; otherwise we reserve ~38%
            # of the height for the centre text below.
            if self._show_label:
                map_h = self.height() * 0.62
                vertical_offset = -self.height() * 0.06
            else:
                map_h = self.height() * 0.96
                vertical_offset = 0.0
            map_w = map_h * 2.0
            if map_w > self.width() * 0.96:
                map_w = self.width() * 0.96
                map_h = map_w / 2.0
            x = (self.width() - map_w) / 2.0
            y = (self.height() - map_h) / 2.0 + vertical_offset
            map_rect = QRectF(x, y, map_w, map_h)

            # Ocean.
            p.fillRect(map_rect, _OCEAN)

            # Continents.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_LAND)
            for (cx, cy, cw, ch) in _CONTINENT_RECTS:
                c_rect = QRectF(
                    map_rect.left() + cx * map_rect.width(),
                    map_rect.top() + cy * map_rect.height(),
                    cw * map_rect.width(),
                    ch * map_rect.height(),
                )
                p.drawRoundedRect(c_rect, 3.0, 3.0)

            # Lat/lon grid — equator + tropics, dateline + meridian.
            pen = QPen(_LAT_LON_LINE)
            pen.setWidth(1)
            p.setPen(pen)
            # Equator + dateline / meridian.
            mid_y = map_rect.top() + map_rect.height() / 2.0
            mid_x = map_rect.left() + map_rect.width() / 2.0
            p.drawLine(
                int(map_rect.left()), int(mid_y),
                int(map_rect.right()), int(mid_y),
            )
            p.drawLine(
                int(mid_x), int(map_rect.top()),
                int(mid_x), int(map_rect.bottom()),
            )

            # Timezone band.
            if self._tz_offset is not None:
                # Longitude (°) = tz_offset * 15. Map: -180° = left,
                # +180° = right. Band width = 15° = 1/24 of the map.
                centre_lon = max(-180.0, min(180.0,
                    float(self._tz_offset) * 15.0,
                ))
                band_w_norm = 1.0 / 24.0
                # The band's centre normalised x position.
                cx_norm = (centre_lon + 180.0) / 360.0
                band_left = map_rect.left() + (
                    cx_norm - band_w_norm / 2.0
                ) * map_rect.width()
                band_rect = QRectF(
                    band_left, map_rect.top(),
                    band_w_norm * map_rect.width(),
                    map_rect.height(),
                )
                p.setBrush(_TZ_BAND)
                pen = QPen(_TZ_BAND_STROKE)
                pen.setWidth(1)
                p.setPen(pen)
                p.drawRect(band_rect)

            # Map border.
            pen = QPen(QColor(0, 0, 0, 80))
            pen.setWidth(1)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(map_rect)

            # Centre text under the map — only when the label is
            # enabled (F-025 v3, Nelson 2026-05-26: the EventCard
            # recap suppresses it so the map fills the column).
            if self._show_label:
                text_rect = QRectF(
                    0, map_rect.bottom() + 4,
                    self.width(),
                    self.height() - map_rect.bottom() - 8,
                )
                font = QFont(p.font())
                font.setBold(True)
                # Lower floor keeps it legible on the small EventCard recap;
                # upper cap stops it ballooning on the larger rebuild dashboard
                # tiles (Nelson 2026-06-01).
                font.setPointSizeF(
                    min(12.0, max(9.0, min(self.width(), self.height()) * 0.14)))
                p.setFont(font)
                p.setPen(self.palette().text().color())
                p.drawText(
                    text_rect, Qt.AlignmentFlag.AlignCenter,
                    self._compose_label(),
                )
        finally:
            p.end()

    # ── Helpers ───────────────────────────────────────────────────

    def _compose_label(self) -> str:
        if self._tz_offset is None:
            return "—"
        hours = int(self._tz_offset)
        minutes = int(round((abs(self._tz_offset) - abs(hours)) * 60))
        if minutes == 0:
            return f"UTC{hours:+d}"
        return f"UTC{hours:+d}:{minutes:02d}"

    def _refresh_tooltip(self) -> None:
        if self._tz_offset is None:
            self.setToolTip("")
            return
        self.setToolTip(
            f"Trip timezone: {self._compose_label()}"
        )
