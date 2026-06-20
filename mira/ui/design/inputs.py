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

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFocusEvent, QMouseEvent, QPixmap, QWheelEvent
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


def _render_search_glyph(size: int = 16) -> QPixmap:
    """Render the search.svg glyph at ``size`` px, tinted ink_soft for the
    active theme. The shared helper (spec/69) owns the cache + the
    SourceIn tint pass — this just resolves the right palette colour
    per theme."""
    from mira.ui.design.icons import GLYPH_SEARCH, tinted_svg_pixmap
    app = QApplication.instance()
    mode = (app.property("theme") if app else None) or "dark"
    return tinted_svg_pixmap(
        GLYPH_SEARCH, size, QColor(PALETTE[mode]["ink_soft"]))


def line_input(
    placeholder: str = "", parent: QWidget | None = None
) -> QLineEdit:
    e = QLineEdit(parent)
    e.setObjectName("DesignInput")
    if placeholder:
        e.setPlaceholderText(placeholder)
    return e


class _DesignSelect(QComboBox):
    """Themed QComboBox that ignores the mouse wheel unless the user has
    actually engaged the combo (spec/75 §3.3).

    Root cause of the original bug #1 (filter dropdown silently changing
    on scroll): Qt's ``WheelFocus`` + window-activation churn can mark a
    combo focused on mere hover, and the app-wide
    ``mira.ui.base.wheel_guard`` then lets the wheel through because the
    widget *is* focused. The Days Table's ``TZ`` and ``Country`` pickers
    already work around this by tracking an explicit ``_user_engaged``
    flag that flips on only on a real click / Tab / Backtab / Shortcut
    focus (see ``mira/ui/base/tz_picker.py:187`` and
    ``country_picker.py:104``). Promoting the same pattern to the
    design-system ``select()`` factory fixes every dropdown built from
    the catalog at once — the events filters and any future surface that
    asks for a themed combo.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._user_engaged = False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._user_engaged = True
        super().mousePressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        # Real focus reasons that mean intent: keyboard traversal
        # (Tab/Backtab) or a shortcut. Wheel-on-hover, MouseFocusReason,
        # ActiveWindowFocusReason and OtherFocusReason all stay
        # un-engaged so the wheel keeps falling through to the scroll
        # area underneath.
        if event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        ):
            self._user_engaged = True
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        # The combo loses focus when the user clicks away OR when the
        # popup opens (``PopupFocusReason``). Treating the popup as a
        # disengagement would close the loop: pop opens → flag clears →
        # scroll inside the popup ignored. Keep the flag set in that
        # case so the popup itself behaves normally.
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self._user_engaged = False
        super().focusOutEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self._user_engaged:
            event.ignore()
            return
        super().wheelEvent(event)


def select(items: list[str], parent: QWidget | None = None) -> QComboBox:
    """Themed QComboBox that ignores the wheel unless engaged (spec/75
    §3.3). Returns a :class:`_DesignSelect` — drop-in for ``QComboBox``;
    callers continue to use ``addItems`` / ``currentIndexChanged`` etc.
    without change."""
    c = _DesignSelect(parent)
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
        self._glyph.setObjectName("SearchGlyph")  # transparent by default (QLabel base, redesign.qss)
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
