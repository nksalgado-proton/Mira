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
    QHBoxLayout,
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

    spec/162 relayout C — an optional right-aligned summary chip
    (``#AccordionSummaryChip``) rides on the eyebrow row so callers that
    used to hang a live readout off the accordion header can carry the
    same readout into the flat-section layout. Pass ``summary=`` to
    seed it; :meth:`set_summary` updates it. When the summary is empty
    the chip hides so a bare eyebrow row stays uncluttered.
    """

    def __init__(
        self,
        text: str,
        parent: QWidget | None = None,
        *,
        summary: str = "",
    ) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 14, 0, 4)
        v.setSpacing(6)
        p = PALETTE[_palette_mode()]

        # Row 1 — eyebrow + optional summary chip.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        lbl = QLabel(text.upper())
        lbl.setObjectName("SectionEyebrow")  # spec/92 §2.4 (redesign.qss)
        row.addWidget(lbl)
        row.addStretch(1)
        self._summary = QLabel("", self)
        self._summary.setObjectName("AccordionSummaryChip")
        self._summary.hide()
        row.addWidget(self._summary)
        v.addLayout(row)

        # Row 2 — the accent-fade underline rule.
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

        if summary:
            self.set_summary(summary)

    def set_summary(self, text: str) -> None:
        """Update the right-aligned summary chip. Empty text hides the
        chip; a non-empty string shows it."""
        text = (text or "").strip()
        if text:
            self._summary.setText(text)
            self._summary.show()
        else:
            self._summary.clear()
            self._summary.hide()

    def summary_text(self) -> str:
        return self._summary.text()


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
