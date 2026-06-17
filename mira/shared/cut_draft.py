"""``CutDraft`` — the dialog→pin-session handoff value (spec/81 §3-§4).

The New Cut dialog snapshots its widgets into a :class:`CutDraft`; the pin
session sources its candidate set from a DC resolution and turns the draft into
a ``cut`` row + frozen membership on commit. Pure data — no Qt, no gateway.

A Cut is made from a DC: the draft carries either a ``source_dc_id`` (a saved
DC) or an inline ``expr`` + ``filters`` (an ad-hoc formula). Style + media
filters live on the DC side (``filters``), never on the Cut. ``pin_mode``
selects how the budget pass starts (keep-all / weed-out / pick-in).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

#: A DC expression: ordered ``(op, operand)`` pairs evaluated left-to-right by
#: :meth:`EventGateway.resolve_dc`. An operand is the base token ``"exported"``
#: or a typed ref ``{"kind":"dc"|"cut","id":...,"tag":...}`` (spec/81 §2).
Expr = Tuple[Tuple[str, Any], ...]

#: Pin modes (spec/81 §4 / spec/80 §2): how the budget pass starts.
PIN_KEEP_ALL = "keep-all"
PIN_WEED_OUT = "weed-out"
PIN_PICK_IN = "pick-in"


@dataclass(frozen=True)
class CutDraft:
    """Everything the dialog composes; the pin session turns it into a ``cut``
    row + frozen membership at Create Cut (spec/81 §3-§4)."""

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
    music_category: Optional[str] = None
    separators: bool = True
    overlay_fields: Tuple[str, ...] = ()
    overlay_mode: Optional[str] = None
    card_style: str = "black"

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

    def __post_init__(self):
        # ``dict`` default = None pattern: frozen dataclasses can't mutate
        # in __init__, but they CAN via object.__setattr__ inside
        # __post_init__. Empty dict means "no narrowing".
        if self.filters is None:
            object.__setattr__(self, "filters", {})


__all__ = [
    "CrossEventCutDraft", "CutDraft", "Expr",
    "PIN_KEEP_ALL", "PIN_WEED_OUT", "PIN_PICK_IN",
]
