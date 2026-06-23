"""Typed dataclasses mirroring the spec/53 user-store tables, field-for-field.

One dataclass per table. **Field names mirror column names one-for-one** — the
repo builds SQL column lists from ``dataclasses.fields(cls)``, so the two must
agree. Booleans map to the ``INTEGER 0/1`` columns (the repo coerces at the SQL
boundary). Nullable columns are ``Optional`` with ``None`` defaults; ``NOT NULL``
columns without a SQL default are required (no dataclass default).

Mirrors :mod:`mira.store.models` for the per-event store; the two layers
share the same conventions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------------- #
# Housekeeping singletons (spec/53 §2.1)
# --------------------------------------------------------------------------- #


@dataclass
class InstallationProfile:
    """Singleton (``id`` is always 1). ``name`` is the bundle identifier the
    code-side ``core/feature_flags.py`` map keys on to compute the per-key
    default flag values (XMC = full enthusiast bundle; MC = streamlined
    Persona-1 bundle; custom = user-mixed)."""

    name: str
    created_at: str
    id: int = 1
    extras_json: str = '{}'


# --------------------------------------------------------------------------- #
# Preferences and wizard (spec/53 §2.2)
# --------------------------------------------------------------------------- #


@dataclass
class Setting:
    """Flat KV row. ``value_json`` carries the JSON-encoded value so any shape
    (scalar / list / object) round-trips. Replaces top-level keys of the legacy
    ``settings.rebuild.json``."""

    key: str
    value_json: str
    updated_at: str


@dataclass
class WizardAnswer:
    """One wizard question's answer. Separated from :class:`Setting` so the
    wizard's concern is isolated from regular preferences (spec/53 §2.2)."""

    question_id: str
    answer_json: str
    answered_at: str


# --------------------------------------------------------------------------- #
# Events index (spec/53 §2.3) — replaces events_index.json
# --------------------------------------------------------------------------- #


@dataclass
class EventIndex:
    """Registry row for one event. ``relpath_to_base`` is the load-bearing
    field per ``feedback_relative_paths_from_user_default`` (single absolute
    anchor = the ``photos_base_path`` setting). ``abs_path`` is the
    cross-volume fallback — normally NULL.

    Cached fields are projections of per-event data; they are refreshed when an
    event is closed (or whenever per-day data changes, per spec/52)."""

    event_uuid: str
    relpath_to_base: str
    abs_path: Optional[str] = None
    name_cached: str = ""
    type_cached: Optional[str] = None
    country_cached: Optional[str] = None        # ISO 3166-1 alpha-2; derived from per-day data
    start_date_cached: Optional[str] = None
    end_date_cached: Optional[str] = None
    is_closed_cached: bool = False
    last_opened_at: Optional[str] = None
    extras_json: str = '{}'


# --------------------------------------------------------------------------- #
# Cut templates (spec/61 — schema v2)
# --------------------------------------------------------------------------- #

# The spec/53-era user-level ``Cut`` retired with spec/61 before any build
# wrote a row: event Cuts live in event.db (definitions + file-based
# membership); only TEMPLATES are user-level (cross-event by purpose).


@dataclass
class CutTemplate:
    """One saved Cut RECIPE (spec/61 §2): the New Cut dialog's fields,
    replayable on any event — the pool expression re-evaluates against
    that event's Cuts by TAG (names are the cross-event glue). No
    pre-shipped templates ship (spec/61 §10 #4)."""

    id: str
    name: str
    created_at: str
    pool_expr_json: str = '[]'            # [["+"|"-", "<tag>"], ...]
    style_filter_json: str = '[]'         # [] = All styles
    type_filter: str = "both"             # 'both' | 'photo' | 'video'
    default_state: str = "skipped"        # session starting state
    target_s: Optional[int] = None
    max_s: Optional[int] = None
    photo_s: float = 6.0
    music_category: Optional[str] = None
    extras_json: str = '{}'


# --------------------------------------------------------------------------- #
# People catalog (spec/53 §2.5)
# --------------------------------------------------------------------------- #


@dataclass
class Person:
    """One catalogued person. The reference photo BYTES live at
    ``%LOCALAPPDATA%\\Mira\\people\\<id>.{jpg,png}`` — only the relpath
    is stored here. Per-photo links in ``event.db.photo_person`` reference
    this row's ``id`` via the user-level business key.

    ``representative_face_id`` (spec/90 §5.2, schema v7): opaque pointer to a
    ``face`` row in event.db — the chosen face box that represents this
    Person in dialogs / chips. No FK because the reference spans stores. NULL
    until recognition runs (the default for legacy rows)."""

    id: str
    display_name: str
    created_at: str
    updated_at: str
    reference_photo_relpath: Optional[str] = None
    embedding_json: Optional[str] = None       # face-rec embedding cached (simplest tier; spec/51 §3.13)
    representative_face_id: Optional[str] = None
    extras_json: str = '{}'


# --------------------------------------------------------------------------- #
# User hardware registry (spec/53 §2.6)
# --------------------------------------------------------------------------- #


