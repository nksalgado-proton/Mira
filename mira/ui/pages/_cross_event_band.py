"""Cross-Event Cuts entry band — the NEW Surface 01 entry point that lets
the user run searches and build cuts spanning every event at once.

Per design-system §Cross-Event Cuts: an accent-bordered Card-style band with
a faint accent gradient wash, an accent icon tile at left, the
"Cross-Event Cuts" title + Preview tag + subtitle, a large SearchField, and
a primary Search button.

This is BANNERED — it sits directly above the per-event filters and reads
as app-level, not per-event. The Search button is a stub for now (no backend
endpoint yet). When the cross_event_search(query) endpoint lands, ``submitted``
emits the trimmed query string for the host to dispatch.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design import primary_button, search_field, tag
from mira.ui.palette import PALETTE


_GLYPH_PATH = (
    Path(__file__).resolve().parents[3]
    / "assets" / "icons" / "glyphs" / "cross_event.svg"
)


class _CrossEventGlyph(QLabel):
    """50px accent-soft tile with the stacked-frames + magnifier SVG glyph
    centered, tinted accent. Replaces the Unicode `❖` placeholder per the
    surface-01 mockup."""

    def __init__(self, tint: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(50, 50)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tint = tint
        self.setStyleSheet(
            "background: #211f3a; border-radius: 14px;"
        )

    def paintEvent(self, evt) -> None:  # noqa: N802 — Qt override
        super().paintEvent(evt)
        if not _GLYPH_PATH.is_file():
            return
        renderer = QSvgRenderer(str(_GLYPH_PATH))
        if not renderer.isValid():
            return
        icon = 26
        buf = QImage(icon, icon, QImage.Format.Format_ARGB32)
        buf.fill(0)
        ip = QPainter(buf)
        ip.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(ip)
        ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        ip.fillRect(buf.rect(), self._tint)
        ip.end()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        x = (self.width() - icon) // 2
        y = (self.height() - icon) // 2
        p.drawImage(x, y, buf)
        p.end()


class CrossEventCutsBand(QFrame):
    """Accent-bordered band hosting the cross-event search entry.

    Signals:
        submitted(str)  query string entered into the search field
                        (emitted on Search-button click or Return press).
    """

    submitted = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CrossEventBand")
        self._build_layout()
        self._apply_shadow()

    def _build_layout(self) -> None:
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 18, 14)
        h.setSpacing(14)

        # Accent icon tile (50px). Glyph = stacked frames + magnifier SVG
        # tinted accent (path data extracted from surface-01-initial-app.html
        # into assets/icons/glyphs/cross_event.svg).
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        tint = QColor(PALETTE[mode]["accent"])
        h.addWidget(_CrossEventGlyph(tint))

        # Label block
        label_box = QVBoxLayout()
        label_box.setContentsMargins(0, 0, 0, 0)
        label_box.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel("Cross-Event Cuts")
        title.setObjectName("CardTitle")
        title_row.addWidget(title)
        title_row.addWidget(tag("Preview"))
        title_row.addStretch()
        label_box.addLayout(title_row)
        sub = QLabel("Search and build cuts across every event at once.")
        sub.setObjectName("Sub")
        label_box.addWidget(sub)
        h.addLayout(label_box, 2)

        # Search field — 17px glyph per mockup hero-input spec
        self._search = search_field(
            "Search captures, picks, cuts and tags across all events…",
            glyph_size=17,
        )
        h.addWidget(self._search, 3)
        self._search.input.returnPressed.connect(self._emit)

        # Primary Search button
        btn = primary_button("Search")
        btn.clicked.connect(self._emit)
        h.addWidget(btn)

    def _apply_shadow(self) -> None:
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(34)
        eff.setOffset(0, 12)
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        try:
            alpha = int(PALETTE[mode]["shadow_alpha"])
        except (KeyError, ValueError):
            alpha = 90
        # Accent-tinted shadow per the spec
        accent = QColor(PALETTE[mode]["accent"])
        accent.setAlpha(alpha)
        eff.setColor(accent)
        self.setGraphicsEffect(eff)

    def _emit(self) -> None:
        text = self._search.input.text().strip()
        if text:
            self.submitted.emit(text)

    def query(self) -> str:
        return self._search.input.text().strip()

    def clear(self) -> None:
        self._search.input.clear()
