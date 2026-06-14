"""Scan → Day → Bucket model for the resume-map navigator (Stage A.3a).

docs/18 §"Culling contexts": a culling context is scanned into
**Day → Bucket** nodes; the navigator paints each with its
resume-map :class:`~core.cull_stats.CullStats` and, on click, routes
the bucket to its derived cull surface.

Pure / Qt-free. The scanner's EXIF heuristics are injected
(``scan_fn``) so day-grouping, flattening, ids, ordering and the
per-kind default state are unit-testable without driving real
camera metadata. Day grouping is by **EXIF DateTimeOriginal date**
(``PhotoExif.timestamp``) — never mtime (the standing rule);
undated files fall into a trailing "Undated" day.

Bucket → derived surface (Stage A.3c wires it):
focus_bracket / exposure_bracket / individual → IngestCullerPage
(flagged); video → VideoCullPage; burst → IngestCullerPage (normal).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

from core.bucket_scanner import (
    BucketScannerConfig,
    BucketScanResult,
    SourceKind,
    scan,
)
from core.cull_state import STATE_DISCARDED, bucket_content_key
from core.cull_stats import (
    BADGE_BROWSED,
    BADGE_IN_PROGRESS,
    BADGE_UNTOUCHED,
    CullStats,
    compute_cull_stats,
)
from core.exif_reader import PhotoExif
from core.import_pipeline import RawExifEntry
from core.models import TripDay
from core.path_builder import day_folder_name

# Every bucket kind defaults to DISCARDED (the user picks keepers).
# Brackets used to default KEPT (demote-the-bad-frames) — dropped
# 2026-05-18 (Nelson): now there is a one-click **Keep All**, the
# bracket exception no longer earns its keep. Uniform discard-default
# = the whole 3-state model's "nothing is kept until you act"
# (docs/18 §3-state + §Stacks, revised).


@dataclass(frozen=True)
class BucketItem:
    """docs/24 Step 2a (corrected concept, 2026-05-28): one member
    of a mixed-kind bucket.

    Used by Select-mode video_moment buckets where photos
    (snapshot JPEGs) and clips (journal entries referencing a
    source video) live side-by-side and are navigated as a single
    group ordered by their position in the source video's timeline.

    Photo / snapshot items: ``kind == "photo"``, ``path`` points at
    the JPEG file, ``clip_range`` is None. The snapshot's lineage
    id (``s1``, ``s2``, …) is stored in ``lineage_id`` when known.

    Clip items: ``kind == "clip"``, ``path`` points at the source
    video file, ``clip_range`` carries ``(start_ms, end_ms)``,
    ``lineage_id`` is the clip's journal id (``c1``, ``c2``, …).
    """
    path: Path
    kind: str                                    # "photo" | "clip"
    clip_range: Optional[tuple[int, int]] = None
    lineage_id: Optional[str] = None

    @property
    def is_clip(self) -> bool:
        return self.kind == "clip"

    @property
    def is_photo(self) -> bool:
        return self.kind == "photo"


@dataclass(frozen=True)
class BucketNode:
    """One cullable bucket within a day."""

    kind: str                       # focus_bracket|exposure_bracket|
    #                                 burst|individual|video|video_moment
    bucket_id: str                  # stable — keys this bucket's journal
    title: str                      # e.g. "Focus Bracket · 26"
    files: tuple[Path, ...]         # member files, ordered
    default_state: str              # cull-state default for this kind
    # Provenance for the resume-map row (Nelson 2026-05-17 — the
    # prototype surfaced these and they let the user trust/distrust a
    # grouping at a glance). Empty when N/A (individual/video have no
    # detection source; camera "" if unknown).
    detection_source: str = ""      # bursts: sequence_number|time_gap;
    #                                 brackets: exif_tag|inferred_*
    camera: str = ""                # representative Make/Model
    # docs/24 Step 2a (corrected concept, 2026-05-28): mixed-kind
    # bucket members. Populated only for ``kind == "video_moment"``
    # buckets where snapshots (photo files) and clips (source-video
    # path + range) are grouped because they came from the same
    # source video and form one source-timeline moment. Default
    # ``None`` for every other bucket kind, where ``files`` is the
    # only contents shape — homogeneous file tuples like today.
    items: Optional[tuple[BucketItem, ...]] = None

    @property
    def count(self) -> int:
        if self.items is not None:
            return len(self.items)
        return len(self.files)

    @property
    def is_video_moment(self) -> bool:
        """True when this bucket groups mixed-kind derivatives from
        one source video (Select-mode post-pass output). Convenience
        for the navigator + bucket-cull-shell to branch on without
        inspecting both ``kind`` and ``items``."""
        return self.kind == "video_moment" and self.items is not None


@dataclass(frozen=True)
class DayNode:
    key: str                        # ISO date or "undated"
    label: str                      # user-facing (plan desc later)
    buckets: tuple[BucketNode, ...]
    # Scenario/style mix for the Day row, desc by count — only the
    # folder-derived (Home) context can know it cheaply (the prior
    # cull already filed by style subfolder); "" otherwise.
    style_mix: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class DayFolder:
    """A day's folder, listed **cheaply** — folder walk only, NO
    EXIF, NO bucket scan (the frozen lazy-scan design, docs/18
    2026-05-18). The Day list is built from these instantly; the
    Day-row resume is a peek of the day journal over ``files``; the
    expensive per-day :func:`scan_day` runs only on day-open."""

    key: str                        # canonical day-folder name
    label: str                      # user-facing (= key here)
    files: tuple[Path, ...]         # all the day's media, recursive
    style_mix: tuple[tuple[str, int], ...] = ()

    @property
    def filenames(self) -> list[str]:
        return [p.name for p in self.files]


def _default_state(_kind: str) -> str:
    return STATE_DISCARDED          # uniform (see note above)


def _camera_str(raw: dict) -> str:
    """Compact 'Make Model' for the provenance readout (deduped —
    Panasonic writes Make 'Panasonic' + Model 'DC-G9M2'; some bodies
    repeat the make in the model)."""
    make = str(raw.get("Make", "") or "").strip()
    model = str(raw.get("Model", "") or "").strip()
    if make and model and not model.lower().startswith(make.lower()):
        return f"{make} {model}"
    return model or make


def _camera_map(photo_exifs) -> dict:
    return {pe.path: _camera_str(pe.raw or {}) for pe in photo_exifs}


def _day_key(ts) -> tuple[int, str]:
    """Sort/group key. Dated days first (chronological), the
    'undated' bucket always last."""
    if ts is None:
        return (1, "undated")
    d: date = ts.date() if hasattr(ts, "date") else ts
    return (0, d.isoformat())


def _flatten(
    day_key: str,
    res: BucketScanResult,
    camera_for: Optional[Callable[[Path], str]] = None,
) -> list[BucketNode]:
    """BucketScanResult → ordered BucketNodes.

    Buckets carry an *anchor timestamp* (first photo's
    DateTimeOriginal for sequences/clusters, the file's timestamp
    for videos) and the final emission order is **chronological by
    anchor**, not by bucket kind (Nelson 2026-05-23: previously
    bursts/brackets came first, then moment clusters, then a single
    catch-all Individuals bucket at the end — so a morning singleton
    appeared after evening clusters and read as 'out of order').

    Individuals split into TIME-LOCALIZED sub-buckets. Anytime two
    consecutive residual photos are more than ``cluster_window_seconds``
    apart, a new sub-bucket starts. Each sub-bucket then takes its
    chronological position among the cluster / bracket / burst /
    video buckets. Single-photo sub-buckets are allowed — they
    appear at their true position in the day.

    ``camera_for`` (optional) maps a path → its Make/Model for the
    row's provenance readout.
    """
    from core.bucket_scanner import load_bucket_scanner_config
    gap_seconds = float(
        load_bucket_scanner_config().cluster_window_seconds)

    def _cam(files: tuple[Path, ...]) -> str:
        if not files or camera_for is None:
            return ""
        try:
            return camera_for(files[0]) or ""
        except Exception:
            return ""

    # Build (anchor, node) pairs. ``anchor`` is the bucket's first
    # photo timestamp (or the video file's timestamp). ``None``
    # anchors sort to the end so undated content trails the rest.
    pending: list[tuple[Optional[datetime], BucketNode]] = []

    def _first_ts(seq) -> Optional[datetime]:
        """Anchor timestamp for a bracket / burst / cluster — first
        photo's DateTimeOriginal. Falls back to the sequence's
        ``representative_timestamp`` (already populated by the
        scanner) when the per-photo timestamp isn't readable."""
        rep = getattr(seq, "representative_timestamp", None)
        if rep is not None:
            return rep
        return None

    # Sequences (brackets + bursts) — each gets its own bucket.
    def _seq(kind: str, sequences, id_attr: str) -> None:
        for s in sequences:
            sid = getattr(s, id_attr, None) or f"{len(pending)}"
            files = tuple(s.photos)
            node = BucketNode(
                kind=kind,
                bucket_id=f"{day_key}|{kind}|{sid}",
                title=f"{kind.replace('_', ' ').title()} · {len(files)}",
                files=files,
                default_state=_default_state(kind),
                detection_source=str(
                    getattr(s, "detection_source", "") or ""),
                camera=_cam(files),
            )
            pending.append((_first_ts(s), node))

    _seq("focus_bracket", res.focus_brackets, "sequence_id")
    _seq("exposure_bracket", res.exposure_brackets, "sequence_id")
    _seq("burst", res.bursts, "burst_id")

    # Individuals → clusters + time-localized residual sub-buckets.
    if res.individuals:
        # Group by cluster_id (preserving first-occurrence order so
        # the cluster's anchor reflects the earliest photo).
        clusters: dict[str, list] = {}
        cluster_order: list[str] = []
        residual_with_ts: list = []      # IndividualPhoto with ts
        residual_no_ts: list = []        # IndividualPhoto without ts
        for i in res.individuals:
            cid = getattr(i, "cluster_id", None)
            if cid:
                if cid not in clusters:
                    clusters[cid] = []
                    cluster_order.append(cid)
                clusters[cid].append(i)
            else:
                if i.timestamp is not None:
                    residual_with_ts.append(i)
                else:
                    residual_no_ts.append(i)

        for cid in cluster_order:
            members = clusters[cid]
            files = tuple(m.path for m in members)
            ck = bucket_content_key(p.name for p in files)
            anchor = members[0].timestamp if members else None
            node = BucketNode(
                kind="moment",
                bucket_id=f"{day_key}|moment|{ck}",
                title=f"Moment · {len(files)}",
                files=files,
                default_state=_default_state("moment"),
                camera=_cam(files),
            )
            pending.append((anchor, node))

        # Residual sub-bucketing: split where the gap between
        # consecutive timestamps exceeds ``gap_seconds``. Each
        # sub-bucket takes its first photo's timestamp as the
        # anchor so it interleaves chronologically with the rest.
        residual_with_ts.sort(key=lambda i: i.timestamp)
        if residual_with_ts:
            current: list = [residual_with_ts[0]]
            sub_groups: list[list] = []
            for prev, nxt in zip(
                residual_with_ts, residual_with_ts[1:],
            ):
                gap = (
                    nxt.timestamp - prev.timestamp
                ).total_seconds()
                if gap > gap_seconds:
                    sub_groups.append(current)
                    current = [nxt]
                else:
                    current.append(nxt)
            sub_groups.append(current)
            for grp in sub_groups:
                files = tuple(m.path for m in grp)
                ck = bucket_content_key(p.name for p in files)
                node = BucketNode(
                    kind="individual",
                    bucket_id=f"{day_key}|individual|{ck}",
                    title=f"Individual{'s' if len(files) > 1 else ''} · "
                          f"{len(files)}",
                    files=files,
                    default_state=_default_state("individual"),
                    camera=_cam(files),
                )
                pending.append((grp[0].timestamp, node))
        # Photos with no readable timestamp — keep them together in
        # one trailing bucket so the user can still review them.
        if residual_no_ts:
            files = tuple(i.path for i in residual_no_ts)
            ck = bucket_content_key(p.name for p in files)
            node = BucketNode(
                kind="individual",
                bucket_id=f"{day_key}|individual|notime|{ck}",
                title=f"Individuals (no timestamp) · {len(files)}",
                files=files,
                default_state=_default_state("individual"),
                camera=_cam(files),
            )
            pending.append((None, node))

    # One bucket PER video clip — same chronological interleaving.
    # Stem-keyed id (stable across re-scans); dedupe collisions.
    vids: list[tuple[Optional[datetime], Path]] = []
    for v in res.videos:
        vids.append((v.timestamp, v.path))
    for m in res.motion_clips:
        ts = getattr(m, "timestamp", None)
        vids.append((ts, m.path))
    seen: dict[str, int] = {}
    for ts, p in vids:
        base = p.stem or p.name
        n = seen.get(base, 0)
        seen[base] = n + 1
        sid = base if n == 0 else f"{base}-{n}"
        node = BucketNode(
            kind="video",
            bucket_id=f"{day_key}|video|{sid}",
            title=f"Video · {p.name}",
            files=(p,),
            default_state=_default_state("video"),
            camera=_cam((p,)),
        )
        pending.append((ts, node))

    # Chronological emit. Dated buckets first (in timestamp order);
    # undated buckets (anchor=None) trail at the end in insertion
    # order — same place the legacy code would have put them, but
    # now grouped together rather than scattered.
    pending.sort(
        key=lambda pair: (pair[0] is None, pair[0] or 0),
    )
    return [node for _ts, node in pending]


