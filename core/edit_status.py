"""The single source of truth for *"has this photo been edited?"*.

Pure-logic, no Qt, no store imports — both a duck-typed Python predicate
(:func:`is_adjustment_edited`) and a matching SQL boolean fragment
(:data:`EDITED_SQL`) so callers can either test one loaded
:class:`~mira.store.models.Adjustment` or ``COUNT`` them in one GROUP BY
without the two definitions drifting apart.

**The semantic (Nelson 2026-06-18).** Every picked photo starts at
*Original* — unprocessed, unedited. A photo counts as **edited** the
moment the user moves it off that baseline by any of:

* choosing a **look** other than the unedited baseline,
* applying a **creative filter**, or
* applying a **crop** (an explicit crop box, a non-Original aspect, a
  straighten angle, or a rotation).

This drives the Edit metric everywhere it appears — the events-tile Edit
donut and the Days-Lists Edit rows both read *edited ÷ picked*.

**The baseline is Original only (Nelson 2026-06-18).** ``'original'`` is
the one unedited look. ``'natural'`` — the schema's default
auto-correction — **counts as a Look change** ("the default one"): a photo
sitting at Natural is edited. Only a photo explicitly at *Original* with no
filter and no crop is unedited. :data:`UNEDITED_LOOKS` is the one knob; to
fold *Natural* back into the baseline, add it here and both the Python
predicate and the SQL fragment follow.
"""
from __future__ import annotations

from typing import Any

# Look identifiers that mean "the user has not changed the look". Only
# ``'original'`` qualifies — ``'natural'`` and every mood bias from
# ``core.photo_auto.available_looks()`` are deliberate Look choices.
UNEDITED_LOOKS: tuple[str, ...] = ("original",)

# Aspect labels that mean "no imposed crop" (core.aspect_ratio.ORIGINAL_LABEL).
_NO_CROP_ASPECTS: frozenset[str] = frozenset({"", "original"})


def _has_crop(adj: Any) -> bool:
    """True when the adjustment carries any crop/geometry change."""
    if getattr(adj, "crop_w", None) is not None:
        return True
    aspect = (getattr(adj, "aspect_label", None) or "").strip().lower()
    if aspect and aspect not in _NO_CROP_ASPECTS:
        return True
    if float(getattr(adj, "crop_angle", 0) or 0) != 0.0:
        return True
    if int(getattr(adj, "rotation", 0) or 0) != 0:
        return True
    return False


# The three dimensions that make a photo "edited", in the fixed display
# order the Days-Grid combined badge shows them (Nelson 2026-06-18):
# Look, then Filter, then Crop. Each is reported INDEPENDENTLY — a photo
# with a non-Original look AND a crop shows both.
REASON_LOOK = "look"
REASON_FILTER = "filter"
REASON_CROP = "crop"
REASON_ORDER: tuple[str, ...] = (REASON_LOOK, REASON_FILTER, REASON_CROP)


def edit_reasons(adj: Any) -> tuple[str, ...]:
    """*Why* this photo counts as edited — every dimension off baseline,
    in :data:`REASON_ORDER`:

    * ``"look"``   — the Look is off baseline (not Original; Natural counts),
    * ``"filter"`` — a creative filter is applied,
    * ``"crop"``   — a crop is defined (box, non-Original aspect, straighten,
      or rotation).

    Returns ``()`` for an unedited photo (no adjustment row, or all at
    baseline). Duck-typed over a :class:`~mira.store.models.Adjustment`;
    badge text is the UI's concern (it ``tr()``-maps these keys), so core
    stays Qt-free and language-free.
    """
    if adj is None:
        return ()
    out: list[str] = []
    look = (getattr(adj, "look", None) or "original").strip().lower()
    if look not in UNEDITED_LOOKS:
        out.append(REASON_LOOK)
    if (getattr(adj, "creative_filter", None) or "") != "":
        out.append(REASON_FILTER)
    if _has_crop(adj):
        out.append(REASON_CROP)
    return tuple(out)


def is_adjustment_edited(adj: Any) -> bool:
    """Has this adjustment moved the photo off its unedited baseline?
    Equivalent to ``bool(edit_reasons(adj))`` — the boolean the Edit metric
    (*edited ÷ picked*) counts.
    """
    return bool(edit_reasons(adj))


# SQL boolean expression over an ``adjustment`` row aliased ``a`` — the
# GROUP-BY twin of :func:`is_adjustment_edited`. Keep the two in lock-step:
# any change to one must change the other.
_UNEDITED_LOOKS_SQL = ", ".join(f"'{look}'" for look in UNEDITED_LOOKS)
EDITED_SQL = (
    "("
    f"  LOWER(COALESCE(a.look, 'original')) NOT IN ({_UNEDITED_LOOKS_SQL})"
    "  OR (a.creative_filter IS NOT NULL AND a.creative_filter != '')"
    "  OR a.crop_w IS NOT NULL"
    "  OR (a.aspect_label IS NOT NULL"
    "      AND LOWER(a.aspect_label) NOT IN ('', 'original'))"
    "  OR a.crop_angle != 0"
    "  OR a.rotation != 0"
    ")"
)
