"""Select-phase pool helpers — unified Select model (spec/48 Slice B, 2026-06-06).

The unified Select phase covers ALL captured items at once. ``pick_pool_ids``
returns every captured item the gateway knows about, minus video masters (their
clips + snapshots are independent items). There is no upstream filter:

* **Quick Sweep** (during Collect) does not persist decisions. Files the user
  marked Discard in Quick Sweep are simply not copied into the event, so they
  never reach this pool. Progressive-filter rule holds without an explicit
  filter (Nelson 2026-06-06).
* **Default-state** rendering happens inside the surface: items with no
  explicit ``phase_state('pick')`` row render in the per-phase default
  colour controlled by the ``pick_default_state`` setting.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Set

from core.bucket_scanner import SourceKind
from mira.picked.model import PickDay, pick_days as _select_days_base


def pick_pool_ids(gateway) -> Set[str]:
    """Every captured item except video masters.

    Masters (captured videos cut into clips/snapshots at Quick Sweep) are
    dropped — their clips + snapshots are independent items that carry their
    own decisions.
    """
    items = gateway.items()
    master_ids = {it.parent_item_id for it in items if it.parent_item_id}
    return {it.id for it in items if it.id not in master_ids}


def pick_days(
    gateway,
    *,
    source_kind: SourceKind = SourceKind.CAMERA,
    read_exif: Optional[Callable] = None,
    scan_fn: Optional[Callable] = None,
    config=None,
    progress: Optional[Callable] = None,
) -> List[PickDay]:
    """Build the Day → Bucket tree for the unified Select surface.

    The pool is every captured item (minus video masters). Delegates to
    :func:`~mira.picked.model.pick_days`.
    """
    kwargs: dict = dict(
        phase="pick",
        source_kind=source_kind,
        config=config,
        progress=progress,
        item_ids=frozenset(pick_pool_ids(gateway)),
    )
    if read_exif is not None:
        kwargs["read_exif"] = read_exif
    if scan_fn is not None:
        kwargs["scan_fn"] = scan_fn
    return _select_days_base(gateway, **kwargs)
