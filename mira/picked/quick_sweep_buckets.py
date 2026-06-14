"""Quick Sweep bucket model — cluster raw source-card items into days/buckets.

Nelson 2026-06-01: the Quick Sweep should organise the card into the same day→bucket shape
the full culler shows, so the user can plan/triage by bucket instead of browsing every photo
sequentially — **without** losing speed (this is a one-time load-cost; browsing stays flat).

The Quick Sweep runs **pre-ingest** on raw card files — there is no event / gateway and no
trip-day assignment yet — so it can't use ``pick_days`` (gateway-backed). This builds the
organisation over ``SourceItem``-shaped objects instead:

* **Days = EXIF capture DATE** (calendar day; Nelson's choice). The real trip-day numbers /
  descriptions are assigned later, at ingest — date grouping is enough for early triage.
* **Buckets** come from the **same core bucket scanner the full culler uses** (identical
  moment / burst / bracket grouping + config), run per day.

Pure (the EXIF reader + scanner are injectable, shared with ``mira.picked.model``) →
unit-tested. The UI feeds it ``progress(done, total, day_label, n_items)`` to drive the same
load dialog the full culler uses.

**Nelson 2026-06-05 — Days panel + Day Grid:** the Quick Sweep now reuses the main Cull
days panel (:class:`mira.ui.base.bucket_navigator.BucketNavigator`) +
:class:`mira.ui.base.day_grid_view.DayGridView`. :func:`build_fast_days` wraps the
:func:`build_quick_sweep_buckets` output as the same :class:`mira.picked.model.PickDay` /
:class:`CullBucket` / :class:`CullItem` shapes those widgets already consume — no new
widget code, just an adapter.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from core.bucket_scanner import SourceKind
from core.cluster_classifier import (
    KIND_REPEAT,
    classify_clusters,
    split_repeats_in_nodes,
)
from core.repeat_detector import (
    DEFAULT_REPEAT_MIN_SEQUENCE_LENGTH,
    DEFAULT_REPEAT_WINDOW_SECONDS,
    RepeatDetectorConfig,
)
from core.video_discovery import VIDEO_EXTENSIONS
from mira.picked.model import (
    CullBucket,
    CullCell,
    CullCluster,
    PickDay,
    CullItem,
    _default_read_exif,
    _default_scan,
    _resolve_config,
)
from mira.picked.status import (
    BADGE_IN_PROGRESS,
    BADGE_UNTOUCHED,
    STATE_CANDIDATE,
    STATE_SKIPPED,
    STATE_PICKED,
    BucketStatus,
    cell_color_for_item,
    cluster_color,
)
from mira.store import models as m


# Cluster kinds that Quick Sweep surfaces as ONE day-grid cell (vs flattening
# to per-item cells). Mirrors :data:`mira.picked.model.REAL_CLUSTER_KINDS`
# plus the spec/52 Quick Sweep ``repeat`` addition — Quick Sweep is the only
# surface today that emits ``repeat`` buckets.
REAL_CLUSTER_KINDS: frozenset = frozenset({
    "burst", "focus_bracket", "exposure_bracket", "repeat",
})


@dataclass(frozen=True)
class FastBucket:
    """One Fast-Picker bucket — a day's slice of clustered card items, in scanner order."""

    day_label: str          # the capture date (ISO) or "Undated"
    kind: str               # scanner kind: individual / moment / burst / *_bracket / video
    title: str
    paths: tuple[Path, ...]

    @property
    def count(self) -> int:
        return len(self.paths)


def _day_label(d: Optional[date]) -> str:
    return d.isoformat() if d is not None else "Undated"


def _day_key(d: Optional[date]) -> str:
    return d.isoformat() if d is not None else "undated"


