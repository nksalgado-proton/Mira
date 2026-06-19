"""Thumb — the capture-card widget for Days Grid + filmstrips.

The §5a/§5b §3 contract:

    Border (3px) = STATE        picked/skipped/compare/mixed/neutral — locked
                                colors from PALETTE; NEVER restyled to the
                                accent palette.
    Top-left      = cluster icon + label badge (clusters only)
    Top-right     = visited eye chip (translucent dark, white outline eye)
    Bottom-left   = exported badge ("↑ Exported", solid accent) — replaces
                                the old diagonal watermark entirely.
    Bottom-right  = cluster count chip (×N) or split chip (3✓·2✗ when mixed)
    Body          = blurred-fill backdrop + contained photo (KeepAspectRatio,
                                never cropped). Cached blurred pixmap stored
                                per-instance for now; the production wiring
                                hooks into the existing PhotoCache singleton
                                (see [[design_rule_photo_cache_architecture]]).

The state border is PAINTED — not QSS — because it's load-bearing semantic
(green / red / orange / yellow / line) and must paint deterministically with
exact colors from PALETTE, never the theme accent.

Cluster cover styling: when ``cluster_type`` is set, two offset card-shaped
frames sit BEHIND the main image, creating the "pile of photos" read before
any text. Driven by paintEvent — no extra widgets.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from mira.ui.palette import PALETTE


_STATE_KEY = {
    "picked": "picked",
    "skipped": "skipped",
    # ``"compare"`` is the visual-colour name; ``"candidate"`` is the
    # persisted phase_state value (mira/picked/status.py). Some call
    # sites (the Pick cycle in DaysGridPage) call ``setState`` with the
    # raw phase value rather than mapping it through
    # ``cell_color_for_item`` first. Accept both so the cycle's
    # ``"candidate"`` landing doesn't KeyError mid-paintEvent
    # (Nelson 2026-06-18 — uncaught in the peek/border-click cycle).
    "compare": "compare",
    "candidate": "compare",
    "mixed": "mixed",
    None: "line",
}
_CLUSTER_LABELS = {
    "repeated": "Repeated",
    "burst": "Burst shot",
    "focus": "Focus bracket",
    "exposure": "Exposure bracket",
    # spec/56 Export-mode clustering — clips + snapshots of one source
    # video are grouped under a "video" cluster cover so a day with a
    # workshopped video reads as ONE pile, drillable to its members.
    "video": "Video",
    # spec/89 Slice 5 — items with 2+ shipped lineage rows surface as
    # a versions cluster cover so the user can compare + decide which
    # version(s) to ship.
    "versions": "Versions",
}

# Per-cell type stamp — the spec/56 "Video Clip" / "Snapshot" badge a
# child cell wears inside a video cluster sub-grid. Mirrors the cluster
# badge slot (top-left chip), so a parent video cluster cover shows its
# "Video" cluster badge and its children show what KIND of unit each
# is. Keys map to the line-icon glyph alongside the label.
_STAMP_LABELS = {
    "clip": "Video Clip",
    "snapshot": "Snapshot",
}
_BADGE_DIR = (
    Path(__file__).resolve().parents[3] / "assets" / "icons" / "clusters" / "badge"
)


def _cluster_icon_path(kind: str) -> Path | None:
    p = _BADGE_DIR / f"{kind}.svg"
    return p if p.exists() else None


class Thumb(QWidget):
    """Grid / filmstrip capture tile.

    Signals:
        clicked()   — left-click anywhere on the tile

    Properties set via constructor and updatable through setters:
        pixmap          QPixmap or None
        state           "picked" | "skipped" | "compare" | "mixed" | None
        cluster_type    "repeated"|"burst"|"focus"|"exposure"|"video" | None
        cluster_count   int (for the bottom-right chip on clusters)
        cluster_split   tuple[int,int] (picked, skipped) when mixed — overrides
                        the count chip with a 3✓·2✗ split chip per §5a.
        visited         bool — adds the top-right eye chip
        exported        bool — adds the bottom-left accent badge
        edit_reasons    tuple[str,...] — reasons the photo is edited
                        ('look'/'filter'/'crop'); rendered as one amber
                        bottom-left pill of small glyphs, stacking above
                        the exported badge when both apply
        border_token    str | None — overrides the state border colour with
                        a PALETTE token (Edit grid: 'green' unedited /
                        'amber' edited). None → the state border is used.
        stamp           "clip" | "snapshot" | None — type stamp shown on
                        Export-mode child cells (a clip / snapshot inside a
                        video cluster). Same top-left chip slot as the
                        cluster badge; never shown simultaneously with one.
    """

    clicked = pyqtSignal()

    def __init__(
        self,
        pixmap: QPixmap | None = None,
        *,
        state: str | None = None,
        size: QSize = QSize(200, 150),
        cluster_type: str | None = None,
        cluster_count: int = 0,
        cluster_split: tuple[int, int] | None = None,
        visited: bool = False,
        exported: bool = False,
        edit_reasons: tuple[str, ...] = (),
        border_token: str | None = None,
        stamp: str | None = None,
        origin: str | None = None,
        skipped_in_pick: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._state = state
        self._cluster_type = cluster_type
        self._cluster_count = cluster_count
        self._cluster_split = cluster_split
        self._visited = visited
        self._exported = exported
        self._edit_reasons = tuple(edit_reasons or ())
        self._border_token = border_token
        self._stamp = stamp
        # spec/89 §2.1 Block 2 D3.B — origin wordmark (Mira / LRC /
        # Helicon / CO / ext) painted on a thin strip under the thumb
        # for Export cells. ``None`` keeps the cell badge-free
        # (0-version cells, Pick / Edit phases).
        self._origin = origin
        # spec/89 §4.2 Block 7 D2.B — "skipped in Pick" indicator chip
        # shown on Export cells that entered the pool only because a
        # ship row exists. ``False`` keeps the cell clean.
        self._skipped_in_pick = bool(skipped_in_pick)
        self._blurred_cache: QPixmap | None = None
        self.setFixedSize(size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def setPixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self._blurred_cache = None
        self.update()

    def setState(self, state: str | None) -> None:
        self._state = state
        self.update()

    def setVisited(self, visited: bool) -> None:
        self._visited = visited
        self.update()

    def setExported(self, exported: bool) -> None:
        self._exported = exported
        self.update()

    def setEditReasons(self, reasons) -> None:
        self._edit_reasons = tuple(reasons or ())
        self.update()

    def setBorderToken(self, token: str | None) -> None:
        self._border_token = token
        self.update()

    def setStamp(self, stamp: str | None) -> None:
        self._stamp = stamp
        self.update()

    def setOrigin(self, origin: str | None) -> None:
        """spec/89 §2.1 — origin wordmark (Mira / LRC / Helicon / CO /
        ext). Set ``None`` to clear the strip."""
        self._origin = origin
        self.update()

    def setSkippedInPick(self, flag: bool) -> None:
        """spec/89 §4.2 / Block 7 D2.B — "skipped in Pick" indicator
        chip for Export-mode cells that are only in the pool because a
        ship row exists."""
        self._skipped_in_pick = bool(flag)
        self.update()

    def setClusterCount(self, n: int) -> None:
        self._cluster_count = n
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt override
        """Invalidate the cached blurred backdrop when the Thumb's size
        changes (Nelson 2026-06-18). The cache was sized to ``width+24,
        height+24`` at first paint, so without this the blur stayed at
        the original tile dimensions and a slider-driven enlargement
        showed a small blurred patch in the centre of a larger cell."""
        self._blurred_cache = None
        super().resizeEvent(event)

    # ── painting ────────────────────────────────────────────────────────

    def _blurred_backdrop(self) -> QPixmap | None:
        """Pre-blur the source pixmap once per Thumb. The production-grade
        cache hooks into PhotoCache; this in-memory cache keeps the catalog
        smoke + small grids cheap."""
        if self._pixmap is None or self._pixmap.isNull():
            return None
        if self._blurred_cache is not None:
            return self._blurred_cache
        # Downscale to tile-ish resolution, lift saturation/brightness down a
        # touch, then up-scale back to fill — fast approximation of blur.
        src = self._pixmap.scaled(
            32, 32,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        upscaled = src.scaled(
            self.width() + 24, self.height() + 24,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Darken via QImage alpha mask
        img = upscaled.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(img.rect(), QColor(0, 0, 0, 110))
        p.end()
        self._blurred_cache = QPixmap.fromImage(img)
        return self._blurred_cache

    def paintEvent(self, _evt) -> None:  # noqa: N802
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        palette = PALETTE[mode]
        # The border colour is normally the decision STATE; an explicit
        # border_token overrides it (Edit grid: 'green' unedited / 'amber'
        # edited — the load-bearing edited signal, Nelson 2026-06-18).
        if self._border_token:
            state_color = QColor(
                palette.get(self._border_token, palette[_STATE_KEY[self._state]]))
        else:
            state_color = QColor(palette[_STATE_KEY[self._state]])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0, 0, self.width(), self.height())
        radius = 12.0
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        painter.setClipPath(clip)

        # Cluster pile: two offset rounded rects behind the cover, before the
        # backdrop draws, so they read as papers under the main image.
        if self._cluster_type is not None:
            offset_color = QColor(palette["card2"])
            for dx, dy, alpha in ((10, 10, 90), (5, 5, 160)):
                r = QRectF(rect.adjusted(dx, dy, -dx, -dy))
                col = QColor(offset_color)
                col.setAlpha(alpha)
                painter.setPen(QPen(QColor(palette["line"]), 1))
                painter.setBrush(QBrush(col))
                painter.drawRoundedRect(r, radius, radius)

        # Backdrop (blurred fill) — only when we actually have a photo
        backdrop = self._blurred_backdrop()
        if backdrop is not None:
            painter.drawPixmap(
                int(-12), int(-12),
                backdrop.width(), backdrop.height(), backdrop,
            )
        else:
            painter.fillRect(rect, QColor(palette["card2"]))

        # Contained photo — KeepAspectRatio, centered
        if self._pixmap is not None and not self._pixmap.isNull():
            inner_pad = 6
            inner = rect.adjusted(inner_pad, inner_pad, -inner_pad, -inner_pad)
            scaled = self._pixmap.scaled(
                int(inner.width()), int(inner.height()),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = inner.x() + (inner.width() - scaled.width()) / 2
            y = inner.y() + (inner.height() - scaled.height()) / 2
            painter.drawPixmap(int(x), int(y), scaled)
        else:
            # No image — paint a soft accent placeholder
            painter.fillRect(rect.adjusted(8, 8, -8, -8), QColor(palette["card"]))

        # Disable clip for border draw so the stroke isn't cut in half
        painter.setClipping(False)

        # 3px state border on the rounded outline
        painter.setBrush(Qt.BrushStyle.NoBrush)
        border_pen = QPen(state_color, 3)
        border_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(border_pen)
        painter.drawRoundedRect(
            rect.adjusted(1.5, 1.5, -1.5, -1.5), radius - 1.5, radius - 1.5,
        )

        # Overlays
        self._paint_visited(painter, palette)
        self._paint_edit_reasons(painter, palette)
        self._paint_exported(painter, palette)
        self._paint_cluster_badge(painter, palette)
        self._paint_type_stamp(painter, palette)
        self._paint_count_chip(painter, palette)
        self._paint_origin_strip(painter, palette)
        self._paint_skipped_in_pick(painter, palette)

        painter.end()

    # ── overlay paints ──────────────────────────────────────────────────

    def _paint_chip(
        self,
        painter: QPainter,
        rect: QRectF,
        bg: QColor,
        border: QColor | None,
    ) -> None:
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(border, 1) if border else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 10, 10)

    def _paint_origin_strip(
        self, painter: QPainter, palette: dict[str, str]
    ) -> None:
        """spec/89 §2.1 / Block 2 D3.B — a thin strip under the thumb
        carrying the origin wordmark (Mira / LRC / Helicon / CO / ext).
        Painted as a centred chip near the bottom edge; theme-tinted so
        it stays legible on either palette."""
        if not self._origin:
            return
        f = painter.font()
        f.setPointSizeF(8.5)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        label_w = fm.horizontalAdvance(self._origin)
        pad_x = 8
        chip_w = label_w + pad_x * 2
        chip_h = 18
        chip_rect = QRectF(
            (self.width() - chip_w) / 2,
            self.height() - chip_h - 8,
            chip_w, chip_h,
        )
        self._paint_chip(
            painter, chip_rect,
            QColor(8, 10, 16, 188), QColor(255, 255, 255, 46),
        )
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            chip_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            self._origin,
        )

    def _paint_skipped_in_pick(
        self, painter: QPainter, palette: dict[str, str]
    ) -> None:
        """spec/89 §4.2 / Block 7 D2.B — small chip telling the user
        why an item is appearing in the Export pool even though it was
        skipped in Pick: it has a file under Exported Media/."""
        if not self._skipped_in_pick:
            return
        f = painter.font()
        f.setPointSizeF(8.5)
        f.setBold(True)
        painter.setFont(f)
        label = "skipped in Pick"
        fm = painter.fontMetrics()
        label_w = fm.horizontalAdvance(label)
        pad_x = 8
        chip_w = label_w + pad_x * 2
        chip_h = 18
        chip_rect = QRectF(8, self.height() - chip_h - 8, chip_w, chip_h)
        # Faintly amber-tinted so it reads as informational, not danger.
        bg = QColor(palette.get("amber", "#fbbf24"))
        bg.setAlpha(220)
        self._paint_chip(painter, chip_rect, bg, QColor(0, 0, 0, 70))
        painter.setPen(QColor("#08101c"))
        painter.drawText(chip_rect, int(Qt.AlignmentFlag.AlignCenter), label)

    def _paint_visited(self, painter: QPainter, palette: dict[str, str]) -> None:
        if not self._visited:
            return
        chip_rect = QRectF(self.width() - 36, 8, 28, 22)
        self._paint_chip(
            painter, chip_rect,
            QColor(8, 10, 16, 188), QColor(255, 255, 255, 46),
        )
        # Eye glyph (rough): horizontal ellipse + center dot
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        eye_rect = QRectF(
            chip_rect.x() + 6, chip_rect.y() + 6,
            chip_rect.width() - 12, chip_rect.height() - 12,
        )
        painter.drawEllipse(eye_rect)
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        cx = chip_rect.x() + chip_rect.width() / 2
        cy = chip_rect.y() + chip_rect.height() / 2
        painter.drawEllipse(QRectF(cx - 1.6, cy - 1.6, 3.2, 3.2))

    def _paint_exported(self, painter: QPainter, palette: dict[str, str]) -> None:
        if not self._exported:
            return
        chip_rect = QRectF(8, self.height() - 28, 88, 20)
        self._paint_chip(painter, chip_rect, QColor(palette["accent"]), None)
        painter.setPen(QPen(QColor("#ffffff"), 1.4))
        # Up-arrow glyph + "Exported"
        ax = chip_rect.x() + 8
        ay = chip_rect.y() + chip_rect.height() / 2
        painter.drawLine(int(ax), int(ay + 4), int(ax), int(ay - 4))
        painter.drawLine(int(ax), int(ay - 4), int(ax - 3), int(ay - 1))
        painter.drawLine(int(ax), int(ay - 4), int(ax + 3), int(ay - 1))
        painter.setPen(QColor("#ffffff"))
        f = painter.font()
        f.setPointSizeF(9.5)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(
            chip_rect.adjusted(18, 0, 0, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            "Exported",
        )

    # Glyph order matches core.edit_status.REASON_ORDER (look, filter, crop).
    _EDIT_GLYPHS = ("look", "filter", "crop")

    def _paint_edit_reasons(
        self, painter: QPainter, palette: dict[str, str]
    ) -> None:
        """Bottom-left **amber** edit badge — one pill carrying a small dark
        glyph per reason the photo is edited (Look / Filter / Crop, in
        order). Amber matches the edited border + the Days-Lists Edited bar.
        Stacks one row above the exported badge when both are present
        (Nelson 2026-06-18). The full reason names ride the cell tooltip,
        set by the host."""
        reasons = [r for r in self._EDIT_GLYPHS if r in self._edit_reasons]
        if not reasons:
            return
        glyph_w, pad, gap = 14, 7, 4
        chip_w = pad * 2 + len(reasons) * glyph_w + (len(reasons) - 1) * gap
        bottom = self.height() - (52 if self._exported else 28)
        chip_rect = QRectF(8, bottom, chip_w, 20)
        self._paint_chip(
            painter, chip_rect, QColor(palette.get("amber", "#fbbf24")), None)
        ink = QColor("#241a02")    # dark ink — readable on amber
        cy = chip_rect.y() + chip_rect.height() / 2
        x = chip_rect.x() + pad
        for r in reasons:
            self._paint_edit_glyph(painter, r, x + glyph_w / 2, cy, ink)
            x += glyph_w + gap

    def _paint_edit_glyph(
        self, painter: QPainter, reason: str, cx: float, cy: float,
        ink: QColor,
    ) -> None:
        """One ~12px reason glyph centred at (cx, cy), drawn in ``ink``."""
        painter.setPen(QPen(ink, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if reason == "look":
            # Tonal disc — circle outline with the left half filled.
            r = 5.0
            ring = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            painter.drawEllipse(ring)
            path = QPainterPath()
            path.moveTo(cx, cy - r)
            path.arcTo(ring, 90, 180)   # left semicircle
            path.closeSubpath()
            painter.fillPath(path, ink)
        elif reason == "filter":
            # Funnel — wide top, converging to a short stem.
            painter.drawLine(int(cx - 5), int(cy - 4), int(cx + 5), int(cy - 4))
            painter.drawLine(int(cx - 5), int(cy - 4), int(cx - 1), int(cy + 1))
            painter.drawLine(int(cx + 5), int(cy - 4), int(cx + 1), int(cy + 1))
            painter.drawLine(int(cx - 1), int(cy + 1), int(cx - 1), int(cy + 5))
            painter.drawLine(int(cx + 1), int(cy + 1), int(cx + 1), int(cy + 5))
        else:  # crop — two overlapping corner brackets
            painter.drawLine(int(cx - 5), int(cy - 2), int(cx - 5), int(cy + 5))
            painter.drawLine(int(cx - 5), int(cy + 5), int(cx + 2), int(cy + 5))
            painter.drawLine(int(cx + 5), int(cy + 2), int(cx + 5), int(cy - 5))
            painter.drawLine(int(cx + 5), int(cy - 5), int(cx - 2), int(cy - 5))

    def _paint_cluster_badge(
        self, painter: QPainter, palette: dict[str, str]
    ) -> None:
        if self._cluster_type is None:
            return
        label = _CLUSTER_LABELS.get(self._cluster_type, self._cluster_type)
        chip_rect = QRectF(8, 8, 130, 24)
        self._paint_chip(
            painter, chip_rect,
            QColor(8, 10, 16, 188), QColor(255, 255, 255, 46),
        )
        # Icon
        icon_path = _cluster_icon_path(self._cluster_type)
        if icon_path is not None:
            renderer = QSvgRenderer(str(icon_path))
            painter.save()
            icon_rect = QRectF(
                chip_rect.x() + 4, chip_rect.y() + 2, 20, 20,
            )
            # Tint the SVG white by drawing it onto a transparent QImage,
            # then composing CompositionMode_SourceIn with white.
            buf = QImage(20, 20, QImage.Format.Format_ARGB32)
            buf.fill(0)
            ip = QPainter(buf)
            renderer.render(ip)
            ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            ip.fillRect(buf.rect(), QColor("#ffffff"))
            ip.end()
            painter.drawImage(icon_rect, buf)
            painter.restore()
        # Label
        painter.setPen(QColor("#ffffff"))
        f = painter.font()
        f.setPointSizeF(9.5)
        f.setBold(True)
        painter.setFont(f)
        text_rect = chip_rect.adjusted(28, 0, 0, 0)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            label,
        )

    def _paint_type_stamp(
        self, painter: QPainter, palette: dict[str, str]
    ) -> None:
        """Top-left "Video Clip" / "Snapshot" type stamp for Export-mode
        child cells (spec/56). Shares the chip slot with the cluster
        badge but is never set on the same cell (a parent cluster cover
        has a cluster_type; its children have stamps), so the two slots
        never collide. Uses the line-icon family via tinted_svg_pixmap,
        matching the split chip's pattern (theme-aware tint, cached)."""
        if self._stamp is None or self._cluster_type is not None:
            return
        label = _STAMP_LABELS.get(self._stamp)
        if label is None:
            return
        from mira.ui.design.icons import (
            GLYPH_CLIP, GLYPH_SNAPSHOT, tinted_svg_pixmap,
        )

        glyph_path = (
            GLYPH_CLIP if self._stamp == "clip" else GLYPH_SNAPSHOT)
        glyph_size = 16
        spacing = 6
        pad_x = 8
        f = painter.font()
        f.setPointSizeF(9.5)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        label_w = fm.horizontalAdvance(label)
        chip_w = pad_x + glyph_size + spacing + label_w + pad_x
        chip_rect = QRectF(8, 8, chip_w, 24)
        self._paint_chip(
            painter, chip_rect,
            QColor(8, 10, 16, 188), QColor(255, 255, 255, 46),
        )
        white = QColor("#ffffff")
        glyph_pm = tinted_svg_pixmap(glyph_path, glyph_size, white)
        glyph_x = chip_rect.x() + pad_x
        glyph_y = chip_rect.y() + (chip_rect.height() - glyph_size) / 2
        painter.drawPixmap(int(glyph_x), int(glyph_y), glyph_pm)
        text_x = chip_rect.x() + pad_x + glyph_size + spacing
        painter.setPen(white)
        painter.drawText(
            QRectF(text_x, chip_rect.y(),
                   chip_rect.right() - text_x - pad_x, chip_rect.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            label,
        )

    def _paint_count_chip(
        self, painter: QPainter, palette: dict[str, str]
    ) -> None:
        if self._cluster_type is None and self._cluster_split is None:
            return
        if self._cluster_split is not None:
            self._paint_split_chip(painter)
            return
        if self._cluster_count <= 0:
            return
        text = f"×{self._cluster_count}"
        chip_w = 38
        chip_rect = QRectF(
            self.width() - chip_w - 8,
            self.height() - 28,
            chip_w, 20,
        )
        self._paint_chip(
            painter, chip_rect,
            QColor(8, 10, 16, 188), QColor(255, 255, 255, 46),
        )
        painter.setPen(QColor("#ffffff"))
        f = painter.font()
        f.setPointSizeF(10)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(
            chip_rect,
            int(Qt.AlignmentFlag.AlignCenter), text,
        )

    def _paint_split_chip(self, painter: QPainter) -> None:
        """spec/69 — the mixed-cluster split chip: a count, the
        line-icon check, an interpunct separator, the second count,
        the line-icon cross. Replaces the Unicode `3✓·2✗` placeholder
        so the symbols match the rest of the line-icon family."""
        from mira.ui.design.icons import (
            GLYPH_CHECK, GLYPH_CROSS, tinted_svg_pixmap,
        )

        picked, skipped = self._cluster_split  # type: ignore[misc]
        picked_text = str(picked)
        skipped_text = str(skipped)
        sep = "·"

        f = painter.font()
        f.setPointSizeF(10)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        # Glyph + text dimensions — same line-height as the count chip
        # so it sits on the same baseline as the ×N variant.
        glyph_size = 12
        spacing = 3
        sep_gap = 5
        picked_w = fm.horizontalAdvance(picked_text)
        skipped_w = fm.horizontalAdvance(skipped_text)
        sep_w = fm.horizontalAdvance(sep)
        content_w = (
            picked_w + spacing + glyph_size
            + sep_gap + sep_w + sep_gap
            + skipped_w + spacing + glyph_size
        )
        pad_x = 8
        chip_w = content_w + pad_x * 2
        chip_rect = QRectF(
            self.width() - chip_w - 8,
            self.height() - 28,
            chip_w, 20,
        )
        self._paint_chip(
            painter, chip_rect,
            QColor(8, 10, 16, 188), QColor(255, 255, 255, 46),
        )

        white = QColor("#ffffff")
        check_pm = tinted_svg_pixmap(GLYPH_CHECK, glyph_size, white)
        cross_pm = tinted_svg_pixmap(GLYPH_CROSS, glyph_size, white)
        text_y = chip_rect.y() + (chip_rect.height() + fm.ascent()) / 2 - 2
        glyph_y = chip_rect.y() + (chip_rect.height() - glyph_size) / 2
        cursor_x = chip_rect.x() + pad_x

        painter.setPen(white)
        painter.drawText(int(cursor_x), int(text_y), picked_text)
        cursor_x += picked_w + spacing
        painter.drawPixmap(int(cursor_x), int(glyph_y), check_pm)
        cursor_x += glyph_size + sep_gap
        painter.drawText(int(cursor_x), int(text_y), sep)
        cursor_x += sep_w + sep_gap
        painter.drawText(int(cursor_x), int(text_y), skipped_text)
        cursor_x += skipped_w + spacing
        painter.drawPixmap(int(cursor_x), int(glyph_y), cross_pm)
