"""Consistency audit + rebuild — task #83 (Nelson 2026-05-23, Model 3 v2).

Read-only audit (and a paired rebuild mode) that compares the
journal (source of truth) against the filesystem projection
(derived). The rule is the circular-safe one from docs/18:

    journal ⇄ filesystem projection

Where the journal is authoritative for cull/select decisions; the
filesystem is its derived projection; and the projection is
recoverable by walking the journal. ``audit_event`` reports
divergences; ``rebuild_event`` regenerates the projection by firing
the same silent-sync code path that runs on phase exit.

Audit scope (Phase 1):
  * **Plan ↔ disk** — every on-disk day folder has a matching
    ``TripDay`` entry; every plan day with on-disk files is
    accounted for.
  * **00 - Captured** — day folders match the canonical
    ``Dia N - YYYY-MM-DD - desc`` shape; the day numbers exist in
    the plan; files inside are non-empty.
  * **01 - Culled** — for every journal-marked KEPT file, a
    corresponding hardlink exists somewhere under
    ``01 - Culled/<bucket>/<day>/<camera_id>/`` (style folder is
    classification-dependent so we don't pin it). Reverse: every
    file under 01 - Culled traces back to a KEPT journal entry
    (orphans are flagged).

Out of scope this pass (follow-up tasks):
  * Phase 2 (02 - Selected) — Select phase isn't fully wired yet;
    add the symmetric check when Select-Export lands.
  * Phase 3 / 3b / 4 / 5 — processed/curated/distributed are out of
    scope for the cull-time projection check. The plan↔disk check
    catches the most common rename divergence at that layer.

Qt-free. Caller drives — typically a sidebar tool or a per-event
button on EventPlanPage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.models import Event

log = logging.getLogger(__name__)


# ── Severity / phase constants ──────────────────────────────────

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

PHASE_PLAN = "plan"
PHASE_CAPTURED = "captured"
PHASE_CULLED = "culled"
PHASE_PICKED = "selected"

ALL_PHASES: tuple[str, ...] = (
    PHASE_PLAN,
    PHASE_CAPTURED,
    PHASE_CULLED,
    PHASE_PICKED,
)


# ── Finding / report dataclasses ────────────────────────────────


@dataclass(frozen=True)
class AuditFinding:
    """One divergence between journal/plan (truth) and disk
    (projection). ``kind`` is a short stable code (snake_case) so
    UI surfaces can switch on it; ``detail`` is the human string.

    ``path`` is the on-disk path the finding refers to when one
    exists (None for plan-level findings)."""

    severity: str
    phase: str
    kind: str
    detail: str
    path: Optional[Path] = None


@dataclass
class AuditReport:
    """Outcome of one ``audit_event`` call. Counts are populated so
    a UI summary can render "201 files captured · 198 culled · 3
    findings (1 error, 2 warnings)" without re-walking the tree."""

    event_name: str = ""
    findings: list[AuditFinding] = field(default_factory=list)
    captured_files: int = 0
    culled_files: int = 0
    selected_files: int = 0
    journal_kept_total: int = 0
    select_kept_total: int = 0

    @property
    def errors(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == SEVERITY_WARNING]

    @property
    def infos(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == SEVERITY_INFO]

    @property
    def ok(self) -> bool:
        """True when there are no errors. Warnings don't block ok."""
        return not self.errors

    def add(
        self,
        severity: str,
        phase: str,
        kind: str,
        detail: str,
        path: Optional[Path] = None,
    ) -> None:
        self.findings.append(AuditFinding(
            severity=severity, phase=phase, kind=kind,
            detail=detail, path=path,
        ))


@dataclass
class RebuildReport:
    """Outcome of one ``rebuild_event`` call."""

    event_name: str = ""
    cameras_synced: int = 0
    files_materialised: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ── Public entry points ──────────────────────────────────────────


