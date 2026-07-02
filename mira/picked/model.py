"""The Day → Bucket → item tree for the Cull surface (spec/11 §5).

Binds the reused pure-logic scanner (``core/bucket_scanner`` +
``core/bucket_navigator_model._flatten``) to the **gateway**: the durable day axis comes
from ``item.day_number``; the within-day clustering is (re)computed from EXIF read off
the byte-pristine origin files; each computed bucket's files are mapped back to their
``item_id`` so status comes from ``phase_state`` (spec/11 §3), not folder names or a
journal.

This is the **compute path** — the foundation, and the recompute path for the planned
fingerprint-invalidated bucket cache (spec/11 §4). ``read_exif`` and ``scan_fn`` are
injected so the whole tree is unit-testable without real camera metadata (mirrors
``core.bucket_navigator_model.build_days``).

Qt-free. Never the source of truth for marks — that is the gateway.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from mira.picked.status import (
    STATE_PICKED,
    STATE_SKIPPED,
    BucketStatus,
    CellColor,
    cell_color_for_item,
    cluster_color,
    project_status,
    rollup_status,
)
from mira.store import models as m

# ``SourceKind`` is the only scanner symbol needed at import time; ``scan`` /
# ``read_exif_batch`` / ``_flatten`` are imported lazily inside the defaults so importing
# this module stays cheap and Qt/exiftool-free (and so tests can inject fakes).
from core.bucket_scanner import SourceKind


@dataclass(frozen=True)
class CullItem:
    """One captured item inside a cull bucket, in scanner order."""

    item_id: str
    path: Path
    kind: str  # 'photo' | 'video'
    capture_time_corrected: Optional[str] = None  # DB corrected time; drives display + sort
    duration_ms: Optional[int] = None  # video length (None for photos); end-time = start + duration_ms


@dataclass(frozen=True)
class CullBucket:
    """One cullable bucket within a day — the navigator's bucket-row model.

    ``bucket_key`` is the content-stable id (``{day}|{kind}|{content_key}``) the scanner
    emits; it keys this bucket's persisted soft-state and survives a membership-preserving
    recompute (spec/11 §4).

    ``browsed`` mirrors ``bucket.browsed`` from the soft-state load; carried here so
    the Day Grid layer (which builds :class:`CullCell` from these) can stamp the
    cluster-cell visited tick (spec/32 §2.10) without a second per-bucket query."""

    bucket_key: str
    kind: str  # focus_bracket|exposure_bracket|burst|moment|individual|video
    title: str
    items: tuple[CullItem, ...]
    status: BucketStatus
    detection_source: str = ""
    camera: str = ""
    browsed: bool = False

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(ci.item_id for ci in self.items)

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class PickDay:
    """One day in the resume map — durable ``day_number`` axis + its buckets + rollup."""

    day_number: Optional[int]
    label: str
    buckets: tuple[CullBucket, ...]
    status: BucketStatus


def _default_read_exif(paths: Sequence[Path]) -> list:
    from core.exif_reader import read_exif_batch

    return read_exif_batch(list(paths))


def _iso_to_exif_dt(iso_str: str) -> str:
    """ISO-8601 → EXIF DateTimeOriginal format (YYYY:MM:DD HH:MM:SS, local, no offset).

    The bucket scanner expects local-time timestamps.  ``capture_time_corrected``
    already carries the TZ-corrected local time, so stripping the UTC offset
    and reformatting is all that's needed."""
    import datetime as _dt
    try:
        return _dt.datetime.fromisoformat(iso_str).strftime("%Y:%m:%d %H:%M:%S")
    except (ValueError, TypeError):
        return ""


def _default_scan(entries, source_kind, config):
    from core.bucket_scanner import scan

    return scan(entries, source_kind, config)


def _day_label(day_number: Optional[int], trip_days: Dict[int, m.TripDay]) -> str:
    if day_number is None:
        return "Undated"
    td = trip_days.get(day_number)
    if td is not None and (td.description or td.date):
        bits = [b for b in (td.description, td.date) if b]
        return f"Day {day_number} — " + " · ".join(bits)
    return f"Day {day_number}"


