"""Typed inputs/outputs for the ingest engine (spec/10 §4).

Deliberately decoupled from the legacy ``ReconcileConfig`` — the UI assembles these from
the scan + the plan editor + the per-camera calibration answers, and hands them to
:func:`mira.ingest.engine.run_ingest`. The engine reuses the pure-logic
``CameraCalibration`` (offset math) from legacy ``core/`` verbatim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import List, Optional

from core.clock_calibration import CameraCalibration


@dataclass
class DayPlan:
    """One planned ``Dia N`` + the trip-local UTC offset the user set in the plan editor."""

    day_number: int
    date: Optional[_date] = None
    description: str = ""
    location: Optional[str] = None
    tz_offset_hours: float = 0.0
    # spec/47 — per-day ISO 3166-1 alpha-2 country code. None when not auto-detected
    # and not manually set. Threaded into the store TripDay's
    # ``extras_json["country_code"]`` by :func:`mira.ingest.engine.run_ingest`.
    country_code: Optional[str] = None


@dataclass
class CameraPlan:
    """One detected camera + its calibration answer.

    ``calibration`` is a pre-built :class:`CameraCalibration` (from the sync-pair-picker
    and/or a declared offset). If ``None`` and ``configured_tz_hours`` is set, the engine
    builds a TZ-only calibration (``trip_tz − configured_tz``). Phones pass through
    uncorrected (NTP-synced trip-local wall-clock)."""

    camera_id: str
    is_phone: bool = False
    is_reference: bool = False
    configured_tz_hours: Optional[float] = None
    calibration: Optional[CameraCalibration] = None


@dataclass
class IngestPlan:
    """The whole create-event-from-photos job."""

    event_id: str
    event_name: str
    event_root: Path
    source_root: Path
    days: List[DayPlan] = field(default_factory=list)
    cameras: List[CameraPlan] = field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@dataclass
class IngestResult:
    """What the ingest did — for the UI summary + tests."""

    event_id: str
    db_path: Optional[Path] = None
    items_created: int = 0
    photos: int = 0
    videos: int = 0
    quarantined: int = 0
    filename_recovered: int = 0
    out_of_day_range: int = 0
    integrity_failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
