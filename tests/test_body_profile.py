"""Tests for body profile loader."""

import json

import pytest

from core.body_profile import (
    BodyProfile,
    Capabilities,
    IsoBaseline,
    Sensor,
    SubjectDetectionCapability,
    build_stub_body_profile,
    list_available_body_profiles,
    load_body_profile,
    match_body_profile_for_photo,
    parse_body_profile,
)
from core.vocabulary import SubjectDetection


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_capabilities_has_by_name():
    c = Capabilities(ibis=True, focus_bracket=True)
    assert c.has("ibis") is True
    assert c.has("focus_bracket") is True
    assert c.has("exposure_bracket") is False
    assert c.has("pixel_shift") is False
    assert c.has("nonexistent_capability") is False


# ---------------------------------------------------------------------------
# SubjectDetectionCapability
# ---------------------------------------------------------------------------

def test_subject_detection_supports():
    sd = SubjectDetectionCapability(
        supported=True,
        types=[SubjectDetection.HUMAN, SubjectDetection.BIRD],
    )
    assert sd.supports(SubjectDetection.HUMAN) is True
    assert sd.supports(SubjectDetection.BIRD) is True
    assert sd.supports(SubjectDetection.ANIMAL) is False


def test_subject_detection_unsupported_body():
    sd = SubjectDetectionCapability(supported=False, types=[SubjectDetection.HUMAN])
    # Even though HUMAN is in the list, the body doesn't support detection at all
    assert sd.supports(SubjectDetection.HUMAN) is False


# ---------------------------------------------------------------------------
# IsoBaseline
# ---------------------------------------------------------------------------

def test_iso_baseline_classifies_high():
    b = IsoBaseline(native_min=100, native_max=25600, high_iso_threshold=6400)
    assert b.classify(12800) == "high"
    assert b.classify(6400) == "high"


def test_iso_baseline_classifies_low():
    b = IsoBaseline(native_min=100, high_iso_threshold=6400)
    assert b.classify(100) == "low"
    assert b.classify(200) == "low"


def test_iso_baseline_classifies_normal():
    b = IsoBaseline(native_min=100, high_iso_threshold=6400)
    assert b.classify(800) == "normal"
    assert b.classify(3200) == "normal"


def test_iso_baseline_invalid_iso_is_normal():
    b = IsoBaseline()
    assert b.classify(0) == "normal"
    assert b.classify(-1) == "normal"


# ---------------------------------------------------------------------------
# BodyProfile — focal_35mm and model matching
# ---------------------------------------------------------------------------

def _minimal_body(crop: float = 2.0) -> BodyProfile:
    return BodyProfile(
        body_id="test_body",
        display_name="Test Body",
        brand_id="test",
        exiftool_model_match=["TEST-1"],
        sensor=Sensor(size="test", crop_factor=crop),
    )


def test_focal_35mm_mft():
    b = _minimal_body(crop=2.0)
    assert b.focal_35mm(100.0) == 200.0
    assert b.focal_35mm(400.0) == 800.0


def test_focal_35mm_aps_c():
    b = _minimal_body(crop=1.5)
    assert b.focal_35mm(100.0) == 150.0
    assert b.focal_35mm(50.0) == 75.0


def test_matches_model_exact_case_insensitive():
    b = _minimal_body()
    # Exact match (case-insensitive) works
    assert b.matches_model("TEST-1") is True
    assert b.matches_model("test-1") is True
    assert b.matches_model("Test-1") is True
    # Exact mismatch
    assert b.matches_model("OTHER-2") is False
    assert b.matches_model("") is False


def test_matches_model_rejects_substring():
    """Model strings must match EXACTLY — substring matching would cause
    collisions like "DC-G9" incorrectly matching "DC-G9M2"."""
    b = _minimal_body()  # exiftool_model_match=["TEST-1"]
    assert b.matches_model("TEST-1 Camera Pro") is False
    assert b.matches_model("MY-TEST-1") is False
    assert b.matches_model("TEST-1M2") is False


def test_matches_model_requires_exact_for_collision_avoidance():
    """Verify the exact fix for the G9 / G9M2 collision."""
    g9 = BodyProfile(
        body_id="g9",
        display_name="G9",
        brand_id="panasonic",
        exiftool_model_match=["DC-G9"],
        sensor=Sensor(crop_factor=2.0),
    )
    g9m2 = BodyProfile(
        body_id="g9m2",
        display_name="G9 II",
        brand_id="panasonic",
        exiftool_model_match=["DC-G9M2"],
        sensor=Sensor(crop_factor=2.0),
    )
    # G9 must NOT match G9M2 photos
    assert g9.matches_model("DC-G9M2") is False
    assert g9m2.matches_model("DC-G9M2") is True
    # G9M2 must NOT match G9 photos
    assert g9m2.matches_model("DC-G9") is False
    assert g9.matches_model("DC-G9") is True


