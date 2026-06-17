"""Refinement rules classifier — the heart of v2.0 classification.

The classifier takes a PhotoContext (already normalized by brand profile +
body profile + lens registry lookup) and runs it through an ordered list of
refinement rules defined declaratively in JSON. First match wins. If no rule
matches, falls back to the first entry in the lens's potential_scenarios, or
to GENERAL with low confidence for unknown lenses.

See v2_design.md §11 for the full design rationale.

Usage:
    from core.classifier_v2 import classify, load_rules, PhotoContext

    rules = load_rules("refinement_rules.json")
    context = PhotoContext(
        focal_length=400.0,
        focal_35mm=800.0,
        aperture=6.3,
        shutter_speed=1/2000,
        iso=800,
        iso_relative_to_body="normal",
        focus_mode=FocusMode.CONTINUOUS,
        subject_detection=SubjectDetection.BIRD,
        ...
        lens=lens_entry,
        body=body_profile,
    )
    result = classify(context, rules)
    # → ClassificationResult(scenario=WILDLIFE, confidence=0.95, rule_id=...)
"""

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional

from core.body_profile import BodyProfile
from core.lens_registry import LensEntry
from core.logging_setup import log_activity
from core.settings import user_data_dir
from core.vocabulary import (
    AfAreaMode,
    DriveMode,
    FocusMode,
    PhotoStyle,
    Scenario,
    ShootingMode,
    SubjectDetection,
)

log = logging.getLogger(__name__)

Source = Literal["camera", "phone"]

# Low-confidence value used when no lens entry is found for an unknown lens.
UNKNOWN_LENS_FALLBACK_CONFIDENCE = 0.30


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PhotoContext:
    """Normalized photo data that the rule engine operates on.

    The caller (import pipeline, bootstrap analysis, etc.) is responsible for
    building this by reading raw EXIF and translating it through the brand
    profile, attaching crop_factor and iso_relative from the body profile,
    and looking up the lens in the registry.
    """
    focal_length: float = 0.0          # mm physical
    focal_35mm: float = 0.0            # mm equivalent (focal_length * body.crop_factor)
    aperture: float = 0.0              # f-number (6.3, 2.8, etc.)
    shutter_speed: float = 0.0         # seconds (0.0005 for 1/2000)
    iso: int = 0
    iso_relative_to_body: Literal["low", "normal", "high"] = "normal"
    focus_mode: FocusMode = FocusMode.UNKNOWN
    af_area_mode: AfAreaMode = AfAreaMode.UNKNOWN
    subject_detection: SubjectDetection = SubjectDetection.NONE
    # Number of faces actually detected by the camera AF (Panasonic
    # MakerNotes: FacesDetected). Distinct from `subject_detection`,
    # which on Panasonic reflects the *mode* the user configured, not a
    # real detection — confirmed empirically 2026-04-29 on P1304837 (RW2
    # had AFSubjectDetection="Human Eye/Face/Body" but FacesDetected=0).
    # Camera rules that key on "human subject" must corroborate via this
    # field; phones bypass the issue because Apple's source tag
    # (RegionType) only exists when a face was registered.
    faces_detected: int = 0
    drive_mode: DriveMode = DriveMode.UNKNOWN
    photo_style: PhotoStyle = PhotoStyle.UNKNOWN
    # The exposure/mode-dial setting the camera was in. Brand-agnostic
    # normalization done in core/brand_profile.translate_shooting_mode.
    # Used by t1_intelligent_auto_{portrait,street} to preempt the
    # mode-vs-result false positives (leftover AF subject detection
    # firing wildlife/macro on a clearly-not-that shot).
    shooting_mode: ShootingMode = ShootingMode.UNKNOWN
    flash_fired: bool = False
    # Distance to focused subject in metres (None if EXIF didn't report it).
    # Used by macro disambiguation rules to distinguish close-focus
    # (typically < 0.5m) from portrait/general at the same focal length.
    focus_distance: Optional[float] = None
    # Brand-aware normalized focus position — [0, 1] where 0 is the
    # lens at its closest focus distance (macro range) and 1 is at
    # infinity. None when the brand profile doesn't write enough info
    # to compute it (e.g. LRC-exported JPGs that lost the maker notes).
    # The conversion from brand-specific EXIF (Panasonic step counts,
    # Sony/Canon meters, EXIF SubjectDistanceRange enum) is owned by
    # BrandProfile.focus_position_normalized — rules see this single
    # normalized concept and stay brand-agnostic.
    focus_position_normalized: Optional[float] = None
    # Bracket-mode flags from camera EXIF tags. Strong intent signal — a
    # photo shot in focus-bracket mode is almost always macro stacking;
    # exposure-bracket mode is almost always HDR landscape work.
    focus_bracket_active: bool = False
    exposure_bracket_active: bool = False
    # Raw EXIF LensModel string, preserved as written by the camera/phone.
    # Used by phone rules to detect the front camera (`"front" in
    # lens_model_raw` ⇒ selfie) without requiring lens registry entries
    # for every front/back permutation.
    lens_model_raw: str = ""

    lens: Optional[LensEntry] = None
    body: Optional[BodyProfile] = None
    source: Source = "camera"

    def get_field(self, path: str) -> Any:
        """Resolve a dotted field path to a value.

        Supports top-level fields (focal_35mm, aperture, focus_mode, ...)
        and nested namespaces (lens.potential_scenarios, body.crop_factor, ...).

        Returns None if any part of the path is missing.
        """
        if "." not in path:
            return getattr(self, path, None)

        head, _, tail = path.partition(".")
        target = getattr(self, head, None)
        if target is None:
            return None

        current: Any = target
        for part in tail.split("."):
            current = getattr(current, part, None)
            if current is None:
                return None
        return current


