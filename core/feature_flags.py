"""Feature flags — the runtime "lego assembly" (spec/53 §2.7).

What it does
------------

Each flag KEY is an app-code constant. The full set is the closed v1 vocabulary
in :data:`FLAG_KEYS`; new flags ship in code, never invented at runtime. Each
**installation profile** (XMC / MC / custom) carries the per-key DEFAULTS in
:data:`DEFAULTS_BY_PROFILE` — so an install whose ``mira.db.feature_flag``
table has no row for a key still gets the right behaviour for its bundle.

How it's used (Nelson 2026-06-08, locked discipline)
----------------------------------------------------

- Flags are **read at startup** via :func:`load_flags` and applied per surface
  as it constructs. The returned :class:`Flags` object is frozen for the
  process lifetime — there is no flag-flipping mid-session.
- Every UI surface that's flag-gated reads ``flags.<key>`` at its construct
  site; the value is constant within the process lifetime.
- A "Reload flags" path (Settings dialog → toggle → "Restart to apply") writes
  a ``source='user'`` row + prompts a restart; this module is reloaded by the
  fresh process.

The decision precedence is per-key:

1. A ``feature_flag`` row with ``source='user'`` (explicit user toggle) wins.
2. Else a row with ``source='install_profile'`` (installer wrote it on first
   launch) wins.
3. Else the per-profile DEFAULT from :data:`DEFAULTS_BY_PROFILE` is applied
   (``source='default'`` — no row exists, falls through this code).

Per spec/53 §2.7.1 the v1 axis is **Premium vs Basic**: XMC turns on the full
enthusiast feature set; MC turns on the streamlined Persona-1 subset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, FrozenSet

if TYPE_CHECKING:
    from mira.user_store.repo import UserStore


# --------------------------------------------------------------------------- #
# The v1 flag-key vocabulary (spec/53 §2.7.1)
# --------------------------------------------------------------------------- #

#: The full v1 flag-key set. ANY runtime read of a key NOT in this set returns
#: ``False`` (unknown keys are not feature gates — they're typos).
FLAG_KEYS: FrozenSet[str] = frozenset({
    "feature.cross_event_cuts",
    "feature.tz_correction",
    "feature.quick_sweep",
    "feature.video_clips_snapshots",
    "feature.third_party_roundtrip",
    "feature.audio_export",
    "feature.maps",
    "feature.collages",
    "feature.people_tagging",
    "feature.bracket_detection",
    "feature.bracket_stacking",
    "feature.wizard_custom_rules",
    "feature.advanced_pick_overlays",
    "feature.plan_save_load_csv",
    "feature.advanced_edit_controls",
    "feature.event_lifecycle_close",
    "feature.detailed_event_types",
})


# --------------------------------------------------------------------------- #
# Per-profile DEFAULT values for every key (spec/53 §2.7.1 table)
# --------------------------------------------------------------------------- #

#: XMC bundle — the full enthusiast feature set. Every Premium gate is on.
_XMC_DEFAULTS: Dict[str, bool] = {key: True for key in FLAG_KEYS}

#: MC bundle — the streamlined Persona-1 feature set. Every Premium gate is off
#: by default; a small subset may light up once the MC profile is designed
#: (spec/53 §2.7.1 leaves the exact MC default subset TBD for some keys, but
#: the v1 contract is "all Premium gates off" — the streamlined experience is
#: defined by what's still on, not what's turned off).
_MC_DEFAULTS: Dict[str, bool] = {key: False for key in FLAG_KEYS}

#: ``custom`` bundle — user-mixed install (rare per spec/53 §2.7). Falls back
#: to the XMC defaults so the user starts from the enthusiast surface; they
#: turn things off via the Settings dialog (which writes ``source='user'``
#: rows that override these defaults).
_CUSTOM_DEFAULTS: Dict[str, bool] = dict(_XMC_DEFAULTS)


#: The installation-profile → defaults map. Keyed by ``installation_profile.name``.
DEFAULTS_BY_PROFILE: Dict[str, Dict[str, bool]] = {
    "XMC": _XMC_DEFAULTS,
    "MC": _MC_DEFAULTS,
    "custom": _CUSTOM_DEFAULTS,
}


def default_for(key: str, profile: str) -> bool:
    """The coded default value for one flag key under one profile.

    Unknown profile names fall back to the XMC defaults so the install never
    silently degrades to "everything off" if the profile row gets corrupted —
    XMC is the most generous bundle, and an explicit user toggle still wins
    via the precedence rules in :func:`load_flags`.

    Unknown keys (not in :data:`FLAG_KEYS`) return ``False``.
    """
    if key not in FLAG_KEYS:
        return False
    return DEFAULTS_BY_PROFILE.get(profile, _XMC_DEFAULTS)[key]


# --------------------------------------------------------------------------- #
# The frozen Flags object — what UI surfaces read at construct time
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Flags:
    """Effective flag values at startup. Each attribute corresponds to one key
    in :data:`FLAG_KEYS` (the leading ``feature.`` prefix is stripped — e.g.
    ``feature.cross_event_cuts`` becomes the attribute ``cross_event_cuts``).

    Frozen by ``@dataclass(frozen=True)`` — surfaces read these as constants
    for the process lifetime. To change a flag after startup, the user toggles
    it in Settings (which writes a ``source='user'`` row) and the app prompts
    a restart; the next process load builds a fresh ``Flags`` with the new
    values.
    """

    cross_event_cuts: bool
    tz_correction: bool
    quick_sweep: bool
    video_clips_snapshots: bool
    third_party_roundtrip: bool
    audio_export: bool
    maps: bool
    collages: bool
    people_tagging: bool
    bracket_detection: bool
    bracket_stacking: bool
    wizard_custom_rules: bool
    advanced_pick_overlays: bool
    plan_save_load_csv: bool
    advanced_edit_controls: bool
    event_lifecycle_close: bool
    detailed_event_types: bool

    def is_on(self, key: str) -> bool:
        """Read a flag by its full ``feature.X`` key string. Convenient for
        config-driven gating where the key is in a registry; the typed
        attribute form is preferred at construct sites."""
        if key not in FLAG_KEYS:
            return False
        return bool(getattr(self, key.removeprefix("feature.")))


# --------------------------------------------------------------------------- #
# Load — the startup entry point
# --------------------------------------------------------------------------- #


def load_flags(store: "UserStore") -> Flags:
    """Build the effective :class:`Flags` object from ``mira.db``.

    Precedence per key, highest first:

    1. ``feature_flag`` row with ``source='user'`` (explicit user toggle).
    2. ``feature_flag`` row with ``source='install_profile'`` (set on first
       launch per the installation profile).
    3. The per-profile coded default from :data:`DEFAULTS_BY_PROFILE`.

    If no ``installation_profile`` row exists, the profile is treated as
    ``'XMC'`` (the conservative dev default — same fallback the one-shot
    importer uses on first launch when no installer-written side channel is
    present).
    """
    # Local import: avoid a top-level cycle (the user_store layer is the
    # substrate; this module sits above it).
    from mira.user_store import models as user_m

    profile_row = store.get(user_m.InstallationProfile, 1)
    profile_name = profile_row.name if profile_row else "XMC"

    # Two passes: profile rows first, then user rows override.
    effective: Dict[str, bool] = {
        key: default_for(key, profile_name) for key in FLAG_KEYS
    }
    for flag in store.query_by(user_m.FeatureFlag, source="install_profile"):
        if flag.key in FLAG_KEYS:
            effective[flag.key] = bool(flag.enabled)
    for flag in store.query_by(user_m.FeatureFlag, source="user"):
        if flag.key in FLAG_KEYS:
            effective[flag.key] = bool(flag.enabled)

    return Flags(**{key.removeprefix("feature."): effective[key] for key in FLAG_KEYS})
