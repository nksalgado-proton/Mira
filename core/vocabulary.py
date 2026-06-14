"""Normalized internal vocabulary for the classification pipeline.

Brand profiles translate raw EXIF tag values into these enums. The rest of
the classifier (refinement rules, lens registry, culler UI) operates on
these values, never on raw brand-specific strings.

When adding a new vocabulary value:
  1. Add it here first
  2. Update all brand profiles to map to it
  3. Update refinement rules that may consult it
"""

from enum import StrEnum


class FocusMode(StrEnum):
    MANUAL = "manual"
    SINGLE = "single"          # AF-S, one-shot
    CONTINUOUS = "continuous"  # AF-C, servo
    UNKNOWN = "unknown"


class AfAreaMode(StrEnum):
    SINGLE_POINT = "single_point"
    ZONE = "zone"
    WIDE = "wide"                      # full-area
    FACE_EYE = "face_eye"
    SUBJECT_TRACKING = "subject_tracking"
    UNKNOWN = "unknown"


class SubjectDetection(StrEnum):
    NONE = "none"
    HUMAN = "human"
    ANIMAL = "animal"
    BIRD = "bird"
    VEHICLE = "vehicle"
    UNKNOWN = "unknown"


class DriveMode(StrEnum):
    SINGLE = "single"
    BURST_LOW = "burst_low"
    BURST_HIGH = "burst_high"
    SELF_TIMER = "self_timer"
    UNKNOWN = "unknown"


class BracketType(StrEnum):
    NONE = "none"
    FOCUS = "focus"
    EXPOSURE = "exposure"


class PhotoStyle(StrEnum):
    """Normalized photo style / creative intent signal.

    Most cameras expose a user-selected style setting (Panasonic: PhotoStyle,
    Sony: CreativeStyle/CreativeLook, Canon: PictureStyle, Nikon:
    PictureControl, Fuji: FilmMode). When the user sets this consciously
    before shooting, it carries strong intent — Portrait style is a much
    better signal that a photo is a portrait than focal length alone.

    Values are normalized across brands via brand profile mappings.
    """
    STANDARD = "standard"      # neutral default — weak signal
    VIVID = "vivid"            # saturated — often landscape/general
    NATURAL = "natural"        # balanced — often wildlife/landscape
    PORTRAIT = "portrait"      # skin tones, soft — STRONG portrait signal
    SCENERY = "scenery"        # high contrast, deep colors — STRONG landscape signal
    MONOCHROME = "monochrome"  # B&W — artistic intent, scenario-neutral
    CUSTOM = "custom"          # user-defined preset — weak signal
    UNKNOWN = "unknown"


class ShootingMode(StrEnum):
    """Normalized exposure/shooting mode the camera was in.

    The "mode dial" semantic — what kind of control the user took over
    exposure parameters. Brand-agnostic by design: Panasonic writes
    ``"Intelligent Auto"`` / ``"P"`` / ``"A"`` / ``"S"`` / ``"M"`` /
    ``"C1"`` in MakerNotes ``ShootingMode``; Sony writes its own values
    in MakerNotes (and standard EXIF ``ExposureProgram`` as a fallback);
    Canon / Nikon write yet other strings. Each brand profile declares
    its own ``shooting_mode`` :class:`TagMapping` to translate raw values
    into this normalized enum.

    Why classification cares: ``INTELLIGENT_AUTO`` is the strongest
    signal that the user wasn't being deliberate about a specialized
    genre — they handed the camera every decision. A photo shot in iA
    is almost never wildlife / macro / sports / astro (those need
    deliberate setup). It's street (or portrait if a face is detected).
    The classifier uses this to preempt mode-vs-result false positives
    where a leftover AF-subject-detection setting from a previous shoot
    fires the wildlife rule on a clearly-not-wildlife shot.

    Phones don't have a "mode dial" concept — they default to
    :attr:`UNKNOWN` and the classifier treats them as implicit-auto via
    the ``source == "phone"`` path instead.
    """
    INTELLIGENT_AUTO = "intelligent_auto"       # iA — fully hands-off
    PROGRAM = "program"                         # P — exposure auto, user picks ISO/etc
    APERTURE_PRIORITY = "aperture_priority"     # A / Av — deliberate DOF
    SHUTTER_PRIORITY = "shutter_priority"       # S / Tv — deliberate motion
    MANUAL = "manual"                           # M — full deliberation
    CUSTOM = "custom"                           # C1/C2/C3 — saved deliberate setup
    SCENE = "scene"                             # canned scene mode (Sports / Night / etc)
    UNKNOWN = "unknown"


class ScenarioKind(StrEnum):
    INTERMEDIATE = "intermediate"  # focus/exposure bracket — temporary
    FINAL = "final"                # permanent classification


class Scenario(StrEnum):
    # Intermediate (temporary, merged into final scenarios after post-processing)
    FOCUS_BRACKET = "focus_bracket"
    EXPOSURE_BRACKET = "exposure_bracket"

    # Final (survive post-processing, used for organization).
    # The wizard (docs/04) generates user scenarios for these 10
    # genres — they're the keys the classifier emits when matching
    # rules fire, whether from built-in refinement_rules.json or from
    # user-generated user-<genre>.json scenarios.
    MACRO = "macro"
    WILDLIFE = "wildlife"
    PORTRAIT = "portrait"
    SELFIE = "selfie"
    LANDSCAPE = "landscape"
    NIGHT_LONG_EXPOSURE = "night_long_exposure"
    VIDEO = "video"
    GENERAL = "general"
    # Added 2026-05-13 for wizard scenario integration. The built-in
    # refinement rules don't emit these yet — they come from user
    # scenarios the wizard writes per genre. Built-in rule support
    # for sports / street / travel / family / astro is Phase 5+.
    SPORTS = "sports"
    STREET = "street"
    TRAVEL = "travel"
    FAMILY = "family"
    ASTRO = "astro"


SCENARIO_KIND: dict[Scenario, ScenarioKind] = {
    Scenario.FOCUS_BRACKET: ScenarioKind.INTERMEDIATE,
    Scenario.EXPOSURE_BRACKET: ScenarioKind.INTERMEDIATE,
    Scenario.MACRO: ScenarioKind.FINAL,
    Scenario.WILDLIFE: ScenarioKind.FINAL,
    Scenario.PORTRAIT: ScenarioKind.FINAL,
    Scenario.SELFIE: ScenarioKind.FINAL,
    Scenario.LANDSCAPE: ScenarioKind.FINAL,
    Scenario.NIGHT_LONG_EXPOSURE: ScenarioKind.FINAL,
    Scenario.VIDEO: ScenarioKind.FINAL,
    Scenario.GENERAL: ScenarioKind.FINAL,
    Scenario.SPORTS: ScenarioKind.FINAL,
    Scenario.STREET: ScenarioKind.FINAL,
    Scenario.TRAVEL: ScenarioKind.FINAL,
    Scenario.FAMILY: ScenarioKind.FINAL,
    Scenario.ASTRO: ScenarioKind.FINAL,
}


FINAL_SCENARIOS: tuple[Scenario, ...] = tuple(
    s for s, kind in SCENARIO_KIND.items() if kind == ScenarioKind.FINAL
)

INTERMEDIATE_SCENARIOS: tuple[Scenario, ...] = tuple(
    s for s, kind in SCENARIO_KIND.items() if kind == ScenarioKind.INTERMEDIATE
)