@dataclass
class ClassificationResult:
    """Output of the classifier for a single photo."""
    scenario: Scenario
    confidence: float
    reason: str
    rule_id: Optional[str]        # None when fallback was used
    source: Source
    tag: Optional[str] = None

    @property
    def needs_review(self) -> bool:
        """True if this classification should be flagged for user review."""
        return self.confidence < 0.60


@dataclass
class Rule:
    """A single refinement rule parsed from JSON."""
    id: str
    description: str
    when: dict[str, Any]
    then_scenario: Scenario
    then_confidence: float
    then_reason: str
    requires_capability: list[str] = field(default_factory=list)
    then_tag: Optional[str] = None


@dataclass
class RuleSet:
    """Ordered collection of rules plus metadata."""
    version: int = 1
    description: str = ""
    rules: list[Rule] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Operator dispatch
# ---------------------------------------------------------------------------

def _op_eq(value: Any, operand: Any) -> bool:
    return value == operand


def _op_ne(value: Any, operand: Any) -> bool:
    return value != operand


def _op_gt(value: Any, operand: Any) -> bool:
    if value is None:
        return False
    return value > operand


def _op_gte(value: Any, operand: Any) -> bool:
    if value is None:
        return False
    return value >= operand


def _op_lt(value: Any, operand: Any) -> bool:
    if value is None:
        return False
    return value < operand


def _op_lte(value: Any, operand: Any) -> bool:
    if value is None:
        return False
    return value <= operand


def _op_in(value: Any, operand: Any) -> bool:
    """Polymorphic: scalar-in-list OR operand-in-value-list.

    If the field value is a list/tuple/set, check whether the operand (a
    scalar) is one of its elements — "the lens's potential_scenarios contains
    'portrait'".

    If the field value is a scalar, check whether it is one of the operand
    list's elements — "subject_detection is one of [animal, bird]".
    """
    if isinstance(value, (list, tuple, set)):
        return operand in value
    if not isinstance(operand, (list, tuple, set)):
        # Degenerate case: single-element operand
        return value == operand
    return value in operand


def _op_not_in(value: Any, operand: Any) -> bool:
    return not _op_in(value, operand)


def _op_contains(value: Any, operand: Any) -> bool:
    """Polymorphic ``contains``:

    - **list / tuple / set field**: True if operand is a member —
      ``lens.potential_scenarios: {contains: macro}`` matches when the
      lens's potentials list includes macro.
    - **string field**: True if operand (coerced to string) is a
      case-insensitive substring — ``lens_model_raw: {contains: front}``
      matches when LensModel contains "front" (used by the phone selfie
      rule on iPhone).
    - Anything else: False.

    Distinct from ``in`` so rules read naturally regardless of field type.
    """
    if isinstance(value, (list, tuple, set)):
        return operand in value
    if isinstance(value, str):
        return str(operand).lower() in value.lower()
    return False


