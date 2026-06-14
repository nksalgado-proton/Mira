"""Capture-time plan-disk consistency check (task #109, Nelson 2026-05-23).

Companion of :mod:`core.day_folder_reconciler`. Where the reconciler
handles "files arrived on disk for a day not in the plan" *after the
fact* (auto-heals by adding the day), this module handles the
*before-the-fact* version: when a Capture surface (past-photos
ingest, card offload) is about to land files in
``00 - Captured/`` and some of those files fall on dates outside
the current plan.

The Capture-time check is more honest than auto-heal — at this
moment the user is actively interacting, so we pause and ask
rather than silently extending the plan. Per Nelson 2026-05-23,
the three options are:

* **Add these as new plan days** — append TripDays with placeholder
  descriptions covering the orphan dates, then proceed with the
  full Capture.
* **Skip these photos** — leave the orphan files at the source and
  copy only the in-plan ones. Source files are never touched.
* **Cancel Capture entirely** — bail out, no files moved.

This module is the Qt-free engine. The UI dialog lives in
``ui/pages/orphan_dates_dialog.py``.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Iterable

from core.models import Event, TripDay

log = logging.getLogger(__name__)


def find_orphan_dates(
    files_with_dates: Iterable[tuple[Path, datetime]],
    plan_days: list[TripDay],
) -> dict[date_cls, list[Path]]:
    """Group ``files_with_dates`` by their date (the ``.date()`` of
    the corrected capture time) and return only those groups whose
    date is not covered by any TripDay in ``plan_days``.

    Returns ``{date: [file_paths sorted]}`` in chronological date
    order. Empty dict when every file's date is in the plan.

    Files whose timestamp is ``None`` are skipped — they couldn't
    be placed anyway, and reconcile handles them via the
    ``_no_timestamp`` quarantine separately.
    """
    plan_dates = {
        d.date for d in (plan_days or []) if d.date is not None
    }
    out: dict[date_cls, list[Path]] = {}
    for path, dt in files_with_dates:
        if dt is None:
            continue
        d = dt.date() if isinstance(dt, datetime) else dt
        if d in plan_dates:
            continue
        out.setdefault(d, []).append(path)
    # Sort by date for predictable presentation; within each date,
    # sort paths for determinism.
    sorted_keys = sorted(out.keys())
    return {k: sorted(out[k]) for k in sorted_keys}


def extend_plan_with_dates(
    event: Event,
    orphan_dates: Iterable[date_cls],
) -> list[TripDay]:
    """Append a TripDay per orphan date to ``event.trip_days``, with
    placeholder description. Day numbers are assigned by sorting
    the merged set chronologically; the function returns ONLY the
    newly-added TripDays so callers can surface them in a summary.

    Idempotent: dates already in the plan are skipped silently.

    Mutates ``event.trip_days`` in place — caller saves the event.
    """
    existing_dates = {
        d.date for d in (event.trip_days or []) if d.date is not None
    }
    new_dates = sorted({
        d for d in orphan_dates if d not in existing_dates
    })
    if not new_dates:
        return []

    new_days: list[TripDay] = []
    for d in new_dates:
        new_days.append(TripDay(
            day_number=0,           # renumbered below
            date=d,
            description="(added at Capture — please describe)",
        ))

    merged = list(event.trip_days or []) + new_days
    # Sort by date; renumber 1..N to keep the plan canonical.
    merged.sort(
        key=lambda x: (x.date or date_cls.min, x.day_number),
    )
    for n, day in enumerate(merged, start=1):
        day.day_number = n
    event.trip_days = merged
    return new_days


def summarise_orphans(
    orphans: dict[date_cls, list[Path]],
) -> list[tuple[date_cls, int]]:
    """``[(date, count), ...]`` sorted by date. Convenience for
    the dialog's row builder."""
    return [(d, len(paths)) for d, paths in orphans.items()]