# ---------------------------------------------------------------------------
# parse_body_profile — JSON shape tolerance
# ---------------------------------------------------------------------------

def test_parse_minimal_json():
    data = {
        "body_id": "x",
        "brand_id": "x",
        "exiftool_model_match": ["X"],
    }
    b = parse_body_profile(data)
    assert b.body_id == "x"
    assert b.kind == "camera"  # default
    assert b.sensor.crop_factor == 1.0  # default
    assert b.capabilities.ibis is False


def test_parse_full_json():
    data = {
        "body_id": "full",
        "display_name": "Full Body",
        "brand_id": "test",
        "kind": "camera",
        "exiftool_model_match": ["FULL"],
        "year_released": 2023,
        "mount": "Test Mount",
        "sensor": {"size": "aps_c", "crop_factor": 1.5, "megapixels": 26},
        "capabilities": {
            "ibis": True,
            "focus_bracket": True,
            "phase_detect_af": True,
        },
        "subject_detection": {
            "supported": True,
            "types": ["human", "animal", "bird"],
        },
        "iso_baseline": {
            "native_min": 100,
            "native_max": 32000,
            "high_iso_threshold": 6400,
        },
    }
    b = parse_body_profile(data)
    assert b.display_name == "Full Body"
    assert b.sensor.crop_factor == 1.5
    assert b.sensor.megapixels == 26
    assert b.capabilities.ibis is True
    assert b.capabilities.focus_bracket is True
    assert b.capabilities.pixel_shift is False  # not specified, default false
    assert b.subject_detection.supported is True
    assert SubjectDetection.BIRD in b.subject_detection.types
    assert b.iso_baseline.high_iso_threshold == 6400


def test_parse_invalid_kind_falls_back_to_camera():
    data = {
        "body_id": "x",
        "brand_id": "x",
        "exiftool_model_match": ["X"],
        "kind": "alien_spaceship",
    }
    b = parse_body_profile(data)
    assert b.kind == "camera"


def test_parse_unknown_subject_detection_type_is_skipped():
    """Unknown subject detection values in JSON should be ignored, not crash."""
    data = {
        "body_id": "x",
        "brand_id": "x",
        "exiftool_model_match": ["X"],
        "subject_detection": {
            "supported": True,
            "types": ["human", "unicorn", "bird"],
        },
    }
    b = parse_body_profile(data)
    assert SubjectDetection.HUMAN in b.subject_detection.types
    assert SubjectDetection.BIRD in b.subject_detection.types
    assert len(b.subject_detection.types) == 2


# ---------------------------------------------------------------------------
# Built-in profiles — smoke tests against real JSON files
# ---------------------------------------------------------------------------

def test_g9_ii_profile_loads():
    b = load_body_profile("panasonic_g9_ii")
    assert b.body_id == "panasonic_g9_ii"
    assert b.brand_id == "panasonic"
    assert b.sensor.crop_factor == 2.0
    assert b.capabilities.ibis is True
    assert b.capabilities.focus_bracket is True
    assert b.subject_detection.supported is True


def test_a6700_profile_loads():
    b = load_body_profile("sony_a6700")
    assert b.body_id == "sony_a6700"
    assert b.brand_id == "sony"
    assert b.sensor.crop_factor == 1.5
    assert b.capabilities.phase_detect_af is True


def test_g9_ii_focal_conversion():
    b = load_body_profile("panasonic_g9_ii")
    # 100-400mm lens on G9II = 200-800mm equiv
    assert b.focal_35mm(100) == 200
    assert b.focal_35mm(400) == 800


def test_a6700_focal_conversion():
    b = load_body_profile("sony_a6700")
    # 70-350mm on A6700 = 105-525mm equiv
    assert b.focal_35mm(70) == 105
    assert b.focal_35mm(350) == 525


def test_list_available_body_profiles_contains_both():
    profiles = list_available_body_profiles()
    assert "panasonic_g9_ii" in profiles
    assert "sony_a6700" in profiles
    assert "gopro_hero12_black" in profiles
    assert "apple_iphone_11" in profiles
    assert "apple_iphone_12" in profiles


