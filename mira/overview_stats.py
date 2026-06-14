"""Gateway-driven stats for the EventPlanPage 2×2 overview (Slice C, charter §5.2).

The legacy ``TwoByTwoOverview`` fed its quadrants from ``core.event_stats`` — filesystem
walks + journal reads over the on-disk projection. In the rebuild the data lives in the
store, so this module is the **data seam**: the same four quadrant inputs, recomputed from
an open :class:`~mira.gateway.event_gateway.EventGateway`. The widget's *rendering*
is ported verbatim; only where its numbers come from changed.

Adaptations from the legacy (Nelson-approved 2026-06-01):

* **"Furthest phase with kept items"** replaces the legacy ``is_phase_done`` walk — the
  rebuild never infers "done" (charter §5.4). The pie + random photo key off the furthest
  pipeline phase (curate → process → select → cull) that actually has kept items, falling
  back to the captured pool for the photo. This matches the Tier-2 fallback the legacy
  photo-picker already used.
* **Slideshow chips are out of scope here** — they need curated buckets from the
  Curate/Collections surface, which isn't ported yet. The overview is fed
  ``show_slideshows=False`` until then.

Absolute paths never enter the model: the random photo is resolved at call time as
``event_gateway.event_root / item.origin_relpath`` — and ``event_root`` is itself resolved
from the single ``photos_base_path`` anchor when the event was opened (charter §5.9).
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoid a runtime import cycle (gateway imports nothing from here)
    from mira.gateway.event_gateway import EventGateway


# Display labels for funnel bars (past-tense verbs are funnel-specific; phase keys live
# in event_classification). spec/48 + spec/52: cull+select collapsed into
# 'pick'; share dropped from the phase enums (Cuts replace the curate
# concept and have no kept-count source until the Cuts surfaces land).
# The funnel iterates pick → edit only; the iteration filter in
# _pipeline_for skips any phase not keyed here.
_PHASE_FUNNEL_LABELS: dict[str, str] = {
    "pick": "Picked",
    "edit": "Edited",
}

_STATE_PICKED = "picked"


def _pipeline_for(eg: "EventGateway") -> tuple[tuple[str, str], ...]:
    """The funnel's phase iteration tuple for this event — pairs of
    ``(phase_key, funnel_label)`` in pipeline order.

    Routes through :func:`mira.event_classification.decision_phases_for_type`
    (spec/44 §1.7 phase-set seam): Slice A returns every phase for every type,
    so the funnel always shows Culled → Selected → Processed → Curated. The
    next sprint changes only the seam body — this function reads through it
    and absorbs the change for free."""
    from mira import event_classification
    et = eg.event().event_type or event_classification.EVENT_TYPE_UNCLASSIFIED
    return tuple(
        (phase, _PHASE_FUNNEL_LABELS[phase])
        for phase in event_classification.decision_phases_for_type(et)
        if phase in _PHASE_FUNNEL_LABELS
    )


def phase_funnel_breakdown(eg: "EventGateway") -> tuple[tuple[str, int, float], ...]:
    """``((label, count, pct_of_captured), ...)`` in pipeline order — Captured (100%
    baseline) → Culled → Selected → Processed → Curated. The bottom-left bar quadrant
    consumes this.

    Captured is the visible captured-item count (hidden days excluded, via the gateway
    reads); each later bar is that phase's ``kept`` count from
    :meth:`EventGateway.phase_picked_count` (visibility-filtered, with the per-phase storage
    routing — ``phase_state`` for Cull/Select, ``adjustment.edit_exported`` for Process,
    ``share_tag`` for Curate). Percentages clamp to [0, 100]. Returns ``()`` when there is
    no captured baseline (the quadrant then paints its empty hint).

    The phase iteration goes through :func:`_pipeline_for` so the next sprint's
    per-type pipeline overrides (Session loses Select, Occasion loses Cull, …)
    flow through automatically — Slice A behaviour is unchanged because the
    seam returns the full set for every type today."""
    captured = len(eg.items(provenance="captured"))
    if captured <= 0:
        return ()
    rows: list[tuple[str, int]] = [("Captured", captured)]
    for phase, label in _pipeline_for(eg):
        rows.append((label, eg.phase_picked_count(phase)))
    out: list[tuple[str, int, float]] = []
    for label, count in rows:
        pct = max(0.0, min(100.0, 100.0 * count / captured))
        out.append((label, int(count), float(pct)))
    return tuple(out)


def style_breakdown_last_phase(
    eg: "EventGateway",
) -> tuple[tuple[tuple[str, int], ...], str]:
    """``(((style_label, count), ...), phase_display_label)`` for the furthest phase that
    has kept items. Styles are the kept items' ``classification`` (title-cased; ``General``
    when unset), sorted by descending count. Returns ``((), "")`` when no phase has keeps."""
    for phase, label in reversed(_pipeline_for(eg)):
        kept = eg.items(phase=phase, state=_STATE_PICKED)
        if not kept:
            continue
        counts: dict[str, int] = {}
        for it in kept:
            key = (it.classification or "").strip().title() or "General"
            counts[key] = counts.get(key, 0) + 1
        ordered = tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        return (ordered, label)
    return ((), "")


def pick_random_last_phase_photo(
    eg: "EventGateway",
    *,
    rng: Optional[random.Random] = None,
) -> Optional[Path]:
    """One random KEPT photo from the furthest phase that has any, falling back to the
    captured pool. Resolves ``event_root / origin_relpath`` at call time (never a persisted
    absolute path; ``event_root`` is anchored to ``photos_base_path``). Virtual items
    (clips/snapshots — no ``origin_relpath``) and videos are excluded. ``None`` when nothing
    resolvable exists on disk."""
    root = Path(eg.event_root) if eg.event_root is not None else None
    if root is None:
        return None
    source = None
    for phase, _label in reversed(_pipeline_for(eg)):
        kept = eg.items(phase=phase, state=_STATE_PICKED, kind="photo")
        if kept:
            source = kept
            break
    if source is None:
        source = eg.items(provenance="captured", kind="photo")
    paths = [
        root / it.origin_relpath
        for it in source
        if it.origin_relpath
    ]
    paths = [p for p in paths if p.exists()]
    if not paths:
        return None
    pick = (rng or random).choice
    return pick(paths)


def captured_per_camera_counts(eg: "EventGateway") -> tuple[tuple[str, int], ...]:
    """``((camera_id, file_count), ...)`` for the visible captured pool — descending by
    count, then by id. The style-pie quadrant's early-stage fallback (F-030) when no later
    phase has style data yet."""
    counts: dict[str, int] = {}
    for it in eg.items(provenance="captured"):
        cam = it.camera_id or "Unknown"
        counts[cam] = counts.get(cam, 0) + 1
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def captured_per_camera_time_share(
    eg: "EventGateway", *, photo_seconds: float,
) -> tuple[tuple[str, int], ...]:
    """``((camera_id, seconds), ...)`` — each camera's share of capture *time* rather than
    item count (Nelson 2026-06-01: the Capture pie should weigh a long video far heavier
    than a single frame). A photo counts as ``photo_seconds`` (the short-tier slide
    duration); a video counts as its probed running time (``item.duration_ms``, captured at
    ingest). Videos with NULL duration (pre-probe ingests) count as one photo-slide
    equivalent so the camera still appears in the chart instead of being silently
    dropped. (The old summed-clip-spans fallback retired with spec/56 schema v4 —
    segment geometry is derived from markers, not stored.) Rounded to whole
    seconds, descending then by id."""
    seconds: dict[str, float] = {}
    for it in eg.items(provenance="captured"):
        cam = it.camera_id or "Unknown"
        if it.kind == "video":
            dur_ms = it.duration_ms or round(photo_seconds * 1000)
            seconds[cam] = seconds.get(cam, 0.0) + dur_ms / 1000.0
        else:
            seconds[cam] = seconds.get(cam, 0.0) + float(photo_seconds)
    rounded = {k: int(round(v)) for k, v in seconds.items() if round(v) > 0}
    return tuple(sorted(rounded.items(), key=lambda kv: (-kv[1], kv[0])))
