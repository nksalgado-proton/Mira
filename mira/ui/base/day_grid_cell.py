"""``DayGridCell`` — one cell in the Day Grid (spec/32 §2.5–§2.6).

A single square widget that renders:
  * an item thumbnail (photo / video) OR a cluster icon,
  * a status border colour (driven by the ``status`` QSS property —
    KEPT green · DISCARDED red · COMPARE orange · MIXED yellow · UNTOUCHED
    neutral — set per :class:`mira.picked.CellColor`),
  * an optional count badge for clusters (baked into the icon pixmap),
  * a tiny ▶ overlay for video cells.

Two distinct click zones (spec/32 §2.6):
  * **border zone** — the outer ring up to the border width → emits
    ``border_clicked``: the host cycles state (Discard ↔ Keep ↔ Compare
    for photos; Discard ↔ Keep for videos; bulk-cycle for clusters).
  * **centre zone** — everything inside → emits ``center_clicked``: the
    host opens the cell (photo surface / video surface / cluster sub-grid).

Pure widget — no gateway, no thumbnail-decoding strategy. The host feeds
it a pre-loaded ``QPixmap`` for items (or ``None`` to show a placeholder).
Cluster cells generate their icon from
:func:`mira.ui.base.cluster_icons.cluster_icon`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from mira.picked.model import CullCell
from mira.picked.status import CellColor
from mira.ui.base.cluster_icons import cluster_icon
from mira.ui.base.exported_watermark import ExportedWatermark
from mira.ui.i18n import tr

# Border thickness scales proportionally with cell size. Hit-test for the
# "border zone" uses this width — clicks within ``BORDER_RATIO * size`` of
# any edge route to ``border_clicked``; everything else is centre.
BORDER_RATIO = 0.10            # 10 % of cell side; ~14 px on a 140 px cell
MIN_BORDER_PX = 6              # minimum so small cells stay clickable
MAX_BORDER_PX = 22             # cap so big cells don't waste real-estate


@dataclass(frozen=True)
class CellRenderData:
    """Everything :class:`DayGridCell` needs to render — decoupled from the
    :class:`mira.picked.model.CullCell` so the widget can be tested
    without the gateway/scanner stack.

    * ``cell`` — the model cell (drives status colour, item vs cluster
      discriminator, and the cluster kind/count).
    * ``thumbnail`` — pre-loaded thumbnail pixmap for item cells; ignored
      for cluster cells (cluster icon is rasterised on demand).
    """

    cell: CullCell
    thumbnail: Optional[QPixmap] = None


class DayGridCell(QFrame):
    """One Day Grid cell. Border colour + click zones; thumbnail or icon."""

    # Border click → cycle the cell's state (photo: D→K→C→D, video: D→K,
    # cluster: bulk D-all → K-all, etc. — host applies the rule).
    border_clicked = pyqtSignal()
    # Centre click → open the cell (photo surface, video surface, cluster
    # sub-grid; host routes by cell kind).
    center_clicked = pyqtSignal()

    def __init__(
        self,
        data: CellRenderData,
        *,
        size: int = 140,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._size = int(size)

        self.setObjectName("DayGridCell")
        # Set the QSS ``status`` property BEFORE the widget's first polish so
        # the initial paint uses the right border colour without an explicit
        # unpolish + polish round-trip (Nelson 2026-06-05 perf — unpolish+
        # polish on every new cell was a hidden N×stylesheet-walk cost in
        # large days; we now only do it on later state changes via set_data).
        self.setProperty("status", str(self._data.cell.color.value))
        # The cursor is the click-affordance signal — pointing hand.
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        sp = self.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Fixed)
        sp.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        self.setSizePolicy(sp)

        layout = QVBoxLayout(self)
        b = self._border_px()
        layout.setContentsMargins(b, b, b, b)
        layout.setSpacing(0)
        self._inner = QLabel(self)
        self._inner.setObjectName("DayGridCellInner")
        self._inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._inner.setScaledContents(False)
        self._inner.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._inner)

        # Optional ▶ play-overlay for video cells. Just the white triangle —
        # no dark scrim. (Nelson 2026-06-04: the previous dark circle made
        # it hard to see what's underneath.) A subtle text-shadow keeps the
        # triangle readable against light thumbnails.
        if self._is_video_item():
            self._play = QLabel("▶", self._inner)
            self._play.setObjectName("DayGridPlayOverlay")
            self._play.setStyleSheet(
                "QLabel#DayGridPlayOverlay {"
                "  color: rgba(255,255,255,235);"
                "  background: transparent;"
                "  font-size: %dpt;"
                "  font-weight: bold;"
                "}" % max(12, self._size // 8)
            )
            # A bare-bones graphics drop shadow so the triangle reads on
            # bright backgrounds without a heavy scrim.
            from PyQt6.QtWidgets import QGraphicsDropShadowEffect
            from PyQt6.QtGui import QColor
            shadow = QGraphicsDropShadowEffect(self._play)
            shadow.setBlurRadius(6)
            shadow.setOffset(0, 0)
            shadow.setColor(QColor(0, 0, 0, 180))
            self._play.setGraphicsEffect(shadow)
            self._play.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._play.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        else:
            self._play = None

        # spec/32 §2.10 visited tick — a small ✓ in the top-right corner of
        # the inner area when the user has previously drilled into this cell
        # at the current phase.  Always created (hidden when not visited)
        # so ``set_data()`` updates are a simple show/hide rather than a
        # widget rebuild.  Theme-neutral translucent pill background + soft
        # drop shadow so it reads against both dark and light thumbnails.
        self._tick = QLabel("✓", self._inner)
        self._tick.setObjectName("DayGridVisitedTick")
        self._tick.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tick.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._apply_tick_style()
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        from PyQt6.QtGui import QColor as _QColor
        tick_shadow = QGraphicsDropShadowEffect(self._tick)
        tick_shadow.setBlurRadius(6)
        tick_shadow.setOffset(0, 0)
        tick_shadow.setColor(_QColor(0, 0, 0, 200))
        self._tick.setGraphicsEffect(tick_shadow)
        self._tick.setVisible(bool(self._data.cell.visited))

        # spec/59 §8 Exported watermark — diagonal translucent text over
        # PHOTO item thumbnails whose item has an exported version
        # (``cell.exported``; the host applies the app-wide setting).
        # Always created (hidden) so ``set_data()`` is a show/hide,
        # mirroring the visited tick.
        self._watermark = ExportedWatermark(self._inner)
        self._sync_watermark()

        self.set_size(self._size)
        # _apply_status() is intentionally skipped on first construction —
        # the property was already set above and Qt's first polish picks
        # it up. set_data() will run _apply_status() on subsequent changes.
        self._apply_pixmap()
        self._refresh_tooltip()

    # ── public API ────────────────────────────────────────────────────

    def cell(self) -> CullCell:
        return self._data.cell

    def render_data(self) -> CellRenderData:
        return self._data

    def set_size(self, size: int) -> None:
        """Resize the cell — also re-rasterises the cluster icon and
        re-applies the border thickness."""
        size = max(40, int(size))
        self._size = size
        self.setFixedSize(size, size)
        b = self._border_px()
        if isinstance(self.layout(), QVBoxLayout):
            self.layout().setContentsMargins(b, b, b, b)
        self._apply_pixmap()
        # Restyle the tick at the new size so its font/pill scale with
        # the cell-size slider (spec/32 §7.4 — "scales proportionally").
        if self._tick is not None:
            self._apply_tick_style()
        self._reposition_play_overlay()

    def set_data(self, data: CellRenderData) -> None:
        """Replace the cell's content (status colour, thumb/icon). Used
        when the host refreshes a cell after a state cycle."""
        self._data = data
        self._apply_status()
        self._apply_pixmap()
        if self._tick is not None:
            self._tick.setVisible(bool(self._data.cell.visited))
        self._sync_watermark()
        self._refresh_tooltip()

    def set_thumbnail(self, pixmap: Optional[QPixmap]) -> None:
        """Just refresh the item thumbnail (lazy-load path) without
        changing the cell's model state."""
        self._data = CellRenderData(cell=self._data.cell, thumbnail=pixmap)
        self._apply_pixmap()

    # ── internals ─────────────────────────────────────────────────────

    def _border_px(self) -> int:
        return max(MIN_BORDER_PX, min(MAX_BORDER_PX, int(self._size * BORDER_RATIO)))

    def _is_video_item(self) -> bool:
        c = self._data.cell
        return not c.is_cluster and c.item_kind == "video"

    def _apply_status(self) -> None:
        """Drive the ``DayGridCell[status="..."]`` QSS rule from the cell's
        :class:`CellColor`. Re-polish so the rule fires."""
        self.setProperty("status", str(self._data.cell.color.value))
        self.style().unpolish(self)
        self.style().polish(self)

    def _apply_tick_style(self) -> None:
        """Style the ✓ visited badge (spec/32 §2.10, §7.4).

        Theme-neutral light pill with a soft translucent background — so it
        reads against dark and light thumbnails alike without ever competing
        with the status border. Font size scales with the cell so the badge
        stays legible at every zoom level the §9 slider produces.
        """
        font_pt = max(9, self._size // 12)
        # Half the pill side, rounded — enough for a clean circle/pill at
        # every size from the 40 px floor up.
        radius = max(9, self._size // 12)
        self._tick.setStyleSheet(
            "QLabel#DayGridVisitedTick {"
            f"  color: rgba(255,255,255,240);"
            f"  background: rgba(40,40,40,170);"
            f"  border-radius: {radius}px;"
            f"  font-size: {font_pt}pt;"
            f"  font-weight: bold;"
            f"  padding: 0px;"
            "}"
        )

    def _apply_pixmap(self) -> None:
        """Set the inner label's pixmap from the cell's content."""
        inner_side = max(1, self._size - 2 * self._border_px())
        cell = self._data.cell
        if cell.is_cluster and cell.cluster is not None:
            pm = cluster_icon(cell.cluster.kind, inner_side, cell.cluster.count)
        else:
            pm = self._data.thumbnail
            if pm is not None and not pm.isNull():
                pm = pm.scaled(
                    inner_side, inner_side,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        if pm is None or pm.isNull():
            # No thumb yet — clear & show "…" placeholder so the cell still
            # renders its border/status.
            self._inner.clear()
            self._inner.setText(tr("…"))
        else:
            self._inner.setText("")
            self._inner.setPixmap(pm)
        self._reposition_play_overlay()

    def _sync_watermark(self) -> None:
        """Show the Exported watermark on photo item cells whose item
        has an exported version (spec/59 §8). Clusters render icons
        (no image to watermark) and videos carry the aggregate status
        grammar — both stay clean."""
        cell = self._data.cell
        show = (
            not cell.is_cluster
            and cell.item_kind == "photo"
            and bool(cell.exported)
        )
        self._watermark.setVisible(show)

    def _reposition_play_overlay(self) -> None:
        # Reposition every overlay child the cell hosts. Kept on the same
        # method so the layout calls (set_size, _apply_pixmap) update both
        # the ▶ play glyph and the ✓ visited tick in one pass.
        inner_w = self._inner.width()
        inner_h = self._inner.height()
        if inner_w <= 0 or inner_h <= 0:
            return
        # The watermark spans the whole inner area; raised first so the
        # ▶ glyph and the ✓ tick stay above it.
        self._watermark.setGeometry(0, 0, inner_w, inner_h)
        self._watermark.raise_()
        if self._play is not None:
            # Centre over the inner label. No background ring → use the natural
            # text bounding box; just a small minimum so it stays clickable-shaped.
            hint = self._play.sizeHint()
            ow = max(20, hint.width())
            oh = max(20, hint.height())
            x = (inner_w - ow) // 2
            y = (inner_h - oh) // 2
            self._play.setGeometry(x, y, ow, oh)
            self._play.raise_()
        if self._tick is not None:
            # Top-right, inset ~6 % of cell side (spec/32 §7.4). Pill-shaped
            # via the QSS rule in _apply_tick_style; size scales with the
            # cell-size slider so the tick reads at every zoom level.
            side = max(18, self._size // 6)
            inset = max(3, self._size // 18)
            x = inner_w - side - inset
            y = inset
            self._tick.setGeometry(x, y, side, side)
            self._tick.raise_()

    def _refresh_tooltip(self) -> None:
        """A small grounding hint per spec/05: cursor + tooltip on every
        clickable. The label tells the user *what* the cell is (kind +
        count for clusters; type for items) and *what clicking will do*
        (border vs centre)."""
        cell = self._data.cell
        if cell.is_cluster and cell.cluster is not None:
            kind_label = {
                "burst": tr("Burst"),
                "focus_bracket": tr("Focus bracket"),
                "exposure_bracket": tr("Exposure bracket"),
                "repeat": tr("Repeat"),
            }.get(cell.cluster.kind, tr("Cluster"))
            head = f"{kind_label} — {cell.cluster.count} {tr('photos')}"
        elif cell.item_kind == "video":
            head = tr("Video")
        else:
            head = tr("Photo")
        body = tr(
            "Click the border to cycle state. Click the centre to open."
        )
        if self._data.cell.visited:
            body = body + "\n" + tr("Already opened.")
        if self._data.cell.exported and not self._data.cell.is_cluster:
            body = body + "\n" + tr("An exported version exists.")
        self.setToolTip(f"{head}\n{body}")

    # ── click routing ─────────────────────────────────────────────────

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        # Accept the press so the click doesn't bubble; release decides
        # which zone fired.
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(ev)
            return
        pos = ev.position().toPoint()
        if not self.rect().contains(pos):
            ev.ignore()
            return
        b = self._border_px()
        # Hit-test: within ``b`` of any edge = border zone.
        in_border = (
            pos.x() < b
            or pos.x() >= self.width() - b
            or pos.y() < b
            or pos.y() >= self.height() - b
        )
        if in_border:
            self.border_clicked.emit()
        else:
            self.center_clicked.emit()
        ev.accept()

    # Hit-test helpers, exposed for unit testing the zone logic without
    # synthesising Qt mouse events.
    def hit_zone(self, x: int, y: int) -> str:
        """Return ``"border"`` if ``(x, y)`` falls in the border ring,
        ``"center"`` if inside, ``"outside"`` if beyond the widget."""
        if not (0 <= x < self.width() and 0 <= y < self.height()):
            return "outside"
        b = self._border_px()
        if (
            x < b or x >= self.width() - b
            or y < b or y >= self.height() - b
        ):
            return "border"
        return "center"


# Map the five cell colours to QSS status property values. Identity for
# the widget but documented here so the host (or theme reviewers) can
# audit the QSS rule keys without grepping the enum source.
QSS_STATUS_VALUES = tuple(c.value for c in CellColor)
