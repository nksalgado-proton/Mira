"""Closed-event recap stats (F-025, Nelson 2026-05-26).

The closed-event surface (EventCard on the events list +
EventPlanPage's recap view) no longer shows phase-progress
heatmaps. Closed events are about looking back at what happened —
this module computes the stats that tell that story:

* **Funnel counts** — Captured → Culled → Selected → Processed →
  Curated. Carried forward from ``core.event_metrics``; surfaced
  here so the closed view consumes a single bundle.
* **Cameras used** — `(camera_name, photo_count)` pairs sorted
  by contribution. Caller renders the top-N.
* **Slideshow tier durations** — Short / Medium / Long file count
  × per-tier seconds-per-slide from settings → minutes. Each tier
  carries its own pace so the duration reflects how the user
  actually plays back that tier.
* **All-Time Best by preferred style** — preferred genres from
  settings (`preferred_genres`), each broken down: how many of
  the user's "all-time best" picks came from each preferred theme
  bucket. Computed by stem-intersecting the All-Time Best folder
  against each theme folder under `04 - Curated/`.
* **Distribution channels** — pulled straight from
  `event.distribution_log`.

Pure-Python; no Qt; walks the filesystem under ``04 - Curated/``
and reads `event.event_settings` / `settings.json`. Defensive on
every disk read — missing trees return zero / empty lists; never
raises.

Random cover-photo picker for the EventCard sits here too —
:func:`pick_random_curated_photo`. Picks one photo per render
from Short → Medium → Long fallback chain so the card surface
feels alive without storing per-event "hero" state.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.cull_state import STATE_KEPT
from core.event_metrics import (
    captured_photos_per_camera,
    kept_in_cull_count,
    kept_in_curate_count,
    kept_in_process_count,
    kept_in_select_count,
    total_captured_photos,
)
from core.models import Event
from core.path_builder import (
    CURATED_DIR_NAME,
    captured_dir,
    culled_dir,
    curated_dir,
    event_root_path,
    processed_dir,
    selected_dir,
)

log = logging.getLogger(__name__)


# Slideshow tier folder names — match the export layout in
# ``core.curate_export`` (``FOLDER_SHORT`` / ``FOLDER_MEDIUM`` /
# ``FOLDER_LONG`` are imported indirectly via the on-disk strings
# to keep this module Qt-free + decoupled).
TIER_SHORT = "Short"
TIER_MEDIUM = "Medium"
TIER_LONG = "Long"
ALL_TIME_BEST = "All-Time Best"

# Tier-name → settings key for the seconds-per-slide value. Used
# by :func:`slideshow_tier_durations`. Default values mirror what
# ``core/settings.py`` HARD_DEFAULT_SETTINGS ships.
_TIER_SETTINGS_KEY = {
    TIER_SHORT:  "slideshow_seconds_per_slide_short",
    TIER_MEDIUM: "slideshow_seconds_per_slide_medium",
    TIER_LONG:   "slideshow_seconds_per_slide_long",
}
_TIER_DEFAULT_SECONDS = {
    TIER_SHORT:  4.0,
    TIER_MEDIUM: 6.0,
    TIER_LONG:   6.0,
}

# Photo extensions — share with event_metrics' definition but
# import-decoupled so this module's tests don't require pulling
# the whole metrics tree.
_PHOTO_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".heic", ".heif",
    ".tif", ".tiff", ".webp",
    ".rw2", ".raf", ".nef", ".arw", ".cr2", ".cr3",
    ".dng", ".orf",
})


@dataclass(frozen=True)
class TierDuration:
    """One slideshow tier's contribution to the recap."""
    tier: str            # "Short" / "Medium" / "Long"
    file_count: int
    seconds_per_slide: float

    @property
    def total_minutes(self) -> float:
        return (self.file_count * self.seconds_per_slide) / 60.0


