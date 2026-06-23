"""Cut aspect ratios (spec/111).

A Cut carries the slideshow canvas aspect — 16:9 / 4:3 / 3:2 / 1:1 —
because the canvas shape belongs to the *show*, not the event. Two
consumers depend on it staying in lock-step:

* **Separator / opener cards** (``mira/ui/shared/separator_card.py``)
  are rendered at the Cut's aspect so cards, photos and the show
  canvas all agree (Nelson 2026-06-22: 16:9 cards vs 4:3 photos was
  the visible bug).
* **PTE output** (spec/107) writes the corresponding ``AspectRatio``
  + ``opt_scr_w`` / ``opt_scr_h`` into the generated ``.pte`` so the
  slideshow tool's canvas matches the renders without per-slide
  edits.

This module is the single source of truth for both: the canonical
enum, the (pte_aspect_string, width, height) map, and the small
helpers the writers / dialog import.

Pure data — no Qt imports (the renderer wrapper lives in
``mira/ui/shared/separator_card.py``; this module only declares
sizes)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple


#: The canonical aspect strings. Order matters — UI pickers render in
#: this order so the default (16:9) sits at the top.
ASPECT_16_9 = "16:9"
ASPECT_4_3 = "4:3"
ASPECT_3_2 = "3:2"
ASPECT_1_1 = "1:1"

#: Default for new Cuts (spec/111 §2 — "the most common").
DEFAULT_ASPECT = ASPECT_16_9

#: Closed set of values the schema CHECK + UI picker enforce.
ASPECTS: Tuple[str, ...] = (
    ASPECT_16_9, ASPECT_4_3, ASPECT_3_2, ASPECT_1_1,
)


@dataclass(frozen=True)
class AspectSpec:
    """One row of the canonical aspect map: PTE-side wire string + the
    canvas pixel dimensions Mira renders at."""

    aspect: str           # the canonical "16:9" / "4:3" / "3:2" / "1:1"
    pte_aspect: str       # PTE ``AspectRatio`` value: "16-9" / "4-3" / …
    width: int            # canvas width  (px)
    height: int           # canvas height (px)


#: The lookup table. Pixel dimensions are 1080-tier (the renderer's
#: default height) for the wider aspects, square for 1:1 — generous
#: enough that downscaling for thumbnails is loss-free, small enough
#: that an exported card stays well below 1 MB JPEG.
_BY_ASPECT = {
    ASPECT_16_9: AspectSpec(ASPECT_16_9, "16-9", 1920, 1080),
    ASPECT_4_3:  AspectSpec(ASPECT_4_3,  "4-3",  1024,  768),
    ASPECT_3_2:  AspectSpec(ASPECT_3_2,  "3-2",  1620, 1080),
    ASPECT_1_1:  AspectSpec(ASPECT_1_1,  "1-1",  1080, 1080),
}


def normalise(aspect: str | None) -> str:
    """Coerce ``aspect`` to a canonical value. Unknown / blank /
    ``None`` falls back to :data:`DEFAULT_ASPECT` so the renderer can
    never crash on a legacy row that pre-dates the v15 migration."""
    if aspect in _BY_ASPECT:
        return aspect
    return DEFAULT_ASPECT


def aspect_spec(aspect: str | None) -> AspectSpec:
    """Resolve one aspect to its full :class:`AspectSpec`. Unknown
    aspects fall through to :data:`DEFAULT_ASPECT` (16:9)."""
    return _BY_ASPECT[normalise(aspect)]


def aspect_dimensions(aspect: str | None) -> Tuple[int, int]:
    """``aspect → (width, height)`` — the canvas pixel size."""
    spec = aspect_spec(aspect)
    return (spec.width, spec.height)


def aspect_pte_string(aspect: str | None) -> str:
    """``aspect → PTE AspectRatio value`` (spec/107). PTE writes the
    ratio with a hyphen separator (``16-9``), not a colon."""
    return aspect_spec(aspect).pte_aspect


def all_aspects() -> Iterable[str]:
    """Render order for UI pickers."""
    return ASPECTS


__all__ = [
    "ASPECTS",
    "ASPECT_16_9", "ASPECT_4_3", "ASPECT_3_2", "ASPECT_1_1",
    "DEFAULT_ASPECT",
    "AspectSpec",
    "aspect_spec",
    "aspect_dimensions",
    "aspect_pte_string",
    "all_aspects",
    "normalise",
]
