"""Process phase — item pool: Select-Kept items only (spec/32 §6.3).

The Day → Bucket tree for Process is the same one Cull/Select use, but:
  1. Only items whose **Select** phase-state is "picked" are included in the pool.
  2. ``phase="edit"`` for all marks (the rebuild's `Adjustment.edit_exported`
     is the cell-colour signal, not phase_state — but the gateway calls in this
     module still pass the phase string so it can key visited / browsed state).
  3. No per-camera filter (Process is cross-camera by design, same as Select).

``process_days`` is the entry point for the EditHostPage, analogous to
:func:`~mira.picked.pick_model.pick_days` for PickPage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Set

from core.bucket_scanner import SourceKind
from core.picked_media import PickedEntry
from mira.picked.model import PickDay, pick_days
from mira.picked.status import STATE_SKIPPED, STATE_PICKED


def edit_pool_ids(
    gateway, pick_default_state: str = STATE_SKIPPED,
) -> Set[str]:
    """The Process pool = items whose **effective Select state is kept**
    (the carry-forward from Select).

    Effective = explicit ``phase_state('pick')`` if present, else the
    configured Select default (Settings → "Default state for un-decided items
    at Select").  So when the Select default is **discard** (the common case)
    only explicitly-kept items carry forward; when it is **keep**, un-decided
    items carry forward too (mirrors the Cull→Select pattern in
    :func:`~mira.picked.pick_model.pick_pool_ids`).

    Masters (captured videos with materialised clip children) are always
    dropped — Select-Kept clips/snapshots already replaced them at Cull
    materialisation.
    """
    items = gateway.items()
    sel_states = gateway.phase_states("pick")
    master_ids = {it.parent_item_id for it in items if it.parent_item_id}
    pool: Set[str] = {
        iid for iid, ps in sel_states.items()
        if ps.state == STATE_PICKED and iid not in master_ids
    }
    if pick_default_state == STATE_PICKED:
        decided = set(sel_states)
        pool |= {
            it.id for it in items
            if it.id not in decided and it.id not in master_ids
        }
    return pool


def picked_media_entries(
    gateway, pick_default_state: str = STATE_SKIPPED,
) -> List[PickedEntry]:
    """Assemble the spec/57 ``Picked Media/`` projection inputs.

    Mirrors the Edit pool rule exactly (:func:`edit_pool_ids` — explicit
    Picks plus the configured pick default for un-decided items), then
    keeps only **byte-bearing** rows (virtual segments/snapshots have
    nothing to link) and threads through the bracket membership so
    focus/exposure bracket members land in their per-bracket subdir
    instead of the flat root (spec/57 §2.1). Bracket grouping comes from
    the cached scanner clusters (``gateway.bracket_memberships`` — the
    brackets the user actually saw in the day grid), with the
    still-unpopulated ``item.bracket_group_id`` ingest-detector column
    as a per-item override when it ever arrives.

    Requires a resolvable ``gateway.event_root`` (sources are absolute
    paths under it). Returns entries in capture order — the projection
    is order-insensitive but determinism keeps reruns byte-identical.
    """
    if gateway.event_root is None:
        raise RuntimeError("picked_media_entries needs a resolvable event_root")
    root = Path(gateway.event_root)
    pool = edit_pool_ids(gateway, pick_default_state)
    try:
        memberships = gateway.bracket_memberships("pick")
    except Exception:  # noqa: BLE001 — cache-less stores degrade to flat root
        memberships = {}
    entries: List[PickedEntry] = []
    for it in gateway.items():
        if it.id not in pool or not it.origin_relpath:
            continue
        src = root / it.origin_relpath
        cached = memberships.get(it.id)
        entries.append(PickedEntry(
            source_path=src,
            filename=Path(it.origin_relpath).name,
            day_number=it.day_number,
            camera_id=it.camera_id,
            bracket_group_id=it.bracket_group_id or (cached[0] if cached else None),
            item_id=it.id,
        ))
    return entries


def process_days(
    gateway,
    *,
    source_kind: SourceKind = SourceKind.CAMERA,
    read_exif: Optional[Callable] = None,
    scan_fn: Optional[Callable] = None,
    config=None,
    progress: Optional[Callable] = None,
    pick_default_state: str = STATE_SKIPPED,
) -> List[PickDay]:
    """Build the Day → Bucket tree for the Process surface.

    Filters the item pool to the Select carry-forward set
    (:func:`edit_pool_ids`, honouring ``pick_default_state``), then
    delegates to :func:`~mira.picked.model.pick_days` with ``phase="edit"``.
    The resulting tree carries status counts that are mostly informational at
    Process (the user-visible signal is per-item ``Adjustment.edit_exported``,
    routed through :func:`~mira.picked.status.cell_color_for_process_item`).
    """
    kept_ids = edit_pool_ids(gateway, pick_default_state)
    kwargs: dict = dict(
        phase="edit",
        source_kind=source_kind,
        config=config,
        progress=progress,
        item_ids=frozenset(kept_ids),
    )
    if read_exif is not None:
        kwargs["read_exif"] = read_exif
    if scan_fn is not None:
        kwargs["scan_fn"] = scan_fn
    return pick_days(gateway, **kwargs)