def _day_key(day_number: Optional[int]) -> str:
    return "undated" if day_number is None else str(day_number)


def _captured_by_day(
    gateway,
    camera_id: Optional[str] = None,
    item_ids: Optional[frozenset] = None,
) -> Dict[Optional[int], list[m.Item]]:
    """Captured items (cull operates on originals; derivatives appear at Process),
    grouped by the durable ``day_number``. ``camera_id`` restricts to one camera — the
    legacy in-event cull is per-camera (Nelson 2026-06-01); buckets can be cross-camera
    under day-grouping, so the camera filter is applied to the item set BEFORE clustering.
    ``item_ids`` further restricts to a pre-determined set (used by Select to limit the
    pool to Cull-Kept items only)."""
    by_day: Dict[Optional[int], list[m.Item]] = {}
    for it in gateway.items():
        if it.provenance == "captured":
            pass
        elif it.provenance == "snapshot" and it.origin_relpath:
            # Materialized snapshot JPEG — appears in the photo pool at Select.
            # camera_id filter: snapshot has no camera_id; skip in per-camera mode.
            if camera_id is not None:
                continue
        elif it.provenance == "clip" and it.origin_relpath:
            # Materialized clip MP4 — appears as a video item at Select (after Cull the
            # master is gone; only clips/snapshots remain).  Derivatives carry no camera_id,
            # so they surface in the cross-camera Select pool (camera_id is None) and are
            # skipped in the per-camera in-event Cull (camera_id set) — same rule as snapshots.
            if camera_id is not None:
                continue
        else:
            continue
        if camera_id is not None and it.camera_id != camera_id:
            continue
        if item_ids is not None and it.id not in item_ids:
            continue
        by_day.setdefault(it.day_number, []).append(it)
    return by_day


def _compute_day(
    gateway,
    day_number: Optional[int],
    day_items: Sequence[m.Item],
    *,
    phase: str,
    source_kind: SourceKind,
    read_exif: Callable[[Sequence[Path]], list],
    scan_fn: Callable,
    config,
    phase_states: Dict[str, m.PhaseState],
    event_root: Path,
) -> List[CullBucket]:
    """Cluster one day's items into buckets — the EXIF-reading compute path (and the
    recompute path behind the cache). Resolves item paths, reads EXIF, runs the reused
    scanner + ``_flatten``, maps each bucket's files back to ``item_id``, projects live
    status."""
    from core.bucket_navigator_model import _flatten
    from core.cluster_classifier import (
        classify_clusters, split_repeats_in_nodes,
    )
    from core.import_pipeline import RawExifEntry
    from core.repeat_detector import (
        DEFAULT_REPEAT_MIN_SEQUENCE_LENGTH,
        DEFAULT_REPEAT_WINDOW_SECONDS,
        RepeatDetectorConfig,
    )

    path_to_item: Dict[Path, m.Item] = {
        event_root / it.origin_relpath: it for it in day_items
    }
    exifs = read_exif(list(path_to_item.keys()))
    entries = []
    for pe in exifs:
        raw = dict(pe.raw or {})
        it = path_to_item.get(pe.path)
        if it is not None and it.capture_time_corrected:
            # Override DateTimeOriginal with the DB-corrected local time so the
            # scanner orders items consistently regardless of what timezone the raw
            # EXIF uses.  GoPro MP4 CreateDate is UTC; photos use local time after
            # TZ bake — without this, video buckets sort before all photos in events
            # with a positive UTC offset (e.g. Nepal = UTC+5:45).
            raw["DateTimeOriginal"] = _iso_to_exif_dt(it.capture_time_corrected)
        entries.append(RawExifEntry(path=pe.path, exif=raw))
    res = scan_fn(entries, source_kind, config)

    def _camera_for(p: Path) -> str:
        it = path_to_item.get(p)
        return it.camera_id if it is not None else ""

    nodes = _flatten(_day_key(day_number), res, _camera_for)

    # spec/52 Quick Sweep slice B port (Nelson 2026-06-09) — apply the
    # phone-repeat cluster layer over scanner individuals so the Picker
    # matches the Quick Sweep's cluster-aware rendering. Settings-driven
    # window (defensive fallback to the detector's hardcoded default if
    # Settings can't be read, mirrors quick_sweep_buckets).
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
    nodes = split_repeats_in_nodes(nodes, assignments)

    buckets: List[CullBucket] = []
    for node in nodes:
        cull_items: list[CullItem] = []
        for p in node.files:
            it = path_to_item.get(p)
            if it is None:  # a path the scanner saw but we can't map — skip defensively
                continue
            cull_items.append(CullItem(
                item_id=it.id, path=p, kind=it.kind,
                capture_time_corrected=it.capture_time_corrected or None,
                duration_ms=it.duration_ms,
            ))
        if not cull_items:
            continue
        soft = gateway.bucket(node.bucket_id, phase)
        status = project_status([ci.item_id for ci in cull_items], phase_states, soft)
        buckets.append(
            CullBucket(
                bucket_key=node.bucket_id, kind=node.kind, title=node.title,
                items=tuple(cull_items), status=status,
                detection_source=node.detection_source, camera=node.camera,
                browsed=bool(soft.browsed) if soft is not None else False,
            )
        )
    return buckets