def build_quick_sweep_buckets(
    items: Sequence,
    *,
    source_kind: SourceKind = SourceKind.CAMERA,
    read_exif: Callable = _default_read_exif,
    scan_fn: Callable = _default_scan,
    config=None,
    progress: Optional[Callable[[int, int, str, int], None]] = None,
) -> List[FastBucket]:
    """Day → bucket ordered list over ``items`` (each needs ``.path``; optional
    ``.timestamp`` [datetime] for the day axis and ``.camera_id``). Same scanner + config as
    the full culler. ``progress(done, total, day_label, n)`` fires before each day + a final
    completion tick — feeds the load dialog."""
    from core.bucket_navigator_model import _flatten
    from core.import_pipeline import RawExifEntry

    eff_config = _resolve_config(config)
    by_day: dict[Optional[date], list] = {}
    for it in items:
        ts = getattr(it, "timestamp", None)
        d = ts.date() if ts is not None else None
        by_day.setdefault(d, []).append(it)

    ordered = sorted(by_day, key=lambda d: (d is None, d))
    total = len(ordered)
    out: List[FastBucket] = []
    for i, day in enumerate(ordered):
        day_items = by_day[day]
        if progress is not None:
            progress(i, total, _day_label(day), len(day_items))
        cam_for = {it.path: (getattr(it, "camera_id", "") or "") for it in day_items}
        exifs = read_exif([it.path for it in day_items])
        entries = [RawExifEntry(path=pe.path, exif=(pe.raw or {})) for pe in exifs]
        res = scan_fn(entries, source_kind, eff_config)
        # spec/52 Quick Sweep slice B (Nelson 2026-06-09) — add the repeat
        # layer over the scanner's individuals. The classifier is happy with
        # the scanner's mutually-exclusive bucket layout; we only re-shape
        # individual / moment nodes that contain repeat-claimed photos.
        # The repeat-detector window is user-tunable via Settings →
        # Collect → "Repeat-cluster window" (Nelson 2026-06-09 audit).
        try:
            from mira.settings.repo import SettingsRepo
            _settings = SettingsRepo().load()
            _repeat_cfg = RepeatDetectorConfig(
                window_seconds=float(_settings.repeat_window_seconds),
                min_sequence_length=DEFAULT_REPEAT_MIN_SEQUENCE_LENGTH,
            )
        except Exception:                                       # noqa: BLE001
            _repeat_cfg = RepeatDetectorConfig(
                window_seconds=DEFAULT_REPEAT_WINDOW_SECONDS,
                min_sequence_length=DEFAULT_REPEAT_MIN_SEQUENCE_LENGTH,
            )
        assignments = classify_clusters(res, repeat_config=_repeat_cfg)
        # The repeat-split helper now lives in core.cluster_classifier so
        # the main Picker (mira.picked.model._compute_day) gets the
        # same shape. Quick Sweep keeps emitting FastBuckets from the
        # post-split BucketNode list (Nelson 2026-06-09 — PickPage port
        # of repeat clusters).
        nodes = split_repeats_in_nodes(
            _flatten(_day_key(day), res, lambda p: cam_for.get(p, "")),
            assignments,
        )
        label = _day_label(day)
        for node in nodes:
            if not node.files:
                continue
            out.append(FastBucket(
                day_label=label, kind=node.kind, title=node.title,
                paths=tuple(node.files),
            ))
    if progress is not None:
        progress(total, total, "", 0)
    return out


# =========================================================================== #
# Day-Grid adapter (Nelson 2026-06-05) — wrap FastBuckets as the same
# PickDay / CullBucket / CullItem / CullCell shapes the main Cull's days
# panel + DayGridView consume, so the Quick Sweep reuses those widgets
# verbatim instead of running its own bucket-step UI.
# =========================================================================== #


def _kind_for(path: Path) -> str:
    return "video" if path.suffix.lower() in VIDEO_EXTENSIONS else "photo"


def _path_to_cull_item(path: Path, ts_by_path: Dict[Path, Optional[str]]) -> CullItem:
    """One CullItem for a SourceItem-path (no gateway → no item_id, no sha).

    ``item_id`` is the **path string** (POSIX form) — content-addressable enough for the
    UI to track per-cell state via the in-memory ``state_for`` callable; never reaches
    the database. ``capture_time_corrected`` is the optional ISO timestamp the caller
    pre-built from each SourceItem's ``.timestamp`` (the day-grid sort key).
    """
    return CullItem(
        item_id=path.as_posix(),
        path=path,
        kind=_kind_for(path),
        capture_time_corrected=ts_by_path.get(path),
        duration_ms=None,   # pre-ingest: we don't probe video durations
    )


def _phase_state_map(
    item_ids: Sequence[str],
    state_for: Callable[[Path], str],
) -> Dict[str, m.PhaseState]:
    """Convert the Fast-Picker's ``path → state`` callable into the
    ``{item_id: PhaseState}`` shape :func:`project_status` /
    :func:`cell_color_for_item` expect. Only paths with an EXPLICIT state
    end up in the map — untouched paths just don't appear, exactly as the
    gateway-backed flow does."""
    out: Dict[str, m.PhaseState] = {}
    for iid in item_ids:
        s = state_for(Path(iid))
        if s in (STATE_PICKED, STATE_SKIPPED, STATE_CANDIDATE):
            out[iid] = m.PhaseState(item_id=iid, phase="pick", state=s)
    return out


