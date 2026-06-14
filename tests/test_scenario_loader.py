"""Tests for core.scenario_loader — wizard scenarios → classifier rules.

This is the seam that makes the wizard's user profile actually
influence classification. Before this lands, wizard scenarios are
written to disk and ignored. After: any ``user-<genre>.json`` file
under ``~/Mira/scenarios/`` becomes a rule the classifier
consults at import time.
"""

from __future__ import annotations

import json

import pytest

from core.classifier_v2 import PhotoContext, classify
from core.scenario_loader import (
    USER_RULE_TAG,
    load_camera_rules_with_user_scenarios,
    load_user_scenarios,
    merge_user_scenarios_into_ruleset,
    scenario_to_rule_dict,
)
from core.vocabulary import (
    AfAreaMode,
    DriveMode,
    FocusMode,
    Scenario,
    SubjectDetection,
)


def _isolate_user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))


def _write_scenario(tmp_path, filename: str, payload: dict) -> None:
    (tmp_path / "scenarios").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scenarios" / filename).write_text(
        json.dumps(payload), encoding="utf-8",
    )


def _macro_scenario() -> dict:
    """A wizard-shaped macro scenario as the wizard would write it."""
    return {
        "schema_version": 1,
        "id": "user-macro",
        "name": "Macro",
        "genre": "macro",
        "kind": "final",
        "created_by": "wizard",
        "wizard_version": "1.1",
        "exif_expectations": {
            "focus_mode": {"eq": "manual"},
            "aperture": {"gte": 8.0, "lte": 16.0},
        },
        "confidence_baseline": 0.88,
    }


# ── load_user_scenarios ──────────────────────────────────────────