def audit_event(event: Event) -> AuditReport:
    """Read-only audit across the plan, the captured tree, and the
    cull projection. Never mutates anything on disk."""
    report = AuditReport(event_name=event.name or event.id)

    base = (event.photos_base_path or "").strip()
    if not base:
        report.add(
            SEVERITY_ERROR, PHASE_PLAN, "no_event_root",
            "Event has no photos_base_path set — no disk to audit.",
        )
        return report

    from core.path_builder import (
        captured_dir, culled_dir, event_root_path,
    )

    root = event_root_path(base, event)
    if not root.is_dir():
        report.add(
            SEVERITY_ERROR, PHASE_PLAN, "missing_event_root",
            f"Event root does not exist on disk: {root}",
            path=root,
        )
        return report

    from core.path_builder import selected_dir

    _audit_plan(event, root, report)
    _audit_captured(event, captured_dir(root), report)
    _audit_culled(event, root, culled_dir(root), report)
    _audit_selected(event, root, selected_dir(root), report)
    return report


def rebuild_event(
    event: Event,
    *,
    phases: Optional[set[str]] = None,
) -> RebuildReport:
    """Regenerate filesystem projections from the journal.

    ``phases`` restricts which projections are rebuilt; ``None``
    defaults to every supported phase. Each phase delegates to its
    canonical silent-sync code path so Audit-rebuild and
    on-phase-exit auto-reconcile use the same mechanism — Model 3
    v2's "journal is truth, projection is derived; we don't have
    two ways to derive it."

    **Select coverage (Nelson 2026-05-28).** Pre-this-extension
    rebuild only ran ``sync_camera_kept_to_culled``; the Select
    projection (``02 - Selected/``) stayed stale relative to the
    Select journal. The user-facing Audit dialog correctly reported
    Select orphans + missing files but couldn't fix them. Now
    rebuild also fires ``sync_kept_to_selected`` so a single click
    of "Rebuild projection" reconciles both projection layers —
    materialises missing hardlinks AND removes stale ``(N)``
    duplicates / orphans via the silent-sync engines' own
    orphan-removal passes.
    """
    requested = (
        phases if phases is not None
        else {PHASE_CULLED, PHASE_PICKED}
    )
    rb = RebuildReport(event_name=event.name or event.id)

    if PHASE_CULLED in requested:
        _rebuild_culled(event, rb)
    if PHASE_PICKED in requested:
        _rebuild_selected(event, rb)

    return rb


# ── Plan / Captured / Culled checks ──────────────────────────────


def _audit_plan(event: Event, root: Path, report: AuditReport) -> None:
    """Plan-shape sanity checks (independent of disk)."""
    days = list(event.trip_days or [])
    if not days:
        report.add(
            SEVERITY_WARNING, PHASE_PLAN, "empty_plan",
            "Event has no trip days — Capture has nothing to land in.",
        )
        return

    seen_numbers: set[int] = set()
    for day in days:
        if day.day_number in seen_numbers:
            report.add(
                SEVERITY_ERROR, PHASE_PLAN, "duplicate_day_number",
                f"Day {day.day_number} appears more than once in the plan.",
            )
        seen_numbers.add(day.day_number)
        if day.date is None:
            report.add(
                SEVERITY_WARNING, PHASE_PLAN, "missing_day_date",
                f"Day {day.day_number} has no date — folder naming"
                " falls back to legacy shape.",
            )