def day_style_mix(
    photo_exifs: Sequence[PhotoExif],
) -> tuple[tuple[str, int], ...]:
    """Real scenario mix for a day's photos, ``(style, count)``
    most-common first (inc.2b, frozen 2026-05-18). Classification is
    the spine of the culler — the lazy folder-only Day list couldn't
    show styles for a fresh card; the *expensive* per-day scan
    already has the EXIF in hand, so classify here and roll it up.
    Uses the shared ``core.genre.classify_exif`` (same rules as the
    canvas). Never raises (classify_exif degrades to GENERAL)."""
    from collections import Counter as _Counter

    from core.genre import classify_exif

    ct: _Counter = _Counter()
    for pe in photo_exifs:
        try:
            sc = classify_exif(
                pe.path, pe.raw or {},
            ).scenario.value
        except Exception:  # noqa: BLE001 — never break the scan
            sc = ""
        if sc:
            ct[sc] += 1
    return tuple(ct.most_common())


def build_days(
    photo_exifs: Sequence[PhotoExif],
    source_kind: SourceKind,
    config: Optional[BucketScannerConfig] = None,
    *,
    scan_fn: Callable[..., BucketScanResult] = scan,
) -> list[DayNode]:
    """Group by EXIF day, scan each day, flatten to nodes. Pure —
    ``scan_fn`` is injected so the grouping/flatten logic is
    testable without real camera metadata. Each ``DayNode`` carries
    its real classified ``style_mix`` (inc.2b) so the Day rows show
    styles for a fresh card too, not just the folder-derived Home
    context."""
    by_day: dict[tuple[int, str], list[PhotoExif]] = {}
    for pe in photo_exifs:
        by_day.setdefault(_day_key(pe.timestamp), []).append(pe)

    days: list[DayNode] = []
    for sort_key in sorted(by_day):
        _rank, key = sort_key
        group = by_day[sort_key]
        entries = [
            RawExifEntry(path=pe.path, exif=(pe.raw or {}))
            for pe in group
        ]
        res = scan_fn(entries, source_kind, config)
        cam = _camera_map(group)
        buckets = _flatten(key, res, cam.get)
        if not buckets:
            continue
        label = "Undated" if key == "undated" else key
        days.append(DayNode(key=key, label=label,
                             buckets=tuple(buckets),
                             style_mix=day_style_mix(group)))
    return days


