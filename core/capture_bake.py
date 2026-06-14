"""Capture-time EXIF bake — Model 3 v2 (Nelson 2026-05-22).

Applies the timezone correction to the EXIF DateTimeOriginal of
every file Mira just copied into ``00 - Captured/``. Runs
ONCE per ingest, between integrity-verify and (for live-card
sources) the wipe gate. After this step, ``00 - Captured`` is the
canonical event origin with TZ-correct EXIF and is contract-frozen
until the explicit "Adjust event TZ" operation.

This module is the SINGLE engine both ingest paths share (B-008,
converged 2026-05-25):

* :func:`bake_operations` — the underlying primitive. Takes a
  pre-built list of ``(file, new_datetime)`` pairs and applies
  them via a single persistent exiftool session (fast — one
  process, not one-per-file). Used directly by the past-photos
  ingest (``core/reconcile_pipeline.reconcile_commit``) because
  past-photos can have per-day TZ overrides → per-file targets.
* :func:`bake_offload_manifest` — the live-card convenience
  wrapper. Takes a single ``offset_hours`` (live-card calibrates
  each camera once, that offset applies uniformly), builds the
  pairs from the offload manifest's ``capture_time_raw`` field +
  the offset, and delegates to :func:`bake_operations`.

The bake is **idempotent at the offset level**: passing offset=0
(or an empty operations list) no-ops cleanly. Re-running with the
SAME offset on already-baked files would re-apply the same shift
(so don't — the caller should track "already baked" state, e.g.,
via the event's calibration record).

Qt-free. Pure dataclasses + filesystem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

from core import exif_rewriter
from core.event_backup_card import OffloadFileRecord, OffloadManifest

log = logging.getLogger(__name__)


@dataclass
class BakeResult:
    """Outcome of a bake pass over an offload session.

    ``ok_count``: files successfully baked.
    ``skipped_no_timestamp``: files whose ``capture_time_raw`` was
      missing — couldn't compute a target time so left untouched.
    ``errors``: ``(path, message)`` tuples for failed rewrites
      (per-file best-effort; one failure doesn't abort the rest).
    ``offset_hours``: the offset that was applied (echoed back for
      audit trail).
    """
    ok_count: int = 0
    skipped_no_timestamp: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    offset_hours: float = 0.0


def bake_operations(
    operations: list[tuple[Path, datetime]],
    *,
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> BakeResult:
    """The shared bake primitive. Apply each ``(file, new_datetime)``
    pair via a single persistent exiftool session.

    Convention: the caller has already computed ``new_datetime`` per
    file (single offset for live-card; per-day-TZ-corrected for
    past-photos). This function just writes — no offset arithmetic,
    no skip logic. The caller decides which files to include.

    ``operations`` empty → fast no-op, returns a clean
    :class:`BakeResult`.

    Each rewrite is atomic write-then-rename (see
    ``core/exif_rewriter``) and preserves every other EXIF tag and
    the file's mtime. The batched session is much faster than
    per-file ``rewrite_capture_time`` because exiftool's per-spawn
    startup is ~0.5 s on Windows (Nelson 2026-05-22 — 1300 files
    via per-file path = ~21 minutes of nothing but process spawning;
    via batched session = seconds).

    The ``progress`` callback (if supplied) is called with
    ``(message, current_index, total_count)`` so a Qt host can
    drive a progress dialog without this module depending on Qt.

    Returns a :class:`BakeResult` summarizing what happened.
    ``offset_hours`` is left at the default 0.0 — the caller knows
    the offset (if any) and can set it on the returned result for
    audit logging.
    """
    result = BakeResult()
    total = len(operations)

    if total == 0:
        return result

    if progress is not None:
        progress(
            f"Applying timezone correction to {total} file(s)…",
            0, total,
        )

    def _batch_progress(msg: str, cur: int, tot: int) -> None:
        if progress is not None:
            progress(msg, cur, tot)

    outcomes = exif_rewriter.rewrite_capture_times_batch(
        operations, progress=_batch_progress,
    )
    for (path, _dt), outcome in zip(operations, outcomes):
        if outcome.error:
            result.errors.append((path, outcome.error))
            log.warning(
                "Bake: rewrite_capture_time failed on %s: %s",
                path, outcome.error,
            )
        else:
            result.ok_count += 1

    log.info(
        "Bake complete: ops=%d ok=%d errors=%d",
        total, result.ok_count, len(result.errors),
    )
    return result


def bake_offload_manifest(
    manifest: OffloadManifest,
    offset_hours: float,
    *,
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> BakeResult:
    """Live-card convenience wrapper: apply ``offset_hours`` to the
    EXIF DateTimeOriginal of every file recorded in ``manifest``.

    Builds the per-file ``(path, raw_dt + offset)`` operations list
    from the manifest's ``capture_time_raw`` field, then delegates
    to :func:`bake_operations`. Files with no readable raw timestamp
    in the manifest are skipped (we wouldn't know what to shift).

    Convention: a positive ``offset_hours`` ADDS hours to the
    camera's reading (use when the camera's clock was set BEHIND
    trip-local time). Negative subtracts.

    ``offset_hours == 0`` is a fast no-op (no files touched, no EXIF
    writes); the caller can pass 0 to indicate "camera time is
    correct" and trust this function to skip cleanly.

    Returns a :class:`BakeResult` with ``offset_hours`` set so audit
    logs and the wipe-gate diagnostics can report the applied shift.
    """
    result = BakeResult(offset_hours=float(offset_hours))
    total = len(manifest.files)

    if offset_hours == 0.0:
        log.info(
            "Bake skipped: offset 0.0 — manifest %s untouched",
            manifest.session_subdir_name,
        )
        return result

    if total == 0:
        return result

    shift = timedelta(hours=float(offset_hours))
    operations: list[tuple[Path, datetime]] = []

    if progress is not None:
        progress(
            f"Preparing bake for {total} file(s)…", 0, total,
        )

    # Build the (path, new_datetime) list. Skip files that have no
    # readable raw timestamp recorded in the manifest — we wouldn't
    # know what to shift.
    for rec in manifest.files:
        raw_iso = rec.capture_time_raw
        if not raw_iso:
            result.skipped_no_timestamp += 1
            continue
        try:
            raw_dt = datetime.fromisoformat(raw_iso)
        except (TypeError, ValueError):
            log.warning(
                "Bake: unparseable capture_time_raw=%r on %s",
                raw_iso, rec.dest,
            )
            result.skipped_no_timestamp += 1
            continue
        operations.append((Path(rec.dest), raw_dt + shift))

    # Delegate to the shared primitive; merge its result into ours
    # (keeping our offset_hours + skipped_no_timestamp counters).
    sub_result = bake_operations(operations, progress=progress)
    result.ok_count = sub_result.ok_count
    result.errors = list(sub_result.errors)

    log.info(
        "Bake complete: manifest=%s offset=%+.2fh ok=%d skipped=%d "
        "errors=%d",
        manifest.session_subdir_name, offset_hours,
        result.ok_count, result.skipped_no_timestamp,
        len(result.errors),
    )
    return result


def remember_camera_offset(
    camera_id: str,
    offset_hours: float,
    settings_path_writer: Callable[[dict], None],
    current_settings: dict,
) -> None:
    """Convenience helper: write ``camera_id → offset_hours`` to the
    user's ``saved_camera_offsets`` setting.

    ``settings_path_writer`` is the caller's persist function (so
    this module doesn't take a runtime dependency on
    :mod:`core.settings`'s save mechanism — callers using
    :func:`core.settings.update_setting` should pass a thin wrapper).
    ``current_settings`` is the read-side; we mutate a copy of its
    ``saved_camera_offsets`` map.
    """
    saved = dict(current_settings.get("saved_camera_offsets", {}) or {})
    saved[camera_id] = float(offset_hours)
    settings_path_writer({"saved_camera_offsets": saved})
    log.info(
        "Saved camera offset: %s = %+.2fh",
        camera_id, offset_hours,
    )