def _audit_captured(
    event: Event, captured: Path, report: AuditReport,
) -> None:
    """Walk ``00 - Captured/`` and check each day folder against
    the plan + the canonical naming convention."""
    from core.path_builder import (
        CAPTURED_CAMERAS_SUBDIR,
        CAPTURED_OTHER_SUBDIR,
        CAPTURED_PHONES_SUBDIR,
        day_number_from_folder,
    )

    if not captured.is_dir():
        # Not an error — pre-Capture events have no 00 - Captured.
        report.add(
            SEVERITY_INFO, PHASE_CAPTURED, "no_captured_tree",
            "00 - Captured does not exist (event not yet ingested).",
            path=captured,
        )
        return

    plan_day_numbers = {d.day_number for d in (event.trip_days or [])}
    captured_files = 0

    for bucket in (
        CAPTURED_CAMERAS_SUBDIR,
        CAPTURED_PHONES_SUBDIR,
        CAPTURED_OTHER_SUBDIR,
    ):
        bucket_dir = captured / bucket
        if not bucket_dir.is_dir():
            continue
        for day_dir in bucket_dir.iterdir():
            if not day_dir.is_dir():
                continue
            day_num = day_number_from_folder(day_dir.name)
            if day_num is None:
                report.add(
                    SEVERITY_WARNING, PHASE_CAPTURED,
                    "unparseable_day_folder",
                    f"Folder name doesn't match Dia N - … shape: "
                    f"{day_dir.name}",
                    path=day_dir,
                )
                continue
            if day_num not in plan_day_numbers:
                report.add(
                    SEVERITY_ERROR, PHASE_CAPTURED,
                    "orphan_day_folder",
                    f"Day {day_num} exists on disk but not in the "
                    f"plan ({day_dir.name}).",
                    path=day_dir,
                )
            # Count files (one cam_dir level deeper).
            for cam_dir in day_dir.iterdir():
                if not cam_dir.is_dir():
                    continue
                for f in cam_dir.rglob("*"):
                    if f.is_file():
                        captured_files += 1

    report.captured_files = captured_files


