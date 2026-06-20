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
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design import ghost_button, primary_button, search_field, tag
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
        self.setObjectName("IconTile")
        self.setProperty("tone", "accent")  # accent_soft holder (redesign.qss)

    def paintEvent(self, evt) -> None:  # noqa: N802 — Qt override
        super().paintEvent(evt)
        from mira.ui.design.icons import tinted_svg_pixmap
        icon = 26
        pm = tinted_svg_pixmap(_GLYPH_PATH, icon, self._tint)
        if pm.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        x = (self.width() - icon) // 2
        y = (self.height() - icon) // 2
        p.drawPixmap(x, y, pm)
        p.end()


class CrossEventCutsBand(QFrame):
    """Accent-bordered band hosting the cross-event search entry.

    Signals:
        submitted(str)  query string entered into the search field
                        (emitted on Search-button click or Return press).
        new_dc_requested()  ghost-button + sigil emit this when the user
                        asks for the new-cross-event-collection dialog
                        (spec/81 Phase 2 — Item 5). Host opens
                        :class:`NewCrossEventDcDialog` and on accept calls
                        :meth:`LibraryGateway.create_dc`.
    """

    submitted = pyqtSignal(str)
    new_dc_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CrossEventBand")
        self._build_layout()
        # No QGraphicsDropShadowEffect: Qt re-paints every child widget
        # through the effect, which in dark mode swaps the children's
        # transparent backgrounds for opaque black boxes (the "Cross-
        # Event Cuts" title + subtitle came out on black blocks). The
        # accent border + gradient on the band already mark it as the
        # hero entry point — no shadow needed.

    def _build_layout(self) -> None:
        h = QHBoxLayout(self)
        # Hero padding — bumped from 14 to 18 vertical so the band reads as
        # the "designated entry point" the spec (§3.1) calls for. Matches
        # the mockup's 16px+ vertical breathing room while staying short
        # enough that the band doesn't dominate the page.
        h.setContentsMargins(20, 18, 20, 18)
        h.setSpacing(16)

        # Accent icon tile (50px). Glyph = stacked frames + magnifier SVG
        # tinted accent (path data extracted from surface-01-initial-app.html
        # into assets/icons/glyphs/cross_event.svg).
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        tint = QColor(PALETTE[mode]["accent"])
        h.addWidget(_CrossEventGlyph(tint))

        # Label block. Title gets tightened letter-spacing (-0.3) and a
        # touch of extra weight so it punches as a hero CTA — the mockup's
        # `letter-spacing:-.2px` plus the band's accent border are what
        # make it read as bigger than its 16px nominal size.
        label_box = QVBoxLayout()
        label_box.setContentsMargins(0, 0, 0, 0)
        label_box.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel("Cross-Event Cuts")
        title.setObjectName("CardTitle")
        title_font = QFont(title.font())
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.3)
        title.setFont(title_font)
        title_row.addWidget(title)
        title_row.addWidget(tag("Preview"))
        title_row.addStretch()
        label_box.addLayout(title_row)
        sub = QLabel("Search and build cuts across every event at once.")
        sub.setObjectName("Sub")
        sub.setWordWrap(True)
        label_box.addWidget(sub)
        h.addLayout(label_box, 2)

        # Search field — 17px glyph per mockup hero-input spec
        self._search = search_field(
            "Search captures, picks, cuts and tags across all events…",
            glyph_size=17,
        )
        h.addWidget(self._search, 3)
        self._search.input.returnPressed.connect(self._emit)

        # New cross-event collection — ghost button next to Search; the
        # primary entry to the spec/81 §2.1 cross-event surface (Item 5).
        new_btn = ghost_button("+ Collection")
        new_btn.clicked.connect(self.new_dc_requested.emit)
        h.addWidget(new_btn)
        self._new_dc_button = new_btn

        # Primary Search button
        btn = primary_button("Search")
        btn.clicked.connect(self._emit)
        h.addWidget(btn)

    def _emit(self) -> None:
        text = self._search.input.text().strip()
        if text:
            self.submitted.emit(text)

    def query(self) -> str:
        return self._search.input.text().strip()

    def clear(self) -> None:
        self._search.input.clear()
