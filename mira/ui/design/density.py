"""Compact "dense" control tier — opt a chrome subtree into slimmer
button / combo / checkbox heights + smaller caption fonts.

Reused across the four media surfaces (Picker / Editor × photo / video),
where vertical room for the media itself is at a premium and the standard
chrome metrics (a ~34 px ghost button, a 13 px caption) stack up fast.

The metrics live in ``redesign.qss`` under the ``[dense="true"]`` selectors,
NOT in widget code — ``QStyleSheetStyle`` clobbers ``setMaximumHeight`` (see
the ``#PlanBrowseCell`` precedent rule). This helper only TAGS the widgets
with the dynamic property and repolishes them so the QSS reapplies.

Scope is deliberate: only the flat chrome roles (Ghost / Primary /
DangerGhost buttons, combos, checkboxes, and ``#Sub`` caption labels) are
slimmed. Segmented Look pills / state chips carry their own roles + metrics
and are left untouched, so a blunt subtree walk can't distort them.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QPushButton,
    QWidget,
)

# Floating overlay buttons that must KEEP their own (round, oversized)
# metrics — they're meant to sit over the media, not in a chrome row.
# Everything else under the subtree is fair game (flat ghosts, the
# FeatureToggle Look pills / toggles, the no-id rotate / Reset buttons).
_DENSE_BUTTON_SKIP = {"MediaNavArrow", "CarouselArrow", "CarouselDot"}


def _tag(widget: QWidget) -> None:
    """Set the dense flag and repolish so the [dense="true"] QSS reapplies."""
    widget.setProperty("dense", True)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def apply_density(root: QWidget) -> None:
    """Tag the chrome controls beneath ``root`` into the compact tier.

    Idempotent — re-tagging an already-dense widget is harmless. Call it
    after the subtree is built (e.g. at the end of a page's ``_build_ui``,
    or after reparenting an engine's tools widget into the page)."""
    for btn in root.findChildren(QPushButton):
        if btn.objectName() not in _DENSE_BUTTON_SKIP:
            _tag(btn)
    for combo in root.findChildren(QComboBox):
        _tag(combo)
    for chk in root.findChildren(QCheckBox):
        _tag(chk)
    for lbl in root.findChildren(QLabel):
        if lbl.objectName() == "Sub":
            _tag(lbl)


__all__ = ["apply_density"]
