"""Data models for the Photos Workflow Manager."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional
import uuid


class EventStatus(Enum):
    """Event lifecycle status — past participle naming (see v2_design.md §3).

    Each value represents a completed milestone. Ordering follows the state
    machine: PLANNED → PREPARED → LAUNCHED → WRAPPED → PROCESSED → ENHANCED
    → CURATED → ARCHIVED. Skipping forward is allowed when phases don't apply.
    """
    PLANNED = "PLANNED"       # trip/event plan drafted
    PREPARED = "PREPARED"     # pre-event checklist done, ready to launch
    LAUNCHED = "LAUNCHED"     # event active; capture and cull happening
    WRAPPED = "WRAPPED"       # all content captured and first-pass culled
    PROCESSED = "PROCESSED"   # stack merges + video edits done (auto-skip if none)
    ENHANCED = "ENHANCED"     # exposure, noise, basic adjustments applied
    CURATED = "CURATED"       # final narrative + quality selection complete
    ARCHIVED = "ARCHIVED"     # event locked, read-only, backup finalized


@dataclass
class Device:
    name: str          # "Lumix G9", "Celular Nelson", "GoPro"
    device_type: str   # "camera", "action_cam", "phone"


@dataclass
class Participant:
    name: str
    devices: list[Device] = field(default_factory=list)
    phone: str = ""  # e.g. "+5511999999999"


@dataclass
class TripDay:
    day_number: int
    date: date
    description: str
    tz_offset: Optional[float] = None  # UTC offset, e.g. -3.0; None = inherit
    # Geographic / logical location for the day. Used by the Curate
    # workflow to consolidate days into "Medium" / "Short" slideshow
    # buckets that cross day boundaries (e.g. days 3-5 all at
    # "La Fortuna"). Optional — None falls back to "Misc" in those
    # outputs. Pure metadata; doesn't change folder layout on disk.
    location: Optional[str] = None
    # spec/47: per-day ISO 3166-1 alpha-2 country code. Independent from
    # ``location`` (which stays free-text for city/venue/region). Auto-filled
    # at ingest from phone GPS (arrival country wins for travel days);
    # editable in PlanEditorDialog via the Country column. Persisted on the
    # store side as ``trip_day.extras_json["country_code"]``.
    country_code: Optional[str] = None


@dataclass
class ChecklistItem:
    id: str
    label: str
    checked: bool = False
    notes: str = ""


@dataclass
class DistributionAction:
    """Record of a distribution action performed on an event.

    Distribution is not a status — it's metadata. An event can be distributed
    through multiple channels over time without changing its status.
    See v2_design.md §3.7.
    """
    timestamp: str                  # ISO datetime
    channel: str                    # "google_photos", "whatsapp", "tv_slideshow", ...
    item_count: int = 0             # photos/videos exported to this channel
    share_url: str = ""             # set if the channel produced a shareable URL
    notes: str = ""                 # free-text (e.g. "Shared with Aida's family")


@dataclass
class Event:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: EventStatus = EventStatus.PLANNED
    # Open / Closed binary state (F-024, frozen 2026-05-25). User-
    # controlled toggle living on the EventPlanPage. When True, the
    # EventPlanPage hides all modification buttons (Edit Plan,
    # Restore, Adjust TZ, Camera Clocks, Re-import LRC, Relocate,
    # Delete, Open phases) and defaults to the Summary view; the
    # user can still browse curated buckets, print individual slides
    # (F-003), back up the event, and run an Audit. To modify a
    # closed event the user must reopen it explicitly. Supersedes
    # the old 8-value ``EventStatus`` enum as the surface-driving
    # signal — ``status`` stays on the dataclass for archival
    # reasons but the UI no longer reads it. Defaults to False
    # (open) so existing events behave exactly as before until the
    # user closes them.
    is_closed: bool = False
    trip_days: list[TripDay] = field(default_factory=list)
    participants: list[Participant] = field(default_factory=list)
    checklist: list[ChecklistItem] = field(default_factory=list)
    whatsapp_message: str = ""
    google_album_name: str = ""
    google_album_link: str = ""
    notes: str = ""
    photos_base_path: str = ""
    event_settings: dict = field(default_factory=dict)
    distribution_log: list[DistributionAction] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def display_name(self) -> str:
        year = self.start_date.year if self.start_date else ""
        return f"{year} - {self.name}" if year else self.name

    @property
    def checklist_progress(self) -> tuple[int, int]:
        total = len(self.checklist)
        done = sum(1 for item in self.checklist if item.checked)
        return done, total

    @property
    def day_count(self) -> int:
        return len(self.trip_days)