@dataclass(frozen=True)
class ClosedEventStats:
    """Bundle of recap stats for the closed EventCard +
    EventPlanPage. Computed in one disk walk; consumed by both
    surfaces so the numbers can't disagree."""
    funnel: dict[str, int] = field(default_factory=dict)
    cameras: tuple[tuple[str, int], ...] = ()
    tier_durations: tuple[TierDuration, ...] = ()
    best_total: int = 0
    best_by_preferred: tuple[tuple[str, int], ...] = ()
    distribution_channels: tuple[str, ...] = ()


# ── Core computation ──────────────────────────────────────────


def compute_closed_event_stats(
    event: Event,
    *,
    settings: Optional[dict] = None,
) -> ClosedEventStats:
    """Compute every stat the closed view needs in one pass.

    ``settings`` is the loaded settings dict (caller does
    ``core.settings.load_settings()``); ``None`` lets us fall back
    to per-tier defaults without forcing the caller to set up the
    settings layer just for this read.
    """
    base = (event.photos_base_path or "").strip()
    if not base:
        return ClosedEventStats()
    try:
        root = event_root_path(base, event)
    except (TypeError, ValueError, OSError):
        return ClosedEventStats()
    if not root.is_dir():
        return ClosedEventStats()

    # Funnel — reuses the existing event_metrics walks.
    funnel = {
        "captured":  total_captured_photos(root),
        "culled":    kept_in_cull_count(root),
        "selected":  kept_in_select_count(root),
        "processed": kept_in_process_count(root),
        "curated":   kept_in_curate_count(root),
    }

    # Cameras → photo counts, sorted by contribution desc.
    cam_dict = captured_photos_per_camera(root)
    cameras = tuple(
        sorted(cam_dict.items(), key=lambda kv: (-kv[1], kv[0]))
    )

    # Slideshow tier durations.
    cur_root = curated_dir(root)
    tier_durations = _compute_tier_durations(cur_root, settings)

    # All-Time Best total + per-preferred-style breakdown.
    best_total, best_by_preferred = _compute_best_breakdown(
        cur_root, settings)

    # Distribution channels — just the unique channel names from
    # the event's log, sorted for stable rendering.
    channels: set[str] = set()
    for action in (event.distribution_log or []):
        ch = (action.channel or "").strip()
        if ch:
            channels.add(ch)

    return ClosedEventStats(
        funnel=funnel,
        cameras=cameras,
        tier_durations=tier_durations,
        best_total=best_total,
        best_by_preferred=best_by_preferred,
        distribution_channels=tuple(sorted(channels)),
    )


def _compute_tier_durations(
    cur_root: Path,
    settings: Optional[dict],
) -> tuple[TierDuration, ...]:
    """Walk Short / Medium / Long under 04-Curated, compute each
    tier's duration at the configured seconds-per-slide."""
    out: list[TierDuration] = []
    for tier in (TIER_SHORT, TIER_MEDIUM, TIER_LONG):
        tier_dir = cur_root / tier
        if tier_dir.is_dir():
            file_count = _count_photos_recursive(tier_dir)
        else:
            file_count = 0
        sps = _tier_seconds(tier, settings)
        out.append(TierDuration(
            tier=tier,
            file_count=file_count,
            seconds_per_slide=sps,
        ))
    return tuple(out)


def _tier_seconds(tier: str, settings: Optional[dict]) -> float:
    """Per-tier seconds-per-slide. Looks up the per-tier settings
    key; falls back to the per-tier default when no setting (or
    no settings dict at all) is available."""
    default = _TIER_DEFAULT_SECONDS.get(tier, 3.0)
    if not settings:
        return default
    key = _TIER_SETTINGS_KEY.get(tier)
    if not key:
        return default
    try:
        value = float(settings.get(key, default))
    except (TypeError, ValueError):
        return default
    # Clamp to a sane range (matches the CurateBrowsePage spinner).
    return max(0.5, min(60.0, value))