def test_load_user_scenarios_empty_dir(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    assert load_user_scenarios() == []


def test_load_user_scenarios_reads_user_files(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    _write_scenario(tmp_path, "user-macro.json", _macro_scenario())
    _write_scenario(tmp_path, "user-wildlife.json", {
        "id": "user-wildlife", "name": "Wildlife", "genre": "wildlife",
        "exif_expectations": {"focal_35mm": {"gte": 200, "lte": 400}},
    })
    scenarios = load_user_scenarios()
    assert len(scenarios) == 2
    genres = {s["genre"] for s in scenarios}
    assert genres == {"macro", "wildlife"}


def test_load_user_scenarios_ignores_non_user_files(tmp_path, monkeypatch):
    """Only ``user-*.json`` files count. Other files in the scenarios
    dir (e.g. backups, future built-in scenarios) are ignored."""
    _isolate_user_dir(tmp_path, monkeypatch)
    _write_scenario(tmp_path, "user-macro.json", _macro_scenario())
    _write_scenario(tmp_path, "builtin-portrait.json", {
        "id": "builtin-portrait", "genre": "portrait",
        "exif_expectations": {"focus_mode": {"eq": "single"}},
    })
    scenarios = load_user_scenarios()
    assert len(scenarios) == 1
    assert scenarios[0]["genre"] == "macro"


def test_load_user_scenarios_skips_corrupt_json(tmp_path, monkeypatch, caplog):
    """A single broken scenario doesn't disable the rest of the profile."""
    _isolate_user_dir(tmp_path, monkeypatch)
    _write_scenario(tmp_path, "user-macro.json", _macro_scenario())
    (tmp_path / "scenarios" / "user-broken.json").write_text(
        "{not valid json", encoding="utf-8",
    )
    with caplog.at_level("WARNING", logger="core.scenario_loader"):
        scenarios = load_user_scenarios()
    assert len(scenarios) == 1
    assert any("unreadable" in r.message for r in caplog.records)


# ── scenario_to_rule_dict ────────────────────────────────────────


def test_scenario_to_rule_dict_macro_shape():
    rule = scenario_to_rule_dict(_macro_scenario())
    assert rule is not None
    assert rule["id"] == "user_user-macro"
    assert rule["when"] == {
        "focus_mode": {"eq": "manual"},
        "aperture": {"gte": 8.0, "lte": 16.0},
    }
    assert rule["then"]["scenario"] == "macro"
    assert rule["then"]["confidence"] == 0.88
    assert rule["then"]["tag"] == USER_RULE_TAG


def test_scenario_to_rule_dict_returns_none_for_missing_genre():
    rule = scenario_to_rule_dict({
        "id": "user-bad",
        "exif_expectations": {"aperture": {"gte": 8.0}},
    })
    assert rule is None


def test_scenario_to_rule_dict_returns_none_for_empty_expectations():
    """All-skip wizard answers produce no clauses — the resulting
    user scenario would match every photo, which is worse than the
    built-in fallback. Skip it."""
    rule = scenario_to_rule_dict({
        "id": "user-macro",
        "genre": "macro",
        "exif_expectations": {},
    })
    assert rule is None


# ── merge_user_scenarios_into_ruleset ────────────────────────────


def test_merge_returns_same_ruleset_when_no_user_scenarios(
    tmp_path, monkeypatch,
):
    """Empty scenarios dir → built-in ruleset returned unchanged."""
    _isolate_user_dir(tmp_path, monkeypatch)
    from core.classifier_v2 import load_camera_rules
    builtin = load_camera_rules()
    merged = merge_user_scenarios_into_ruleset(builtin)
    assert merged is builtin


def test_merge_splices_user_scenarios_after_t1_rules(
    tmp_path, monkeypatch,
):
    """User scenarios sit between T1 (deterministic intent) and T2
    (lens-aware) — more specific than lens fallbacks, less specific
    than focus-bracket-active intent."""
    _isolate_user_dir(tmp_path, monkeypatch)
    _write_scenario(tmp_path, "user-macro.json", _macro_scenario())

    merged = load_camera_rules_with_user_scenarios()
    rule_ids = [r.id for r in merged.rules]
    # T1 rules first.
    last_t1_idx = max(
        i for i, rid in enumerate(rule_ids) if rid.startswith("t1_")
    )
    user_idx = rule_ids.index("user_user-macro")
    first_non_t1_after_user = next(
        (i for i, rid in enumerate(rule_ids)
         if i > user_idx and not rid.startswith("user_")),
        None,
    )
    assert last_t1_idx < user_idx
    assert first_non_t1_after_user is not None
    # The rule right after the user-rule block is a non-T1 built-in
    # (the splice sits between T1 and T2).
    assert not rule_ids[first_non_t1_after_user].startswith("t1_")


def test_user_macro_scenario_fires_on_matching_photo(
    tmp_path, monkeypatch,
):
    """End-to-end: with a user-macro scenario on disk, a photo whose
    EXIF matches the user's macro habits classifies as macro via the
    user-derived rule (not via a built-in lens fallback)."""
    _isolate_user_dir(tmp_path, monkeypatch)
    _write_scenario(tmp_path, "user-macro.json", _macro_scenario())

    merged = load_camera_rules_with_user_scenarios()
    ctx = PhotoContext(
        focal_length=60.0,
        focal_35mm=120.0,
        aperture=11.0,          # matches user-macro (8 <= 11 <= 16)
        shutter_speed=0.005,
        iso=400,
        iso_relative_to_body="normal",
        focus_mode=FocusMode.MANUAL,   # matches user-macro
        af_area_mode=AfAreaMode.SINGLE_POINT,
        subject_detection=SubjectDetection.NONE,
        drive_mode=DriveMode.SINGLE,
        flash_fired=False,
        lens=None,                      # no registry → built-in T2 macro rules can't fire
        body=None,
        source="camera",
    )
    result = classify(ctx, merged)
    assert result.scenario == Scenario.MACRO
    # The user-rule fired — confidence matches the scenario's baseline.
    assert result.rule_id == "user_user-macro"
    assert pytest.approx(result.confidence, abs=0.01) == 0.88


def test_lens_name_macro_rule_fires_case_insensitive(tmp_path, monkeypatch):
    """The hardware-independent macro rule fires when LensModel
    contains 'macro' AND focus_mode is manual — regardless of casing.
    Covers users without wizard scenarios and without a lens registry.
    Manual focus is the guard against using a macro lens for non-macro
    work (portraits, landscapes); see the next test."""
    _isolate_user_dir(tmp_path, monkeypatch)
    from core.classifier_v2 import load_camera_rules
    rules = load_camera_rules()

    for lens_name in (
        "Olympus M.Zuiko 60mm f/2.8 Macro",
        "PANASONIC LUMIX 30MM MACRO",
        "irix 150mm macro",
    ):
        ctx = PhotoContext(
            focal_length=60.0, focal_35mm=120.0,
            aperture=8.0, shutter_speed=0.005, iso=400,
            iso_relative_to_body="normal",
            focus_mode=FocusMode.MANUAL,
            af_area_mode=AfAreaMode.SINGLE_POINT,
            subject_detection=SubjectDetection.NONE,
            drive_mode=DriveMode.SINGLE,
            flash_fired=False,
            lens=None, body=None,
            lens_model_raw=lens_name,
            source="camera",
        )
        result = classify(ctx, rules)
        assert result.scenario == Scenario.MACRO, (
            f"Expected macro for lens {lens_name!r}, got {result.scenario}"
        )
        assert result.rule_id == "t2_lens_name_macro"


def test_lens_name_macro_does_not_fire_with_autofocus(tmp_path, monkeypatch):
    """Macro lens used for non-macro work (portrait of a person,
    landscape) with AF on must NOT classify as macro just because the
    lens name says 'Macro'. Real macro work is almost always manual
    focus; AF on a macro lens means the user is using it as a general
    short-tele. Falls through to other rules: T1 subject detection
    catches humans → portrait and animals → wildlife; T3 lens
    fallbacks catch the rest."""
    _isolate_user_dir(tmp_path, monkeypatch)
    from core.classifier_v2 import load_camera_rules
    rules = load_camera_rules()

    for af_mode in (FocusMode.SINGLE, FocusMode.CONTINUOUS):
        ctx = PhotoContext(
            focal_length=60.0, focal_35mm=120.0,
            aperture=4.0, shutter_speed=1 / 250, iso=400,
            iso_relative_to_body="normal",
            focus_mode=af_mode,
            af_area_mode=AfAreaMode.SINGLE_POINT,
            subject_detection=SubjectDetection.NONE,
            drive_mode=DriveMode.SINGLE,
            flash_fired=False,
            lens=None, body=None,
            lens_model_raw="Olympus M.Zuiko 60mm f/2.8 Macro",
            source="camera",
        )
        result = classify(ctx, rules)
        assert result.rule_id != "t2_lens_name_macro", (
            f"Macro-name rule must not fire with focus_mode={af_mode}; "
            f"got {result.rule_id}"
        )
        assert result.scenario != Scenario.MACRO, (
            f"Expected non-macro for AF mode {af_mode}, "
            f"got {result.scenario} via {result.rule_id}"
        )
