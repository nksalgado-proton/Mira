"""Cut time-budget math (spec/61 §2 step 5).

Minutes are the truth: each photo costs the Cut's seconds-per-photo, each
clip costs its TRUE duration, each separator slide costs one photo slot.
The "≈ N slides, keep ~1 in K" line is rough orientation shown only for
photo-only pools — it is never the accounting.

Zones (spec/61 §2): green at/under target, amber between target and max,
red over max. A Cut may have no time limit at all (both NULL).

Pure logic, no Qt (charter invariant 8).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ZONE_NONE = "none"    # the Cut has no time limit
ZONE_GREEN = "green"
ZONE_AMBER = "amber"
ZONE_RED = "red"


@dataclass(frozen=True)
class ShowTotals:
    """The composition of one Cut's projected show."""

    photo_count: int = 0
    video_count: int = 0
    separator_count: int = 0
    video_ms_total: int = 0
    # spec/152 §3 — opener slide count (always 0 or 1). The opener
    # spends ``photo_s + transition_s`` of show time just like
    # separators and photos do; pre-152 callers passed ``transition_s
    # = 0`` so the difference vanished. Kept as a separate field so
    # callers that build totals manually can tell the opener apart
    # (e.g. when ``separators_on`` is False the opener is still
    # rendered as a card and counts here, but no day separators do).
    opener_count: int = 0

    def seconds(self, photo_s: float, transition_s: float = 0.0) -> float:
        """Projected show length: photos + separators + opener at
        ``photo_s + transition_s`` each, videos at true duration with
        NO transition added (spec/150 §1 — the next slide's transition
        window overlaps the clip's tail). ``transition_s`` defaults to
        zero for backward compatibility with callers that pre-date
        spec/152; the Play / Export call sites pass the Settings'
        ``default_transition_ms / 1000`` so the audio playlist + PTE
        ``[Times]`` agree with what's shown."""
        slot_s = float(photo_s) + float(transition_s)
        slide_count = (
            self.photo_count + self.separator_count + self.opener_count)
        return slide_count * slot_s + self.video_ms_total / 1000.0


def zone(total_s: float, target_s: Optional[int], max_s: Optional[int]) -> str:
    """Classify a projected length against the Cut's budget.

    Degenerate budgets behave sensibly: no limits at all → ``ZONE_NONE``;
    target only → over-target reads amber (there is no max to breach);
    max only → it acts as the single hard line (green/red).
    """
    if target_s is None and max_s is None:
        return ZONE_NONE
    if target_s is None:
        return ZONE_GREEN if total_s <= max_s else ZONE_RED
    if total_s <= target_s:
        return ZONE_GREEN
    if max_s is None or total_s <= max_s:
        return ZONE_AMBER
    return ZONE_RED


@dataclass(frozen=True)
class PhotoOnlyHint:
    """The rough-orientation line for photo-only pools."""

    slides_fit: int        # photo slides that fit the target after separators
    keep_one_in: Optional[int]  # "keep ~1 in K" (None when everything fits)


def photo_only_hint(
    pool_photo_count: int,
    separator_count: int,
    photo_s: float,
    target_s: Optional[int],
) -> Optional[PhotoOnlyHint]:
    """``≈ N slides at Xs · keep ~1 in K`` — only meaningful when the pool is
    photo-only and a target exists; returns ``None`` otherwise (the dialog
    hides the line)."""
    if target_s is None or photo_s <= 0:
        return None
    slides_fit = max(0, int(target_s / photo_s) - separator_count)
    if slides_fit <= 0:
        return PhotoOnlyHint(slides_fit=0, keep_one_in=None)
    if pool_photo_count <= slides_fit:
        return PhotoOnlyHint(slides_fit=slides_fit, keep_one_in=None)
    return PhotoOnlyHint(
        slides_fit=slides_fit,
        keep_one_in=max(1, round(pool_photo_count / slides_fit)),
    )
