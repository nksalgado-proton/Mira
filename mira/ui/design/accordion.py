"""Accordion + Recipe-container primitives (spec/162 §4.2–§4.5).

Reusable widgets that back the redesigned New Cut / Edit Cut dialog's
Stage A. Slice 2 lands the primitives + their unit tests; Slice 3 wires
them into the dialog scaffolding.

Three exports:

* :class:`AccordionSection` — a card with a clickable header
  (``#AccordionHeader``) that toggles the visibility of an inner content
  widget. The section itself wears the ``#AccordionSection`` role + a
  ``expanded="true"|"false"`` dynamic property so the QSS from Slice 1
  can drive per-state styling (and the widget-side chevron flip).
* :class:`RecipeContainer` — the outer Stage-A frame
  (``#RecipeContainer``). Holds a ``#RecipeContainerHeader`` row
  (Recipe-name label + injectable Load / Save Recipe buttons) and a
  vertical body that accepts :class:`AccordionSection`s.
* :class:`StrictAccordionGroup` — the strict-accordion arbitrator (§4.5).
  Given a list of sections, enforces "exactly one expanded at a time" by
  intercepting each section's ``toggled`` signal and rebalancing peers.
  Set ``allow_all_collapsed=True`` to permit the all-collapsed state; the
  default follows spec/162 §4.5.

QSS-only styling — no ``setStyleSheet`` in this module. All twelve roles
consumed here landed in ``assets/themes/redesign.qss`` under the
``spec/162`` section during Slice 1.
"""
from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


# ── glyphs ──────────────────────────────────────────────────────────
# Unicode chevrons per spec/162 §4.5. Widget-side text swap because
# QSS ``content:`` is not honoured on QLabel.
_CHEVRON_EXPANDED = "▾"   # ▾
_CHEVRON_COLLAPSED = "▸"  # ▸


