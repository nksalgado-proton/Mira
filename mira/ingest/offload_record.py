"""Record a legacy card-offload session into the event DB (spec/13 §1, Option 1).

The Capture flow keeps the legacy offload engine (``core.event_backup_card.offload_to_captured``
+ ``verify_offload``) **verbatim** — same folders, typed ``camera_id``, bucket, collision,
Mode-B ``included_names``. The ONE data-layer change (charter §0): the legacy *baked* the TZ
offset into the copied files' EXIF and leaned on the folder tree; we leave the originals
byte-pristine and instead record one ``Item`` row per copied file, storing
``capture_time_raw`` + ``capture_time_corrected`` (= raw + offset) — the virtual-EXIF model.

The offload's ``OffloadManifest`` already carries everything needed per file (``dest`` /
``sha256`` / ``bytes`` / ``day_number`` / ``capture_time_raw``), so this is a straight
projection of the manifest into item rows + a camera row, appended via the gateway. Day
routing matches legacy exactly (the offload routed by raw time, ``calibration=None``); only
the *record* carries the correction.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from core.path_builder import CAPTURED_PHONES_SUBDIR
from core.video_discovery import VIDEO_EXTENSIONS

from mira.store import models as m

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_offload(
    manifest,
    *,
    gateway,
    event_id: str,
    camera_id: str,
    bucket: str,
    offset_hours: float,
    event_root: Path,
    now: Callable[[], str] = _utc_now_iso,
) -> int:
    """Append the offload session's files to ``event_id``'s ``event.db`` and return the
    item count. ``offset_hours`` is the F-019 correction (target_tz − camera_tz); 0 = the
    camera was right. The camera row is inserted only if new (no calibration clobber)."""
    stamp = now()
    event_root = Path(event_root)
    items: list[m.Item] = []

    for rec in manifest.files:
        dest = Path(rec.dest)
        kind = "video" if dest.suffix.lower() in VIDEO_EXTENSIONS else "photo"
        raw = rec.capture_time_raw  # ISO string or None
        # day_number: >0 = a planned Dia; 0 = quarantined (no EXIF time); None = flat (no plan)
        quarantined = (not raw) or rec.day_number == 0
        day_number = rec.day_number if (rec.day_number and rec.day_number > 0) else None

        corrected = raw
        tz_seconds = 0
        if raw and offset_hours:
            try:
                corrected = (datetime.fromisoformat(raw) + timedelta(hours=offset_hours)).isoformat()
                tz_seconds = round(offset_hours * 3600)
            except ValueError:
                corrected = raw  # unparseable → store raw unchanged

        items.append(m.Item(
            id=uuid.uuid4().hex, kind=kind,
            origin_relpath=dest.relative_to(event_root).as_posix(),
            sha256=rec.sha256, byte_size=rec.bytes,
            materialized_at=stamp, materialized_phase="ingest",
            camera_id=camera_id,
            capture_time_raw=raw or "", capture_time_corrected=corrected or "",
            created_at=stamp,
            tz_offset_seconds=tz_seconds,
            tz_source=("user_declared" if (raw and offset_hours) else "none"),
            day_number=day_number,
            quarantine_status="no_timestamp" if quarantined else "ok",
            provenance="captured",
            # spec/134 — project the exposure quartet + lens + flash the
            # manifest carried (read in the offload's single EXIF pass) so the
            # captured Item matches the Create-Event shape and the overlay's
            # Exposure/Camera fields render. ``getattr`` tolerates manifests
            # written before these fields existed (→ None / unknown).
            iso=getattr(rec, "iso", None),
            aperture_f=getattr(rec, "aperture_f", None),
            shutter_speed_s=getattr(rec, "shutter_speed_s", None),
            focal_length_mm=getattr(rec, "focal_length_mm", None),
            flash_fired=getattr(rec, "flash_fired", None),
            lens_model=getattr(rec, "lens_model", None),
        ))

    camera = m.Camera(
        camera_id=camera_id,
        is_phone=(bucket == CAPTURED_PHONES_SUBDIR),
        applied_offset_seconds=(round(offset_hours * 3600) if offset_hours else None),
        applied_at=(stamp if offset_hours else None),
    )

    eg = gateway.open_event(event_id)
    try:
        eg.add_cameras([camera])  # insert-only-missing (no calibration clobber)
        eg.add_items(items)
    finally:
        eg.close()
    log.info("Capture: recorded %d item(s) for camera %s into event %s",
             len(items), camera_id, event_id)
    return len(items)