def _op_exists(value: Any, operand: Any) -> bool:
    """Truthiness check. {"exists": true} → value is truthy."""
    return bool(value) == bool(operand)


OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": _op_eq,
    "ne": _op_ne,
    "gt": _op_gt,
    "gte": _op_gte,
    "lt": _op_lt,
    "lte": _op_lte,
    "in": _op_in,
    "not_in": _op_not_in,
    "contains": _op_contains,
    "exists": _op_exists,
}


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def _evaluate_condition(value: Any, condition: Any) -> bool:
    """Check a single field's value against a condition specification.

    Condition can be:
      - A scalar: {"field": 200} → equivalent to {"field": {"eq": 200}}
      - A 2-element numeric list: {"field": [200, 300]} → gte 200 AND lte 300
      - A dict of operators: {"field": {"gte": 200, "lte": 300}}
    """
    # Shortcut: scalar equality
    if not isinstance(condition, (dict, list)):
        return _op_eq(value, condition)

    # Shortcut: 2-element numeric range
    if isinstance(condition, list):
        if len(condition) != 2:
            raise ValueError(
                f"Range shortcut must have exactly 2 elements, got {len(condition)}"
            )
        lo, hi = condition
        if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float))):
            raise ValueError(
                "Range shortcut only supports numeric bounds. "
                f"Use explicit operators for non-numeric lists: got {condition}"
            )
        return _op_gte(value, lo) and _op_lte(value, hi)

    # Full operator dict — all operators must match (AND)
    if not condition:
        raise ValueError("Empty condition dict is not allowed")
    for op_name, operand in condition.items():
        if op_name not in OPERATORS:
            raise ValueError(
                f"Unknown operator '{op_name}'. Valid: {sorted(OPERATORS.keys())}"
            )
        if not OPERATORS[op_name](value, operand):
            return False
    return True


def _body_has_capability(body: Optional[BodyProfile], cap_name: str) -> bool:
    """Check whether a body supports a named capability.

    Capabilities live in two places on the body profile:
      - body.subject_detection.supported (for "subject_detection")
      - body.capabilities.<name> (everything else)

    Missing body profile → all capabilities absent.
    Unknown capability name → returns False (safer than raising).
    """
    if body is None:
        return False
    if cap_name == "subject_detection":
        return body.subject_detection.supported
    return body.capabilities.has(cap_name)