def _audit_culled(
    event: Event, root: Path, culled: Path, report: AuditReport,
) -> None:
    """Compare the cull projection against the journals.

    For each discovered camera:
      * KEPT files in the journal must have a hardlink under
        ``01 - Culled/<bucket>/<day>/<camera_id>/`` (any style
        subfolder).
      * Files under that same prefix must trace back to a KEPT
        journal entry (orphans flagged).

    F-029 video promotion (Nelson 2026-05-28): a video source is
    KEPT iff ``has_any_kept_derivative`` (whole-video kept OR any
    clip kept OR any snapshot kept). Pre-fix, the audit checked
    only the raw ``marks`` dict — so a video with kept clips but a
    discarded whole-video state was flagged as ``orphan_hardlink``
    even though the silent-sync correctly hardlinked it forward.
    Now the audit applies the same promotion the sync engines use,
    so audit verdict matches sync behaviour.
    """
    from core.cull_dashboard import (
        _load_camera_decisions,
        discover_cameras,
    )
    from core.cull_phase_sync import _apply_video_marks_to_decisions
    from core.cull_state import STATE_KEPT
    from core.ingest_session import load_ingest_journal
    from core.cull_dashboard import journal_root_for_camera
    from core.path_builder import sanitize_folder_name

    rows = discover_cameras(event)
    if not rows and not culled.is_dir():
        # Nothing captured and nothing culled — symmetric.
        return

    if not culled.is_dir():
        # Cameras exist but no culled tree — fine if no kept marks.
        any_kept = False
        for r in rows:
            decisions = _load_camera_decisions(event, r.camera_id)
            if any(s == STATE_KEPT for s in decisions.values()):
                any_kept = True
                break
        if any_kept:
            report.add(
                SEVERITY_ERROR, PHASE_CULLED, "missing_culled_tree",
                "Journal marks files KEPT but 01 - Culled does not "
                "exist on disk. Run rebuild to materialise.",
                path=culled,
            )
        else:
            report.add(
                SEVERITY_INFO, PHASE_CULLED, "no_culled_tree",
                "01 - Culled does not exist (nothing kept yet).",
                path=culled,
            )
        return

    # Build a name → list[path] index over the entire culled tree
    # so we can answer "does this kept file have a hardlink?" in
    # O(1) per kept file rather than O(culled_tree) per check.
    culled_index: dict[str, list[Path]] = {}
    culled_files_total = 0
    for fp in culled.rglob("*"):
        if not fp.is_file():
            continue
        culled_files_total += 1
        culled_index.setdefault(fp.name, []).append(fp)
    report.culled_files = culled_files_total

    journal_kept_total = 0
    matched_culled_paths: set[Path] = set()

    for row in rows:
        decisions = _load_camera_decisions(event, row.camera_id)
        # F-029 video promotion: walk every per-camera journal,
        # synthesise STATE_KEPT for video sources whose journal
        # carries a kept clip / snapshot / whole-video mark. Mirrors
        # the silent-sync engine's pre-pass so audit verdict matches
        # sync behaviour. **Must run BEFORE the empty-decisions
        # early-return** (Nelson 2026-05-28): on a video-only camera
        # like the HERO12, all decisions live in the journals'
        # ``clips`` / ``snapshots`` arrays — ``marks`` is empty, so
        # ``_load_camera_decisions`` returns ``{}`` and the early-
        # return previously fired before F-029 could promote any
        # video to KEPT. Result: every HERO12 hardlink in 01-Culled
        # was wrongly flagged as orphan even though the silent-sync
        # correctly preserved them.
        cam_root = journal_root_for_camera(event, row.camera_id)
        if cam_root.is_dir():
            for journal_file in cam_root.rglob("ingest_journal.json"):
                try:
                    journal_data = load_ingest_journal(
                        journal_file.parent)
                except Exception:  # noqa: BLE001 — defensive
                    continue
                _apply_video_marks_to_decisions(journal_data, decisions)
        if not decisions:
            continue
        cam_safe = sanitize_folder_name(row.camera_id) or "_unknown_camera"
        bucket_safe = sanitize_folder_name(row.bucket)

        for fp in row.files:
            state = decisions.get(fp.name)
            if state != STATE_KEPT:
                continue
            journal_kept_total += 1

            # Expected hardlink lives somewhere under
            # 01 - Culled/<bucket>/<day_label>/<camera_id>/<style>/.
            # We don't pin style (classification-dependent), so
            # accept any path whose ancestor chain contains the
            # bucket/day/camera tuple.
            try:
                day_label = fp.parent.parent.name
            except IndexError:
                day_label = ""

            candidates = culled_index.get(fp.name, [])
            hit: Optional[Path] = None
            for cand in candidates:
                if cand in matched_culled_paths:
                    continue
                ancestors = {p.name for p in cand.parents}
                if (
                    bucket_safe in ancestors
                    and day_label in ancestors
                    and cam_safe in ancestors
                ):
                    hit = cand
                    break
            if hit is None:
                report.add(
                    SEVERITY_ERROR, PHASE_CULLED, "missing_hardlink",
                    f"Journal says KEPT but no hardlink exists in "
                    f"01 - Culled: {fp.name} (camera {row.camera_id},"
                    f" day {day_label}). Run rebuild to materialise.",
                    path=fp,
                )
            else:
                matched_culled_paths.add(hit)

    report.journal_kept_total = journal_kept_total

    # Orphan check — files under 01 - Culled that didn't match a
    # KEPT journal entry. Walk every file we found and report any
    # that wasn't claimed by a kept mark.
    #
    # Task #121 (Nelson 2026-05-23): the previous video clip/frame
    # carve-out (`_is_explicit_export`) was removed. Cull is now
    # binary K/D for video — raw kept videos hardlink to 01-Culled
    # like photos; clip extraction moved to Process. So every file
    # in 01-Culled is expected to be a hardlink and the simple
    # journal-claim check is the right rule for all media types.
    for paths in culled_index.values():
        for p in paths:
            if p in matched_culled_paths:
                continue
            report.add(
                SEVERITY_WARNING, PHASE_CULLED, "orphan_hardlink",
                f"File present in 01 - Culled but not marked KEPT in "
                f"any journal: {p.name}. Stale from a prior cull "
                f"session? Rebuild will not remove it.",
                path=p,
            )