def build_pick_days(
    gateway,
    *,
    phase: str = "pick",
    source_kind: SourceKind = SourceKind.CAMERA,
    read_exif: Callable[[Sequence[Path]], list] = _default_read_exif,
    scan_fn: Callable = _default_scan,
    config=None,
    camera_id: Optional[str] = None,
    item_ids: Optional[frozenset] = None,
) -> List[PickDay]:
    """Build the Day → Bucket → item tree for ``phase`` — the **uncached** compute path.

    Groups captured items by the durable ``day_number``; per day clusters via the reused
    scanner and projects honest status from ``phase_state`` + bucket soft-state.
    ``read_exif`` / ``scan_fn`` are injected for deterministic tests. ``camera_id`` scopes to
    one camera (the per-camera in-event cull). ``item_ids`` restricts the item pool to a
    pre-determined set (Select uses this to show Cull-Kept items only). For the persisted,
    fingerprint-invalidated path use :func:`pick_days`."""
    event_root = Path(gateway.event_root) if gateway.event_root is not None else Path(".")
    phase_states = gateway.phase_states(phase)
    trip_days = {td.day_number: td for td in gateway.trip_days()}
    by_day = _captured_by_day(gateway, camera_id, item_ids)

    days: List[PickDay] = []
    for day_number in sorted(by_day, key=lambda d: (d is None, d)):
        buckets = _compute_day(
            gateway, day_number, by_day[day_number], phase=phase,
            source_kind=source_kind, read_exif=read_exif, scan_fn=scan_fn,
            config=config, phase_states=phase_states, event_root=event_root,
        )
        if not buckets:
            continue
        days.append(_assemble_day(day_number, buckets, trip_days))
    return days


def _assemble_day(day_number, buckets, trip_days) -> PickDay:
    return PickDay(
        day_number=day_number,
        label=_day_label(day_number, trip_days),
        buckets=tuple(buckets),
        status=rollup_status([b.status for b in buckets]),
    )


