"""Pure helpers that build :class:`~mira.ui.base.event_card.EventCardData`
from gateway rows.

Lifted out of the retired ``events_dashboard_page.py`` (the legacy
:class:`DashboardPage` and its FilterRail were retired with Surface 01) so
the helpers can travel with whichever events list / search surface needs
them. No Qt imports — pure data shaping over the gateway seam.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date
from typing import Any, Dict, Optional

from mira.gateway import Gateway
from mira.ui.base.event_card import EventCardData

log = logging.getLogger(__name__)

_PHASES = ("pick", "edit", "export")


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _derive_year_hint(base, trip_days, eg) -> Optional[str]:
    """spec/77 (Nelson 2026-06-28) — a fallback year for the tile meta
    line when the event has no header ``start_date`` yet, so every tile
    shows a year consistently instead of flickering between
    "Type · Subtype" and "Type · year · Subtype". Priority: end date →
    earliest capture day → creation date. ``None`` only when nothing is
    knowable."""
    if base.end_date:
        return str(base.end_date.year)
    day_dates = sorted(d.date for d in trip_days if getattr(d, "date", None))
    if day_dates:
        dt = _parse_date(day_dates[0])
        if dt:
            return str(dt.year)
    try:
        created = _parse_date(eg.event().created_at)
    except Exception:                                              # noqa: BLE001
        created = None
    return str(created.year) if created else None


def _tz_display(trip_days) -> str:
    offs = [d.tz_minutes for d in trip_days if d.tz_minutes is not None]
    if not offs:
        return ""
    main = Counter(offs).most_common(1)[0][0]
    sign = "−" if main < 0 else "+"
    hh, mm = divmod(abs(int(main)), 60)
    s = f"UTC{sign}{hh}:{mm:02d}"
    loc = (trip_days[0].location or "") if trip_days else ""
    return f"{s}\n{loc}" if loc else s


def _status_by_phase(eg, trip_days, day_tree) -> Dict[str, Dict[int, str]]:
    """Derive the {phase_key: {day_number: STATUS}} heatmap input from
    gateway queries (spec/48 4-phase pivot)."""
    from core.event_card_grid import STATUS_DONE, STATUS_NOT_STARTED
    out: Dict[str, Dict[int, str]] = {}
    totals_by_day = {g["day_number"]: g["total"] for g in day_tree}
    out["collect"] = {
        d.day_number: (
            STATUS_DONE if totals_by_day.get(d.day_number, 0) > 0
            else STATUS_NOT_STARTED
        )
        for d in trip_days
    }
    pdp = eg.phase_day_progress()
    from core.event_card_grid import STATUS_IN_PROGRESS
    for phase in _PHASES:
        phase_map = pdp.get(phase, {})
        m: Dict[int, str] = {}
        for d in trip_days:
            cell = phase_map.get(d.day_number)
            if not cell or cell.get("decided", 0) == 0:
                m[d.day_number] = STATUS_NOT_STARTED
            elif cell["decided"] >= cell.get("total", cell["decided"]):
                m[d.day_number] = STATUS_DONE
            else:
                m[d.day_number] = STATUS_IN_PROGRESS
        out[phase] = m
    return out


_SAMPLE_PIXMAP_CAP = 12


def _sample_pixmap_paths(eg, collected) -> list:
    """Resolve the photo-cycler source list for the closed tile.

    spec/132 — the closed event's carousel must show **only** exported
    photos. The legacy 3-tier fallback (Exported → Picked → any capture)
    let the closed card display frames the user never chose to export,
    defeating the point of a closed event's "highlight reel". Empty
    exported returns empty; the closed tile renders a neutral
    placeholder (PhotoCycler's built-in "no photos" path) instead of
    parading un-chosen captures.

    Performance: rendering reaches for the export thumb cache
    (``core.photo_thumb_cache``, already populated via
    ``queue_export_thumb`` at export time). If a frame must decode from
    the full ``Exported Media/`` JPEG the carousel just cycles slower —
    correctness over speed, per spec/132 §2.

    Returns absolute :class:`Path` instances. Capped at
    ``_SAMPLE_PIXMAP_CAP`` so the events list keeps a bounded memory
    footprint as closed events accumulate.

    ``collected`` is preserved in the signature so existing callers
    don't break, but the value is ignored (the any-capture fallback
    was retired with spec/132).
    """
    del collected  # spec/132 — any-capture fallback retired
    if eg.event_root is None:
        return []
    try:
        return [
            eg.event_root / lin.export_relpath
            for lin in eg.exported_files()[:_SAMPLE_PIXMAP_CAP]
            if lin.export_relpath
        ]
    except Exception:                                          # noqa: BLE001
        log.exception("exported_files() failed for sample paths")
        return []


def _populate_body_data(
    gateway: "Gateway", eg, base: "EventCardData",
) -> None:
    """Fill in the per-event aggregates the v2 EventTile (spec/77) needs
    on **every** card — open and closed. The open tile reads
    ``collected/picked/decided/developed/exported`` for its 2×2 donut
    grid; the closed tile reads the same plus
    ``sample_pixmap_paths`` for its PhotoCycler.

    Counts come from the gateway's existing aggregate seams so the tile
    and the Phases page agree:

    * ``collected_count``  — every captured photo (``items(kind="photo")``).
    * ``picked_count``     — keepers (``phase_state.state='picked'`` on
                             ``phase='pick'``).
    * ``decided_count``    — any explicit pick decision (picked OR
                             skipped OR compare).
    * ``developed_count``  — real adjustment rows (any row exists).
    * ``edited_count``     — rows that are **off the unedited baseline**:
                             a look other than Original/Natural, a
                             creative filter, or a crop
                             (``core.edit_status``). This is the Edit
                             donut's numerator — *edited ÷ picked*
                             (Nelson 2026-06-18) — so a photo opened in
                             Edit but left at Original does NOT count.
    * ``exported_count``   — shipped lineage rows
                             (``adjustment.edit_exported = 1``).
    * ``days_with_captures`` — distinct day_numbers with any captured
                             items (Collect numerator, spec/77 §4).
    """
    try:
        collected = eg.items(kind="photo")
        base.collected_count = len(collected)
    except Exception:                                          # noqa: BLE001
        log.exception("collected count failed for %s", base.event_id)
        collected = []
    try:
        base.picked_count = eg.phase_picked_count("pick")
    except Exception:                                          # noqa: BLE001
        log.exception("picked count failed for %s", base.event_id)
    try:
        base.decided_count = eg.phase_decided_count("pick")
    except Exception:                                          # noqa: BLE001
        log.exception("decided count failed for %s", base.event_id)
    try:
        base.developed_count = len(eg.adjustments())
        # Edited = off the unedited baseline (non-default look/crop/filter),
        # NOT merely "a row exists". The Edit donut + the stat grids read
        # this as edited / picked (Nelson 2026-06-18).
        base.edited_count = eg.edited_count()
    except Exception:                                          # noqa: BLE001
        log.exception("developed count failed for %s", base.event_id)
    try:
        base.exported_count = len(eg.exported_item_ids())
    except Exception:                                          # noqa: BLE001
        log.exception("exported count failed for %s", base.event_id)
        base.exported_count = 0

    try:
        day_tree = eg.day_tree()
        base.days_with_captures = sum(
            1 for d in day_tree if d.get("total", 0) > 0
        )
    except Exception:                                          # noqa: BLE001
        log.exception("days_with_captures failed for %s", base.event_id)

    # PhotoCycler source list (spec/75 §6.2, narrowed by spec/132 to
    # exported-only — never the picked-but-not-exported or any-capture
    # fallback). Closed tiles read this; open tiles ignore it.
    base.sample_pixmap_paths = _sample_pixmap_paths(eg, collected)

    counts: Counter = Counter()
    for it in collected:
        cls = (it.classification or "").strip()
        if cls:
            counts[cls] += 1
    base.classification_counts = dict(counts)


def _populate_closed_body_data(
    gateway: "Gateway", eg, base: "EventCardData",
) -> None:
    """Backwards-compatible alias — the spec/77 rework folded the
    open + closed populators into a single seam (the donuts need the
    same numbers on both tiles)."""
    _populate_body_data(gateway, eg, base)


def card_data(gateway: Gateway, row: Dict[str, Any]) -> EventCardData:
    """Build one card's data from its index row + a per-event open.

    Never raises — any failure returns a bare card so one broken event
    cannot prevent the whole list from rendering.
    """
    base = EventCardData(
        event_id=str(row.get("id", "")),
        name=str(row.get("name") or ""),
        start_date=_parse_date(row.get("start_date")),
        end_date=_parse_date(row.get("end_date")),
        is_closed=bool(row.get("is_closed")),
        total_days=0,
        event_type=str(row.get("event_type") or "unclassified"),
        event_subtype=(row.get("event_subtype") or None),
        description=str(row.get("description") or ""),
        tags=list(row.get("tags") or []),
    )
    if row.get("event_root") is None:
        log.warning(
            "event %r (%s) has no resolvable root — rendering bare card",
            base.name, base.event_id,
        )
        return base
    try:
        eg = gateway.open_event(base.event_id)
    except Exception:
        log.exception(
            "could not open event %r (%s) — rendering bare card",
            base.name, base.event_id,
        )
        return base
    try:
        trip_days = eg.trip_days()
        # spec/77 §5 — total_days comes from the event header's date
        # span (start_date..end_date inclusive), NOT from the count of
        # days that have photos. Without this change Collect always
        # reads 100% the moment any day has captures, because the
        # denominator was the count of populated days. The span gives
        # Collect a real "% of the planned event you've actually shot
        # on" metric. Falls back to ``len(trip_days)`` only when the
        # event row has no dates yet (pre-spec/77 events).
        if base.start_date and base.end_date and base.end_date >= base.start_date:
            base.total_days = (base.end_date - base.start_date).days + 1
        else:
            base.total_days = len(trip_days)
        base.tz_display = _tz_display(trip_days)
        # spec/77 (Nelson 2026-06-28) — fill a year for tiles whose
        # event has no header start_date yet, so the meta line is
        # consistent across the list. ``start_date.year`` still wins
        # in the tile when present.
        if base.start_date is None:
            base.year_hint = _derive_year_hint(base, trip_days, eg)
        base.status_by_phase = _status_by_phase(
            eg, trip_days, eg.day_tree()
        )
        # Spec/77 §4 — populate the donut numerators on EVERY event,
        # not just closed ones (the open tile's 2×2 donut grid needs
        # them). Closed tiles continue to read the sample-pixmap-paths
        # piece for the PhotoCycler.
        _populate_body_data(gateway, eg, base)
    except Exception:
        log.exception(
            "could not load detail for event %r (%s) — rendering bare card",
            base.name, base.event_id,
        )
    finally:
        eg.close()
    return base