def _compute_best_breakdown(
    cur_root: Path,
    settings: Optional[dict],
) -> tuple[int, tuple[tuple[str, int], ...]]:
    """``(total_best, [(theme_title, count), ...])`` for the
    user's preferred genres.

    Stem-intersect: a file in All-Time Best whose stem also appears
    in ``<preferred_genre.title()>/`` counts toward that theme.
    A best photo may belong to multiple themes if the user tagged
    it across more than one — counted in each.

    Empty list when no preferred_genres setting OR no
    All-Time Best folder.
    """
    best_dir = cur_root / ALL_TIME_BEST
    if not best_dir.is_dir():
        return (0, ())

    best_stems = {
        f.stem.lower() for f in best_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in _PHOTO_EXTS
    }
    best_total = len(best_stems)
    if best_total == 0:
        return (0, ())

    preferred_raw = (settings or {}).get("preferred_genres") or []
    if not isinstance(preferred_raw, list):
        return (best_total, ())
    breakdown: list[tuple[str, int]] = []
    for theme in preferred_raw:
        theme_title = str(theme).strip().title()
        if not theme_title:
            continue
        theme_dir = cur_root / theme_title
        if not theme_dir.is_dir():
            breakdown.append((theme_title, 0))
            continue
        theme_stems = {
            f.stem.lower() for f in theme_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in _PHOTO_EXTS
        }
        breakdown.append((theme_title, len(best_stems & theme_stems)))
    return (best_total, tuple(breakdown))


def _count_photos_recursive(d: Path) -> int:
    try:
        return sum(
            1 for f in d.rglob("*")
            if f.is_file() and f.suffix.lower() in _PHOTO_EXTS
        )
    except OSError:
        return 0


# ── Cover-photo picker ────────────────────────────────────────


def pick_random_curated_photo(
    event: Event,
    *,
    rng: Optional[random.Random] = None,
) -> Optional[Path]:
    """One random photo for the closed EventCard's cover area.

    Source fallback chain: Short → Medium → Long. Picks from the
    first tier that has any photos; returns None when no tier
    has any (the card falls back to a no-image placeholder).

    Per Nelson 2026-05-26: "one photo, chosen randomly every time
    the page is displayed". Caller invokes this per render — the
    visual rotates each time the user opens / refreshes the
    events list.

    ``rng`` is injectable for tests. Default uses the module-level
    ``random.choice``.
    """
    base = (event.photos_base_path or "").strip()
    if not base:
        return None
    try:
        root = event_root_path(base, event)
    except (TypeError, ValueError, OSError):
        return None
    cur_root = curated_dir(root)
    if not cur_root.is_dir():
        return None

    pick = (rng or random).choice if rng else random.choice
    for tier in (TIER_SHORT, TIER_MEDIUM, TIER_LONG):
        tier_dir = cur_root / tier
        if not tier_dir.is_dir():
            continue
        try:
            candidates = [
                f for f in tier_dir.rglob("*")
                if f.is_file() and f.suffix.lower() in _PHOTO_EXTS
            ]
        except OSError:
            continue
        if candidates:
            try:
                return pick(candidates)
            except IndexError:
                continue
    return None


# ── F-026 — last-completed-phase helpers ──────────────────────


# Pipeline phases in furthest → earliest order. Used to find the
# last DONE phase via ``phase_status_for``.
#
# Note (Nelson 2026-05-26): the walk keys are past-tense
# (folder-named: "curated", "processed", etc.) — that matches the
# on-disk folder taxonomy + this module's local style routing. The
# phase_progress cache uses verb-form keys ("curate", "process",
# etc.); we translate via _PROGRESS_KEY_FOR. Keeping the two
# vocabularies separate honours each module's existing convention
# rather than forcing one to bend.
_PHASE_WALK_ORDER: tuple[tuple[str, str], ...] = (
    ("curated",   "Curated"),
    ("processed", "Processed"),
    ("selected",  "Selected"),
    ("culled",    "Culled"),
    ("captured",  "Captured"),
)

