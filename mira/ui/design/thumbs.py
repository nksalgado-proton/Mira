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

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
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
        edited_since_export
                        bool (spec/118 §2) — adds an amber "Edited" chip
                        in the top-right corner for stale exports (the
                        on-disk Mira render no longer matches the live
                        recipe). Loud and distinct from both the
                        ship-intent border and the provenance wordmark.
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
        edited_since_export: bool = False,
        stars: int | None = None,
        color_label: str | None = None,
        flag: bool = False,
        to_delete: bool = False,
        to_delete_split: tuple[int, int] | None = None,
        preferred: bool = False,
        preferred_origin: str | None = None,
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
        # spec/159 — per-version review chrome for the closed-event
        # Exported Collection grid. ``stars`` 1-5 or None; ``color_label``
        # is the LRC-style label string; ``flag`` is the portfolio
        # toggle; ``to_delete`` paints the "Marked for deletion" bottom
        # strip + hides the star chip (no point showing a rating on
        # something about to be deleted). All four default to off so
        # the legacy chrome contract is unchanged for callers that
        # don't pass them.
        self._stars = stars
        self._color_label = color_label
        self._flag = bool(flag)
        self._to_delete = bool(to_delete)
        # spec/159 §4.4 — versions-cluster "N/M to delete" sub-chip.
        # Only painted when this Thumb is a cluster cover AND at least
        # one inner version carries ``to_delete = 1``. Tuple of
        # (marked_count, total_count); ``None`` hides the chip.
        self._to_delete_split = to_delete_split
        # spec/159 §6+ — preferred-version chrome.
        # ``preferred`` paints a small "✓" pill in the top-right of
        # a single-version flat cell. ``preferred_origin`` (the wordmark
        # of the preferred member — "LRC" / "Mira" / "ext") paints
        # alongside the ✓ on a cluster cover so the user knows which
        # version is the chosen one without drilling in.
        self._preferred = bool(preferred)
        self._preferred_origin = preferred_origin
        # spec/89 §2.1 Block 2 D3.B — origin wordmark (Mira / LRC /
        # Helicon / CO / ext) painted on a thin strip under the thumb
        # for Export cells. ``None`` keeps the cell badge-free
        # (0-version cells, Pick / Edit phases).
        self._origin = origin
        # spec/89 §4.2 Block 7 D2.B — "skipped in Pick" indicator chip
        # shown on Export cells that entered the pool only because a
        # ship row exists. ``False`` keeps the cell clean.
        self._skipped_in_pick = bool(skipped_in_pick)
        # spec/89 §4.2 / Block 7 D3.B Slice 7 — Export-mode flag that
        # flips the "Exported" stamp into a destructive cue. See
        # :meth:`setExportDestructiveMode`.
        self._export_destructive_mode = False
        # spec/118 §2 — loud "edited since export" badge: the on-disk
        # export no longer matches the current edit. Painted as an amber
        # corner flag, separate from the ship-intent border and the soft
        # provenance wordmark.
        self._edited_since_export = bool(edited_since_export)
        self._blurred_cache: QPixmap | None = None
        self.setFixedSize(size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # spec/159 hover-meaning tooltip. Composed from
        # (color_label, stars, flag) using the user's rating_meanings
        # dictionary from Settings — so parking the mouse on a photo
        # explains what its rating means without opening the review
        # dialog.
        self._refresh_rating_tooltip()

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

    def setEditedSinceExport(self, flag: bool) -> None:
        """spec/118 §2 — loud "edited since export" badge. True paints
        the corner flag on cells whose on-disk export no longer matches
        the live recipe; False clears it."""
        self._edited_since_export = bool(flag)
        self.update()

    def setExportDestructiveMode(self, flag: bool) -> None:
        """spec/89 §4.2 / Block 7 D3.B Slice 7 — flip the "Exported"
        stamp's meaning to a destructive cue. When True the chip only
        paints on cells where the user pressing X would actually
        unlink a real file (state == 'picked' AND exported), and uses
        a red tint so the destructive intent is unmistakable. The
        legacy informational reading stays under False."""
        self._export_destructive_mode = bool(flag)
        self.update()

    def setClusterCount(self, n: int) -> None:
        self._cluster_count = n
        self.update()

    # ── spec/159 — per-version review chrome setters ───────────────────

    def setStars(self, stars: int | None) -> None:
        """spec/159 — star rating 1..5 or None (no rating)."""
        self._stars = stars
        self._refresh_rating_tooltip()
        self.update()

    def setColorLabel(self, label: str | None) -> None:
        """spec/159 — LRC-style colour label or None (no label).
        Accepted: 'red'/'yellow'/'green'/'blue'/'purple'."""
        self._color_label = label
        self._refresh_rating_tooltip()
        self.update()

    def setToDeleteSplit(
        self, split: tuple[int, int] | None,
    ) -> None:
        """spec/159 §4.4 — set the versions-cluster cover's "N/M to
        delete" sub-chip. ``None`` hides it; ``(n, m)`` with ``n >= 1``
        paints it in the bottom-left."""
        self._to_delete_split = split
        self.update()

    def setPreferred(self, preferred: bool) -> None:
        """spec/159 §6+ — paint / hide the ✓ preferred-version pill."""
        self._preferred = bool(preferred)
        self.update()

    def setPreferredOrigin(self, origin: str | None) -> None:
        """spec/159 §6+ — set the cluster cover's preferred wordmark
        ("LRC" / "Mira" / "ext"). ``None`` hides the chip."""
        self._preferred_origin = origin
        self.update()

    def setFlag(self, flag: bool) -> None:
        """spec/159 — portfolio flag toggle."""
        self._flag = bool(flag)
        self._refresh_rating_tooltip()
        self.update()

    def _refresh_rating_tooltip(self) -> None:
        """Compose the whole-tile tooltip from the tile's current
        (color_label, stars, flag), using the user's rating meanings.
        Empty when the tile carries no rating chrome — the tile stays
        silent instead of showing a stray dash.

        Lazy-imports :func:`_load_rating_meanings` from
        ``mira.ui.exported.rating_widgets`` to keep the design →
        exported dependency at runtime only (no import-time cycle)."""
        has_any = bool(self._color_label) or (self._stars is not None) or self._flag
        if not has_any:
            self.setToolTip("")
            return
        try:
            from mira.ui.exported.rating_widgets import _load_rating_meanings
            meanings = _load_rating_meanings()
        except Exception:                                   # noqa: BLE001
            meanings = {}
        lines: list[str] = []
        if self._color_label:
            name = str(self._color_label).title()
            meaning = (meanings.get(f"color_{self._color_label}") or "").strip()
            category = (meanings.get("category_color") or "").strip()
            line = name
            if meaning:
                line = f"{line} — {meaning}"
            if category:
                line = f"{line}  ({category})"
            lines.append(line)
        if self._stars is not None:
            n = int(self._stars)
            header = "1 star" if n == 1 else f"{n} stars"
            meaning = (meanings.get(f"stars_{n}") or "").strip()
            category = (meanings.get("category_stars") or "").strip()
            line = header
            if meaning:
                line = f"{line} — {meaning}"
            if category:
                line = f"{line}  ({category})"
            lines.append(line)
        if self._flag:
            meaning = (meanings.get("flag_on") or "").strip()
            category = (meanings.get("category_flag") or "").strip()
            line = "Portfolio flag"
            if meaning:
                line = f"{line} — {meaning}"
            if category:
                line = f"{line}  ({category})"
            lines.append(line)
        self.setToolTip("\n".join(lines))

    def setToDelete(self, to_delete: bool) -> None:
        """spec/159 — "Marked for deletion" badge toggle. True paints
        the bottom strip and hides the star chip."""
        self._to_delete = bool(to_delete)
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
        # spec/159 (Nelson 2026-06-30 follow-up) — an LRC ``color_label``
        # outranks both: the cell-border colour IS the rating chip on
        # the Exported Collection grid (replaces the v1 top strip; the
        # delete badge owns the bottom strip so the cell already wears
        # one stripe + we want the rating to read at a glance).
        if self._color_label and self._color_label in self._COLOR_LABEL_HEX:
            state_color = QColor(self._COLOR_LABEL_HEX[self._color_label])
        elif self._border_token:
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
        self._paint_edited_since_export(painter, palette)
        # spec/159 — review chrome. Paint after the rest so they sit on
        # top of cluster overlays etc. The colour-label strip retired
        # 2026-06-30 — the cell border now carries the LRC colour (see
        # the ``state_color`` resolve above).
        self._paint_star_chip(painter)
        self._paint_flag_glyph(painter, palette)
        self._paint_to_delete_badge(painter)
        # spec/159 §4.4 — versions-cluster "N/M to delete" sub-chip.
        # Only fires on cluster covers; only when the inner versions
        # carry at least one mark. Sits in the bottom-left so it
        # shares the row with the bottom-right ×N count chip.
        self._paint_to_delete_split_chip(painter)
        # spec/159 §6+ — preferred-version chrome.
        self._paint_preferred_pill(painter)
        self._paint_preferred_origin_chip(painter)

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

    def _paint_edited_since_export(
        self, painter: QPainter, palette: dict[str, str]
    ) -> None:
        """spec/118 §2 — distinctive amber "edited" badge top-right when
        the on-disk export no longer matches the current edit. Loud
        enough to be unmistakable; intentionally not the soft
        provenance wordmark, and orthogonal to the ship-intent border.
        Stacked LEFT of the visited eye chip when both apply so neither
        chip hides the other."""
        if not self._edited_since_export:
            return
        f = painter.font()
        f.setPointSizeF(8.5)
        f.setBold(True)
        painter.setFont(f)
        label = "Edited"
        fm = painter.fontMetrics()
        label_w = fm.horizontalAdvance(label)
        pad_x = 8
        chip_w = label_w + pad_x * 2
        chip_h = 22
        right_edge = self.width() - 8
        if self._visited:
            right_edge = self.width() - 36 - 4
        chip_rect = QRectF(
            right_edge - chip_w, 8, chip_w, chip_h,
        )
        bg = QColor(palette.get("amber", "#fbbf24"))
        self._paint_chip(painter, chip_rect, bg, QColor(0, 0, 0, 90))
        painter.setPen(QColor("#241a02"))
        painter.drawText(
            chip_rect, int(Qt.AlignmentFlag.AlignCenter), label,
        )

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
        """spec/89 §4.2 / Block 7 D3.B Slice 7 — the "Exported" stamp
        switches meaning on the Export surface: in legacy mode it
        reads as **informational** ("this item has shipped before");
        in Export's destructive mode (set via
        :meth:`setExportDestructiveMode`) it ONLY paints on cells
        where a green→red flip would actually unlink an on-disk file
        — i.e. ``state == 'picked'`` AND ``exported`` — and uses a
        red tint so the user reads it as "X here is destructive."
        Cells already in red intent don't get the chip (the destructive
        flip has already been armed)."""
        if not self._exported:
            return
        destructive = bool(getattr(self, "_export_destructive_mode", False))
        if destructive and self._state != "picked":
            return
        chip_rect = QRectF(8, self.height() - 28, 88, 20)
        chip_bg = QColor(palette.get("red", "#ef4444")) if destructive \
            else QColor(palette["accent"])
        self._paint_chip(painter, chip_rect, chip_bg, None)
        painter.setPen(QPen(QColor("#ffffff"), 1.4))
        # Up-arrow glyph + label
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
        label = "Exported" if not destructive else "Has file"
        painter.drawText(
            chip_rect.adjusted(18, 0, 0, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            label,
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

    # ── spec/159 — review chrome paints ────────────────────────────────

    #: spec/159 §4.2 — LRC colour-label hex values.
    _COLOR_LABEL_HEX = {
        "red":    "#D9382E",
        "yellow": "#E4B91F",
        "green":  "#2DA84A",
        "blue":   "#3A8DD8",
        "purple": "#9C4DC9",
    }

    def _paint_star_chip(self, painter: QPainter) -> None:
        """spec/159 — small "★N" chip in the BOTTOM-RIGHT corner. The
        slot is shared with the cluster-count chip (suppressed on
        cluster covers — the cover already shows ×N there); the slot
        also collapses when the to-delete badge is on (no point
        rating something marked for deletion)."""
        if self._stars is None:
            return
        if self._to_delete:
            return
        if self._cluster_type is not None or self._cluster_split is not None:
            return
        # Tight chip — "★N" reads as one symbol; keep narrow.
        text = f"★{int(self._stars)}"
        f = painter.font()
        f.setPointSizeF(10)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        label_w = fm.horizontalAdvance(text)
        pad_x = 7
        chip_w = label_w + pad_x * 2
        chip_h = 20
        chip_rect = QRectF(
            self.width() - chip_w - 8,
            self.height() - chip_h - 8,
            chip_w, chip_h,
        )
        self._paint_chip(
            painter, chip_rect,
            QColor(8, 10, 16, 200), QColor(255, 255, 255, 60),
        )
        # Gold-tinted star + white digit.
        painter.setPen(QColor("#F2C84A"))
        painter.drawText(
            chip_rect,
            int(Qt.AlignmentFlag.AlignCenter), text,
        )

    def _paint_flag_glyph(
        self, painter: QPainter, palette: dict[str, str],
    ) -> None:
        """spec/159 — flag glyph in the TOP-LEFT corner when the
        portfolio flag is set.

        Pennant body with a forked tail so it reads as a *flag* and
        not a triangle (Nelson 2026-06-30 round 2). 28 px tall × 24
        px wide, anchored 10 px in from the cell edges. The cloth is
        amber and the pole goes on top so the join reads clean.
        """
        if not self._flag:
            return
        x, y = 10.0, 10.0
        pole_h = 28.0
        cloth_w = 24.0
        cloth_h = 16.0
        # Cloth — pennant with forked tail (matches the dialog's
        # FlagToggle silhouette).
        flag_color = QColor(palette.get("amber", "#F5B042"))
        path = QPainterPath()
        tx = x
        ty = y + 1
        path.moveTo(tx,                  ty)
        path.lineTo(tx + cloth_w,        ty + cloth_h * 0.25)
        path.lineTo(tx + cloth_w * 0.78, ty + cloth_h * 0.50)
        path.lineTo(tx + cloth_w,        ty + cloth_h * 0.78)
        path.lineTo(tx,                  ty + cloth_h)
        path.closeSubpath()
        painter.setBrush(QBrush(flag_color))
        painter.setPen(QPen(QColor("#7A4A12"), 1.4))
        painter.drawPath(path)
        # Pole + finial in the SAME amber as the cloth (Nelson
        # 2026-06-30 round 3): a darker pole was getting lost against
        # the photo behind it, and the eye then saw just the cloth —
        # a triangle, not a flag. Matching the amber makes the whole
        # glyph read as one continuous object on any backdrop.
        pole_pen = QPen(flag_color, 2.0)
        painter.setPen(pole_pen)
        painter.drawLine(int(x), int(y), int(x), int(y + pole_h))
        painter.setBrush(QBrush(flag_color))
        painter.drawEllipse(QPointF(x, y), 2.0, 2.0)

    #: spec/159 §6+ — green used by the preferred-version chrome.
    #: Distinct from the cell's state-border palette so the badge
    #: doesn't read as "picked".
    _PREFERRED_GREEN = QColor("#2DA84A")
    _PREFERRED_GREEN_DARK = QColor("#1F7A36")

    def _paint_preferred_pill(self, painter: QPainter) -> None:
        """spec/159 §6+ — small ✓ pill in the TOP-RIGHT corner when
        the lineage row is the preferred version of its source.

        Hidden on cluster covers — those carry the
        ``_paint_preferred_origin_chip`` chip instead (which also
        names the chosen origin, e.g. "✓ LRC")."""
        if not self._preferred:
            return
        if self._cluster_type is not None:
            return
        # ✓ glyph; medium chip; high-contrast green so it reads on
        # both light and dark thumbs without a backdrop dependency.
        f = painter.font()
        f.setPointSizeF(11)
        f.setBold(True)
        painter.setFont(f)
        diameter = 22
        rect = QRectF(
            self.width() - diameter - 8,
            8,
            diameter, diameter,
        )
        painter.setBrush(QBrush(self._PREFERRED_GREEN))
        painter.setPen(QPen(self._PREFERRED_GREEN_DARK, 1.4))
        painter.drawEllipse(rect)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignCenter),
            "✓",
        )

    def _paint_preferred_origin_chip(self, painter: QPainter) -> None:
        """spec/159 §6+ — "✓ LRC" / "✓ Mira" sub-chip on a versions
        cluster cover, painted in the top-left. Tells the user which
        member of the cluster is the chosen one without drilling in.
        Hidden when the cover has no preferred member."""
        if self._cluster_type is None:
            return
        if not self._preferred_origin:
            return
        label = f"✓ {self._preferred_origin}"
        f = painter.font()
        f.setPointSizeF(9.5)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        pad_x = 9
        chip_w = fm.horizontalAdvance(label) + pad_x * 2
        chip_h = fm.height() + 6
        chip_rect = QRectF(
            8, 8, chip_w, chip_h,
        )
        painter.setBrush(QBrush(self._PREFERRED_GREEN))
        painter.setPen(QPen(self._PREFERRED_GREEN_DARK, 1.2))
        painter.drawRoundedRect(chip_rect, chip_h / 2, chip_h / 2)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            chip_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            label,
        )

    def _paint_to_delete_split_chip(self, painter: QPainter) -> None:
        """spec/159 §4.4 — bottom-left "N/M to delete" sub-chip on a
        versions-cluster cover. Hidden when there is no split, when
        the marked count is zero, or when this Thumb is NOT a cluster
        cover (the chip is meaningless on a flat cell — the cell's
        own bottom "DELETE" pill carries that signal).
        """
        if self._to_delete_split is None:
            return
        if self._cluster_type is None:
            return
        marked, total = self._to_delete_split
        if marked <= 0 or total <= 0:
            return
        text = f"{int(marked)}/{int(total)}"
        f = painter.font()
        f.setPointSizeF(9.5)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        # Mini "DELETE" tag next to the count so the chip reads at a
        # glance ("3/5 DELETE") without needing a hover tooltip.
        tag = "DELETE"
        sep = "·"
        sep_w = fm.horizontalAdvance(f" {sep} ")
        text_w = fm.horizontalAdvance(text)
        tag_w = fm.horizontalAdvance(tag)
        pad_x = 8
        chip_w = text_w + sep_w + tag_w + pad_x * 2
        chip_h = fm.height() + 6
        chip_rect = QRectF(
            8, self.height() - chip_h - 8, chip_w, chip_h,
        )
        painter.setBrush(QBrush(QColor("#A02020")))
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.drawRoundedRect(chip_rect, chip_h / 2, chip_h / 2)
        painter.setPen(QColor("#ffffff"))
        # Hand-place the two parts so we don't burn a layout pass.
        text_y = chip_rect.y() + (chip_rect.height() + fm.ascent()) / 2 - 2
        cursor_x = chip_rect.x() + pad_x
        painter.drawText(int(cursor_x), int(text_y), text)
        cursor_x += text_w
        painter.drawText(int(cursor_x), int(text_y), f" {sep} ")
        cursor_x += sep_w
        painter.drawText(int(cursor_x), int(text_y), tag)

    def _paint_to_delete_badge(self, painter: QPainter) -> None:
        """spec/159 — compact "Delete" pill across the BOTTOM of the
        cell. Smaller than the v1 full-strip (Nelson 2026-06-30 —
        "the badge looks bad: too large + square corners on a
        rounded grid"). Pill is inset from the cell edges so it
        respects the card's rounded corners; opaque dark-red bg,
        white text."""
        if not self._to_delete:
            return
        f = painter.font()
        f.setPointSizeF(9.0)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        label = "DELETE"
        label_w = fm.horizontalAdvance(label)
        pad_x = 10
        pad_y = 4
        pill_w = label_w + pad_x * 2
        pill_h = fm.height() + pad_y * 2
        pill_rect = QRectF(
            (self.width() - pill_w) / 2,
            self.height() - pill_h - 8,
            pill_w, pill_h,
        )
        painter.setBrush(QBrush(QColor("#A02020")))
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.drawRoundedRect(
            pill_rect, pill_h / 2, pill_h / 2)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            pill_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            label,
        )
