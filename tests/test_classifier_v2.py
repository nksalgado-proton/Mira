"""Tests for the refinement rules classifier engine."""

import json

import pytest

from core.body_profile import (
    BodyProfile,
    Capabilities,
    IsoBaseline,
    Sensor,
    SubjectDetectionCapability,
)
from core.classifier_v2 import (
    OPERATORS,
    UNKNOWN_LENS_FALLBACK_CONFIDENCE,
    ClassificationResult,
    PhotoContext,
    Rule,
    RuleSet,
    _body_has_capability,
    _evaluate_condition,
    _parse_rule,
    _parse_ruleset,
    _rule_matches,
    classify,
    classify_batch,
    load_camera_rules,
    load_phone_rules,
    load_rules,
)
from core.lens_registry import LensEntry
from core.vocabulary import (
    AfAreaMode,
    DriveMode,
    FocusMode,
    PhotoStyle,
    Scenario,
    ShootingMode,
    SubjectDetection,
)


# ---------------------------------------------------------------------------
# Test fixtures / factories
# ---------------------------------------------------------------------------

def _body_g9_ii() -> BodyProfile:
    return BodyProfile(
        body_id="g9_ii",
        display_name="Panasonic G9 II",
        brand_id="panasonic",
        kind="camera",
        exiftool_model_match=["DC-G9M2"],
        sensor=Sensor(size="four_thirds", crop_factor=2.0, megapixels=25),
        capabilities=Capabilities(
            ibis=True,
            focus_bracket=True,
            exposure_bracket=True,
            phase_detect_af=True,
        ),
        subject_detection=SubjectDetectionCapability(
            supported=True,
            types=[SubjectDetection.HUMAN, SubjectDetection.ANIMAL, SubjectDetection.BIRD],
        ),
        iso_baseline=IsoBaseline(native_min=100, native_max=25600, high_iso_threshold=6400),
    )


def _body_without_subject_detection() -> BodyProfile:
    return BodyProfile(
        body_id="old_body",
        display_name="Old Body",
        brand_id="panasonic",
        kind="camera",
        exiftool_model_match=["OLD"],
        sensor=Sensor(crop_factor=2.0),
        capabilities=Capabilities(focus_bracket=False),
        subject_detection=SubjectDetectionCapability(supported=False),
    )


def _body_iphone_11() -> BodyProfile:
    """iPhone 11 body fixture for phone-source classifier tests
    (Nelson 2026-05-28). kind="phone" is the device-class signal —
    legitimately hardware-dependent per the brand-agnostic
    invariant (phone vs camera is a fundamental semantic difference,
    not a brand leak)."""
    return BodyProfile(
        body_id="apple_iphone_11",
        display_name="Apple iPhone 11",
        brand_id="apple",
        kind="phone",
        exiftool_model_match=["iPhone 11"],
        sensor=Sensor(crop_factor=6.55, megapixels=12),
        capabilities=Capabilities(
            ibis=False,
            focus_bracket=False,
            exposure_bracket=False,
            phase_detect_af=True,
        ),
        subject_detection=SubjectDetectionCapability(
            supported=True,
            types=[SubjectDetection.HUMAN],
        ),
        iso_baseline=IsoBaseline(
            native_min=32, native_max=3072,
            high_iso_threshold=1600,
        ),
    )


def _lens_wildlife() -> LensEntry:
    return LensEntry(
        id="leica_100_400",
        display_name="Leica DG 100-400",
        lens_model_contains=["100-400"],
        potential_scenarios=[Scenario.WILDLIFE],
        confidence=1.0,
        source="bootstrap",
        evidence={"wildlife": 5},
    )


def _lens_macro() -> LensEntry:
    return LensEntry(
        id="olympus_60_macro",
        display_name="Olympus 60mm Macro",
        lens_model_contains=["60mm"],
        potential_scenarios=[Scenario.MACRO, Scenario.PORTRAIT],
        confidence=0.80,
        source="bootstrap",
        evidence={"macro": 4, "portrait": 1},
    )


def _context(**overrides) -> PhotoContext:
    """Build a default PhotoContext with sensible values, overrideable per test."""
    defaults: dict = dict(
        focal_length=200.0,
        focal_35mm=400.0,
        aperture=5.6,
        shutter_speed=0.002,  # 1/500
        iso=400,
        iso_relative_to_body="normal",
        focus_mode=FocusMode.CONTINUOUS,
        af_area_mode=AfAreaMode.SUBJECT_TRACKING,
        subject_detection=SubjectDetection.NONE,
        drive_mode=DriveMode.BURST_HIGH,
        flash_fired=False,
        lens=_lens_wildlife(),
        body=_body_g9_ii(),
        source="camera",
    )
    defaults.update(overrides)
    return PhotoContext(**defaults)


# ---------------------------------------------------------------------------
# PhotoContext.get_field
# ---------------------------------------------------------------------------

def test_get_field_direct():
    ctx = _context(focal_35mm=800.0)
    assert ctx.get_field("focal_35mm") == 800.0


def test_get_field_enum_value():
    ctx = _context(focus_mode=FocusMode.MANUAL)
    assert ctx.get_field("focus_mode") == FocusMode.MANUAL


def test_get_field_nested_lens():
    ctx = _context(lens=_lens_macro())
    assert ctx.get_field("lens.potential_scenarios") == [Scenario.MACRO, Scenario.PORTRAIT]
    assert ctx.get_field("lens.confidence") == 0.80


def test_get_field_nested_body():
    ctx = _context()
    assert ctx.get_field("body.crop_factor") == 2.0
    assert ctx.get_field("body.brand_id") == "panasonic"


def test_get_field_missing_top_level():
    ctx = _context()
    assert ctx.get_field("nonexistent_field") is None


