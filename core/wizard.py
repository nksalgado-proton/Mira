"""Wizard state persistence + scenario generation.

The first-run wizard converts the user's shooting habits into a
user-specific scenario library that supplements the built-in
classifier rules. Each genre block produces one ``user-<genre>.json``
scenario file under ``%LOCALAPPDATA%/Mira/scenarios/``.

This module is the *core* (no Qt) side of the wizard: state schema,
load/save, answer normalisation, scenario generation. The UI side
(step widgets + host dialog) lives in ``ui/wizard/``.

Per ``docs/04-wizard-question-bank.md`` and ``docs/07-scenario-schema.md``.
Per the **wizard-derives-from-prototype-rules** memory: every question
maps to a parameter an existing rule reads from EXIF. We add habit
filters; we don't reinvent the classification logic.

Current block coverage: **Macro only** (this iteration). Subsequent
iterations add Wildlife / Landscape / Portrait / Astro / etc. blocks
one at a time, each block standing alone — the user can run any
subset of blocks at any time.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.settings import user_data_dir


log = logging.getLogger(__name__)


# ── Persistence constants ───────────────────────────────────────────


WIZARD_STATE_SCHEMA_VERSION = 1
WIZARD_STATE_FILENAME = "wizard_state.json"
SCENARIOS_SUBDIR = "scenarios"
WIZARD_VERSION = "1.2"  # 1.2 added the Capture & Timezones section (#96).


# ── Step keys ───────────────────────────────────────────────────────


STEP_WELCOME = "welcome"
# Task #96 (#1d) — Capture & Timezones section (3 educational
# screens; always applicable; sit between Welcome and Genre Picker
# so new users get the contract-frozen guarantee + pick their
# defaults BEFORE they start opting into genre blocks).
STEP_CAPTURE_OVERVIEW = "capture_overview"
STEP_CAPTURE_CALIBRATION = "capture_calibration"
STEP_CAPTURE_PRECULL = "capture_precull"
STEP_GENRE_PICKER = "genre_picker"
STEP_MACRO_BLOCK = "macro_block"
STEP_WILDLIFE_BLOCK = "wildlife_block"
STEP_SPORTS_BLOCK = "sports_block"
STEP_LANDSCAPE_BLOCK = "landscape_block"
STEP_ASTRO_BLOCK = "astro_block"
STEP_PORTRAIT_BLOCK = "portrait_block"
STEP_FAMILY_BLOCK = "family_block"
STEP_STREET_BLOCK = "street_block"
STEP_TRAVEL_BLOCK = "travel_block"
STEP_VIDEO_BLOCK = "video_block"
STEP_DONE = "done"

# Order matters — STEPS_IN_ORDER is the canonical traversal.
# Genre blocks are conditional (only shown when their genre is
# selected on the picker). The traversal helpers below skip
# inapplicable steps automatically.
STEPS_IN_ORDER: list[str] = [
    STEP_WELCOME,
    STEP_CAPTURE_OVERVIEW,
    STEP_CAPTURE_CALIBRATION,
    STEP_CAPTURE_PRECULL,
    STEP_GENRE_PICKER,
    STEP_MACRO_BLOCK,
    STEP_WILDLIFE_BLOCK,
    STEP_SPORTS_BLOCK,
    STEP_LANDSCAPE_BLOCK,
    STEP_ASTRO_BLOCK,
    STEP_PORTRAIT_BLOCK,
    STEP_FAMILY_BLOCK,
    STEP_STREET_BLOCK,
    STEP_TRAVEL_BLOCK,
    STEP_VIDEO_BLOCK,
    STEP_DONE,
]


# ── Genre catalog ───────────────────────────────────────────────────


GENRE_MACRO = "macro"
GENRE_WILDLIFE = "wildlife"
GENRE_LANDSCAPE = "landscape"
GENRE_PORTRAIT = "portrait"
GENRE_STREET = "street"
GENRE_SPORTS = "sports"
GENRE_TRAVEL = "travel"
GENRE_ASTRO = "astro"
GENRE_FAMILY = "family"
GENRE_VIDEO = "video"

ALL_GENRES: list[str] = [
    GENRE_MACRO,
    GENRE_WILDLIFE,
    GENRE_LANDSCAPE,
    GENRE_PORTRAIT,
    GENRE_STREET,
    GENRE_SPORTS,
    GENRE_TRAVEL,
    GENRE_ASTRO,
    GENRE_FAMILY,
    GENRE_VIDEO,
]

# Genres whose wizard blocks have actually been implemented.
# As blocks land, more entries move from ALL_GENRES into here.
IMPLEMENTED_GENRES: list[str] = [
    GENRE_MACRO, GENRE_WILDLIFE, GENRE_SPORTS,
    GENRE_LANDSCAPE, GENRE_ASTRO,
    GENRE_PORTRAIT, GENRE_FAMILY,
    GENRE_STREET, GENRE_TRAVEL,
    GENRE_VIDEO,
]


# ── Answer keys + values ────────────────────────────────────────────


# Common "I don't know / skip" value, available on every question.
ANSWER_SKIP = "skip"


# Genre picker — the list of genres the user opted into.
# Stored in answers as a comma-separated string for JSON simplicity
# (the get_selected_genres helper handles parsing).
GENRE_PICKER_KEY = "selected_genres"


# ── Capture & Timezones section (task #96 / #1d) ────────────────
# The 3 screens are educational + initial defaults for the capture
# flow (docs/14 §"The disclosure split"). Unlike genre blocks,
# answers here feed ``settings.json`` (calibration_mode +
# default_pre_cull_mode) instead of producing scenario rule files
# — see ``apply_capture_settings_to_settings``. The overview step
# is text-only (no answer captured) so it has no key here.

CAPTURE_CALIBRATION_KEY = "capture_calibration_mode"
CAPTURE_CALIBRATION_PROMPT = "prompt"
CAPTURE_CALIBRATION_SAVED = "saved"
CAPTURE_CALIBRATION_REFERENCE_PHOTO = "reference_photo"

CAPTURE_PRECULL_KEY = "capture_default_precull_mode"
CAPTURE_PRECULL_VERBATIM = "verbatim"
CAPTURE_PRECULL_PRECULL = "pre_cull"


# Macro block — four habit questions.
MACRO_FOCUS_KEY = "macro_focus_mode"
MACRO_FOCUS_MANUAL = "manual"          # → focus_mode eq manual
MACRO_FOCUS_AF = "af"                  # → focus_mode in [single, continuous]
MACRO_FOCUS_MIXED = "mixed"            # → no constraint (any focus_mode)

MACRO_APERTURE_KEY = "macro_aperture_range"
MACRO_APERTURE_WIDE = "wide"           # f/2.8–f/4
MACRO_APERTURE_MODERATE = "moderate"   # f/4–f/8
MACRO_APERTURE_STOPPED = "stopped"     # f/8–f/16
MACRO_APERTURE_VERY_SMALL = "very_small"  # f/16+
MACRO_APERTURE_MIXED = "mixed"         # → no constraint

MACRO_BRACKETING_KEY = "macro_focus_stacking"
MACRO_BRACKETING_ALWAYS = "always"
MACRO_BRACKETING_SOMETIMES = "sometimes"
MACRO_BRACKETING_NEVER = "never"

MACRO_FLASH_KEY = "macro_flash_usage"
MACRO_FLASH_YES = "yes"
MACRO_FLASH_NO = "no"


# Shared answer values — used by Wildlife, Sports, and future blocks
# whose questions share the same axes. Keeping the values shared keeps
# the scenario builders consistent (one mapping function per axis).

FOCAL_ULTRA_WIDE = "ultra_wide"    # < 24 mm equiv
FOCAL_WIDE = "wide"                # 24–35 mm equiv
FOCAL_NORMAL = "normal"            # 35–70 mm equiv
FOCAL_SHORT_TELE = "short_tele"    # 70–200 mm equiv
FOCAL_LONG_TELE = "long_tele"      # 200–400 mm equiv
FOCAL_VERY_LONG = "very_long"      # 400+ mm equiv
FOCAL_MIXED = "mixed"

DRIVE_BURST_HIGH = "burst_high"
DRIVE_BURST_LOW = "burst_low"
DRIVE_SINGLE = "single"
DRIVE_MIXED = "mixed"

SHUTTER_VERY_FAST = "very_fast"    # 1/2000+
SHUTTER_FAST = "fast"              # 1/500 – 1/2000
SHUTTER_MODERATE = "moderate"      # 1/250 – 1/500
SHUTTER_MIXED = "mixed"


# Wildlife block — four questions.
WILDLIFE_FOCAL_KEY = "wildlife_focal_range"
WILDLIFE_AF_KEY = "wildlife_af_mode"
WILDLIFE_AF_SUBJECT_DETECT = "subject_detect"  # animal/bird detection
WILDLIFE_AF_TRACKING = "tracking"
WILDLIFE_AF_SINGLE_POINT = "single_point_afc"
WILDLIFE_AF_MANUAL = "manual"
WILDLIFE_AF_MIXED = "mixed"
WILDLIFE_DRIVE_KEY = "wildlife_drive_mode"
WILDLIFE_SHUTTER_KEY = "wildlife_shutter_speed"


# Sports block — four questions, same axes as Wildlife with different
# defaults and a different AF/subject question (human/vehicle, not
# animal/bird).
SPORTS_FOCAL_KEY = "sports_focal_range"
SPORTS_AF_KEY = "sports_af_mode"
SPORTS_AF_HUMAN_DETECT = "human_detect"
SPORTS_AF_VEHICLE_DETECT = "vehicle_detect"
SPORTS_AF_TRACKING = "tracking"
SPORTS_AF_SINGLE_POINT = "single_point_afc"
SPORTS_AF_MIXED = "mixed"
SPORTS_DRIVE_KEY = "sports_drive_mode"
SPORTS_SHUTTER_KEY = "sports_shutter_speed"


# Landscape block — four questions.
LANDSCAPE_FOCAL_KEY = "landscape_focal_range"
LANDSCAPE_APERTURE_KEY = "landscape_aperture_range"
LANDSCAPE_APERTURE_WIDER = "wider"            # f/2.8–f/5.6 separation
LANDSCAPE_APERTURE_STANDARD = "standard"      # f/5.6–f/11 standard DOF
LANDSCAPE_APERTURE_STOPPED = "stopped"        # f/11–f/16 maximum DOF
LANDSCAPE_APERTURE_MIXED = "mixed"
LANDSCAPE_LONG_EXPOSURE_KEY = "landscape_long_exposure"
LANDSCAPE_LONG_EXPOSURE_FREQUENT = "frequent"     # >1s exposures common
LANDSCAPE_LONG_EXPOSURE_OCCASIONAL = "occasional"
LANDSCAPE_LONG_EXPOSURE_NEVER = "never"
LANDSCAPE_AF_KEY = "landscape_af_approach"
LANDSCAPE_AF_SINGLE_POINT = "single_point_afs"    # AF-S on infinity / subject
LANDSCAPE_AF_MANUAL_HYPERFOCAL = "manual_hyperfocal"
LANDSCAPE_AF_MIXED = "mixed"


# Astro block — four questions.
ASTRO_SUBTYPE_KEY = "astro_subtype"
ASTRO_SUBTYPE_MILKY_WAY = "milky_way"
ASTRO_SUBTYPE_MOON = "moon"
ASTRO_SUBTYPE_URBAN_NIGHT = "urban_night"
ASTRO_SUBTYPE_STAR_TRAILS = "star_trails"
ASTRO_SUBTYPE_MIXED = "mixed"
ASTRO_FOCAL_KEY = "astro_focal_range"
ASTRO_APERTURE_KEY = "astro_aperture_range"
ASTRO_APERTURE_WIDE_OPEN = "wide_open"         # f/1.4–f/2.8 (Milky Way)
ASTRO_APERTURE_MODERATE = "moderate"           # f/4–f/5.6 (urban)
ASTRO_APERTURE_STOPPED = "stopped"             # f/8–f/11 (moon)
ASTRO_APERTURE_MIXED = "mixed"
ASTRO_SHUTTER_KEY = "astro_shutter_speed"
ASTRO_SHUTTER_VERY_LONG = "very_long"          # 10–30s (Milky Way)
ASTRO_SHUTTER_LONG = "long"                    # 1–10s (urban / city)
ASTRO_SHUTTER_MODERATE = "moderate"            # 1/30–1s (handheld dusk)
ASTRO_SHUTTER_FAST = "fast"                    # 1/250+ (moon)
ASTRO_SHUTTER_MIXED = "mixed"


# Portrait block — four questions.
PORTRAIT_FOCAL_KEY = "portrait_focal_range"
PORTRAIT_AF_KEY = "portrait_af_approach"
PORTRAIT_AF_FACE_EYE = "face_eye"          # → af_area_mode = face_eye
PORTRAIT_AF_SINGLE_POINT = "single_point"  # → focus_mode + af_area_mode
PORTRAIT_AF_MANUAL = "manual"
PORTRAIT_AF_MIXED = "mixed"
PORTRAIT_APERTURE_KEY = "portrait_aperture_range"
PORTRAIT_APERTURE_VERY_WIDE = "very_wide"  # f/1.4 – f/2.8 (separation)
PORTRAIT_APERTURE_MODERATE = "moderate"    # f/2.8 – f/5.6
PORTRAIT_APERTURE_STOPPED = "stopped"      # f/5.6 – f/11 (group)
PORTRAIT_APERTURE_MIXED = "mixed"
PORTRAIT_LIGHTING_KEY = "portrait_lighting"
PORTRAIT_LIGHTING_NATURAL = "natural"      # → flash_fired = false
PORTRAIT_LIGHTING_SPEEDLIGHT = "speedlight"  # → flash_fired = true
PORTRAIT_LIGHTING_STROBE = "strobe"        # → flash_fired = true (studio)
PORTRAIT_LIGHTING_MIXED = "mixed"


# Family block — four questions, similar to Portrait but tuned for
# kids-and-gatherings (kids move; rooms are dim; groups need DOF).
FAMILY_FOCAL_KEY = "family_focal_range"
FAMILY_AF_KEY = "family_af_approach"
FAMILY_AF_FACE_EYE = "face_eye"
FAMILY_AF_SINGLE_POINT = "single_point"
FAMILY_AF_CONTINUOUS = "continuous"        # for kids running around
FAMILY_AF_MIXED = "mixed"
FAMILY_APERTURE_KEY = "family_aperture_range"
FAMILY_APERTURE_WIDE = "wide"              # f/1.8 – f/2.8 (low light)
FAMILY_APERTURE_MODERATE = "moderate"      # f/2.8 – f/5.6
FAMILY_APERTURE_SMALLER = "smaller"        # f/5.6 – f/8 (groups)
FAMILY_APERTURE_MIXED = "mixed"
FAMILY_FLASH_KEY = "family_flash_usage"
FAMILY_FLASH_AVAILABLE = "available"       # → flash_fired = false
FAMILY_FLASH_ON_CAMERA = "on_camera"       # → flash_fired = true
FAMILY_FLASH_OFF_CAMERA = "off_camera"     # → flash_fired = true
FAMILY_FLASH_MIXED = "mixed"


# Street / Documentary block — four questions.
STREET_FOCAL_KEY = "street_focal_range"
STREET_AF_KEY = "street_af_approach"
STREET_AF_SINGLE = "single_afs"            # → focus_mode=single + single_point
STREET_AF_ZONE = "zone"                    # → af_area_mode=zone
STREET_AF_MANUAL_HYPERFOCAL = "manual_hyperfocal"  # → focus_mode=manual
STREET_AF_MIXED = "mixed"
STREET_APERTURE_KEY = "street_aperture_range"
STREET_APERTURE_WIDE = "wide"              # f/1.4–f/2.8
STREET_APERTURE_MODERATE = "moderate"      # f/2.8–f/5.6
STREET_APERTURE_STOPPED = "stopped"        # f/5.6–f/11
STREET_APERTURE_MIXED = "mixed"
STREET_COLOR_KEY = "street_color_rendering"
STREET_COLOR_STANDARD = "standard"         # → photo_style = standard
STREET_COLOR_MONOCHROME = "monochrome"     # → photo_style = monochrome
STREET_COLOR_VIVID = "vivid"               # → photo_style = vivid
STREET_COLOR_CUSTOM = "custom"             # → no clause (per-camera custom)
STREET_COLOR_MIXED = "mixed"


# Travel / General block — four questions. This is the fallback
# genre; the user scenario is mostly reference-card content with
# broad EXIF expectations.
TRAVEL_FOCAL_KEY = "travel_focal_range"
TRAVEL_APERTURE_KEY = "travel_aperture_range"
TRAVEL_APERTURE_WIDE = "wide"              # f/2.8–f/4 separation
TRAVEL_APERTURE_MODERATE = "moderate"      # f/4–f/8 versatile
TRAVEL_APERTURE_STOPPED = "stopped"        # f/8–f/11 for landscape-ish
TRAVEL_APERTURE_MIXED = "mixed"
TRAVEL_AF_KEY = "travel_af_mode"
TRAVEL_AF_SINGLE = "single"
TRAVEL_AF_CONTINUOUS = "continuous"
TRAVEL_AF_MIXED = "mixed"
TRAVEL_DRIVE_KEY = "travel_drive_mode"     # uses shared DRIVE_* values


# Video block — four questions. Per docs/04 "v1 does not over-attempt
# classification of video into specific scenarios" — clips bucket as
# "video / general" and per-scenario classification is a v1.1+ feature.
# The wizard's video scenario is primarily reference-card content.
VIDEO_RECORDING_KEY = "video_recording_mode"
VIDEO_RECORDING_STANDARD = "standard"        # in-camera basic
VIDEO_RECORDING_PHOTO_STYLE = "photo_style"  # Photo Style passthrough
VIDEO_RECORDING_CINELIKE = "cinelike"
VIDEO_RECORDING_V_LOG = "v_log"
VIDEO_RECORDING_HLG = "hlg"
VIDEO_RECORDING_MIXED = "mixed"
VIDEO_RESOLUTION_KEY = "video_resolution_framerate"
VIDEO_RESOLUTION_4K_30 = "4k_30"
VIDEO_RESOLUTION_4K_60 = "4k_60"
VIDEO_RESOLUTION_4K_24 = "4k_24"             # cinematic
VIDEO_RESOLUTION_FHD_60 = "fhd_60"
VIDEO_RESOLUTION_FHD_30 = "fhd_30"
VIDEO_RESOLUTION_MIXED = "mixed"
VIDEO_FOCAL_KEY = "video_focal_range"        # uses shared FOCAL_* values
VIDEO_SUBJECT_KEY = "video_subject_focus"
VIDEO_SUBJECT_WILDLIFE_BEHAVIOR = "wildlife_behavior"
VIDEO_SUBJECT_TRAVEL_BROLL = "travel_broll"
VIDEO_SUBJECT_FAMILY = "family"
VIDEO_SUBJECT_MACRO_BEHAVIOR = "macro_behavior"
VIDEO_SUBJECT_OTHER = "other"
VIDEO_SUBJECT_MIXED = "mixed"


# ── State dataclass ─────────────────────────────────────────────────


@dataclass
class WizardState:
    """In-memory mirror of wizard_state.json.

    ``current_step`` is one of ``STEPS_IN_ORDER``. ``answers`` maps
    question-key → user-chosen value (free strings; per-step
    validation is the step widget's job).
    """
    schema_version: int = WIZARD_STATE_SCHEMA_VERSION
    started_at: str = ""
    last_action_at: str = ""
    completed: bool = False
    current_step: str = STEP_WELCOME
    answers: dict[str, str] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wizard_state_path() -> Path:
    return user_data_dir() / WIZARD_STATE_FILENAME


def _scenarios_dir() -> Path:
    p = user_data_dir() / SCENARIOS_SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── State load / save ───────────────────────────────────────────────


def load_wizard_state() -> WizardState:
    """Load wizard_state.json. Missing/malformed → return a fresh
    ``WizardState`` with ``started_at`` set to now."""
    path = _wizard_state_path()
    if not path.exists():
        return WizardState(started_at=_now_iso(), last_action_at=_now_iso())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("wizard_state.json unreadable (%s); starting fresh", exc)
        return WizardState(started_at=_now_iso(), last_action_at=_now_iso())

    return WizardState(
        schema_version=int(data.get("schema_version", WIZARD_STATE_SCHEMA_VERSION)),
        started_at=str(data.get("started_at", "")) or _now_iso(),
        last_action_at=str(data.get("last_action_at", "")) or _now_iso(),
        completed=bool(data.get("completed", False)),
        current_step=str(data.get("current_step", STEP_WELCOME)),
        answers={str(k): str(v) for k, v in (data.get("answers") or {}).items()},
    )


def save_wizard_state(state: WizardState) -> None:
    """Atomic write of wizard_state.json (same write-then-rename
    pattern as ``core.settings.save_settings``)."""
    state.last_action_at = _now_iso()
    path = _wizard_state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(asdict(state), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def is_wizard_completed() -> bool:
    """Quick check — does the on-disk state say the user finished?"""
    try:
        return load_wizard_state().completed
    except Exception:  # noqa: BLE001
        return False


# ── Genre selection helpers ─────────────────────────────────────────


def get_selected_genres(state: WizardState) -> list[str]:
    """Parse the comma-separated list of selected genres from state.

    Returns only genres present in ``ALL_GENRES`` — drops anything
    unrecognised (defensive against an old wizard_state.json that
    referenced retired genre keys).
    """
    raw = state.answers.get(GENRE_PICKER_KEY, "")
    if not isinstance(raw, str):
        return []
    return [g for g in raw.split(",") if g in ALL_GENRES]


def set_selected_genres(state: WizardState, genres: list[str]) -> None:
    """Store the picker's multi-selection back into the state."""
    state.answers[GENRE_PICKER_KEY] = ",".join(g for g in genres if g in ALL_GENRES)


# ── Step traversal ──────────────────────────────────────────────────


# Map of genre → step key for that genre's block. As more blocks
# land, more entries land here.
GENRE_BLOCK_STEP: dict[str, str] = {
    GENRE_MACRO: STEP_MACRO_BLOCK,
    GENRE_WILDLIFE: STEP_WILDLIFE_BLOCK,
    GENRE_SPORTS: STEP_SPORTS_BLOCK,
    GENRE_LANDSCAPE: STEP_LANDSCAPE_BLOCK,
    GENRE_ASTRO: STEP_ASTRO_BLOCK,
    GENRE_PORTRAIT: STEP_PORTRAIT_BLOCK,
    GENRE_FAMILY: STEP_FAMILY_BLOCK,
    GENRE_STREET: STEP_STREET_BLOCK,
    GENRE_TRAVEL: STEP_TRAVEL_BLOCK,
    GENRE_VIDEO: STEP_VIDEO_BLOCK,
}


def _step_applies(step: str, selected_genres: list[str]) -> bool:
    """True when ``step`` is reachable given the user's genre selection.

    Welcome / Capture-section / genre picker / done are always
    applicable — the capture section is part of the every-user
    educational + initial-defaults flow (docs/14 §"The disclosure
    split"). Genre-block steps only apply when their genre is in
    the selected list.
    """
    if step in (
        STEP_WELCOME,
        STEP_CAPTURE_OVERVIEW,
        STEP_CAPTURE_CALIBRATION,
        STEP_CAPTURE_PRECULL,
        STEP_GENRE_PICKER,
        STEP_DONE,
    ):
        return True
    # Genre blocks: find the genre whose block-step is this one.
    for genre, block_step in GENRE_BLOCK_STEP.items():
        if step == block_step:
            return genre in selected_genres
    # Unknown step — treat as inapplicable rather than crash.
    return False


def next_applicable_step(state: WizardState) -> str | None:
    """Return the next step the user should see, or None if there is
    nothing more (i.e. the wizard is at completion).

    The traversal skips genre blocks whose genre wasn't picked, so the
    Next button moves correctly past inapplicable steps.
    """
    try:
        idx = STEPS_IN_ORDER.index(state.current_step)
    except ValueError:
        idx = 0
    selected = get_selected_genres(state)
    for next_idx in range(idx + 1, len(STEPS_IN_ORDER)):
        candidate = STEPS_IN_ORDER[next_idx]
        if candidate == STEP_DONE:
            return STEP_DONE
        if _step_applies(candidate, selected):
            return candidate
    return None


def previous_applicable_step(state: WizardState) -> str | None:
    """Mirror of :func:`next_applicable_step` going backward.

    Skips inapplicable steps so Back from a genre block lands on the
    genre picker (not on a stale genre block from an earlier session).
    """
    try:
        idx = STEPS_IN_ORDER.index(state.current_step)
    except ValueError:
        return None
    selected = get_selected_genres(state)
    for prev_idx in range(idx - 1, -1, -1):
        candidate = STEPS_IN_ORDER[prev_idx]
        if _step_applies(candidate, selected):
            return candidate
    return None


# ── Scenario generation ─────────────────────────────────────────────


def generate_scenarios_from_answers(answers: dict[str, str]) -> list[Path]:
    """Build a user-scenario JSON per selected genre and write each
    under ``user_data_dir()/scenarios/``.

    Returns the list of file paths that were written. Genres without
    an implemented block (or without enough non-skip answers to make
    a meaningful scenario) are quietly skipped — the user can re-run
    those blocks later.
    """
    written: list[Path] = []
    selected = [
        g for g in answers.get(GENRE_PICKER_KEY, "").split(",")
        if g in IMPLEMENTED_GENRES
    ]
    for genre in selected:
        scenario = _build_scenario(genre, answers)
        if scenario is None:
            continue
        out = _scenarios_dir() / f"{scenario['id']}.json"
        tmp = out.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(scenario, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(out)
        written.append(out)
        log.info("Generated scenario: %s", out)
    return written


def apply_capture_settings_to_settings(
    answers: dict[str, str],
) -> dict[str, str]:
    """Persist the Capture & Timezones picks into ``settings.json``
    (task #96 / #1d).

    Unlike :func:`generate_scenarios_from_answers` (which writes
    per-genre scenario JSONs), the capture section feeds the
    user-level settings that drive every ingest: ``calibration_mode``
    and ``default_pre_cull_mode``. Returns a dict describing what
    was applied (keys = settings keys, values = the strings written)
    so callers can log or surface a confirmation. Keys absent from
    ``answers`` (user backed out before answering one of the picker
    steps) leave the corresponding setting untouched.

    Idempotent: re-calling with the same answers writes the same
    values; with an empty ``answers`` dict it's a no-op.

    docs/14 §"The disclosure split" — wizard and settings mirror
    each other; the wizard is the educational entry-point but
    Settings is the ongoing-change surface for the same controls.
    """
    from core.settings import load_settings, save_settings

    applied: dict[str, str] = {}
    cal = answers.get(CAPTURE_CALIBRATION_KEY, "").strip()
    pre = answers.get(CAPTURE_PRECULL_KEY, "").strip()
    if cal in (
        CAPTURE_CALIBRATION_PROMPT,
        CAPTURE_CALIBRATION_SAVED,
        CAPTURE_CALIBRATION_REFERENCE_PHOTO,
    ):
        applied["calibration_mode"] = cal
    if pre in (CAPTURE_PRECULL_VERBATIM, CAPTURE_PRECULL_PRECULL):
        applied["default_pre_cull_mode"] = pre
    if not applied:
        return applied
    settings = load_settings()
    settings.update(applied)
    try:
        save_settings(settings)
        log.info(
            "Capture settings applied from wizard: %s",
            ", ".join(f"{k}={v}" for k, v in applied.items()),
        )
    except OSError as exc:
        log.warning(
            "Failed to persist capture settings from wizard: %s", exc,
        )
    return applied


def _build_scenario(genre: str, answers: dict[str, str]) -> dict | None:
    """Dispatch to the per-genre builder. Returns None for genres
    that don't yet have a wizard block (the user picked them but the
    block hasn't been implemented yet)."""
    if genre == GENRE_MACRO:
        return _build_macro_scenario(answers)
    if genre == GENRE_WILDLIFE:
        return _build_wildlife_scenario(answers)
    if genre == GENRE_SPORTS:
        return _build_sports_scenario(answers)
    if genre == GENRE_LANDSCAPE:
        return _build_landscape_scenario(answers)
    if genre == GENRE_ASTRO:
        return _build_astro_scenario(answers)
    if genre == GENRE_PORTRAIT:
        return _build_portrait_scenario(answers)
    if genre == GENRE_FAMILY:
        return _build_family_scenario(answers)
    if genre == GENRE_STREET:
        return _build_street_scenario(answers)
    if genre == GENRE_TRAVEL:
        return _build_travel_scenario(answers)
    if genre == GENRE_VIDEO:
        return _build_video_scenario(answers)
    return None


def _build_macro_scenario(answers: dict[str, str]) -> dict:
    """Map the macro-block answers to a docs/07-conforming scenario.

    Each non-skip answer becomes a clause inside ``exif_expectations``;
    skipped questions are absent (broader match — the rule fires for
    more photos). The ``expects_focus_brackets`` flag is at scenario
    top level for the bracket detector (not a classifier predicate).

    Note: this scenario doesn't include a lens condition. The
    built-in macro rules in ``assets/refinement_rules.json`` already
    handle lens detection (T2 tier rules read
    ``lens.potential_scenarios contains "macro"``). The user scenario
    layers habit-level refinements on top — aperture, focus mode,
    flash — and supplies reference-card content for J8.
    """
    exif: dict[str, dict] = {}

    focus = answers.get(MACRO_FOCUS_KEY)
    if focus == MACRO_FOCUS_MANUAL:
        exif["focus_mode"] = {"eq": "manual"}
    elif focus == MACRO_FOCUS_AF:
        exif["focus_mode"] = {"in": ["single", "continuous"]}
    # MACRO_FOCUS_MIXED or skip → no constraint

    aperture = answers.get(MACRO_APERTURE_KEY)
    if aperture == MACRO_APERTURE_WIDE:
        exif["aperture"] = {"gte": 2.8, "lte": 4.0}
    elif aperture == MACRO_APERTURE_MODERATE:
        exif["aperture"] = {"gte": 4.0, "lte": 8.0}
    elif aperture == MACRO_APERTURE_STOPPED:
        exif["aperture"] = {"gte": 8.0, "lte": 16.0}
    elif aperture == MACRO_APERTURE_VERY_SMALL:
        exif["aperture"] = {"gte": 16.0}
    # MACRO_APERTURE_MIXED or skip → no constraint

    flash = answers.get(MACRO_FLASH_KEY)
    if flash == MACRO_FLASH_YES:
        exif["flash_fired"] = {"eq": True}
    elif flash == MACRO_FLASH_NO:
        exif["flash_fired"] = {"eq": False}
    # skip → no constraint

    bracketing = answers.get(MACRO_BRACKETING_KEY)
    expects_focus_brackets = bracketing in (
        MACRO_BRACKETING_ALWAYS, MACRO_BRACKETING_SOMETIMES,
    )

    return {
        "schema_version": 1,
        "id": "user-macro",
        "name": "Macro",
        "name_localized": {"en": "Macro", "pt": "Macro"},
        "genre": "macro",
        "kind": "final",
        "description": "Close-up photography of small subjects.",
        "description_localized": {
            "en": "Close-up photography of small subjects.",
            "pt": "Fotografia de aproximação de pequenos sujeitos.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "expects_focus_brackets": expects_focus_brackets,
        "reference_card": _build_macro_reference_card(answers),
        "confidence_baseline": 0.85,
        "tags": _build_macro_tags(answers),
    }


def _build_macro_reference_card(answers: dict[str, str]) -> dict:
    """Reference-card content for J8 export. Filled per the user's
    answers — skipped questions become a generic placeholder."""
    aperture_text = {
        MACRO_APERTURE_WIDE: "f/2.8 to f/4 — shallow DOF",
        MACRO_APERTURE_MODERATE: "f/4 to f/8 — moderate DOF",
        MACRO_APERTURE_STOPPED: "f/8 to f/16 — deep DOF",
        MACRO_APERTURE_VERY_SMALL: "f/16+ — accept diffraction for DOF",
        MACRO_APERTURE_MIXED: "varies with subject",
    }.get(answers.get(MACRO_APERTURE_KEY, ""), "varies")

    focus_text = {
        MACRO_FOCUS_MANUAL: "Manual focus (magnification assist if available)",
        MACRO_FOCUS_AF: "Autofocus, single or continuous",
        MACRO_FOCUS_MIXED: "Manual or AF depending on subject",
    }.get(answers.get(MACRO_FOCUS_KEY, ""), "Manual focus preferred")

    flash_text = {
        MACRO_FLASH_YES: "Macro/ring flash or diffused speedlight",
        MACRO_FLASH_NO: "Available light only",
    }.get(answers.get(MACRO_FLASH_KEY, ""), "As needed for subject")

    bracketing_text = {
        MACRO_BRACKETING_ALWAYS: "Always — focus bracketing for stacking",
        MACRO_BRACKETING_SOMETIMES: "Sometimes — when DOF requires it",
        MACRO_BRACKETING_NEVER: "Single-shot only",
    }.get(answers.get(MACRO_BRACKETING_KEY, ""), "Depends on subject")

    return {
        "physical_setup": {
            "lens": "Dedicated macro lens (detected from EXIF)",
            "tripod": "Recommended for higher magnification",
            "flash": flash_text,
        },
        "software_settings": {
            "mode": "Aperture priority or manual",
            "aperture": aperture_text,
            "focus_mode": focus_text,
            "focus_bracketing": bracketing_text,
        },
        "rationale": (
            "Macro depth of field is razor-thin. Focus discipline, stable "
            "shooting, and aperture choice matter more than fast shutter."
        ),
    }


def _build_macro_tags(answers: dict[str, str]) -> list[str]:
    """Free-form tags. Reflect the user's answers so a future wizard
    re-run can show "what you previously said" inline."""
    tags = ["macro"]
    bracketing = answers.get(MACRO_BRACKETING_KEY)
    if bracketing and bracketing != ANSWER_SKIP:
        tags.append(f"bracketing:{bracketing}")
    return tags


# ── Shared range helpers (used by Wildlife / Sports / future blocks) ─


def _focal_clause(value: str | None) -> dict | None:
    """Map a focal-length-range answer to an EXIF clause."""
    return {
        FOCAL_ULTRA_WIDE: {"lte": 24},
        FOCAL_WIDE:       {"gte": 24, "lte": 35},
        FOCAL_NORMAL:     {"gte": 35, "lte": 70},
        FOCAL_SHORT_TELE: {"gte": 70, "lte": 200},
        FOCAL_LONG_TELE:  {"gte": 200, "lte": 400},
        FOCAL_VERY_LONG:  {"gte": 400},
    }.get(value or "")


def _drive_clause(value: str | None) -> dict | None:
    """Map a drive-mode answer to an EXIF clause."""
    return {
        DRIVE_BURST_HIGH: {"eq": "burst_high"},
        DRIVE_BURST_LOW:  {"eq": "burst_low"},
        DRIVE_SINGLE:     {"eq": "single"},
    }.get(value or "")


def _shutter_clause(value: str | None) -> dict | None:
    """Map a shutter-speed-range answer to an EXIF clause (seconds)."""
    return {
        SHUTTER_VERY_FAST: {"lte": 0.0005},                 # 1/2000+
        SHUTTER_FAST:      {"gte": 0.0005, "lte": 0.002},   # 1/500 – 1/2000
        SHUTTER_MODERATE:  {"gte": 0.002, "lte": 0.004},    # 1/250 – 1/500
    }.get(value or "")


def _focal_text(value: str | None) -> str:
    return {
        FOCAL_ULTRA_WIDE: "Ultra-wide (under 24mm)",
        FOCAL_WIDE:       "Wide (24–35mm)",
        FOCAL_NORMAL:     "35–70mm",
        FOCAL_SHORT_TELE: "70–200mm",
        FOCAL_LONG_TELE:  "200–400mm",
        FOCAL_VERY_LONG:  "400mm and longer",
        FOCAL_MIXED:      "mixed across the zoom range",
    }.get(value or "", "as the scene demands")


def _drive_text(value: str | None) -> str:
    return {
        DRIVE_BURST_HIGH: "Burst high (12fps+)",
        DRIVE_BURST_LOW:  "Burst low",
        DRIVE_SINGLE:     "Single shot",
        DRIVE_MIXED:      "Mixed — single between bursts",
    }.get(value or "", "as the action demands")


def _shutter_text(value: str | None) -> str:
    return {
        SHUTTER_VERY_FAST: "1/2000s or faster",
        SHUTTER_FAST:      "1/500s – 1/2000s",
        SHUTTER_MODERATE:  "1/250s – 1/500s (good for panning)",
        SHUTTER_MIXED:     "varies with subject motion",
    }.get(value or "", "as needed")


# ── Wildlife scenario builder ───────────────────────────────────────


def _build_wildlife_scenario(answers: dict[str, str]) -> dict:
    """Build the user-wildlife scenario from the four wildlife answers.

    Each non-skip answer becomes an ``exif_expectations`` clause.
    AF / subject-lock answers fan out into different EXIF fields:
    the subject-detection option produces a ``subject_detection``
    clause that mirrors the built-in T1 rule; tracking produces an
    ``af_area_mode`` clause; single-point produces a focus_mode
    plus af_area_mode pair; manual produces a focus_mode clause.
    """
    exif: dict[str, dict] = {}

    focal_clause = _focal_clause(answers.get(WILDLIFE_FOCAL_KEY))
    if focal_clause is not None:
        exif["focal_35mm"] = focal_clause

    af = answers.get(WILDLIFE_AF_KEY)
    if af == WILDLIFE_AF_SUBJECT_DETECT:
        exif["subject_detection"] = {"in": ["animal", "bird"]}
    elif af == WILDLIFE_AF_TRACKING:
        exif["af_area_mode"] = {"eq": "subject_tracking"}
    elif af == WILDLIFE_AF_SINGLE_POINT:
        exif["focus_mode"] = {"eq": "continuous"}
        exif["af_area_mode"] = {"eq": "single_point"}
    elif af == WILDLIFE_AF_MANUAL:
        exif["focus_mode"] = {"eq": "manual"}

    drive_clause = _drive_clause(answers.get(WILDLIFE_DRIVE_KEY))
    if drive_clause is not None:
        exif["drive_mode"] = drive_clause

    shutter_clause = _shutter_clause(answers.get(WILDLIFE_SHUTTER_KEY))
    if shutter_clause is not None:
        exif["shutter_speed"] = shutter_clause

    return {
        "schema_version": 1,
        "id": "user-wildlife",
        "name": "Wildlife",
        "name_localized": {"en": "Wildlife", "pt": "Vida Selvagem"},
        "genre": "wildlife",
        "kind": "final",
        "description": "Birds and mammals, typically with a long lens.",
        "description_localized": {
            "en": "Birds and mammals, typically with a long lens.",
            "pt": "Pássaros e mamíferos, tipicamente com lente teleobjetiva.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "reference_card": _build_wildlife_reference_card(answers),
        "confidence_baseline": 0.85,
        "tags": ["wildlife"],
    }


def _build_wildlife_reference_card(answers: dict[str, str]) -> dict:
    af_text = {
        WILDLIFE_AF_SUBJECT_DETECT: "Animal / bird subject detection (AF-C)",
        WILDLIFE_AF_TRACKING:       "Subject tracking (AF-C)",
        WILDLIFE_AF_SINGLE_POINT:   "Single-point AF-C",
        WILDLIFE_AF_MANUAL:         "Manual focus (BIF / eye-pinch override)",
        WILDLIFE_AF_MIXED:          "Mixed — subject-aware where the body supports it",
    }.get(answers.get(WILDLIFE_AF_KEY, ""), "AF-C, subject-aware where possible")

    return {
        "physical_setup": {
            "lens": _focal_text(answers.get(WILDLIFE_FOCAL_KEY)),
            "support": "Monopod or handheld with stabilization",
        },
        "software_settings": {
            "mode": "Aperture priority or manual with Auto ISO",
            "focus": af_text,
            "drive_mode": _drive_text(answers.get(WILDLIFE_DRIVE_KEY)),
            "shutter": _shutter_text(answers.get(WILDLIFE_SHUTTER_KEY)),
        },
        "rationale": (
            "Wildlife rewards fast reactions and long reach. Subject "
            "detection (where available) and AF-C keep the eye sharp; "
            "burst captures the decisive moment."
        ),
    }


# ── Sports scenario builder ─────────────────────────────────────────


def _build_sports_scenario(answers: dict[str, str]) -> dict:
    """Build the user-sports scenario from the four sports answers.
    Same shape as Wildlife with the human/vehicle-detection AF
    options replacing animal/bird."""
    exif: dict[str, dict] = {}

    focal_clause = _focal_clause(answers.get(SPORTS_FOCAL_KEY))
    if focal_clause is not None:
        exif["focal_35mm"] = focal_clause

    af = answers.get(SPORTS_AF_KEY)
    if af == SPORTS_AF_HUMAN_DETECT:
        exif["subject_detection"] = {"eq": "human"}
        exif["faces_detected"] = {"gt": 0}
    elif af == SPORTS_AF_VEHICLE_DETECT:
        exif["subject_detection"] = {"eq": "vehicle"}
    elif af == SPORTS_AF_TRACKING:
        exif["af_area_mode"] = {"eq": "subject_tracking"}
    elif af == SPORTS_AF_SINGLE_POINT:
        exif["focus_mode"] = {"eq": "continuous"}
        exif["af_area_mode"] = {"eq": "single_point"}

    drive_clause = _drive_clause(answers.get(SPORTS_DRIVE_KEY))
    if drive_clause is not None:
        exif["drive_mode"] = drive_clause

    shutter_clause = _shutter_clause(answers.get(SPORTS_SHUTTER_KEY))
    if shutter_clause is not None:
        exif["shutter_speed"] = shutter_clause

    return {
        "schema_version": 1,
        "id": "user-sports",
        "name": "Sports",
        "name_localized": {"en": "Sports", "pt": "Esportes"},
        "genre": "sports",
        "kind": "final",
        "description": "Action photography — court, field, or track.",
        "description_localized": {
            "en": "Action photography — court, field, or track.",
            "pt": "Fotografia de ação — quadra, campo ou pista.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "reference_card": _build_sports_reference_card(answers),
        "confidence_baseline": 0.85,
        "tags": ["sports"],
    }


def _build_sports_reference_card(answers: dict[str, str]) -> dict:
    af_text = {
        SPORTS_AF_HUMAN_DETECT:   "Human subject detection (AF-C)",
        SPORTS_AF_VEHICLE_DETECT: "Vehicle subject detection (AF-C)",
        SPORTS_AF_TRACKING:       "Subject tracking (AF-C)",
        SPORTS_AF_SINGLE_POINT:   "Single-point AF-C",
        SPORTS_AF_MIXED:          "Mixed — subject-aware where the body supports it",
    }.get(answers.get(SPORTS_AF_KEY, ""), "AF-C, subject-aware where possible")

    return {
        "physical_setup": {
            "lens": _focal_text(answers.get(SPORTS_FOCAL_KEY)),
            "support": "Monopod or handheld with stabilization",
        },
        "software_settings": {
            "mode": "Shutter priority or manual with Auto ISO",
            "focus": af_text,
            "drive_mode": _drive_text(answers.get(SPORTS_DRIVE_KEY)),
            "shutter": _shutter_text(answers.get(SPORTS_SHUTTER_KEY)),
        },
        "rationale": (
            "Sports rewards freezing the peak moment. Fast shutter is "
            "non-negotiable; subject detection (where available) plus "
            "high-frame-rate burst catches the expression."
        ),
    }


# ── Landscape scenario builder ──────────────────────────────────────


def _landscape_aperture_clause(value: str | None) -> dict | None:
    return {
        LANDSCAPE_APERTURE_WIDER:    {"gte": 2.8, "lte": 5.6},
        LANDSCAPE_APERTURE_STANDARD: {"gte": 5.6, "lte": 11.0},
        LANDSCAPE_APERTURE_STOPPED:  {"gte": 11.0, "lte": 16.0},
    }.get(value or "")


def _build_landscape_scenario(answers: dict[str, str]) -> dict:
    """Build the user-landscape scenario from the four landscape
    answers. Long-exposure habit influences both the shutter range
    expectation and an ``expects_exposure_brackets`` scenario flag."""
    exif: dict[str, dict] = {}

    focal_clause = _focal_clause(answers.get(LANDSCAPE_FOCAL_KEY))
    if focal_clause is not None:
        exif["focal_35mm"] = focal_clause

    aperture_clause = _landscape_aperture_clause(
        answers.get(LANDSCAPE_APERTURE_KEY),
    )
    if aperture_clause is not None:
        exif["aperture"] = aperture_clause

    long_exposure = answers.get(LANDSCAPE_LONG_EXPOSURE_KEY)
    # Frequent long-exposure work biases the shutter range expectation
    # to a slow-tripod regime. Occasional/Never leaves shutter open.
    if long_exposure == LANDSCAPE_LONG_EXPOSURE_FREQUENT:
        exif["shutter_speed"] = {"gte": 1.0}  # ≥ 1 second

    af = answers.get(LANDSCAPE_AF_KEY)
    if af == LANDSCAPE_AF_SINGLE_POINT:
        exif["focus_mode"] = {"eq": "single"}
        exif["af_area_mode"] = {"eq": "single_point"}
    elif af == LANDSCAPE_AF_MANUAL_HYPERFOCAL:
        exif["focus_mode"] = {"eq": "manual"}
    # LANDSCAPE_AF_MIXED or skip → no constraint

    return {
        "schema_version": 1,
        "id": "user-landscape",
        "name": "Landscape",
        "name_localized": {"en": "Landscape", "pt": "Paisagem"},
        "genre": "landscape",
        "kind": "final",
        "description": "Scenic compositions, often on a tripod.",
        "description_localized": {
            "en": "Scenic compositions, often on a tripod.",
            "pt": "Composições paisagísticas, frequentemente em tripé.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "reference_card": _build_landscape_reference_card(answers),
        "confidence_baseline": 0.85,
        "tags": _build_landscape_tags(answers),
    }


def _build_landscape_reference_card(answers: dict[str, str]) -> dict:
    aperture_text = {
        LANDSCAPE_APERTURE_WIDER:    "f/2.8 – f/5.6 for separation",
        LANDSCAPE_APERTURE_STANDARD: "f/5.6 – f/11 standard DOF",
        LANDSCAPE_APERTURE_STOPPED:  "f/11 – f/16 for maximum DOF",
        LANDSCAPE_APERTURE_MIXED:    "varies with composition",
    }.get(answers.get(LANDSCAPE_APERTURE_KEY, ""), "f/8 – f/11 default")

    long_exposure_text = {
        LANDSCAPE_LONG_EXPOSURE_FREQUENT:   "Frequent — tripod + ND filter common",
        LANDSCAPE_LONG_EXPOSURE_OCCASIONAL: "Occasional — when scene calls for it",
        LANDSCAPE_LONG_EXPOSURE_NEVER:      "Never — handheld or tripod-fast only",
    }.get(answers.get(LANDSCAPE_LONG_EXPOSURE_KEY, ""), "As the scene demands")

    af_text = {
        LANDSCAPE_AF_SINGLE_POINT:      "AF-S, single-point on a foreground anchor",
        LANDSCAPE_AF_MANUAL_HYPERFOCAL: "Manual focus, hyperfocal distance",
        LANDSCAPE_AF_MIXED:             "Mixed — AF-S or manual depending on subject",
    }.get(answers.get(LANDSCAPE_AF_KEY, ""), "AF-S, single-point")

    return {
        "physical_setup": {
            "lens": _focal_text(answers.get(LANDSCAPE_FOCAL_KEY)),
            "tripod": "Recommended for low-light and long exposures",
            "filter": "ND / graduated ND as conditions demand",
        },
        "software_settings": {
            "mode": "Aperture priority or manual",
            "aperture": aperture_text,
            "focus": af_text,
            "long_exposure": long_exposure_text,
            "iso": "Base ISO; raise only when shutter floor would blur",
        },
        "rationale": (
            "Landscapes reward patience and depth. Aperture for DOF, "
            "tripod for stability, and shutter timing for the light "
            "matter more than fast reactions."
        ),
    }


def _build_landscape_tags(answers: dict[str, str]) -> list[str]:
    tags = ["landscape"]
    long_exposure = answers.get(LANDSCAPE_LONG_EXPOSURE_KEY)
    if long_exposure == LANDSCAPE_LONG_EXPOSURE_FREQUENT:
        tags.append("long_exposure")
    return tags


# ── Astro scenario builder ──────────────────────────────────────────


def _astro_aperture_clause(value: str | None) -> dict | None:
    return {
        ASTRO_APERTURE_WIDE_OPEN: {"gte": 1.4, "lte": 2.8},
        ASTRO_APERTURE_MODERATE:  {"gte": 4.0, "lte": 5.6},
        ASTRO_APERTURE_STOPPED:   {"gte": 8.0, "lte": 11.0},
    }.get(value or "")


def _astro_shutter_clause(value: str | None) -> dict | None:
    return {
        ASTRO_SHUTTER_VERY_LONG: {"gte": 10.0, "lte": 30.0},
        ASTRO_SHUTTER_LONG:      {"gte": 1.0, "lte": 10.0},
        ASTRO_SHUTTER_MODERATE:  {"gte": 0.033, "lte": 1.0},   # 1/30 – 1s
        ASTRO_SHUTTER_FAST:      {"lte": 0.004},               # 1/250+
    }.get(value or "")


def _build_astro_scenario(answers: dict[str, str]) -> dict:
    """Build the user-astro scenario from the four astro answers.

    Astro sub-types vary widely — Milky Way wants wide-open aperture
    plus very long shutter, moon wants stopped-down plus fast. The
    sub-type answer feeds tags (not exif_expectations); the user's
    chosen aperture/shutter/focal answers drive the actual clauses.

    Note: the built-in classifier's ``t1_long_exposure`` rule emits
    ``scenario: night_long_exposure`` for shutters ≥ 1s. The user's
    scenario here uses ``genre: astro`` (user mental model). Both
    classifications can coexist — first-match-wins picks one per photo.
    """
    exif: dict[str, dict] = {}

    focal_clause = _focal_clause(answers.get(ASTRO_FOCAL_KEY))
    if focal_clause is not None:
        exif["focal_35mm"] = focal_clause

    aperture_clause = _astro_aperture_clause(
        answers.get(ASTRO_APERTURE_KEY),
    )
    if aperture_clause is not None:
        exif["aperture"] = aperture_clause

    shutter_clause = _astro_shutter_clause(answers.get(ASTRO_SHUTTER_KEY))
    if shutter_clause is not None:
        exif["shutter_speed"] = shutter_clause

    return {
        "schema_version": 1,
        "id": "user-astro",
        "name": "Astro",
        "name_localized": {"en": "Astro / Night", "pt": "Astro / Noturna"},
        "genre": "astro",
        "kind": "final",
        "description": "Night sky and long-exposure scenes.",
        "description_localized": {
            "en": "Night sky and long-exposure scenes.",
            "pt": "Céu noturno e cenas de longa exposição.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "reference_card": _build_astro_reference_card(answers),
        "confidence_baseline": 0.85,
        "tags": _build_astro_tags(answers),
    }


def _build_astro_reference_card(answers: dict[str, str]) -> dict:
    subtype_text = {
        ASTRO_SUBTYPE_MILKY_WAY:   "Milky Way — wide-open aperture, very long shutter",
        ASTRO_SUBTYPE_MOON:        "Moon — stopped aperture, fast shutter",
        ASTRO_SUBTYPE_URBAN_NIGHT: "Urban night — moderate aperture, long shutter on tripod",
        ASTRO_SUBTYPE_STAR_TRAILS: "Star trails — stacked long exposures",
        ASTRO_SUBTYPE_MIXED:       "Mixed — varies by scene",
    }.get(answers.get(ASTRO_SUBTYPE_KEY, ""), "Long-exposure night work")

    aperture_text = {
        ASTRO_APERTURE_WIDE_OPEN: "f/1.4 – f/2.8 (light gathering)",
        ASTRO_APERTURE_MODERATE:  "f/4 – f/5.6 (urban night)",
        ASTRO_APERTURE_STOPPED:   "f/8 – f/11 (moon detail)",
        ASTRO_APERTURE_MIXED:     "varies with subject",
    }.get(answers.get(ASTRO_APERTURE_KEY, ""), "as the subject demands")

    shutter_text = {
        ASTRO_SHUTTER_VERY_LONG: "10 – 30s (Milky Way)",
        ASTRO_SHUTTER_LONG:      "1 – 10s (cityscape, light trails)",
        ASTRO_SHUTTER_MODERATE:  "1/30 – 1s (handheld dusk)",
        ASTRO_SHUTTER_FAST:      "1/250s+ (moon)",
        ASTRO_SHUTTER_MIXED:     "varies with subject",
    }.get(answers.get(ASTRO_SHUTTER_KEY, ""), "long, on tripod")

    return {
        "physical_setup": {
            "lens": _focal_text(answers.get(ASTRO_FOCAL_KEY)),
            "tripod": "Required for any shutter slower than 1/30s",
            "subject": subtype_text,
        },
        "software_settings": {
            "mode": "Manual",
            "aperture": aperture_text,
            "shutter": shutter_text,
            "focus": "Manual focus (live-view magnification on a bright star)",
            "iso": "Higher ISO for Milky Way (3200+); low for moon (100–400)",
        },
        "rationale": (
            "Astro / night rewards stable framing and deliberate exposure. "
            "Aperture-shutter-ISO triangle is more conscious than reactive — "
            "and the camera does it on a tripod, not in your hands."
        ),
    }


def _build_astro_tags(answers: dict[str, str]) -> list[str]:
    tags = ["astro"]
    subtype = answers.get(ASTRO_SUBTYPE_KEY)
    if subtype and subtype != ANSWER_SKIP and subtype != ASTRO_SUBTYPE_MIXED:
        tags.append(subtype)
    return tags


# ── Portrait scenario builder ───────────────────────────────────────


def _portrait_aperture_clause(value: str | None) -> dict | None:
    return {
        PORTRAIT_APERTURE_VERY_WIDE: {"gte": 1.4, "lte": 2.8},
        PORTRAIT_APERTURE_MODERATE:  {"gte": 2.8, "lte": 5.6},
        PORTRAIT_APERTURE_STOPPED:   {"gte": 5.6, "lte": 11.0},
    }.get(value or "")


def _build_portrait_scenario(answers: dict[str, str]) -> dict:
    """Build the user-portrait scenario.

    AF / face detection answer maps to af_area_mode = face_eye (which
    pairs with the built-in t1_af_face_eye rule) plus a corroborating
    ``faces_detected > 0`` clause — same false-positive guard the
    prototype uses to avoid stale AF mode flags.

    Lighting answer maps to flash_fired: strobe / speedlight → true,
    natural → false.
    """
    exif: dict[str, dict] = {}

    focal_clause = _focal_clause(answers.get(PORTRAIT_FOCAL_KEY))
    if focal_clause is not None:
        exif["focal_35mm"] = focal_clause

    af = answers.get(PORTRAIT_AF_KEY)
    if af == PORTRAIT_AF_FACE_EYE:
        exif["af_area_mode"] = {"eq": "face_eye"}
        exif["faces_detected"] = {"gt": 0}
    elif af == PORTRAIT_AF_SINGLE_POINT:
        exif["focus_mode"] = {"eq": "single"}
        exif["af_area_mode"] = {"eq": "single_point"}
    elif af == PORTRAIT_AF_MANUAL:
        exif["focus_mode"] = {"eq": "manual"}
    # PORTRAIT_AF_MIXED or skip → no constraint

    aperture_clause = _portrait_aperture_clause(
        answers.get(PORTRAIT_APERTURE_KEY),
    )
    if aperture_clause is not None:
        exif["aperture"] = aperture_clause

    lighting = answers.get(PORTRAIT_LIGHTING_KEY)
    if lighting == PORTRAIT_LIGHTING_NATURAL:
        exif["flash_fired"] = {"eq": False}
    elif lighting in (PORTRAIT_LIGHTING_SPEEDLIGHT, PORTRAIT_LIGHTING_STROBE):
        exif["flash_fired"] = {"eq": True}
    # mixed or skip → no constraint

    return {
        "schema_version": 1,
        "id": "user-portrait",
        "name": "Portrait",
        "name_localized": {"en": "Portrait", "pt": "Retrato"},
        "genre": "portrait",
        "kind": "final",
        "description": "People photography — environmental to headshot.",
        "description_localized": {
            "en": "People photography — environmental to headshot.",
            "pt": "Fotografia de pessoas — ambiental a close.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "reference_card": _build_portrait_reference_card(answers),
        "confidence_baseline": 0.85,
        "tags": ["portrait"],
    }


def _build_portrait_reference_card(answers: dict[str, str]) -> dict:
    af_text = {
        PORTRAIT_AF_FACE_EYE:     "Eye / face detection AF",
        PORTRAIT_AF_SINGLE_POINT: "Single-point AF-S on the eye",
        PORTRAIT_AF_MANUAL:       "Manual focus (deliberate technique)",
        PORTRAIT_AF_MIXED:        "Mixed — depends on the subject",
    }.get(answers.get(PORTRAIT_AF_KEY, ""), "Eye / face detection AF")

    aperture_text = {
        PORTRAIT_APERTURE_VERY_WIDE: "f/1.4 – f/2.8 for separation",
        PORTRAIT_APERTURE_MODERATE:  "f/2.8 – f/5.6",
        PORTRAIT_APERTURE_STOPPED:   "f/5.6 – f/11 for group shots",
        PORTRAIT_APERTURE_MIXED:     "varies with composition",
    }.get(answers.get(PORTRAIT_APERTURE_KEY, ""), "f/2.8 – f/4")

    lighting_text = {
        PORTRAIT_LIGHTING_NATURAL:    "Natural light",
        PORTRAIT_LIGHTING_SPEEDLIGHT: "Speedlight (on- or off-camera)",
        PORTRAIT_LIGHTING_STROBE:     "Studio strobe",
        PORTRAIT_LIGHTING_MIXED:      "Mixed — natural with fill flash as needed",
    }.get(answers.get(PORTRAIT_LIGHTING_KEY, ""), "Natural light first")

    return {
        "physical_setup": {
            "lens": _focal_text(answers.get(PORTRAIT_FOCAL_KEY)),
            "lighting": lighting_text,
        },
        "software_settings": {
            "mode": "Aperture priority or manual",
            "aperture": aperture_text,
            "focus": af_text,
            "iso": "Low base ISO; raise for available-light indoor",
            "drive_mode": "Single shot, burst-low for expressions",
        },
        "rationale": (
            "Portraits live or die on the eye being sharp. Subject-aware "
            "AF (eye/face) is the single biggest classifier signal; "
            "aperture controls separation; lighting shapes the face."
        ),
    }


# ── Family scenario builder ─────────────────────────────────────────


def _family_aperture_clause(value: str | None) -> dict | None:
    return {
        FAMILY_APERTURE_WIDE:     {"gte": 1.8, "lte": 2.8},
        FAMILY_APERTURE_MODERATE: {"gte": 2.8, "lte": 5.6},
        FAMILY_APERTURE_SMALLER:  {"gte": 5.6, "lte": 8.0},
    }.get(value or "")


def _build_family_scenario(answers: dict[str, str]) -> dict:
    """Build the user-family scenario. Similar shape to Portrait but
    with continuous-AF as a distinct AF option (kids moving), and an
    explicit on-/off-camera flash distinction in the lighting question."""
    exif: dict[str, dict] = {}

    focal_clause = _focal_clause(answers.get(FAMILY_FOCAL_KEY))
    if focal_clause is not None:
        exif["focal_35mm"] = focal_clause

    af = answers.get(FAMILY_AF_KEY)
    if af == FAMILY_AF_FACE_EYE:
        exif["af_area_mode"] = {"eq": "face_eye"}
        exif["faces_detected"] = {"gt": 0}
    elif af == FAMILY_AF_SINGLE_POINT:
        exif["focus_mode"] = {"eq": "single"}
        exif["af_area_mode"] = {"eq": "single_point"}
    elif af == FAMILY_AF_CONTINUOUS:
        exif["focus_mode"] = {"eq": "continuous"}
    # mixed or skip → no constraint

    aperture_clause = _family_aperture_clause(
        answers.get(FAMILY_APERTURE_KEY),
    )
    if aperture_clause is not None:
        exif["aperture"] = aperture_clause

    flash = answers.get(FAMILY_FLASH_KEY)
    if flash == FAMILY_FLASH_AVAILABLE:
        exif["flash_fired"] = {"eq": False}
    elif flash in (FAMILY_FLASH_ON_CAMERA, FAMILY_FLASH_OFF_CAMERA):
        exif["flash_fired"] = {"eq": True}
    # mixed or skip → no constraint

    return {
        "schema_version": 1,
        "id": "user-family",
        "name": "Events",
        "name_localized": {"en": "Events", "pt": "Eventos"},
        "genre": "family",
        "kind": "final",
        "description": "Gatherings, kids, events.",
        "description_localized": {
            "en": "Gatherings, kids, events.",
            "pt": "Encontros, crianças, eventos.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "reference_card": _build_family_reference_card(answers),
        "confidence_baseline": 0.85,
        "tags": ["family"],
    }


def _build_family_reference_card(answers: dict[str, str]) -> dict:
    af_text = {
        FAMILY_AF_FACE_EYE:     "Face detection AF",
        FAMILY_AF_SINGLE_POINT: "Single-point AF-S",
        FAMILY_AF_CONTINUOUS:   "Continuous AF (for kids in motion)",
        FAMILY_AF_MIXED:        "Mixed — depends on subject",
    }.get(answers.get(FAMILY_AF_KEY, ""), "Face detection AF")

    aperture_text = {
        FAMILY_APERTURE_WIDE:     "f/1.8 – f/2.8 for low light",
        FAMILY_APERTURE_MODERATE: "f/2.8 – f/5.6",
        FAMILY_APERTURE_SMALLER:  "f/5.6 – f/8 for group shots",
        FAMILY_APERTURE_MIXED:    "varies with scene",
    }.get(answers.get(FAMILY_APERTURE_KEY, ""), "f/2.8 – f/4")

    flash_text = {
        FAMILY_FLASH_AVAILABLE:  "Available light only",
        FAMILY_FLASH_ON_CAMERA:  "On-camera flash (bounced when possible)",
        FAMILY_FLASH_OFF_CAMERA: "Off-camera flash with trigger",
        FAMILY_FLASH_MIXED:      "Mixed — flash when ambient is too low",
    }.get(answers.get(FAMILY_FLASH_KEY, ""), "Available light first")

    return {
        "physical_setup": {
            "lens": _focal_text(answers.get(FAMILY_FOCAL_KEY)),
            "flash": flash_text,
        },
        "software_settings": {
            "mode": "Aperture priority with Auto ISO",
            "aperture": aperture_text,
            "focus": af_text,
            "iso": "Auto with high cap (≤ 6400) for indoor",
            "drive_mode": "Single shot, burst-low for kid expressions",
        },
        "rationale": (
            "Family rewards readiness over preparation. Face detection + "
            "Auto ISO + a versatile lens means the camera handles the "
            "exposure triangle while you watch for the moment."
        ),
    }


# ── Street scenario builder ─────────────────────────────────────────


def _street_aperture_clause(value: str | None) -> dict | None:
    return {
        STREET_APERTURE_WIDE:     {"gte": 1.4, "lte": 2.8},
        STREET_APERTURE_MODERATE: {"gte": 2.8, "lte": 5.6},
        STREET_APERTURE_STOPPED:  {"gte": 5.6, "lte": 11.0},
    }.get(value or "")


def _build_street_scenario(answers: dict[str, str]) -> dict:
    """Build the user-street scenario. Color rendering answer maps to
    photo_style — the brand-localized name (Photo Style / Picture
    Style / Film Simulation / etc.) lands on the normalized enum
    after brand profile lookup."""
    exif: dict[str, dict] = {}

    focal_clause = _focal_clause(answers.get(STREET_FOCAL_KEY))
    if focal_clause is not None:
        exif["focal_35mm"] = focal_clause

    af = answers.get(STREET_AF_KEY)
    if af == STREET_AF_SINGLE:
        exif["focus_mode"] = {"eq": "single"}
        exif["af_area_mode"] = {"eq": "single_point"}
    elif af == STREET_AF_ZONE:
        exif["af_area_mode"] = {"eq": "zone"}
    elif af == STREET_AF_MANUAL_HYPERFOCAL:
        exif["focus_mode"] = {"eq": "manual"}

    aperture_clause = _street_aperture_clause(
        answers.get(STREET_APERTURE_KEY),
    )
    if aperture_clause is not None:
        exif["aperture"] = aperture_clause

    color = answers.get(STREET_COLOR_KEY)
    if color == STREET_COLOR_STANDARD:
        exif["photo_style"] = {"eq": "standard"}
    elif color == STREET_COLOR_MONOCHROME:
        exif["photo_style"] = {"eq": "monochrome"}
    elif color == STREET_COLOR_VIVID:
        exif["photo_style"] = {"eq": "vivid"}
    # custom or mixed or skip → no constraint

    return {
        "schema_version": 1,
        "id": "user-street",
        "name": "Street",
        "name_localized": {"en": "Street / Documentary", "pt": "Rua / Documentário"},
        "genre": "street",
        "kind": "final",
        "description": "Walking around in public, candid moments.",
        "description_localized": {
            "en": "Walking around in public, candid moments.",
            "pt": "Andando em público, momentos espontâneos.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "reference_card": _build_street_reference_card(answers),
        "confidence_baseline": 0.85,
        "tags": _build_street_tags(answers),
    }


def _build_street_reference_card(answers: dict[str, str]) -> dict:
    af_text = {
        STREET_AF_SINGLE:            "Single-point AF-S, subject-by-subject",
        STREET_AF_ZONE:              "Zone AF (pre-framed)",
        STREET_AF_MANUAL_HYPERFOCAL: "Manual focus, hyperfocal distance",
        STREET_AF_MIXED:             "Mixed — depends on the scene",
    }.get(answers.get(STREET_AF_KEY, ""), "AF-S, single-point")

    aperture_text = {
        STREET_APERTURE_WIDE:     "f/1.4 – f/2.8 for low light / separation",
        STREET_APERTURE_MODERATE: "f/2.8 – f/5.6 (handheld versatile)",
        STREET_APERTURE_STOPPED:  "f/5.6 – f/11 for deep DOF",
        STREET_APERTURE_MIXED:    "varies with light",
    }.get(answers.get(STREET_APERTURE_KEY, ""), "f/4 – f/8 default")

    color_text = {
        STREET_COLOR_STANDARD:   "Standard color",
        STREET_COLOR_MONOCHROME: "Monochrome (Acros / Eterna / B&W preset)",
        STREET_COLOR_VIVID:      "Vivid",
        STREET_COLOR_CUSTOM:     "Custom color preset",
        STREET_COLOR_MIXED:      "Mixed — varies with scene",
    }.get(answers.get(STREET_COLOR_KEY, ""), "Standard")

    return {
        "physical_setup": {
            "lens": _focal_text(answers.get(STREET_FOCAL_KEY)),
            "carrying": "Discreet — sling strap or compact body recommended",
        },
        "software_settings": {
            "mode": "Aperture priority with Auto ISO",
            "aperture": aperture_text,
            "focus": af_text,
            "color_rendering": color_text,
            "drive_mode": "Single shot, subject-by-subject",
        },
        "rationale": (
            "Street rewards quick reaction and discretion. Single-shot "
            "drive, fast focus on the moment, and a deliberate color "
            "rendering define the genre's visual signature."
        ),
    }


def _build_street_tags(answers: dict[str, str]) -> list[str]:
    tags = ["street"]
    color = answers.get(STREET_COLOR_KEY)
    if color == STREET_COLOR_MONOCHROME:
        tags.append("monochrome")
    return tags


# ── Travel scenario builder ─────────────────────────────────────────


def _travel_aperture_clause(value: str | None) -> dict | None:
    return {
        TRAVEL_APERTURE_WIDE:     {"gte": 2.8, "lte": 4.0},
        TRAVEL_APERTURE_MODERATE: {"gte": 4.0, "lte": 8.0},
        TRAVEL_APERTURE_STOPPED:  {"gte": 8.0, "lte": 11.0},
    }.get(value or "")


def _build_travel_scenario(answers: dict[str, str]) -> dict:
    """Build the user-travel scenario.

    Travel is the fallback genre — the built-in classifier rules already
    route "nothing else matched" to general / travel. The user scenario
    here is mostly reference-card content + broad EXIF expectations to
    flag photos that don't fit other genres but are still the user's
    travel work.
    """
    exif: dict[str, dict] = {}

    focal_clause = _focal_clause(answers.get(TRAVEL_FOCAL_KEY))
    if focal_clause is not None:
        exif["focal_35mm"] = focal_clause

    aperture_clause = _travel_aperture_clause(
        answers.get(TRAVEL_APERTURE_KEY),
    )
    if aperture_clause is not None:
        exif["aperture"] = aperture_clause

    af = answers.get(TRAVEL_AF_KEY)
    if af == TRAVEL_AF_SINGLE:
        exif["focus_mode"] = {"eq": "single"}
    elif af == TRAVEL_AF_CONTINUOUS:
        exif["focus_mode"] = {"eq": "continuous"}
    # mixed or skip → no constraint

    drive_clause = _drive_clause(answers.get(TRAVEL_DRIVE_KEY))
    if drive_clause is not None:
        exif["drive_mode"] = drive_clause

    return {
        "schema_version": 1,
        "id": "user-travel",
        "name": "Travel",
        "name_localized": {"en": "Travel / General", "pt": "Viagem / Geral"},
        "genre": "travel",
        "kind": "final",
        "description": "The everyday catch-all — travel, snapshots, anything not otherwise scoped.",
        "description_localized": {
            "en": "The everyday catch-all — travel, snapshots, anything not otherwise scoped.",
            "pt": "O cotidiano — viagem, fotos do dia, qualquer coisa que não se encaixe em outro estilo.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "reference_card": _build_travel_reference_card(answers),
        "confidence_baseline": 0.80,  # slightly lower — fallback genre
        "tags": ["travel", "general"],
    }


def _build_travel_reference_card(answers: dict[str, str]) -> dict:
    af_text = {
        TRAVEL_AF_SINGLE:     "Single AF (deliberate framing)",
        TRAVEL_AF_CONTINUOUS: "Continuous AF (subject in motion)",
        TRAVEL_AF_MIXED:      "Mixed — depends on the subject",
    }.get(answers.get(TRAVEL_AF_KEY, ""), "AF-S, single-point")

    aperture_text = {
        TRAVEL_APERTURE_WIDE:     "f/2.8 – f/4 for separation",
        TRAVEL_APERTURE_MODERATE: "f/4 – f/8 (versatile default)",
        TRAVEL_APERTURE_STOPPED:  "f/8 – f/11 for landscape-ish",
        TRAVEL_APERTURE_MIXED:    "varies with subject",
    }.get(answers.get(TRAVEL_APERTURE_KEY, ""), "f/4 – f/8")

    return {
        "physical_setup": {
            "lens": _focal_text(answers.get(TRAVEL_FOCAL_KEY)),
            "carrying": "Single versatile zoom or one-lens setup",
        },
        "software_settings": {
            "mode": "Aperture priority with Auto ISO",
            "aperture": aperture_text,
            "focus": af_text,
            "drive_mode": _drive_text(answers.get(TRAVEL_DRIVE_KEY)),
            "iso": "Auto with moderate cap (≤ 3200)",
        },
        "rationale": (
            "Travel rewards readiness over specialization. A versatile "
            "zoom, Auto ISO, single AF, and single drive cover most "
            "everything that doesn't deserve a dedicated scenario."
        ),
    }


# ── Video scenario builder ──────────────────────────────────────────


def _build_video_scenario(answers: dict[str, str]) -> dict:
    """Build the user-video scenario.

    Per docs/04: v1 does not over-attempt video classification beyond
    "video / general." This scenario's primary value is reference-card
    content; ``exif_expectations`` is intentionally light (focal length
    is the one stills-style field video EXIF commonly carries).
    Recording mode, resolution, and frame rate flow into the reference
    card but don't have rules in the current engine. The subject focus
    answer becomes a tag for clip grouping.
    """
    exif: dict[str, dict] = {}

    focal_clause = _focal_clause(answers.get(VIDEO_FOCAL_KEY))
    if focal_clause is not None:
        exif["focal_35mm"] = focal_clause

    return {
        "schema_version": 1,
        "id": "user-video",
        "name": "Video",
        "name_localized": {"en": "Video", "pt": "Vídeo"},
        "genre": "video",
        "kind": "final",
        "description": "Moving image — frame rate, codec, picture profile.",
        "description_localized": {
            "en": "Moving image — frame rate, codec, picture profile.",
            "pt": "Imagem em movimento — taxa de quadros, codec, perfil.",
        },
        "created_by": "wizard",
        "wizard_version": WIZARD_VERSION,
        "created_at": _now_iso(),
        "modified_at": _now_iso(),
        "exif_expectations": exif,
        "reference_card": _build_video_reference_card(answers),
        "confidence_baseline": 0.80,  # fallback-ish; v1 doesn't try hard
        "tags": _build_video_tags(answers),
    }


def _build_video_reference_card(answers: dict[str, str]) -> dict:
    recording_text = {
        VIDEO_RECORDING_STANDARD:    "Standard (in-camera basic)",
        VIDEO_RECORDING_PHOTO_STYLE: "Photo Style passthrough",
        VIDEO_RECORDING_CINELIKE:    "Cinelike (D / V)",
        VIDEO_RECORDING_V_LOG:       "V-Log L / S-Log (graded in post)",
        VIDEO_RECORDING_HLG:         "HLG (Hybrid Log Gamma)",
        VIDEO_RECORDING_MIXED:       "Mixed — varies with project",
    }.get(answers.get(VIDEO_RECORDING_KEY, ""), "Standard")

    resolution_text = {
        VIDEO_RESOLUTION_4K_30: "4K at 30p",
        VIDEO_RESOLUTION_4K_60: "4K at 60p",
        VIDEO_RESOLUTION_4K_24: "4K at 24p (cinematic)",
        VIDEO_RESOLUTION_FHD_60: "FHD at 60p",
        VIDEO_RESOLUTION_FHD_30: "FHD at 30p",
        VIDEO_RESOLUTION_MIXED:  "Mixed — varies with project",
    }.get(answers.get(VIDEO_RESOLUTION_KEY, ""), "4K at 30p")

    subject_text = {
        VIDEO_SUBJECT_WILDLIFE_BEHAVIOR: "Wildlife behavior clips",
        VIDEO_SUBJECT_TRAVEL_BROLL:      "Travel B-roll",
        VIDEO_SUBJECT_FAMILY:            "Family events / kids",
        VIDEO_SUBJECT_MACRO_BEHAVIOR:    "Macro behavior (insects, water drops)",
        VIDEO_SUBJECT_OTHER:             "Other recurring subject",
        VIDEO_SUBJECT_MIXED:             "Mixed — varies",
    }.get(answers.get(VIDEO_SUBJECT_KEY, ""), "Mixed")

    return {
        "physical_setup": {
            "lens": _focal_text(answers.get(VIDEO_FOCAL_KEY)),
            "support": "Gimbal / tripod / handheld with IBIS — clip-dependent",
            "audio": "Use on-camera mic or external — clip-dependent",
        },
        "software_settings": {
            "recording_mode": recording_text,
            "resolution_framerate": resolution_text,
            "subject_focus": subject_text,
            "stabilization": "IBIS on, electronic stabilization when handheld",
        },
        "rationale": (
            "v1 video sits one step beyond stills — clips bucket as "
            "'video' and trim externally. The reference card here "
            "captures your typical recording setup so the camera-side "
            "card content matches what you actually shoot."
        ),
    }


def _build_video_tags(answers: dict[str, str]) -> list[str]:
    tags = ["video"]
    subject = answers.get(VIDEO_SUBJECT_KEY)
    if subject and subject not in (ANSWER_SKIP, VIDEO_SUBJECT_MIXED, VIDEO_SUBJECT_OTHER):
        tags.append(subject)
    return tags
