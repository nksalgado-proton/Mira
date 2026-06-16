"""``CutDraft`` — the dialog→session handoff value (spec/61 §2 step 7).

The New Cut dialog snapshots its widgets into a :class:`CutDraft`;
the picking session resolves the recipe (pool + filters) into a
``files`` set + decisions ledger via
:meth:`mira.shared.cut_session.CutSession.from_draft`. Pure data — no
Qt, no gateway. Lives in :mod:`mira.shared` alongside
:class:`~mira.shared.cut_session.CutSession` so both the dialog
adapter and the engine consume the same shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

#: A pool expression: a sequence of ``(op, tag)`` tuples evaluated
#: left-to-right by :meth:`EventGateway.resolve_pool` (``"+"`` unions,
#: ``"-"`` subtracts). Tags are the bare cut names (``"exported"``,
#: ``"best_macro"`` …) — no leading ``#``.
PoolExpr = Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class CutDraft:
    """Everything the dialog composes; the picking session turns it
    into a ``cut`` row + membership at Create Cut (spec/61 §2 step
    7)."""

    name: str
    tag: str
    pool_expr: PoolExpr
    style_filter: Tuple[str, ...]   # () = All styles
    type_filter: str                # 'both' | 'photo' | 'video'
    default_state: str              # 'skipped' | 'picked'
    target_s: Optional[int]         # None = no limit
    max_s: Optional[int]
    photo_s: float
    music_category: Optional[str]   # None = no music
    card_style: str = "black"       # 'black' | 'single' | 'multi'


__all__ = ["CutDraft", "PoolExpr"]
