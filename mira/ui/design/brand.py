"""Mira brand widgets — the app mark + wordmark lockup.

From the redesign logo kit (``MiraCrafter Redesign/mira-logo.html``):

* :class:`MiraMark` — the rounded gradient tile (accent → pink) holding the
  white viewfinder + spark mark (``assets/icons/mira-mark.svg``). Reusable as
  the app-icon glyph at any size.
* :class:`MiraLogo` — the mark tile beside the ``M✦ıra`` wordmark (the ``i`` is
  an accent ``✦`` spark) with an optional "See the keepers." tagline.

Both paint from the live :data:`mira.ui.palette.PALETTE` so they follow theme
toggles. The mark is recoloured white via the QSvgRenderer + ``SourceIn``
pattern the rest of the design system uses, so the source SVG colour is
irrelevant.
"""
from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mira.ui.palette import PALETTE

TAGLINE = "See the keepers."

_MARK_PATH = (
    Path(__file__).resolve().parents[3] / "assets" / "icons" / "mira-mark.svg"
)


def _theme_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


class MiraMark(QWidget):
    """Square gradient app-mark tile (accent → pink) with the white mark."""

    def __init__(
        self, size: int = 28, *, radius: int | None = None, parent=None
    ) -> None:
        super().__init__(parent)
        self._size = int(size)
        self._radius = (
            radius if radius is not None else max(6, round(size * 0.30))
        )
        self.setFixedSize(self._size, self._size)

    def paintEvent(self, _evt) -> None:  # noqa: N802 — Qt override
        pal = PALETTE[_theme_mode()]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = QRectF(0, 0, self._size, self._size)
        grad = QLinearGradient(0, 0, self._size, self._size)
        grad.setColorAt(0.0, QColor(pal["accent"]))
        grad.setColorAt(1.0, QColor(pal["pink"]))
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        p.fillPath(path, grad)

        if _MARK_PATH.is_file():
            renderer = QSvgRenderer(str(_MARK_PATH))
            if renderer.isValid():
                icon = max(8, round(self._size * 0.58))
                buf = QImage(icon, icon, QImage.Format.Format_ARGB32)
                buf.fill(0)
                ip = QPainter(buf)
                ip.setRenderHint(QPainter.RenderHint.Antialiasing)
                renderer.render(ip)
                ip.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceIn
                )
                ip.fillRect(buf.rect(), QColor("#ffffff"))
                ip.end()
                x = (self._size - icon) // 2
                y = (self._size - icon) // 2
                p.drawImage(x, y, buf)
        p.end()


class _Wordmark(QWidget):
    """Painted ``M✦ıra`` wordmark — the ``i`` is a dotless ``ı`` stem with an
    accent ``✦`` spark sitting where its dot would be, matching the logo kit
    (``.wm .iw .sp`` at 0.42em, centred over the stem). Painted (not rich
    text) so the spark lands precisely over the ``ı`` at any size."""

    def __init__(self, pt: float, parent=None) -> None:
        super().__init__(parent)
        self._pt = pt
        self._base = QFont(self.font())
        self._base.setPointSizeF(pt)
        self._base.setWeight(QFont.Weight.ExtraBold)
        self._base.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.5)
        self._spark = QFont(self._base)
        self._spark.setPointSizeF(pt * 0.42)
        self._spark.setWeight(QFont.Weight.Bold)

        fm = QFontMetricsF(self._base)
        self._text = "Mıra"  # dotless ı (U+0131); the spark is its dot
        self._ascent = fm.ascent()
        self._m_w = fm.horizontalAdvance("M")
        self._i_w = fm.horizontalAdvance("ı")
        width = fm.horizontalAdvance(self._text) + 4
        self.setFixedSize(math.ceil(width), math.ceil(fm.height()))

    def paintEvent(self, _evt) -> None:  # noqa: N802 — Qt override
        pal = PALETTE[_theme_mode()]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Base word in ink, on the baseline.
        p.setFont(self._base)
        p.setPen(QColor(pal["ink"]))
        p.drawText(QPointF(0.0, self._ascent), self._text)

        # Accent spark centred over the ı stem, near where the dot sits.
        spark_cx = self._m_w + self._i_w / 2.0
        sfm = QFontMetricsF(self._spark)
        spark = "✦"
        sx = spark_cx - sfm.horizontalAdvance(spark) / 2.0
        sy = self._ascent * 0.46  # baseline of the spark near the top
        p.setFont(self._spark)
        p.setPen(QColor(pal["accent"]))
        p.drawText(QPointF(sx, sy), spark)
        p.end()


class MiraLogo(QWidget):
    """Mark tile + ``M✦ıra`` wordmark (+ optional tagline).

    Parameters
    ----------
    tile_size:
        Edge length of the gradient mark tile in px. The wordmark point size
        and gaps scale from it unless overridden.
    wordmark:
        Show the ``M✦ıra`` text beside the tile (False = bare mark).
    tagline:
        Add the "See the keepers." line under the wordmark.
    wordmark_pt:
        Override the wordmark point size (defaults to ~0.7 × tile_size).
    """

    def __init__(
        self,
        *,
        tile_size: int = 28,
        wordmark: bool = True,
        tagline: bool = False,
        wordmark_pt: float | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(max(6, round(tile_size * 0.34)))
        row.addWidget(MiraMark(tile_size))
        if wordmark:
            row.addLayout(
                self._wordmark_block(
                    wordmark_pt or max(11.0, tile_size * 0.7), tagline
                )
            )

    def _wordmark_block(self, pt: float, tagline: bool) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(_Wordmark(pt))
        if tagline:
            tg = QLabel(TAGLINE)
            tg.setObjectName("Sub")
            box.addWidget(tg)
        return box
