"""Resume-map cull statistics for the bucket navigator.

docs/18 §"Culling contexts" (frozen 2026-05-17, Nelson): the
Day→Bucket selection lists are a *resume map* — every entry shows,
on its face, where the user is and what is left, so any
interruption is recoverable at a glance. This module is the pure,
journal-derived model behind that (the chart widget paints a
``CullStats``; the navigator rolls them up per day).

Two things, deliberately distinct (the "good catch"):

* **The tally** — kept / compare / discard counts over the
  bucket's universe via ``cull_state.state_counts``. Always exact.
  The journal is *deliberately sparse* (a file at the bucket
  default has no entry), so "untouched" is **not** a per-file
  slice — the tally is the faithful 3-state distribution, full
  stop.
* **The badge state** — ``untouched`` / ``in_progress`` / ``done``,
  where **done is USER-DECLARED and reversible** (the
  ``cull_state`` ``reviewed`` flag), never inferred. ``in_progress``
  ⇔ not done and ≥1 explicit mark. ``untouched`` ⇔ not done and no
  explicit marks.

Pure / Qt-free / never raises — opening the navigator must never
re-cull or re-score (Speed is King; peek-don't-compute, same
discipline as the grid). The chart *widget* lives in ``ui/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from core.cull_state import (
    STATE_CANDIDATE,
    STATE_DISCARDED,
    STATE_KEPT,
    has_explicit_marks,
    is_bucket_browsed,
    is_bucket_reviewed,
    state_counts,
)
from core.video_discovery import VIDEO_EXTENSIONS

# Badge states (string constants — the UI maps them to the
# PhaseButton-style status palette). Four-state ladder (Nelson
# 2026-05-18): untouched → browsed (opened, no mark) → in_progress
# (≥1 mark) → done (user-declared).
BADGE_UNTOUCHED = "untouched"
BADGE_BROWSED = "browsed"
BADGE_IN_PROGRESS = "in_progress"
BADGE_DONE = "done"

# Rollup-classification states for the StatusBreakdown widget +
# any upstream summary surfaces (B-026, Nelson 2026-05-27).
# Distinct from the cull_state enum: the cull model is 3-state
# (kept / candidate / discarded) at the file level with no
# "untouched" slot — the bucket default folds unmarked files into
# discarded. The rollup needs an explicit "untouched" so a fresh
# video bucket reads as 0/0/N instead of 0/0/N-defaulted-to-red.
ROLLUP_KEPT = "kept"
ROLLUP_DISCARDED = "discarded"
ROLLUP_UNTOUCHED = "untouched"


@dataclass(frozen=True)
class CullStats:
    """One bucket's resume-map figure.

    ``kept``/``candidate``/``discarded`` sum to ``total`` (the
    faithful 3-state distribution — paint these in the cull-state
    palette). ``badge`` is the user-facing state. ``reviewed`` is
    the raw user-declared flag (badge == DONE iff this is True).

    **F-034 (Nelson 2026-05-28) — time-weighted bars.** The integer
    counts above carry **file-count semantics** ("3 of 5 kept");
    they're authoritative for tooltips, log lines, and the
    StatusBreakdown's text labels. The ``*_weight`` floats below
    carry **time-weighted semantics** for the bar lengths in the
    StatusBreakdown — a 5-second clip from a 60-second video
    contributes 1/12 of a unit, not 1 unit. For photos +
    legacy-clip data (no duration stamped) the weight equals the
    count so the new behaviour degrades gracefully to today's
    file-count bars. The semantics are deliberately split so the
    user sees honest "what fraction of the source survived"
    bars without losing the "N of M derivatives kept" tooltip.
    """

    total: int
    kept: int
    candidate: int
    discarded: int
    reviewed: bool
    badge: str
    browsed: bool = False           # user opened it, no mark yet
    # F-034: time-weighted contributions for bar rendering.
    # Default to the integer counts so legacy callers that don't
    # pass weights see today's file-count-proportional bars.
    kept_weight: float = -1.0
    candidate_weight: float = -1.0
    discarded_weight: float = -1.0

    def __post_init__(self) -> None:        # noqa: D401
        # Backfill the weight defaults from the counts when the
        # caller didn't supply them — keeps legacy CullStats(...)
        # constructions binary-equivalent.
        if self.kept_weight < 0:
            object.__setattr__(self, "kept_weight", float(self.kept))
        if self.candidate_weight < 0:
            object.__setattr__(
                self, "candidate_weight", float(self.candidate))
        if self.discarded_weight < 0:
            object.__setattr__(
                self, "discarded_weight", float(self.discarded))

    @property
    def progress(self) -> float:
        """0.0–1.0 convenience for a ring/bar fill. 1.0 when the
        user declared the bucket done; otherwise the kept+compare
        share (what has been *positively* triaged off the discard
        default). The authoritative readout is the tally itself —
        this is only a glanceable fill hint."""
        if self.reviewed:
            return 1.0
        if self.total <= 0:
            return 0.0
        return (self.kept + self.candidate) / self.total

    @property
    def is_empty(self) -> bool:
        return self.total <= 0

    @property
    def total_weight(self) -> float:
        """The denominator for time-weighted bar rendering. Sum of
        kept + candidate + discarded weights (rather than ``total``
        as an int) so the StatusBreakdown widget can paint bars
        whose lengths sum to a meaningful 1.0 (= one unit per file
        for photos / whole-video kept; less per partial-clip
        contribution)."""
        return (self.kept_weight
                + self.candidate_weight
                + self.discarded_weight)


def _is_video_filename(name: str) -> bool:
    """True iff ``name`` looks like a video file by extension. Used
    by the rollup classifier to branch between the photo 3-state
    fold and the F-029 video-derivative classification."""
    suffix = ""
    dot = name.rfind(".")
    if dot >= 0:
        suffix = name[dot:].lower()
    return suffix in VIDEO_EXTENSIONS


# F-034 (Nelson 2026-05-28): each kept snapshot contributes the
# equivalent of this many seconds of source video to the time-
# weighted Kept bar. Default 10 s matches a "typical attention
# span on one slideshow image" and is documented in docs/18
# §"Time-weighted Kept tally". Constant rather than a setting
# because it's a display heuristic, not user preference; if the
# heuristic feels wrong in practice we can promote it.
STILL_EQUIVALENT_SECONDS = 10


def video_rollup_weight(
    journal: dict,
    video_name: str,
    *,
    still_seconds: int = STILL_EQUIVALENT_SECONDS,
) -> float:
    """Time-weighted "Kept" contribution for one video file —
    a float in ``[0.0, 1.0]``.

    Frozen 2026-05-28 (Nelson, F-034). Rule:

    * **Whole-video kept (binary K/D pill)** ⇒ ``1.0``. The
      user said "all of it"; no fractional math needed.
    * **Otherwise**: ``min(1.0, (Σ clip_durations + still_seconds
      × kept_snap_count) / source_duration_ms)``. Mixing clip
      time and snap-equivalent time onto one denominator gives a
      single honest fraction of how much of the source survived.
      Capped at 1.0 so overlapping clips (legitimate user
      intent) can't make a 60-second video read as 1.3 units.
    * **No source_duration_ms stamped** (legacy clip / pre-F-034
      journal): fall back to **binary** weighting — 1.0 if any
      kept derivative exists, 0.0 otherwise. Same as
      :func:`classify_video_for_rollup` returns
      ``ROLLUP_KEPT``/``ROLLUP_DISCARDED``. This keeps old data
      visually unchanged; new data benefits from the fractional
      bars without a migration step.
    * **Whole video discarded with no kept derivatives** ⇒
      ``0.0``.

    Pure / Qt-free / never raises — a malformed journal degrades
    to ``0.0``. Photos don't go through this function; they're a
    direct 1-unit contribution computed by the caller."""
    from core import video_marks

    try:
        marks = journal.get("marks") or {}
        if (isinstance(marks, dict)
                and marks.get(video_name) == STATE_KEPT):
            return 1.0
    except Exception:                                        # noqa: BLE001
        return 0.0

    try:
        kept_clips = [
            c for c in video_marks.list_clips(journal)
            if c.source == video_name and c.is_kept
        ]
        kept_snaps = [
            s for s in video_marks.list_snapshots(journal)
            if s.source == video_name and s.is_kept
        ]
    except Exception:                                        # noqa: BLE001
        return 0.0

    if not kept_clips and not kept_snaps:
        return 0.0

    # Pick a representative source_duration_ms — first kept clip
    # or first kept snap that has it stamped. If NONE have it
    # (all legacy entries), fall back to binary 1.0 (matches
    # classify_video_for_rollup == ROLLUP_KEPT).
    duration_ms = 0
    for entry in (*kept_clips, *kept_snaps):
        if entry.source_duration_ms > 0:
            duration_ms = entry.source_duration_ms
            break
    if duration_ms <= 0:
        return 1.0

    clip_ms = sum(c.duration_ms for c in kept_clips)
    snap_ms = max(0, int(still_seconds)) * 1000 * len(kept_snaps)
    fraction = (clip_ms + snap_ms) / duration_ms
    if fraction < 0:
        return 0.0
    return min(1.0, fraction)


def classify_video_for_rollup(
    journal: dict, video_name: str,
) -> str:
    """Classify one video file for rollup display purposes.

    Returns one of :data:`ROLLUP_KEPT` / :data:`ROLLUP_DISCARDED` /
    :data:`ROLLUP_UNTOUCHED`.

    **Spec (Nelson 2026-05-27, F-032 / B-026).** Aligned with the
    F-029 silent-sync rule: what flows forward IS what reads as
    kept.

      * **kept** iff :func:`core.video_marks.has_any_kept_derivative`
        returns True — whole-video kept OR any kept clip OR any
        kept snap. Includes the "user just created a clip"
        case, since clips default to ``state="kept"`` on
        creation (F-029 Step 2 freeze).
      * **untouched** iff no whole-video mark AND no clip
        definitions AND no snapshot definitions. Truly nothing.
      * **discarded** otherwise — explicit DISCARDED whole-video
        mark with no kept derivative, OR clips/snaps exist but
        all are discarded (user created, reviewed, decided no).

    Pure / Qt-free / never raises — a malformed journal degrades
    to ``ROLLUP_UNTOUCHED``."""
    # Lazy import — avoid circular dependency with video_marks
    # (which imports nothing from this module).
    from core import video_marks

    try:
        if video_marks.has_any_kept_derivative(journal, video_name):
            return ROLLUP_KEPT
    except Exception:                                            # noqa: BLE001
        # Degrade to untouched on a malformed journal — never raise
        # from a display-side classifier.
        return ROLLUP_UNTOUCHED

    marks = journal.get("marks")
    has_mark = (
        isinstance(marks, dict) and video_name in marks
    )
    try:
        clips_for_source = [
            c for c in video_marks.list_clips(journal)
            if c.source == video_name
        ]
        snaps_for_source = [
            s for s in video_marks.list_snapshots(journal)
            if s.source == video_name
        ]
    except Exception:                                            # noqa: BLE001
        return ROLLUP_UNTOUCHED

    if not has_mark and not clips_for_source and not snaps_for_source:
        return ROLLUP_UNTOUCHED
    return ROLLUP_DISCARDED


def has_video_interaction(
    journal: dict, video_names: Iterable[str],
) -> bool:
    """True iff any of the supplied video files has any user
    interaction in the journal (whole-video mark OR any clip OR
    any snap, regardless of K/D state). Used by the badge logic
    in :func:`compute_cull_stats` to surface "touched" status for
    video-bearing buckets that would otherwise read as Untouched
    (since photos-only ``has_explicit_marks`` misses video
    derivatives)."""
    for name in video_names:
        if classify_video_for_rollup(journal, name) != ROLLUP_UNTOUCHED:
            return True
    return False


def compute_cull_stats(
    journal: dict,
    filenames: Iterable[str],
    *,
    bucket_key: Optional[str] = None,
) -> CullStats:
    """Resume-map stats for one bucket. ``journal`` is the cull
    journal (sparse marks + default + soft-state); ``filenames`` is
    the bucket's universe (the navigator already has it from the
    scan — never re-walk disk here).

    ``bucket_key`` selects the per-bucket soft-state of a **shared**
    day journal — pass it for buckets that share one journal (the
    Moment clusters + the residual Individuals, frozen 2026-05-18):
    `browsed`/`reviewed` then read the content-keyed sub-map and
    `has_explicit_marks` is scoped to *these* files, so each Moment
    view's badge is its own. ``None`` (default) = a bucket with its
    own journal (bracket/burst/video) — the legacy global flags;
    every existing caller is unchanged.

    **Video-aware (B-026, Nelson 2026-05-27, F-032 Spec B).** Files
    classified as video by extension are scored via
    :func:`classify_video_for_rollup` (F-029-aligned: whole-video
    mark OR any kept derivative ⇒ kept; no mark + no derivatives ⇒
    untouched-folded-into-discarded; otherwise discarded). Photos
    keep the legacy 3-state ``state_counts`` path. A video bucket
    with at least one kept/discarded derivative is "touched" — the
    badge promotes to IN_PROGRESS even if photo marks are absent.

    Never raises: a missing/garbled journal degrades to all-default
    (a display model must never crash the navigator)."""
    names = list(filenames)
    total = len(names)

    # Split by extension — videos go through the F-029-aligned
    # classifier, photos through the legacy 3-state counter.
    photo_names: list[str] = []
    video_names: list[str] = []
    for name in names:
        if _is_video_filename(name):
            video_names.append(name)
        else:
            photo_names.append(name)

    counts = state_counts(journal, photo_names) if photo_names else {}
    kept = int(counts.get(STATE_KEPT, 0))
    cand = int(counts.get(STATE_CANDIDATE, 0))
    disc = int(counts.get(STATE_DISCARDED, 0))

    # F-034: weights start as floats from the photo counts (each
    # photo = 1 unit of contribution to its column) and are then
    # augmented with the time-weighted video contributions.
    kept_w = float(kept)
    cand_w = float(cand)
    disc_w = float(disc)

    for vname in video_names:
        rollup = classify_video_for_rollup(journal, vname)
        if rollup == ROLLUP_KEPT:
            kept += 1
        else:
            # ROLLUP_DISCARDED + ROLLUP_UNTOUCHED both fold into
            # the discard count — matches the photo bucket default,
            # where unmarked-at-discard-default reads as discarded.
            # The "touched" distinction is in the badge below.
            disc += 1
        # F-034: each video contributes ONE unit of mass to the
        # weighted bars, split between kept_w and disc_w by the
        # actual fraction of the source that survived. A 5-second
        # clip from a 60-second video adds 5/60 to kept_w and
        # 55/60 to disc_w — honest representation of "how much of
        # the source survived" without inventing fractional file
        # counts.
        w = video_rollup_weight(journal, vname)
        if w < 0:
            w = 0.0
        if w > 1:
            w = 1.0
        kept_w += w
        disc_w += (1.0 - w)

    reviewed = is_bucket_reviewed(journal, bucket_key)
    browsed = is_bucket_browsed(journal, bucket_key)
    photo_marks = (has_explicit_marks(journal, photo_names)
                   if bucket_key is not None
                   else has_explicit_marks(journal))
    video_touch = (has_video_interaction(journal, video_names)
                   if video_names else False)
    marks = photo_marks or video_touch
    if reviewed:
        badge = BADGE_DONE
    elif marks:
        badge = BADGE_IN_PROGRESS
    elif browsed:
        badge = BADGE_BROWSED
    else:
        badge = BADGE_UNTOUCHED

    return CullStats(
        total=total,
        kept=kept,
        candidate=cand,
        discarded=disc,
        reviewed=reviewed,
        badge=badge,
        browsed=browsed,
        kept_weight=kept_w,
        candidate_weight=cand_w,
        discarded_weight=disc_w,
    )


def rollup(stats: list[CullStats]) -> CullStats:
    """Day-level rollup across its buckets. ``total`` and the three
    tallies sum; the day is ``done`` only when **every** non-empty
    bucket is user-declared done (docs/18: "a day reads done only
    when every bucket is done"); else ``in_progress`` if any has a
    mark or is done; else ``untouched``. ``reviewed`` mirrors the
    all-done condition for ``progress``."""
    real = [s for s in stats if not s.is_empty]
    total = sum(s.total for s in real)
    kept = sum(s.kept for s in real)
    cand = sum(s.candidate for s in real)
    disc = sum(s.discarded for s in real)
    # F-034: weights sum across buckets the same way counts do.
    kept_w = sum(s.kept_weight for s in real)
    cand_w = sum(s.candidate_weight for s in real)
    disc_w = sum(s.discarded_weight for s in real)

    if real and all(s.badge == BADGE_DONE for s in real):
        badge, reviewed, browsed = BADGE_DONE, True, False
    elif any(s.badge in (BADGE_IN_PROGRESS, BADGE_DONE)
             for s in real):
        badge, reviewed, browsed = BADGE_IN_PROGRESS, False, False
    elif any(s.badge == BADGE_BROWSED for s in real):
        badge, reviewed, browsed = BADGE_BROWSED, False, True
    else:
        badge, reviewed, browsed = BADGE_UNTOUCHED, False, False

    return CullStats(
        total=total, kept=kept, candidate=cand, discarded=disc,
        reviewed=reviewed, badge=badge, browsed=browsed,
        kept_weight=kept_w, candidate_weight=cand_w,
        discarded_weight=disc_w,
    )