def test_get_field_missing_nested_when_parent_none():
    ctx = _context(lens=None)
    assert ctx.get_field("lens.potential_scenarios") is None


def test_get_field_missing_nested_leaf():
    ctx = _context()
    assert ctx.get_field("body.nonexistent_attr") is None


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

def test_op_eq():
    assert OPERATORS["eq"](5, 5) is True
    assert OPERATORS["eq"](5, 6) is False
    assert OPERATORS["eq"]("macro", "macro") is True


def test_op_eq_strenum_with_string():
    # StrEnum comparison with plain string should work
    assert OPERATORS["eq"](Scenario.MACRO, "macro") is True
    assert OPERATORS["eq"](FocusMode.CONTINUOUS, "continuous") is True


def test_op_ne():
    assert OPERATORS["ne"](5, 6) is True
    assert OPERATORS["ne"](5, 5) is False


def test_op_gt_gte_lt_lte():
    assert OPERATORS["gt"](10, 5) is True
    assert OPERATORS["gt"](5, 5) is False
    assert OPERATORS["gte"](5, 5) is True
    assert OPERATORS["lt"](5, 10) is True
    assert OPERATORS["lt"](5, 5) is False
    assert OPERATORS["lte"](5, 5) is True


def test_op_gt_with_none_returns_false():
    # None should not raise when compared numerically
    assert OPERATORS["gt"](None, 5) is False
    assert OPERATORS["gte"](None, 5) is False
    assert OPERATORS["lt"](None, 5) is False
    assert OPERATORS["lte"](None, 5) is False


def test_op_in_scalar_in_list():
    assert OPERATORS["in"]("bird", ["animal", "bird"]) is True
    assert OPERATORS["in"]("vehicle", ["animal", "bird"]) is False


def test_op_in_operand_in_list_value():
    # Polymorphic: when the value is a list, check whether operand is in it
    assert OPERATORS["in"]([Scenario.MACRO, Scenario.PORTRAIT], Scenario.MACRO) is True
    assert OPERATORS["in"]([Scenario.MACRO], Scenario.WILDLIFE) is False


def test_op_not_in():
    assert OPERATORS["not_in"]("bird", ["animal", "vehicle"]) is True
    assert OPERATORS["not_in"]("bird", ["bird", "animal"]) is False


def test_op_exists():
    assert OPERATORS["exists"]("some value", True) is True
    assert OPERATORS["exists"]("", True) is False
    assert OPERATORS["exists"](0, True) is False
    assert OPERATORS["exists"](None, True) is False
    assert OPERATORS["exists"](None, False) is True


# ---------------------------------------------------------------------------
# _evaluate_condition
# ---------------------------------------------------------------------------

def test_evaluate_condition_scalar_shortcut():
    assert _evaluate_condition(200, 200) is True
    assert _evaluate_condition(200, 300) is False
    assert _evaluate_condition("macro", "macro") is True


def test_evaluate_condition_range_shortcut():
    assert _evaluate_condition(250, [200, 300]) is True
    assert _evaluate_condition(200, [200, 300]) is True  # inclusive
    assert _evaluate_condition(300, [200, 300]) is True
    assert _evaluate_condition(150, [200, 300]) is False
    assert _evaluate_condition(400, [200, 300]) is False


def test_evaluate_condition_range_requires_numeric():
    with pytest.raises(ValueError, match="Range shortcut only supports numeric"):
        _evaluate_condition("macro", ["a", "b"])


def test_evaluate_condition_range_requires_two_elements():
    with pytest.raises(ValueError, match="exactly 2 elements"):
        _evaluate_condition(5, [1, 2, 3])


def test_evaluate_condition_operator_dict():
    assert _evaluate_condition(250, {"gte": 200, "lte": 300}) is True
    assert _evaluate_condition(100, {"gte": 200}) is False


def test_evaluate_condition_multiple_operators_all_must_match():
    # AND semantics: both gte and lte must hold
    assert _evaluate_condition(250, {"gte": 200, "lte": 300}) is True
    assert _evaluate_condition(150, {"gte": 200, "lte": 300}) is False  # gte fails
    assert _evaluate_condition(350, {"gte": 200, "lte": 300}) is False  # lte fails


def test_evaluate_condition_unknown_operator_raises():
    with pytest.raises(ValueError, match="Unknown operator"):
        _evaluate_condition(5, {"approximately": 5})


def test_evaluate_condition_empty_dict_raises():
    with pytest.raises(ValueError, match="Empty condition"):
        _evaluate_condition(5, {})


# ---------------------------------------------------------------------------
# _body_has_capability
# ---------------------------------------------------------------------------

def test_capability_check_direct_field():
    body = _body_g9_ii()
    assert _body_has_capability(body, "ibis") is True
    assert _body_has_capability(body, "focus_bracket") is True
    assert _body_has_capability(body, "pixel_shift") is False


def test_capability_check_subject_detection_special():
    body = _body_g9_ii()
    assert _body_has_capability(body, "subject_detection") is True

    body2 = _body_without_subject_detection()
    assert _body_has_capability(body2, "subject_detection") is False


def test_capability_check_none_body():
    assert _body_has_capability(None, "ibis") is False


def test_capability_check_unknown_capability_name():
    body = _body_g9_ii()
    assert _body_has_capability(body, "teleport") is False


# ---------------------------------------------------------------------------
# _rule_matches
# ---------------------------------------------------------------------------

def _wildlife_rule() -> Rule:
    return Rule(
        id="wildlife_long_lens_with_subject",
        description="Long lens + bird/animal",
        when={
            "focal_35mm": {"gte": 300},
            "subject_detection": {"in": ["animal", "bird"]},
        },
        then_scenario=Scenario.WILDLIFE,
        then_confidence=0.95,
        then_reason="Wildlife signal",
        requires_capability=["subject_detection"],
    )


