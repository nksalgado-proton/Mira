"""Dynamic Collection resolution — the set-algebra engine (spec/81 §2).

Pure logic, **no Qt and no DB-seam assumptions** (charter invariant 8 + 1):
the gateway injects every data accessor as a callable, so this module only
knows about operand *shapes* and set algebra. It evaluates a DC's
``expr_json`` (ordered ``[[<op>, <operand>], …]``) into a set of member keys
(export relpaths) and applies the DC's filters.

The model (spec/81):

* **Operators** — ``'+'`` union, ``'-'`` difference, ``'&'`` intersection
  (display ``∩``). All three ship. Evaluated **left-to-right**, no precedence;
  **grouping is done by nesting a DC as an operand** (the resolver recurses).
* **Operands**
    * the base-universe token ``"exported"`` (event scope) → the
      lineage-backed exported-file set (TERMINAL for the cycle guard);
    * a typed ref ``{"kind":"dc","id":…,"tag":…}`` → that DC's **live**
      resolution (recursed; cycle-guarded);
    * a typed ref ``{"kind":"cut","id":…,"tag":…}`` → that Cut's **frozen**
      members (TERMINAL — a Cut never re-queries its DC, spec/81 §5).
* **Filters** narrow the resolved set. Event scope = Style (classification,
  combinable) + media type (photo/video). The dispatch is structured so the
  spec/32 Phase-2 catalogue (EXIF / settings / location) slots in without a
  rewrite (it is left open for Task D).
* **Cycle-safe + memoised** within one resolution pass — a malformed graph
  can never infinite-loop here even though the write seam already rejects
  cycles (the two guards are independent).

The accessors the gateway injects (all pure data, no Qt):

* ``base_universe(token) -> set[str]`` — member keys for a base token
  (``"exported"`` → the exported-file relpaths). Unknown token → empty set.
* ``dc_by_ref(ref) -> Optional[DCExpr]`` — resolve an operand ref to the
  referenced DC's ``(id, expr, filters)`` triple, or None when it is gone
  (graceful shrink — a deleted operand contributes nothing).
* ``cut_members(ref) -> set[str]`` — the frozen member keys of a referenced
  Cut, or empty when it is gone.
* ``apply_filters(keys, filters) -> list[str]`` — narrow + chronologically
  order a member-key set against ``filters_json``; the gateway owns the SQL.

The output is the ordered (chronological) member-key list ``apply_filters``
returns for the top-level DC.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

#: The base-universe tokens (spec/81 §2.1). Event scope offers ONLY
#: :data:`BASE_EXPORTED`; cross-event offers the full ladder
#: (:data:`BASE_COLLECTED` / :data:`BASE_PICKED` / :data:`BASE_EDITED` /
#: :data:`BASE_EXPORTED`) so a cross-event DC can reach what *didn't* finish,
#: not just what did (spec/61 §8). The resolver itself is scope-agnostic —
#: the caller injects ``base_universe`` and decides which tokens to honour.
BASE_EXPORTED = "exported"
BASE_COLLECTED = "collected"
BASE_PICKED = "picked"
BASE_EDITED = "edited"

#: Every ladder rung as one set (the cross-event accessor uses this to
#: validate operand tokens against the spec/81 §2.1 ladder).
LADDER_TOKENS = frozenset({BASE_COLLECTED, BASE_PICKED, BASE_EDITED, BASE_EXPORTED})

#: Valid set-algebra operators (spec/81 §2). '&' displays as ∩.
OPERATORS = frozenset({"+", "-", "&"})


@dataclass(frozen=True)
class DCExpr:
    """A DC reduced to what the resolver needs: its id (for the cycle guard),
    its ordered ``expr`` (list of ``[op, operand]``) and its ``filters``
    mapping. The gateway builds these from rows; the resolver never sees a
    dataclass row or a DB cursor."""

    id: str
    expr: Sequence[Sequence[Any]]
    filters: Mapping[str, Any]


class CycleError(ValueError):
    """A DC's operand graph references itself (directly or transitively).

    Carries the message ``"cycle"`` so the UI maps it to a ``tr()`` string
    (the write seam raises ``ValueError("cycle")`` for the same reason)."""

    def __init__(self) -> None:
        super().__init__("cycle")


def is_base_token(operand: Any) -> bool:
    """True iff ``operand`` is a base-universe token (a bare string)."""
    return isinstance(operand, str)


def operand_kind(operand: Any) -> Optional[str]:
    """``'dc'`` / ``'cut'`` for a typed-ref operand, ``None`` for a base token
    or a malformed operand."""
    if isinstance(operand, Mapping):
        kind = operand.get("kind")
        return kind if kind in ("dc", "cut") else None
    return None


def resolve(
    expr: Sequence[Sequence[Any]],
    filters: Mapping[str, Any],
    *,
    base_universe: Callable[[str], Set[str]],
    dc_by_ref: Callable[[Mapping[str, Any]], Optional[DCExpr]],
    cut_members: Callable[[Mapping[str, Any]], Set[str]],
    apply_filters: Callable[[Set[str], Mapping[str, Any]], List[str]],
    _seen: Optional[Set[str]] = None,
    _memo: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """Resolve one DC ``expr`` + ``filters`` to an ordered member-key list.

    Set algebra over operand key-sets, left-to-right; then the DC's own
    filters narrow + order the result. Operands resolve recursively (a nested
    DC) or terminally (the base universe, or a frozen Cut). Cycle-safe: a DC
    whose own id is already on the resolution stack raises :class:`CycleError`.

    ``_seen`` / ``_memo`` are internal recursion state — callers pass the
    top-level ``expr`` + ``filters`` and leave them defaulted."""
    keys = _resolve_keys(
        expr,
        base_universe=base_universe,
        dc_by_ref=dc_by_ref,
        cut_members=cut_members,
        apply_filters=apply_filters,
        seen=_seen if _seen is not None else set(),
        memo=_memo if _memo is not None else {},
    )
    return apply_filters(keys, filters)


def _resolve_keys(
    expr: Sequence[Sequence[Any]],
    *,
    base_universe: Callable[[str], Set[str]],
    dc_by_ref: Callable[[Mapping[str, Any]], Optional[DCExpr]],
    cut_members: Callable[[Mapping[str, Any]], Set[str]],
    apply_filters: Callable[[Set[str], Mapping[str, Any]], List[str]],
    seen: Set[str],
    memo: Dict[str, List[str]],
) -> Set[str]:
    """The set-algebra core: fold operand key-sets left-to-right. Returns an
    UNORDERED key set — ordering is the caller's ``apply_filters`` job."""
    members: Set[str] = set()
    for pair in expr:
        try:
            op, operand = pair[0], pair[1]
        except (IndexError, TypeError):
            continue                                   # malformed term: skip (graceful)
        if op not in OPERATORS:
            raise ValueError(f"unknown operator: {op!r}")
        operand_set = _resolve_operand(
            operand,
            base_universe=base_universe,
            dc_by_ref=dc_by_ref,
            cut_members=cut_members,
            apply_filters=apply_filters,
            seen=seen,
            memo=memo,
        )
        if op == "+":
            members |= operand_set
        elif op == "-":
            members -= operand_set
        else:  # '&'
            # First term with '&' over an empty accumulator yields empty (an
            # intersection-led expr is degenerate); subsequent '&' narrows.
            members &= operand_set
    return members