def _project_status(
    item_ids: Sequence[str], state_for: Callable[[Path], str],
) -> BucketStatus:
    """Synthesize a :class:`BucketStatus` from in-memory K/D state — no gateway,
    no soft-state row."""
    kept = candidate = discarded = untouched = 0
    for iid in item_ids:
        s = state_for(Path(iid))
        if s == STATE_PICKED:
            kept += 1
        elif s == STATE_CANDIDATE:
            candidate += 1
        elif s == STATE_SKIPPED:
            discarded += 1
        else:
            untouched += 1
    total = len(item_ids)
    has_marks = (kept + candidate + discarded) > 0
    return BucketStatus(
        total=total, kept=kept, candidate=candidate, discarded=discarded,
        untouched=untouched, reviewed=False, browsed=False,
        badge=BADGE_IN_PROGRESS if has_marks else BADGE_UNTOUCHED,
    )


def refresh_day_statuses(
    days: Sequence[PickDay], state_for: Callable[[Path], str],
) -> List[PickDay]:
    """Re-project every bucket status + day rollup against the current
    ``state_for`` callable. The expensive scan (EXIF + bucket detection
    from :func:`build_fast_days`) stays cached; only the cheap
    ``_project_status`` per-item count walk re-runs.

    Used by ``QuickSweepPage`` so the days panel's per-day Pick / Skip
    counts update live whenever the user returns from the Day Grid
    (Nelson 2026-06-13 — Bug 2: the days list inside Quick Sweep stayed
    at its load-time counts because PickDay / BucketStatus are
    frozen dataclasses; rebuilding cheap shapes here is the fix)."""
    out: List[PickDay] = []
    from mira.picked.model import CullBucket
    for day in days:
        new_buckets: List[CullBucket] = []
        for b in day.buckets:
            new_buckets.append(CullBucket(
                bucket_key=b.bucket_key,
                kind=b.kind,
                title=b.title,
                items=b.items,
                status=_project_status(
                    [ci.item_id for ci in b.items], state_for),
            ))
        all_ids = [ci.item_id for nb in new_buckets for ci in nb.items]
        out.append(PickDay(
            day_number=day.day_number,
            label=day.label,
            buckets=tuple(new_buckets),
            status=_project_status(all_ids, state_for),
        ))
    return out


def build_fast_days(
    items: Sequence,
    *,
    source_kind: SourceKind = SourceKind.CAMERA,
    read_exif: Callable = _default_read_exif,
    scan_fn: Callable = _default_scan,
    config=None,
    progress: Optional[Callable[[int, int, str, int], None]] = None,
    state_for: Optional[Callable[[Path], str]] = None,
) -> List[PickDay]:
    """Build :class:`mira.picked.model.PickDay` shapes from raw card items so the
    Quick Sweep can reuse the main Cull's days panel + Day Grid widgets.

    Day numbers are synthesised in chronological order (1, 2, 3, …) since pre-ingest
    there are no real trip-day numbers yet (those get assigned at ingest). Day labels
    are the EXIF capture date (or ``"Undated"`` for items without a timestamp).

    ``state_for(path) -> 'picked' | 'skipped' | 'candidate' | <other>`` exposes the
    page's in-memory K/D map so the days-panel status bars and cell border colours
    track live edits. Defaults to "every item is KEPT" (Quick Sweep's default-Keep
    convention — Nelson 2026-05-25 freeze)."""
    if state_for is None:
        state_for = lambda _p: STATE_PICKED      # noqa: E731 — terse default

    fast = build_quick_sweep_buckets(
        items,
        source_kind=source_kind,
        read_exif=read_exif,
        scan_fn=scan_fn,
        config=config,
        progress=progress,
    )

    # Group FastBuckets by their day label (chronological inside each by scanner).
    by_label: Dict[str, List[FastBucket]] = defaultdict(list)
    for fb in fast:
        by_label[fb.day_label].append(fb)

    # Capture-time index for the cell sort key. Walk the items once; missing items
    # (e.g. tests that fed paths the scanner kept but no original SourceItem for)
    # just sort to the top of the day.
    ts_by_path: Dict[Path, Optional[str]] = {}
    for it in items:
        ts = getattr(it, "timestamp", None)
        ts_by_path[it.path] = ts.isoformat() if ts is not None else None

    # "Undated" sorts last; otherwise ISO date string sort is correct.
    ordered = sorted(
        by_label.keys(),
        key=lambda lbl: (lbl == "Undated", lbl),
    )

    days: List[PickDay] = []
    for day_num, day_label in enumerate(ordered, start=1):
        fbuckets = by_label[day_label]
        cbuckets: List[CullBucket] = []
        for fb in fbuckets:
            citems = tuple(_path_to_cull_item(p, ts_by_path) for p in fb.paths)
            status = _project_status(
                [ci.item_id for ci in citems], state_for)
            cbuckets.append(CullBucket(
                bucket_key=f"{day_num}|{fb.kind}|{fb.title}",
                kind=fb.kind, title=fb.title,
                items=citems, status=status,
            ))
        # Day-level rollup is the same arithmetic on the same paths.
        all_ids = [
            ci.item_id for b in cbuckets for ci in b.items
        ]
        day_status = _project_status(all_ids, state_for)
        days.append(PickDay(
            day_number=day_num,
            label=(f"Day {day_num} — {day_label}"
                   if day_label != "Undated" else day_label),
            buckets=tuple(cbuckets),
            status=day_status,
        ))
    return days