def test_rule_matches_happy_path():
    ctx = _context(
        focal_35mm=800,
        subject_detection=SubjectDetection.BIRD,
    )
    assert _rule_matches(_wildlife_rule(), ctx) is True


def test_rule_matches_fails_on_condition():
    ctx = _context(
        focal_35mm=100,  # too short
        subject_detection=SubjectDetection.BIRD,
    )
    assert _rule_matches(_wildlife_rule(), ctx) is False


def test_rule_matches_fails_on_capability():
    ctx = _context(
        focal_35mm=800,
        subject_detection=SubjectDetection.BIRD,
        body=_body_without_subject_detection(),
    )
    assert _rule_matches(_wildlife_rule(), ctx) is False


def test_rule_matches_no_capabilities_required():
    rule = Rule(
        id="any_long_lens",
        description="",
        when={"focal_35mm": {"gte": 300}},
        then_scenario=Scenario.WILDLIFE,
        then_confidence=0.7,
        then_reason="",
    )
    ctx = _context(focal_35mm=800, body=_body_without_subject_detection())
    assert _rule_matches(rule, ctx) is True


# ---------------------------------------------------------------------------
# classify — end-to-end scenarios
# ---------------------------------------------------------------------------

def _ruleset_small() -> RuleSet:
    """Small rule set covering a few scenarios for integration tests."""
    return RuleSet(
        version=1,
        rules=[
            Rule(
                id="long_exposure",
                description="",
                when={"shutter_speed": {"gte": 1.0}},
                then_scenario=Scenario.NIGHT_LONG_EXPOSURE,
                then_confidence=0.95,
                then_reason="Long shutter",
            ),
            Rule(
                id="wildlife_long_lens",
                description="",
                requires_capability=["subject_detection"],
                when={
                    "focal_35mm": {"gte": 300},
                    "subject_detection": {"in": ["animal", "bird"]},
                },
                then_scenario=Scenario.WILDLIFE,
                then_confidence=0.95,
                then_reason="Long lens + subject",
            ),
            Rule(
                id="macro_manual",
                description="",
                when={
                    "lens.potential_scenarios": {"contains": "macro"},
                    "focus_mode": {"eq": "manual"},
                },
                then_scenario=Scenario.MACRO,
                then_confidence=0.85,
                then_reason="Macro + MF",
            ),
        ],
    )


def test_classify_first_match_wins():
    ctx = _context(
        shutter_speed=2.0,
        focal_35mm=800,
        subject_detection=SubjectDetection.BIRD,
    )
    result = classify(ctx, _ruleset_small())
    # Long exposure rule comes first and matches — wins even though wildlife also would
    assert result.scenario == Scenario.NIGHT_LONG_EXPOSURE
    assert result.rule_id == "long_exposure"


def test_classify_wildlife_with_subject():
    ctx = _context(
        focal_35mm=800,
        subject_detection=SubjectDetection.BIRD,
    )
    result = classify(ctx, _ruleset_small())
    assert result.scenario == Scenario.WILDLIFE
    assert result.confidence == 0.95
    assert result.rule_id == "wildlife_long_lens"
    assert result.source == "camera"


def test_classify_macro_manual_focus():
    ctx = _context(
        focal_35mm=120,
        focus_mode=FocusMode.MANUAL,
        lens=_lens_macro(),
    )
    result = classify(ctx, _ruleset_small())
    assert result.scenario == Scenario.MACRO
    assert result.rule_id == "macro_manual"


def test_classify_fallback_goes_to_general_when_no_rule_matches():
    """When no rule fires, classification falls to general regardless
    of what the lens registry says — the 'lens.potential_scenarios[0]'
    escape hatch was removed 2026-05-13 (Nelson's pure-EXIF rule:
    classification must work for any camera/lens without per-user
    hardware curation). User reclassifies via the Type override in the
    culler UI."""
    ctx = _context(
        focal_35mm=200,  # not long enough for wildlife rule
        subject_detection=SubjectDetection.NONE,
        lens=_lens_wildlife(),
    )
    result = classify(ctx, _ruleset_small())
    assert result.scenario == Scenario.GENERAL
    assert result.rule_id is None
    assert "no rule matched" in result.reason.lower()


def test_classify_fallback_to_general_for_unknown_lens():
    ctx = _context(lens=None, focal_35mm=100)
    result = classify(ctx, _ruleset_small())
    assert result.scenario == Scenario.GENERAL
    assert result.rule_id is None
    assert result.confidence == UNKNOWN_LENS_FALLBACK_CONFIDENCE
    assert result.tag == "needs_review"
    assert result.needs_review is True


def test_classify_skips_rule_with_missing_capability():
    """A rule with requires_capability that the body doesn't have is
    skipped silently. With the lens-fallback removal (2026-05-13), a
    skipped rule that would have classified the photo means the photo
    falls to general — user reclassifies via Type override."""
    ctx = _context(
        focal_35mm=800,
        subject_detection=SubjectDetection.BIRD,
        body=_body_without_subject_detection(),
        lens=_lens_wildlife(),
    )
    result = classify(ctx, _ruleset_small())
    assert result.rule_id is None
    assert result.scenario == Scenario.GENERAL


def test_classify_result_needs_review_property():
    low = ClassificationResult(
        scenario=Scenario.GENERAL,
        confidence=0.30,
        reason="",
        rule_id=None,
        source="camera",
    )
    assert low.needs_review is True

    high = ClassificationResult(
        scenario=Scenario.MACRO,
        confidence=0.95,
        reason="",
        rule_id="r1",
        source="camera",
    )
    assert high.needs_review is False


def test_classify_source_propagates():
    ctx = _context(source="phone")
    result = classify(ctx, _ruleset_small())
    assert result.source == "phone"