def _resolve_operand(
    operand: Any,
    *,
    base_universe: Callable[[str], Set[str]],
    dc_by_ref: Callable[[Mapping[str, Any]], Optional[DCExpr]],
    cut_members: Callable[[Mapping[str, Any]], Set[str]],
    apply_filters: Callable[[Set[str], Mapping[str, Any]], List[str]],
    seen: Set[str],
    memo: Dict[str, List[str]],
) -> Set[str]:
    """One operand → its member-key set. Base token + Cut ref are terminal;
    a DC ref recurses (cycle-guarded + memoised on the DC id)."""
    if is_base_token(operand):
        return set(base_universe(operand))
    kind = operand_kind(operand)
    if kind == "cut":
        return set(cut_members(operand))
    if kind == "dc":
        dc = dc_by_ref(operand)
        if dc is None:
            return set()                               # deleted operand → empty
        if dc.id in seen:
            raise CycleError()
        if dc.id in memo:
            return set(memo[dc.id])
        seen.add(dc.id)
        try:
            keys = _resolve_keys(
                dc.expr,
                base_universe=base_universe,
                dc_by_ref=dc_by_ref,
                cut_members=cut_members,
                apply_filters=apply_filters,
                seen=seen,
                memo=memo,
            )
            # A nested DC's OWN filters apply before it composes upward.
            ordered = apply_filters(keys, dc.filters)
        finally:
            seen.discard(dc.id)
        memo[dc.id] = ordered
        return set(ordered)
    return set()                                       # unknown operand → empty


# --------------------------------------------------------------------------- #
# Cycle guard — the cheap, non-resolving check used at the WRITE seam (Task A).
# Independent of the resolution-time guard above; this one never reads member
# sets, it only walks DC→DC operand refs.
# --------------------------------------------------------------------------- #


def reaches(
    start_id: str,
    expr: Sequence[Sequence[Any]],
    *,
    dc_expr_by_id: Callable[[str], Optional[Sequence[Sequence[Any]]]],
) -> bool:
    """Cheap cycle probe (spec/81 §2): does the DC operand graph rooted at
    ``expr`` reach ``start_id``? Walks DC→DC operand refs only — the base
    token ``"exported"`` and any ``cut`` ref are TERMINAL (a frozen Cut never
    re-queries its DC). ``dc_expr_by_id(id) -> expr | None`` lets the caller
    feed today's stored DCs plus the about-to-be-written one.

    The write seam calls this with the DC's own id to reject a save that would
    create a cycle (self-reference or A→B→A), BEFORE any resolution."""
    stack: List[Sequence[Sequence[Any]]] = [expr]
    visited: Set[str] = set()
    while stack:
        cur = stack.pop()
        for pair in cur:
            try:
                operand = pair[1]
            except (IndexError, TypeError):
                continue
            if operand_kind(operand) != "dc":
                continue                               # base token / cut ref: terminal
            ref_id = operand.get("id")
            if ref_id is None:
                continue
            if ref_id == start_id:
                return True
            if ref_id in visited:
                continue
            visited.add(ref_id)
            child = dc_expr_by_id(ref_id)
            if child is not None:
                stack.append(child)
    return False


__all__ = [
    "BASE_COLLECTED",
    "BASE_EDITED",
    "BASE_EXPORTED",
    "BASE_PICKED",
    "LADDER_TOKENS",
    "OPERATORS",
    "DCExpr",
    "CycleError",
    "is_base_token",
    "operand_kind",
    "resolve",
    "reaches",
]
