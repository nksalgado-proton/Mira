"""Tests for core.wizard — state persistence + scenario generation.

Macro block iteration (2026-05-13). The wizard's block-by-block
architecture is exercised here: state machine, genre selection, and
scenario JSON generation. UI-level tests live in test_wizard_window.py.
"""

from __future__ import annotations

import json

import pytest

from core.wizard import (
    ANSWER_SKIP,
    GENRE_MACRO,
    GENRE_PICKER_KEY,
    GENRE_PORTRAIT,
    GENRE_WILDLIFE,
    IMPLEMENTED_GENRES,
    MACRO_APERTURE_KEY,
    MACRO_APERTURE_STOPPED,
    MACRO_APERTURE_WIDE,
    MACRO_BRACKETING_ALWAYS,
    MACRO_BRACKETING_KEY,
    MACRO_BRACKETING_NEVER,
    MACRO_FLASH_KEY,
    MACRO_FLASH_NO,
    MACRO_FLASH_YES,
    MACRO_FOCUS_AF,
    MACRO_FOCUS_KEY,
    MACRO_FOCUS_MANUAL,
    STEP_DONE,
    STEP_GENRE_PICKER,
    STEP_MACRO_BLOCK,
    STEP_WELCOME,
    WIZARD_VERSION,
    WizardState,
    generate_scenarios_from_answers,
    get_selected_genres,
    is_wizard_completed,
    load_wizard_state,
    next_applicable_step,
    previous_applicable_step,
    save_wizard_state,
    set_selected_genres,
)


def _isolate_user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))


# ── Wizard state ──────────────────────────────────────────────────