def _camera_scoped_days(
    gateway, *, phase, source_kind, read_exif, scan_fn, config, progress, camera_id,
) -> List[PickDay]:
    """Per-camera Day → Bucket tree — ``pick_days``' ``camera_id`` branch.

    **Cache-aware (fixed 2026-06-02):** the all-camera cache is keyed on all items for a
    day; if it is valid we load and filter by camera without touching EXIF — turning a
    ~10 s Back-button re-scan into a sub-second cache lookup.  On a miss we fall through to
    the full compute path (no write: partial per-camera data must not clobber the
    all-camera cache)."""
    event_root = Path(gateway.event_root) if gateway.event_root is not None else Path(".")
    phase_states = gateway.phase_states(phase)
    trip_days = {td.day_number: td for td in gateway.trip_days()}
    by_day_cam = _captured_by_day(gateway, camera_id)   # this camera's items (for compute)
    by_day_all = _captured_by_day(gateway)              # all items (for fingerprint check)
    eff_config = _resolve_config(config)

    ordered = sorted(by_day_cam, key=lambda d: (d is None, d))
    total = len(ordered)
    days: List[PickDay] = []
    for i, day_number in enumerate(ordered):
        if progress is not None:
            progress(i, total, day_number, len(by_day_cam[day_number]))

        all_day_items = by_day_all.get(day_number, [])
        fingerprint = _day_fingerprint(all_day_items, eff_config)

        if gateway.clustering_fingerprint(phase, day_number) == fingerprint:
            # Cache hit: load the full-day buckets and filter to this camera — no EXIF.
            all_buckets = _load_cached_day(
                gateway, day_number, all_day_items,
                phase=phase, phase_states=phase_states, event_root=event_root,
            )
            buckets = [b for b in all_buckets if b.camera == camera_id]
        else:
            # Cache miss: compute from EXIF using this camera's items.  Do NOT write to
            # the all-camera cache — we only have partial data.
            buckets = _compute_day(
                gateway, day_number, by_day_cam[day_number], phase=phase,
                source_kind=source_kind, read_exif=read_exif, scan_fn=scan_fn,
                config=eff_config, phase_states=phase_states, event_root=event_root,
            )

        if buckets:
            days.append(_assemble_day(day_number, buckets, trip_days))

    if progress is not None:
        progress(total, total, None, 0)
    return days


# --------------------------------------------------------------------------- #
# Cached path (spec/11 §4, D5-revised) — persist the clustering, recompute only on a
# fingerprint change (the moment-gap setting / a TZ re-adjustment / the item set).
# --------------------------------------------------------------------------- #


def _resolve_config(config):
    if config is not None:
        return config
    from core.bucket_scanner import load_bucket_scanner_config

    return load_bucket_scanner_config()


def _day_fingerprint(day_items: Sequence[m.Item], config) -> str:
    """Hash the variable clustering inputs for one day: the scanner-config knobs that
    affect grouping (chiefly the moment-gap window) + each item's identity, corrected
    capture time, and kind. The files' clustering EXIF is fixed (byte-pristine
    originals) so it can't change without the item set changing — hence it need not be
    hashed directly (spec/11 §4)."""
    import hashlib

    cfg = (
        f"cw={getattr(config, 'cluster_window_seconds', '')};"
        f"cm={getattr(config, 'cluster_min_size', '')};"
        f"bg={getattr(config, 'camera_burst_max_gap_seconds', '')};"
        f"bl={getattr(config, 'camera_burst_min_sequence_length', '')}"
    )
    parts = sorted(
        f"{it.id}|{it.capture_time_corrected}|{it.kind}" for it in day_items
    )
    blob = cfg + "\n" + "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _load_cached_day(
    gateway, day_number, day_items, *, phase, phase_states, event_root,
) -> List[CullBucket]:
    """Reconstruct one day's buckets from the cache (no EXIF, no scanner). Status is
    still computed **live** from ``phase_state`` + soft-state — only the structure is
    cached."""
    id_to_item: Dict[str, m.Item] = {it.id: it for it in day_items}
    caches = sorted(gateway.cached_buckets(phase, day_number), key=lambda bc: bc.ordinal)
    buckets: List[CullBucket] = []
    for bc in caches:
        cull_items: list[CullItem] = []
        for bm in gateway.bucket_members(bc.bucket_key, phase):
            it = id_to_item.get(bm.item_id)
            if it is None:
                continue
            cull_items.append(CullItem(
                item_id=it.id, path=event_root / it.origin_relpath, kind=it.kind,
                capture_time_corrected=it.capture_time_corrected or None,
                duration_ms=it.duration_ms,
            ))
        if not cull_items:
            continue
        soft = gateway.bucket(bc.bucket_key, phase)
        status = project_status([ci.item_id for ci in cull_items], phase_states, soft)
        buckets.append(
            CullBucket(
                bucket_key=bc.bucket_key, kind=bc.kind, title=bc.title,
                items=tuple(cull_items), status=status,
                detection_source=bc.detection_source, camera=bc.camera,
                browsed=bool(soft.browsed) if soft is not None else False,
            )
        )
    return buckets