def _audit_selected(
    event: Event, root: Path, selected: Path, report: AuditReport,
) -> None:
    """Compare the Select projection against the Select journal
    (task #115).

    The Select journal lives at ``<event_root>/.cull/select/`` —
    same ``ingest_journal.json`` shape as Cull's per-camera journals
    but a single scope (Select is consolidated across cameras).

    Rules:
      * Files marked KEPT in the Select journal must have a
        corresponding entry under ``02 - Selected/<day>/<style>/``.
      * Files under ``02 - Selected/`` must either trace back to a
        KEPT Select-journal entry or be an explicit export (video
        clip / frame extract — same carve-out as the Cull audit).
    """
    select_journal_root = root / ".cull" / "pick"
    journal_kept_names: set[str] = set()
    if select_journal_root.is_dir():
        # Nelson 2026-05-28: apply F-029 video promotion at audit
        # time, mirroring what ``_load_select_decisions`` +
        # ``_apply_video_marks_to_decisions`` do for the sync path.
        # Without this, videos that are kept via clips/snapshots
        # (whole-video discarded) are flagged as missing or orphan
        # even though the silent-sync correctly hardlinked them.
        from core.cull_phase_sync import _apply_video_marks_to_decisions
        from core.cull_state import STATE_KEPT

        for journal_file in select_journal_root.rglob("ingest_journal.json"):
            try:
                with open(journal_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            marks = data.get("marks") or {}
            if not isinstance(marks, dict):
                continue
            for name, state in marks.items():
                if state == STATE_KEPT:
                    journal_kept_names.add(str(name))
            # Synthesise STATE_KEPT for video sources with kept
            # derivatives (clips/snapshots). Mutating a throwaway
            # dict so we can extract the synthesised names.
            synth: dict = {}
            _apply_video_marks_to_decisions(data, synth)
            for name, state in synth.items():
                if state == STATE_KEPT:
                    journal_kept_names.add(str(name))

    report.select_kept_total = len(journal_kept_names)

    if not selected.is_dir():
        if journal_kept_names:
            report.add(
                SEVERITY_ERROR, PHASE_PICKED, "missing_selected_tree",
                "Select journal marks files KEPT but 02 - Selected "
                "does not exist on disk. Run Select-Export.",
                path=selected,
            )
        elif select_journal_root.is_dir():
            report.add(
                SEVERITY_INFO, PHASE_PICKED, "no_selected_tree",
                "02 - Selected does not exist (nothing selected yet).",
                path=selected,
            )
        return

    # Build a name → list[path] index over the entire selected tree.
    selected_index: dict[str, list[Path]] = {}
    selected_files_total = 0
    for fp in selected.rglob("*"):
        if not fp.is_file():
            continue
        selected_files_total += 1
        selected_index.setdefault(fp.name, []).append(fp)
    report.selected_files = selected_files_total

    matched_paths: set[Path] = set()

    # Missing-file check — every KEPT name should have at least one
    # match in the selected tree.
    for name in journal_kept_names:
        candidates = selected_index.get(name, [])
        # Files in Select-Export get a courtesy filename prefix
        # (DateTimeOriginal_<stem>), so also accept a name whose
        # SUFFIX matches the journal name. Loose but useful.
        if not candidates:
            for cname, paths in selected_index.items():
                if cname.endswith(name):
                    candidates = paths
                    break
        if not candidates:
            report.add(
                SEVERITY_ERROR, PHASE_PICKED, "missing_selected_file",
                f"Select journal says KEPT but no file in "
                f"02 - Selected: {name}. Run Select-Export.",
            )
        else:
            # Mark the first unmatched candidate as paired.
            for c in candidates:
                if c not in matched_paths:
                    matched_paths.add(c)
                    break

    # Orphan check — files in 02 - Selected with no journal mark.
    # Task #121: no media-type carve-out; everything in 02-Selected
    # is expected to be a hardlink with a matching KEPT journal entry
    # (the binary-K/D video model). Clip/still creation moved to
    # Process; those land in 03-Processed and aren't checked here.
    for paths in selected_index.values():
        for p in paths:
            if p in matched_paths:
                continue
            report.add(
                SEVERITY_WARNING, PHASE_PICKED, "orphan_selected",
                f"File present in 02 - Selected but not marked KEPT "
                f"in the Select journal: {p.name}. Stale from a prior "
                f"Select session?",
                path=p,
            )


# NOTE: ``_is_explicit_export`` (and its FRAME_EXTRACT regex) were
# removed in task #121 (Nelson 2026-05-23). The video Cull/Select
# surface became binary K/D — raw kept videos hardlink to 01/02 like
# photos, no clip exports in those phases. So every file in 01/02 is
# expected to be a hardlink with a matching KEPT journal entry; the
# old media-extension carve-out is no longer needed.


# ── Rebuild ──────────────────────────────────────────────────────


def _rebuild_culled(event: Event, rb: RebuildReport) -> None:
    """Fire the silent-sync code path per discovered camera. Same
    mechanism the cull shell uses on phase exit — single source of
    truth for journal → 01 - Culled materialisation."""
    from core.cull_dashboard import discover_cameras
    from core.cull_phase_sync import sync_camera_kept_to_culled

    rows = discover_cameras(event)
    for row in rows:
        try:
            result = sync_camera_kept_to_culled(event, row.camera_id)
        except Exception as exc:        # noqa: BLE001 — defensive
            rb.errors.append((
                Path(row.camera_id),
                f"rebuild raised for {row.camera_id}: {exc}",
            ))
            log.exception(
                "rebuild_event: sync raised for camera %s",
                row.camera_id,
            )
            continue
        rb.cameras_synced += 1
        rb.files_materialised += result.ok_count
        rb.errors.extend(result.errors)

    log.info(
        "rebuild_event: event=%s cameras=%d files=%d errors=%d",
        event.name or event.id,
        rb.cameras_synced,
        rb.files_materialised,
        len(rb.errors),
    )


def _rebuild_selected(event: Event, rb: RebuildReport) -> None:
    """Fire the Select silent-sync (Nelson 2026-05-28). Same engine
    the bucket-cull-shell uses on Select phase exit — walks the
    consolidated Select journal, hardlinks every KEPT name from
    01-Culled into 02-Selected, removes orphan files (including
    UNIQUE-collision ``(N)`` duplicates from earlier
    pre-00.093 / pre-00.098 silent-sync bugs), and replaces stale
    hardlinks whose source inodes changed (e.g., after Adjust TZ
    rewrote the EXIF in 00-Captured and propagated through to
    01-Culled). One single source of truth — same code path the
    on-phase-exit auto-reconcile fires, so Audit-rebuild can't
    diverge from normal usage.

    Also runs ``cleanup_stale_select_kepts`` (Nelson 2026-05-28
    post-Nepal): demotes KEPT entries in the Select journal whose
    source file no longer exists under 01-Culled. Closes the loop
    on ``missing_selected_file`` audit findings caused by stale
    ``(N)``-suffix names left over from the pre-00.093 duplicate
    phase."""
    from core.cull_phase_sync import (
        cleanup_stale_select_kepts,
        sync_kept_to_selected,
    )

    # Stale-KEPT cleanup BEFORE sync: ensure the sync sees a journal
    # whose KEPT set matches files actually on disk in 01-Culled, so
    # orphan-removal + additive pass operate on a coherent snapshot.
    try:
        cleanup_stale_select_kepts(event)
    except Exception:                  # noqa: BLE001 — defensive
        log.exception("rebuild_event: stale-KEPT cleanup raised")

    try:
        result = sync_kept_to_selected(event)
    except Exception as exc:        # noqa: BLE001 — defensive
        rb.errors.append((
            Path("02 - Selected"),
            f"select rebuild raised: {exc}",
        ))
        log.exception("rebuild_event: select-sync raised")
        return
    rb.files_materialised += result.ok_count
    rb.errors.extend(result.errors)
    log.info(
        "rebuild_event: select-sync ok=%d orphans_removed=%d "
        "errors=%d",
        result.ok_count,
        getattr(result, "orphans_removed", 0),
        len(result.errors),
    )
