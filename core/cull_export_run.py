"""Export run/probe composition — Stage C inc.4c-2 (core, pure).

Two one-liners that compose the already-tested pieces so the shell
+ the off-thread runner + the dialog's collision probe all call
**one** function (and it is unit-tested as a unit, not just its
parts):

- :func:`collision_count` — the ``CullExportDialog`` collision
  probe: how many files of this manifest would clash in
  ``dest_root`` (per-file, frozen).
- :func:`run_export` — resolve the kept items to a ``dest_root``
  manifest and execute it with the chosen collision policy.

Qt-free; the off-thread :class:`~ui.culler.cull_export_runner`
wraps these.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from core.cull_export import (
    CollisionPolicy,
    ExportResult,
    detect_collisions,
    export_items,
)
from core.cull_export_resolver import KeptItem, build_export_manifest


def collision_count(
    kept_items: Iterable[KeptItem],
    dest_root: Path,
) -> int:
    """How many of these kept items already exist at their
    destination under ``dest_root`` (per destination file — the
    frozen collision unit). The dialog surfaces the Override/Unique
    choice only when this is > 0."""
    return len(
        detect_collisions(
            build_export_manifest(list(kept_items), dest_root)))


def run_export(
    kept_items: Iterable[KeptItem],
    dest_root: Path,
    *,
    collision: CollisionPolicy,
    allow_hardlinks: bool = True,
) -> ExportResult:
    """Resolve → execute. Lays ``Day/Style[/bracket]`` directly
    under ``dest_root`` (the user's chosen destination) and copies
    atomically with the per-file collision policy. Never raises out
    of the engine (bad items collected in the result).

    Model 3 v2 (Nelson 2026-05-22): ``allow_hardlinks=True`` by
    default — Cull-Export and Select-Export are pure consolidation
    (no EXIF rewriting for Fork-A-ingested events) so files in
    01-Culled and 02-Selected become hardlinks to their upstream
    counterparts. Items that DO carry an ``exif_datetime`` retime
    (legacy events going through Select-Export) automatically fall
    back to copy per :func:`export_items` — the retime would back-
    propagate through a hardlink, so we must NOT link those items."""
    return export_items(
        build_export_manifest(list(kept_items), dest_root),
        collision=collision,
        allow_hardlinks=allow_hardlinks,
    )
