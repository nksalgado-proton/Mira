"""``CutDraft`` — the dialog→pin-session handoff value (spec/81 §3-§4 +
spec/90 §1.5 rule-list extension).

The New Cut dialog snapshots its widgets into a :class:`CutDraft`; the pin
session sources its candidate set from a DC resolution and turns the draft into
a ``cut`` row + frozen membership on commit. Pure data — no Qt, no gateway.

A Cut is made from a DC: the draft carries either a ``source_dc_id`` (a saved
DC) or an inline ``expr`` + ``filters`` (an ad-hoc formula). Style + media
filters live on the DC side (``filters``), never on the Cut.

``pin_mode`` selects how the budget pass starts. The three legacy values
(``keep-all`` / ``weed-out`` / ``pick-in``) carry over verbatim (spec/81 §4
/ spec/80 §2). spec/90 §1.5 adds a fourth — ``rule-based`` — for Recipes
that compose a non-trivial rule list (the `#short` shape and friends): the
picker seeds each item by walking ``rules`` first-match-wins, falling back
to ``otherwise`` for items matching no rule. The legacy modes remain
expressible as the §1.5 sugar (no rules + ``otherwise`` = pick/skip); they
just don't go through ``rule-based`` so the picker keeps its existing
all-in / all-out / curate paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

#: A DC expression: ordered ``(op, operand)`` pairs evaluated left-to-right by
#: :meth:`EventGateway.resolve_dc`. An operand is the base token ``"exported"``
#: or a typed ref ``{"kind":"dc"|"cut","id":...,"tag":...}`` (spec/81 §2).
Expr = Tuple[Tuple[str, Any], ...]

#: Pin modes (spec/81 §4 / spec/80 §2): how the budget pass starts.
PIN_KEEP_ALL = "keep-all"
PIN_WEED_OUT = "weed-out"
PIN_PICK_IN = "pick-in"
#: spec/90 §1.5 — the rule-list mode. Picker seeds each item by walking
#: :attr:`CutDraft.rules` first-match-wins, falling back to
#: :attr:`CutDraft.otherwise` (a ``'pick'`` / ``'skip'`` verdict) for items
#: matching no rule. Only meaningful when ``rules`` is non-empty; the
#: adapter collapses empty-rules + otherwise back to the legacy three (no
#: rules + ``'skip'`` → :data:`PIN_PICK_IN`; no rules + ``'pick'`` →
#: :data:`PIN_WEED_OUT` — spec/90 §1.5).
PIN_RULE_BASED = "rule-based"

#: Otherwise verdicts (spec/90 §1.3) — the closed enum for the default-when-
#: no-rule-matched verdict. Kept narrow on purpose so the picker doesn't
#: have to interpret arbitrary strings.
OTHERWISE_PICK = "pick"
OTHERWISE_SKIP = "skip"


@dataclass(frozen=True)
class CutDraftRule:
    """One rule in a rule-list :class:`CutDraft` (spec/90 §1.3). Predicate
    is a chip + join-word sentence in the spec/81 expression shape; verdict
    is the closed ``'pick'`` / ``'skip'`` enum (:data:`OTHERWISE_PICK` /
    :data:`OTHERWISE_SKIP`). First match wins per item (spec/90 §1.3)."""

    predicate: Expr
    verdict: str


@dataclass(frozen=True)
class CutDraft:
    """Everything the dialog composes; the pin session turns it into a ``cut``
    row + frozen membership at Create Cut (spec/81 §3-§4 + spec/90 §1.5)."""

    name: str
    tag: str
    source_dc_id: Optional[str] = None
    expr: Expr = ()
    styles: Tuple[str, ...] = ()
    media_type: str = "both"
    pin_mode: str = PIN_WEED_OUT
    target_s: Optional[int] = None
    max_s: Optional[int] = None
    photo_s: float = 6.0
    #: spec/152 §3 — per-Cut crossfade transition (ms). ``None`` =
    #: defer to ``Settings.default_transition_ms``. The New / Adjust
    #: dialog seeds the spinbox from the global default but only
    #: writes here when the user actually overrode it.
    transition_ms: Optional[int] = None
    music_category: Optional[str] = None
    separators: bool = True
    overlay_fields: Tuple[str, ...] = ()
    overlay_mode: Optional[str] = None
    card_style: str = "black"
    #: spec/111 — slideshow canvas aspect ('16:9' / '4:3' / '3:2' /
    #: '1:1'). Sibling to ``photo_s``; both belong to the show.
    aspect: str = "16:9"
    #: spec/90 §1.5 — the rule list (ordered, first-match-wins). Empty for
    #: every legacy ``keep-all`` / ``weed-out`` / ``pick-in`` draft. Carries
    #: through to the picker when ``pin_mode == 'rule-based'``.
    rules: Tuple[CutDraftRule, ...] = ()
    #: spec/90 §1.3 — the verdict for items matching no rule. Required in
    #: ``rule-based`` mode; harmless on legacy modes (empty string =
    #: derive from pin_mode). The adapter sets this explicitly on every
    #: round-trip so the picker never has to.
    otherwise: str = ""
    #: spec/94 Phase 3 — initial Pick/Skip verdicts per resolved member,
    #: computed by the dialog's :func:`core.recipe_resolver.resolve_recipe`
    #: call at Start time. Tuple of ``(export_relpath, picked)`` pairs so
    #: the dataclass stays frozen-friendly. Empty = no seed; the session
    #: falls back to the :attr:`pin_mode` default. Not part of the saved
    #: Recipe's identity — the round-trip
    #: :func:`mira.shared.recipe_draft_adapter.cut_draft_to_recipe_composition`
    #: drops it.
    seed: Tuple[Tuple[str, bool], ...] = ()

    @property
    def filters(self) -> dict:
        return {"styles": list(self.styles), "media_type": self.media_type}


@dataclass(frozen=True)
class CrossEventCutDraft:
    """The cross-event counterpart of :class:`CutDraft` (spec/81 Phase 2).
    Same handoff role — the New Cross-Event Cut dialog snapshots its widgets
    into one of these; the cross-event pin session
    (:class:`mira.shared.cross_event_cut_session.CrossEventCutSession`) sources
    its candidates from a cross-event DC resolution and writes a cut row +
    cross-event membership on commit.

    The catalogue widens (spec/81 §2.1): instead of the event-scope
    ``styles`` + ``media_type`` pair, this draft carries the full spec/32 §2
    ``filters`` dict. The session resolves via :class:`LibraryGateway` over
    ``mira.db``, the commit lands in an anchor event's ``event.db``
    (``anchor_event_id`` — schema v8 hosts cross-event Cuts there).

    Attachment defaults flip cross-event: ``separators`` defaults to OFF (no
    single timeline to orient — spec/81 §3.1), overlays default ON in the
    UI (the dialog wires it in)."""

    name: str
    tag: str
    source_dc_id: Optional[str] = None             # saved_filter id
    expr: Expr = ()
    filters: dict = None                            # spec/32 §2 catalogue
    pin_mode: str = PIN_WEED_OUT
    target_s: Optional[int] = None
    max_s: Optional[int] = None
    photo_s: float = 6.0
    music_category: Optional[str] = None
    anchor_event_id: Optional[str] = None           # event.db that hosts the Cut
    separators: bool = False                        # cross-event default OFF
    overlay_fields: Tuple[str, ...] = ()
    overlay_mode: Optional[str] = None
    card_style: str = "black"
    # spec/111 — slideshow canvas aspect (sibling to ``photo_s``).
    aspect: str = "16:9"

    def __post_init__(self):
        # ``dict`` default = None pattern: frozen dataclasses can't mutate
        # in __init__, but they CAN via object.__setattr__ inside
        # __post_init__. Empty dict means "no narrowing".
        if self.filters is None:
            object.__setattr__(self, "filters", {})


__all__ = [
    "CrossEventCutDraft", "CutDraft", "CutDraftRule", "Expr",
    "OTHERWISE_PICK", "OTHERWISE_SKIP",
    "PIN_KEEP_ALL", "PIN_PICK_IN", "PIN_RULE_BASED", "PIN_WEED_OUT",
]
