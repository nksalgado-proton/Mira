""":mod:`mira.ui.design.form_field` — the shared form-field primitives.

Two widgets promoted from ``mira/ui/pages/event_header_dialog.py`` for
reuse across every Mira dialog (spec/162 relayout A). Both compose from
QSS roles defined in ``assets/themes/redesign.qss``; no callers should
duplicate the layout logic locally.

* :func:`field` — wrap a bare input widget in a ``#FormFieldGroup``
  ``QGroupBox`` with the title notched into the top border
  (spec/92 §2.3.1). Every input in a Mira dialog lives inside one of
  these; labels never sit to the left of an input.

* :class:`SectionHeader` — the small uppercase accent label + fading
  underline that separates sections (IDENTITY / LOGISTICS / …) inside
  a dialog body (spec/92 §2.4).

The module is one-way-dep clean: it imports from ``mira.ui.palette``
and nothing under ``mira/ui/pages/*``.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mira.ui.palette import PALETTE


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


def _with_alpha(hex_color: str, alpha255: int) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha255 / 255:.3f})"


class SectionHeader(QWidget):
    """Section divider — small uppercase accent label + a thin accent
    underline rule + a touch of breathing room above.

    Spec/65 §3.2 / spec/92 §2.4: the bare-label section dividers read as
    decoration; the rule beneath each one is what turns them into a real
    visual cleft. The accent fades out across the row so the rule reads
    as a soft underline, not a hard divider — matches the design-system's
    quieter group rules.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 14, 0, 4)
        v.setSpacing(6)
        p = PALETTE[_palette_mode()]
        lbl = QLabel(text.upper())
        lbl.setObjectName("SectionEyebrow")  # spec/92 §2.4 (redesign.qss)
        v.addWidget(lbl)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(  # pragma: no-qss — decorative accent-fade gradient (computed)
            "background: qlineargradient("
            f"x1:0, y1:0, x2:1, y2:0,"
            f" stop:0 {_with_alpha(p['accent'], 90)},"
            f" stop:0.6 {_with_alpha(p['accent'], 28)},"
            f" stop:1 {_with_alpha(p['line'], 0)});"
            " border: none;"
        )
        v.addWidget(rule)


def field(
    title: str, widget: QWidget, *, required: bool = False
) -> QGroupBox:
    """Wrap ``widget`` in the canonical ``#FormFieldGroup`` titled group
    box (spec/92 §2.3.1). The title is rendered UPPERCASE per §2.0
    ('labelling chrome' tier); required fields append a trailing ``*``
    (the accent-coloured ``[required="true"]`` selector is reserved for
    a later sweep per spec/92 Appendix A — for now the asterisk inherits
    the title's ink_soft tint, consistent with the New Cut surface).

    The group box owns the title rendering (notched into the top
    border); the inner ``QVBoxLayout`` holds the bare input widget so
    caller-side wiring (``signals``, ``setObjectName``, ``setToolTip``)
    is unchanged. spec/92 §4 Stage 2b."""
    box = QGroupBox()
    box.setObjectName("FormFieldGroup")
    raw = title.upper() + (" *" if required else "")
    box.setTitle(raw)
    inner = QVBoxLayout(box)
    inner.setContentsMargins(0, 6, 0, 0)
    inner.setSpacing(0)
    inner.addWidget(widget)
    return box


__all__ = ["SectionHeader", "field"]