def scan_tree(
    root: Path,
    source_kind: SourceKind,
    config: Optional[BucketScannerConfig] = None,
) -> list[DayNode]:
    """Disk-facing convenience: walk photos + videos under ``root``,
    read EXIF, and :func:`build_days`. Thin (the heuristics live in
    the unit-tested ``build_days`` + the scanner) — exercised via
    the navigator harness."""
    from core.exif_reader import read_exif_batch
    from core.folder_scanner import walk_photo_paths
    from core.video_discovery import VIDEO_EXTENSIONS

    root = Path(root)
    try:
        photos = list(walk_photo_paths(root))
    except (FileNotFoundError, NotADirectoryError):
        photos = []
    vids = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ] if root.exists() else []
    files = photos + vids
    if not files:
        return []
    return build_days(read_exif_batch(files), source_kind, config)


def list_event_day_folders(
    root: Path,
    trip_days: Sequence[TripDay],
) -> list[DayFolder]:
    """**Cheap** folder-derived day list — the lazy-scan front door
    (docs/18 frozen 2026-05-18). Days + labels come from the EVENT
    PLAN's canonical ``day_folder_name`` (NOT raw EXIF — the "wrong
    days" fix). For each ``TripDay`` whose folder exists, gather its
    media filenames *recursively* + the style mix. **No
    ``read_exif_batch``, no bucket scan** — this is what makes
    context entry instant and crash-free; the heavy work is
    :func:`scan_day`, lazy on day-open."""
    from collections import Counter as _Counter
    from core.folder_scanner import PHOTO_EXTENSIONS
    from core.video_discovery import VIDEO_EXTENSIONS

    root = Path(root)
    keep = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
    out: list[DayFolder] = []
    for d in sorted(trip_days, key=lambda x: x.day_number):
        folder = day_folder_name(d)
        day_dir = root / folder
        if not day_dir.is_dir():
            continue
        files = sorted(
            p for p in day_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in keep
        )
        if not files:
            continue
        style_ct: _Counter = _Counter()
        for p in files:
            parts = p.relative_to(day_dir).parts
            if len(parts) > 1:                  # immediate sub-folder
                style_ct[parts[0]] += 1
        out.append(DayFolder(
            key=folder, label=folder,
            files=tuple(files),
            style_mix=tuple(style_ct.most_common()),
        ))
    return out


