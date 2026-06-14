"""Typed dataclasses mirroring the spec/30 tables, field-for-field.

One dataclass per table. **Field names mirror column names one-for-one** — the repo
builds SQL column lists from ``dataclasses.fields(cls)``, so the two must agree.
Booleans map to the ``INTEGER 0/1`` columns (the repo coerces at the SQL boundary).
Nullable columns are ``Optional`` with ``None`` defaults; ``NOT NULL`` columns without
a SQL default are required (no dataclass default). No ``*_json`` blobs remain:
the D4 tone-slider blob retired with the Looks redesign (spec/54 §6 — the tone
payload is the Look choice in real columns); everything is real columns (spec/31).

:class:`EventDocument` is the in-memory aggregate of one whole event: flat lists of
the above. It is the unit that :mod:`mira.store.json_dump` (nested JSON) and
:mod:`mira.store.repo` (SQLite rows) convert to and from. Deliberately flat —
nesting is json_dump's concern, persistence is repo's. The derived cache tables
(:class:`BucketCache` / :class:`BucketMember` / :class:`Clustering`) are **not** in
``EventDocument`` (regenerable; excluded from the backup, schema.CACHE_TABLES).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# --------------------------------------------------------------------------- #
# Event-level tables (spec/30 §3.2–§3.6)
# --------------------------------------------------------------------------- #


@dataclass
class Event:
    """The enforced singleton (``id`` is always 1); ``uuid`` is the stable external
    id the app-level events index keys on. Trip budget is folded in (1:1)."""

    uuid: str
    name: str
    created_at: str
    updated_at: str
    id: int = 1
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_closed: bool = False
    # Classification (spec/44). Closed enum drives dashboard filter, EventCard badge, and the
    # per-type extras-editor row set. Vocabulary + ordering live in `mira.event_classification`.
    # The next sprint makes the per-event phase pipeline depend on `event_type` (Session loses
    # Plan + Select, Occasion loses Plan + Cull, etc. — routed through the phase-set seam in
    # the same module). For Slice A every type still owns the full pipeline.
    event_type: str = "unclassified"
    event_subtype: Optional[str] = None    # free-text; UI offers curated presets per type
    description: str = ""                   # short paragraph; EventCard tooltip + dashboard search
    event_root_abs: Optional[str] = None
    budget_short_target_s: Optional[int] = None
    budget_short_max_s: Optional[int] = None
    budget_long_target_s: Optional[int] = None
    budget_long_max_s: Optional[int] = None
    budget_video_share: Optional[float] = None
    # Structured event qualifiers (spec/64 — supersedes the spec/52
    # Scope/Mood/Transport vocabulary). Duration and participants survive
    # as columns; Scope/Mood/Transport retired and were replaced by
    # Context (baseline environment) / Experience Type (vibe) / Creative
    # Focus (photographic subjects, multi-select). The per-unit duration
    # cap retired with spec/64 — duration_value is just a free integer > 0
    # in the chosen unit.
    duration_value: Optional[int] = None        # > 0 (paired with unit); no per-unit cap
    duration_unit: Optional[str] = None         # hours|days|weeks|months|years
    participants: str = '[]'                    # JSON array of category strings
    context: Optional[str] = None               # leisure|professional_trip|home_routine
    experience_type: Optional[str] = None       # expedition_discovery|studio_craft|slow_down|urban_culture|milestones_traditions
    creative_focus: str = '[]'                  # JSON array of subjects; '["none"]' = explicit "not a photo event"
    # Classification extras only (spec/52 cleanup) — see schema.py DDL comment block
    # for the full per-event-type key vocabulary. No location keys, no people arrays.
    extras_json: str = '{}'


@dataclass
class TripDay:
    day_number: int
    date: Optional[str] = None
    description: str = ""
    location: Optional[str] = None   # user-facing single-string location (spec/52 §6)
    tz_minutes: Optional[int] = None
    hidden: bool = False  # soft-hide: items on a hidden day are disregarded everywhere
    # Structured machine-readable per-day data (country + country_code for dashboard
    # chrome / flag emoji / filter-by-country). User-facing string lives in `location`.
    extras_json: str = '{}'


@dataclass
class Camera:
    """spec/52 retired the reference-camera concept (phone EXIF is the reference
    when present; pair-pick TZ calibration uses phone+camera photo pairs)."""
    camera_id: str
    is_phone: bool = False
    configured_tz_minutes: Optional[int] = None
    applied_offset_minutes: Optional[int] = None
    applied_at: Optional[str] = None


@dataclass
class CameraDayTz:
    """spec/45 Slice TZ-3 — per-(camera, day) declared TZ. The bake computes
    the EXIF offset for one item as ``trip_day.tz_minutes − declared_tz_minutes``;
    the legacy ``camera.applied_offset_minutes`` field is the fallback when
    no row exists for an (item.camera_id, item.day_number) pair.

    ``source`` enum (CHECK in DDL): ``'phone_auto'`` — auto-derived from a
    phone with ``OffsetTimeOriginal``; ``'user_declared'`` — DiscreteTzDialog
    pick; ``'pair_picker'`` — legacy reference+subject photo flow."""
    camera_id: str
    day_number: int
    declared_tz_minutes: int
    source: str
    declared_at: str


@dataclass
class CameraCalibrationPair:
    """One pair-picker calibration record — replaces ``camera.calibration_json``.
    Real FKs to the actual reference + subject photos used (nullable: a pair can
    outlive its anchoring items as a bare offset+timestamps)."""

    id: str
    camera_id: str
    ref_time: str
    camera_time: str
    offset_minutes: int
    created_at: str
    ref_item_id: Optional[str] = None
    subject_item_id: Optional[str] = None


# spec/52 retired Participant, ParticipantDevice (people tracking moved to
# user-level catalog + photo_person), ChecklistItem (per-camera-TZ checklist
# retired with past_photos_cameras), DistributionAction (share-event log no
# longer needed).

# --------------------------------------------------------------------------- #
# Item spine and satellites (spec/30 §3.7–§3.11)
# --------------------------------------------------------------------------- #


@dataclass
class Item:
    """The spine. ONE node per captured/derived unit — a clip/snapshot is an item
    (child of its source video via ``parent_item_id``), virtual until materialised.
    *Virtual* = NULL file identity (``origin_relpath``/``sha256``/``byte_size``/
    ``materialized_at`` all NULL), enforced by an all-or-nothing CHECK in the DDL.
    ``capture_time_*``/``camera_id`` are nullable at the column level (a virtual clip
    inherits its parent's instant) but the DDL forces them present for ``captured``."""

    id: str
    kind: str  # 'photo' | 'video'
    created_at: str
    provenance: str = "captured"  # 'captured' | 'snapshot' | 'clip' | 'stack_output' | 'authored' (spec/52 §4 maps + collages)
    # File identity — NULL while virtual:
    origin_relpath: Optional[str] = None
    sha256: Optional[str] = None
    byte_size: Optional[int] = None
    materialized_at: Optional[str] = None
    materialized_phase: Optional[str] = None  # 'ingest' | 'edit' | None ('pick' retired — spec/56)
    # Identity / placement:
    camera_id: Optional[str] = None
    day_number: Optional[int] = None
    parent_item_id: Optional[str] = None
    capture_time_raw: Optional[str] = None         # virtual EXIF, never mutated
    capture_time_corrected: Optional[str] = None   # derived = raw + offset; sort key
    tz_offset_minutes: int = 0
    tz_source: str = "none"  # 'phone_auto' | 'user_declared' | 'pair_picker' | 'none' (matches camera_day_tz.source + 'none' sentinel)
    classification: Optional[str] = None
    classification_source: Optional[str] = None  # 'auto' | 'user' | None
    classification_rules_version: Optional[str] = None
    classification_needs_review: int = 0  # 1 = auto-classified but uncertain; Select nudge reads this
    classification_confidence: Optional[float] = None  # classifier score 0..1 (spec/58)
    sharpness_score: Optional[float] = None
    sharpness_metric: Optional[str] = None
    duration_ms: Optional[int] = None  # video running time (NULL for stills / un-probed)
    # Per-item Subject (Nelson 2026-06-08) — free-text user annotation: bird
    # species, plant name, person, landmark, anything to research later in
    # external tools (e-bird, iNaturalist, Wikipedia). UI surface TBD; the
    # column is here so the next surface can read/write it without a
    # follow-on schema bump.
    subject: Optional[str] = None
    extras_json: str = '{}'  # sanctioned JSON escape hatch; DEFAULT '{}' so json_set always works
    # EXIF technical facets — captured at ingest from the same exiftool pass; None = unknown/video
    iso: Optional[int] = None            # sensor sensitivity
    aperture_f: Optional[float] = None  # f-number (e.g. 2.8)
    shutter_speed_s: Optional[float] = None  # in seconds (e.g. 0.0005 = 1/2000)
    focal_length_mm: Optional[float] = None  # actual focal length (not 35mm-equiv)
    flash_fired: Optional[bool] = None  # True = flash fired; None = unknown
    lens_model: Optional[str] = None    # lens string from EXIF LensModel tag
    # Bracket detection — None = not a bracket; populated by ingest bracket detector
    bracket_group_id: Optional[str] = None  # shared id across all frames of one bracket set
    bracket_role: Optional[str] = None      # 'leader' | 'member'
    quarantine_status: str = "ok"  # 'ok' | 'no_timestamp' | 'recovered'
    recovered_from_filename: bool = False


@dataclass
class PhaseState:
    item_id: str
    phase: str  # 'pick' | 'edit'  (spec/52: 'share' dropped — Cut walks per spec/51 don't use phase state)
    state: str = "skipped"  # 'skipped' | 'candidate' | 'picked'
    derived_dirty: bool = False
    decided_at: Optional[str] = None
    committed_at: Optional[str] = None


@dataclass
class VideoMarker:
    """One user cut point on a source video (spec/56 §1). The implicit start/end
    markers are never stored — zero rows means the video is one segment. Markers
    MOVE without touching segment identity (``at_ms`` updates in place); insert
    splits the containing segment, delete merges two. The gateway owns those
    rules (:meth:`EventGateway.add_video_marker` and friends)."""

    id: str
    video_item_id: str
    at_ms: int
    created_at: str


@dataclass
class VideoSegment:
    """1:1 satellite for segment items (spec/56 §1). A segment's identity is its
    POSITION in the marker order (``seg_index``), never milliseconds — geometry is
    derived from :class:`VideoMarker` rows at read time
    (``core.video_segments.segment_bounds``). ``video_item_id`` mirrors
    ``item.parent_item_id`` (the acknowledged denormalization, hosting
    ``UNIQUE(video_item_id, seg_index)``); the gateway keeps them in lockstep and
    maintains ``count(segments) = count(markers) + 1`` with dense indexes."""

    item_id: str
    video_item_id: str
    seg_index: int
    created_at: str


@dataclass
class VideoSnapshot:
    """1:1 satellite for snapshot items (spec/56 §1) — the point on the source
    timeline. Creation auto-Picks the snapshot (``phase_state`` edit/picked);
    its development state is a photo :class:`Adjustment`, identical to a photo."""

    item_id: str
    video_item_id: str
    at_ms: int
    created_at: str


@dataclass
class Adjustment:
    """Photo (and snapshot) Edit state. The tone payload is the Look
    CHOICE (spec/54 §6, zero-sliders lock): ``style`` (NULL = item's
    classification), ``look`` (one of
    ``core.photo_auto.available_looks()``) and ``creative_filter``.
    Resolved Params are recomputed deterministically at render/export
    — never persisted. Crop rectangle in ``[0,1]`` columns."""

    item_id: str
    style: Optional[str] = None
    look: str = "natural"
    creative_filter: Optional[str] = None  # spec/54 §8; None = no filter
    crop_x: Optional[float] = None
    crop_y: Optional[float] = None
    crop_w: Optional[float] = None
    crop_h: Optional[float] = None
    crop_angle: float = 0.0
    rotation: int = 0  # 0 | 90 | 180 | 270
    aspect_label: Optional[str] = None
    # Nelson 2026-06-13: Look Strength slider — 0..2 multiplier on the
    # resolved Look Params (engine seam: .scaled(s)). 1.0 = the Look
    # exactly as it ships; 0.0 = identity; 2.0 = exaggerated.
    look_strength: float = 1.0
    edit_exported: bool = False


@dataclass
class VideoAdjustment:
    """Segment Edit refinements, keyed on the SEGMENT item (spec/56 — a segment
    is its own item, provenance ``'clip'``). Shares the Look-choice tone payload
    + crop columns with :class:`Adjustment` (spec/54 §7 #1 — Looks on video,
    uncalibrated) + the per-segment video extras (audio, speed, stabilise, fade;
    spec/56 §1). The trim deltas retired with spec/56 §4 — markers ARE the trim."""

    item_id: str
    look: str = "natural"
    creative_filter: Optional[str] = None  # spec/54 §8; None = no filter
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
    kind: str  # 'focus' | 'exposure'
    action: Optional[str] = None  # 'stacked' | 'picked' | 'skipped' | None
    picked_index: int = -1
    output_item_id: Optional[str] = None  # the merged result, an item (provenance='stack_output')
    day_number: Optional[int] = None


@dataclass
class StackMember:
    bracket_id: str
    item_id: str
    ordinal: int


# --------------------------------------------------------------------------- #
# Share layer: Cuts + people links, lineage (spec/61 + spec/52)
# --------------------------------------------------------------------------- #

# spec/52 retired ShareTag / Subset / SubsetMember / ShareMap. Its PhotoTag
# (item-based Cut membership, the spec/51 plan) retired unused with spec/61 —
# Cut membership is FILE-based (CutMember → Lineage).


@dataclass
class Cut:
    """One event Cut definition (spec/61). ``tag`` is the canonical lowercase
    slug WITHOUT the '#' (display prepends it; the export folder is
    ``Cuts/<tag>/``) — unique per event, produced by ``core.cut_names``.
    ``target_s``/``max_s`` NULL = no time limit. ``pool_expr_json`` is the
    recipe: ``[["+"|"-", "<tag>"], ...]`` evaluated left to right, where
    ``"exported"`` names the built-in live query. ``style_filter_json`` ``[]``
    = All styles. The built-in #exported is never a row in this table."""

    id: str
    tag: str
    created_at: str
    updated_at: str
    target_s: Optional[int] = None
    max_s: Optional[int] = None
    photo_s: float = 6.0
    pool_expr_json: str = '[]'
    style_filter_json: str = '[]'
    type_filter: str = "both"  # 'both' | 'photo' | 'video'
    default_state: str = "skipped"  # 'picked' | 'skipped' — the session's starting state
    music_category: Optional[str] = None  # audio-library subdir name; None = no music
    last_exported_at: Optional[str] = None
    extras_json: str = '{}'  # escape hatch; holds card_style ('black'|'single'|'multi')


@dataclass
class CutMember:
    """One exported FILE's membership in one Cut (spec/61 §1.2) — references
    ``Lineage`` by its PK, so an export record's deletion drops the file from
    every Cut and a Cut's deletion cascades its membership away. Two exports
    of one photo are two distinct candidate members."""

    cut_id: str
    export_relpath: str
    added_at: str


@dataclass
class PhotoPerson:
    """Per-photo link to the user-level people catalog (the catalog itself is
    NOT in event.db — it lives at ``%LOCALAPPDATA%\\Mira\\``). Set up now
    so the people-tagging feature lands without a migration; unused until then.

    ``source='user'`` — explicit user tag (confidence unused / NULL).
    ``source='auto'`` — face-match suggestion; confidence is the matcher score."""

    item_id: str
    person_id: str
    source: str               # 'user' | 'auto'
    tagged_at: str
    confidence: Optional[float] = None


@dataclass
class Lineage:
    """Export traceability with real, discriminated FKs. ``source_kind='item'`` ⇒
    1→1 ``source_item_id``; ``source_kind='bracket'`` ⇒ N→1 ``source_bracket_id``
    (focus/exposure stacks — the only durable N→1 export in v1).

    ``recipe_json`` + ``exported_at`` (spec/54 §8, versions-as-exports):
    append-only snapshot of the recipe AND resolved Params this export
    rendered with — archival, never re-read for rendering. ``exported_at``
    orders a photo's versions in the Cut picker."""

    export_relpath: str
    phase: str  # 'edit' | 'share'
    source_kind: str  # 'item' | 'bracket'
    source_item_id: Optional[str] = None
    source_bracket_id: Optional[str] = None
    recipe_json: Optional[str] = None
    exported_at: Optional[str] = None


# --------------------------------------------------------------------------- #
# Durable bucket soft-state (spec/30 §3.17)
# --------------------------------------------------------------------------- #


@dataclass
class Bucket:
    """Durable soft-state ONLY (reviewed/browsed/cursor/nudge/default). ``bucket_key``
    is FK-less by design — it is a content-stable recomputed id that must survive a
    membership-preserving cache recompute (spec/30 §5)."""

    bucket_key: str
    phase: str
    default_state: str = "skipped"  # 'skipped' | 'picked'
    reviewed: bool = False
    browsed: bool = False
    nudge_dismissed: bool = False
    current_index: int = 0


@dataclass
class ItemVisit:
    """Day Grid visited tick (spec/32 §2.10, §8.6) — per-(item, phase) "user drilled
    into this cell" bit. Sibling of ``Bucket.browsed`` for non-cluster cells. Real FK
    to ``item.item_id`` with cascade delete; writes go through
    ``EventGateway.set_item_visited`` (ON CONFLICT DO UPDATE upsert)."""

    item_id: str
    phase: str  # 'pick' | 'edit'  (spec/52: 'share' dropped — Cut walks per spec/51 use auto-exclusion instead)
    visited: bool = False
    updated_at: str = ""


# --------------------------------------------------------------------------- #
# Derived cache layer (spec/30 §3.18) — NOT in EventDocument / the backup JSON.
# Regenerable from a re-scan; invalidated per (phase, day_number) fingerprint.
# --------------------------------------------------------------------------- #


@dataclass
class BucketCache:
    """One cached bucket's structural metadata (membership lives in
    :class:`BucketMember`). ``day_number`` is a real nullable FK (NULL = undated) —
    no more ``day_key`` free-TEXT sentinel."""

    bucket_key: str
    phase: str
    kind: str  # focus_bracket|exposure_bracket|burst|moment|individual|video|video_moment
    day_number: Optional[int] = None
    title: str = ""
    detection_source: str = ""
    camera: str = ""
    ordinal: int = 0


@dataclass
class BucketMember:
    """One item's membership in a cached bucket, in scanner order. Composite FK +
    cascade to :class:`BucketCache` (the missing v3 integrity)."""

    bucket_key: str
    phase: str
    item_id: str
    ordinal: int = 0


@dataclass
class Clustering:
    """The per-(phase, day) clustering fingerprint — the cache-invalidation key."""

    phase: str
    fingerprint: str
    computed_at: str
    day_number: Optional[int] = None


# --------------------------------------------------------------------------- #
# The whole-event aggregate
# --------------------------------------------------------------------------- #


@dataclass
class EventDocument:
    """One whole event as flat lists. The unit of round-trip (store ⇄ JSON).

    Excludes the ``schema_info`` table (schema/app version, created_at, event_id) —
    a DB-creation fact owned by :mod:`mira.store.schema`, regenerated on restore.
    Also excludes the derived cache tables (schema.CACHE_TABLES). Trip budget is folded
    into :class:`Event` (no separate list)."""

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
    cuts: List[Cut] = field(default_factory=list)
    cut_members: List[CutMember] = field(default_factory=list)
    photo_persons: List[PhotoPerson] = field(default_factory=list)
    buckets: List[Bucket] = field(default_factory=list)
    item_visits: List[ItemVisit] = field(default_factory=list)
    lineage: List[Lineage] = field(default_factory=list)
