"""Missing-originals classification — the detection layer over the captured tree.

Charter invariant #7 says the captured tree is never mutated. Today nothing
*detects* when it silently is — by a drive unmount, a folder move, or a
genuine delete. This module is the read-only signal that drives the
locate/relink flow: it classifies why ``Original Media/`` isn't where the
index says it should be, so the UI can pick the right response.

The exported reconcile (:meth:`EventGateway.rescan_exported_media`) treats
the filesystem as truth and **prunes** missing files. That is safe for
exported media — JPEGs are regenerable. Originals are not. A missing
original is overwhelmingly *moved* or *offline*, almost never *deleted*. So
the rule is inverted: detect, surface, and let the user choose. Pruning
happens only through an explicit confirmation, never from this signal.

The check itself is a pure filesystem read (no ``event.db`` touched); it
runs once per :class:`Gateway` event-open and the result is cached on the
caller. Phase-entry re-checks are theatre — the gateway lifetime is the
debounce.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


ORIGINAL_MEDIA_DIRNAME = "Original Media"


class OriginalsHealth(Enum):
    """The three states the locate/relink flow keys on.

    * ``OK`` — ``event_root`` exists and ``Original Media/`` is present and
      non-empty. Proceed normally.
    * ``STORAGE_OFFLINE`` — the storage anchor (or the abs-anchored event's
      drive root) is unreadable. Likely cause: unplugged drive, Synology off.
      Action: non-destructive alert, zero data change.
    * ``ORIGINALS_MOVED`` — the storage is reachable, but the event's
      originals aren't where the index expects (event folder moved, or
      ``Original Media/`` carved out). Action: offer Locate → relink.
    """
    OK = "ok"
    STORAGE_OFFLINE = "storage_offline"
    ORIGINALS_MOVED = "originals_moved"


@dataclass(frozen=True)
class OriginalsCheck:
    """The verdict from :meth:`Gateway.check_originals`.

    ``event_root`` is the path the index resolves to *right now* (may not
    exist — that's the whole point). ``originals_dir`` is the same with the
    ``Original Media/`` leaf appended. The UI uses both to name what's
    missing in the dialog body.
    """
    state: OriginalsHealth
    event_root: Optional[Path]
    base_path: Optional[Path]
    originals_dir: Optional[Path]

    @property
    def is_ok(self) -> bool:
        return self.state == OriginalsHealth.OK


def _is_dir_empty(p: Path) -> bool:
    """``True`` if ``p`` exists as a directory and contains no entries.

    Cheap: ``next(iterdir())`` short-circuits after the first entry. We do
    NOT recurse — an event with a single empty cameras subfolder still
    counts as empty originals at this layer.
    """
    try:
        next(p.iterdir())
    except StopIteration:
        return True
    except OSError:
        # Not readable — caller already classified the parent unreachable;
        # treat this leaf as missing too.
        return True
    return False


def _any_ancestor_exists(p: Path) -> bool:
    """Walk up from ``p`` through every parent. ``True`` if any directory
    along the chain is readable. ``False`` only when the drive root itself
    is gone (the OFFLINE signal).

    Disambiguates "the folder was moved/deleted" (some ancestor still
    exists) from "the drive is unmounted" (nothing up the chain exists).
    On Windows that means ``D:\\Photos\\...`` returns True even with the
    leaf gone, but a path under a missing ``Z:\\`` returns False.
    """
    for ancestor in p.parents:
        try:
            if ancestor.exists():
                return True
        except OSError:
            continue
    return False


def classify(
    *,
    base_path: Optional[Path],
    event_root: Optional[Path],
    requires_base: bool,
) -> OriginalsCheck:
    """Pure classification — no Gateway, no index, just paths.

    ``base_path`` is the resolved ``photos_base_path`` (or ``None`` if not
    configured). ``event_root`` is the resolved root for this event
    (``None`` only when neither relpath nor abs-fallback is set in the
    index). ``requires_base`` is True for relative-anchored events (base
    unreachable orphans them) and False for abs-anchored events (their
    root doesn't depend on the base).

    Decision order is OFFLINE-before-MOVED so a drive unmount never gets
    misread as a deliberate move.
    """
    originals_dir = (event_root / ORIGINAL_MEDIA_DIRNAME) if event_root else None

    if requires_base and base_path is not None and not base_path.exists():
        return OriginalsCheck(
            state=OriginalsHealth.STORAGE_OFFLINE,
            event_root=event_root,
            base_path=base_path,
            originals_dir=originals_dir,
        )

    if event_root is None:
        return OriginalsCheck(
            state=OriginalsHealth.STORAGE_OFFLINE,
            event_root=None,
            base_path=base_path,
            originals_dir=None,
        )

    if not event_root.exists():
        if _any_ancestor_exists(event_root):
            return OriginalsCheck(
                state=OriginalsHealth.ORIGINALS_MOVED,
                event_root=event_root,
                base_path=base_path,
                originals_dir=originals_dir,
            )
        return OriginalsCheck(
            state=OriginalsHealth.STORAGE_OFFLINE,
            event_root=event_root,
            base_path=base_path,
            originals_dir=originals_dir,
        )

    if originals_dir is None or not originals_dir.exists() or _is_dir_empty(originals_dir):
        return OriginalsCheck(
            state=OriginalsHealth.ORIGINALS_MOVED,
            event_root=event_root,
            base_path=base_path,
            originals_dir=originals_dir,
        )

    return OriginalsCheck(
        state=OriginalsHealth.OK,
        event_root=event_root,
        base_path=base_path,
        originals_dir=originals_dir,
    )
