"""Recipe resolution — the rule-list engine (spec/90 §7 Phase 2).

Pure logic, no Qt and no DB-seam assumptions (charter invariant 8 + 1): every
data accessor is injected as a callable, so this module only knows about the
composition shape, the rule semantics, and how to stitch the spec/81
:mod:`core.collection_resolver` engine into a pool + per-item verdict map.

The model (spec/90 §1.1, §1.3-§1.5):

* **Composition** is one JSON blob (the ``recipe.composition_json`` column)
  with five logical sections — Scope / Source / Filters / Rules / Otherwise —
  plus optional ``presentation`` settings the resolver ignores.

* **Resolution order** is strict:

  1. **Scope** — set of event uuids the recipe reaches. Empty for Cut
     flavour ⇒ "current event"; for Collection flavour, the caller passes
     pre-resolved uuids (the library face resolves ``composition['scope']``
     itself before invoking the resolver).
  2. **Source** — spec/81 set-algebra over operands. The result is the pool
     before any narrowing. Operands resolve via the same callables the DC
     engine takes (base / dc / cut), plus the spec/90 §4.3 Person operand
     via :mod:`core.collection_resolver`'s ``extra_operand`` hook.
  3. **Filters** — narrow the pool by item metadata. Vocabulary filters
     (Style / Media / Camera / Lens) ride the existing ``apply_filters``
     accessor on the pool. The Person filter (multi-select; spec/90 §4.3)
     intersects the pool with the union of items where any selected Person
     is detected — handled here, after the pool is ordered, so the SQL
     stays simple (no JOIN on ``face``).
  4. **Rules** — ordered list; first match wins. Each rule's predicate is
     an expression that resolves to a SET of keys (unordered — no
     chronological pass; only set membership is consulted). The verdict
     attached to the first matching rule wins for that item.
  5. **Otherwise** — the default verdict (``'pick'`` / ``'skip'``) for
     items matching no rule. Always present.

* **Strict reference resolution** (spec/90 §1.4). A Recipe references named
  operands — DCs, Cuts, Event Collections, People. A missing named operand
  raises :class:`RecipeResolutionError` with the operand's user-facing
  label. **Vocabulary-based filters** (Style / Media / Camera / Lens)
  resolve LENIENTLY to empty when nothing matches — the vocabulary itself
  is library-wide; "0 items match Camera=G9 in Bali" is just zero, not an
  error.

* **Person operand** (spec/90 §5.2 + §4.3). The face substrate ships empty
  in Phase 1, so a Person chip resolves to an empty set when no
  detection has run yet — and that is the **correct behaviour, not an
  error**. The strict-ref guard fires only when the PERSON itself doesn't
  exist (i.e. the recipe references a Person id that was deleted from
  the catalog).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from core import collection_resolver

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #


#: Closed enum for Rule + Otherwise verdicts (spec/90 §1.3).
VERDICTS: FrozenSet[str] = frozenset({"pick", "skip"})

#: The operand kinds the spec/90 grammar admits beyond the spec/81 base.
#: ``person`` is the only new ITEM-set operand; ``event`` / ``event_collection``
#: are EVENT-set operands the Scope section consumes.
PERSON_KIND = "person"
EVENT_KIND = "event"
EVENT_COLLECTION_KIND = "event_collection"


@dataclass(frozen=True)
class RecipeResolution:
    """The resolver's output (spec/90 §7 Phase 2).

    ``pool`` is the ordered member-key list (export relpaths for event scope;
    cross-event packed keys for cross-event). ``seed`` maps each key to its
    initial picked-state (``True`` = pick / ``False`` = skip). The key sets
    of ``pool`` and ``seed`` are identical by construction; ``pool`` carries
    the chronological order, ``seed`` carries the per-item verdict."""

    pool: List[str]
    seed: Dict[str, bool]


class RecipeResolutionError(ValueError):
    """A Recipe references a named operand that no longer exists (spec/90
    §1.4 strict-reference rule). Carries the operand's user-facing label
    (``tag`` for tagged nouns, ``display_name`` / ``id`` for People) and the
    operand kind so the UI can craft the right message ("This Recipe
    references #best_wildlife (deleted on …)")."""

    def __init__(self, missing_operand: str, *, kind: str = "") -> None:
        suffix = f" {missing_operand!r}" if missing_operand else ""
        prefix = f"missing {kind}" if kind else "missing operand"
        super().__init__(f"{prefix}{suffix}")
        self.missing_operand: str = missing_operand
        self.kind: str = kind


# --------------------------------------------------------------------------- #
# Composition parsers — tolerant readers (charter §5.3)
# --------------------------------------------------------------------------- #


def _expr(value: Any) -> List[List[Any]]:
    """Coerce a composition expression into ``[[op, operand], …]`` form.
    Tolerant: a missing / non-list value reads as the empty expression."""
    if not isinstance(value, (list, tuple)):
        return []
    out: List[List[Any]] = []
    for pair in value:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            out.append([pair[0], pair[1]])
    return out


def _filters(value: Any) -> Dict[str, Any]:
    """Coerce a composition filters mapping; non-dict → empty."""
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _string_list(value: Any) -> List[str]:
    """Coerce a value into a list of non-empty strings; anything else → []."""
    if not isinstance(value, (list, tuple)):
        return []
    return [s for s in value if isinstance(s, str) and s]


def _verdict(value: Any, *, default: str) -> str:
    """Coerce a value to a closed verdict; falls back to ``default`` (which
    callers can set to the strict default ``'skip'`` or surface as an
    error). Validation happens at the top-level call so unknown values
    raise once, not silently here."""
    if isinstance(value, str) and value in VERDICTS:
        return value
    return default


# --------------------------------------------------------------------------- #
# Strict-reference walk
# --------------------------------------------------------------------------- #


def _walk_operands(
    expr: Sequence[Sequence[Any]],
    *,
    validate_named: Callable[[Mapping[str, Any]], None],
    dc_expr_by_ref: Callable[[Mapping[str, Any]], Optional[Sequence[Sequence[Any]]]],
    seen_dcs: Optional[Set[str]] = None,
) -> None:
    """Walk every operand in ``expr``, validating named references. DC operands
    are recursed into so a missing transitive reference also raises.

    The walk is cycle-safe via the ``seen_dcs`` set on dc.id — the same shape
    as :func:`core.collection_resolver.reaches`. The write-seam cycle guard
    prevents cycles being saved, so this is belt-and-braces.

    ``validate_named`` is called once per named operand and may raise
    :class:`RecipeResolutionError`; the resolver doesn't catch."""
    seen = seen_dcs if seen_dcs is not None else set()
    for pair in expr:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        operand = pair[1]
        if not isinstance(operand, Mapping):
            continue                                       # base token or malformed
        validate_named(operand)
        kind = operand.get("kind")
        if kind == "dc":
            ref_id = operand.get("id")
            if not ref_id or ref_id in seen:
                continue
            seen.add(ref_id)
            child_expr = dc_expr_by_ref(operand)
            if child_expr is not None:
                _walk_operands(
                    child_expr,
                    validate_named=validate_named,
                    dc_expr_by_ref=dc_expr_by_ref,
                    seen_dcs=seen,
                )


def _validate_person_ids(
    person_ids: Sequence[str],
    *,
    person_exists: Callable[[str], bool],
) -> None:
    """The Filter-row Person ids (spec/90 §4.3) get their own walk — they're
    a flat list, not an expression. Each id must exist in the catalog."""
    for pid in person_ids:
        if not person_exists(pid):
            raise RecipeResolutionError(pid, kind=PERSON_KIND)


# --------------------------------------------------------------------------- #
# The main entry point
# --------------------------------------------------------------------------- #


def resolve_recipe(
    composition: Mapping[str, Any],
    *,
    # Pool resolution: source expression + filters → ordered pool keys.
    resolve_pool: Callable[[Sequence[Sequence[Any]], Mapping[str, Any]], List[str]],
    # Rule-predicate resolution: predicate expression → set of keys (no
    # ordering, no top-level filters; the existing resolver's set-algebra
    # core run with a passthrough ``apply_filters``).
    resolve_predicate_keys: Callable[[Sequence[Sequence[Any]]], Set[str]],
    # Person resolution: ``person_id -> set of pool keys this person appears
    # in`` (the SQL ``SELECT key FROM face WHERE person_id = ?`` reduction).
    # ``None`` = the Person doesn't exist at all (strict-ref miss); an empty
    # set = exists but no faces detected yet (lenient — the Phase 1 substrate
    # ships empty).
    person_members: Callable[[str], Optional[Set[str]]],
    # Strict-reference validators for named operands (spec/90 §1.4). Each is
    # called once per operand instance; raise :class:`RecipeResolutionError`
    # to mark a miss. ``operand`` is the typed-ref Mapping (kind/id/tag/uuid).
    validate_named_operand: Callable[[Mapping[str, Any]], None],
    # Live DC expr lookup used by the strict-walk to recurse into nested DCs
    # and validate transitive references. ``None`` if the DC is gone (the
    # validator already raised on it; this is for the "DC exists but its
    # sub-DC is gone" case).
    dc_expr_by_ref: Callable[[Mapping[str, Any]], Optional[Sequence[Sequence[Any]]]],
) -> RecipeResolution:
    """Evaluate a Recipe composition into ``(pool, seed)``.

    Pipeline:

    1. Read sections from ``composition``: ``source`` (required, non-empty),
       ``filters`` (optional dict), ``rules`` (optional list), ``otherwise``
       (required ``'pick'``/``'skip'``). ``scope`` is read for the strict
       walk but isn't applied here — the gateway face owns scope semantics.
       ``presentation`` is ignored.
    2. **Strict-reference walk** over every named operand in scope / source /
       rule predicates + the Person ids in filters. Any miss raises
       :class:`RecipeResolutionError` before any resolution work.
    3. **Pool** = ``resolve_pool(source_expr, vocabulary_filters)``. The
       vocabulary filters (styles / media_type / camera_ids / lens_models)
       ride the gateway's existing ``apply_filters`` plumbing; an empty pool
       is fine (lenient — spec/90 §1.4 last paragraph).
    4. **Person filter** (spec/90 §4.3) intersects the pool with the union
       of ``person_members(pid)`` over every ``filters['person_ids']``.
       Done here so the per-event SQL stays simple — no JOIN on ``face``.
    5. **Rules** — each predicate resolves once to a set; first-match-wins
       per item; otherwise's verdict for items matching no rule.
    6. Return :class:`RecipeResolution`.

    Sanity-checks ``source`` (must be non-empty after coercion) and
    ``otherwise`` (must be in :data:`VERDICTS`). Both raise ``ValueError``
    with a clear message — these are author errors in the composition, not
    deletion misses, so they don't carry a ``missing_operand``."""
    # --- parse + sanity-check ------------------------------------------------ #
    source_expr = _expr(composition.get("source"))
    if not source_expr:
        raise ValueError("recipe composition has no source expression")
    filters = _filters(composition.get("filters"))
    rules_raw = composition.get("rules") or []
    if not isinstance(rules_raw, (list, tuple)):
        rules_raw = []
    otherwise = composition.get("otherwise")
    if otherwise not in VERDICTS:
        raise ValueError(
            f"recipe composition has invalid otherwise verdict: {otherwise!r}")

    rules: List[Tuple[List[List[Any]], str]] = []
    for r in rules_raw:
        if not isinstance(r, Mapping):
            continue
        predicate = _expr(r.get("predicate"))
        if not predicate:
            continue                                       # empty rule = no-op
        verdict = r.get("verdict")
        if verdict not in VERDICTS:
            raise ValueError(
                f"recipe rule has invalid verdict: {verdict!r}")
        rules.append((predicate, verdict))

    scope_expr = _expr(composition.get("scope"))
    person_ids = _string_list(filters.get("person_ids"))

    # --- strict-reference walk ----------------------------------------------- #
    # Scope first (may contain event / event_collection refs); Source next
    # (DC / Cut); each rule predicate (DC / Cut / Person). Person ids in
    # the Filter row get their own walk (they're a flat id list, not an
    # expression).
    for expr in (scope_expr, source_expr, *(p for p, _ in rules)):
        _walk_operands(
            expr,
            validate_named=validate_named_operand,
            dc_expr_by_ref=dc_expr_by_ref,
        )

    def _person_exists(pid: str) -> bool:
        return person_members(pid) is not None

    _validate_person_ids(person_ids, person_exists=_person_exists)

    # --- resolve pool -------------------------------------------------------- #
    pool = list(resolve_pool(source_expr, filters))

    # --- Person filter (spec/90 §4.3 — union across selected Person ids) ---- #
    if person_ids:
        person_pool: Set[str] = set()
        for pid in person_ids:
            members = person_members(pid)
            if members is None:                            # validator already raised
                raise RecipeResolutionError(pid, kind=PERSON_KIND)
            person_pool |= set(members)
        pool = [k for k in pool if k in person_pool]

    # --- rules → seed -------------------------------------------------------- #
    # Resolve each predicate once to a set; for each item, walk rules
    # first-match-wins; items matching no rule fall through to Otherwise.
    rule_sets: List[Tuple[Set[str], str]] = [
        (set(resolve_predicate_keys(predicate)), verdict)
        for predicate, verdict in rules
    ]

    pick_default = (otherwise == "pick")
    seed: Dict[str, bool] = {}
    for key in pool:
        matched = False
        for predicate_set, verdict in rule_sets:
            if key in predicate_set:
                seed[key] = (verdict == "pick")
                matched = True
                break
        if not matched:
            seed[key] = pick_default

    return RecipeResolution(pool=pool, seed=seed)


__all__ = [
    "VERDICTS",
    "PERSON_KIND",
    "EVENT_KIND",
    "EVENT_COLLECTION_KIND",
    "RecipeResolution",
    "RecipeResolutionError",
    "resolve_recipe",
]
