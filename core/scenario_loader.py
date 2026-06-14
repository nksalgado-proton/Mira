"""Wizard-scenario → classifier-rule adapter.

The wizard (``core/wizard.py``) writes one ``user-<genre>.json``
scenario file per chosen genre under ``~/Mira/scenarios/``. The
file's ``exif_expectations`` clause has the same operator vocabulary
as the classifier's rule ``when`` clauses (docs/07 cross-walk), so
the adapter is one-to-one — no engine extension needed.

This module is the seam that turns wizard output into something the
classifier actually consumes. Before this lands, the wizard's user
profile is written to disk but never read at classification time.

Public API:

  • ``load_user_scenarios()``                 → list of scenario dicts
  • ``scenario_to_rule_dict(scenario)``        → rule dict (pre-parse)
  • ``merge_user_scenarios_into_ruleset(rs)``  → RuleSet with user
        scenarios spliced after the T1 deterministic rules and before
        the T2 lens-aware rules (so user's habits beat lens fallbacks
        but yield to deterministic intent signals like
        ``focus_bracket_active``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.classifier_v2 import (
    Rule,
    RuleSet,
    _parse_rule,
    load_camera_rules,
    load_phone_rules,
)
from core.settings import user_data_dir


log = logging.getLogger(__name__)


SCENARIOS_SUBDIR = "scenarios"
USER_SCENARIO_PREFIX = "user-"
# Rule IDs starting with "t1_" are the deterministic-intent tier in
# the built-in refinement rules. User scenarios slot in immediately
# after these — they're more specific than T2/T3 (lens-aware) rules
# but yield to T1 signals like focus_bracket_active.
T1_RULE_PREFIX = "t1_"
# Tag attached to rules produced from user scenarios so test code
# and debugging can tell the wizard-derived rules apart from the
# built-in ones at a glance.
USER_RULE_TAG = "user_scenario"


def _scenarios_dir() -> Path:
    return user_data_dir() / SCENARIOS_SUBDIR


def user_scenarios_fingerprint() -> str:
    """Short stable fingerprint of the user-scenario files' CONTENT.

    Part of the persisted classification rules-version stamp (spec/58
    §3): a wizard re-run rewrites ``user-*.json``, the fingerprint
    changes, and the background pass re-classifies Edit-untouched items.
    Empty dir → ``"0"``. Unreadable files still hash (raw bytes)."""
    import hashlib

    scenarios_dir = _scenarios_dir()
    if not scenarios_dir.exists():
        return "0"
    h = hashlib.sha256()
    found = False
    for path in sorted(scenarios_dir.glob(f"{USER_SCENARIO_PREFIX}*.json")):
        try:
            h.update(path.name.encode("utf-8"))
            h.update(path.read_bytes())
            found = True
        except OSError:
            continue
    return h.hexdigest()[:8] if found else "0"


def load_user_scenarios() -> list[dict]:
    """Read every ``user-*.json`` file in the scenarios dir.

    Returns the parsed scenario dicts. Files that fail to parse are
    logged and skipped — one corrupt scenario shouldn't disable the
    rest of the user's profile.
    """
    scenarios_dir = _scenarios_dir()
    if not scenarios_dir.exists():
        return []

    out: list[dict] = []
    for path in sorted(scenarios_dir.glob(f"{USER_SCENARIO_PREFIX}*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Skipping unreadable scenario %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            log.warning(
                "Scenario %s is not a JSON object — skipping", path,
            )
            continue
        out.append(data)
    return out


def scenario_to_rule_dict(scenario: dict[str, Any]) -> dict[str, Any] | None:
    """Adapt one scenario JSON to a rule-dict the classifier can parse.

    Returns ``None`` when the scenario is incomplete (missing genre /
    exif_expectations / etc.) — the caller filters None values out.

    Mapping:
      • ``scenario.exif_expectations`` → ``rule.when``
      • ``scenario.genre``            → ``rule.then.scenario``
      • ``scenario.confidence_baseline`` (default 0.85) → ``rule.then.confidence``
      • Rule ID is prefixed with ``user_`` so logs / debug tags can
        identify wizard-derived rules at a glance.
    """
    genre = scenario.get("genre")
    if not isinstance(genre, str) or not genre:
        log.warning("Scenario missing 'genre' — skipping: %s", scenario.get("id"))
        return None

    expectations = scenario.get("exif_expectations")
    if not isinstance(expectations, dict):
        log.warning(
            "Scenario %s has no exif_expectations — skipping",
            scenario.get("id"),
        )
        return None
    if not expectations:
        # Empty expectations would match every photo — the user
        # answered every question with Skip. Skip the rule entirely;
        # the built-in fallbacks handle classification.
        log.debug(
            "Scenario %s has empty exif_expectations — skipping (no "
            "predicate would over-match every photo)",
            scenario.get("id"),
        )
        return None

    scenario_id = scenario.get("id") or genre
    confidence = float(scenario.get("confidence_baseline", 0.85))

    return {
        "id": f"user_{scenario_id}",
        "description": (
            f"User scenario from wizard: {scenario.get('name', genre)}"
        ),
        "when": expectations,
        "then": {
            "scenario": genre,
            "confidence": confidence,
            "reason": f"User scenario: {scenario.get('name', genre)}",
            "tag": USER_RULE_TAG,
        },
    }


def merge_user_scenarios_into_ruleset(ruleset: RuleSet) -> RuleSet:
    """Return a new RuleSet with user scenarios spliced into the
    rule list at the correct priority slot.

    User scenarios sit AFTER T1 deterministic rules (focus_bracket
    intent, subject detection with corroboration, etc.) and BEFORE
    T2 lens-aware rules. Rationale: T1 signals are camera-set intent
    and should always win; the user's habit ranges are more specific
    than lens fallbacks, so they should fire before T2/T3.

    If no user scenarios exist, the original ruleset is returned
    unchanged (no list copy — caller can rely on identity check).
    """
    user_scenarios = load_user_scenarios()
    if not user_scenarios:
        return ruleset

    user_rules: list[Rule] = []
    for scenario in user_scenarios:
        rule_dict = scenario_to_rule_dict(scenario)
        if rule_dict is None:
            continue
        try:
            user_rules.append(_parse_rule(rule_dict))
        except ValueError as exc:
            log.warning(
                "Could not parse user scenario %s as a rule: %s",
                scenario.get("id"), exc,
            )

    if not user_rules:
        return ruleset

    # Find the splice point: after the last T1 rule.
    splice_index = 0
    for i, rule in enumerate(ruleset.rules):
        if rule.id.startswith(T1_RULE_PREFIX):
            splice_index = i + 1

    merged_rules = (
        list(ruleset.rules[:splice_index])
        + user_rules
        + list(ruleset.rules[splice_index:])
    )
    log.info(
        "Merged %d user scenario(s) into ruleset after position %d (%d total rules)",
        len(user_rules), splice_index, len(merged_rules),
    )
    return RuleSet(
        version=ruleset.version,
        description=ruleset.description,
        rules=merged_rules,
    )


# ── Convenience loaders used by the import pipeline ─────────────────


def load_camera_rules_with_user_scenarios() -> RuleSet:
    """Built-in camera refinement rules + user scenarios merged.

    Wraps ``classifier_v2.load_camera_rules()`` and splices in any
    ``user-*.json`` scenarios from disk. Called by the import pipeline
    (and any code that classifies real photos) so the wizard's user
    profile actually influences classification.

    Use ``classifier_v2.load_camera_rules()`` directly when you want
    built-in rules only (e.g. unit tests that assert specific
    built-in behaviour without user-scenario interference).
    """
    return merge_user_scenarios_into_ruleset(load_camera_rules())


def load_phone_rules_with_user_scenarios() -> RuleSet:
    """Built-in phone refinement rules + user scenarios merged.

    User scenarios from the wizard describe shooting habits which
    are camera-vs-phone-neutral — same scenarios get merged into both
    rule sets. The wizard's macro habit-pattern fires on a phone
    macro photo just like it does on a camera macro photo.
    """
    return merge_user_scenarios_into_ruleset(load_phone_rules())