def test_match_body_profile_for_photo_gopro_hero12():
    """Regression for the 2026-04-29 Costa Rica field test where
    every GoPro file logged ``Unknown body 'GoPro HERO12 Black'
    — generating stub profile`` because no matching body profile
    existed. With the new gopro_hero12_black.json it should match
    cleanly via Make=GoPro + Model=HERO12 Black."""
    exif = {"Make": "GoPro", "Model": "HERO12 Black"}
    b = match_body_profile_for_photo(exif)
    assert b is not None
    assert b.body_id == "gopro_hero12_black"
    assert b.brand_id == "gopro"
    # No AI subject detection, no brackets — verify capabilities
    # match the action-camera shape we configured
    assert b.subject_detection.supported is False
    assert b.crop_factor == 5.56


def test_match_body_profile_for_photo_iphone_11_and_12():
    """Regression for the 2026-04-29 phone-import warning storm where
    every iPhone photo logged ``Unknown body 'Apple iPhone 11'`` /
    ``'Apple iPhone 12'`` (Nelson's friends' phones from the trip).
    Both bodies must match cleanly. iPhone 11 Pro / Pro Max are NOT
    covered by these profiles — exact-match means they fall through
    to stub until someone adds them."""
    b11 = match_body_profile_for_photo({"Make": "Apple", "Model": "iPhone 11"})
    assert b11 is not None
    assert b11.body_id == "apple_iphone_11"
    assert b11.brand_id == "apple"
    assert b11.subject_detection.supported is True

    b12 = match_body_profile_for_photo({"Make": "Apple", "Model": "iPhone 12"})
    assert b12 is not None
    assert b12.body_id == "apple_iphone_12"

    # Defensive: Pro variants share the "iPhone 11" prefix but must
    # not match — exact-match prevents a Pro/Pro Max from inheriting
    # the wrong sensor specs.
    pro = match_body_profile_for_photo({"Make": "Apple", "Model": "iPhone 11 Pro"})
    assert pro is None


def test_match_body_profile_for_photo_g9_ii():
    exif = {"Make": "Panasonic", "Model": "DC-G9M2"}
    b = match_body_profile_for_photo(exif)
    assert b is not None
    assert b.body_id == "panasonic_g9_ii"


def test_match_body_profile_for_photo_a6700():
    exif = {"Make": "SONY", "Model": "ILCE-6700"}
    b = match_body_profile_for_photo(exif)
    assert b is not None
    assert b.body_id == "sony_a6700"


def test_match_body_profile_for_photo_unknown():
    exif = {"Make": "Hasselblad", "Model": "X2D"}
    assert match_body_profile_for_photo(exif) is None


# ---------------------------------------------------------------------------
# Stub generation for unknown bodies
# ---------------------------------------------------------------------------

def test_build_stub_body_profile():
    exif = {"Make": "Canon", "Model": "EOS R50"}
    stub = build_stub_body_profile(exif, brand_id="canon")
    assert stub.body_id == "eos_r50"
    assert "Canon" in stub.display_name
    assert "EOS R50" in stub.display_name
    assert stub.kind == "camera"
    assert stub.capabilities.ibis is False  # conservative defaults
    assert stub.capabilities.focus_bracket is False
    assert stub.sensor.crop_factor == 1.0
    assert stub.matches_model("EOS R50") is True


def test_build_stub_body_profile_missing_model():
    exif = {"Make": "Unknown"}
    stub = build_stub_body_profile(exif)
    assert stub.body_id == "unknown_model"
    assert stub.brand_id == "unknown"


# ---------------------------------------------------------------------------
# User override merge
# ---------------------------------------------------------------------------

def test_user_override_capability_flip(tmp_path, monkeypatch):
    """User override should be able to flip a single capability without
    rewriting the whole capabilities block."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    user_dir = tmp_path / "body_profiles"
    user_dir.mkdir(parents=True)

    override = {"capabilities": {"pixel_shift": True}}
    (user_dir / "panasonic_g9_ii.json").write_text(
        json.dumps(override), encoding="utf-8"
    )

    b = load_body_profile("panasonic_g9_ii")
    # Overridden
    assert b.capabilities.pixel_shift is True
    # Original values preserved
    assert b.capabilities.ibis is True
    assert b.capabilities.focus_bracket is True


def test_user_override_iso_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    user_dir = tmp_path / "body_profiles"
    user_dir.mkdir(parents=True)

    override = {"iso_baseline": {"high_iso_threshold": 12800}}
    (user_dir / "panasonic_g9_ii.json").write_text(
        json.dumps(override), encoding="utf-8"
    )

    b = load_body_profile("panasonic_g9_ii")
    assert b.iso_baseline.high_iso_threshold == 12800
    # Other iso_baseline fields preserved
    assert b.iso_baseline.native_min == 100
    assert b.iso_baseline.native_max == 25600


def test_load_nonexistent_body_profile_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        load_body_profile("nikon_z8")