def list_culled_day_folders(
    culled_root: Path,
    trip_days: Sequence[TripDay],
) -> list[DayFolder]:
    """Cheap folder-derived day list **specifically for the
    Select-phase source layout** (Nelson 2026-05-20 v4):
    ``01 - Culled/<bucket>/<day>/<camera>/<style>/<file>``.

    Walks the per-bucket layout that Cull-Export writes and
    aggregates files per trip day across all buckets and cameras.
    The Style mix is computed from the path segment under each
    camera (parts[2] from the day-dir's perspective:
    ``<camera>/<style>/<file>``).

    Same contract as :func:`list_event_day_folders`: folder names
    drive day assignment (no EXIF read), so context entry stays
    instant. Returns a ``DayFolder`` only when that day has at
    least one file under any bucket / camera."""
    from collections import Counter as _Counter
    from core.folder_scanner import PHOTO_EXTENSIONS
    from core.path_builder import (
        CAPTURED_CAMERAS_SUBDIR,
        CAPTURED_OTHER_SUBDIR,
        CAPTURED_PHONES_SUBDIR,
    )
    from core.video_discovery import VIDEO_EXTENSIONS

    culled_root = Path(culled_root)
    keep = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
    buckets = (
        CAPTURED_CAMERAS_SUBDIR,
        CAPTURED_PHONES_SUBDIR,
        CAPTURED_OTHER_SUBDIR,
    )
    out: list[DayFolder] = []
    for d in sorted(trip_days, key=lambda x: x.day_number):
        folder = day_folder_name(d)
        all_files: list[Path] = []
        style_ct: _Counter = _Counter()
        for bucket in buckets:
            day_dir = culled_root / bucket / folder
            if not day_dir.is_dir():
                continue
            for p in day_dir.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in keep:
                    continue
                all_files.append(p)
                # parts under day_dir: <camera>/<style>/[<bracket>/]<file>
                parts = p.relative_to(day_dir).parts
                if len(parts) > 2:
                    # parts[1] is the Style (under camera).
                    style_ct[parts[1]] += 1
        if not all_files:
            continue
        all_files.sort()
        out.append(DayFolder(
            key=folder, label=folder,
            files=tuple(all_files),
            style_mix=tuple(style_ct.most_common()),
        ))
    return out