@dataclass
class UserCamera:
    """One camera the user owns. ``camera_id`` cross-references
    ``event.db.camera.camera_id`` via the same Make+Model business key."""

    camera_id: str
    make: str
    model: str
    created_at: str
    is_phone: bool = False
    owned_since: Optional[str] = None
    extras_json: str = '{}'


# --------------------------------------------------------------------------- #
# Cross-event surface (spec/81 Phase 2; spec/32 §3 + §4) — schema v3
# --------------------------------------------------------------------------- #


@dataclass
class GlobalItem:
    """One row of the cross-event item projection (spec/32 §3). A denormalised
    snapshot of one item plus its enclosing event/day context — synced from
    every event.db on event close + startup reconcile so cross-event resolution
    hits this one file. PK is the composite ``(event_uuid, item_id)``.

    Reconciled names from the spec/32 §3 sketch: ``pick_state`` was
    ``cull_state`` (the locked Pick/Skip verb pair); ``flag`` was ``pick``
    (the portfolio bit, distinct from the decision verb). The ladder rungs
    (spec/81 §2.1) read off:
    ``#collected`` = every row;
    ``#picked``    = ``pick_state == 'picked'``;
    ``#edited``    = ``edit_state == 'picked'`` (the Edit-phase commit;
                     spec/61 §1.1 — edited ≠ exported);
    ``#exported``  = ``has_export == True``.
    """

    event_uuid: str
    item_id: str
    synced_at: str
    event_name: str = ""
    origin_relpath: Optional[str] = None
    export_relpath: Optional[str] = None         # latest exported relpath; NULL if not exported
    capture_time: Optional[str] = None
    kind: Optional[str] = None
    provenance: Optional[str] = None
    classification: Optional[str] = None
    iso: Optional[int] = None
    aperture_f: Optional[float] = None
    shutter_speed_s: Optional[float] = None
    focal_length_mm: Optional[float] = None
    flash_fired: Optional[int] = None         # 0/1; NULL = unknown
    lens_model: Optional[str] = None
    camera_id: Optional[str] = None
    duration_ms: Optional[int] = None
    pick_state: Optional[str] = None
    edit_state: Optional[str] = None
    has_export: bool = False
    country: Optional[str] = None
    country_code: Optional[str] = None
    day_city: Optional[str] = None
    day_sublocation: Optional[str] = None
    stars: Optional[int] = None
    color_label: Optional[str] = None
    flag: Optional[int] = None                # 0/1; NULL = unset
    # Event-level qualifiers (spec/86) — denormalised onto every item of the
    # event. ``participants`` is the same JSON envelope as
    # ``event.participants``. ``event_start`` / ``event_end`` are DERIVED at
    # sync time = min/max of the event's ``trip_day.date`` values, so the
    # cross-event date-range filter prunes whole events without joining
    # back to ``trip_day``. NULL = unset (or no dated days for the span).
    event_type: Optional[str] = None
    event_subtype: Optional[str] = None
    experience_type: Optional[str] = None
    participants: Optional[str] = None        # JSON array of strings
    event_start: Optional[str] = None         # ISO date
    event_end: Optional[str] = None           # ISO date


@dataclass
class SavedFilter:
    """One cross-event Dynamic Collection (spec/32 §4 + spec/81 §2.1). Shape
    is intentionally identical to the per-event ``DynamicCollection`` — the
    spec/81 §2 model is scope-agnostic; cross-event differs only in what
    operands ``expr_json`` admits (the full ladder, not just ``exported``) and
    in the breadth of ``filters_json`` (the full spec/32 §2 catalogue). The
    spec/32 §4 predicate-tree framing reconciles here to ``filters_json``
    (the predicates) + ``expr_json`` (set algebra)."""

    id: str
    tag: str
    created_at: str
    updated_at: str
    description: Optional[str] = None
    expr_json: str = '[]'
    filters_json: str = '{}'
    extras_json: str = '{}'


# --------------------------------------------------------------------------- #
# spec/90 Phase 1 — Recipe + Event Collection (schema v7)
# --------------------------------------------------------------------------- #


@dataclass
class Recipe:
    """One saved Recipe (spec/90 §5.1) — the New Cut / New Collection dialog
    configuration, persisted at the library level so the user replays it
    across events. ``flavour`` discriminates the two dialog faces: ``'cut'``
    (event-scope, audience-facing — no Scope, no hardware / face filters),
    ``'collection'`` (cross-event, curation-facing — full sections).
    ``composition_json`` is the opaque blob that captures Scope, Source,
    Filters, Rules (predicates + verdicts), Otherwise verdict, and
    presentation settings (card style, target/max minutes, photo seconds,
    music). Its shape is dialog-defined — Phase 1 is substrate only.
    UNIQUE(flavour, name) splits the namespace by flavour so a Cut Recipe
    and a Collection Recipe may share a name (spec/90 §5.5)."""

    id: str
    name: str
    flavour: str                              # 'cut' | 'collection'
    composition_json: str
    created_at: str
    updated_at: str
    extras_json: str = '{}'


