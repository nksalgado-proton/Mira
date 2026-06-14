"""The headless Cull data model (spec/11 §5).

Qt-free. Turns gateway items + phase_state into the Day → Bucket → item tree the
navigator renders, with the **honest** four-way status projection (kept / candidate /
discarded / untouched) the new ``phase_state`` model enables — no badge-gating
heuristic (spec/11 §3). The clustering itself reuses the legacy pure-logic scanner
(``core/bucket_scanner`` + ``core/bucket_navigator_model._flatten``); this package only
binds it to the gateway and projects status. The UI surfaces bind to this, never to a
journal or the filesystem.
"""
from mira.picked.status import (
    BADGE_BROWSED,
    BADGE_DONE,
    BADGE_IN_PROGRESS,
    BADGE_UNTOUCHED,
    BucketStatus,
    CellColor,
    cell_color_for_item,
    cluster_color,
    project_status,
    rollup_status,
)
from mira.picked.model import (
    REAL_CLUSTER_KINDS,
    CullBucket,
    CullCell,
    CullCluster,
    PickDay,
    CullItem,
    build_pick_days,
    pick_days,
    day_grid_cells,
)

__all__ = [
    "BADGE_BROWSED",
    "BADGE_DONE",
    "BADGE_IN_PROGRESS",
    "BADGE_UNTOUCHED",
    "BucketStatus",
    "CellColor",
    "cell_color_for_item",
    "cluster_color",
    "project_status",
    "rollup_status",
    "REAL_CLUSTER_KINDS",
    "CullBucket",
    "CullCell",
    "CullCluster",
    "PickDay",
    "CullItem",
    "build_pick_days",
    "pick_days",
    "day_grid_cells",
]