def _rule_matches(rule: Rule, context: PhotoContext) -> bool:
    """Return True if this rule's capabilities and conditions all match."""
    # Capability gate: body must have ALL listed capabilities
    for cap in rule.requires_capability:
        if not _body_has_capability(context.body, cap):
            return False

    # Condition gate: all fields in `when` must match
    for field_path, condition in rule.when.items():
        value = context.get_field(field_path)
        if not _evaluate_condition(value, condition):
            return False
    return True


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(
    context: PhotoContext,
    rules: RuleSet,
    *,
    gear_hint: Optional[
        Callable[[PhotoContext], Optional[tuple]]
    ] = None,
) -> ClassificationResult:
    """Classify a photo by running it through the refinement rules.

    Evaluation order is the order of rules in the RuleSet. First match
    wins. If no rule matches, the **user-gear-hint tier** (spec/85 §5)
    fires — when the caller supplies ``gear_hint``, the callable peeks at
    the user's gear-profile tags via :class:`PhotoContext` and returns
    ``(scenario, confidence)`` for a tagged camera or lens. If no rule
    matched AND the gear hint is silent, falls back to GENERAL with low
    confidence.

    The fallback previously tried ``context.lens.potential_scenarios[0]``
    (the per-user lens registry's primary use scenario), but that's a
    hardware-dependent escape hatch that contradicts the project's "pure
    EXIF" rule. Removed 2026-05-13. Photos that no rule classifies fall
    to general; the user fixes them via the Type override in the culler
    UI (in-event culler today, classification preview page already has
    the combo).

    This function does not raise — any invalid condition in a rule
    causes that rule to be skipped with a warning log. The gear hint is
    likewise sandboxed: a raise inside it logs + falls through to the
    GENERAL fallback. This prevents a single malformed user override
    from crashing classification for every photo.
    """
    for rule in rules.rules:
        try:
            if _rule_matches(rule, context):
                return ClassificationResult(
                    scenario=rule.then_scenario,
                    confidence=rule.then_confidence,
                    reason=rule.then_reason,
                    rule_id=rule.id,
                    source=context.source,
                    tag=rule.then_tag,
                )
        except (ValueError, TypeError) as exc:
            log.warning(
                "Rule '%s' raised %s during evaluation — skipping: %s",
                rule.id, type(exc).__name__, exc,
            )
            continue

    # spec/85 §5 — user-gear-hint tier. Runs AFTER the rule loop so user
    # scenarios (from the first-run wizard) and built-in refinement rules
    # win when they fire. Above the generic unknown-lens fallback
    # (UNKNOWN_LENS_FALLBACK_CONFIDENCE = 0.30), below explicit user
    # scenarios (typically ≥ 0.55).
    if gear_hint is not None:
        try:
            hinted = gear_hint(context)
        except Exception as exc:                                # noqa: BLE001
            log.warning(
                "user-gear-hint raised %s — falling through to GENERAL: %s",
                type(exc).__name__, exc,
            )
            hinted = None
        if hinted is not None:
            try:
                scenario, confidence = hinted
            except (TypeError, ValueError) as exc:
                log.warning(
                    "user-gear-hint returned %r (expected (scenario, "
                    "confidence) tuple): %s", hinted, exc,
                )
            else:
                return ClassificationResult(
                    scenario=scenario,
                    confidence=float(confidence),
                    reason="user gear hint (spec/85 §5)",
                    rule_id="user_gear_hint",
                    source=context.source,
                    tag=None,
                )

    return ClassificationResult(
        scenario=Scenario.GENERAL,
        confidence=UNKNOWN_LENS_FALLBACK_CONFIDENCE,
        reason="No rule matched; marked for review",
        rule_id=None,
        source=context.source,
        tag="needs_review",
    )


def classify_batch(
    contexts: Iterable[PhotoContext],
    rules: RuleSet,
) -> list[ClassificationResult]:
    """Classify many photos at once. Convenient wrapper over classify().

    Wraps the whole batch in a log_activity so you see timing for imports.
    """
    contexts_list = list(contexts)
    with log_activity(log, f"classifying batch of {len(contexts_list)} photos"):
        return [classify(ctx, rules) for ctx in contexts_list]


# ---------------------------------------------------------------------------
# Loading rules from JSON
# ---------------------------------------------------------------------------

def _parse_rule(data: dict[str, Any]) -> Rule:
    """Build a Rule from a parsed JSON dict. Raises ValueError on invalid shape."""
    if "id" not in data:
        raise ValueError(f"Rule missing 'id': {data}")
    if "when" not in data or not isinstance(data["when"], dict):
        raise ValueError(f"Rule '{data['id']}' missing or invalid 'when'")
    if "then" not in data or not isinstance(data["then"], dict):
        raise ValueError(f"Rule '{data['id']}' missing or invalid 'then'")

    then = data["then"]
    if "scenario" not in then:
        raise ValueError(f"Rule '{data['id']}' then.scenario is required")

    try:
        scenario = Scenario(then["scenario"])
    except ValueError as exc:
        raise ValueError(
            f"Rule '{data['id']}' has invalid scenario '{then['scenario']}'"
        ) from exc

    confidence = float(then.get("confidence", 0.5))
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(
            f"Rule '{data['id']}' confidence must be in [0, 1], got {confidence}"
        )

    return Rule(
        id=str(data["id"]),
        description=str(data.get("description", "")),
        when=dict(data["when"]),
        then_scenario=scenario,
        then_confidence=confidence,
        then_reason=str(then.get("reason", "")),
        requires_capability=list(data.get("requires_capability", [])),
        then_tag=then.get("tag"),
    )


def _parse_ruleset(data: dict[str, Any]) -> RuleSet:
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError(f"'rules' must be a list, got {type(raw_rules).__name__}")

    rules: list[Rule] = []
    for i, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise ValueError(f"Rule at index {i} must be a dict, got {type(raw).__name__}")
        rules.append(_parse_rule(raw))

    return RuleSet(
        version=int(data.get("version", 1)),
        description=str(data.get("description", "")),
        rules=rules,
    )