def test_load_wizard_state_missing_file_returns_fresh(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    state = load_wizard_state()
    assert state.completed is False
    assert state.current_step == STEP_WELCOME
    assert state.answers == {}
    assert state.started_at  # not empty


def test_save_then_load_wizard_state_round_trip(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)

    state = WizardState(
        current_step=STEP_MACRO_BLOCK,
        answers={
            GENRE_PICKER_KEY: GENRE_MACRO,
            MACRO_FOCUS_KEY: MACRO_FOCUS_MANUAL,
        },
    )
    save_wizard_state(state)

    reloaded = load_wizard_state()
    assert reloaded.current_step == STEP_MACRO_BLOCK
    assert reloaded.answers[MACRO_FOCUS_KEY] == MACRO_FOCUS_MANUAL
    assert reloaded.answers[GENRE_PICKER_KEY] == GENRE_MACRO
    assert reloaded.completed is False


def test_is_wizard_completed_false_when_not_finished(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    assert is_wizard_completed() is False


def test_is_wizard_completed_true_after_flag_flip(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    state = WizardState(completed=True)
    save_wizard_state(state)
    assert is_wizard_completed() is True


def test_load_wizard_state_malformed_returns_fresh(tmp_path, monkeypatch):
    """Recovery: if wizard_state.json is broken, start fresh — don't
    crash the app."""
    _isolate_user_dir(tmp_path, monkeypatch)
    (tmp_path / "wizard_state.json").write_text("garbage", encoding="utf-8")
    state = load_wizard_state()
    assert state.completed is False
    assert state.current_step == STEP_WELCOME


def test_save_wizard_state_is_atomic(tmp_path, monkeypatch):
    """save_wizard_state writes via .tmp + rename, leaving no
    partial-write file behind."""
    _isolate_user_dir(tmp_path, monkeypatch)
    state = WizardState()
    save_wizard_state(state)
    target = tmp_path / "wizard_state.json"
    tmp_leftover = tmp_path / "wizard_state.tmp"
    assert target.exists()
    assert not tmp_leftover.exists()


# ── Genre selection helpers ───────────────────────────────────────


def test_set_then_get_selected_genres_round_trips():
    state = WizardState()
    set_selected_genres(state, [GENRE_MACRO])
    assert get_selected_genres(state) == [GENRE_MACRO]


def test_set_selected_genres_filters_unknown():
    """Defensive: setting unknown genre keys should be a no-op for those keys."""
    state = WizardState()
    set_selected_genres(state, [GENRE_MACRO, "bogus_genre"])
    assert get_selected_genres(state) == [GENRE_MACRO]


def test_get_selected_genres_handles_empty():
    state = WizardState()
    assert get_selected_genres(state) == []


def test_implemented_genres_is_subset_of_all_genres():
    """Sanity: every implemented genre is in the catalog."""
    from core.wizard import ALL_GENRES
    for genre in IMPLEMENTED_GENRES:
        assert genre in ALL_GENRES


# ── Step traversal ────────────────────────────────────────────────


def test_next_step_from_welcome_is_capture_overview():
    """Task #96 inserted the Capture & Timezones section between
    Welcome and the Genre Picker. The next step after Welcome is
    now the first capture screen (overview), and the picker is
    reached by stepping through all three capture screens."""
    from core.wizard import (
        STEP_CAPTURE_CALIBRATION,
        STEP_CAPTURE_OVERVIEW,
        STEP_CAPTURE_PRECULL,
    )
    state = WizardState(current_step=STEP_WELCOME)
    assert next_applicable_step(state) == STEP_CAPTURE_OVERVIEW
    state.current_step = STEP_CAPTURE_OVERVIEW
    assert next_applicable_step(state) == STEP_CAPTURE_CALIBRATION
    state.current_step = STEP_CAPTURE_CALIBRATION
    assert next_applicable_step(state) == STEP_CAPTURE_PRECULL
    state.current_step = STEP_CAPTURE_PRECULL
    assert next_applicable_step(state) == STEP_GENRE_PICKER


def test_next_step_skips_macro_block_when_genre_not_selected():
    """If the user didn't pick Macro on the genre picker, the Macro
    block step is not reachable."""
    state = WizardState(current_step=STEP_GENRE_PICKER, answers={})
    # No selection → skip directly past macro block to done.
    assert next_applicable_step(state) == STEP_DONE


def test_next_step_includes_macro_block_when_macro_selected():
    state = WizardState(
        current_step=STEP_GENRE_PICKER,
        answers={GENRE_PICKER_KEY: GENRE_MACRO},
    )
    assert next_applicable_step(state) == STEP_MACRO_BLOCK


def test_next_step_after_macro_block_is_done():
    state = WizardState(
        current_step=STEP_MACRO_BLOCK,
        answers={GENRE_PICKER_KEY: GENRE_MACRO},
    )
    assert next_applicable_step(state) == STEP_DONE


def test_previous_step_from_macro_block_is_genre_picker():
    state = WizardState(
        current_step=STEP_MACRO_BLOCK,
        answers={GENRE_PICKER_KEY: GENRE_MACRO},
    )
    assert previous_applicable_step(state) == STEP_GENRE_PICKER


def test_previous_step_from_welcome_is_none():
    state = WizardState(current_step=STEP_WELCOME)
    assert previous_applicable_step(state) is None


# ── Scenario generation ──────────────────────────────────────────


def test_generate_scenario_for_macro_with_all_answers(tmp_path, monkeypatch):
    """Full set of macro answers → one user-macro.json with every
    user-supplied predicate in exif_expectations."""
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_MACRO,
        MACRO_FOCUS_KEY: MACRO_FOCUS_MANUAL,
        MACRO_APERTURE_KEY: MACRO_APERTURE_STOPPED,
        MACRO_BRACKETING_KEY: MACRO_BRACKETING_ALWAYS,
        MACRO_FLASH_KEY: MACRO_FLASH_YES,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-macro.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["id"] == "user-macro"
    assert payload["genre"] == "macro"
    assert payload["created_by"] == "wizard"
    assert payload["wizard_version"] == WIZARD_VERSION
    # Every habit-answer landed as an exif_expectations clause.
    exif = payload["exif_expectations"]
    assert exif["focus_mode"] == {"eq": "manual"}
    assert exif["aperture"] == {"gte": 8.0, "lte": 16.0}
    assert exif["flash_fired"] == {"eq": True}
    # Bracketing is a scenario-level flag, NOT an exif predicate.
    assert payload["expects_focus_brackets"] is True
    assert "focus_bracket_active" not in exif


def test_generate_scenario_skipped_questions_become_absent_clauses(
    tmp_path, monkeypatch,
):
    """Skipping a question removes the corresponding clause entirely —
    the rule matches more photos (broader). The scenario is still
    written; it's just less specific."""
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_MACRO,
        MACRO_FOCUS_KEY: ANSWER_SKIP,
        MACRO_APERTURE_KEY: MACRO_APERTURE_WIDE,
        MACRO_BRACKETING_KEY: ANSWER_SKIP,
        MACRO_FLASH_KEY: ANSWER_SKIP,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    exif = payload["exif_expectations"]
    # Only the aperture answer survived.
    assert "aperture" in exif
    assert "focus_mode" not in exif
    assert "flash_fired" not in exif
    # Bracketing skip means no focus-bracket expectation.
    assert payload["expects_focus_brackets"] is False


def test_generate_scenario_no_genre_selected_writes_nothing(
    tmp_path, monkeypatch,
):
    """If the user opted into zero genres on the picker, no scenarios
    get written — and that's the legitimate path to General classification."""
    _isolate_user_dir(tmp_path, monkeypatch)
    written = generate_scenarios_from_answers({GENRE_PICKER_KEY: ""})
    assert written == []
    assert not (tmp_path / "scenarios" / "user-macro.json").exists()


def test_generate_scenario_unknown_genre_in_picker_is_ignored(
    tmp_path, monkeypatch,
):
    """Defence: a genre key in selected_genres that isn't in
    IMPLEMENTED_GENRES (e.g. a future v1.1 genre key migrated through
    settings before its block ships) gets silently filtered. The
    implemented genres still produce their scenarios; nothing else does."""
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: f"{GENRE_MACRO},future_genre",
        MACRO_FOCUS_KEY: ANSWER_SKIP,
        MACRO_APERTURE_KEY: ANSWER_SKIP,
        MACRO_BRACKETING_KEY: ANSWER_SKIP,
        MACRO_FLASH_KEY: ANSWER_SKIP,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-macro.json"
    assert not (tmp_path / "scenarios" / "user-future_genre.json").exists()


def test_scenario_includes_reference_card_with_user_specific_text(
    tmp_path, monkeypatch,
):
    """Reference-card text reflects the user's specific answers."""
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_MACRO,
        MACRO_FOCUS_KEY: MACRO_FOCUS_MANUAL,
        MACRO_APERTURE_KEY: MACRO_APERTURE_STOPPED,
        MACRO_BRACKETING_KEY: MACRO_BRACKETING_ALWAYS,
        MACRO_FLASH_KEY: MACRO_FLASH_NO,
    }
    written = generate_scenarios_from_answers(answers)
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    card = payload["reference_card"]
    # User picked manual focus → card mentions it.
    assert "manual" in card["software_settings"]["focus_mode"].lower()
    # User picked stopped aperture → card mentions the f-range.
    assert "f/8" in card["software_settings"]["aperture"].lower()
    # User picked no flash → card mentions available light.
    assert "available light" in card["physical_setup"]["flash"].lower()


def test_scenario_handles_missing_answers_dict(tmp_path, monkeypatch):
    """Empty answers dict → no scenarios written (no crash)."""
    _isolate_user_dir(tmp_path, monkeypatch)
    assert generate_scenarios_from_answers({}) == []


# ── Wildlife + Sports scenarios ───────────────────────────────────


def test_generate_wildlife_scenario_with_subject_detection(
    tmp_path, monkeypatch,
):
    """Subject-detection answer maps to subject_detection clause that
    mirrors the built-in T1 wildlife rule."""
    from core.wizard import (
        DRIVE_BURST_LOW, FOCAL_LONG_TELE, GENRE_WILDLIFE, SHUTTER_FAST,
        WILDLIFE_AF_KEY, WILDLIFE_AF_SUBJECT_DETECT, WILDLIFE_DRIVE_KEY,
        WILDLIFE_FOCAL_KEY, WILDLIFE_SHUTTER_KEY,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_WILDLIFE,
        WILDLIFE_FOCAL_KEY:   FOCAL_LONG_TELE,
        WILDLIFE_AF_KEY:      WILDLIFE_AF_SUBJECT_DETECT,
        WILDLIFE_DRIVE_KEY:   DRIVE_BURST_LOW,
        WILDLIFE_SHUTTER_KEY: SHUTTER_FAST,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-wildlife.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["genre"] == "wildlife"
    exif = payload["exif_expectations"]
    assert exif["subject_detection"] == {"in": ["animal", "bird"]}
    assert exif["focal_35mm"] == {"gte": 200, "lte": 400}
    assert exif["drive_mode"] == {"eq": "burst_low"}
    assert exif["shutter_speed"] == {"gte": 0.0005, "lte": 0.002}


def test_generate_wildlife_scenario_with_manual_focus(tmp_path, monkeypatch):
    """Manual-focus answer produces focus_mode clause instead of
    subject_detection. Sanity check that the AF question fans out."""
    from core.wizard import (
        GENRE_WILDLIFE, WILDLIFE_AF_KEY, WILDLIFE_AF_MANUAL,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_WILDLIFE,
        WILDLIFE_AF_KEY: WILDLIFE_AF_MANUAL,
    }
    written = generate_scenarios_from_answers(answers)
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    exif = payload["exif_expectations"]
    assert exif["focus_mode"] == {"eq": "manual"}
    assert "subject_detection" not in exif


def test_generate_sports_scenario_with_human_detect(tmp_path, monkeypatch):
    """Sports human-detection answer adds both subject_detection and
    faces_detected (matching the T1 portrait rule's corroboration
    pattern — avoid false positives from a stale detection mode)."""
    from core.wizard import (
        DRIVE_BURST_HIGH, FOCAL_SHORT_TELE, GENRE_SPORTS, SHUTTER_VERY_FAST,
        SPORTS_AF_HUMAN_DETECT, SPORTS_AF_KEY, SPORTS_DRIVE_KEY,
        SPORTS_FOCAL_KEY, SPORTS_SHUTTER_KEY,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_SPORTS,
        SPORTS_FOCAL_KEY:   FOCAL_SHORT_TELE,
        SPORTS_AF_KEY:      SPORTS_AF_HUMAN_DETECT,
        SPORTS_DRIVE_KEY:   DRIVE_BURST_HIGH,
        SPORTS_SHUTTER_KEY: SHUTTER_VERY_FAST,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-sports.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["genre"] == "sports"
    exif = payload["exif_expectations"]
    assert exif["subject_detection"] == {"eq": "human"}
    assert exif["faces_detected"] == {"gt": 0}
    assert exif["focal_35mm"] == {"gte": 70, "lte": 200}
    assert exif["drive_mode"] == {"eq": "burst_high"}
    assert exif["shutter_speed"] == {"lte": 0.0005}


def test_generate_multiple_scenarios_when_multiple_genres_selected(
    tmp_path, monkeypatch,
):
    """Picking Macro + Wildlife + Sports writes three files."""
    from core.wizard import GENRE_SPORTS, GENRE_WILDLIFE
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: f"{GENRE_MACRO},{GENRE_WILDLIFE},{GENRE_SPORTS}",
        # All other answers skipped → builders produce broad scenarios.
    }
    written = generate_scenarios_from_answers(answers)
    names = sorted(p.name for p in written)
    assert names == ["user-macro.json", "user-sports.json", "user-wildlife.json"]


def test_wildlife_step_traversal_when_only_wildlife_picked(tmp_path, monkeypatch):
    """Genre picker → Wildlife block → Done. Macro block + Sports
    block are skipped because their genres weren't picked."""
    from core.wizard import (
        GENRE_WILDLIFE, STEP_MACRO_BLOCK, STEP_SPORTS_BLOCK, STEP_WILDLIFE_BLOCK,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    state = WizardState(
        current_step=STEP_GENRE_PICKER,
        answers={GENRE_PICKER_KEY: GENRE_WILDLIFE},
    )
    assert next_applicable_step(state) == STEP_WILDLIFE_BLOCK

    state.current_step = STEP_WILDLIFE_BLOCK
    assert next_applicable_step(state) == STEP_DONE


# ── Landscape + Astro scenarios ───────────────────────────────────


def test_generate_landscape_scenario_with_long_exposure_frequent(
    tmp_path, monkeypatch,
):
    """Frequent-long-exposure answer adds a shutter_speed ≥ 1.0 clause
    and the 'long_exposure' tag."""
    from core.wizard import (
        FOCAL_WIDE, GENRE_LANDSCAPE, LANDSCAPE_AF_KEY,
        LANDSCAPE_AF_SINGLE_POINT, LANDSCAPE_APERTURE_KEY,
        LANDSCAPE_APERTURE_STANDARD, LANDSCAPE_FOCAL_KEY,
        LANDSCAPE_LONG_EXPOSURE_FREQUENT, LANDSCAPE_LONG_EXPOSURE_KEY,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_LANDSCAPE,
        LANDSCAPE_FOCAL_KEY:         FOCAL_WIDE,
        LANDSCAPE_APERTURE_KEY:      LANDSCAPE_APERTURE_STANDARD,
        LANDSCAPE_LONG_EXPOSURE_KEY: LANDSCAPE_LONG_EXPOSURE_FREQUENT,
        LANDSCAPE_AF_KEY:            LANDSCAPE_AF_SINGLE_POINT,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-landscape.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["genre"] == "landscape"
    exif = payload["exif_expectations"]
    assert exif["focal_35mm"] == {"gte": 24, "lte": 35}
    assert exif["aperture"] == {"gte": 5.6, "lte": 11.0}
    assert exif["shutter_speed"] == {"gte": 1.0}
    assert exif["focus_mode"] == {"eq": "single"}
    assert "long_exposure" in payload["tags"]


def test_generate_astro_scenario_with_milky_way_defaults(tmp_path, monkeypatch):
    """Milky-Way style answers produce wide-open aperture + very long
    shutter + ultra-wide focal — the canonical Milky Way EXIF signature."""
    from core.wizard import (
        ASTRO_APERTURE_KEY, ASTRO_APERTURE_WIDE_OPEN, ASTRO_FOCAL_KEY,
        ASTRO_SHUTTER_KEY, ASTRO_SHUTTER_VERY_LONG, ASTRO_SUBTYPE_KEY,
        ASTRO_SUBTYPE_MILKY_WAY, FOCAL_ULTRA_WIDE, GENRE_ASTRO,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_ASTRO,
        ASTRO_SUBTYPE_KEY:  ASTRO_SUBTYPE_MILKY_WAY,
        ASTRO_FOCAL_KEY:    FOCAL_ULTRA_WIDE,
        ASTRO_APERTURE_KEY: ASTRO_APERTURE_WIDE_OPEN,
        ASTRO_SHUTTER_KEY:  ASTRO_SHUTTER_VERY_LONG,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-astro.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["genre"] == "astro"
    exif = payload["exif_expectations"]
    assert exif["focal_35mm"] == {"lte": 24}
    assert exif["aperture"] == {"gte": 1.4, "lte": 2.8}
    assert exif["shutter_speed"] == {"gte": 10.0, "lte": 30.0}
    # Sub-type lands in tags, not exif clauses.
    assert "milky_way" in payload["tags"]


def test_generate_astro_scenario_for_moon_inverts_settings(tmp_path, monkeypatch):
    """Moon work needs stopped aperture + fast shutter — the inverse
    of Milky Way. Sanity-check the per-option mapping holds."""
    from core.wizard import (
        ASTRO_APERTURE_KEY, ASTRO_APERTURE_STOPPED, ASTRO_FOCAL_KEY,
        ASTRO_SHUTTER_FAST, ASTRO_SHUTTER_KEY, ASTRO_SUBTYPE_KEY,
        ASTRO_SUBTYPE_MOON, FOCAL_LONG_TELE, GENRE_ASTRO,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_ASTRO,
        ASTRO_SUBTYPE_KEY:  ASTRO_SUBTYPE_MOON,
        ASTRO_FOCAL_KEY:    FOCAL_LONG_TELE,
        ASTRO_APERTURE_KEY: ASTRO_APERTURE_STOPPED,
        ASTRO_SHUTTER_KEY:  ASTRO_SHUTTER_FAST,
    }
    written = generate_scenarios_from_answers(answers)
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    exif = payload["exif_expectations"]
    assert exif["focal_35mm"] == {"gte": 200, "lte": 400}
    assert exif["aperture"] == {"gte": 8.0, "lte": 11.0}
    assert exif["shutter_speed"] == {"lte": 0.004}
    assert "moon" in payload["tags"]


# ── Portrait + Family scenarios ───────────────────────────────────


def test_generate_portrait_scenario_with_face_detection(tmp_path, monkeypatch):
    """Face/eye AF answer maps to af_area_mode=face_eye plus the
    faces_detected>0 corroboration (matches built-in t1_af_face_eye
    rule's false-positive guard)."""
    from core.wizard import (
        FOCAL_SHORT_TELE, GENRE_PORTRAIT, PORTRAIT_AF_FACE_EYE,
        PORTRAIT_AF_KEY, PORTRAIT_APERTURE_KEY, PORTRAIT_APERTURE_VERY_WIDE,
        PORTRAIT_FOCAL_KEY, PORTRAIT_LIGHTING_KEY, PORTRAIT_LIGHTING_NATURAL,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_PORTRAIT,
        PORTRAIT_FOCAL_KEY:    FOCAL_SHORT_TELE,
        PORTRAIT_AF_KEY:       PORTRAIT_AF_FACE_EYE,
        PORTRAIT_APERTURE_KEY: PORTRAIT_APERTURE_VERY_WIDE,
        PORTRAIT_LIGHTING_KEY: PORTRAIT_LIGHTING_NATURAL,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-portrait.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["genre"] == "portrait"
    exif = payload["exif_expectations"]
    assert exif["af_area_mode"] == {"eq": "face_eye"}
    assert exif["faces_detected"] == {"gt": 0}
    assert exif["focal_35mm"] == {"gte": 70, "lte": 200}
    assert exif["aperture"] == {"gte": 1.4, "lte": 2.8}
    assert exif["flash_fired"] == {"eq": False}


def test_generate_portrait_scenario_with_strobe(tmp_path, monkeypatch):
    """Strobe lighting answer flips flash_fired to true (same EXIF
    clause as speedlight — only reference-card text differs)."""
    from core.wizard import (
        GENRE_PORTRAIT, PORTRAIT_LIGHTING_KEY, PORTRAIT_LIGHTING_STROBE,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_PORTRAIT,
        PORTRAIT_LIGHTING_KEY: PORTRAIT_LIGHTING_STROBE,
    }
    written = generate_scenarios_from_answers(answers)
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["exif_expectations"]["flash_fired"] == {"eq": True}


def test_generate_street_scenario_with_monochrome(tmp_path, monkeypatch):
    """Monochrome color answer adds photo_style clause + monochrome
    tag — the iconic street rendering signal."""
    from core.wizard import (
        FOCAL_WIDE, GENRE_STREET, STREET_AF_KEY, STREET_AF_SINGLE,
        STREET_APERTURE_KEY, STREET_APERTURE_MODERATE, STREET_COLOR_KEY,
        STREET_COLOR_MONOCHROME, STREET_FOCAL_KEY,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_STREET,
        STREET_FOCAL_KEY:    FOCAL_WIDE,
        STREET_AF_KEY:       STREET_AF_SINGLE,
        STREET_APERTURE_KEY: STREET_APERTURE_MODERATE,
        STREET_COLOR_KEY:    STREET_COLOR_MONOCHROME,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-street.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["genre"] == "street"
    exif = payload["exif_expectations"]
    assert exif["focal_35mm"] == {"gte": 24, "lte": 35}
    assert exif["focus_mode"] == {"eq": "single"}
    assert exif["aperture"] == {"gte": 2.8, "lte": 5.6}
    assert exif["photo_style"] == {"eq": "monochrome"}
    assert "monochrome" in payload["tags"]


def test_generate_video_scenario_minimal_exif(tmp_path, monkeypatch):
    """Video scenario is reference-card-heavy and exif-light per
    docs/04 ("v1 does not over-attempt video classification"). The
    only stills-EXIF clause is focal length when set; recording mode,
    resolution, and subject feed reference card + tags."""
    from core.wizard import (
        FOCAL_SHORT_TELE, GENRE_VIDEO, VIDEO_FOCAL_KEY,
        VIDEO_RECORDING_KEY, VIDEO_RECORDING_V_LOG, VIDEO_RESOLUTION_4K_24,
        VIDEO_RESOLUTION_KEY, VIDEO_SUBJECT_KEY,
        VIDEO_SUBJECT_WILDLIFE_BEHAVIOR,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_VIDEO,
        VIDEO_RECORDING_KEY:  VIDEO_RECORDING_V_LOG,
        VIDEO_RESOLUTION_KEY: VIDEO_RESOLUTION_4K_24,
        VIDEO_FOCAL_KEY:      FOCAL_SHORT_TELE,
        VIDEO_SUBJECT_KEY:    VIDEO_SUBJECT_WILDLIFE_BEHAVIOR,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-video.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["genre"] == "video"
    exif = payload["exif_expectations"]
    # Focal is the only stills-EXIF clause that lands.
    assert exif["focal_35mm"] == {"gte": 70, "lte": 200}
    assert "shutter_speed" not in exif
    assert "aperture" not in exif
    # Subject feeds tags.
    assert "wildlife_behavior" in payload["tags"]
    # Recording + resolution land in reference card, not exif clauses.
    card = payload["reference_card"]
    assert "V-Log" in card["software_settings"]["recording_mode"]
    assert "4K at 24p" in card["software_settings"]["resolution_framerate"]
    # Confidence floor slightly lower (fallback-ish behavior).
    assert payload["confidence_baseline"] == 0.80


def test_generate_travel_scenario_with_mixed_focal_defaults(tmp_path, monkeypatch):
    """Travel's default 'mixed' focal length produces NO focal clause —
    the fallback genre stays broad. Single AF + single drive land
    cleanly though."""
    from core.wizard import (
        DRIVE_SINGLE, FOCAL_MIXED, GENRE_TRAVEL, TRAVEL_AF_KEY,
        TRAVEL_AF_SINGLE, TRAVEL_APERTURE_KEY, TRAVEL_APERTURE_MODERATE,
        TRAVEL_DRIVE_KEY, TRAVEL_FOCAL_KEY,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_TRAVEL,
        TRAVEL_FOCAL_KEY:    FOCAL_MIXED,
        TRAVEL_APERTURE_KEY: TRAVEL_APERTURE_MODERATE,
        TRAVEL_AF_KEY:       TRAVEL_AF_SINGLE,
        TRAVEL_DRIVE_KEY:    DRIVE_SINGLE,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-travel.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["genre"] == "travel"
    exif = payload["exif_expectations"]
    # Mixed focal → no focal clause (broader match).
    assert "focal_35mm" not in exif
    assert exif["aperture"] == {"gte": 4.0, "lte": 8.0}
    assert exif["focus_mode"] == {"eq": "single"}
    assert exif["drive_mode"] == {"eq": "single"}
    # Travel uses a slightly lower confidence baseline (fallback genre).
    assert payload["confidence_baseline"] == 0.80


def test_generate_family_scenario_with_continuous_af(tmp_path, monkeypatch):
    """Family's distinct AF option — continuous AF (kids moving) —
    produces focus_mode=continuous, not face_eye."""
    from core.wizard import (
        FAMILY_AF_CONTINUOUS, FAMILY_AF_KEY, FAMILY_APERTURE_KEY,
        FAMILY_APERTURE_MODERATE, FAMILY_FLASH_AVAILABLE, FAMILY_FLASH_KEY,
        FAMILY_FOCAL_KEY, FOCAL_NORMAL, GENRE_FAMILY,
    )
    _isolate_user_dir(tmp_path, monkeypatch)
    answers = {
        GENRE_PICKER_KEY: GENRE_FAMILY,
        FAMILY_FOCAL_KEY:    FOCAL_NORMAL,
        FAMILY_AF_KEY:       FAMILY_AF_CONTINUOUS,
        FAMILY_APERTURE_KEY: FAMILY_APERTURE_MODERATE,
        FAMILY_FLASH_KEY:    FAMILY_FLASH_AVAILABLE,
    }
    written = generate_scenarios_from_answers(answers)
    assert len(written) == 1
    assert written[0].name == "user-family.json"

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["genre"] == "family"
    exif = payload["exif_expectations"]
    assert exif["focus_mode"] == {"eq": "continuous"}
    assert "af_area_mode" not in exif  # not face_eye in this branch
    assert exif["focal_35mm"] == {"gte": 35, "lte": 70}
    assert exif["aperture"] == {"gte": 2.8, "lte": 5.6}
    assert exif["flash_fired"] == {"eq": False}



# ── Task #96 — Capture & Timezones (3 steps + settings contract) ─


def test_capture_steps_are_always_applicable():
    """The three capture screens sit between Welcome and Genre
    Picker and are part of the every-user educational flow — they
    must not be skipped based on genre selection (unlike genre
    blocks)."""
    from core.wizard import (
        STEP_CAPTURE_CALIBRATION,
        STEP_CAPTURE_OVERVIEW,
        STEP_CAPTURE_PRECULL,
    )
    state = WizardState(current_step=STEP_WELCOME)
    assert next_applicable_step(state) == STEP_CAPTURE_OVERVIEW
    state.current_step = STEP_CAPTURE_OVERVIEW
    assert next_applicable_step(state) == STEP_CAPTURE_CALIBRATION
    state.current_step = STEP_CAPTURE_CALIBRATION
    assert next_applicable_step(state) == STEP_CAPTURE_PRECULL
    state.current_step = STEP_CAPTURE_PRECULL
    assert next_applicable_step(state) == STEP_GENRE_PICKER


def test_capture_steps_reachable_via_back_button():
    """Back from the genre picker walks BACK through the capture
    section (so the user can revise a calibration / pre-cull pick
    without restarting). Mirror of next_applicable_step."""
    from core.wizard import (
        STEP_CAPTURE_CALIBRATION,
        STEP_CAPTURE_OVERVIEW,
        STEP_CAPTURE_PRECULL,
    )
    state = WizardState(current_step=STEP_GENRE_PICKER)
    assert previous_applicable_step(state) == STEP_CAPTURE_PRECULL
    state.current_step = STEP_CAPTURE_PRECULL
    assert previous_applicable_step(state) == STEP_CAPTURE_CALIBRATION
    state.current_step = STEP_CAPTURE_CALIBRATION
    assert previous_applicable_step(state) == STEP_CAPTURE_OVERVIEW
    state.current_step = STEP_CAPTURE_OVERVIEW
    assert previous_applicable_step(state) == STEP_WELCOME


@pytest.mark.skip(reason="Slice B: legacy + rebuild vocab boundary")
def test_apply_capture_settings_writes_picks_to_settings(
    tmp_path, monkeypatch,
):
    """The capture section feeds settings.json (not scenario
    files) — calibration_mode + default_pre_cull_mode. Round-trip
    via load/save_settings to prove persistence."""
    _isolate_user_dir(tmp_path, monkeypatch)
    from core.settings import load_settings
    from core.wizard import (
        CAPTURE_CALIBRATION_KEY,
        CAPTURE_CALIBRATION_SAVED,
        CAPTURE_PRECULL_KEY,
        CAPTURE_PRECULL_PRECULL,
        apply_capture_settings_to_settings,
    )
    answers = {
        CAPTURE_CALIBRATION_KEY: CAPTURE_CALIBRATION_SAVED,
        CAPTURE_PRECULL_KEY: CAPTURE_PRECULL_PRECULL,
    }
    applied = apply_capture_settings_to_settings(answers)
    assert applied == {
        "calibration_mode": "saved",
        "default_pre_cull_mode": "pre_cull",
    }
    settings = load_settings()
    assert settings["calibration_mode"] == "saved"
    assert settings["default_pre_cull_mode"] == "pre_cull"


@pytest.mark.skip(reason="Slice B: legacy + rebuild vocab boundary")
def test_apply_capture_settings_no_op_with_empty_answers(
    tmp_path, monkeypatch,
):
    """User cancelled mid-wizard before answering either picker —
    apply_capture_settings_to_settings leaves settings.json
    untouched (no key written)."""
    _isolate_user_dir(tmp_path, monkeypatch)
    from core.settings import load_settings
    from core.wizard import apply_capture_settings_to_settings

    baseline = load_settings()
    baseline_cal = baseline["calibration_mode"]
    baseline_pre = baseline["default_pre_cull_mode"]

    assert apply_capture_settings_to_settings({}) == {}

    after = load_settings()
    assert after["calibration_mode"] == baseline_cal
    assert after["default_pre_cull_mode"] == baseline_pre


@pytest.mark.skip(reason="Slice B: legacy + rebuild vocab boundary")
def test_apply_capture_settings_ignores_garbage_values(
    tmp_path, monkeypatch,
):
    """Defensive: a hand-edited wizard_state.json with a value not
    in the documented set is dropped silently (the setting keeps
    its current value)."""
    _isolate_user_dir(tmp_path, monkeypatch)
    from core.settings import load_settings
    from core.wizard import (
        CAPTURE_CALIBRATION_KEY,
        CAPTURE_PRECULL_KEY,
        apply_capture_settings_to_settings,
    )
    applied = apply_capture_settings_to_settings({
        CAPTURE_CALIBRATION_KEY: "banana",
        CAPTURE_PRECULL_KEY: "nonsense",
    })
    assert applied == {}
    after = load_settings()
    assert after["calibration_mode"] in ("prompt", "saved", "reference_photo")
    assert after["default_pre_cull_mode"] in ("verbatim", "pre_cull")


@pytest.mark.skip(reason="Slice B: legacy + rebuild vocab boundary")
def test_apply_capture_settings_partial_only_writes_present_key(
    tmp_path, monkeypatch,
):
    """The user might back out after answering one picker but not
    the other. The helper writes only the keys that are present
    AND valid — the rest stays at its current settings.json value."""
    _isolate_user_dir(tmp_path, monkeypatch)
    from core.settings import load_settings
    from core.wizard import (
        CAPTURE_CALIBRATION_KEY,
        CAPTURE_CALIBRATION_REFERENCE_PHOTO,
        apply_capture_settings_to_settings,
    )
    baseline_pre = load_settings()["default_pre_cull_mode"]
    applied = apply_capture_settings_to_settings({
        CAPTURE_CALIBRATION_KEY: CAPTURE_CALIBRATION_REFERENCE_PHOTO,
    })
    assert applied == {"calibration_mode": "reference_photo"}
    after = load_settings()
    assert after["calibration_mode"] == "reference_photo"
    assert after["default_pre_cull_mode"] == baseline_pre
