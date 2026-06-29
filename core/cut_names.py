"""Cut tag names — the user types anything, the system derives the tag.

spec/61 §1.5: the user never learns naming rules. They type a free name
("Best Macro Shots", "Pássaros do Pantanal") and the dialog shows the
derived tag live. The transform is total — lowercase, accents stripped,
separators to underscores, everything else dropped — because the slug is
the cross-event join key (spec/61 §8): two events spell the same Cut
identically or name-matching breaks.

The slug is what ``cut.tag`` stores and what the export folder is named
(``Cuts/<slug>/``). The ``#`` is presentation only — prepended for display,
never persisted, never on disk.

Pure logic, no Qt (charter invariant 8). Validation returns error CODES;
the UI maps them to ``tr()`` messages.
"""
from __future__ import annotations

import unicodedata
from typing import Iterable, Optional

#: Prepended for display ("#best_macro_shots"); never stored, never on disk.
DISPLAY_PREFIX = "#"

#: The built-in live-query Cut every event has (spec/61 §1.1) — the universe
#: of event-Cut pools. Pool expressions name it by this tag.
EXPORTED_TAG = "exported"

#: Built-in live-query Cuts (spec/61 §1.1) — refused as user Cut names.
#: 'exported' is live today; the other ladder rungs (collected → picked →
#: edited) are reserved for cross-event Cuts (spec/61 §8).
RESERVED_TAGS = frozenset({EXPORTED_TAG, "collected", "picked", "edited"})

#: Characters that read as word separators — they become underscores rather
#: than vanishing, so "Best-Macro Shots" → "best_macro_shots".
_SEPARATORS = set(" \t-–—.,;:/\\|+&")


def slugify(name: str) -> str:
    """Canonical tag slug for a user-typed Cut name.

    Lowercase; accents stripped (Pássaros → passaros); separators collapse
    to single underscores; anything else outside ``[a-z0-9_]`` drops. An
    empty result means the name had no usable characters — the caller
    treats that as invalid (:func:`check_tag` returns ``'empty'``).
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    out: list[str] = []
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch == "_"):
            out.append(ch)
        elif ch in _SEPARATORS:
            out.append("_")
    slug = "".join(out)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def display_tag(slug: str) -> str:
    """The user-facing spelling of a slug: ``#best_macro_shots``."""
    return DISPLAY_PREFIX + slug


def slugify_event_name(name: str) -> str:
    """Filesystem-safe slug for an EVENT name — used as a directory
    component under ``<library_root>/Cuts/<event slug>/<cut slug>/``
    (spec/105 §2). Same transform as :func:`slugify` (lowercase,
    accents stripped, separators → underscores, anything else
    dropped): one canonical predictable spelling per event keeps the
    Cuts home discoverable and stable across renames that touch only
    accents / punctuation. Falls back to ``"event"`` when the name
    has no usable characters (an empty path component would be a
    user-confusing crash)."""
    return slugify(name) or "event"


def check_tag(slug: str, existing_tags: Iterable[str]) -> Optional[str]:
    """Validate a slug against the event's existing Cut tags.

    Returns ``None`` when the slug is usable, else an error code the UI
    translates: ``'empty'`` (nothing usable survived the transform),
    ``'reserved'`` (collides with a built-in live-query Cut), ``'taken'``
    (another Cut in this event already owns it). Comparison is case-blind
    by construction — slugs are always lowercase; ``existing_tags`` are
    lowercased defensively in case a hand-edited DB carried mixed case.
    """
    if not slug:
        return "empty"
    if slug in RESERVED_TAGS:
        return "reserved"
    if slug in {t.lower() for t in existing_tags}:
        return "taken"
    return None