def _builtin_rules_dir() -> Path:
    """Path to bundled refinement rules directory (assets/)."""
    return Path(__file__).resolve().parent.parent / "assets"


def _user_override_rules_path(filename: str) -> Path:
    return user_data_dir() / filename


def _builtin_rules_path(filename: str) -> Path:
    return _builtin_rules_dir() / filename


def ensure_user_rules_exist(filename: str) -> Path:
    """Make sure ``user_data_dir()/{filename}`` exists and is at least as
    new as the bundled default. Returns the user path.

    Three cases:

    1. **No user file yet** — copy the bundled default. First-run seed.
    2. **User file present, version >= bundled** — return as-is. The
       user's copy is the source of truth for customizations.
    3. **User file present, version < bundled** — the app shipped new
       rules; back the user file up to ``<filename>.bak`` and re-seed.
       A warning is logged so the user notices if they had customized
       the file (they can hand-merge from the .bak).

    Without case 3, a new rule added to the bundled file silently never
    reaches an existing user. Found in the wild 2026-05-13 when Nelson's
    classifier was running an old 17-rule snapshot from before
    t2_lens_name_macro existed — every macro fell to general because the
    new rule was in assets/ but his user copy still had the old set.

    Raises:
        FileNotFoundError: if the bundled default doesn't exist —
            indicates a build problem, not a runtime issue.
    """
    user_path = _user_override_rules_path(filename)
    builtin_path = _builtin_rules_path(filename)
    if not builtin_path.exists():
        raise FileNotFoundError(
            f"No bundled default for '{filename}' at {builtin_path}"
        )

    if not user_path.exists():
        user_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(builtin_path, user_path)
        log.info("Seeded user rules file %s from %s", user_path, builtin_path)
        return user_path

    # User file exists — check version.
    try:
        with user_path.open("r", encoding="utf-8") as f:
            user_version = int(json.load(f).get("version", 0))
        with builtin_path.open("r", encoding="utf-8") as f:
            builtin_version = int(json.load(f).get("version", 0))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning(
            "Could not read versions for %s migration check: %s — "
            "leaving user file alone", filename, exc,
        )
        return user_path

    if builtin_version > user_version:
        backup_path = user_path.with_suffix(user_path.suffix + ".bak")
        try:
            shutil.copy2(user_path, backup_path)
            shutil.copy2(builtin_path, user_path)
            log.warning(
                "Refinement rules %s upgraded from v%d to v%d. The old "
                "user file was backed up to %s — if you had customized "
                "it, hand-merge changes from the .bak file.",
                filename, user_version, builtin_version, backup_path,
            )
        except OSError as exc:
            log.error(
                "Rule file upgrade failed for %s (v%d → v%d): %s — "
                "leaving user file alone",
                filename, user_version, builtin_version, exc,
            )
    return user_path


def load_rules(filename: str = "refinement_rules.json") -> RuleSet:
    """Load a rule set by filename from user_data_dir, seeding from
    bundled defaults on first run.

    The user override fully REPLACES the built-in rule set (unlike brand/body
    profiles which merge at the field level). Rule ordering is semantically
    meaningful — partial merges would silently reorder precedence and cause
    hard-to-debug behavior. If the user wants to customize, they edit the
    user-data-dir file directly (or via the Classification Rules UI tab).

    Raises:
        FileNotFoundError: if neither user nor built-in file exists
        ValueError: if the JSON is structurally invalid
    """
    with log_activity(log, f"loading refinement rules '{filename}'"):
        user_path = ensure_user_rules_exist(filename)

        with user_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        ruleset = _parse_ruleset(data)
        log.debug(
            "Parsed rule set from %s: version %d, %d rules",
            user_path, ruleset.version, len(ruleset.rules),
        )
        return ruleset


def load_camera_rules() -> RuleSet:
    """Convenience: load the default camera refinement rules."""
    return load_rules("refinement_rules.json")


def load_phone_rules() -> RuleSet:
    """Convenience: load the default phone refinement rules."""
    return load_rules("refinement_rules_phone.json")