def fast_day_grid_cells(
    day: PickDay,
    state_for: Callable[[Path], str],
    *,
    visited_for: Optional[Callable[[str], bool]] = None,
    cluster_visited_for: Optional[Callable[[str], bool]] = None,
) -> List[CullCell]:
    """Build the flat :class:`CullCell` list :class:`DayGridView` consumes for one
    Quick Sweep day. Same shape as :func:`mira.picked.model.day_grid_cells`
    but pre-ingest: status comes from ``state_for`` (no gateway / phase_state), and
    the yellow-video / clip-extracts rule does not apply (the source is raw card
    files; nothing has been materialised yet).

    **Cluster cells (spec/52 Quick Sweep redesign, Nelson 2026-06-09).** Buckets
    whose ``kind`` is in :data:`REAL_CLUSTER_KINDS` (``burst`` /
    ``focus_bracket`` / ``exposure_bracket`` / ``repeat``) collapse to ONE
    cluster cell each — the user picks keepers in a sub-grid (slice C wires
    that). All other bucket kinds (``individual`` / ``moment`` / ``video`` /
    anything else) still flatten to per-item cells.

    **Visited ticks (Nelson 2026-06-09 — port of PickPage spec/32 §2.10).**
    Quick Sweep has no event.db backing pre-ingest, so the host tracks
    visited cells in memory and passes two lookup callables:

    * ``visited_for(item_id)`` — returns ``True`` when the user has
      already centre-clicked that item into the single-photo viewer.
    * ``cluster_visited_for(bucket_key)`` — returns ``True`` when the
      user has already drilled into that cluster (= bucket_key).

    Both default to ``None`` (no tick). Bucket-level ``browsed`` is the
    fallback for clusters when the host doesn't pass a lookup.

    The Quick Sweep treats Compare as Keep at SAVE time but the cell still shows
    ORANGE during triage so the user can see which they marked for later review.
    ``default_state=STATE_PICKED`` is the Quick Sweep's contract."""
    cells: List[CullCell] = []
    for bucket in day.buckets:
        item_ids = [ci.item_id for ci in bucket.items]
        phase_states = _phase_state_map(item_ids, state_for)

        if bucket.kind in REAL_CLUSTER_KINDS:
            member_colors = [
                cell_color_for_item(
                    ci.item_id, ci.kind, "pick", phase_states,
                    default_state=STATE_PICKED,
                )
                for ci in bucket.items
            ]
            color = cluster_color(member_colors)
            cluster = CullCluster(
                bucket_key=bucket.bucket_key,
                kind=bucket.kind,
                title=bucket.title,
                members=bucket.items,
                color=color,
                detection_source=bucket.detection_source,
                camera=bucket.camera,
            )
            visited = (
                bool(cluster_visited_for(bucket.bucket_key))
                if cluster_visited_for is not None
                else bool(bucket.browsed)
            )
            cells.append(CullCell(
                end_time=_cluster_end_time(bucket.items),
                color=color,
                cluster=cluster,
                visited=visited,
            ))
            continue

        for ci in bucket.items:
            color = cell_color_for_item(
                ci.item_id, ci.kind, "pick", phase_states,
                default_state=STATE_PICKED,
            )
            visited = (
                bool(visited_for(ci.item_id))
                if visited_for is not None
                else False
            )
            cells.append(CullCell(
                end_time=(ci.capture_time_corrected or ""),
                color=color,
                item_id=ci.item_id,
                item_kind=ci.kind,
                visited=visited,
            ))

    cells.sort(key=lambda c: (
        c.end_time or "",
        c.item_id or (c.cluster.bucket_key if c.cluster else ""),
    ))
    return cells


def _cluster_end_time(members: Sequence[CullItem]) -> str:
    """Latest member capture_time_corrected — the cluster's chronological
    sort key. Quick Sweep doesn't probe video durations pre-ingest, so the
    per-item end-time IS the capture time. Mirror of the per-item sort key
    in :func:`fast_day_grid_cells`."""
    times = [ci.capture_time_corrected for ci in members if ci.capture_time_corrected]
    return max(times) if times else ""