@dataclass
class EventCollection:
    """One saved Event Collection (spec/90 §5.3) — the cross-event analogue
    of a DC, at the event level. Same set-algebra shape as
    :class:`DynamicCollection` / :class:`SavedFilter`, but the universe is
    EVENTS (not items). Operands the resolver will admit are events (by
    uuid) and other Event Collections (nested grouping). ``filters_json``
    holds the date-range predicate today and grows to the broader
    event-metadata catalogue from spec/86 as needed. Tag namespace is
    global at the user level, ``COLLATE NOCASE UNIQUE``. Empty in Phase 1."""

    id: str
    tag: str
    created_at: str
    updated_at: str
    expr_json: str = '[]'
    filters_json: str = '{}'
    extras_json: str = '{}'


# --------------------------------------------------------------------------- #
# Gear profile (spec/85) — schema v5
# --------------------------------------------------------------------------- #


@dataclass
class GearProfile:
    """One row of the user's gear tag (spec/85 §4). ``kind`` discriminates
    cameras from lenses; ``key`` matches the corresponding
    ``global_items.camera_id`` or ``global_items.lens_model`` so the picker
    (spec/83 §4) and the classifier user-gear-hint tier (spec/85 §5 +
    spec/58) can join on it.

    Two pieces of user intent:

    * ``is_active`` — the "I currently use this" flag. Beats the photo-count
      heuristic in the high-cardinality picker's main / occasional split,
      so a borrowed camera with 300 frames stays out of the way (spec/85
      §1 + §5).
    * ``preferred_genres`` — optional JSON array of :class:`Scenario` keys.
      The classifier slots a user-gear-hint tier above the generic
      unknown-lens fallback (spec/85 §5). NULL = unset.

    User-level by purpose: the photographer's kit spans events.
    """

    kind: str            # 'camera' | 'lens'
    key: str             # camera_id | lens_model — matches global_items
    updated_at: str
    is_active: bool = False
    preferred_genres: Optional[str] = None  # JSON array of genre keys, or NULL


# --------------------------------------------------------------------------- #
# Cross-event Cuts (spec/93 §3, spec/94 Phase 4a-ii) — schema v8
# --------------------------------------------------------------------------- #


@dataclass
class Cut:
    """One cross-event Cut (spec/93 §3 — cross-event Cuts live in mira.db).

    Same field shape as :class:`mira.store.models.Cut` (event.db's per-event
    Cut) so callers that read either side use the same dataclass shape.
    Two semantic shifts here:

    * ``source_dc_id`` is always opaque TEXT — no FK across stores. The
      freeze invariant (spec/81 §5) moves to the gateway: delete a source
      DC and any Cut that pointed at it has its ``source_dc_id`` NULLed;
      the Cut survives via ``expr_snapshot_json``.
    * ``separators`` defaults to ``False`` — cross-event Cuts orient no
      single timeline (spec/81 §3.1). event.db's per-event ``Cut`` keeps
      the True default (spec/61 §4).
    """

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
    separators: bool = False
    overlay_fields_json: str = '[]'
    overlay_mode: Optional[str] = None
    last_exported_at: Optional[str] = None
    # spec/111 — slideshow canvas aspect (mirrors event.db Cut.aspect).
    aspect: str = "16:9"
    extras_json: str = '{}'


@dataclass
class CutMember:
    """One row of a cross-event Cut's membership (spec/93 §3, schema v8).

    ``event_id`` is REQUIRED — by definition a cross-event Cut spans
    events; the source event's UUID routes the export pipeline back to
    the right ``Exported Media/`` (kind='export') or ``Original Media/``
    (kind='grab') tree. ``member_id`` is the content-stable distinguisher
    (export_relpath OR origin_relpath, depending on kind), matching
    event.db's PK convention.

    No FK to event.db — the source event's lineage may move or vanish out
    of band (relocated event, sd-card-wiped); the gateway's sweeps handle
    those cases. ``cut_id`` FK CASCADE drops members on Cut deletion
    (same store, safe).
    """

    cut_id: str
    event_id: str
    added_at: str = ""
    member_id: Optional[str] = None
    kind: str = "export"                          # 'export' | 'grab'
    export_relpath: Optional[str] = None
    origin_relpath: Optional[str] = None

    def __post_init__(self) -> None:
        if self.member_id is None:
            # Auto-derive from the relpath of the kind in use — keeps
            # construction ergonomic when callers pass just the relpath.
            if self.kind == "grab" and self.origin_relpath:
                object.__setattr__(self, "member_id", self.origin_relpath)
            elif self.export_relpath:
                object.__setattr__(self, "member_id", self.export_relpath)


# --------------------------------------------------------------------------- #
# Feature flags (spec/53 §2.7)
# --------------------------------------------------------------------------- #


@dataclass
class FeatureFlag:
    """One feature gate. ``source`` distinguishes coded default / install
    profile / explicit user toggle — see spec/53 §2.7 source semantics."""

    key: str
    enabled: bool
    source: str    # 'default' | 'install_profile' | 'user'
    set_at: str
