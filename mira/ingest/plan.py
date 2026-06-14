"""Plan proposal — turn a raw source scan into editable day + camera defaults (spec/10 §6).

The ingest *engine* (:func:`mira.ingest.engine.run_ingest`) takes a finished
``IngestPlan``; this is the brain the wizard uses to *propose* one from a scan, which the
user then edits (descriptions, per-day TZ, per-camera clock answers) before committing.

Pure / Qt-free / testable:
- **Days** are clustered from capture dates — preferring a detected reference phone's dates
  (NTP-synced trip-local) so a miscalibrated camera's wrong dates don't invent spurious
  days; falling back to all dated items when no phone is detected.
- **Cameras** are the distinct ids (most-shots-first), each tagged with a phone heuristic;
  the busiest detected phone is proposed as the reference. The user confirms/overrides all
  of this in the wizard — the heuristic only sets sensible defaults.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from core.clock_calibration import build_calibration
from core.fresh_source import SourceItem
from mira.ingest.model import CameraPlan, DayPlan, IngestPlan

# Substrings that mark a camera_id (EXIF Model) as a phone. Phones are NTP-synced, so
# their EXIF is trip-local wall-clock and needs no calibration — and they make the best
# reference clock. Lower-cased contains-match; the user can override in the wizard.
_PHONE_HINTS = (
    "iphone", "ipad", "pixel", "galaxy", "sm-", "oneplus", "redmi", "xiaomi",
    "mi ", "moto", "huawei", "nexus", "phone", "android",
)


def looks_like_phone(camera_id: str) -> bool:
    cid = (camera_id or "").lower()
    return any(hint in cid for hint in _PHONE_HINTS)


@dataclass
class ProposedPlan:
    """Editable defaults the wizard renders; the user adjusts then commits."""

    days: List[DayPlan] = field(default_factory=list)
    cameras: List[CameraPlan] = field(default_factory=list)
    reference_camera_id: Optional[str] = None


def propose_plan(
    source_items: Sequence[SourceItem], *, default_tz_hours: float = 0.0
) -> ProposedPlan:
    """Propose editable day + camera defaults from a scan (see module docstring)."""
    counts = Counter(si.camera_id for si in source_items if si.camera_id)

    phone_ids = [cid for cid in counts if looks_like_phone(cid)]
    reference = max(phone_ids, key=lambda c: counts[c]) if phone_ids else None

    cameras = [
        CameraPlan(
            camera_id=cid,
            is_phone=looks_like_phone(cid),
            is_reference=(cid == reference),
        )
        for cid, _n in counts.most_common()
    ]

    dated = [si for si in source_items if si.timestamp is not None]
    basis = [si for si in dated if si.camera_id == reference] if reference else dated
    if not basis:
        basis = dated
    dates = sorted({si.timestamp.date() for si in basis})
    days = [
        DayPlan(day_number=i + 1, date=d, description="", tz_offset_hours=default_tz_hours)
        for i, d in enumerate(dates)
    ]

    return ProposedPlan(days=days, cameras=cameras, reference_camera_id=reference)


def plan_from_reconcile(
    *,
    event_id: str,
    event_name: str,
    event_root: Path,
    source_root: Path,
    edited_days: Sequence[Any],
    tz_camera_groups: Dict[float, Sequence[Any]],
    trip_tz: float,
) -> IngestPlan:
    """Convert the **reused legacy flow's** outputs into an engine ``IngestPlan`` — the
    single data seam between the ported ``PastPhotosDialog`` and the new commit (charter
    §5.2). ``edited_days`` are legacy ``TripDay`` rows from the plan editor;
    ``tz_camera_groups`` is the per-TZ ``{tz: [CameraInput]}`` the calibration loop
    produced (``CameraInput`` read duck-typed so this stays decoupled from the legacy
    reconcile engine).

    A camera with calibration pairs → a derived ``CameraCalibration``; otherwise its
    declared ``configured_tz`` becomes ``configured_tz_hours`` (the engine builds the
    TZ-only offset). Phones pass through. A camera shooting across multiple TZ groups uses
    its first group's answer (single-TZ trips — the common case — are exact; cross-TZ is
    the documented constant-offset simplification, as in legacy)."""
    days = [
        DayPlan(
            day_number=d.day_number, date=d.date, description=d.description,
            location=getattr(d, "location", None),
            country_code=getattr(d, "country_code", None),
            tz_offset_hours=float(d.tz_offset if d.tz_offset is not None else trip_tz),
        )
        for d in edited_days
    ]

    cameras: List[CameraPlan] = []
    seen: set = set()
    for tz, cams in tz_camera_groups.items():
        for ci in cams:
            if ci.camera_id in seen:
                continue
            seen.add(ci.camera_id)
            pairs = list(getattr(ci, "calibration_pairs", []) or [])
            cal = (
                build_calibration(
                    ci.camera_id, pairs,
                    configured_tz=float(ci.configured_tz), trip_tz=float(tz))
                if pairs else None
            )
            cameras.append(CameraPlan(
                camera_id=ci.camera_id,
                is_phone=bool(getattr(ci, "is_phone", False)),
                is_reference=bool(getattr(ci, "is_reference", False)),
                configured_tz_hours=(
                    None if getattr(ci, "is_phone", False) else float(ci.configured_tz)),
                calibration=cal,
            ))

    iso = [d.date.isoformat() for d in days if d.date]
    return IngestPlan(
        event_id=event_id, event_name=event_name,
        event_root=event_root, source_root=source_root,
        days=days, cameras=cameras,
        start_date=min(iso) if iso else None, end_date=max(iso) if iso else None,
    )
