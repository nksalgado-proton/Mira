"""Bracket-help context (spec/110) — Qt-free.

When a focus or exposure bracket cluster is open in the Picker, the
help panel needs three concrete facts about the bracket the user is
looking at: its kind, its member count, and the **link-stem prefix**
the return scanner expects an external tool's output to start with
(spec/108's round-trip contract: filename starts with the picked link
stem, ``D{day:02d}_{camera}_{originalname}``). This module builds that
context from a :class:`mira.picked.CullBucket` + gateway items, reusing
the same helpers the return scanner uses (``link_name`` from
``core.picked_media`` and the projection rules in
``mira.picked.external_returns``) so the rendered guidance and the
matcher stay in lock-step.

Pure logic — no Qt imports. Charter inv. #8.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.picked_media import PickedEntry, link_name
from core.path_builder import picked_media_dir


@dataclass(frozen=True)
class BracketHelpContext:
    """Everything the bracket help panel renders.

    ``name_prefix`` is the **link stem** (extension stripped) of the
    anchor bracket member — the prefix an external tool's filename must
    start with so the spec/57 §3.2 / spec/108 matcher binds the result
    back to this bracket. ``member_prefixes`` holds every member's stem
    so the panel can let the user copy whichever frame's name they
    pointed their stacker at (most external tools take the first input
    file's stem).
    """

    kind: str                   # "focus_bracket" or "exposure_bracket"
    member_count: int
    name_prefix: str            # the anchor member's link stem
    member_prefixes: tuple      # tuple[str, ...] — every member's link stem
    bracket_key: str
    picked_media_dir: Path      # absolute Picked Media/ path


def _entry_for_item(item, event_root: Path) -> PickedEntry:
    """Build a :class:`PickedEntry` from a gateway item the same way the
    spec/57 link projection does — keep the link-naming rule in ONE
    place (``link_name``). ``filename`` is the captured file's name on
    disk (not the link name)."""
    return PickedEntry(
        source_path=event_root / (item.origin_relpath or ""),
        filename=Path(item.origin_relpath or "").name,
        day_number=item.day_number,
        camera_id=item.camera_id,
    )


def build_help_context(
    bucket,
    *,
    gateway,
    event_root: Path,
) -> BracketHelpContext:
    """Resolve a bracket's :class:`BracketHelpContext` from the gateway.

    The anchor member is the chronologically first one (matches
    ``adopt_stack_output``'s anchor pick — spec/57 §2.3), so the copied
    prefix points at the same frame the stacker sees as its first input
    when the user drags the picked subdir in.
    """
    items: List = []
    for ci in getattr(bucket, "items", ()):
        it = gateway.item(ci.item_id)
        if it is None or not it.origin_relpath:
            continue
        items.append(it)
    items.sort(key=lambda it: it.capture_time_corrected or "")
    prefixes = tuple(
        Path(link_name(_entry_for_item(it, event_root))).stem
        for it in items
    )
    anchor_prefix = prefixes[0] if prefixes else ""
    return BracketHelpContext(
        kind=getattr(bucket, "kind", ""),
        member_count=getattr(bucket, "count", len(prefixes)),
        name_prefix=anchor_prefix,
        member_prefixes=prefixes,
        bracket_key=getattr(bucket, "bucket_key", ""),
        picked_media_dir=picked_media_dir(event_root),
    )


def is_bracket_kind(kind: Optional[str]) -> bool:
    """Visibility predicate shared by the help button + the panel
    constructor (spec/110 §2): only ``focus_bracket`` /
    ``exposure_bracket`` clusters get the help affordance — burst /
    repeat / single photos do not."""
    return kind in ("focus_bracket", "exposure_bracket")


__all__ = [
    "BracketHelpContext",
    "build_help_context",
    "is_bracket_kind",
]
