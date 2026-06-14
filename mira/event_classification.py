"""Event classification — pure-logic vocabulary + phase routing seam.

Single source of truth for:

* the closed enum of event **types** (Trip / Session / Occasion / Project /
  Unclassified)
* the curated **subtype** preset lists per type (free-text fallback supported
  at the UI layer — this module just publishes the curated vocabulary)
* the suggested **extras_json** keys per type (the editor binds these as
  labeled rows; unknown keys roundtrip preserved)
* the forward-looking **phase-set seam** — :func:`phases_for_type`,
  :func:`decision_phases_for_type`, :func:`preceding_phase`,
  :func:`following_phase`. Slice A returns the full pipeline for every type;
  the next sprint changes ONLY this module so phase iteration callers don't
  need a sweep. (spec/44 §1.7.)

No Qt, no store reads, no I/O. Importable from every layer.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple


# ── Types (closed enum) ────────────────────────────────────────────────────

EVENT_TYPE_TRIP         = "trip"
EVENT_TYPE_SESSION      = "session"
EVENT_TYPE_OCCASION     = "occasion"
EVENT_TYPE_PROJECT      = "project"
EVENT_TYPE_UNCLASSIFIED = "unclassified"

ALL_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TYPE_TRIP,
    EVENT_TYPE_SESSION,
    EVENT_TYPE_OCCASION,
    EVENT_TYPE_PROJECT,
    EVENT_TYPE_UNCLASSIFIED,
)


_TYPE_LABELS: Dict[str, str] = {
    EVENT_TYPE_TRIP:         "Trip",
    EVENT_TYPE_SESSION:      "Session",
    EVENT_TYPE_OCCASION:     "Occasion",
    EVENT_TYPE_PROJECT:      "Project",
    EVENT_TYPE_UNCLASSIFIED: "Unclassified",
}


def is_known_type(event_type: str) -> bool:
    return event_type in ALL_EVENT_TYPES


def display_label_for_type(event_type: str) -> str:
    """Human-readable label for the type pill / dropdown. Translation seam is
    the caller's job — wrap with ``tr()`` in UI code; this module is i18n-agnostic
    so it can be imported from non-Qt code."""
    return _TYPE_LABELS.get(event_type, _TYPE_LABELS[EVENT_TYPE_UNCLASSIFIED])


def normalize_type(event_type: Optional[str]) -> str:
    """Coerce any value to a known type — unknown / None / empty all collapse
    to ``unclassified``. Used at the I/O boundary (gateway readers, settings
    load) so the rest of the code can assume a valid enum value."""
    if event_type and event_type in ALL_EVENT_TYPES:
        return event_type
    return EVENT_TYPE_UNCLASSIFIED


# ── Subtype presets (curated per type; UI may accept free text too) ───────

#: Subtype presets — **activity-only** per Nelson 2026-06-08. The legacy
#: list mixed duration ("One week"), scope ("International"), and activity
#: ("Roadtrip"); duration and scope are now first-class event columns
#: (see schema.py). The UI keeps the subtype combo EDITABLE so a user can
#: still type a custom value the curated list doesn't cover.
SUBTYPE_PRESETS: Dict[str, Tuple[str, ...]] = {
    EVENT_TYPE_TRIP: (
        "City", "Beach", "Nature", "Adventure", "Wildlife", "Cultural", "Road",
    ),
    EVENT_TYPE_SESSION: (
        "Portrait", "Product", "Event coverage", "Family", "Personal",
    ),
    EVENT_TYPE_OCCASION: (
        "Wedding", "Birthday", "Anniversary", "Graduation", "Memorial",
    ),
    EVENT_TYPE_PROJECT: (
        "Photo essay", "Documentary", "Time-lapse", "Series",
    ),
    EVENT_TYPE_UNCLASSIFIED: (),
}


def subtype_presets_for(event_type: str) -> Tuple[str, ...]:
    """Curated subtype list for ``event_type``, or ``()`` if the type has no
    presets (or is unknown)."""
    return SUBTYPE_PRESETS.get(event_type, ())


def is_preset_subtype(event_type: str, subtype: Optional[str]) -> bool:
    """``True`` iff ``subtype`` is in the curated list for ``event_type``.
    ``False`` for custom (user-typed) subtypes — the dashboard filter groups
    those under a "Custom" chip."""
    if not subtype:
        return False
    return subtype in SUBTYPE_PRESETS.get(event_type, ())


# ── Structured qualifiers (spec/64 — supersedes the spec/52 Scope/Mood/  ──
#    Transport vocabulary). Duration / participants survive as columns; the
#    three retired axes are replaced by Context / Experience Type / Creative
#    Focus (spec/64 §3.2 / §3.3 / §3.4). The dashboard filter rail can query
#    on each in plain SQL (Context + Experience Type are scalar enums;
#    Creative Focus is a JSON array, queried via json_each).

#: Time units the duration picker offers. The per-unit cap (spec/52 §14)
#: retired with spec/64 — the user types a free integer > 0 in the chosen
#: unit (7 days instead of being forced to 1 week).
DURATION_UNITS: Tuple[str, ...] = ("hours", "days", "weeks", "months", "years")

#: Participant categories (multi-select). Stored as a JSON array of these
#: strings in event.participants. Unchanged by spec/64.
PARTICIPANT_OPTIONS: Tuple[str, ...] = (
    "Solo", "Couple", "With Family", "With Kids",
    "With Friends", "With Colleagues", "Client",
)


# ── Context (single-select, spec/64 §3.2) ─────────────────────────────────
#
# The baseline environment of the event — the answer to "what kind of time
# was this?". Single-select; NULL = unset.

CONTEXT_LEISURE           = "leisure"
CONTEXT_PROFESSIONAL_TRIP = "professional_trip"
CONTEXT_HOME_ROUTINE      = "home_routine"

CONTEXT_OPTIONS: Tuple[str, ...] = (
    CONTEXT_LEISURE, CONTEXT_PROFESSIONAL_TRIP, CONTEXT_HOME_ROUTINE,
)

CONTEXT_LABELS: Dict[str, str] = {
    CONTEXT_LEISURE:           "Leisure",
    CONTEXT_PROFESSIONAL_TRIP: "Professional Trip",
    CONTEXT_HOME_ROUTINE:      "Home / Routine",
}

CONTEXT_DESCRIPTIONS: Dict[str, str] = {
    CONTEXT_LEISURE:
        "Pure personal time, vacations, and family life.",
    CONTEXT_PROFESSIONAL_TRIP:
        "Business travel, board meetings, or work events.",
    CONTEXT_HOME_ROUTINE:
        "Activities anchored at your primary residence or local neighborhood.",
}


def is_known_context(value: str) -> bool:
    return value in CONTEXT_OPTIONS


# ── Experience Type (single-select, spec/64 §3.3) ─────────────────────────
#
# The primary vibe, intent, or creative energy of the experience.
# Single-select; NULL = unset.

EXPERIENCE_EXPEDITION_DISCOVERY   = "expedition_discovery"
EXPERIENCE_STUDIO_CRAFT           = "studio_craft"
EXPERIENCE_SLOW_DOWN              = "slow_down"
EXPERIENCE_URBAN_CULTURE          = "urban_culture"
EXPERIENCE_MILESTONES_TRADITIONS  = "milestones_traditions"

EXPERIENCE_TYPE_OPTIONS: Tuple[str, ...] = (
    EXPERIENCE_EXPEDITION_DISCOVERY,
    EXPERIENCE_STUDIO_CRAFT,
    EXPERIENCE_SLOW_DOWN,
    EXPERIENCE_URBAN_CULTURE,
    EXPERIENCE_MILESTONES_TRADITIONS,
)

EXPERIENCE_TYPE_LABELS: Dict[str, str] = {
    EXPERIENCE_EXPEDITION_DISCOVERY:  "Expedition & Discovery",
    EXPERIENCE_STUDIO_CRAFT:          "Studio & Craft",
    EXPERIENCE_SLOW_DOWN:             "The Slow Down",
    EXPERIENCE_URBAN_CULTURE:         "Urban & Culture",
    EXPERIENCE_MILESTONES_TRADITIONS: "Milestones & Traditions",
}

EXPERIENCE_TYPE_DESCRIPTIONS: Dict[str, str] = {
    EXPERIENCE_EXPEDITION_DISCOVERY:
        "Active exploration, tracking wildlife, birding, nature travel, "
        "or heavy outdoor photography.",
    EXPERIENCE_STUDIO_CRAFT:
        "Highly deliberate, technical, or staged creative projects at home "
        "(e.g., complex macro rigs, focus-stacking setups, waterdrop "
        "experiments).",
    EXPERIENCE_SLOW_DOWN:
        "Retreats, quiet weekend getaways, cabins, or anywhere the explicit "
        "goal was rest and disconnecting.",
    EXPERIENCE_URBAN_CULTURE:
        "City breaks, architecture walks, museum visits, dining experiences, "
        "or theater.",
    EXPERIENCE_MILESTONES_TRADITIONS:
        "Birthdays, anniversaries, weddings, family holiday gatherings, "
        "and major life markers.",
}


def is_known_experience_type(value: str) -> bool:
    return value in EXPERIENCE_TYPE_OPTIONS


# ── Creative Focus (multi-select, spec/64 §3.4) ───────────────────────────
#
# Photographic subjects of the event. Stored as a JSON array; empty array =
# blank (user hasn't decided). The special value "none" is the explicit
# "this was not a photo event" — selecting it clears the subjects; selecting
# any subject clears it. The mutual-exclusion rule is enforced UI-side.

CREATIVE_FOCUS_MACRO        = "macro"
CREATIVE_FOCUS_BIRDS        = "birds"
CREATIVE_FOCUS_WILDLIFE     = "wildlife"
CREATIVE_FOCUS_LANDSCAPE    = "landscape"
CREATIVE_FOCUS_URBAN_STREET = "urban_street"
CREATIVE_FOCUS_NONE         = "none"

CREATIVE_FOCUS_OPTIONS: Tuple[str, ...] = (
    CREATIVE_FOCUS_MACRO,
    CREATIVE_FOCUS_BIRDS,
    CREATIVE_FOCUS_WILDLIFE,
    CREATIVE_FOCUS_LANDSCAPE,
    CREATIVE_FOCUS_URBAN_STREET,
    CREATIVE_FOCUS_NONE,
)

CREATIVE_FOCUS_LABELS: Dict[str, str] = {
    CREATIVE_FOCUS_MACRO:        "Macro",
    CREATIVE_FOCUS_BIRDS:        "Birds",
    CREATIVE_FOCUS_WILDLIFE:     "Wildlife",
    CREATIVE_FOCUS_LANDSCAPE:    "Landscape",
    CREATIVE_FOCUS_URBAN_STREET: "Urban / Street",
    CREATIVE_FOCUS_NONE:         "None",
}


def is_known_creative_focus(value: str) -> bool:
    return value in CREATIVE_FOCUS_OPTIONS


# ── Type-specific extras_json keys (suggested vocabulary) ─────────────────
#
# These share the existing ``event.extras_json`` bag with the IPTC location
# facets (``city`` / ``country`` / …). Namespaces don't collide; the editor
# binds known keys as labeled rows and roundtrips unknown ones.

EXTRAS_KEYS_BY_TYPE: Dict[str, Tuple[str, ...]] = {
    EVENT_TYPE_TRIP:     ("countries", "duration_label", "people"),
    EVENT_TYPE_SESSION:  ("target_subject", "people"),
    EVENT_TYPE_OCCASION: ("host", "people"),
    EVENT_TYPE_PROJECT:  ("goal", "subject", "target_artifact", "people"),
    EVENT_TYPE_UNCLASSIFIED: (),
}


def extras_keys_for(event_type: str) -> Tuple[str, ...]:
    """Suggested classification-extras keys for ``event_type``."""
    return EXTRAS_KEYS_BY_TYPE.get(event_type, ())


# ── Tag vocabulary (curated chip set per event_type) ──────────────────────
#
# Nelson 2026-06-06: the Event Info dialog renders tags as a multi-select
# chip grid. The curated set per type is large enough to cover common cases
# without the user having to invent vocabulary. Custom tags are intentionally
# NOT supported in the dialog yet (post-X-1.0 follow-up if the curated set
# proves limiting).

TAG_PRESETS: Dict[str, Tuple[str, ...]] = {
    EVENT_TYPE_TRIP: (
        "Family", "Friends", "Solo", "Couple", "Group",
        "Nature", "Wildlife", "Landscape", "Cities", "Food",
        "Culture", "Architecture", "Beach", "Mountain", "Adventure",
        "Road trip", "Hiking", "Cruise",
    ),
    EVENT_TYPE_SESSION: (
        "Wildlife", "Portrait", "Street", "Macro", "Sports",
        "Architecture", "Landscape", "Studio", "Outdoor",
        "Practice", "Client", "Personal", "Equipment test",
    ),
    EVENT_TYPE_OCCASION: (
        "Wedding", "Birthday", "Graduation", "Anniversary",
        "Reunion", "Concert", "Performance", "Holiday",
    ),
    EVENT_TYPE_PROJECT: (
        "Documentary", "Series", "Book", "Exhibition", "Stock",
        "Editorial", "Portfolio", "Solo", "Collaboration",
    ),
    EVENT_TYPE_UNCLASSIFIED: (),
}


def tag_presets_for(event_type: str) -> Tuple[str, ...]:
    """Curated tag list for ``event_type``, or ``()`` if the type has none."""
    return TAG_PRESETS.get(event_type, ())


# ── Phase-set seam (forward-looking — spec/44 §1.7) ───────────────────────

PHASE_COLLECT = "collect"
PHASE_PICK    = "pick"
PHASE_EDIT    = "edit"
# spec/66 §3 vocabulary delta — phase 4's internal key is now "export";
# "share" survives as a STATE word (the Cuts surface on closed events).
PHASE_EXPORT  = "export"
PHASE_SHARE   = "share"

# The four working phases (spec/66 §1). ``share`` is no longer in this
# tuple — it's a closed-event state, reached through a closed event's
# door, not stepped through. The Phases-page donuts read this order.
ALL_PHASES: Tuple[str, ...] = (
    PHASE_COLLECT, PHASE_PICK, PHASE_EDIT, PHASE_EXPORT,
)

# Subset that records explicit pick/discarded decisions — the funnel +
# PickedRatioDonut consume this (Collect has no K/D bookkeeping).
DECISION_PHASES: Tuple[str, ...] = (
    PHASE_PICK, PHASE_EDIT, PHASE_EXPORT,
)


def phases_for_type(event_type: str) -> Tuple[str, ...]:
    """The ordered phase list for an event of this type.

    **Slice A default:** every type returns :data:`ALL_PHASES`. The next sprint
    will return different lists per type (Session loses Plan + Select; Occasion
    loses Plan + Cull; Trip keeps the full set; …). This is the single point
    of change — every call site reads through this function, so no codebase
    sweep is required when the per-type map lands.

    Unknown / future types fall back to :data:`ALL_PHASES`.
    """
    # Slice A: full pipeline for everyone. Do NOT branch on event_type yet —
    # the next sprint owns that change.
    _ = event_type  # documented unused; the parameter is the contract
    return ALL_PHASES


def decision_phases_for_type(event_type: str) -> Tuple[str, ...]:
    """The kept-count subset of :func:`phases_for_type` — the phases the
    funnel and the PickedRatioDonut iterate. Slice A default:
    :data:`DECISION_PHASES` for every type. Same single-point-of-change
    property as :func:`phases_for_type`."""
    return tuple(p for p in phases_for_type(event_type) if p in DECISION_PHASES)


def preceding_phase(event_type: str, phase: str) -> Optional[str]:
    """The phase immediately before ``phase`` in this type's pipeline, or
    ``None`` if ``phase`` is the first or absent from the pipeline. Used by
    silent-sync to find the "previous phase" to read from when phase order
    varies per type."""
    phases = phases_for_type(event_type)
    if phase not in phases:
        return None
    idx = phases.index(phase)
    return phases[idx - 1] if idx > 0 else None


def following_phase(event_type: str, phase: str) -> Optional[str]:
    """The phase immediately after ``phase`` in this type's pipeline, or
    ``None`` if ``phase`` is the last or absent."""
    phases = phases_for_type(event_type)
    if phase not in phases:
        return None
    idx = phases.index(phase)
    return phases[idx + 1] if idx < len(phases) - 1 else None


# ── Tag suggestions per type (spec/44 — classification panel education) ───
#
# Tags are free-form, but new users often don't know what kinds of tags would
# help them find photos later. Each type gets a small curated set of example
# tags the panel exposes as clickable chips below the Tags input. The user
# can click any chip to add it (it appends to the comma-separated text) or
# type their own — the suggestions are illustrative, not prescriptive.

TAG_SUGGESTIONS_BY_TYPE: Dict[str, Tuple[str, ...]] = {
    EVENT_TYPE_TRIP: (
        "wildlife", "landscape", "sunset", "coast", "mountain",
        "urban", "street", "food", "culture", "family",
    ),
    EVENT_TYPE_SESSION: (
        "golden-hour", "blue-hour", "b&w", "long-exposure", "macro",
        "panning", "motion-blur", "high-key", "low-key", "minimal",
    ),
    EVENT_TYPE_OCCASION: (
        "candid", "group-photo", "milestone", "celebration", "formal",
        "family", "kids", "decoration", "speeches",
    ),
    EVENT_TYPE_PROJECT: (
        "portrait", "series", "b&w", "documentary", "weekly",
        "abstract", "still-life",
    ),
    EVENT_TYPE_UNCLASSIFIED: (
        "candid", "favorite", "share", "sunset", "b&w", "wallpaper",
    ),
}


def tag_suggestions_for(event_type: str) -> Tuple[str, ...]:
    """Example tag chips shown below the panel's Tags input. Editing-only
    aid; nothing in the gateway pipeline reads this list."""
    return TAG_SUGGESTIONS_BY_TYPE.get(
        event_type, TAG_SUGGESTIONS_BY_TYPE[EVENT_TYPE_UNCLASSIFIED],
    )


# ── Triage heuristic (spec/44 Slice E) ─────────────────────────────────────


def suggest_type_from_signals(
    *,
    day_count: int,
    camera_count: int,
    tz_count: int,
) -> Optional[str]:
    """Suggested event_type from coarse signals on an existing event row.

    Used by the triage view's "Suggested type" column to nudge the user
    when there's a reasonable guess; ``None`` means *no clear suggestion*,
    leave the row blank and let the user pick.

    The heuristic stays deliberately narrow:

    * **1 day · 1 camera** → :data:`EVENT_TYPE_SESSION` (a focused single-day
      shoot — birds, macro, etc.)
    * **≥5 days · multi-TZ** → :data:`EVENT_TYPE_TRIP` (multi-day travel)
    * everything else → ``None`` (a 2–4-day event could be a Trip, Session
      series, or Occasion — too ambiguous to guess).
    """
    if day_count == 1 and camera_count == 1:
        return EVENT_TYPE_SESSION
    if day_count >= 5 and tz_count >= 2:
        return EVENT_TYPE_TRIP
    return None
