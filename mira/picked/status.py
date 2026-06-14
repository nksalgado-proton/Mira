"""Honest cull-status projection (spec/11 §3) + Day Grid cell colour (spec/32 §2.4).

The legacy resume map could not distinguish an **explicit discard** from a file merely
**defaulted to discard** (the journal is sparse — an unmarked file has no entry), so it
leaned on a ``BADGE_UNTOUCHED`` heuristic to avoid painting a fresh bucket 100% red.
The new ``phase_state`` table has a row **iff** the user made an explicit decision, so
``untouched`` becomes a first-class state and the projection is exact — no heuristic,
no F-034 time-weighting, no candidate-folds-into-kept.

This module owns **two** status concepts:
  * :class:`BucketStatus` + :func:`project_status` / :func:`rollup_status` — the
    bucket-level four-way count used by the legacy navigator and still by
    ``build_pick_days`` / ``pick_days``.
  * :class:`CellColor` + :func:`cell_color_for_item` / :func:`cluster_color` — the
    per-cell border colour used by the Day Grid (spec/32). A flat enum, computed
    per item or per cluster, matching the QSS property values on ``DayGridCell``.

Pure / Qt-free / never raises — a display model must never crash the surface. The
state-string constants (``'picked'`` / ``'candidate'`` / ``'skipped'``) match the
``phase_state.state`` wire values; the badge constants match the legacy QSS role keys so
the navigator's role mapping ports unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence

from mira.store import models as m

# phase_state.state wire values (spec/03 §3).
STATE_PICKED = "picked"
STATE_CANDIDATE = "candidate"
STATE_SKIPPED = "skipped"

# Badge ladder — user-facing resume-map status. Strings match the legacy
# ``core.cull_stats`` keys so the navigator's badge→QSS-role map is reused as-is.
# Ladder (no heuristic): reviewed → DONE; any explicit mark → IN_PROGRESS;
# opened-but-unmarked → BROWSED; nothing → UNTOUCHED.
BADGE_DONE = "done"
BADGE_IN_PROGRESS = "in_progress"
BADGE_BROWSED = "browsed"
BADGE_UNTOUCHED = "untouched"


@dataclass(frozen=True)
class BucketStatus:
    """One bucket's (or day's) honest resume-map figure.

    ``kept + candidate + discarded + untouched == total``. ``untouched`` is the count of
    items with **no** ``phase_state`` row for this phase — genuinely undecided, distinct
    from an explicit discard. ``reviewed`` / ``browsed`` are the user-declared bucket
    soft-state flags (never inferred). ``badge`` is the derived ladder state.
    """

    total: int
    kept: int
    candidate: int
    discarded: int
    untouched: int
    reviewed: bool
    browsed: bool
    badge: str

    @property
    def is_empty(self) -> bool:
        return self.total <= 0

    @property
    def has_explicit_marks(self) -> bool:
        return (self.kept + self.candidate + self.discarded) > 0

    @property
    def progress(self) -> float:
        """0.0–1.0 glanceable fill: 1.0 when declared done, else the
        positively-triaged (kept + candidate) share of the population."""
        if self.reviewed:
            return 1.0
        if self.total <= 0:
            return 0.0
        return (self.kept + self.candidate) / self.total


def default_state_for(settings_repo, phase: str) -> str:
    """The configured default state for un-decided items at ``phase`` — Settings → "Default
    state for untouched items" (``cull_default_state`` / ``pick_default_state`` /
    ``edit_default_state``). Returns ``'picked'`` or ``'skipped'``; falls back to discard
    if the value is missing/unreadable/not a valid default.

    This is the **single reader** of those settings — it wires the previously-orphaned
    per-phase default to real behaviour (Nelson 2026-06-03). Every surface that resolves an
    un-decided item (pill display, carry-forward to the next phase) routes through here so the
    setting is honoured uniformly. ``candidate`` is intentionally not a valid default (only the
    two endpoints can be a default; matches the ``settings`` + ``bucket.default_state`` CHECK)."""
    try:
        val = getattr(settings_repo.load(), f"{phase}_default_state", STATE_SKIPPED)
    except Exception:  # noqa: BLE001 — a display/decision default must never crash a surface
        return STATE_SKIPPED
    return val if val in (STATE_PICKED, STATE_SKIPPED) else STATE_SKIPPED


def _badge(*, reviewed: bool, has_marks: bool, browsed: bool) -> str:
    if reviewed:
        return BADGE_DONE
    if has_marks:
        return BADGE_IN_PROGRESS
    if browsed:
        return BADGE_BROWSED
    return BADGE_UNTOUCHED


def project_status(
    item_ids: Iterable[str],
    phase_states: Dict[str, m.PhaseState],
    bucket: Optional[m.Bucket] = None,
) -> BucketStatus:
    """Honest status for a bucket over ``item_ids``.

    ``phase_states`` is the phase's explicit rows keyed by item id
    (``EventGateway.phase_states(phase)``); a missing key = untouched. ``bucket`` is the
    persisted soft-state row for this ``bucket_key`` (or ``None`` if the user never
    touched it). Never raises — an unknown state value folds into ``untouched`` rather
    than poisoning the count."""
    ids = list(item_ids)
    total = len(ids)
    kept = candidate = discarded = untouched = 0
    for iid in ids:
        ps = phase_states.get(iid)
        state = ps.state if ps is not None else None
        if state == STATE_PICKED:
            kept += 1
        elif state == STATE_CANDIDATE:
            candidate += 1
        elif state == STATE_SKIPPED:
            discarded += 1
        else:  # no row, or a corrupt/unknown value → undecided
            untouched += 1

    reviewed = bool(bucket.reviewed) if bucket is not None else False
    browsed = bool(bucket.browsed) if bucket is not None else False
    has_marks = (kept + candidate + discarded) > 0
    return BucketStatus(
        total=total,
        kept=kept,
        candidate=candidate,
        discarded=discarded,
        untouched=untouched,
        reviewed=reviewed,
        browsed=browsed,
        badge=_badge(reviewed=reviewed, has_marks=has_marks, browsed=browsed),
    )


# =========================================================================== #
# Day Grid cell colour (spec/32 §2.4) — the flat border-colour model used by
# DayGridCell. Independent from BucketStatus (which keeps the badge ladder for
# the navigator). Same source of truth: phase_state.
# =========================================================================== #


class CellColor(str, Enum):
    """The five Day Grid border colours (spec/32 §2.4).

    String-valued so it serialises cleanly as a QSS property
    (``DayGridCell[status="picked"]`` etc.) and round-trips through the gateway.
    """

    KEPT = "picked"            # green — explicit Keep
    DISCARDED = "skipped"  # red   — explicit Discard
    COMPARE = "compare"      # orange — phase_state='candidate' (photo/snapshot only)
    MIXED = "mixed"          # yellow — partial decision (cluster aggregate)
    UNTOUCHED = "untouched"  # neutral — no decision yet


def cell_color_for_item(
    item_id: str,
    item_kind: str,
    phase: str,
    phase_states: Dict[str, m.PhaseState],
    *,
    default_state: str = STATE_SKIPPED,
) -> CellColor:
    """Border colour for one item-cell in the Day Grid (spec/32 §2.4).

    - **Photo / snapshot** at any phase: maps ``phase_state.state`` to
      ``KEPT`` / ``DISCARDED`` / ``COMPARE``.
    - **Video**: maps to ``KEPT`` / ``DISCARDED`` — the whole-video P/D
      (spec/56: Pick decides the whole video; the yellow kept-extracts
      rule retired with Pick-time clip creation). Videos do not support
      Compare — a stray ``candidate`` row folds to the default.
    - **No explicit phase_state row** → resolved to the phase's ``default_state``
      (``STATE_PICKED`` → green, ``STATE_SKIPPED`` → red). Nelson 2026-06-04:
      "untouched" is gone as a user-visible state; every cell looks decided.
      The user can still tell explicit-from-default by clicking — the cycle
      always starts from the default and moves forward.

    Never raises; missing item ids / unknown states fall to ``default_state``.
    """
    ps = phase_states.get(item_id)
    state = ps.state if ps is not None else None
    if state == STATE_PICKED:
        return CellColor.KEPT
    if state == STATE_SKIPPED:
        return CellColor.DISCARDED
    if state == STATE_CANDIDATE and item_kind != "video":
        return CellColor.COMPARE
    # No row, unknown state, or candidate on video → resolve to the phase default.
    return CellColor.KEPT if default_state == STATE_PICKED else CellColor.DISCARDED


def cell_color_for_process_item(
    item_id: str,
    adjustments: Dict[str, m.Adjustment],
    *,
    default_state: str = STATE_PICKED,
) -> CellColor:
    """Border colour for one item-cell in the **Process** Day Grid (spec/32 §6.3).

    Process has no ``phase_state`` semantics — the only signal is whether the
    item has been materialised through the export engine.
    ``Adjustment.edit_exported`` is the bit that flips when the export
    completes. There is **no Compare** at Process.

    Per Nelson 2026-06-05 ([[feedback_no_untouched_status_users_see_default]])
    "untouched is gone as a user-visible state": un-decided items render in
    the **phase default colour**, not neutral. At Process the default lives
    in Settings → ``edit_default_state``:

    - ``edit_exported=True`` → :attr:`CellColor.KEPT` (green)
    - Not exported AND ``default_state="picked"`` → :attr:`CellColor.KEPT`
      (green; the phase reads as "everything is keep-pending until you
      decide otherwise")
    - Not exported AND ``default_state="skipped"`` → :attr:`CellColor.DISCARDED`
      (red; the phase reads as "nothing has been processed yet; everything
      is in the 'skip' bucket until you export")

    The ``UNTOUCHED`` colour is never emitted — matching the rest of the
    Day Grid's "every cell looks decided" rule.
    """
    adj = adjustments.get(item_id)
    if adj is not None and adj.edit_exported:
        return CellColor.KEPT
    return CellColor.KEPT if default_state == STATE_PICKED else CellColor.DISCARDED


def cluster_color(member_colors: Sequence[CellColor]) -> CellColor:
    """Aggregate border colour for a cluster (spec/32 §2.4).

    - All members the same colour → that colour (uniform Kept / Discarded /
      Compare / Untouched cluster).
    - Any mix at all (e.g. kept + discarded, kept + untouched, anything →
      partial decision) → :attr:`CellColor.MIXED` (yellow).
    - Empty member list → :attr:`CellColor.UNTOUCHED` (defensive).

    This is the rule that drives §2.5 progress accounting: yellow counts 0.5
    Kept + 0.5 Discarded in any day-level summary.
    """
    if not member_colors:
        return CellColor.UNTOUCHED
    unique = set(member_colors)
    if len(unique) == 1:
        return next(iter(unique))
    return CellColor.MIXED


def rollup_status(parts: List[BucketStatus]) -> BucketStatus:
    """Day-level rollup across its buckets. Counts sum; the day is DONE only when
    **every** non-empty bucket is declared done; else IN_PROGRESS if any has a mark or
    is done; else BROWSED if any was opened; else UNTOUCHED (mirrors the legacy
    ``core.cull_stats.rollup`` ladder, on honest counts)."""
    real = [s for s in parts if not s.is_empty]
    total = sum(s.total for s in real)
    kept = sum(s.kept for s in real)
    candidate = sum(s.candidate for s in real)
    discarded = sum(s.discarded for s in real)
    untouched = sum(s.untouched for s in real)

    if real and all(s.badge == BADGE_DONE for s in real):
        badge, reviewed, browsed = BADGE_DONE, True, False
    elif any(s.badge in (BADGE_IN_PROGRESS, BADGE_DONE) for s in real):
        badge, reviewed, browsed = BADGE_IN_PROGRESS, False, False
    elif any(s.badge == BADGE_BROWSED for s in real):
        badge, reviewed, browsed = BADGE_BROWSED, False, True
    else:
        badge, reviewed, browsed = BADGE_UNTOUCHED, False, False

    return BucketStatus(
        total=total, kept=kept, candidate=candidate, discarded=discarded,
        untouched=untouched, reviewed=reviewed, browsed=browsed, badge=badge,
    )