# Walk-key → phase_progress key. "captured" maps to itself because
# phase_status_for treats it specially (filesystem-driven, no
# cache).
_PROGRESS_KEY_FOR: dict[str, str] = {
    "curated":   "curate",
    "processed": "process",
    "selected":  "pick",
    "culled":    "cull",
    "captured":  "capture",
}


def _phase_dir(event_root: Path, phase_key: str) -> Optional[Path]:
    """Resolve the on-disk parent directory for a phase key. Returns
    ``None`` for an unknown key."""
    if phase_key == "curated":
        return curated_dir(event_root)
    if phase_key == "processed":
        return processed_dir(event_root)
    if phase_key == "selected":
        return selected_dir(event_root)
    if phase_key == "culled":
        return culled_dir(event_root)
    if phase_key == "captured":
        return captured_dir(event_root)
    return None


def last_completed_phase(
    event: Event,
) -> Optional[tuple[str, str, Path]]:
    """Return ``(phase_key, phase_label, phase_root)`` for the
    furthest-along phase whose **status is DONE** (Nelson
    2026-05-26 post-00.047: "the phases have a status — Done /
    Ready / In Progress — you should take the info from the last
    Done Phase").

    The phase status is computed by
    :func:`core.phase_progress.phase_status_for`, which centralises
    the B-018 rule ("touched days all complete; no partials
    remaining"). Skipping over IN_PROGRESS / READY phases means
    the pie + photo quadrant on the EventPlanPage land on the
    phase the user actually FINISHED, not just the furthest one
    that happens to have any files lying around.

    Walks 04-Curated → 03-Processed → 02-Selected → 01-Culled →
    00-Captured and returns the first phase whose status is
    DONE. If no phase is Done (a fresh event, an in-flight one,
    etc.), returns ``None`` — callers render the empty-state
    branch.
    """
    base = (event.photos_base_path or "").strip()
    if not base:
        return None
    try:
        root = event_root_path(base, event)
    except (TypeError, ValueError, OSError):
        return None
    if not root.is_dir():
        return None

    from core.phase_progress import is_phase_done

    for phase_key, label in _PHASE_WALK_ORDER:
        phase_root = _phase_dir(root, phase_key)
        if phase_root is None:
            continue
        progress_key = _PROGRESS_KEY_FOR[phase_key]
        # Pass event_root so the capture-phase check has its
        # filesystem-walk input.
        if is_phase_done(event, progress_key, event_root=root):
            return (phase_key, label, phase_root)
    return None


def _has_any_photo(root: Path) -> bool:
    """Cheap check: does *any* descendant under ``root`` look like
    a photo file?  Walks recursively; returns on the first hit."""
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in _PHOTO_EXTS:
                return True
    except OSError:
        return False
    return False