# --------------------------------------------------------------------------- #
# Day Grid layer (spec/32) — flat cell sequence that surfaces under each day,
# built on top of ``pick_days``. Only real clusters (burst / focus_bracket /
# exposure_bracket) become cluster cells; the artificial ``moment`` and
# ``individual`` scanner groupings are flattened to standalone item cells;
# ``video`` buckets become standalone video cells.
# --------------------------------------------------------------------------- #


# Cluster kinds that survive as Day Grid cluster cells (spec/32 §1 +
# spec/52 Quick Sweep slice B — Nelson 2026-06-09 added "repeat" so the
# main Picker matches the Quick Sweep's cluster-aware rendering).
REAL_CLUSTER_KINDS: frozenset = frozenset({
    "burst", "focus_bracket", "exposure_bracket", "repeat",
})

# Scanner kinds that are flattened to individual cells at the Day Grid layer
# (spec/32 §8.2 — the artificial groupings eliminated by the redesign).
_FLATTEN_KINDS: frozenset = frozenset({"moment", "individual", "video_moment"})


@dataclass(frozen=True)
class CullCluster:
    """A real cluster (burst / focus bracket / exposure bracket) surfaced as
    ONE Day Grid cell (spec/32 §2.2 + §3).

    The cluster's status border colour is derived from its members; the
    cluster sub-grid renders the members with per-member colours when the user
    enters it. ``bucket_key`` lets the gateway lookup soft-state
    (reviewed / browsed / current_index / default_state) shared with the
    legacy bucket layer.
    """

    bucket_key: str
    kind: str                       # 'burst' | 'focus_bracket' | 'exposure_bracket'
    title: str
    members: tuple[CullItem, ...]
    color: CellColor                # aggregate border colour
    detection_source: str = ""
    camera: str = ""

    @property
    def member_ids(self) -> tuple[str, ...]:
        return tuple(ci.item_id for ci in self.members)

    @property
    def count(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class CullCell:
    """One Day Grid cell (spec/32 §2.2). A cell is either an item cell
    (``item_id`` set, ``cluster=None``) or a cluster cell (``cluster`` set,
    ``item_id=None``); the discriminator is :pyattr:`is_cluster`.

    ``end_time`` is the chronological sort key (spec/32 §2.3): for photos /
    snapshots it equals ``capture_time_corrected``; for videos / clips it is
    ``capture_time_corrected + duration_ms``; for cluster cells it is the
    ``max(end_time)`` of the members. Empty string sorts first (defensive —
    items with no corrected time appear at the top of the day).

    ``color`` is the border colour the Day Grid widget paints; computed once
    at build time from the live ``phase_state`` snapshot.

    ``visited`` drives the spec/32 §2.10 corner-tick badge: True iff the user
    has previously centre-clicked this cell open at the current phase.  For
    cluster cells it mirrors ``bucket.browsed``; for item cells it mirrors
    ``item_visit.visited``.  Pure display — does not affect status, sort,
    or phase progression.
    """

    end_time: str                       # ISO timestamp; sort key
    color: CellColor                    # border colour
    item_id: Optional[str] = None       # set for item cells
    item_kind: str = "photo"            # 'photo' | 'video'; ignored for cluster cells
    cluster: Optional[CullCluster] = None  # set for cluster cells
    visited: bool = False               # spec/32 §2.10 tick
    # spec/59 §8 Exported watermark — True iff an exported/associated
    # version of this PHOTO exists (edit-phase lineage). Stamped only
    # by Edit-phase projections; Pick callers leave the default.
    exported: bool = False

    @property
    def is_cluster(self) -> bool:
        return self.cluster is not None


def _item_end_time(item: CullItem) -> str:
    """ISO end-time for sorting (spec/32 §2.3). Photo / snapshot end == start
    (instantaneous). Video end = start + ``duration_ms`` when known, else start
    (fallback when duration was never probed). Empty input → empty output."""
    start = item.capture_time_corrected
    if not start:
        return ""
    if item.kind != "video" or not item.duration_ms:
        return start
    from datetime import datetime, timedelta
    try:
        dt = datetime.fromisoformat(start)
        return (dt + timedelta(milliseconds=item.duration_ms)).isoformat()
    except (ValueError, TypeError):
        return start


def _cluster_end_time(members: Sequence[CullItem]) -> str:
    """End-time of the latest-finishing member (spec/32 §2.3)."""
    if not members:
        return ""
    return max(_item_end_time(m) for m in members)


# spec/56 slice 2 retired _video_has_kept_extracts (the spec/32 §2.4
# yellow-border rule): Pick no longer creates clip/snapshot children, so a
# video cell shows its own whole-video P/D state, exactly like a photo.


def video_edit_color(
    gateway, item_id: str, phase_states, default_state: str,
) -> CellColor:
    """spec/59 export-status — a video cell at EDIT aggregates its clips
    + snapshots: green when everything inside is marked for export, red
    when nothing is, yellow for the partial state (the picker's cluster
    grammar — Nelson 2026-06-11). A video the workshop never touched has
    no children yet and reads as the phase default."""
    children: list[str] = []
    try:
        children += [s.item_id for s in gateway.video_segments(item_id)]
        children += [sn.item_id for sn in gateway.video_snapshots(item_id)]
    except Exception:  # noqa: BLE001 — display model: degrade gracefully
        children = []
    if not children:
        return (CellColor.KEPT if default_state == STATE_PICKED
                else CellColor.DISCARDED)
    states = []
    for cid in children:
        ps = phase_states.get(cid)
        st = (ps.state if ps is not None
              and ps.state in (STATE_PICKED, STATE_SKIPPED)
              else default_state)
        states.append(st)
    if all(s == STATE_PICKED for s in states):
        return CellColor.KEPT
    if all(s != STATE_PICKED for s in states):
        return CellColor.DISCARDED
    return CellColor.MIXED


def day_grid_cells(
    gateway,
    day_number: Optional[int],
    *,
    phase: str = "pick",
    source_kind: SourceKind = SourceKind.CAMERA,
    read_exif: Callable[[Sequence[Path]], list] = _default_read_exif,
    scan_fn: Callable = _default_scan,
    config=None,
    camera_id: Optional[str] = None,
    item_ids: Optional[frozenset] = None,
    days: Optional[Sequence["PickDay"]] = None,
    default_state: str = "skipped",
    exported_ids: Optional[set] = None,
) -> List[CullCell]:
    """The flat chronological cell list for one day (spec/32 §2.2).

    Reuses :func:`pick_days` to get (cached) bucket structure, then:

    * ``burst`` / ``focus_bracket`` / ``exposure_bracket`` buckets become
      :class:`CullCluster` cells (one cell per real cluster).
    * ``video`` buckets become individual video cells (each master video is
      one cell showing its own whole-video P/D state — spec/56).
    * ``moment`` / ``individual`` / ``video_moment`` buckets are **flattened**
      to their members as standalone item cells (the artificial groupings the
      redesign eliminates).

    All cells are ordered by end-time ascending (§2.3). Returns an empty list
    when the day has no items.

    **Perf**: ``days`` is an optional pre-built list of :class:`PickDay` (e.g.
    the one PickPage already built in ``open_event``). When supplied, the
    expensive :func:`pick_days` walk is skipped — opening a day becomes a
    pure in-memory projection plus per-video ``children()`` queries (Nelson
    eyeball 2026-06-04 — "days panel takes a lot to open").

    ``exported_ids`` (spec/59 §8, Edit only): the gateway's
    ``exported_item_ids()`` set — PHOTO item cells in it get
    ``exported=True`` (the watermark). ``None`` (every Pick caller, or
    the watermark setting off) stamps nothing.
    """
    if days is None:
        days = pick_days(
            gateway,
            phase=phase,
            source_kind=source_kind,
            read_exif=read_exif,
            scan_fn=scan_fn,
            config=config,
            camera_id=camera_id,
            item_ids=item_ids,
        )
    day = next((d for d in days if d.day_number == day_number), None)
    if day is None:
        return []

    phase_states = gateway.phase_states(phase)

    def _color(item_id: str, item_kind: str):
        """Phase-aware per-item colour resolver. spec/59 export-status
        (Nelson 2026-06-11): Edit cells colour by the edit
        ``phase_state`` — green = marked for export, red = not (the
        old ``Adjustment.edit_exported`` signal moved to the Exported
        watermark); a VIDEO cell aggregates its clips + snapshots with
        the cluster grammar (yellow = partial). Every phase honours its
        ``default_state``."""
        if phase == "edit":
            if item_kind == "video":
                return video_edit_color(
                    gateway, item_id, phase_states, default_state)
            c = cell_color_for_item(
                item_id, item_kind, phase, phase_states,
                default_state=default_state,
            )
            if c not in (CellColor.KEPT, CellColor.DISCARDED):
                # Edit has only the two endpoints — fold strays.
                c = (CellColor.KEPT if default_state == STATE_PICKED
                     else CellColor.DISCARDED)
            return c
        return cell_color_for_item(
            item_id, item_kind, phase, phase_states,
            default_state=default_state,
        )
    # spec/32 §2.10 visited-tick lookup: one batched query per Day Grid open.
    # Cluster ticks already ride on ``CullBucket.browsed`` (carried from the
    # soft-state load); item ticks come from this per-day set.
    try:
        visited_items = gateway.items_visited_for_day(day_number, phase)
    except Exception:  # noqa: BLE001 — display model: degrade gracefully if
        # the table is missing (pre-v4 stragglers should be migrated, but the
        # tick is informational — never crash the Day Grid over it).
        visited_items = set()
    cells: list[CullCell] = []

    for bucket in day.buckets:
        if bucket.kind in REAL_CLUSTER_KINDS:
            member_colors = [
                _color(ci.item_id, ci.kind)
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
            cells.append(CullCell(
                end_time=_cluster_end_time(bucket.items),
                color=color,
                cluster=cluster,
                visited=bucket.browsed,
            ))
        elif bucket.kind == "video":
            # spec/56: a video cell shows its own whole-video P/D state
            # (the yellow kept-extracts override retired with Pick-time
            # clip creation).
            for ci in bucket.items:
                color = _color(ci.item_id, ci.kind)
                cells.append(CullCell(
                    end_time=_item_end_time(ci),
                    color=color,
                    item_id=ci.item_id,
                    item_kind=ci.kind,
                    visited=ci.item_id in visited_items,
                ))
        else:
            # moment / individual / video_moment / unknown → flatten.
            for ci in bucket.items:
                color = _color(ci.item_id, ci.kind)
                cells.append(CullCell(
                    end_time=_item_end_time(ci),
                    color=color,
                    item_id=ci.item_id,
                    item_kind=ci.kind,
                    visited=ci.item_id in visited_items,
                    # spec/89 §4.1 (Nelson 2026-07-02) — videos count
                    # too. A source video whose ONLY ship is a clip
                    # render or a snapshot render still deserves the
                    # "Exported" / "Has file" chip, because at the
                    # keeper-unit level the video IS shipped. Callers
                    # feed a set that already unions parent video ids
                    # via
                    # :meth:`EventGateway.exported_item_ids_with_video_parents`.
                    exported=bool(
                        exported_ids
                        and ci.item_id in exported_ids),
                ))

    # Sort by end_time ASC; tie-break by item_id / bucket_key so the order is
    # stable across rebuilds.
    cells.sort(key=lambda c: (
        c.end_time or "",
        c.item_id or (c.cluster.bucket_key if c.cluster else ""),
    ))
    return cells


def pick_days(
    gateway,
    *,
    phase: str = "pick",
    source_kind: SourceKind = SourceKind.CAMERA,
    read_exif: Callable[[Sequence[Path]], list] = _default_read_exif,
    scan_fn: Callable = _default_scan,
    config=None,
    progress: Optional[Callable[[int, int, Optional[int], int], None]] = None,
    camera_id: Optional[str] = None,
    item_ids: Optional[frozenset] = None,
) -> List[PickDay]:
    """The Day → Bucket → item tree, **cache-backed** (spec/11 §4). Per day: compute the
    clustering fingerprint; on a hit, load the cached structure (no EXIF / no scan); on a
    miss, run the compute path and persist it. Status is always live. This is the entry
    the navigator binds to.

    ``camera_id`` scopes the tree to one camera (the per-camera in-event cull, Nelson
    2026-06-01). Because the persisted cache is keyed ``(phase, day_number)`` over ALL
    cameras, a per-camera build **bypasses the cache** (computes fresh) so it never clobbers
    the all-camera cache — the item set is one camera's, so it's cheap.

    ``item_ids`` restricts the item pool to a pre-determined set (Select uses this to show
    Cull-Kept items only). The fingerprint is keyed by the restricted set, so the
    ``phase="pick"`` cache is independent of the ``phase="pick"`` cache.

    ``progress(done, total, day_number, n_items)`` is called before each day is built (and
    once more with ``done == total`` at the end) so a host can drive a progress dialog —
    the first, uncached open reads EXIF per day and can lag (spec/05 §4b). Pure data: the
    callback gets the day number + item count; the UI formats the (translated) message."""
    if camera_id is not None:
        return _camera_scoped_days(
            gateway, phase=phase, source_kind=source_kind, read_exif=read_exif,
            scan_fn=scan_fn, config=config, progress=progress, camera_id=camera_id)
    event_root = Path(gateway.event_root) if gateway.event_root is not None else Path(".")
    phase_states = gateway.phase_states(phase)
    trip_days = {td.day_number: td for td in gateway.trip_days()}
    eff_config = _resolve_config(config)
    by_day = _captured_by_day(gateway, item_ids=item_ids)

    ordered = sorted(by_day, key=lambda d: (d is None, d))
    total_days = len(ordered)
    days: List[PickDay] = []
    for i, day_number in enumerate(ordered):
        day_items = by_day[day_number]
        if progress is not None:
            progress(i, total_days, day_number, len(day_items))
        fingerprint = _day_fingerprint(day_items, eff_config)
        if gateway.clustering_fingerprint(phase, day_number) == fingerprint:
            buckets = _load_cached_day(
                gateway, day_number, day_items,
                phase=phase, phase_states=phase_states, event_root=event_root,
            )
        else:
            buckets = _compute_day(
                gateway, day_number, day_items, phase=phase,
                source_kind=source_kind, read_exif=read_exif, scan_fn=scan_fn,
                config=eff_config, phase_states=phase_states, event_root=event_root,
            )
            gateway.save_day_cache(
                phase, day_number, fingerprint,
                [
                    {
                        "bucket_key": b.bucket_key, "kind": b.kind, "title": b.title,
                        "detection_source": b.detection_source, "camera": b.camera,
                        "item_ids": list(b.item_ids),
                    }
                    for b in buckets
                ],
            )
        if not buckets:
            continue
        days.append(_assemble_day(day_number, buckets, trip_days))
    if progress is not None:
        progress(total_days, total_days, None, 0)
    return days
