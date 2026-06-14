"""Silent cull-phase sync (task #79, Nelson 2026-05-19 Model 3 v2
freeze).

Frozen Model 3 v2 contract: **"Cull and Select have no user-visible
Export step — hardlink creation is essentially free; the journal IS
the source of truth for cull/select decisions … on phase exit,
Mira auto-reconciles the filesystem projection to match the
journal."**

This module is the auto-reconcile half. The Cull dashboard's
``keep_all`` (in :mod:`core.cull_dashboard`) covers the "skip the
per-photo cull, accept everything" path; this one covers the
opposite — the user did a per-photo cull, and we materialise their
journal decisions into ``01 - Culled/`` when they leave the
camera's cull session.

How it differs from the Keep-all path:

* **Source of truth: the journal.** Read every
  ``ingest_journal.json`` under
  ``<event_root>/.cull/<camera_id_safe>/`` and merge the marks
  across every bucket. Files marked ``kept`` (or carrying the
  bucket's default state when that default is ``kept``) get
  hardlinked.
* **Idempotent.** Running twice produces the same result; the
  underlying ``run_export`` short-circuits on hardlink targets that
  already exist.
* **Best effort.** A camera with zero kept marks produces zero
  hardlinks and zero errors — totally fine for a Cull session the
  user opened, browsed, but didn't mark anything in.

Qt-free. Caller fires after the user leaves the cull shell.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core import video_marks
from core.cull_state import STATE_DISCARDED, STATE_KEPT
from core.models import Event
from core.video_discovery import VIDEO_EXTENSIONS

log = logging.getLogger(__name__)


def _is_video_name(name: str) -> bool:
    """True when ``name`` looks like a video file by extension. Used
    to gate the F-029 ``has_any_kept_derivative`` synthesis so it
    never accidentally upgrades a photo's discarded state to kept
    (clips + snapshots are a video-only concept; an entry in those
    arrays for a non-video file would be a bug we don't want to
    silently launder)."""
    suffix = ""
    dot = name.rfind(".")
    if dot >= 0:
        suffix = name[dot:].lower()
    return suffix in VIDEO_EXTENSIONS


def _apply_video_marks_to_decisions(
    journal_data: dict, out: dict[str, str],
) -> None:
    """F-029 Step 4 — for every video referenced by this journal's
    ``clips`` or ``snapshots`` arrays, evaluate
    :func:`core.video_marks.has_any_kept_derivative` and synthesise
    ``STATE_KEPT`` in ``out`` when it returns True.

    The silent-sync rule (frozen 2026-05-26): hardlink a video source
    forward iff whole-video kept OR any clip kept OR any snapshot
    kept. The synthesised entry lets the downstream additive + orphan
    passes (which already key off ``decisions.get(name) ==
    STATE_KEPT``) honour the rule without branching. Photos are
    untouched — the synthesis only fires for filenames whose
    extension is in :data:`VIDEO_EXTENSIONS`.
    """
    sources: set[str] = set()
    for c in video_marks.list_clips(journal_data):
        sources.add(c.source)
    for s in video_marks.list_snapshots(journal_data):
        sources.add(s.source)
    for source in sources:
        if not _is_video_name(source):
            continue
        if video_marks.has_any_kept_derivative(journal_data, source):
            out[source] = STATE_KEPT


def synthesise_whole_video_clips_for_journal(
    journal_path: Path,
    source_path_for,                                 # noqa: ANN001
) -> int:
    """docs/24 Step 3a — whole-video-kept synthesis at silent-sync.

    For every video in the journal's ``marks`` dict with state
    ``STATE_KEPT`` AND no clip in ``clips[]`` referencing that
    source, probe the source file's duration via FFmpeg and write
    a virtual ``(0, duration_ms)`` clip via
    :func:`core.video_marks.add_clip`. The synthesised clip is
    indistinguishable from a user-created kept clip downstream,
    eliminating the "whole video kept" special case for Select+
    (docs/24 §"Per-phase contract" — Cull bullet).

    ``source_path_for`` is a callable ``basename → Path | None``
    that resolves a journal filename to the live capture file.
    Typically built from ``CameraRow.files`` at sync time, but
    parameterised so tests don't need an Event.

    Idempotent. Runs twice produce the same journal: the second
    run sees the synthesised clip in ``existing_sources`` and
    skips. Best-effort: malformed JSON, missing source files,
    FFmpeg failures all log and continue.

    Writes the journal back to disk only when at least one clip
    was synthesised (zero-mutation runs leave the file untouched).
    Returns count of clips written.
    """
    from core.ingest_session import save_ingest_journal
    from core.video_extract import probe_video

    if not journal_path.is_file():
        return 0
    try:
        with open(journal_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0
    marks = data.get("marks") or {}
    if not isinstance(marks, dict):
        return 0
    existing_sources = {c.source for c in video_marks.list_clips(data)}

    written = 0
    for name, state in marks.items():
        if str(state) != STATE_KEPT:
            continue
        name_s = str(name)
        if not _is_video_name(name_s):
            continue
        if name_s in existing_sources:
            continue
        src_path = source_path_for(name_s)
        if src_path is None:
            continue
        src_path = Path(src_path)
        if not src_path.is_file():
            continue
        try:
            meta = probe_video(src_path)
        except (FileNotFoundError, RuntimeError) as exc:
            log.warning(
                "synthesise whole-video clip: probe failed for %s: %s",
                name_s, exc,
            )
            continue
        if meta.duration_ms <= 0:
            log.warning(
                "synthesise whole-video clip: probed duration=0 for "
                "%s, skipping",
                name_s,
            )
            continue
        video_marks.add_clip(
            data, name_s, 0, meta.duration_ms,
            source_duration_ms=meta.duration_ms,
        )
        existing_sources.add(name_s)            # idempotent within-run
        written += 1

    if written > 0:
        save_ingest_journal(journal_path.parent, data)
    return written


def synthesise_whole_video_clips_for_camera(
    event: Event, camera_id: str,
) -> int:
    """Pre-pass for :func:`sync_camera_kept_to_culled`. Walks every
    ``ingest_journal.json`` under the camera's ``.cull`` root and
    fires :func:`synthesise_whole_video_clips_for_journal` against
    each. Returns total clips synthesised across all journals.

    No-ops cleanly when the camera has no journals yet (typical for
    a freshly-discovered camera the user hasn't culled), or when
    discover_cameras finds no live files (the same precondition
    sync_camera_kept_to_culled itself checks).
    """
    from core.cull_dashboard import (
        discover_cameras, journal_root_for_camera,
    )

    root = journal_root_for_camera(event, camera_id)
    if not root.is_dir():
        return 0
    rows = discover_cameras(event)
    row = next((r for r in rows if r.camera_id == camera_id), None)
    if row is None or not row.files:
        return 0
    paths_by_name = {fp.name: fp for fp in row.files}

    total = 0
    for journal_file in root.rglob("ingest_journal.json"):
        total += synthesise_whole_video_clips_for_journal(
            journal_file, paths_by_name.get,
        )
    if total > 0:
        log.info(
            "Silent cull-sync: synthesised %d whole-video-kept clip(s) "
            "for camera=%s",
            total, camera_id,
        )
    return total


@dataclass
class SyncResult:
    """Outcome of one camera's silent sync.

    ``ok_count`` — files successfully hardlinked / copied.
    ``skipped`` — files marked KEPT in the journal but no longer
      present in 00 - Captured (e.g., user moved them outside
      Mira).
    ``orphans_removed`` — hardlinks under ``01 - Culled/`` for the
      camera whose journal state is no longer STATE_KEPT (B-006).
      Counted separately from ``ok_count`` because the two passes
      are independent: one adds, the other removes.
    ``errors`` — ``(path, message)`` tuples for files where the
      export engine failed OR the orphan-unlink failed.
    """

    camera_id: str = ""
    ok_count: int = 0
    skipped: int = 0
    orphans_removed: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)


def sync_camera_kept_to_culled(
    event: Event, camera_id: str,
) -> SyncResult:
    """Materialise the camera's journal-marked-KEPT files into
    ``01 - Culled/``. Same engine the manual Cull-Export button
    used to drive, just fired silently on phase exit.

    Mechanism:
      1. Walk every ``ingest_journal.json`` under
         ``.cull/<camera_id_safe>/`` to build a single
         filename → state map across every bucket.
      2. Walk every file in
         ``00 - Captured/<bucket>/<Dia N>/<camera_id>/`` and check
         each name against the merged journal.
      3. For each file marked KEPT (or carrying a bucket-default
         ``kept`` state with no explicit mark), build a
         :class:`KeptItem` and feed it to
         :func:`core.cull_export_run.run_export` with
         ``allow_hardlinks=True``.

    Idempotent.
    """
    from core.cull_dashboard import (
        discover_cameras,
        journal_root_for_camera,
    )
    from core.cull_export import CollisionPolicy
    from core.cull_export_resolver import KeptItem
    from core.cull_export_run import run_export
    from core.path_builder import culled_dir, event_root_path

    result = SyncResult(camera_id=camera_id)

    rows = discover_cameras(event)
    row = next((r for r in rows if r.camera_id == camera_id), None)
    if row is None or not row.files:
        return result

    # docs/24 Step 3a — synthesise whole-video-kept virtual clips
    # into the journal BEFORE loading decisions, so Select reads a
    # uniform "everything is a clip" shape (no whole-video special
    # case downstream). Safe to ignore the count — sync's correctness
    # doesn't depend on it; logging happens inside.
    synthesise_whole_video_clips_for_camera(event, camera_id)

    decisions = _load_camera_decisions(event, camera_id)
    # F-029 (Nelson 2026-05-26, supersedes task #121's binary-only
    # video rule): clip + snapshot creation is back at Cull. A video
    # source hardlinks forward iff whole-video kept OR any clip kept
    # OR any snapshot kept — the rule encoded in
    # :func:`core.video_marks.has_any_kept_derivative` and folded
    # into the merged decisions dict by
    # :func:`_apply_video_marks_to_decisions`. From here downstream,
    # videos look identical to photos: a STATE_KEPT entry triggers
    # the additive pass, anything else triggers orphan removal.
    # docs/24 follow-up (Nelson 2026-05-28): the per-phase default
    # state controls how untouched files (no explicit journal mark)
    # are treated. Type-2 user defaults to "discarded" (out-of-box) —
    # only explicitly kept files flow forward; Type-1 user can set
    # "kept" so files they didn't touch at Cull still hardlink.
    from core.settings import load_settings
    cull_default = (
        load_settings().get("cull_default_state") or STATE_DISCARDED
    )

    if not decisions and cull_default != STATE_KEPT:
        # No journal entries AND default-Discard → nothing to do.
        log.info(
            "sync_camera_kept_to_culled: no journal entries for %s "
            "— nothing to materialise",
            camera_id,
        )
        return result

    # Build KeptItems for every file that's marked KEPT and still
    # exists on disk under 00 - Captured.
    items: list[KeptItem] = []
    for fp in row.files:
        state = decisions.get(fp.name, cull_default)
        if state != STATE_KEPT:
            continue
        try:
            day_label = fp.parent.parent.name
        except IndexError:
            continue
        if not day_label or not day_label.lower().startswith("dia"):
            continue
        items.append(KeptItem(
            src=fp,
            capture_dt=None,
            day_label=day_label,
            style="general",          # silent sync doesn't classify
            bracket_id=None,
            exif_datetime=None,       # no retime — hardlink-friendly
            bucket=row.bucket,
            camera_id=row.camera_id,
        ))

    base = (event.photos_base_path or "").strip()
    if not base:
        result.errors.append((Path(""), "event has no photos_base_path"))
        return result
    dest_root = culled_dir(event_root_path(base, event))

    # B-006 — orphan removal must run even when there's nothing to
    # ADD. Canonical case: user marked a photo KEPT, sync happened,
    # then user came back and flipped it to DISCARDED. The second
    # sync has zero KEPT items but a stale hardlink to unlink. The
    # early-return on ``not items`` below skips the additive pass
    # but the orphan pass still runs above it.
    if dest_root.is_dir():
        result.orphans_removed = _remove_orphan_hardlinks(
            dest_root, camera_id, decisions, errors=result.errors,
            default_state=cull_default,
        )

    if not items:
        log.info(
            "sync_camera_kept_to_culled: %s has journal entries "
            "but no files are marked KEPT (orphans_removed=%d)",
            camera_id, result.orphans_removed,
        )
        return result

    dest_root.mkdir(parents=True, exist_ok=True)

    try:
        export_result = run_export(
            items,
            dest_root,
            collision=CollisionPolicy.UNIQUE,
            allow_hardlinks=True,
        )
    except Exception as exc:        # noqa: BLE001 — defensive
        result.errors.append((Path(""), f"export failed: {exc}"))
        log.exception(
            "sync_camera_kept_to_culled: export raised for %s",
            camera_id,
        )
        return result

    result.ok_count = export_result.ok_count
    for src, msg in export_result.errors:
        result.errors.append((src, msg))

    log.info(
        "Silent cull-sync: camera=%s kept=%d ok=%d orphans_removed=%d "
        "errors=%d",
        camera_id, len(items), result.ok_count,
        result.orphans_removed, len(result.errors),
    )
    return result


def _remove_orphan_hardlinks(
    dest_root: Path,
    camera_id: str,
    decisions: dict[str, str],
    *,
    errors: list[tuple[Path, str]],
    default_state: str = STATE_DISCARDED,
) -> int:
    """B-006 — walk every file under ``01 - Culled/`` belonging to
    ``camera_id`` and unlink the ones whose journal state is no
    longer STATE_KEPT. Returns the count removed.

    Layout (per ``core.cull_export_resolver.target_relpath``):
      ``<dest_root>/<bucket>/<day>/<camera_id>/<style>/[<bracket>/]<file>``

    The walk uses a targeted glob so we only inspect files belonging
    to ``camera_id`` — other cameras' hardlinks are untouched.

    Empty parent directories are pruned after a removal (style, then
    bracket if present) so we don't leave stale empty trees behind.
    The bucket / day / camera_id dirs are NOT pruned — they may be
    re-populated on the next additive pass + they're cheap inodes.

    Failures are appended to ``errors`` and counted as removed=0 for
    that file. The pass never raises — best-effort, just like
    ``run_export``.
    """
    if not dest_root.is_dir():
        return 0
    removed = 0
    # Targeted glob: every file under a directory whose third path
    # component (bucket / day / camera_id) matches our camera. The
    # ``**/*`` tail covers <style>/ and the optional <bracket>/
    # sub-folder for focus/exposure-bracket buckets.
    for f in dest_root.glob(f"*/*/{camera_id}/**/*"):
        if not f.is_file():
            continue
        # docs/24 follow-up (Nelson 2026-05-28): default-aware
        # lookup respects the user's per-phase setting for files
        # not in the journal. Out-of-box (Discard) preserves
        # legacy behaviour; "kept" leaves untouched files alone.
        state = decisions.get(f.name, default_state)
        if state == STATE_KEPT:
            continue
        # Either no journal entry at all (orphan from a since-
        # deleted bucket) OR explicitly DISCARDED — both cases
        # mean the hardlink should not exist.
        try:
            f.unlink()
            removed += 1
        except OSError as exc:
            errors.append((f, f"orphan unlink failed: {exc}"))
            continue
        # Prune the now-empty style folder (and the optional bracket
        # sub-folder it might sit inside).
        _prune_empty_dir(f.parent, stop_at=dest_root)
    return removed


def _prune_empty_dir(path: Path, *, stop_at: Path) -> None:
    """Remove ``path`` if empty, then walk up doing the same — but
    never cross ``stop_at`` (the cull-dest root) and never delete
    the camera_id / day / bucket / dest_root levels themselves.

    The camera_id directory is the *third* path component below
    ``stop_at``; we stop pruning before we reach it. Keeps the
    layout's skeleton intact for the next additive pass.
    """
    try:
        # Compute depth from dest_root so we know when to stop.
        rel_parts = path.relative_to(stop_at).parts
    except ValueError:
        return
    # rel_parts = (bucket, day, camera_id, style, [bracket])
    # Prune levels >= 3 (style and deeper); never prune at the
    # camera level (index 2) or above.
    cur = path
    while True:
        try:
            rel = cur.relative_to(stop_at).parts
        except ValueError:
            return
        if len(rel) < 4:                        # at camera_id or shallower
            return
        try:
            cur.rmdir()
        except OSError:
            return                              # not empty or perms — stop
        cur = cur.parent


def _load_camera_decisions(
    event: Event, camera_id: str,
) -> dict[str, str]:
    """Merge filename → state across every journal under the
    camera's journal root. Defensive: malformed JSON / missing
    keys / non-dict ``marks`` blocks degrade gracefully to an
    empty contribution.

    F-029 Step 4: per-journal, videos with any kept derivative
    (whole-video mark, any kept clip, or any kept snapshot) get
    ``STATE_KEPT`` synthesised into the merged dict so the
    downstream additive + orphan passes honour the unified rule.
    Photos are untouched.
    """
    from core.cull_dashboard import journal_root_for_camera

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
            for k, v in marks.items():
                out[str(k)] = str(v)
        # Apply video_marks AFTER the marks loop so a kept-derivative
        # synthesis can override a stale DISCARDED whole-video mark.
        _apply_video_marks_to_decisions(data, out)
    return out


def wipe_camera_culled_hardlinks(
    event: Event, camera_id: str,
) -> tuple[int, list[tuple[Path, str]]]:
    """F-032 (Nelson 2026-05-27): unlink every 01-Culled hardlink
    belonging to ``camera_id`` regardless of the camera's journal
    state. Returns ``(removed_count, error_tuples)``.

    Used by :func:`core.cull_dashboard.discard_all` (mark every
    file DISCARDED — sweep the kept hardlinks left over from a
    prior Keep-all) and :func:`core.cull_dashboard.reset_all`
    (wipe everything — sweep the hardlinks the journal-wipe just
    abandoned).

    Bypasses :func:`sync_camera_kept_to_culled`'s "empty
    decisions → early return" guard (B-006's documented
    limitation: that guard was meant to keep an accidental
    journal wipe from sweeping the user's hardlinks). The batch
    ops above are EXPLICIT — the user clicked Discard-all /
    Reset-all and accepted the consequence in the confirmation
    dialog — so the guard doesn't apply.
    """
    from core.path_builder import culled_dir, event_root_path

    base = (event.photos_base_path or "").strip()
    if not base:
        return 0, [(Path(""), "event has no photos_base_path")]
    dest_root = culled_dir(event_root_path(base, event))
    if not dest_root.is_dir():
        return 0, []
    errors: list[tuple[Path, str]] = []
    # Empty decisions dict ⇒ every file under the camera reads
    # as "no longer STATE_KEPT" and gets unlinked. Same orphan
    # walk + same empty-parent pruning as the synced path.
    removed = _remove_orphan_hardlinks(
        dest_root, camera_id, decisions={}, errors=errors,
    )
    return removed, errors


# NOTE: ``_videos_with_cull_activity`` was removed in task #121
# (Nelson 2026-05-23) when video Cull/Select was binary K/D only.
# F-029 (Nelson 2026-05-26) re-enabled clip + snapshot creation at
# Cull — but the implicit-keep signal it computes lives in the
# journal's ``clips`` / ``snapshots`` arrays now, not in a derived
# session-file walk. :func:`_apply_video_marks_to_decisions` folds
# the rule into the merged decisions dict at journal-read time, so
# the same downstream code paths handle photos + videos uniformly.


# ── Select-phase silent sync (B-007, 2026-05-24) ─────────────────


def sync_kept_to_selected(event: Event) -> SyncResult:
    """Materialise the Select journal's KEPT marks into ``02 -
    Selected/``. Symmetric to :func:`sync_camera_kept_to_culled`
    but at the Select layer.

    Key differences from the Cull version:

      * **No camera_id parameter.** The Select journal is
        consolidated (single root at ``<event>/.cull/select/``);
        Select-phase output is also consolidated
        (``02 - Selected/<day>/<style>/`` — no per-camera or
        per-bucket sub-shape).
      * **Source root is ``01 - Culled/``**, not ``00 - Captured/``.
        Select reads from the Cull projection.
      * **Destination layout is consolidated**: ``KeptItem`` with
        ``bucket=""`` triggers the ``<dest>/<day>/<style>/<file>``
        path in :func:`core.cull_export_resolver.target_relpath`.

    Two-pass behaviour mirrors Cull (B-006):
      1. Additive — for every name marked KEPT in the Select
         journal, find the upstream file in ``01 - Culled/`` and
         hardlink it under ``02 - Selected/<day>/<style>/``.
      2. Orphan removal — walk ``02 - Selected/`` and unlink any
         file whose Select-journal state is no longer STATE_KEPT.

    Idempotent. Best effort. Qt-free.
    """
    from core.cull_export import CollisionPolicy
    from core.cull_export_resolver import KeptItem
    from core.cull_export_run import run_export
    from core.path_builder import (
        culled_dir, event_root_path, selected_dir,
    )

    result = SyncResult(camera_id="")          # no per-camera scope

    base = (event.photos_base_path or "").strip()
    if not base:
        result.errors.append((Path(""), "event has no photos_base_path"))
        return result
    event_root = event_root_path(base, event)
    src_root = culled_dir(event_root)
    dest_root = selected_dir(event_root)

    decisions = _load_select_decisions(event_root)
    # docs/24 follow-up (Nelson 2026-05-28): the Select default
    # state controls how files NOT in the Select journal are
    # treated. Type-1 user with "kept" default → files that
    # survived Cull but the user never K/D'd at Select still flow
    # to 02-Selected. Type-2 (out-of-box "discarded") → only
    # explicitly kept files flow.
    from core.settings import load_settings
    select_default = (
        load_settings().get("pick_default_state") or STATE_DISCARDED
    )
    if not decisions and select_default != STATE_KEPT:
        log.info(
            "sync_kept_to_selected: no Select journal entries "
            "— nothing to materialise",
        )
        return result

    # ── Additive pass ────────────────────────────────────────────
    # Walk 01-Culled to find the source file for each KEPT name. The
    # layout is <bucket>/<day>/<camera>/<style>/<file> per the Cull-
    # Export resolver. For each found file we extract day_label and
    # style from the path parts.
    items: list[KeptItem] = []
    if src_root.is_dir():
        for fp in src_root.rglob("*"):
            if not fp.is_file():
                continue
            if decisions.get(fp.name, select_default) != STATE_KEPT:
                continue
            # Path parts under src_root: (bucket, day, camera, style, file)
            try:
                rel_parts = fp.relative_to(src_root).parts
            except ValueError:
                continue
            if len(rel_parts) < 5:
                continue
            _bucket, day_label, _cam, style = rel_parts[:4]
            items.append(KeptItem(
                src=fp,
                capture_dt=None,
                day_label=day_label,
                style=style,
                bracket_id=None,
                exif_datetime=None,
                bucket="",                     # consolidated layout
                camera_id="",
            ))

    # ── Orphan-removal pass (B-006 symmetric for Select) ─────────
    # Always runs, even when there's nothing to add — same
    # canonical regression case as Cull: user marked KEPT, sync
    # happened, user flipped to DISCARDED, second sync must remove
    # the stale hardlink.
    if dest_root.is_dir():
        result.orphans_removed = _remove_orphan_hardlinks_select(
            dest_root, decisions, errors=result.errors,
            default_state=select_default,
        )

    if not items:
        log.info(
            "sync_kept_to_selected: Select journal has no STATE_KEPT "
            "entries (orphans_removed=%d)",
            result.orphans_removed,
        )
        return result

    dest_root.mkdir(parents=True, exist_ok=True)

    try:
        export_result = run_export(
            items,
            dest_root,
            collision=CollisionPolicy.UNIQUE,
            allow_hardlinks=True,
        )
    except Exception as exc:        # noqa: BLE001 — defensive
        result.errors.append((Path(""), f"export failed: {exc}"))
        log.exception("sync_kept_to_selected: export raised")
        return result

    result.ok_count = export_result.ok_count
    for src, msg in export_result.errors:
        result.errors.append((src, msg))

    log.info(
        "Silent select-sync: kept=%d ok=%d orphans_removed=%d errors=%d",
        len(items), result.ok_count,
        result.orphans_removed, len(result.errors),
    )
    return result


def _load_select_decisions(event_root: Path) -> dict[str, str]:
    """Merge filename → state across every journal under
    ``<event_root>/.cull/select/``. Same shape as
    :func:`_load_camera_decisions` but rooted at the consolidated
    Select-journal location, not a per-camera one.

    F-029 Step 4: the same video-mark synthesis applies — a video
    with any kept derivative in the Select journal hardlinks
    forward to ``02 - Selected/``. (The cross-phase inheritance
    of clips defined in Cull but never re-marked in Select is a
    separate concern, tracked outside this function.)
    """
    root = event_root / ".cull" / "pick"
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
            for k, v in marks.items():
                out[str(k)] = str(v)
        _apply_video_marks_to_decisions(data, out)
    return out


def _remove_orphan_hardlinks_select(
    dest_root: Path,
    decisions: dict[str, str],
    *,
    errors: list[tuple[Path, str]],
    default_state: str = STATE_DISCARDED,
) -> int:
    """B-007 — walk every file under ``02 - Selected/`` and unlink
    the ones whose Select-journal state is no longer STATE_KEPT.

    Layout (consolidated):
      ``<dest_root>/<day>/<style>/<file>``

    Empty parent directories (style) are pruned after a removal;
    day directories are NOT pruned (they may be repopulated on the
    next additive pass + they're cheap inodes).

    docs/24 follow-up (Nelson 2026-05-28): ``default_state``
    controls how files lacking a Select-journal entry are treated.
    Out-of-box ``"discarded"`` preserves the legacy behaviour
    (untouched files removed as orphans); the user can set
    ``"kept"`` so untouched files stay.
    """
    if not dest_root.is_dir():
        return 0
    removed = 0
    for f in dest_root.rglob("*"):
        if not f.is_file():
            continue
        if decisions.get(f.name, default_state) == STATE_KEPT:
            continue
        try:
            f.unlink()
            removed += 1
        except OSError as exc:
            errors.append((f, f"orphan unlink failed: {exc}"))
            continue
        _prune_empty_dir_select(f.parent, stop_at=dest_root)
    return removed


def _prune_empty_dir_select(path: Path, *, stop_at: Path) -> None:
    """Like :func:`_prune_empty_dir` but for the consolidated Select
    layout: only the style level (depth 2 from dest_root) is
    eligible for pruning. The day level (depth 1) is preserved for
    the next additive pass."""
    cur = path
    while True:
        try:
            rel = cur.relative_to(stop_at).parts
        except ValueError:
            return
        if len(rel) < 2:                       # at day level or shallower
            return
        try:
            cur.rmdir()
        except OSError:
            return
        cur = cur.parent


def cleanup_stale_select_kepts(event: Event) -> int:
    """Walk every Select-journal ``ingest_journal.json``; for each
    name marked STATE_KEPT, check whether the source file actually
    exists anywhere under ``01 - Culled/``; if not, demote the
    entry to STATE_DISCARDED. Returns the number of demoted entries.

    Why DEMOTE rather than DELETE (Nelson 2026-05-28): keeps the
    audit trail intact — the entry still records that the user
    once-upon-a-time chose to keep this name, just rolled forward
    to "no longer kept" because the source isn't on disk. Future
    audit / re-import / forensic work can still see the history.

    Use case: post-cleanup of pre-00.093 ``(N)``-suffix duplicates.
    Nelson kept several ``2025-10-31_03.46.15 (2).jpg``-style
    entries during the duplicate-laden phase; we later cleaned the
    ``(N)`` files off disk but the journal still references the
    stale names as KEPT, producing ``missing_selected_file`` audit
    findings. This pass closes that loop.

    Idempotent. Best effort — per-journal errors are logged and
    skipped, never raised."""
    from core.path_builder import culled_dir, event_root_path

    base = (event.photos_base_path or "").strip()
    if not base:
        return 0
    event_root = event_root_path(base, event)
    src_root = culled_dir(event_root)
    select_root = event_root / ".cull" / "pick"
    if not select_root.is_dir():
        return 0

    # Build the set of every filename that exists under 01-Culled
    # (single pass, O(n) — beats per-name rglobs).
    culled_names: set[str] = set()
    if src_root.is_dir():
        for fp in src_root.rglob("*"):
            if fp.is_file():
                culled_names.add(fp.name)

    demoted = 0
    for journal_file in select_root.rglob("ingest_journal.json"):
        try:
            with open(journal_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        marks = data.get("marks")
        if not isinstance(marks, dict):
            continue
        changed = False
        for name, state in list(marks.items()):
            if state != STATE_KEPT:
                continue
            if str(name) in culled_names:
                continue
            # Stale KEPT — file isn't in 01-Culled. Demote.
            marks[str(name)] = STATE_DISCARDED
            demoted += 1
            changed = True
        if changed:
            try:
                # Preserve the rest of the journal structure
                # (clips, snapshots, sharpness, default_state, etc.).
                data["marks"] = marks
                tmp = journal_file.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                tmp.replace(journal_file)
                log.info(
                    "cleanup_stale_select_kepts: demoted entries in %s",
                    journal_file,
                )
            except OSError as exc:
                log.warning(
                    "cleanup_stale_select_kepts: write failed for %s: %s",
                    journal_file, exc,
                )
                continue
    if demoted:
        log.info(
            "cleanup_stale_select_kepts: demoted %d stale KEPT "
            "entries across the Select journal",
            demoted,
        )
    return demoted