def style_breakdown_last_phase(
    event: Event,
) -> tuple[tuple[tuple[str, int], ...], str]:
    """``((style_label, count), ...), phase_display_label``.

    **Reads from JOURNALS, not from the filesystem projection**
    (Nelson 2026-05-26, post-00.046 correction). The folder
    structure under ``04 - Curated/`` / ``03 - Processed/`` /
    ``02 - Selected/`` / ``01 - Culled/`` is *derived* from the
    journals — using folder names as the style signal mixed in
    accidental tier folders (Short/Medium/Long) and missed
    journal-only signals (per-photo genre overrides, curate tag
    parsing, etc.). Journals are the source of truth.

    Source per phase:

      * **curated** — ``04 - Curated/_curate_tags.json``. Each tag
        contributes its theme name (e.g. ``Macro``, ``Wildlife``)
        or, for non-theme tags (``best`` without theme, ``short``,
        ``medium``, ``long``, ``composition``, ``collage_only``),
        the human-readable level label. Counts the number of
        files carrying each.
      * **selected** — every ``ingest_journal.json`` under
        ``<event>/.cull/select/``. For each filename marked
        ``kept``, the effective genre = per-file override → per-
        file auto-classification cache → bucket-level override
        → ``"general"``. (Mirrors ``core.genre.effective_genre``.)
      * **culled** — same shape, walked across every per-camera
        journal root under ``<event>/.cull/`` (excluding the
        ``select/`` sub-tree, which belongs to the Select phase).
      * **processed** — Process inherits the style from the
        upstream Select decision, so we read the Select journal
        for processed-phase events. (The process journal carries
        scenario overrides too, but only for items the user
        actively touched; it's a sparse delta layered atop the
        Select baseline — not a complete source.)
      * **captured** — no style data exists at this stage; the
        walk skips Captured as a style source.

    Returns ``((), "")`` only when no phase has any files at all.
    When the furthest phase has files but no journal entries, the
    walk falls back to the next earlier phase (e.g., a curated
    tree exists on disk but `_curate_tags.json` is missing → fall
    back to Select). The returned label tracks whichever phase
    actually yielded data.
    """
    info = last_completed_phase(event)
    if info is None:
        return ((), "")

    start_phase_key = info[0]
    start_idx = next(
        (i for i, (k, _l) in enumerate(_PHASE_WALK_ORDER)
         if k == start_phase_key),
        0,
    )

    base = (event.photos_base_path or "").strip()
    if not base:
        return ((), info[1])
    try:
        root = event_root_path(base, event)
    except (TypeError, ValueError, OSError):
        return ((), info[1])

    # Walk furthest → earliest. Skip captured (no style data) and
    # skip phases whose own folder doesn't exist on disk — without
    # the filesystem check, a phase whose reader happens to share a
    # journal location with a later phase (e.g. Processed reads the
    # Select journal) would falsely "win" with another phase's
    # data and the wrong label.
    for idx in range(start_idx, len(_PHASE_WALK_ORDER)):
        phase_key, label = _PHASE_WALK_ORDER[idx]
        if phase_key == "captured":
            continue
        phase_root = _phase_dir(root, phase_key)
        if phase_root is None or not phase_root.is_dir():
            continue
        if not _has_any_photo(phase_root):
            continue
        counts = _styles_for_phase_from_journals(
            phase_key, root)
        if counts:
            ordered = tuple(sorted(
                counts.items(), key=lambda kv: (-kv[1], kv[0])))
            return (ordered, label)

    return ((), info[1])


def _styles_for_phase_from_journals(
    phase_key: str,
    event_root: Path,
) -> dict[str, int]:
    """Dispatch to the per-phase journal reader."""
    if phase_key == "curated":
        return _styles_from_curate_journal(event_root)
    if phase_key == "selected":
        return _styles_from_cull_shaped_journal(
            event_root / ".cull" / "pick")
    if phase_key == "processed":
        # Process inherits styles from Select — the Select journal
        # carries the per-file genre data Process operates on.
        return _styles_from_cull_shaped_journal(
            event_root / ".cull" / "pick")
    if phase_key == "culled":
        return _styles_from_all_camera_journals(event_root / ".cull")
    return {}


# ── Curate journal reader ─────────────────────────────────────


def _styles_from_curate_journal(event_root: Path) -> dict[str, int]:
    """RETIRED with the legacy Curate engine (spec/52 + spec/51 cleanup).

    Used to read ``04 - Curated/_curate_tags.json`` (pre-relational Curate's
    JSON journal). With the legacy Curate UI + engine deleted and the new
    Cuts model storing membership in ``event.db.cut_member`` (spec/61), this
    journal file no longer exists in modern events. Stub returns empty so the
    dispatcher in :func:`_styles_for_phase_from_journals` still has
    something to call; the empty result reads as "Share phase has no per-
    style counts yet" which is correct for any post-redesign event.

    When the new Cuts surface lands, this gets a real implementation that
    aggregates per-style counts from ``cut_member`` → ``lineage`` joined to
    ``item.classification``.
    """
    return {}


