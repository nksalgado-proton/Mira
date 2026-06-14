"""Cull-phase landing dashboard engine (task #104, Nelson 2026-05-23).

The single source-of-truth for what the Cull dashboard renders:
  * walks ``00 - Captured/`` to find every camera_id and its files
  * reads each camera's journal to compute cull status
  * exposes "Keep all" — stamp every file KEPT and run the same
    Cull-Export the manual button used to drive

Replaces the cameras / phones / other 3-way chooser
(``ui/culler/cull_context_chooser.py``) with a single flat per-camera
view. The on-disk bucket subdirs (_cameras / _phones / _other) stay
as internal organisation — the scanner still uses them to apply
phone-vs-camera heuristics — but the user never sees a chooser.

The "cull is obligatory but Keep-all is one click" contract lives
here: every camera must reach a Done state (either ``done_kept_all``
or ``done_partial``) before Select can read ``01 - Culled/``.

Qt-free; UI consumes via ``ui/pages/cull_dashboard_page.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.models import Event
from core.path_builder import (
    CAPTURED_CAMERAS_SUBDIR,
    CAPTURED_OTHER_SUBDIR,
    CAPTURED_PHONES_SUBDIR,
    captured_dir,
    event_root_path,
)

log = logging.getLogger(__name__)


# Filename-safe sanitiser shared with the cull shell's journal-id
# escaping. Keep the rule in sync — the dashboard's journal_root for
# a camera must match what the shell would look up.
_SAFE = re.compile(r"[^A-Za-z0-9_.+-]")


# Status enum (plain strings — JSON-friendly, render-side does the
# label).
STATUS_NOT_DONE = "not_done"                # journal empty / no marks
STATUS_IN_PROGRESS = "in_progress"          # some files marked, some not
STATUS_DONE_KEPT_ALL = "done_kept_all"      # user clicked Keep all
STATUS_DONE_PARTIAL = "done_partial"        # user culled, kept some, not all
STATUS_DONE_DISCARD_ALL = "done_discard_all"
# ^ user clicked Discard all (F-032 batch-ops, Nelson 2026-05-27).
# Parallel to STATUS_DONE_KEPT_ALL: the dashboard reads a
# `_discard_all` sentinel marker file under the camera's journal
# root and reports this status without re-counting. Visually it
# renders as 100% discarded in the StatusBreakdown.


@dataclass(frozen=True)
class DayBreakdown:
    """Per-day completion for one camera (F-023, 2026-05-25).

    The dashboard renders these as a row of small pills under the
    camera's status text — one pill per day, color-coded by completion.
    ``kept`` counts files in ``kept`` or ``candidate`` state (the
    Cull-Export bridge treats ``candidate`` as kept until Select
    refines it).
    """

    day_label: str
    total: int
    kept: int = 0
    decided: int = 0       # files with an explicit cull mark

    @property
    def is_done(self) -> bool:
        """Every file in the day has an explicit cull mark."""
        return self.total > 0 and self.decided >= self.total

    @property
    def pct_decided(self) -> float:
        return self.decided / self.total if self.total else 0.0


@dataclass(frozen=True)
class CameraRow:
    """One row of the dashboard. Files are split by the on-disk
    bucket so the UI can render a tooltip ('200 from card +
    50 from phones bucket') if it ever wants to — by default only
    the total file_count is shown.

    ``bucket`` is the dominant on-disk bucket for this camera
    (_cameras / _phones / _other) — needed when routing Cull into
    the shell because Cull-Export keys its destination by bucket.
    When a camera has files in multiple buckets (rare; usually a
    mis-classified ingest), the dominant one wins.

    ``days`` (F-023, 2026-05-25): per-day breakdown so the dashboard
    can render a pill row showing which days the user has finished
    vs which still need attention. Sorted by day_label."""

    camera_id: str
    bucket: str
    file_count: int
    files: tuple[Path, ...]
    status: str
    kept_count: int = 0       # only meaningful for done_partial
    total_decided: int = 0    # files with an explicit cull mark
    days: tuple[DayBreakdown, ...] = ()


# ── Discovery ─────────────────────────────────────────────────────


def discover_cameras(event: Event) -> list[CameraRow]:
    """Walk ``00 - Captured/`` and return one :class:`CameraRow` per
    distinct ``camera_id`` (across all three bucket subdirs and all
    day folders).

    Rows are sorted by descending file count then by camera_id so
    the camera the user shoots the most lands first.

    Returns an empty list when 00 - Captured doesn't exist (event
    hasn't been Captured yet)."""
    base = (event.photos_base_path or "").strip()
    if not base:
        return []
    cap = captured_dir(event_root_path(base, event))
    if not cap.is_dir():
        return []

    # camera_id → bucket → list[Path]
    by_cam: dict[str, dict[str, list[Path]]] = {}
    for bucket in (
        CAPTURED_CAMERAS_SUBDIR,
        CAPTURED_PHONES_SUBDIR,
        CAPTURED_OTHER_SUBDIR,
    ):
        bucket_dir = cap / bucket
        if not bucket_dir.is_dir():
            continue
        for day_dir in bucket_dir.iterdir():
            if not day_dir.is_dir():
                continue
            for cam_dir in day_dir.iterdir():
                if not cam_dir.is_dir():
                    continue
                cam_id = cam_dir.name
                files = [
                    p for p in cam_dir.rglob("*") if p.is_file()
                ]
                if not files:
                    continue
                by_cam.setdefault(cam_id, {}).setdefault(
                    bucket, []).extend(files)

    rows: list[CameraRow] = []
    for cam_id, bucket_map in by_cam.items():
        # Pick the dominant bucket (most files). Ties broken by
        # the canonical order cameras > phones > other so a
        # genuinely-camera body that happened to land a stray
        # phone-bucket file still routes through Cull-Export's
        # camera path.
        ordered_buckets = sorted(
            bucket_map.items(),
            key=lambda kv: (
                -len(kv[1]),
                _bucket_order(kv[0]),
            ),
        )
        dominant_bucket = ordered_buckets[0][0]
        all_files = [
            p for files in bucket_map.values() for p in files
        ]
        all_files.sort()
        status, kept, decided = _compute_status(
            event, cam_id, all_files)
        days = _compute_day_breakdown(event, cam_id, all_files)
        rows.append(CameraRow(
            camera_id=cam_id,
            bucket=dominant_bucket,
            file_count=len(all_files),
            files=tuple(all_files),
            status=status,
            kept_count=kept,
            total_decided=decided,
            days=days,
        ))
    rows.sort(key=lambda r: (-r.file_count, r.camera_id))
    return rows


def _bucket_order(bucket: str) -> int:
    """Tie-break key — cameras > phones > other."""
    return {
        CAPTURED_CAMERAS_SUBDIR: 0,
        CAPTURED_PHONES_SUBDIR: 1,
        CAPTURED_OTHER_SUBDIR: 2,
    }.get(bucket, 9)


# ── Per-camera state ──────────────────────────────────────────────


def journal_root_for_camera(event: Event, camera_id: str) -> Path:
    """The per-camera Cull journal scope. Sanitised camera_id so
    weird characters (e.g. `DC-G9M2 #0156`) don't break the path."""
    base = (event.photos_base_path or "").strip()
    root = event_root_path(base, event) if base else Path()
    safe = _SAFE.sub("_", camera_id) or "unknown"
    return root / ".cull" / safe


def keep_all_marker_path(event: Event, camera_id: str) -> Path:
    """Sentinel file the dashboard writes when the user picks
    "Keep all" for a camera. Lets the status read distinguish
    Done (Keep all) from Done (user actually culled and kept
    everything via the culler) — both visually similar, but the
    Reopen action behaves differently."""
    return journal_root_for_camera(event, camera_id) / "_keep_all"


def discard_all_marker_path(event: Event, camera_id: str) -> Path:
    """Sentinel file the dashboard writes when the user picks
    "Discard all" for a camera (F-032 batch-ops, Nelson
    2026-05-27). Parallel to :func:`keep_all_marker_path`.

    Discard all is the "skip this camera entirely" path — useful
    when the user did a thorough Capture-phase Fast Cull and
    doesn't want any of this camera's content to flow into
    01-Culled. The next silent-sync run sees no STATE_KEPT
    decisions for the camera + the orphan-removal pass takes
    care of any pre-existing 01-Culled hardlinks."""
    return journal_root_for_camera(event, camera_id) / "_discard_all"


def _compute_status(
    event: Event, camera_id: str, files: list[Path],
) -> tuple[str, int, int]:
    """``(status, kept_count, total_decided)`` for the camera.

    Walks every journal under the camera's journal_root and folds
    the per-file state in. Returns:
      * ``STATUS_DONE_KEPT_ALL`` when the keep-all marker file exists
      * ``STATUS_DONE_DISCARD_ALL`` when the discard-all marker file
        exists (F-032, Nelson 2026-05-27)
      * ``STATUS_DONE_PARTIAL`` when every file has an explicit mark
        (kept/discarded/candidate) recorded
      * ``STATUS_IN_PROGRESS`` when some files have marks but not all
      * ``STATUS_NOT_DONE`` when no marks exist or no journal yet

    Both markers can't legitimately coexist (the engine helpers
    delete one before writing the other). If they do — most likely
    a hand-edited filesystem — keep-all wins; the other marker is
    ignored on read until the user takes a fresh decision.
    """
    if not files:
        return STATUS_NOT_DONE, 0, 0

    # Marker-file fast path: Keep-all wins over journal counts.
    if keep_all_marker_path(event, camera_id).exists():
        return STATUS_DONE_KEPT_ALL, len(files), len(files)
    if discard_all_marker_path(event, camera_id).exists():
        # All files decided, none kept.
        return STATUS_DONE_DISCARD_ALL, 0, len(files)

    decisions = _load_camera_decisions(event, camera_id)
    if not decisions:
        return STATUS_NOT_DONE, 0, 0

    file_names = {p.name for p in files}
    decided = {
        name: state for name, state in decisions.items()
        if name in file_names
    }
    decided_count = len(decided)
    kept = sum(
        1 for s in decided.values() if s in ("kept", "candidate")
    )
    if decided_count == 0:
        return STATUS_NOT_DONE, 0, 0
    if decided_count >= len(files):
        return STATUS_DONE_PARTIAL, kept, decided_count
    return STATUS_IN_PROGRESS, kept, decided_count


def _compute_day_breakdown(
    event: Event, camera_id: str, files: list[Path],
) -> tuple[DayBreakdown, ...]:
    """Per-day breakdown for ``camera_id`` (F-023, 2026-05-25).

    The day_label is the *parent of the parent* of the file path —
    the canonical layout under 00 - Captured is
    ``<bucket>/<Dia N - desc>/<camera_id>/<file>``. Files outside
    that shape (e.g. quarantined files with no EXIF day) are
    grouped under the literal label ``(no day)`` so they still get
    counted; they typically won't have journals either, so they'll
    surface as "not done" — useful signal for the user.

    When Keep-all is set, every day is treated as 100% done — the
    marker file overrides per-day journal state by design (the
    same fast-path the camera-level status read uses).
    """
    if not files:
        return ()

    # Group files by day_label
    days: dict[str, list[Path]] = {}
    for fp in files:
        try:
            day = fp.parent.parent.name or "(no day)"
        except IndexError:
            day = "(no day)"
        days.setdefault(day, []).append(fp)

    decisions = _load_camera_decisions(event, camera_id)
    keep_all = keep_all_marker_path(event, camera_id).exists()

    out: list[DayBreakdown] = []
    for day_label in sorted(days.keys()):
        day_files = days[day_label]
        total = len(day_files)
        if keep_all:
            out.append(DayBreakdown(
                day_label=day_label,
                total=total,
                kept=total,
                decided=total,
            ))
            continue
        decided = 0
        kept = 0
        for fp in day_files:
            state = decisions.get(fp.name)
            if state in ("kept", "discarded", "candidate"):
                decided += 1
                if state in ("kept", "candidate"):
                    kept += 1
        out.append(DayBreakdown(
            day_label=day_label,
            total=total,
            kept=kept,
            decided=decided,
        ))
    return tuple(out)


def _load_camera_decisions(
    event: Event, camera_id: str,
) -> dict[str, str]:
    """Per-filename cull state across every journal under
    ``journal_root_for_camera(event, camera_id)``.

    The journals are written by the existing
    :mod:`core.ingest_journal` machinery; we sidestep its
    bucket-aware loader and just walk every journal JSON we find.
    Filenames are unique within a camera's day-folder set so a
    simple flat map is enough for the dashboard's read-only
    counting purpose.
    """
    import json

    root = journal_root_for_camera(event, camera_id)
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for journal_file in root.rglob("ingest_journal.json"):
        try:
            with open(journal_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        marks = data.get("marks") or data.get("states") or {}
        if isinstance(marks, dict):
            out.update(
                {str(k): str(v) for k, v in marks.items()}
            )
    return out


def _apply_video_state_to_camera(
    event: Event, camera_id: str, target_state: str,
) -> tuple[int, list[tuple[Path, str]]]:
    """Bulk-flip every video-derivative state under ``camera_id``'s
    per-bucket journals to ``target_state``. Returns
    ``(journals_touched, errors)`` where ``errors`` is a list of
    ``(journal_file, message)`` tuples.

    F-032 Spec A (Nelson 2026-05-27, frozen in docs/18 §"Video
    batch ops"). Walks every ``ingest_journal.json`` under the
    camera's ``journal_root`` and, for each:

      * Flips every clip ``state`` field via
        :func:`core.video_marks.apply_state_to_all_derivatives`.
      * Flips every snapshot ``state`` field the same way.
      * Flips every entry in ``marks`` whose filename has a video
        extension to ``target_state`` (so the whole-video binary
        K/D — which the 2026-05-23 freeze put back into the photo
        ``marks`` map — also matches the batch intent).

    **Photo marks are deliberately untouched.** The marker-file
    model overrides display while preserving the user's fine-
    grained photo K/D for a future Reopen. Only video data
    flips, matching the user's spec ("Keep all respects clips
    and snapshots and sets their state to Keep").

    Pure I/O — never raises; per-journal load/save errors are
    swallowed into the errors list.
    """
    from core import video_marks
    from core.ingest_session import (
        load_ingest_journal,
        save_ingest_journal,
    )
    from core.video_discovery import VIDEO_EXTENSIONS

    if target_state not in ("kept", "discarded"):
        raise ValueError(
            f"unknown target_state: {target_state!r}")

    root = journal_root_for_camera(event, camera_id)
    if not root.is_dir():
        return 0, []

    touched = 0
    errors: list[tuple[Path, str]] = []
    for journal_file in root.rglob("ingest_journal.json"):
        bucket_root = journal_file.parent
        try:
            data = load_ingest_journal(bucket_root)
        except Exception as exc:                         # noqa: BLE001
            errors.append((journal_file, f"load failed: {exc}"))
            continue

        mutated = video_marks.apply_state_to_all_derivatives(
            data, target_state)

        # Whole-video marks live in the same `marks` map as photos
        # (2026-05-23 binary K/D freeze). Flip only those entries
        # whose filename has a video extension; photo marks stay
        # untouched (the marker-file model is the user-facing
        # source of truth for photos).
        marks = data.get("marks")
        if isinstance(marks, dict):
            for name in list(marks.keys()):
                dot = name.rfind(".")
                if dot < 0:
                    continue
                if name[dot:].lower() not in VIDEO_EXTENSIONS:
                    continue
                if marks.get(name) != target_state:
                    marks[name] = target_state
                    mutated += 1

        if mutated == 0:
            # Nothing changed — skip the write to avoid bumping
            # the journal's history rotation for a no-op.
            continue

        try:
            save_ingest_journal(bucket_root, data)
            touched += 1
        except Exception as exc:                         # noqa: BLE001
            errors.append((journal_file, f"save failed: {exc}"))
    return touched, errors


# ── Actions ──────────────────────────────────────────────────────


def keep_all(event: Event, camera_id: str) -> tuple[int, list[str]]:
    """Mark every file for ``camera_id`` as KEPT and materialise the
    hardlink projection into ``01 - Culled/``. Returns
    ``(kept_count, error_messages)``.

    Mechanism:
      1. Write the keep_all marker file so subsequent status reads
         see Done (Keep all) without re-counting.
      2. Build a :class:`core.cull_export_resolver.KeptItem` per file
         (bucket + camera_id from the discovered row, day_label
         derived from the enclosing ``Dia N - desc`` parent folder,
         style defaulted to ``general`` because Keep-all explicitly
         declines per-file classification).
      3. Call :func:`core.cull_export_run.run_export` with
         ``dest_root = 01 - Culled/`` and ``allow_hardlinks=True``
         — Model 3 v2 says cull keepers are byte-identical
         hardlinks to upstream (no EXIF rewrite at this step).

    The same code path the future [#3] silent-sync reconciler will
    use for journal-marked KEPT files; Keep-all just skips the
    "ask the user" step and stamps everything KEPT at once.
    """
    from core.cull_export import CollisionPolicy
    from core.cull_export_resolver import KeptItem
    from core.cull_export_run import run_export
    from core.path_builder import culled_dir, event_root_path

    rows = discover_cameras(event)
    row = next((r for r in rows if r.camera_id == camera_id), None)
    if row is None or not row.files:
        return 0, [f"camera {camera_id!r} not found in 00 - Captured"]

    errors: list[str] = []
    # 0. F-032 Spec A (Nelson 2026-05-27): flip every video
    # derivative (clip + snapshot states + video-extension marks)
    # to KEPT so the post-Reopen silent-sync still hardlinks the
    # raw video forward. Photo marks untouched — preserved for
    # the marker-file override model. Best-effort; non-fatal.
    try:
        _, vid_errors = _apply_video_state_to_camera(
            event, camera_id, "kept")
        for path, msg in vid_errors:
            errors.append(
                f"keep-all video-state on {Path(str(path)).name}:"
                f" {msg}"
            )
    except Exception as exc:                              # noqa: BLE001
        errors.append(f"keep-all video-state flip failed: {exc}")

    # 1. Marker file — survives even if the hardlink export errors
    # (so the user doesn't have to re-confirm Keep-all to retry).
    try:
        marker = keep_all_marker_path(event, camera_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "User chose 'Keep all' for this camera.\n",
            encoding="utf-8",
        )
    except OSError as exc:
        errors.append(f"keep-all marker write failed: {exc}")
        return 0, errors

    # 2. Build KeptItems. day_label comes from each file's parent's
    # parent — the canonical layout is
    # ``00 - Captured/<bucket>/Dia N - desc/<camera_id>/<file>``.
    # Files outside that shape (e.g., ``_no_timestamp`` quarantine)
    # are skipped with a warning — they're not eligible for the
    # culled tree until the user fixes their timestamps.
    items: list[KeptItem] = []
    for fp in row.files:
        try:
            day_label = fp.parent.parent.name
        except IndexError:
            continue
        if not day_label or not day_label.lower().startswith("dia"):
            errors.append(
                f"skipped (not under a Dia folder): {fp.name}"
            )
            continue
        items.append(KeptItem(
            src=fp,
            capture_dt=None,
            day_label=day_label,
            style="general",          # Keep-all bypasses classification
            bracket_id=None,
            exif_datetime=None,       # no retime — hardlink-friendly
            bucket=row.bucket,
            camera_id=row.camera_id,
        ))

    # 3. Run the export. Hardlinks where the filesystem supports
    # them; the engine falls back to copy on cross-volume targets.
    base = (event.photos_base_path or "").strip()
    if not base:
        errors.append("event has no photos_base_path set")
        return 0, errors
    dest_root = culled_dir(event_root_path(base, event))
    dest_root.mkdir(parents=True, exist_ok=True)

    try:
        result = run_export(
            items,
            dest_root,
            collision=CollisionPolicy.UNIQUE,
            allow_hardlinks=True,
        )
    except Exception as exc:        # noqa: BLE001 — defensive
        errors.append(f"hardlink export failed: {exc}")
        return 0, errors

    if result.errors:
        for src, msg in result.errors:
            errors.append(f"export error on {Path(src).name}: {msg}")

    log.info(
        "Keep-all done: camera=%s files=%d ok=%d retimed=%d "
        "skipped=%d errors=%d",
        camera_id, len(row.files),
        result.ok_count,
        len(result.retimed),
        len(result.skipped),
        len(errors),
    )
    return result.ok_count, errors


def reopen(event: Event, camera_id: str) -> None:
    """Undo a Keep-all decision so the dashboard row flips back to
    Not done / In progress (whatever the journal independently
    says). Deletes the marker file only — the journal is
    untouched.

    F-032 (Nelson 2026-05-27): also clears the discard-all marker
    if it happens to be set (e.g. user clicked Discard-all then
    Reopen). Both markers are mutually exclusive in normal flow
    — the helpers below delete the other when writing one — but
    Reopen wipes both as a defence against any drift."""
    for marker in (
        keep_all_marker_path(event, camera_id),
        discard_all_marker_path(event, camera_id),
    ):
        if marker.exists():
            try:
                marker.unlink()
            except OSError as exc:
                log.warning(
                    "reopen: couldn't delete marker %s: %s",
                    marker, exc,
                )


def discard_all(
    event: Event, camera_id: str,
) -> tuple[int, list[str]]:
    """Mark every file for ``camera_id`` as DISCARDED — the
    "skip this camera entirely" path. Returns
    ``(discarded_count, error_messages)``.

    F-032 (Nelson 2026-05-27): parallel to :func:`keep_all` but
    inverted. Use case: the user did a thorough Capture-phase
    Fast Cull and doesn't want any of this camera's content to
    flow into 01-Culled.

    Mechanism:
      1. Delete any prior keep_all marker (mutually exclusive).
      2. Write the discard_all marker file so the status read
         reports STATUS_DONE_DISCARD_ALL without re-counting.
      3. Run :func:`core.cull_phase_sync.sync_camera_kept_to_culled`
         so the orphan-removal pass unlinks any pre-existing
         01-Culled hardlinks for this camera (the additive pass
         is a no-op because the merged decisions dict will carry
         no STATE_KEPT entries).

    The journal contents (if any) stay on disk — Reopen restores
    them. Reset all is the destructive sibling that also wipes
    journals."""
    from core.cull_phase_sync import sync_camera_kept_to_culled

    rows = discover_cameras(event)
    row = next((r for r in rows if r.camera_id == camera_id), None)
    if row is None or not row.files:
        return 0, [f"camera {camera_id!r} not found in 00 - Captured"]

    errors: list[str] = []
    # 0. F-032 Spec A (Nelson 2026-05-27): flip every video
    # derivative (clip + snapshot states + video-extension marks)
    # to DISCARDED so the post-Reopen silent-sync correctly drops
    # the raw video. Photo marks untouched — the marker-file
    # model handles photo display. Best-effort; non-fatal.
    try:
        _, vid_errors = _apply_video_state_to_camera(
            event, camera_id, "discarded")
        for path, msg in vid_errors:
            errors.append(
                f"discard-all video-state on "
                f"{Path(str(path)).name}: {msg}"
            )
    except Exception as exc:                              # noqa: BLE001
        errors.append(f"discard-all video-state flip failed: {exc}")

    # 1. Tear down the keep_all marker if present — the two are
    # mutually exclusive.
    keep_marker = keep_all_marker_path(event, camera_id)
    if keep_marker.exists():
        try:
            keep_marker.unlink()
        except OSError as exc:
            errors.append(
                f"discard_all: couldn't clear keep_all marker: {exc}"
            )

    # 2. Write the discard marker.
    try:
        marker = discard_all_marker_path(event, camera_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "User chose 'Discard all' for this camera.\n",
            encoding="utf-8",
        )
    except OSError as exc:
        errors.append(f"discard-all marker write failed: {exc}")
        return 0, errors

    # 3. Sweep any stale 01-Culled hardlinks for this camera.
    # Bypass sync_camera_kept_to_culled — its orphan pass
    # early-returns when decisions are empty (B-006's documented
    # safety guard), but the user's explicit Discard-all click
    # overrides that.
    try:
        from core.cull_phase_sync import wipe_camera_culled_hardlinks
        _removed, sweep_errors = wipe_camera_culled_hardlinks(
            event, camera_id)
        for path, msg in sweep_errors:
            errors.append(
                f"hardlink sweep error on {Path(str(path)).name}: {msg}"
            )
    except Exception as exc:  # noqa: BLE001 — defensive
        errors.append(f"hardlink sweep failed: {exc}")

    log.info(
        "Discard-all done: camera=%s files=%d errors=%d",
        camera_id, len(row.files), len(errors),
    )
    return len(row.files), errors


def reset_all(
    event: Event, camera_id: str,
) -> tuple[int, list[str]]:
    """Wipe all cull decisions for ``camera_id`` — markers,
    per-bucket journals, and any 01-Culled hardlinks. Returns
    ``(file_count, error_messages)``.

    F-032 (Nelson 2026-05-27): the destructive sibling of
    :func:`reopen`. Reopen flips the camera back to "not done"
    while preserving the journal (the user's prior K/D
    decisions stay — they can resume editing). Reset all wipes
    those decisions too — the camera goes back to "Not culled"
    with nothing to restore.

    Mechanism:
      1. Delete the camera's journal_root entirely (per-bucket
         journals + markers + any video_marks / browsed flags).
         If the directory doesn't exist it's already a clean
         reset — no-op.
      2. Run :func:`core.cull_phase_sync.sync_camera_kept_to_culled`
         so the orphan-removal pass unlinks any pre-existing
         01-Culled hardlinks for this camera.
    """
    import shutil

    from core.cull_phase_sync import wipe_camera_culled_hardlinks

    rows = discover_cameras(event)
    row = next((r for r in rows if r.camera_id == camera_id), None)
    if row is None:
        return 0, [f"camera {camera_id!r} not found in 00 - Captured"]

    errors: list[str] = []
    journal_root = journal_root_for_camera(event, camera_id)
    if journal_root.is_dir():
        try:
            shutil.rmtree(journal_root)
        except OSError as exc:
            errors.append(
                f"reset_all: couldn't wipe journal root: {exc}"
            )
            # Don't bail — still try the hardlink sweep below in
            # case the partial state matters.

    # Sweep any 01-Culled hardlinks for this camera. Same
    # rationale as discard_all — the user explicitly asked to
    # wipe everything, so bypass sync's defensive guard.
    try:
        _removed, sweep_errors = wipe_camera_culled_hardlinks(
            event, camera_id)
        for path, msg in sweep_errors:
            errors.append(
                f"hardlink sweep error on {Path(str(path)).name}: {msg}"
            )
    except Exception as exc:  # noqa: BLE001 — defensive
        errors.append(f"hardlink sweep failed: {exc}")

    log.info(
        "Reset-all done: camera=%s files=%d errors=%d",
        camera_id, len(row.files), len(errors),
    )
    return len(row.files), errors
