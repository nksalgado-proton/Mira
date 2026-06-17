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
    this row's ``id`` via the user-level business key."""

    id: str
    display_name: str
    created_at: str
    updated_at: str
    reference_photo_relpath: Optional[str] = None
    embedding_json: Optional[str] = None       # face-rec embedding cached (simplest tier; spec/51 §3.13)
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
