"""spec/159 §4.5 / §4.6 — the in-memory filter predicate.

Originally hosted a :class:`QToolButton` + :class:`QMenu` popup
(``FilterPopupButton``) but Nelson 2026-06-30 reworked the visual
shape to a proper group-box bar (:class:`mira.ui.exported.filter_bar
.FilterBar`); the popup is gone, only the predicate dataclass
survives. The module name is kept so existing imports of
``LineageFilter`` keep resolving.

spec/162 §8 Round 3 extended the predicate with four cross-event
dimensions (``cameras`` / ``lenses`` / ``date_from`` / ``date_to`` /
``places``). Every new field defaults to a match-anything state so
event-scope callers (:class:`DCDetailPage`, :class:`FilterBar` with
``scope="event"``) continue to build the predicate exactly as before.
The new fields fire only when the host actively sets them at cross-
event scope; a row lacking the attribute (production
:class:`mira.store.models.Lineage` rows don't carry camera / lens /
capture-date / place directly — they live on the joined Item /
TripDay) reads as unmatched, mirroring the existing empty-attribute
semantics on ``colour_labels`` / ``min_stars``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
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


def _coerce_date(value) -> Optional[date]:
    """Best-effort convert a row's capture-date attribute into a
    :class:`datetime.date`. Accepts ``date`` / ``datetime`` /
    ISO-8601-ish string; returns ``None`` on anything else.

    Used by :meth:`LineageFilter.matches` — the cross-event grid
    enriches each row with a ``capture_date`` attribute derived from
    the source item's ``capture_time_corrected``; here we tolerate
    both the raw string form and a pre-parsed :class:`date` so tests
    can seed either shape without ceremony."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None
    return None


@dataclass
class LineageFilter:
    """The in-memory filter predicate the FilterBar writes through.

    ``matches`` is the test the host applies to each lineage row.
    ``is_active`` is the predicate hosts can use to flip an indicator
    when the filter is non-default.

    All fields default to "match anything" so a freshly-constructed
    instance is a no-op filter — :meth:`matches` is ``True`` for
    every row.

    Cross-event fields (spec/162 §8) — ``cameras`` / ``lenses`` /
    ``date_from`` / ``date_to`` / ``places`` — behave the same way
    at the predicate layer regardless of the ``FilterBar.scope`` the
    host runs. The FilterBar keeps the new widgets hidden at event
    scope so the surface stays five knobs; the predicate itself is
    scope-agnostic.
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
    #: spec/162 §8 — camera-model membership. Empty set matches every
    #: row. A non-empty set means "match ONLY rows whose ``camera``
    #: attribute is in this set." Rows lacking a ``camera`` fail when
    #: the set is non-empty.
    cameras: Set[str] = field(default_factory=set)
    #: spec/162 §8 — lens-model membership. Same shape as ``cameras``,
    #: reads ``lens_model`` on the row.
    lenses: Set[str] = field(default_factory=set)
    #: spec/162 §8 — capture-date lower bound (inclusive). ``None`` =
    #: unbounded. Rows lacking a parseable ``capture_date`` fail when
    #: the bound is set (they read as undated).
    date_from: Optional[date] = None
    #: spec/162 §8 — capture-date upper bound (inclusive). ``None`` =
    #: unbounded.
    date_to: Optional[date] = None
    #: spec/162 §8 — place attribution (per-row ``place`` field on the
    #: enriched cross-event lineage projection; production
    #: :class:`mira.store.models.Lineage` doesn't carry a place field
    #: — the FilterBar's ``Places`` widget stays hidden at cross-event
    #: scope pending a per-lineage place attribution, but the
    #: predicate is ready so a future host can enrich rows with a
    #: ``place`` attribute and the filter applies with no more work.
    places: Set[str] = field(default_factory=set)

    def is_active(self) -> bool:
        """``True`` when at least one knob is non-default."""
        return (
            self.min_stars is not None
            or bool(self.colour_labels)
            or self.flag != "any"
            or self.to_delete != "any"
            or bool(self.cameras)
            or bool(self.lenses)
            or self.date_from is not None
            or self.date_to is not None
            or bool(self.places)
        )

    def matches(self, row) -> bool:
        """``True`` when ``row`` passes every active knob.

        ``row`` is a duck-typed dataclass — any object with
        ``stars`` / ``color_label`` / ``flag`` / ``to_delete``
        attributes works (the production caller is
        :class:`mira.store.models.Lineage`). The cross-event caller
        may additionally set ``camera`` / ``lens_model`` /
        ``capture_date`` / ``place`` on an enriched row for the four
        cross-event knobs."""
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
        if self.cameras:
            cam = getattr(row, "camera", None)
            if cam is None or cam not in self.cameras:
                return False
        if self.lenses:
            lens = getattr(row, "lens_model", None)
            if lens is None or lens not in self.lenses:
                return False
        if self.date_from is not None or self.date_to is not None:
            captured = _coerce_date(getattr(row, "capture_date", None))
            if captured is None:
                return False
            if self.date_from is not None and captured < self.date_from:
                return False
            if self.date_to is not None and captured > self.date_to:
                return False
        if self.places:
            place = getattr(row, "place", None)
            if place is None or place not in self.places:
                return False
        return True


__all__ = ["LineageFilter"]
