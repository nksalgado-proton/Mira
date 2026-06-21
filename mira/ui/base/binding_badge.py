"""Binding badge (spec/93 §7).

A small read-only chip that reports a definition's placement —
*Global* / *Event: <name>* / *Cross-event* — derived live from the
classifier output (:func:`core.placement_classifier.classify_placement`).

The badge never offers a control: spec/93 §7 ("no user-facing 'where
does this live' control"). Its job is honesty — the user sees WHY a
definition is global / bound, and a one-line migration note appears
when an edit flips the classification.

Visual treatment is QSS-driven via the standard
``setObjectName('BindingBadge')`` + dynamic ``tone`` property
(``global`` / ``bound`` / ``cross_bound``) so the redesign template
can style the three tones independently. No inline stylesheets.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget

from core.placement_classifier import (
    BoundPlacement,
    PLACEMENT_CROSS_BOUND,
    PLACEMENT_GLOBAL,
    Placement,
    placement_badge_text,
)
from mira.ui.i18n import tr


_TONE_GLOBAL = "global"
_TONE_BOUND = "bound"
_TONE_CROSS_BOUND = "cross_bound"


class BindingBadge(QLabel):
    """Read-only chip showing the current placement.

    Call :meth:`set_placement` on every composition change to refresh
    the text + the dynamic ``tone`` property. Style is QSS:

        #BindingBadge[tone="global"]      { ... }
        #BindingBadge[tone="bound"]       { ... }
        #BindingBadge[tone="cross_bound"] { ... }

    Until the redesign template carries rules for these selectors the
    badge renders as a plain themed label — readable, just not
    distinct between tones.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("BindingBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Default to global until the first refresh runs.
        self.set_placement(PLACEMENT_GLOBAL)

    def set_placement(
        self,
        placement: Placement,
        *,
        event_name: str = "",
    ) -> None:
        """Update the badge text + the ``tone`` property to match.

        ``event_name`` is the human-readable name for the BOUND case
        (the caller resolves the event_id to a name via the
        gateway). Falls back to a short id stub if absent.
        """
        text = self._localize(placement, event_name=event_name)
        self.setText(text)
        tone = self._tone(placement)
        self._reapply_tone(tone)
        self.setToolTip(self._tooltip(placement, event_name=event_name))

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _tone(placement: Placement) -> str:
        if placement == PLACEMENT_GLOBAL:
            return _TONE_GLOBAL
        if placement == PLACEMENT_CROSS_BOUND:
            return _TONE_CROSS_BOUND
        if isinstance(placement, BoundPlacement):
            return _TONE_BOUND
        return _TONE_GLOBAL

    @staticmethod
    def _localize(placement: Placement, *, event_name: str = "") -> str:
        """Wrap :func:`placement_badge_text` in tr() so the strings
        land in the translation catalogue."""
        if placement == PLACEMENT_GLOBAL:
            return tr("Global")
        if placement == PLACEMENT_CROSS_BOUND:
            return tr("Cross-event")
        if isinstance(placement, BoundPlacement):
            if event_name:
                return tr("Event: {name}").replace("{name}", event_name)
            return tr("Event: {id}").replace(
                "{id}", placement.event_id[:8])
        return placement_badge_text(placement)

    @staticmethod
    def _tooltip(placement: Placement, *, event_name: str = "") -> str:
        """Explain WHY this badge reads what it does — the spec/93 §7
        "honesty without management" contract."""
        if placement == PLACEMENT_GLOBAL:
            return tr(
                "Global — this definition uses only the universal "
                "vocabulary, so it lives in your library and is "
                "available in every event."
            )
        if placement == PLACEMENT_CROSS_BOUND:
            return tr(
                "Cross-event — this definition pins concrete Cuts "
                "from more than one event, so it lives in your "
                "library and resolves only for those events."
            )
        if isinstance(placement, BoundPlacement):
            name = event_name or placement.event_id[:8]
            return tr(
                "Bound to a single event ({name}) — pins a concrete "
                "Cut/Collection from that event, so it only appears "
                "there. Remove the pinned operand to make it global."
            ).replace("{name}", name)
        return ""

    def _reapply_tone(self, tone: str) -> None:
        """Set the dynamic ``tone`` property and re-polish so QSS
        selectors update. PyQt6 needs the unpolish/polish dance for
        property-driven styling to refresh."""
        self.setProperty("tone", tone)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)


__all__ = ["BindingBadge"]
