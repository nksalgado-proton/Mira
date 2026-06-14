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

_PHASES = ("pick", "edit", "share")


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


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


def _populate_closed_body_data(
    gateway: "Gateway", eg, base: "EventCardData",
) -> None:
    """Fill in the closed-tile body data: stat counts, classification
    distribution, and the sample exported-file paths feeding the Surface 01
    Carousel."""
    try:
        collected = eg.items(kind="photo")
        base.collected_count = len(collected)
    except Exception:                                          # noqa: BLE001
        log.exception("collected count failed for %s", base.event_id)
        collected = []
    try:
        base.picked_count = len(
            eg.items(kind="photo", phase="pick", state="picked")
        )
    except Exception:                                          # noqa: BLE001
        log.exception("picked count failed for %s", base.event_id)
    try:
        base.edited_count = len(eg.adjustments())
    except Exception:                                          # noqa: BLE001
        log.exception("edited count failed for %s", base.event_id)
        base.edited_count = 0
    try:
        base.exported_count = len(eg.exported_item_ids())
    except Exception:                                          # noqa: BLE001
        log.exception("exported count failed for %s", base.event_id)
        base.exported_count = 0

    # Sample exported-file absolute paths for the Carousel (Surface 01).
    # First 5 from chronologically-ordered exported_files(); skips when
    # event_root is unresolvable (bare-card fallback).
    try:
        if eg.event_root is not None:
            base.sample_pixmap_paths = [
                eg.event_root / lin.export_relpath
                for lin in eg.exported_files()[:5]
                if lin.export_relpath
            ]
    except Exception:                                          # noqa: BLE001
        log.exception("sample_pixmap_paths failed for %s", base.event_id)
        base.sample_pixmap_paths = []

    counts: Counter = Counter()
    for it in collected:
        cls = (it.classification or "").strip()
        if cls:
            counts[cls] += 1
    base.classification_counts = dict(counts)


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
        base.total_days = len(trip_days)
        base.tz_display = _tz_display(trip_days)
        base.status_by_phase = _status_by_phase(
            eg, trip_days, eg.day_tree()
        )
        if base.is_closed:
            _populate_closed_body_data(gateway, eg, base)
    except Exception:
        log.exception(
            "could not load detail for event %r (%s) — rendering bare card",
            base.name, base.event_id,
        )
    finally:
        eg.close()
    return base
