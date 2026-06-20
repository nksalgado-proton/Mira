"""Recipe ↔ :class:`CutDraft` adapter (spec/90 §7 Phase 3).

A :class:`mira.user_store.models.Recipe` is the library-level saved Cut /
Collection configuration. Its ``composition_json`` carries Scope / Source /
Filters / Rules / Otherwise / Presentation — the rule-list shape spec/90
§5.1 documents. A :class:`mira.shared.cut_draft.CutDraft` is the dialog →
pin-session handoff value the spec/61 picker + the spec/81 commit path
consume.

The two shapes overlap deliberately. The adapter:

* **Collapses §1.5 syntactic sugar** on the way to a CutDraft. A Recipe
  with no rules + Otherwise → skip becomes a :data:`PIN_PICK_IN` draft;
  no rules + Otherwise → pick becomes a :data:`PIN_WEED_OUT` draft. (The
  third sugar case — keep-all = no rules + Otherwise → pick + Picker
  session skipped — isn't expressible in CutDraft today; the adapter
  treats it as weed-out and the dialog can layer the "skip-the-picker"
  hint on top in Phase 4.)
* **Carries the rule list through verbatim** when it is non-trivial. A
  Recipe with one or more rules becomes a :data:`PIN_RULE_BASED` draft
  with the same predicates + verdicts on
  :attr:`CutDraft.rules` and the explicit Otherwise on
  :attr:`CutDraft.otherwise`.
* **Round-trips** through :func:`cut_draft_to_recipe_composition`. A
  legacy-mode draft (pick-in / weed-out / keep-all) translates back to
  a no-rules composition with the matching Otherwise; a rule-based draft
  serialises its rule list directly.

The adapter is pure logic — no DB, no Qt — so it composes naturally with
:class:`mira.shared.recipe_store.RecipeStore` (which owns the JSON
encoding) and the dialog widget (which owns the user-facing affordances).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from mira.shared.cut_draft import (
    CutDraft,
    CutDraftRule,
    OTHERWISE_PICK,
    OTHERWISE_SKIP,
    PIN_KEEP_ALL,
    PIN_PICK_IN,
    PIN_RULE_BASED,
    PIN_WEED_OUT,
)
from mira.user_store import models as um

log = logging.getLogger(__name__)


_DEFAULT_PHOTO_S = 6.0
_DEFAULT_CARD_STYLE = "black"


# --------------------------------------------------------------------------- #
# Shared coercions
# --------------------------------------------------------------------------- #


def _slug(name: str) -> str:
    """Tag slug for a Recipe's name. Same shape as the cut/dc slug helper
    (lowercase, non-alnum → underscore, collapse repeats) so a CutDraft
    derived from a Recipe lands a usable tag without a gateway probe."""
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "untitled"


def _expr_to_tuples(expr: Any) -> Tuple[Tuple[str, Any], ...]:
    """Coerce a composition expression into the :class:`CutDraft` expr
    tuple shape. The resolver tolerates both ``[op, operand]`` lists and
    ``(op, operand)`` tuples; the draft expects tuples. Malformed
    pairs are dropped (charter §5.3 — tolerate, don't crash)."""
    if not isinstance(expr, (list, tuple)):
        return ()
    out: list[Tuple[str, Any]] = []
    for pair in expr:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        op, operand = pair[0], pair[1]
        if not isinstance(op, str):
            continue
        out.append((op, operand))
    return tuple(out)


def _expr_to_lists(expr: Iterable[Tuple[str, Any]]) -> List[List[Any]]:
    """Inverse — a draft expr (tuple of tuples) into the composition's
    list-of-lists shape. Composition JSON uses lists (canonical JSON has
    no tuples)."""
    return [[op, operand] for op, operand in expr or ()]


def _string_tuple(value: Any) -> Tuple[str, ...]:
    """Coerce a value into a tuple of non-empty strings; anything else → ()."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(s for s in value if isinstance(s, str) and s)


def _normalise_otherwise(value: Any, *, default: str) -> str:
    """Closed-enum check for the Otherwise verdict. Unknown / missing falls
    back to ``default``."""
    return value if value in (OTHERWISE_PICK, OTHERWISE_SKIP) else default


def _media_type(value: Any) -> str:
    if value in ("photo", "video", "both"):
        return value
    return "both"


def _opt_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _opt_float(value: Any, *, default: float = _DEFAULT_PHOTO_S) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _opt_str(value: Any) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    return None


# --------------------------------------------------------------------------- #
# Recipe → CutDraft  (the dialog "Load Recipe…" path)
# --------------------------------------------------------------------------- #


def recipe_to_cut_draft(recipe: um.Recipe) -> CutDraft:
    """Translate a Cut-flavoured :class:`Recipe` into a :class:`CutDraft`.

    Composition fields map as follows:

    ===========================  ============================================
    Composition key              CutDraft field
    ===========================  ============================================
    ``source``                   ``expr`` (tuple of (op, operand))
    ``filters.styles``           ``styles``
    ``filters.media_type``       ``media_type``
    ``rules``                    ``rules`` (tuple of :class:`CutDraftRule`)
    ``otherwise``                ``otherwise`` + derived ``pin_mode``
    ``presentation.target_s``    ``target_s``
    ``presentation.max_s``       ``max_s``
    ``presentation.photo_s``     ``photo_s``
    ``presentation.music_category`` ``music_category``
    ``presentation.card_style``  ``card_style``
    ``presentation.separators``  ``separators``
    ``presentation.overlay_fields`` ``overlay_fields``
    ``presentation.overlay_mode``   ``overlay_mode``
    ===========================  ============================================

    spec/90 §1.5 sugar collapse: with **no rules**, the Otherwise verdict
    drives a legacy pin mode (``skip`` → :data:`PIN_PICK_IN`, ``pick`` →
    :data:`PIN_WEED_OUT`). With **non-empty rules**, the draft enters
    :data:`PIN_RULE_BASED` mode and the rule list carries through verbatim.

    The Recipe MUST be ``flavour == 'cut'``; the Collection-flavoured
    counterpart will land in Phase 4 alongside the Collection dialog.
    Collection-flavoured input here raises ``ValueError`` — the cross-
    pollination check is the dialog's job (spec/90 §5.5), not the
    adapter's, but a misuse should fail loudly rather than silently
    produce a wrong-shaped draft."""
    if recipe.flavour != "cut":
        raise ValueError(
            f"recipe_to_cut_draft requires flavour='cut', got "
            f"{recipe.flavour!r}")

    composition = _decode(recipe.composition_json)

    source_expr = _expr_to_tuples(composition.get("source"))
    filters = composition.get("filters") if isinstance(
        composition.get("filters"), Mapping) else {}
    styles = _string_tuple(filters.get("styles"))
    media_type = _media_type(filters.get("media_type"))

    rules_raw = composition.get("rules") or []
    if not isinstance(rules_raw, (list, tuple)):
        rules_raw = []

    rule_list: list[CutDraftRule] = []
    for r in rules_raw:
        if not isinstance(r, Mapping):
            continue
        predicate = _expr_to_tuples(r.get("predicate"))
        verdict = _normalise_otherwise(r.get("verdict"), default="")
        if not predicate or verdict not in (OTHERWISE_PICK, OTHERWISE_SKIP):
            continue
        rule_list.append(CutDraftRule(predicate=predicate, verdict=verdict))

    otherwise = _normalise_otherwise(
        composition.get("otherwise"), default=OTHERWISE_SKIP)

    if rule_list:
        pin_mode = PIN_RULE_BASED
    else:
        pin_mode = (
            PIN_WEED_OUT if otherwise == OTHERWISE_PICK else PIN_PICK_IN
        )

    presentation = composition.get("presentation") or {}
    if not isinstance(presentation, Mapping):
        presentation = {}

    target_s = _opt_int(presentation.get("target_s"))
    max_s = _opt_int(presentation.get("max_s"))
    photo_s = _opt_float(
        presentation.get("photo_s"), default=_DEFAULT_PHOTO_S)
    music_category = _opt_str(presentation.get("music_category"))
    card_style = presentation.get("card_style") or _DEFAULT_CARD_STYLE
    if card_style not in ("black", "single", "multi"):
        card_style = _DEFAULT_CARD_STYLE
    overlay_fields = _string_tuple(presentation.get("overlay_fields"))
    overlay_mode = presentation.get("overlay_mode")
    if overlay_mode not in ("embedded", "burn_in"):
        overlay_mode = None
    separators_raw = presentation.get("separators")
    separators = (
        bool(separators_raw) if separators_raw is not None else True
    )

    # ``source_dc_id`` is reverse-derivable when the source expression is a
    # single ``+`` over a typed DC ref (the spec/81 §2 "DC only" shape). For
    # anything more composed, leave it ``None`` — the draft's ``expr`` is
    # the authoritative source.
    source_dc_id = _infer_source_dc_id(source_expr)

    return CutDraft(
        name=recipe.name,
        tag=_slug(recipe.name),
        source_dc_id=source_dc_id,
        expr=source_expr,
        styles=styles,
        media_type=media_type,
        pin_mode=pin_mode,
        target_s=target_s,
        max_s=max_s,
        photo_s=photo_s,
        music_category=music_category,
        separators=separators,
        overlay_fields=overlay_fields,
        overlay_mode=overlay_mode,
        card_style=card_style,
        rules=tuple(rule_list),
        otherwise=otherwise,
    )


# --------------------------------------------------------------------------- #
# CutDraft → composition  (the "Save as Recipe…" path)
# --------------------------------------------------------------------------- #


def cut_draft_to_recipe_composition(draft: CutDraft) -> dict:
    """Translate a :class:`CutDraft` into a Recipe composition dict ready
    for :meth:`RecipeStore.create` / :meth:`update`.

    The shape follows spec/90 §5.1. For a legacy-mode draft (pin-in /
    weed-out / keep-all), the composition has **no rules**, an explicit
    Otherwise verdict (spec/90 §1.5 sugar), and the presentation block.
    For a rule-based draft, the rule list serialises verbatim and
    Otherwise is the draft's explicit value (falling back to ``'skip'``
    if the dialog forgot to set it — defensive).

    The inverse of :func:`recipe_to_cut_draft` for the round-trippable
    subset. A legacy draft round-trips exactly back to itself; a
    rule-based draft round-trips its rules + otherwise + presentation."""
    composition: dict[str, Any] = {
        "source": _expr_to_lists(draft.expr),
        "filters": {
            "styles": list(draft.styles),
            "media_type": draft.media_type,
        },
    }

    if draft.pin_mode == PIN_RULE_BASED and draft.rules:
        composition["rules"] = [
            {
                "predicate": _expr_to_lists(r.predicate),
                "verdict": r.verdict,
            }
            for r in draft.rules
        ]
        composition["otherwise"] = (
            draft.otherwise
            if draft.otherwise in (OTHERWISE_PICK, OTHERWISE_SKIP)
            else OTHERWISE_SKIP
        )
    else:
        # spec/90 §1.5 sugar — no rules, just Otherwise. pin_mode dictates
        # the verdict: keep-all + weed-out both start all-in (pick);
        # pick-in starts all-out (skip).
        if draft.otherwise in (OTHERWISE_PICK, OTHERWISE_SKIP):
            composition["otherwise"] = draft.otherwise
        elif draft.pin_mode == PIN_PICK_IN:
            composition["otherwise"] = OTHERWISE_SKIP
        else:
            # keep-all + weed-out + (unknown / default) → pick. keep-all's
            # "Picker session skipped" hint isn't carried here; spec/90 §1.5
            # documents the collapse.
            composition["otherwise"] = OTHERWISE_PICK

    presentation: dict[str, Any] = {
        "photo_s": draft.photo_s,
        "card_style": draft.card_style,
        "separators": draft.separators,
    }
    if draft.target_s is not None:
        presentation["target_s"] = int(draft.target_s)
    if draft.max_s is not None:
        presentation["max_s"] = int(draft.max_s)
    if draft.music_category:
        presentation["music_category"] = draft.music_category
    if draft.overlay_fields:
        presentation["overlay_fields"] = list(draft.overlay_fields)
    if draft.overlay_mode:
        presentation["overlay_mode"] = draft.overlay_mode
    composition["presentation"] = presentation
    return composition


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _decode(composition_json: Optional[str]) -> dict:
    """Decode a composition_json string into a dict. Tolerant — malformed
    JSON reads as ``{}``."""
    if not composition_json:
        return {}
    try:
        data = json.loads(composition_json)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _infer_source_dc_id(expr: Sequence[Tuple[str, Any]]) -> Optional[str]:
    """When the source is exactly ``[("+", {"kind": "dc", "id": X, …})]``,
    surface the DC id on the draft (the legacy ``source_dc_id`` field).
    Anything more composed returns ``None`` — the expr itself is the
    authoritative source."""
    if len(expr) != 1:
        return None
    op, operand = expr[0]
    if op != "+":
        return None
    if not isinstance(operand, Mapping):
        return None
    if operand.get("kind") != "dc":
        return None
    dc_id = operand.get("id")
    return dc_id if isinstance(dc_id, str) and dc_id else None


__all__ = [
    "recipe_to_cut_draft",
    "cut_draft_to_recipe_composition",
]
