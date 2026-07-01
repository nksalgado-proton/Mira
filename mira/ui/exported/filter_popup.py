"""spec/159 §4.5 / §4.6 — the in-memory filter predicate.

Originally hosted a :class:`QToolButton` + :class:`QMenu` popup
(``FilterPopupButton``) but Nelson 2026-06-30 reworked the visual
shape to a proper group-box bar (:class:`mira.ui.exported.filter_bar
.FilterBar`); the popup is gone, only the predicate dataclass
survives. The module name is kept so existing imports of
``LineageFilter`` keep resolving.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set


#: spec/159 §2.1 — accepted colour labels (LRC convention).
_COLOR_LABELS: tuple[str, ...] = (
    "red", "yellow", "green", "blue", "purple",
)

#: Tri-state flag values. ``"any"`` lets every row pass; the other
#: two filter to flagged-only / unflagged-only.
_FLAG_STATES: tuple[str, ...] = ("any", "yes", "no")

#: Tri-state for the marked-for-deletion knob. ``"any"`` lets every row
#: pass; ``"only"`` shows ONLY marked rows; ``"hide"`` drops marked rows.
_TO_DELETE_STATES: tuple[str, ...] = ("any", "only", "hide")


@dataclass
class LineageFilter:
    """The in-memory filter predicate the FilterBar writes through.

    ``matches`` is the test the host applies to each lineage row.
    ``is_active`` is the predicate hosts can use to flip an indicator
    when the filter is non-default.

    All fields default to "match anything" so a freshly-constructed
    instance is a no-op filter — :meth:`matches` is ``True`` for
    every row.
    """

    #: ``None`` lets every row pass; ``1..5`` requires that many
    #: stars or more (``None`` star ratings always fail when a min
    #: is set — they read as "unrated").
    min_stars: Optional[int] = None
    #: Empty set matches every colour (and unlabelled rows). A
    #: non-empty set means "match ONLY rows whose label is in this
    #: set." Unlabelled rows fail when the set is non-empty.
    colour_labels: Set[str] = field(default_factory=set)
    #: ``"any"`` / ``"yes"`` / ``"no"``.
    flag: str = "any"
    #: Marked-for-deletion tri-state. ``"any"`` lets every row pass;
    #: ``"only"`` shows ONLY rows with ``to_delete=1``; ``"hide"``
    #: drops them from the rendered list.
    to_delete: str = "any"

    def is_active(self) -> bool:
        """``True`` when at least one knob is non-default."""
        return (
            self.min_stars is not None
            or bool(self.colour_labels)
            or self.flag != "any"
            or self.to_delete != "any"
        )

    def matches(self, row) -> bool:
        """``True`` when ``row`` passes every active knob.

        ``row`` is a duck-typed dataclass — any object with
        ``stars`` / ``color_label`` / ``flag`` / ``to_delete``
        attributes works (the production caller is
        :class:`mira.store.models.Lineage`)."""
        if self.min_stars is not None:
            stars = getattr(row, "stars", None)
            if stars is None or int(stars) < int(self.min_stars):
                return False
        if self.colour_labels:
            label = getattr(row, "color_label", None)
            if label not in self.colour_labels:
                return False
        if self.flag != "any":
            wants_flag = (self.flag == "yes")
            row_flag = bool(getattr(row, "flag", False))
            if row_flag != wants_flag:
                return False
        if self.to_delete != "any":
            row_marked = bool(getattr(row, "to_delete", False))
            if self.to_delete == "only" and not row_marked:
                return False
            if self.to_delete == "hide" and row_marked:
                return False
        return True


__all__ = ["LineageFilter"]