def scan_day(
    df: DayFolder,
    source_kind: SourceKind,
    config: Optional[BucketScannerConfig] = None,
    *,
    scan_fn: Callable[..., BucketScanResult] = scan,
) -> tuple[tuple[BucketNode, ...], tuple[tuple[str, int], ...]]:
    """The **expensive** per-day half — `read_exif_batch` over ONE
    day's files + the bucket scan + flatten. Runs **lazily on
    day-open** (off-thread + cached). Bounded to a single day's
    files, so it no longer carries the bulk-scan SIGSEGV. ``scan_fn``
    injected for deterministic tests.

    Returns ``(buckets, style_mix)`` — the EXIF is already in hand
    here, so classify the day and roll the real scenario mix up too
    (inc.2b): the lazy folder-only Day list couldn't show styles for
    a fresh card; now it can, **once the day has been opened**
    (classifying a never-opened day up-front is the very bulk-scan
    the lazy design exists to avoid — frozen)."""
    from core.exif_reader import read_exif_batch

    exifs = read_exif_batch(list(df.files))
    entries = [
        RawExifEntry(path=pe.path, exif=(pe.raw or {}))
        for pe in exifs
    ]
    res = scan_fn(entries, source_kind, config)
    buckets = tuple(_flatten(df.key, res, _camera_map(exifs).get))
    return buckets, day_style_mix(exifs)