def test_classify_tag_propagates_from_rule():
    rule = Rule(
        id="tagged",
        description="",
        when={"iso_relative_to_body": {"eq": "high"}},
        then_scenario=Scenario.GENERAL,
        then_confidence=0.5,
        then_reason="",
        then_tag="low_light",
    )
    ruleset = RuleSet(rules=[rule])
    ctx = _context(iso_relative_to_body="high")
    result = classify(ctx, ruleset)
    assert result.tag == "low_light"


def test_classify_malformed_rule_skipped_gracefully(caplog):
    """A rule with an unknown operator should be skipped with a warning,
    not crash the whole classification."""
    ruleset = RuleSet(
        rules=[
            Rule(
                id="broken",
                description="",
                when={"focal_35mm": {"approximately": 300}},
                then_scenario=Scenario.WILDLIFE,
                then_confidence=0.9,
                then_reason="",
            ),
            Rule(
                id="good",
                description="",
                when={"focal_35mm": {"gte": 300}},
                then_scenario=Scenario.WILDLIFE,
                then_confidence=0.7,
                then_reason="Fallback rule",
            ),
        ],
    )
    ctx = _context(focal_35mm=800)
    result = classify(ctx, ruleset)
    # Broken rule was skipped; good rule matched
    assert result.rule_id == "good"
    assert any("broken" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# classify_batch
# ---------------------------------------------------------------------------

def test_classify_batch_empty():
    results = classify_batch([], _ruleset_small())
    assert results == []


def test_classify_batch_multiple():
    contexts = [
        _context(focal_35mm=800, subject_detection=SubjectDetection.BIRD),
        _context(shutter_speed=3.0),
        _context(focal_35mm=100, lens=_lens_wildlife()),
    ]
    results = classify_batch(contexts, _ruleset_small())
    assert len(results) == 3
    assert results[0].scenario == Scenario.WILDLIFE
    assert results[1].scenario == Scenario.NIGHT_LONG_EXPOSURE
    # No rule matches the third — falls to general (no lens fallback).
    assert results[2].scenario == Scenario.GENERAL


# ---------------------------------------------------------------------------
# _parse_rule / _parse_ruleset
# ---------------------------------------------------------------------------

def test_parse_rule_minimal():
    data = {
        "id": "r1",
        "when": {"focal_35mm": 300},
        "then": {"scenario": "wildlife"},
    }
    rule = _parse_rule(data)
    assert rule.id == "r1"
    assert rule.then_scenario == Scenario.WILDLIFE
    assert rule.then_confidence == 0.5  # default
    assert rule.requires_capability == []


def test_parse_rule_full():
    data = {
        "id": "r1",
        "description": "desc",
        "requires_capability": ["subject_detection"],
        "when": {"focal_35mm": {"gte": 300}},
        "then": {
            "scenario": "wildlife",
            "confidence": 0.95,
            "reason": "the reason",
            "tag": "wildlife_hit",
        },
    }
    rule = _parse_rule(data)
    assert rule.description == "desc"
    assert rule.requires_capability == ["subject_detection"]
    assert rule.then_confidence == 0.95
    assert rule.then_reason == "the reason"
    assert rule.then_tag == "wildlife_hit"


def test_parse_rule_missing_id_raises():
    with pytest.raises(ValueError, match="missing 'id'"):
        _parse_rule({"when": {}, "then": {"scenario": "macro"}})


def test_parse_rule_missing_when_raises():
    with pytest.raises(ValueError, match="'when'"):
        _parse_rule({"id": "r1", "then": {"scenario": "macro"}})


def test_parse_rule_missing_then_raises():
    with pytest.raises(ValueError, match="'then'"):
        _parse_rule({"id": "r1", "when": {}})


def test_parse_rule_invalid_scenario_raises():
    data = {
        "id": "r1",
        "when": {},
        "then": {"scenario": "nonexistent_scenario"},
    }
    with pytest.raises(ValueError, match="invalid scenario"):
        _parse_rule(data)


def test_parse_rule_confidence_out_of_range_raises():
    data = {
        "id": "r1",
        "when": {},
        "then": {"scenario": "macro", "confidence": 1.5},
    }
    with pytest.raises(ValueError, match="confidence"):
        _parse_rule(data)


def test_parse_ruleset_empty():
    rs = _parse_ruleset({"version": 1, "rules": []})
    assert rs.version == 1
    assert rs.rules == []


def test_parse_ruleset_with_rules():
    data = {
        "version": 2,
        "description": "test set",
        "rules": [
            {"id": "r1", "when": {}, "then": {"scenario": "macro"}},
            {"id": "r2", "when": {}, "then": {"scenario": "wildlife"}},
        ],
    }
    rs = _parse_ruleset(data)
    assert rs.version == 2
    assert rs.description == "test set"
    assert len(rs.rules) == 2
    assert rs.rules[0].id == "r1"


# ---------------------------------------------------------------------------
# Built-in rule files — smoke tests
# ---------------------------------------------------------------------------

def test_load_camera_rules_smoke():
    rules = load_camera_rules()
    assert rules.version >= 1
    assert len(rules.rules) > 0
    # Verify at least one expected rule from each tier is present.
    rule_ids = {r.id for r in rules.rules}
    assert "t1_long_exposure" in rule_ids
    assert "t1_subject_human" in rule_ids
    assert "t2_close_focus_macro" in rule_ids
    assert "t3_wide_closed_landscape" in rule_ids


def test_load_phone_rules_smoke():
    rules = load_phone_rules()
    assert rules.version >= 1
    assert len(rules.rules) > 0
    rule_ids = {r.id for r in rules.rules}
    assert "phone_selfie" in rule_ids
    assert "phone_face_detected" in rule_ids


def test_load_rules_seeds_user_data_dir_on_first_call(tmp_path, monkeypatch):
    """First call to load_rules with an empty user_data_dir must copy
    the bundled defaults into user_data_dir so the user can edit them
    without needing a built-in shipping detail.
    """
    from core.classifier_v2 import ensure_user_rules_exist

    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    user_file = tmp_path / "refinement_rules.json"
    assert not user_file.exists()

    rules = load_camera_rules()  # should auto-seed
    assert user_file.exists()
    assert len(rules.rules) > 0

    # Idempotent (when user version >= bundled): a second call leaves
    # user edits alone. The user pins their override by setting its
    # version field to at least the bundled version — see the docstring
    # of ensure_user_rules_exist for the migration contract.
    pinned = '{"version": 9999, "rules": []}'
    user_file.write_text(pinned, encoding="utf-8")
    seeded_path = ensure_user_rules_exist("refinement_rules.json")
    assert seeded_path == user_file
    assert user_file.read_text(encoding="utf-8") == pinned


def test_load_rules_migrates_when_bundled_is_newer(tmp_path, monkeypatch):
    """When the bundled file's version is newer than the user file's
    (e.g. a new app release ships additional rules), the user file is
    backed up to <name>.bak and re-seeded so the new rules become
    available. Found in the wild 2026-05-13: t2_lens_name_macro was
    added to the bundled defaults but every existing user's classifier
    silently kept running the older 17-rule snapshot."""
    from core.classifier_v2 import ensure_user_rules_exist

    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    user_file = tmp_path / "refinement_rules.json"
    user_file.write_text(
        '{"version": 0, "rules": [{"id": "stale_only",'
        ' "when": {"focal_35mm": {"gte": 100}},'
        ' "then": {"scenario": "wildlife", "confidence": 0.5, "reason": "x"}}]}',
        encoding="utf-8",
    )

    rules = load_camera_rules()
    # Bundled set has many rules; the stale single-rule file got replaced.
    assert len(rules.rules) > 1
    assert not any(r.id == "stale_only" for r in rules.rules)
    # Backup of the old user file lives next to it.
    backup = tmp_path / "refinement_rules.json.bak"
    assert backup.exists()
    assert "stale_only" in backup.read_text(encoding="utf-8")


def test_built_in_camera_rules_classify_wildlife_correctly():
    """End-to-end: the built-in rules should correctly classify a clear
    wildlife shot with the G9II body."""
    rules = load_camera_rules()
    ctx = PhotoContext(
        focal_length=400.0,
        focal_35mm=800.0,
        aperture=6.3,
        shutter_speed=0.0005,
        iso=800,
        iso_relative_to_body="normal",
        focus_mode=FocusMode.CONTINUOUS,
        subject_detection=SubjectDetection.BIRD,
        drive_mode=DriveMode.BURST_HIGH,
        lens=_lens_wildlife(),
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    assert result.scenario == Scenario.WILDLIFE
    assert result.rule_id == "t1_subject_wildlife"
    assert result.confidence == 0.95


def test_built_in_camera_rules_classify_long_exposure():
    rules = load_camera_rules()
    ctx = PhotoContext(
        focal_length=24.0,
        focal_35mm=48.0,
        aperture=8.0,
        shutter_speed=30.0,  # 30 seconds
        iso=100,
        iso_relative_to_body="low",
        focus_mode=FocusMode.MANUAL,
        lens=LensEntry(
            id="any",
            display_name="Any",
            lens_model_contains=["any"],
            potential_scenarios=[Scenario.LANDSCAPE],
            confidence=0.9,
            source="manual",
        ),
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    assert result.scenario == Scenario.NIGHT_LONG_EXPOSURE
    assert result.rule_id == "t1_long_exposure"


def test_built_in_camera_rules_subject_human_requires_faces_detected():
    """Regression for the 2026-04-29 finding: on Panasonic, the
    AFSubjectDetection EXIF tag reflects the configured detection MODE,
    not an actual face detection — so subject_detection=HUMAN alone is
    insufficient. The t1_subject_human rule must additionally require
    faces_detected > 0 (the camera's count of faces actually registered).

    P1304837.RW2 was a paisagem em f/11 com modo Human ligado que
    misclassificava como portrait sem essa salvaguarda.
    """
    rules = load_camera_rules()
    landscape_lens = LensEntry(
        id="lumix_12_35",
        display_name="Lumix G X 12-35",
        lens_model_contains=["12-35"],
        potential_scenarios=[Scenario.LANDSCAPE, Scenario.GENERAL],
        confidence=0.9,
        source="manual",
    )

    # Mode set to Human, but FacesDetected=0 → must NOT be portrait
    ctx_no_faces = PhotoContext(
        focal_length=32.0,
        focal_35mm=64.0,
        aperture=11.0,
        shutter_speed=0.005,  # 1/200
        iso=100,
        focus_mode=FocusMode.CONTINUOUS,
        subject_detection=SubjectDetection.HUMAN,
        faces_detected=0,
        lens=landscape_lens,
        body=_body_g9_ii(),
    )
    result = classify(ctx_no_faces, rules)
    assert result.rule_id != "t1_subject_human"
    assert result.scenario != Scenario.PORTRAIT

    # Same context but FacesDetected=1 → t1_subject_human fires
    ctx_with_faces = PhotoContext(
        focal_length=32.0,
        focal_35mm=64.0,
        aperture=11.0,
        shutter_speed=0.005,
        iso=100,
        focus_mode=FocusMode.CONTINUOUS,
        subject_detection=SubjectDetection.HUMAN,
        faces_detected=1,
        lens=landscape_lens,
        body=_body_g9_ii(),
    )
    result = classify(ctx_with_faces, rules)
    assert result.rule_id == "t1_subject_human"
    assert result.scenario == Scenario.PORTRAIT


def test_built_in_camera_rules_af_face_eye_requires_faces_detected():
    """Regression for 2026-04-30: PANA3803.RW2 was a manual-focus macro
    shot with the Olympus 60mm Macro, but the user had AFAreaMode left
    on 'Face Detect' from a previous shoot. The t1_af_face_eye rule
    fired on the mode setting alone and classified the shot as
    portrait, losing macro. Same Panasonic mode-vs-result trap as
    AFSubjectDetection — must corroborate with FacesDetected > 0.

    With this guard: face_eye + no actual face → falls through to T2
    macro rules → t2_lens_name_macro fires → MACRO."""
    rules = load_camera_rules()

    # Manual-focus macro with face_eye AF area mode (lingering setting)
    # but ZERO faces detected. Should NOT be classified as portrait;
    # should fall through to t2_lens_name_macro (lens name contains
    # 'Macro' + manual focus). v4 rules pass: no registry dependency.
    ctx_macro_with_face_area = PhotoContext(
        focal_length=60.0,
        focal_35mm=120.0,
        aperture=2.8,
        shutter_speed=0.005,
        iso=400,
        focus_mode=FocusMode.MANUAL,
        af_area_mode=AfAreaMode.FACE_EYE,
        faces_detected=0,  # camera saw nothing
        lens_model_raw="Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
        lens=_lens_macro(),
        body=_body_g9_ii(),
    )
    result = classify(ctx_macro_with_face_area, rules)
    assert result.rule_id != "t1_af_face_eye"
    assert result.rule_id == "t2_lens_name_macro"
    assert result.scenario == Scenario.MACRO

    # Same context but FacesDetected=1 → t1_af_face_eye fires.
    # (Real portrait shot using face_eye AF mode that locked.)
    ctx_real_face = PhotoContext(
        focal_length=60.0,
        focal_35mm=120.0,
        aperture=2.8,
        shutter_speed=0.005,
        iso=400,
        focus_mode=FocusMode.SINGLE,
        af_area_mode=AfAreaMode.FACE_EYE,
        faces_detected=1,
        lens=_lens_macro(),
        body=_body_g9_ii(),
    )
    result = classify(ctx_real_face, rules)
    assert result.rule_id == "t1_af_face_eye"
    assert result.scenario == Scenario.PORTRAIT


def test_built_in_camera_rules_photo_style_portrait():
    """PhotoStyle=Portrait is exclusive to Custom mode C2 in Nelson's
    setup, so it should classify as portrait even on a non-portrait
    lens with no face detection. Regression for P1304526.RW2 (Drake
    Bay 2026-04-13): 12-35 at f/4, manual focus, AFSubjectDetection
    n/a, FacesDetected=0, but PhotoStyle=Portrait — was misclassified
    as general before this rule existed.
    """
    rules = load_camera_rules()
    landscape_lens = LensEntry(
        id="lumix_12_35",
        display_name="Lumix G X 12-35",
        lens_model_contains=["12-35"],
        potential_scenarios=[Scenario.LANDSCAPE, Scenario.GENERAL],
        confidence=0.9,
        source="manual",
    )
    ctx = PhotoContext(
        focal_length=15.0,
        focal_35mm=30.0,
        aperture=4.0,
        shutter_speed=0.0015625,  # 1/640
        iso=100,
        focus_mode=FocusMode.MANUAL,
        subject_detection=SubjectDetection.NONE,
        faces_detected=0,
        photo_style=PhotoStyle.PORTRAIT,
        lens=landscape_lens,
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    assert result.rule_id == "t1_photo_style_portrait"
    assert result.scenario == Scenario.PORTRAIT
    assert result.confidence == 0.92


def test_built_in_camera_rules_photo_style_scenery_overrides_aperture_fallback():
    """PhotoStyle=Scenery on a wide-aperture shot should classify as
    landscape, not general. This is the case where the lens has both
    landscape and general in potential_scenarios — without PhotoStyle
    the aperture-based fallback (T3 wide-aperture-general) would route
    to general. With PhotoStyle=Scenery, T1 wins and the user's
    explicit C3-1 dial is respected.
    """
    rules = load_camera_rules()
    lens = LensEntry(
        id="lumix_12_35",
        display_name="Lumix G X 12-35",
        lens_model_contains=["12-35"],
        potential_scenarios=[Scenario.LANDSCAPE, Scenario.GENERAL],
        confidence=0.9,
        source="manual",
    )
    ctx = PhotoContext(
        focal_length=12.0,
        focal_35mm=24.0,
        aperture=4.0,  # Wide — would otherwise hit the general fallback
        shutter_speed=0.005,
        iso=100,
        focus_mode=FocusMode.CONTINUOUS,
        photo_style=PhotoStyle.SCENERY,
        lens=lens,
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    assert result.rule_id == "t1_photo_style_scenery"
    assert result.scenario == Scenario.LANDSCAPE


# Removed 2026-05-13: test_built_in_camera_rules_photo_style_natural_macro_requires_macro_lens.
# The rule t2_photo_style_natural_macro was deleted as part of the
# pure-EXIF cleanup — it required lens.potential_scenarios from the
# per-user lens registry. Today PhotoStyle=Natural alone is too weak
# a signal to fire macro; user reclassifies via the Type override.


def test_built_in_phone_rules_classify_face_detection():
    rules = load_phone_rules()
    ctx = PhotoContext(
        focal_length=4.0,
        focal_35mm=24.0,
        aperture=1.8,
        shutter_speed=0.01,
        iso=100,
        subject_detection=SubjectDetection.HUMAN,
        source="phone",
    )
    result = classify(ctx, rules)
    assert result.scenario == Scenario.PORTRAIT
    assert result.source == "phone"


# ---------------------------------------------------------------------------
# User override loading
# ---------------------------------------------------------------------------

def test_user_override_replaces_built_in(tmp_path, monkeypatch):
    """A user override that pins its version to be at least as high as
    the bundled version is honored as-is (replaces bundled entirely).
    This is how users keep customizations alive across app updates."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))

    # Pin version to a large number so the auto-migration leaves us alone.
    # See ensure_user_rules_exist's docstring for the migration contract.
    custom = {
        "version": 9999,
        "rules": [
            {
                "id": "my_custom_rule",
                "when": {"focal_35mm": {"gte": 100}},
                "then": {
                    "scenario": "wildlife",
                    "confidence": 0.99,
                    "reason": "custom",
                },
            }
        ],
    }
    (tmp_path / "refinement_rules.json").write_text(
        json.dumps(custom), encoding="utf-8"
    )

    rules = load_camera_rules()
    # Override REPLACES built-in entirely
    assert len(rules.rules) == 1
    assert rules.rules[0].id == "my_custom_rule"


def test_load_rules_raises_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        load_rules("nonexistent_rules.json")


# ---------------------------------------------------------------------------
# t2_close_focus_normalized — brand-agnostic close-focus rule
# ---------------------------------------------------------------------------


def test_t2_close_focus_normalized_fires_on_macro_position():
    """A lens focused at its minimum focus distance (focus_position
    ~ 0.0) classifies as macro — regardless of which brand-specific
    EXIF fields encoded the position. This is the rule that catches
    macros from lenses whose name doesn't say 'macro' (OM Systems
    writes 'OM 90mm F3.5' without the keyword)."""
    rules = load_camera_rules()
    ctx = PhotoContext(
        focal_length=90.0,
        focal_35mm=180.0,
        aperture=8.0,
        shutter_speed=0.005,
        iso=400,
        focus_mode=FocusMode.SINGLE,         # AF — name-rule wouldn't fire
        af_area_mode=AfAreaMode.SINGLE_POINT,
        subject_detection=SubjectDetection.NONE,
        faces_detected=0,
        photo_style=PhotoStyle.STANDARD,
        focus_position_normalized=0.0,        # lens at minimum focus
        lens_model_raw="OM 90mm F3.5 + MC-20",  # no 'macro' substring
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    assert result.rule_id == "t2_close_focus_normalized"
    assert result.scenario == Scenario.MACRO


def test_t2_close_focus_normalized_does_not_fire_when_focus_position_unknown():
    """When the brand profile can't compute focus position (LRC-
    stripped JPG, unfamiliar brand), the rule's predicate fails and
    the photo falls through to general."""
    rules = load_camera_rules()
    ctx = PhotoContext(
        focal_length=90.0,
        focal_35mm=180.0,
        aperture=8.0,
        shutter_speed=0.005,
        iso=400,
        focus_mode=FocusMode.SINGLE,
        af_area_mode=AfAreaMode.SINGLE_POINT,
        subject_detection=SubjectDetection.NONE,
        faces_detected=0,
        photo_style=PhotoStyle.STANDARD,
        focus_position_normalized=None,
        lens_model_raw="OM 90mm F3.5 + MC-20",
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    assert result.rule_id != "t2_close_focus_normalized"
    assert result.scenario == Scenario.GENERAL


def test_t2_close_focus_normalized_does_not_fire_at_infinity():
    """A landscape shot focused at infinity (focus_position ~ 1.0) on
    the same macro lens does NOT classify as macro."""
    rules = load_camera_rules()
    ctx = PhotoContext(
        focal_length=60.0,
        focal_35mm=120.0,
        aperture=8.0,
        shutter_speed=0.005,
        iso=400,
        focus_mode=FocusMode.SINGLE,
        af_area_mode=AfAreaMode.SINGLE_POINT,
        subject_detection=SubjectDetection.NONE,
        faces_detected=0,
        photo_style=PhotoStyle.STANDARD,
        focus_position_normalized=1.0,        # infinity
        lens_model_raw="Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    # Note: t2_lens_name_macro requires manual focus, so it doesn't fire
    # here either. Falls to general.
    assert result.rule_id != "t2_close_focus_normalized"
    assert result.scenario != Scenario.MACRO


# ── 00.088 (Nelson 2026-05-28): hands-off / Intelligent Auto rules ────


def test_t1_intelligent_auto_street_kathmandu_case():
    """Kathmandu 2025-10-27_06.54.29.jpg — the canonical bug.

    Lumix G9 II in Intelligent Auto, AF subject detection left on
    "Animal Body" from a previous shoot, 12mm Summilux (26mm equiv)
    super-wide, f/1.6, faces_detected=0. Before 00.088:
    t1_subject_wildlife fired (subject_detection=animal) → photo
    misclassified as WILDLIFE with 0.95 confidence — exact mode-vs-
    result trap that t1_subject_human + t1_af_face_eye were already
    guarded against, but t1_subject_wildlife wasn't. After 00.088:
    t1_intelligent_auto_street preempts wildlife (hands-off context
    + no face = casual street snap)."""
    rules = load_camera_rules()
    ctx = PhotoContext(
        focal_length=12.0,
        focal_35mm=26.0,
        aperture=1.6,
        shutter_speed=1.0 / 160,
        iso=100,
        focus_mode=FocusMode.MANUAL,
        af_area_mode=AfAreaMode.WIDE,
        subject_detection=SubjectDetection.ANIMAL,   # leftover mode
        faces_detected=0,
        shooting_mode=ShootingMode.INTELLIGENT_AUTO,
        photo_style=PhotoStyle.STANDARD,
        lens_model_raw="LEICA DG SUMMILUX 12/F1.4",
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    assert result.rule_id == "t1_intelligent_auto_street"
    assert result.scenario == Scenario.STREET
    # Low confidence flags needs_review so the user can override
    # when the casual default is wrong.
    assert result.confidence < 0.60
    assert result.needs_review is True


def test_t1_intelligent_auto_portrait_face_in_ia_with_animal_mode():
    """Edge case the t1_intelligent_auto_portrait rule covers: user
    is in iA, has subject_detection mode still on Animal Body from a
    previous shoot, BUT a face is actually in the frame. Without
    this rule t1_intelligent_auto_street would skip (faces > 0) and
    t1_subject_wildlife would fire (subject_detection=animal) →
    wrong (it's clearly portrait when there's a face in the iA shot).
    The rule emits PORTRAIT with corroboration on faces_detected."""
    rules = load_camera_rules()
    ctx = PhotoContext(
        focal_length=12.0,
        focal_35mm=26.0,
        aperture=1.6,
        shutter_speed=1.0 / 160,
        iso=100,
        focus_mode=FocusMode.MANUAL,
        af_area_mode=AfAreaMode.WIDE,
        subject_detection=SubjectDetection.ANIMAL,
        faces_detected=2,                            # real faces
        shooting_mode=ShootingMode.INTELLIGENT_AUTO,
        lens_model_raw="LEICA DG SUMMILUX 12/F1.4",
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    assert result.rule_id == "t1_intelligent_auto_portrait"
    assert result.scenario == Scenario.PORTRAIT


def test_t1_intelligent_auto_does_not_fire_in_aperture_priority():
    """The iA rules only fire when shooting_mode is exactly
    INTELLIGENT_AUTO. A wildlife shot in aperture-priority with a
    real animal detected MUST still classify as wildlife — we don't
    want to over-broaden the preempt."""
    rules = load_camera_rules()
    ctx = PhotoContext(
        focal_length=400.0,
        focal_35mm=800.0,
        aperture=6.3,
        shutter_speed=1.0 / 2000,
        iso=800,
        focus_mode=FocusMode.CONTINUOUS,
        subject_detection=SubjectDetection.BIRD,
        faces_detected=0,
        shooting_mode=ShootingMode.APERTURE_PRIORITY,    # deliberate
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    assert result.rule_id == "t1_subject_wildlife"
    assert result.scenario == Scenario.WILDLIFE


def test_t1_intelligent_auto_does_not_fire_when_mode_unknown():
    """Shots from older / less-introspectable cameras where
    shooting_mode stays UNKNOWN (no MakerNotes ShootingMode tag, no
    ExposureProgram) must NOT trip the iA preempt by accident.
    Defensive against the "missing data" failure mode."""
    rules = load_camera_rules()
    ctx = PhotoContext(
        focal_length=50.0,
        focal_35mm=100.0,
        aperture=4.0,
        shutter_speed=1.0 / 500,
        iso=400,
        focus_mode=FocusMode.SINGLE,
        subject_detection=SubjectDetection.ANIMAL,
        faces_detected=0,
        shooting_mode=ShootingMode.UNKNOWN,          # no signal
        body=_body_g9_ii(),
    )
    result = classify(ctx, rules)
    # iA rules don't fire (shooting_mode != iA), so existing
    # t1_subject_wildlife handles it (same behavior as pre-00.088).
    assert result.rule_id == "t1_subject_wildlife"


def test_phone_default_street_iphone_casual_shot():
    """Casual phone shot — no front camera, no macro, no bracket, no
    long exposure, no face. Pre-00.088 this fell through to GENERAL
    with needs_review (81%+ of non-selfie iPhone shots in the
    Nepal 2025 corpus). Now: phone_default_street emits STREET with
    needs_review so the photo lands in a useful bucket while still
    flagging the casual-default uncertainty."""
    rules = load_phone_rules()
    ctx = PhotoContext(
        focal_length=4.25,
        focal_35mm=26.0,
        aperture=1.8,
        shutter_speed=1.0 / 60,
        iso=100,
        focus_mode=FocusMode.UNKNOWN,
        af_area_mode=AfAreaMode.UNKNOWN,
        subject_detection=SubjectDetection.NONE,
        faces_detected=0,
        lens_model_raw="iPhone 11 back camera 4.25mm f/1.8",
        body=_body_iphone_11(),
        source="phone",
    )
    result = classify(ctx, rules)
    assert result.rule_id == "phone_default_street"
    assert result.scenario == Scenario.STREET
    assert result.confidence < 0.60
    assert result.needs_review is True


def test_phone_default_street_does_not_preempt_selfie():
    """Front camera shot must still match phone_selfie first.
    Ordering pin: phone_default_street is appended at the END of the
    phone rule file precisely so deterministic phone rules
    (front-camera, close-focus, bracket, face) win."""
    rules = load_phone_rules()
    ctx = PhotoContext(
        focal_length=2.71,
        focal_35mm=23.0,
        aperture=2.2,
        shutter_speed=1.0 / 60,
        iso=100,
        lens_model_raw="iPhone 11 front camera 2.71mm f/2.2",
        body=_body_iphone_11(),
        source="phone",
    )
    result = classify(ctx, rules)
    assert result.rule_id == "phone_selfie"
    assert result.scenario == Scenario.SELFIE


def test_phone_default_street_does_not_preempt_face_detected():
    """A phone photo with a detected face still classifies as
    portrait via phone_face_detected — the default rule only fires
    when nothing more specific did."""
    rules = load_phone_rules()
    ctx = PhotoContext(
        focal_length=4.25,
        focal_35mm=26.0,
        aperture=1.8,
        shutter_speed=1.0 / 60,
        iso=100,
        subject_detection=SubjectDetection.HUMAN,    # face detected
        faces_detected=1,
        lens_model_raw="iPhone 11 back camera 4.25mm f/1.8",
        body=_body_iphone_11(),
        source="phone",
    )
    result = classify(ctx, rules)
    assert result.rule_id == "phone_face_detected"
    assert result.scenario == Scenario.PORTRAIT