def _repolish(widget: QWidget) -> None:
    """Kick the QSS cascade so a freshly-set dynamic property takes effect."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


class _AccordionHeader(QWidget):
    """Clickable header row for an :class:`AccordionSection`.

    Emits :attr:`clicked` on a left-mouse-button release inside the
    widget. Wears the ``#AccordionHeader`` role — QSS from Slice 1
    carries the hover + pressed backgrounds. A ``QWidget`` (not a
    ``QPushButton``) so the row can host title + chevron + summary chip
    in a plain QHBoxLayout without fighting Qt's button paint.
    """

    clicked = pyqtSignal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AccordionHeader")
        # Allow the QSS `#AccordionHeader` background + `:hover` pseudo-state
        # to paint on a plain QWidget. WA_Hover fires the enter/leave events
        # Qt's style engine needs to flip the `:hover` selector.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # The app-level clickable-cursor filter only covers QPushButton /
        # QToolButton subclasses; a bare QWidget needs its own setCursor.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._chevron = QLabel(_CHEVRON_COLLAPSED, self)
        self._chevron.setObjectName("Sub")
        row.addWidget(self._chevron)

        self._title = QLabel(title, self)
        self._title.setObjectName("Sub")
        row.addWidget(self._title)

        row.addStretch(1)

        self._summary = QLabel("", self)
        self._summary.setObjectName("AccordionSummaryChip")
        # Hide until the caller sets a non-empty summary — an empty chip
        # would render as an odd stub in the header.
        self._summary.hide()
        row.addWidget(self._summary)

    def set_chevron(self, expanded: bool) -> None:
        self._chevron.setText(
            _CHEVRON_EXPANDED if expanded else _CHEVRON_COLLAPSED
        )

    def set_summary(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._summary.setText(text)
            self._summary.show()
        else:
            self._summary.clear()
            self._summary.hide()

    def summary_text(self) -> str:
        return self._summary.text()

    def title_text(self) -> str:
        return self._title.text()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setProperty("pressed", "true")
            _repolish(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_pressed = self.property("pressed") == "true"
        if was_pressed:
            self.setProperty("pressed", "false")
            _repolish(self)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class AccordionSection(QWidget):
    """One card in a strict-accordion group.

    Constructor:
        ``AccordionSection(title, content, *, summary="", parent=None)``

        * ``title`` — text on the left of the header row.
        * ``content`` — the inner widget shown while expanded; hidden
          while collapsed. Reparented into the section.
        * ``summary`` — optional initial text for the right-aligned
          ``#AccordionSummaryChip``. Update later via
          :meth:`set_summary`.

    Signals:
        ``toggled(bool)`` — emitted when the expanded state actually
        changes (via header click or :meth:`set_expanded`). Not fired
        for no-op calls.

    A bare :class:`AccordionSection` (not wrapped in a
    :class:`StrictAccordionGroup`) flips state freely on every header
    click. The strict "exactly one expanded" invariant is the group's
    job, not the section's.
    """

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        summary: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AccordionSection")
        # Border-only QSS role — WA_StyledBackground makes the frame
        # geometry behave consistently across styles.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._header = _AccordionHeader(title, self)
        self._header.clicked.connect(self._on_header_clicked)
        v.addWidget(self._header)

        self._content = content
        self._content.setParent(self)
        v.addWidget(self._content)

        # Default: collapsed. StrictAccordionGroup expands its
        # `initially_expanded` section on wire-up; a bare section stays
        # collapsed so an unattached one doesn't render as an odd
        # already-expanded stub.
        self._expanded = False
        self._apply_expanded()

        if summary:
            self.set_summary(summary)

    # ── public API ──────────────────────────────────────────────

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """Programmatic expand / collapse. Fires ``toggled(bool)`` iff
        the state actually changes."""
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._apply_expanded()
        self.toggled.emit(expanded)

    def set_summary(self, text: str) -> None:
        """Update the right-aligned summary chip."""
        self._header.set_summary(text)

    def summary_text(self) -> str:
        return self._header.summary_text()

    def title_text(self) -> str:
        return self._header.title_text()

    def header(self) -> QWidget:
        """The clickable header widget. Exposed for advanced hosts +
        tests that need to simulate a click on it."""
        return self._header

    def content(self) -> QWidget:
        """The inner widget the section shows when expanded."""
        return self._content

    # ── internal ────────────────────────────────────────────────

    def _on_header_clicked(self) -> None:
        # Bare-section behaviour: toggle freely. StrictAccordionGroup
        # observes the `toggled` signal that this call fires and
        # rebalances peers.
        self.set_expanded(not self._expanded)

    def _apply_expanded(self) -> None:
        self.setProperty("expanded", "true" if self._expanded else "false")
        _repolish(self)
        # Widget-side chevron swap.
        self._header.set_chevron(self._expanded)
        # Content visibility.
        self._content.setVisible(self._expanded)


class RecipeContainer(QFrame):
    """The Stage-A frame in the New Cut / Edit Cut dialog (spec/162 §4.2).

    Constructor:
        ``RecipeContainer(recipe_name="(new · unsaved)", *,
                          load_button=None, save_button=None, parent=None)``

        * ``recipe_name`` — text shown after the ``Recipe:`` prefix in
          the header row.
        * ``load_button`` / ``save_button`` — widgets injected into the
          right of the header row. Kept as parameters (not built here)
          so the container stays testable without wiring to
          ``RecipeStore``; Slice 3 will pass ghost buttons wired to the
          dialog's Recipe controller. Either may be ``None``.

    Body content is added via :meth:`add_section` — the accordion
    sections stack vertically under the ``#RecipeContainerHeader`` +
    hairline divider.

    Slice 3 note: the ``Recipe:`` prefix label + the default
    ``"(new · unsaved)"`` are literal strings here. Slice 3 wraps them
    with :func:`tr` at wire-time; the primitive stays i18n-agnostic so
    tests don't need translation fixtures.
    """

    def __init__(
        self,
        recipe_name: str = "(new · unsaved)",
        *,
        load_button: QWidget | None = None,
        save_button: QWidget | None = None,
        bordered: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """Build the Stage-A frame.

        ``bordered`` (default ``True``) controls whether the widget
        picks up the ``#RecipeContainer`` + ``#RecipeContainerHeader``
        QSS roles that paint the card2 fill + accent_soft border +
        hairline header divider. Pass ``bordered=False`` from callers
        that want the header + body structure without the visible
        frame (spec/162 relayout D — NewCutDialog stops painting the
        outer Recipe frame so the ``#FormFieldGroup`` boxes read as
        the only frames in the body). External callers keep the
        default and their visual output is unchanged.
        """
        super().__init__(parent)
        self._bordered = bool(bordered)
        if self._bordered:
            self.setObjectName("RecipeContainer")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # ── #RecipeContainerHeader row ──────────────────────
        self._header = QFrame(self)
        if self._bordered:
            self._header.setObjectName("RecipeContainerHeader")
        header_row = QHBoxLayout(self._header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        prefix = QLabel("Recipe:", self._header)
        prefix.setObjectName("Sub")
        header_row.addWidget(prefix)

        self._name_label = QLabel(recipe_name, self._header)
        self._name_label.setObjectName("CardTitle")
        header_row.addWidget(self._name_label)

        header_row.addStretch(1)

        if load_button is not None:
            header_row.addWidget(load_button)
        if save_button is not None:
            header_row.addWidget(save_button)

        outer.addWidget(self._header)

        # ── accordion body host ─────────────────────────────
        self._body = QWidget(self)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(8)
        outer.addWidget(self._body)

    # ── public API ──────────────────────────────────────────────

    def set_recipe_name(self, name: str) -> None:
        """Update the Recipe-name label. Does not touch the ``Recipe:``
        prefix or the header divider."""
        self._name_label.setText(name)

    def recipe_name(self) -> str:
        return self._name_label.text()

    def add_section(self, section: AccordionSection) -> None:
        """Append an :class:`AccordionSection` to the accordion body."""
        self._body_layout.addWidget(section)

    def sections(self) -> list[AccordionSection]:
        """The accordion sections currently in the body, in insertion
        order."""
        out: list[AccordionSection] = []
        for i in range(self._body_layout.count()):
            item = self._body_layout.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, AccordionSection):
                out.append(w)
        return out

    def header_widget(self) -> QFrame:
        """The ``#RecipeContainerHeader`` row. Exposed for tests + hosts
        that want to reach the injected load / save buttons."""
        return self._header


class StrictAccordionGroup:
    """Enforce "exactly one expanded" across a set of
    :class:`AccordionSection`s (spec/162 §4.5).

    Not a QWidget — a plain arbitrator that listens to each section's
    ``toggled`` signal and rebalances peers by calling
    :meth:`AccordionSection.set_expanded` on them. A guard flag breaks
    the resulting one-hop cascade so no re-entrant signalling occurs.

    Args:
        sections: the sections to arbitrate. Insertion order matters —
            :attr:`initially_expanded` indexes into this list.
        allow_all_collapsed: if ``False`` (default, per spec/162 §4.5),
            clicking the currently-expanded section re-expands it —
            the group refuses to let the accordion enter the
            all-collapsed state. If ``True``, clicking the expanded
            section collapses it, allowing all-collapsed.
        initially_expanded: index of the section to expand on wire-up
            (default ``0``). Pass ``-1`` to leave every section
            collapsed on wire-up (only meaningful when
            ``allow_all_collapsed=True``).
    """

    def __init__(
        self,
        sections: Iterable[AccordionSection],
        *,
        allow_all_collapsed: bool = False,
        initially_expanded: int = 0,
    ) -> None:
        self._sections: list[AccordionSection] = list(sections)
        self._allow_all_collapsed = bool(allow_all_collapsed)
        # Break re-entrant rebalancing: our set_expanded calls on peers
        # fire their toggled signals, which route back into this handler.
        self._rebalancing = False

        for s in self._sections:
            s.toggled.connect(
                lambda expanded, s=s: self._on_section_toggled(s, expanded)
            )

        if not self._sections:
            return
        if initially_expanded < 0:
            if not self._allow_all_collapsed:
                # Same guard we enforce on click — an all-collapsed
                # start state would violate the group's invariant.
                self._sections[0].set_expanded(True)
            # else: leave everyone collapsed.
        else:
            target = min(initially_expanded, len(self._sections) - 1)
            self._rebalancing = True
            try:
                for j, s in enumerate(self._sections):
                    s.set_expanded(j == target)
            finally:
                self._rebalancing = False

    def sections(self) -> list[AccordionSection]:
        return list(self._sections)

    def expanded_index(self) -> int:
        """Index of the currently-expanded section, or ``-1`` if none."""
        for i, s in enumerate(self._sections):
            if s.is_expanded():
                return i
        return -1

    def allow_all_collapsed(self) -> bool:
        return self._allow_all_collapsed

    def _on_section_toggled(
        self, source: AccordionSection, expanded: bool
    ) -> None:
        if self._rebalancing:
            return
        self._rebalancing = True
        try:
            if expanded:
                # Collapse every other section.
                for s in self._sections:
                    if s is not source:
                        s.set_expanded(False)
            else:
                if not self._allow_all_collapsed:
                    # Re-expand the section that was just collapsed —
                    # the group refuses the all-collapsed state.
                    source.set_expanded(True)
        finally:
            self._rebalancing = False


__all__ = [
    "AccordionSection",
    "RecipeContainer",
    "StrictAccordionGroup",
]
