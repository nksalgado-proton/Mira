"""Typed dataclasses mirroring the spec/30 tables, field-for-field.

One dataclass per table. Field names mirror column names one-for-one — the repo
builds SQL column lists from dataclasses.fields(cls), so the two must agree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Event:
    uuid: str
    name: str
    created_at: str
    updated_at: str
    id: int = 1
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_closed: bool = False
    event_type: str = "unclassified"
    event_subtype: Optional[str] = None
    description: str = ""
    event_root_abs: Optional[str] = None
    budget_short_target_s: Optional[int] = None
    budget_short_max_s: Optional[int] = None
    budget_long_target_s: Optional[int] = None
    budget_long_max_s: Optional[int] = None
    budget_video_share: Optional[float] = None
    duration_value: Optional[int] = None
    duration_unit: Optional[str] = None
    participants: str = '[]'
    context: Optional[str] = None
    experience_type: Optional[str] = None
    creative_focus: str = '[]'
    extras_json: str = '{}'


@dataclass
class TripDay:
    day_number: int
    date: Optional[str] = None
    description: str = ""
    location: Optional[str] = None
    tz_minutes: Optional[int] = None
    hidden: bool = False
    extras_json: str = '{}'


@dataclass
class Camera:
    camera_id: str
    is_phone: bool = False
    configured_tz_minutes: Optional[int] = None
    applied_offset_minutes: Optional[int] = None
    applied_at: Optional[str] = None


@dataclass
class CameraDayTz:
    camera_id: str
    day_number: int
    declared_tz_minutes: int
    source: str
    declared_at: str


@dataclass
class CameraCalibrationPair:
    id: str
    camera_id: str
    ref_time: str
    camera_time: str
    offset_minutes: int
    created_at: str
    ref_item_id: Optional[str] = None
    subject_item_id: Optional[str] = None


@dataclass
class Item:
    id: str
    kind: str
    created_at: str
    provenance: str = "captured"
    origin_relpath: Optional[str] = None
    sha256: Optional[str] = None
    byte_size: Optional[int] = None
    materialized_at: Optional[str] = None
    materialized_phase: Optional[str] = None
    camera_id: Optional[str] = None
    day_number: Optional[int] = None
    parent_item_id: Optional[str] = None
    capture_time_raw: Optional[str] = None
    capture_time_corrected: Optional[str] = None
    tz_offset_minutes: int = 0
    tz_source: str = "none"
    classification: Optional[str] = None
    classification_source: Optional[str] = None
    classification_rules_version: Optional[str] = None
    classification_needs_review: int = 0
    classification_confidence: Optional[float] = None
    sharpness_score: Optional[float] = None
    sharpness_metric: Optional[str] = None
    duration_ms: Optional[int] = None
    subject: Optional[str] = None
    extras_json: str = '{}'
    iso: Optional[int] = None
    aperture_f: Optional[float] = None
    shutter_speed_s: Optional[float] = None
    focal_length_mm: Optional[float] = None
    flash_fired: Optional[bool] = None
    lens_model: Optional[str] = None
    bracket_group_id: Optional[str] = None
    bracket_role: Optional[str] = None
    quarantine_status: str = "ok"
    recovered_from_filename: bool = False


@dataclass
class PhaseState:
    item_id: str
    phase: str
    state: str = "skipped"
    derived_dirty: bool = False
    decided_at: Optional[str] = None
    committed_at: Optional[str] = None


@dataclass
class VideoMarker:
    id: str
    video_item_id: str
    at_ms: int
    created_at: str


@dataclass
class VideoSegment:
    item_id: str
    video_item_id: str
    seg_index: int
    created_at: str


@dataclass
class VideoSnapshot:
    item_id: str
    video_item_id: str
    at_ms: int
    created_at: str


@dataclass
class Adjustment:
    item_id: str
    style: Optional[str] = None
    # Default Look is **Original** = no processing applied (identity; raw
    # capture). A photo only leaves the unedited baseline when the user
    # deliberately picks a Look (Natural is "the default one", still a Look
    # choice), applies a filter, or crops (Nelson 2026-06-18).
    look: str = "original"
    creative_filter: Optional[str] = None
    crop_x: Optional[float] = None
    crop_y: Optional[float] = None
    crop_w: Optional[float] = None
    crop_h: Optional[float] = None
    crop_angle: float = 0.0
    rotation: int = 0
    aspect_label: Optional[str] = None
    look_strength: float = 1.0
    edit_exported: bool = False


@dataclass
class VideoAdjustment:
    item_id: str
    look: str = "natural"
    creative_filter: Optional[str] = None
    crop_x: Optional[float] = None
    crop_y: Optional[float] = None
    crop_w: Optional[float] = None
    crop_h: Optional[float] = None
    box_angle: float = 0.0
    aspect_ratio_label: Optional[str] = None
    style: Optional[str] = None
    rep_frame_ms: Optional[int] = None
    include_audio: bool = True
    rotation_degrees: int = 0
    audio_volume: float = 1.0
    audio_fade_ms: int = 0
    speed: float = 1.0
    stabilise: float = 0.0


@dataclass
class StackBracket:
    bracket_id: str
    kind: str
    action: Optional[str] = None
    picked_index: int = -1
    output_item_id: Optional[str] = None
    day_number: Optional[int] = None
    # spec/109 §5 — who fused the bracket's master. 'external' (default)
    # = adopted from a third-party stacker via the spec/57 round trip;
    # 'mira' = in-app Mertens (exposure brackets only, the spec/109
    # lane). Drives the origin wordmark on the consolidation badge
    # (``Mira`` vs ``ext`` post-spec/108 flatten).
    producer: str = "external"


@dataclass
class StackMember:
    bracket_id: str
    item_id: str
    ordinal: int


@dataclass
class DynamicCollection:
    """A Dynamic Collection (spec/81 §2) — a FORMULA, resolved live to a set of
    files. The live-query noun: set algebra over operands + filters, never a
    stored member set. ``tag`` unique per event in a SEPARATE namespace from
    :class:`Cut`. ``expr_json`` = ordered [[op, operand], ...]; ``filters_json``
    = {"styles":[...],"media_type":...}."""

    id: str
    tag: str
    created_at: str
    updated_at: str
    expr_json: str = '[]'
    filters_json: str = '{}'
    extras_json: str = '{}'


@dataclass
class Cut:
    """One Cut (spec/81 §3) — a FROZEN materialisation of a DC.
    ``expr_snapshot_json`` is the formula frozen at pin; members live in
    :class:`CutMember`. ``source_dc_id`` is the DC pinned from (NULL =
    ad-hoc / source DC deleted — the freeze invariant moved to the gateway in
    schema v8; spec/81 Phase 2). ``source_dc_kind`` discriminates: 'event'
    (the id is in this event.db's ``dynamic_collection``), 'user' (the id is
    in mira.db's ``saved_filter`` — a cross-event Cut), NULL (legacy / unset
    — readers treat as 'event' for back-compat). Style + media filters live
    on the DC, not here. ``overlay_fields_json`` [] = off; ``overlay_mode``
    'embedded'|'burn_in'|None. ``separators`` default ON."""

    id: str
    tag: str
    created_at: str
    updated_at: str
    source_dc_id: Optional[str] = None
    source_dc_kind: Optional[str] = None        # 'event' | 'user' | None (legacy)
    expr_snapshot_json: str = '[]'
    target_s: Optional[int] = None
    max_s: Optional[int] = None
    photo_s: float = 6.0
    default_state: str = "skipped"
    music_category: Optional[str] = None
    separators: bool = True
    overlay_fields_json: str = '[]'
    overlay_mode: Optional[str] = None
    last_exported_at: Optional[str] = None
    extras_json: str = '{}'


@dataclass
class CutMember:
    """One row of a Cut's membership.

    ``event_id`` (schema v8, spec/81 Phase 2 Item 4): NULL = legacy event-
    scope (the member is from THIS event's lineage); non-NULL = the source
    event's UUID for cross-event Cut members whose bytes live in another
    event's ``Exported Media/``.

    ``kind`` (schema v9, spec/81 Phase 2 Item 6, spec/61 §6 + §8): the
    grab-originals discriminator. ``'export'`` = the legacy shape, member's
    bytes live in the source event's ``Exported Media/<export_relpath>``.
    ``'grab'`` = the source event has no lineage row yet (#collected /
    #picked / #edited rungs); the export pipeline copies the ORIGINAL bytes
    from ``Original Media/<origin_relpath>``. ``member_id`` is the
    content-stable PK distinguisher — the export_relpath OR origin_relpath
    depending on kind; auto-derived in ``__post_init__`` when omitted so
    legacy callers that pass only the v7 fields still construct cleanly."""

    cut_id: str
    export_relpath: Optional[str] = None
    added_at: str = ""
    member_id: Optional[str] = None
    kind: str = "export"                          # 'export' | 'grab'
    origin_relpath: Optional[str] = None
    event_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.member_id is None:
            # Auto-derive from the relpath of the kind in use. Legacy callers
            # (v7-shape construction) only pass export_relpath; this keeps them
            # working without touching their call site.
            self.member_id = self.origin_relpath if self.kind == "grab" else self.export_relpath


@dataclass
class PhotoPerson:
    item_id: str
    person_id: str
    source: str
    tagged_at: str
    confidence: Optional[float] = None


@dataclass
class Face:
    """One detected face on an item (spec/90 §5.2). ``person_id`` references
    the library-level ``person`` table in mira.db — opaque TEXT because no FK
    can span stores (NULL = unrecognized, same shape as ``photo_person``).
    Bounding box is normalised 0..1 over the item's pixel space so the
    coordinates stay correct across thumb / proxy / original tiers."""

    id: str
    item_id: str
    detected_at: str
    person_id: Optional[str] = None
    bbox_x: Optional[float] = None
    bbox_y: Optional[float] = None
    bbox_w: Optional[float] = None
    bbox_h: Optional[float] = None
    confidence: Optional[float] = None


@dataclass
class Lineage:
    export_relpath: str
    phase: str
    source_kind: str
    source_item_id: Optional[str] = None
    source_bracket_id: Optional[str] = None
    recipe_json: Optional[str] = None
    exported_at: Optional[str] = None
    # spec/72 §1 / spec/89 §1.4 Model B — the unambiguous lineage
    # signal. 'mira_render' = Mira produced the file via spec/60 (the
    # default, so existing call sites stay unchanged). 'third_party' =
    # the file came from an external editor (LRC / Helicon / etc.) and
    # was hardlinked into Exported Media/ by the return scanner. The
    # displayed badge wordmark (LRC vs Helicon vs CO vs generic ext)
    # is inferred from the filename at the badge layer.
    provenance: str = "mira_render"
    # spec/89 §1.2 / Block 1 D2.B — per-version intent for items that
    # form a versions cluster (2+ rows for one source item). Members
    # enter in 'compare' so the cluster cover paints Compare orange
    # until the user resolves each version to 'picked' (ship) or
    # 'skipped' (drop on next Export run). Single-version flat cells
    # ignore this field — their intent reads from phase_state(edit).
    intent_state: str = "picked"


@dataclass
class Bucket:
    bucket_key: str
    phase: str
    default_state: str = "skipped"
    reviewed: bool = False
    browsed: bool = False
    nudge_dismissed: bool = False
    current_index: int = 0


@dataclass
class ItemVisit:
    item_id: str
    phase: str
    visited: bool = False
    updated_at: str = ""


@dataclass
class BucketCache:
    bucket_key: str
    phase: str
    kind: str
    day_number: Optional[int] = None
    title: str = ""
    detection_source: str = ""
    camera: str = ""
    ordinal: int = 0


@dataclass
class BucketMember:
    bucket_key: str
    phase: str
    item_id: str
    ordinal: int = 0


@dataclass
class Clustering:
    phase: str
    fingerprint: str
    computed_at: str
    day_number: Optional[int] = None


@dataclass
class EventDocument:
    event: Event
    trip_days: List[TripDay] = field(default_factory=list)
    cameras: List[Camera] = field(default_factory=list)
    camera_calibration_pairs: List[CameraCalibrationPair] = field(default_factory=list)
    camera_day_tz: List[CameraDayTz] = field(default_factory=list)
    items: List[Item] = field(default_factory=list)
    phase_states: List[PhaseState] = field(default_factory=list)
    video_markers: List[VideoMarker] = field(default_factory=list)
    video_segments: List[VideoSegment] = field(default_factory=list)
    video_snapshots: List[VideoSnapshot] = field(default_factory=list)
    adjustments: List[Adjustment] = field(default_factory=list)
    video_adjustments: List[VideoAdjustment] = field(default_factory=list)
    stacks: List[StackBracket] = field(default_factory=list)
    stack_members: List[StackMember] = field(default_factory=list)
    dynamic_collections: List[DynamicCollection] = field(default_factory=list)
    cuts: List[Cut] = field(default_factory=list)
    cut_members: List[CutMember] = field(default_factory=list)
    photo_persons: List[PhotoPerson] = field(default_factory=list)
    faces: List[Face] = field(default_factory=list)
    buckets: List[Bucket] = field(default_factory=list)
    item_visits: List[ItemVisit] = field(default_factory=list)
    lineage: List[Lineage] = field(default_factory=list)
