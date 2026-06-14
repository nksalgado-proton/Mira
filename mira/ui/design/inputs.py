"""Input factory helpers — line_input / select / search_field.

Returns plain QLineEdit / QComboBox with the right ObjectName applied so the
design-system QSS rules (#DesignInput / #DesignSelect / #SearchField) take
effect. ``search_field`` additionally bakes in a leading magnifier SVG glyph
by way of a 34px left padding + a positioned QLabel overlay so the magnifier
sits inside the input frame.

The QPalette PlaceholderText role is set by ``apply_theme`` to ink_faint,
so placeholders render correctly across both modes without per-widget QSS.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QWidget,
)

from mira.ui.palette import PALETTE


_SEARCH_GLYPH_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets" / "icons" / "glyphs" / "search.svg"
)


def _render_search_glyph(size: int = 16) -> QPixmap:
    """Render the search.svg glyph at ``size`` px, tinted ink_soft for the
    active theme. Cached lazily on first call (the result is cheap enough
    that per-instance render is fine, but a module-level cache keeps the
    filter row from re-decoding the SVG for every search field)."""
    app = QApplication.instance()
    mode = (app.property("theme") if app else None) or "dark"
    cache_key = (mode, size)
    cached = _GLYPH_CACHE.get(cache_key)
    if cached is not None:
        return cached
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    if not _SEARCH_GLYPH_PATH.is_file():
        _GLYPH_CACHE[cache_key] = pm
        return pm
    renderer = QSvgRenderer(str(_SEARCH_GLYPH_PATH))
    if not renderer.isValid():
        _GLYPH_CACHE[cache_key] = pm
        return pm
    buf = QImage(size, size, QImage.Format.Format_ARGB32)
    buf.fill(0)
    ip = QPainter(buf)
    ip.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(ip)
    ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    ip.fillRect(buf.rect(), QColor(PALETTE[mode]["ink_soft"]))
    ip.end()
    pm = QPixmap.fromImage(buf)
    _GLYPH_CACHE[cache_key] = pm
    return pm


_GLYPH_CACHE: dict[tuple[str, int], QPixmap] = {}


def line_input(
    placeholder: str = "", parent: QWidget | None = None
) -> QLineEdit:
    e = QLineEdit(parent)
    e.setObjectName("DesignInput")
    if placeholder:
        e.setPlaceholderText(placeholder)
    return e


def select(items: list[str], parent: QWidget | None = None) -> QComboBox:
    """Themed QComboBox. Caller adds items / signals."""
    c = QComboBox(parent)
    c.setObjectName("DesignSelect")
    c.addItems(items)
    return c


class _SearchFieldWrap(QWidget):
    """Container around a #SearchField QLineEdit that overlays a leading
    magnifier glyph. The QLineEdit itself is reachable via .input — caller
    uses normal text() / setText() / returnPressed signal on it."""

    def __init__(
        self,
        placeholder: str,
        parent: QWidget | None = None,
        *,
        glyph_size: int = 16,
    ) -> None:
        super().__init__(parent)
        self.input = QLineEdit(self)
        self.input.setObjectName("SearchField")
        self.input.setPlaceholderText(placeholder)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self.input)
        # Inline SVG glyph at left — positioned over the input's 34px padding.
        # Source: assets/icons/glyphs/search.svg (path data extracted from
        # surface-01-initial-app.html). Tinted ink_soft for the active theme.
        self._glyph = QLabel(self.input)
        self._glyph.setObjectName("SearchGlyph")
        self._glyph.setStyleSheet("background: transparent;")
        self._glyph.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._glyph_size = glyph_size
        pm = _render_search_glyph(glyph_size)
        self._glyph.setPixmap(pm)
        self._glyph.resize(glyph_size, glyph_size)
        self._glyph.move(12, 10)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    def resizeEvent(self, e):  # noqa: D401, N802 — Qt override
        super().resizeEvent(e)
        # Keep the glyph centered vertically when the input grows.
        h = self.input.height()
        self._glyph.move(12, max(8, (h - self._glyph_size) // 2))


def search_field(
    placeholder: str = "Search…",
    parent: QWidget | None = None,
    *,
    glyph_size: int = 16,
) -> _SearchFieldWrap:
    """Themed search field with a leading magnifier SVG glyph inside the
    input. Use the returned widget's ``.input`` for text() / returnPressed /
    etc. The wrap itself is what you addWidget into the parent layout.

    ``glyph_size`` lets the Cross-Event Cuts band ask for a larger 17px
    magnifier to match the mockup's hero-input feel.
    """
    return _SearchFieldWrap(placeholder, parent, glyph_size=glyph_size)