def scan_event_day_folders(
    root: Path,
    trip_days: Sequence[TripDay],
    source_kind: SourceKind,
    config: Optional[BucketScannerConfig] = None,
    *,
    scan_fn: Callable[..., BucketScanResult] = scan,
) -> list[DayNode]:
    """EAGER compose (back-compat): list every day folder then scan
    each. The navigator now uses the lazy pair
    (:func:`list_event_day_folders` + :func:`scan_day`) instead;
    this thin wrapper keeps existing callers/tests working."""
    days: list[DayNode] = []
    for df in list_event_day_folders(root, trip_days):
        buckets, mix = scan_day(
            df, source_kind, config, scan_fn=scan_fn)
        if not buckets:
            continue
        # Folder-derived style (Home) wins — it is authoritative and
        # cheap; the classified mix only FILLS a fresh card whose
        # folder pass found no style sub-dirs.
        days.append(DayNode(
            key=df.key, label=df.label, buckets=buckets,
            style_mix=df.style_mix or mix))
    return days


def _video_cull_stats(vs) -> CullStats:
    """Map a ``VideoResumeStats`` peek → CullStats so a video bucket
    paints the SAME cull-palette bar as photos (Nelson 2026-05-18):
    red = whole duration discarded by default, green = Σ kept-span
    time; **no orange** (video is 2-state, no Compare). Badge ladder:
    never-opened → untouched; opened, nothing extracted → browsed;
    any clip/still → in-progress (video has no user-declared 'done'
    yet — a later refinement)."""
    if vs is None or not getattr(vs, "has_entry", False):
        return CullStats(total=1, kept=0, candidate=0, discarded=1,
                          reviewed=False, badge=BADGE_UNTOUCHED)
    dur = max(1, int(getattr(vs, "duration_ms", 0)))
    kept = max(0, min(int(getattr(vs, "kept_ms", 0)), dur))
    extracted = (int(getattr(vs, "clips", 0))
                 + int(getattr(vs, "stills", 0))) > 0
    badge = BADGE_IN_PROGRESS if extracted else BADGE_BROWSED
    return CullStats(
        total=dur, kept=kept, candidate=0, discarded=dur - kept,
        reviewed=False, badge=badge, browsed=not extracted,
    )


def bucket_stats(node: BucketNode, journal: dict) -> CullStats:
    """Resume-map stats for ``node`` from its ``journal``. Honours
    the bucket's default state for a never-opened bucket (a fresh
    focus bracket reads all-kept) without mutating the journal. A
    video bucket maps its VideoSession peek (kept-time) instead.

    ``individual`` + ``moment`` buckets are VIEWS over a SHARED day
    journal — their soft-state is per-bucket, keyed by the
    content-stable bucket key, and their marks are scoped to their
    own files (frozen 2026-05-18). Bracket/burst use their own
    journal → global soft-state (``bucket_key=None``)."""
    if node.kind == "video":
        return _video_cull_stats(journal.get("_video_stats"))
    if "default_state" in journal:
        j = journal
    else:
        j = {**journal, "default_state": node.default_state}
    names = [p.name for p in node.files]
    key = (bucket_content_key(names)
           if node.kind in ("individual", "moment") else None)
    return compute_cull_stats(j, names, bucket_key=key)