def _kept_style_index_from_cull_shaped_journal(
    journal_root: Path,
) -> dict[str, str]:
    """``{filename: effective_genre}`` for every KEPT file in
    every ``ingest_journal.json`` under ``journal_root``.

    Sibling of :func:`_styles_from_cull_shaped_journal` — that
    one returns counts; this one returns the per-file index so
    a downstream caller (the curate-journal reader) can look
    up a file's style without re-implementing the override
    resolution. Title-cased values so the pie legend renders
    consistently."""
    out: dict[str, str] = {}
    if not journal_root.is_dir():
        return out
    for journal_file in journal_root.rglob("ingest_journal.json"):
        try:
            with open(journal_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        marks = data.get("marks") or data.get("states") or {}
        if not isinstance(marks, dict):
            continue
        override_map = data.get("genre") or {}
        auto_cache = data.get("_genre_auto") or {}
        bucket_override = data.get("genre_bucket")
        if not isinstance(bucket_override, str) or not bucket_override:
            bucket_override = None

        for fname, state in marks.items():
            if str(state) != STATE_KEPT:
                continue
            genre = None
            if isinstance(override_map, dict):
                v = override_map.get(fname)
                if isinstance(v, str) and v:
                    genre = v
            if genre is None and isinstance(auto_cache, dict):
                cached = auto_cache.get(fname)
                if (isinstance(cached, dict)
                        and isinstance(cached.get("s"), str)):
                    genre = cached["s"]
            if genre is None and bucket_override is not None:
                genre = bucket_override
            if genre is None:
                genre = "general"
            out[fname] = str(genre).strip().title() or "General"
    return out


# ── Cull-shaped journal reader ────────────────────────────────


def _styles_from_cull_shaped_journal(
    journal_root: Path,
) -> dict[str, int]:
    """Walk every ``ingest_journal.json`` under ``journal_root``
    and count KEPT files by their effective genre.

    The per-bucket cull / select journal stores:

      * ``marks`` — ``{filename: state}`` where state ∈ {kept,
        candidate, discarded, ...}.
      * ``genre`` — ``{filename: scenario}`` — user override
        (sparse).
      * ``_genre_auto`` — ``{filename: {"s": scenario, "r":
        needs_review}}`` — auto-classification cache.
      * ``genre_bucket`` — single scenario string for the whole
        bucket (only set on bucket-styled types).

    Effective genre = per-file override → per-file auto cache →
    bucket override → ``"general"``. The journal is the source
    of truth; the on-disk folder names are derived from it.

    Title-cases the style key for legend display (``wildlife`` →
    ``Wildlife``).
    """
    if not journal_root.is_dir():
        return {}
    counts: dict[str, int] = {}
    for journal_file in journal_root.rglob("ingest_journal.json"):
        try:
            with open(journal_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        marks = data.get("marks") or data.get("states") or {}
        if not isinstance(marks, dict):
            continue
        override_map = data.get("genre") or {}
        auto_cache = data.get("_genre_auto") or {}
        bucket_override = data.get("genre_bucket")
        if not isinstance(bucket_override, str) or not bucket_override:
            bucket_override = None

        for fname, state in marks.items():
            if str(state) != STATE_KEPT:
                continue
            # Effective genre lookup.
            genre = None
            if isinstance(override_map, dict):
                v = override_map.get(fname)
                if isinstance(v, str) and v:
                    genre = v
            if genre is None and isinstance(auto_cache, dict):
                cached = auto_cache.get(fname)
                if (isinstance(cached, dict)
                        and isinstance(cached.get("s"), str)):
                    genre = cached["s"]
            if genre is None and bucket_override is not None:
                genre = bucket_override
            if genre is None:
                genre = "general"
            key = str(genre).strip().title() or "General"
            counts[key] = counts.get(key, 0) + 1
    return counts


def _styles_from_all_camera_journals(
    cull_root: Path,
) -> dict[str, int]:
    """The Cull phase's journals live one level down from
    ``<event>/.cull/`` — one per camera (sanitised camera_id).
    The Select journal also lives there at ``.cull/select/`` —
    skip it so cull-phase counts don't double-count Select's data
    when the user is still at the Cull stage.
    """
    if not cull_root.is_dir():
        return {}
    counts: dict[str, int] = {}
    for entry in cull_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name == "pick":
            continue
        sub = _styles_from_cull_shaped_journal(entry)
        for k, n in sub.items():
            counts[k] = counts.get(k, 0) + n
    return counts


# NOTE (post-00.046, Nelson 2026-05-26): the two filesystem-
# walking helpers that used to live here — ``_style_breakdown_
# curated`` (walked ``04 - Curated/<bucket>/``) and
# ``_style_breakdown_day_style`` (walked ``<phase>/<day>/
# <style>/``) — were removed. The folder structure was being
# treated as a style signal, but it's a DERIVED projection of
# the journal, not the source of truth. Filesystem walking
# mixed in accidental tier folders (Short/Medium/Long) and
# missed journal-only signals (per-photo genre overrides,
# curate tag parsing). The journal-driven readers above
# (``_styles_from_curate_journal``, ``_styles_from_cull_shaped_
# journal``, ``_styles_from_all_camera_journals``) are the
# correct source. CLAUDE.md invariant #8 — "every long-running
# editor writes a journal … the journal IS the source of truth
# for cull/select decisions" — applies here too.


def pick_random_last_phase_photo(
    event: Event,
    *,
    rng: Optional[random.Random] = None,
) -> Optional[Path]:
    """One random photo from the furthest-along phase with files.

    Source fallback chain: 04-Curated → 03-Processed → 02-Selected
    → 01-Culled → 00-Captured. Returns ``None`` when no phase has
    any photos.

    Per Nelson 2026-05-26 (F-026): the EventPlanPage's
    bottom-right quadrant uses this so the visual rotates each
    time the user opens the page.
    """
    # Prefer a phase the user has explicitly FINISHED (status DONE);
    # but fall back to the furthest-along phase that simply HAS photos
    # on disk. The strict Done-only rule left the quadrant empty
    # whenever the user had real Culled / Selected output that wasn't
    # marked Done in the phase_progress cache (Nelson 2026-05-29 — the
    # photo came up empty while the funnel bars showed real counts).
    phase_root = _photo_source_phase_root(event)
    if phase_root is None:
        return None
    pick = (rng or random).choice if rng else random.choice
    try:
        candidates = [
            f for f in phase_root.rglob("*")
            if f.is_file() and f.suffix.lower() in _PHOTO_EXTS
        ]
    except OSError:
        return None
    if not candidates:
        return None
    try:
        return pick(candidates)
    except IndexError:
        return None


def _photo_source_phase_root(event: Event) -> Optional[Path]:
    """Pick the phase folder the random-photo quadrant should sample.

    Two-tier resolution (Nelson 2026-05-29):

    1. The furthest-along phase whose status is **DONE**
       (:func:`last_completed_phase`) — the user explicitly finished
       it, so it's the most meaningful source.
    2. If no phase is Done, the furthest-along phase that simply has
       photos on disk (Curated → Processed → Selected → Culled →
       Captured). This keeps the quadrant alive for in-flight events
       — the common case where the user has Culled / Selected output
       but hasn't declared any phase "done".

    Returns ``None`` only when no phase has any photos at all.
    """
    base = (event.photos_base_path or "").strip()
    if not base:
        return None
    try:
        root = event_root_path(base, event)
    except (TypeError, ValueError, OSError):
        return None
    if not root.is_dir():
        return None

    # Tier 1: the user's furthest DONE phase — BUT only if it
    # actually contains a photo. A phase can read DONE while its
    # output folder holds no sampleable image (e.g. Process marked
    # done with only a journal / a video clip on disk — the real
    # Nepal case: 03 - Processed had 0 photos yet status was DONE).
    # When that happens, fall through to the filesystem walk so the
    # quadrant shows a real photo from an earlier phase instead of
    # going blank.
    info = last_completed_phase(event)
    if info is not None:
        done_root = info[2]
        if _has_any_photo(done_root):
            return done_root

    # Tier 2: the furthest-along phase that simply has photos on
    # disk (Curated → Processed → Selected → Culled → Captured).
    for phase_key, _label in _PHASE_WALK_ORDER:
        phase_root = _phase_dir(root, phase_key)
        if phase_root is not None and _has_any_photo(phase_root):
            return phase_root
    return None


def phase_funnel_breakdown(
    event: Event,
) -> tuple[tuple[str, int, float], ...]:
    """``((label, count, pct_of_captured), ...)`` in pipeline
    order. The bar chart on the EventPlanPage's bottom-left
    quadrant consumes this.

    Bars: Captured (the baseline 100%) → Culled → Selected →
    Processed → Curated. Percentages are clamped to [0, 100] —
    a downstream count > captured (rare; e.g. cascade duplicates
    if any sneaked past the Curate de-dup) gets shown as 100%
    rather than a misleading overflow bar.

    Returns ``()`` when the event has no readable photos_base_path
    or no Captured tree (no baseline → no funnel).
    """
    base = (event.photos_base_path or "").strip()
    if not base:
        return ()
    try:
        root = event_root_path(base, event)
    except (TypeError, ValueError, OSError):
        return ()
    if not root.is_dir():
        return ()

    captured = total_captured_photos(root)
    if captured <= 0:
        # No baseline → the percentages would all be 0/undefined.
        # Surface as "no data" via empty tuple so the bar quadrant
        # paints its empty-state hint.
        return ()

    culled = kept_in_cull_count(root)
    selected = kept_in_select_count(root)
    processed = kept_in_process_count(root)
    curated = kept_in_curate_count(root)

    rows = (
        ("Captured",  captured),
        ("Culled",    culled),
        ("Selected",  selected),
        ("Processed", processed),
        ("Curated",   curated),
    )
    out: list[tuple[str, int, float]] = []
    for label, count in rows:
        pct = max(0.0, min(100.0, 100.0 * count / captured))
        out.append((label, int(count), float(pct)))
    return tuple(out)


# ── F-030 early-stage fallback for the style-pie cell ──────────


def captured_per_camera_counts(
    event: Event,
) -> tuple[tuple[str, int], ...]:
    """``((camera_id, file_count), ...)`` for the event's Captured
    tree — sorted by descending count, then by ``camera_id`` for
    deterministic ordering when counts tie.

    F-030 (Nelson 2026-05-26): events in early stages (Captured
    only, nothing past it) used to render the EventPlanPage's
    style-pie cell as a dead "No per-style breakdown yet" box.
    The pie quadrant now falls back to a per-camera count chart
    drawn from this helper so the cell carries useful information
    at every stage of the event's life.

    Includes both photos and videos (everything in
    ``00 - Captured/`` counts), per Nelson's framing: "total
    file count (photos + videos) per camera". The grouping uses
    :func:`core.cull_dashboard.discover_cameras` so the per-camera
    id matches what the cull dashboard already labels each row by
    — no separate identity scheme to surprise the user.

    Returns ``()`` when the event has no Captured tree on disk
    (the cell falls through to the generic empty hint).
    """
    from core.cull_dashboard import discover_cameras

    rows = discover_cameras(event)
    out = [
        (str(r.camera_id), int(len(r.files)))
        for r in rows if r.files
    ]
    out.sort(key=lambda item: (-item[1], item[0]))
    return tuple(out)
