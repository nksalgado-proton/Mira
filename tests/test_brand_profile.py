"""Tests for brand profile loader and EXIF translator."""

import json

import pytest

from core.brand_profile import (
    BracketRule,
    BrandProfile,
    LensAlias,
    LensNormalization,
    TagMapping,
    list_available_brand_profiles,
    load_brand_profile,
    match_brand_profile_for_photo,
    parse_brand_profile,
)
from core.vocabulary import (
    AfAreaMode,
    BracketType,
    DriveMode,
    FocusMode,
    PhotoStyle,
    SubjectDetection,
)


# ---------------------------------------------------------------------------
# TagMapping — core translation logic
# ---------------------------------------------------------------------------

def test_tag_mapping_basic_match():
    m = TagMapping(
        exif_tag="FocusMode",
        mapping={"manual": ["MF", "Manual"], "continuous": ["AFC", "AF-C"]},
        default="unknown",
    )
    assert m.translate({"FocusMode": "AFC"}) == "continuous"
    assert m.translate({"FocusMode": "Manual"}) == "manual"
    assert m.translate({"FocusMode": "MF"}) == "manual"


def test_tag_mapping_case_insensitive():
    m = TagMapping(
        exif_tag="FocusMode",
        mapping={"continuous": ["AFC"]},
        default="unknown",
    )
    assert m.translate({"FocusMode": "afc"}) == "continuous"
    assert m.translate({"FocusMode": "AFC"}) == "continuous"
    assert m.translate({"FocusMode": "Afc"}) == "continuous"


def test_tag_mapping_substring_match():
    m = TagMapping(
        exif_tag="FocusMode",
        mapping={"continuous": ["AFC"]},
        default="unknown",
    )
    assert m.translate({"FocusMode": "Auto Focus Continuous (AFC)"}) == "continuous"


def test_tag_mapping_first_match_wins():
    m = TagMapping(
        exif_tag="FocusMode",
        mapping={"single": ["AF-S"], "continuous": ["AFS"]},
        default="unknown",
    )
    # "AF-S" substring is checked first because single is listed first
    assert m.translate({"FocusMode": "AF-S"}) == "single"


def test_tag_mapping_default_when_no_match():
    m = TagMapping(
        exif_tag="FocusMode",
        mapping={"manual": ["MF"]},
        default="unknown",
    )
    assert m.translate({"FocusMode": "SomethingWeird"}) == "unknown"
    assert m.translate({}) == "unknown"
    assert m.translate({"FocusMode": ""}) == "unknown"
    assert m.translate({"FocusMode": None}) == "unknown"


def test_tag_mapping_alternative_tags():
    m = TagMapping(
        exif_tag="FocusMode",
        exif_tag_alternatives=["FocusModeFallback"],
        mapping={"manual": ["MF"]},
        default="unknown",
    )
    # Primary empty, alternative populated
    assert m.translate({"FocusMode": "", "FocusModeFallback": "MF"}) == "manual"
    # Primary populated, alternative ignored
    assert m.translate({"FocusMode": "MF", "FocusModeFallback": ""}) == "manual"


# ---------------------------------------------------------------------------
# BracketRule
# ---------------------------------------------------------------------------

def test_bracket_rule_value_greater_than_zero():
    rule = BracketRule(exif_tag="FocusBracket", is_active_when="value > 0")
    assert rule.is_active({"FocusBracket": 3}) is True
    assert rule.is_active({"FocusBracket": "5"}) is True
    assert rule.is_active({"FocusBracket": 0}) is False
    assert rule.is_active({"FocusBracket": ""}) is False
    assert rule.is_active({}) is False


def test_bracket_rule_active_values_substring():
    rule = BracketRule(
        exif_tag="DriveMode",
        active_values=["Bracketing", "AE Bracket"],
    )
    assert rule.is_active({"DriveMode": "Continuous Bracketing"}) is True
    assert rule.is_active({"DriveMode": "AE Bracket Low"}) is True
    assert rule.is_active({"DriveMode": "Single Frame"}) is False
    assert rule.is_active({"DriveMode": ""}) is False


def test_bracket_rule_missing_tag_is_inactive():
    rule = BracketRule(exif_tag="Bracketing", active_values=["AEB"])
    assert rule.is_active({"OtherTag": "AEB"}) is False


# ---------------------------------------------------------------------------
# LensNormalization
# ---------------------------------------------------------------------------

def test_lens_normalization_alias_match():
    norm = LensNormalization(
        aliases=[
            LensAlias(canonical="Leica DG 100-400", matches=["LUMIX G VARIO 100-400"]),
            LensAlias(
                canonical="Olympus 60mm Macro",
                matches=["M.ZUIKO DIGITAL ED 60mm"],
            ),
        ]
    )
    assert norm.canonicalize("LUMIX G VARIO 100-400/F4.0-6.3") == "Leica DG 100-400"
    assert norm.canonicalize("M.ZUIKO DIGITAL ED 60mm f/2.8") == "Olympus 60mm Macro"


def test_lens_normalization_no_match_returns_raw():
    norm = LensNormalization(aliases=[])
    assert norm.canonicalize("Unknown Lens 42mm") == "Unknown Lens 42mm"


def test_lens_normalization_empty_input():
    norm = LensNormalization()
    assert norm.canonicalize("") == ""


# ---------------------------------------------------------------------------
# LensNormalization.read_raw_lens — multi-tag fallback chain
# ---------------------------------------------------------------------------

def test_read_raw_lens_primary_tag():
    norm = LensNormalization(
        lens_model_tag="LensModel",
        lens_model_tag_alternatives=["LensType", "LensID"],
    )
    exif = {"LensModel": "Leica DG 100-400"}
    assert norm.read_raw_lens(exif) == "Leica DG 100-400"


def test_read_raw_lens_falls_back_to_alternatives():
    """This is the G9I fix: LensModel is empty but LensType has the info."""
    norm = LensNormalization(
        lens_model_tag="LensModel",
        lens_model_tag_alternatives=["LensType", "LensID"],
    )
    exif = {
        "LensModel": "",  # empty
        "LensType": "Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
        "LensID": "Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
    }
    assert norm.read_raw_lens(exif) == "Olympus M.Zuiko Digital ED 60mm F2.8 Macro"


def test_read_raw_lens_uses_first_non_empty():
    """Primary wins even when alternatives are also populated."""
    norm = LensNormalization(
        lens_model_tag="LensModel",
        lens_model_tag_alternatives=["LensType"],
    )
    exif = {
        "LensModel": "From LensModel",
        "LensType": "From LensType",
    }
    assert norm.read_raw_lens(exif) == "From LensModel"


def test_read_raw_lens_walks_all_alternatives_in_order():
    norm = LensNormalization(
        lens_model_tag="LensModel",
        lens_model_tag_alternatives=["LensType", "LensID"],
    )
    # LensModel and LensType empty, only LensID populated
    exif = {
        "LensModel": "",
        "LensType": "",
        "LensID": "Fallback Lens",
    }
    assert norm.read_raw_lens(exif) == "Fallback Lens"


def test_read_raw_lens_returns_empty_when_all_tags_missing():
    norm = LensNormalization(
        lens_model_tag="LensModel",
        lens_model_tag_alternatives=["LensType", "LensID"],
    )
    assert norm.read_raw_lens({}) == ""


def test_read_raw_lens_returns_empty_when_all_tags_blank():
    norm = LensNormalization(
        lens_model_tag="LensModel",
        lens_model_tag_alternatives=["LensType"],
    )
    exif = {"LensModel": "   ", "LensType": ""}
    assert norm.read_raw_lens(exif) == ""


def test_read_raw_lens_strips_whitespace():
    norm = LensNormalization(lens_model_tag="LensModel")
    exif = {"LensModel": "  Leica DG 100-400  "}
    assert norm.read_raw_lens(exif) == "Leica DG 100-400"


def test_read_raw_lens_no_alternatives_single_tag():
    norm = LensNormalization(lens_model_tag="LensModel")
    exif = {"LensModel": "Only lens"}
    assert norm.read_raw_lens(exif) == "Only lens"


def test_canonicalize_lens_uses_multi_tag_chain():
    """End-to-end: BrandProfile.canonicalize_lens should use the multi-tag
    fallback via read_raw_lens, then apply aliases."""
    profile = BrandProfile(
        brand_id="test",
        display_name="Test",
        version=1,
        exiftool_make_match=["Panasonic"],
        focus_mode=TagMapping(exif_tag="F", mapping={}, default="unknown"),
        af_area_mode=TagMapping(exif_tag="A", mapping={}, default="unknown"),
        subject_detection=TagMapping(exif_tag="S", mapping={}, default="none"),
        drive_mode=TagMapping(exif_tag="D", mapping={}, default="single"),
        lens_normalization=LensNormalization(
            lens_model_tag="LensModel",
            lens_model_tag_alternatives=["LensType"],
            aliases=[
                LensAlias(
                    canonical="Olympus 60mm Macro",
                    matches=["M.Zuiko Digital ED 60mm"],
                )
            ],
        ),
    )
    # G9I-style EXIF: LensModel empty, LensType has the info
    exif = {
        "LensModel": "",
        "LensType": "Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
    }
    assert profile.canonicalize_lens(exif) == "Olympus 60mm Macro"


def test_canonicalize_lens_returns_empty_when_no_lens_in_any_tag():
    profile = BrandProfile(
        brand_id="test",
        display_name="Test",
        version=1,
        exiftool_make_match=["Panasonic"],
        focus_mode=TagMapping(exif_tag="F", mapping={}, default="unknown"),
        af_area_mode=TagMapping(exif_tag="A", mapping={}, default="unknown"),
        subject_detection=TagMapping(exif_tag="S", mapping={}, default="none"),
        drive_mode=TagMapping(exif_tag="D", mapping={}, default="single"),
        lens_normalization=LensNormalization(
            lens_model_tag="LensModel",
            lens_model_tag_alternatives=["LensType", "LensID"],
        ),
    )
    # All lens tags missing
    exif = {"Make": "Panasonic", "Model": "DC-G9"}
    assert profile.canonicalize_lens(exif) == ""


def test_panasonic_profile_has_lens_fallback_for_g9_olympus():
    """Regression test for the G9I bug: built-in panasonic.json must include
    LensType/LensID as fallbacks so third-party lenses on older bodies are
    still canonicalized."""
    p = load_brand_profile("panasonic")
    assert "LensType" in p.lens_normalization.lens_model_tag_alternatives
    assert "LensID" in p.lens_normalization.lens_model_tag_alternatives

    # Simulate the exact G9I EXIF shape
    exif = {
        "Make": "Panasonic",
        "Model": "DC-G9",
        "LensModel": "",
        "LensType": "Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
        "LensID": "Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
    }
    canonical = p.canonicalize_lens(exif)
    # Should canonicalize to the Olympus 60mm alias
    assert "60mm" in canonical.lower()
    assert canonical != ""  # not empty like before the fix


def test_panasonic_focus_bracket_detected_via_burstmode(qapp=None):
    """Regression test for Nelson 2026-06-04 (D:\\teste, D:\\Photos\\.../stack):
    G9 (DC-G9) and G9 II (DC-G9M2) both write ``BurstMode = "Focus Bracketing"``
    on every frame of a focus bracket sequence, AND the first frame's
    ``FocusBracket`` value is 0 (sequence index, not a flag). The old rule
    ``FocusBracket > 0`` was missing the first frame of every sequence;
    switching to ``BurstMode == "Focus Bracketing"`` catches all frames.

    Sample EXIF taken verbatim from the user's RW2 files.
    """
    p = load_brand_profile("panasonic")
    assert p.focus_bracket is not None

    # G9 II first frame of a focus bracket (the case the old rule missed).
    first_frame = {
        "Make": "Panasonic", "Model": "DC-G9M2",
        "BurstMode": "Focus Bracketing",
        "FocusBracket": 0, "BracketSettings": "No Bracket",
        "SequenceNumber": 1,
    }
    assert p.detect_bracket(first_frame) == BracketType.FOCUS

    # G9 (original) mid-sequence frame.
    mid_frame = {
        "Make": "Panasonic", "Model": "DC-G9",
        "BurstMode": "Focus Bracketing",
        "FocusBracket": 5, "BracketSettings": "No Bracket",
        "SequenceNumber": 6,
    }
    assert p.detect_bracket(mid_frame) == BracketType.FOCUS

    # Non-bracket frame still reads as NONE.
    plain = {
        "Make": "Panasonic", "Model": "DC-G9M2",
        "BurstMode": "Off",
        "FocusBracket": 0, "BracketSettings": "No Bracket",
        "SequenceNumber": 0,
    }
    assert p.detect_bracket(plain) == BracketType.NONE

    # Exposure bracket detection (BurstMode = "Auto Exposure Bracketing (AEB)")
    # is unchanged — the focus rule must not steal AEB frames.
    aeb = {
        "Make": "Panasonic", "Model": "DC-G9M2",
        "BurstMode": "Auto Exposure Bracketing (AEB)",
        "FocusBracket": 0, "BracketSettings": "No Bracket",
    }
    assert p.detect_bracket(aeb) == BracketType.EXPOSURE


# ---------------------------------------------------------------------------
# BrandProfile — make matching and end-to-end translation
# ---------------------------------------------------------------------------

def _minimal_profile() -> BrandProfile:
    return BrandProfile(
        brand_id="test",
        display_name="Test",
        version=1,
        exiftool_make_match=["Panasonic"],
        focus_mode=TagMapping(
            exif_tag="FocusMode",
            mapping={"continuous": ["AFC"]},
            default="unknown",
        ),
        af_area_mode=TagMapping(
            exif_tag="AFAreaMode",
            mapping={"single_point": ["1-Area"]},
            default="unknown",
        ),
        subject_detection=TagMapping(
            exif_tag="AFSubjectDetection",
            mapping={"bird": ["Bird"]},
            default="none",
        ),
        drive_mode=TagMapping(
            exif_tag="BurstMode",
            mapping={"burst_high": ["H"]},
            default="single",
        ),
        focus_bracket=BracketRule(exif_tag="FocusBracket", is_active_when="value > 0"),
    )


def test_matches_make_case_insensitive():
    p = _minimal_profile()
    assert p.matches_make("PANASONIC") is True
    assert p.matches_make("panasonic") is True
    assert p.matches_make("Panasonic Corporation") is True
    assert p.matches_make("Sony") is False
    assert p.matches_make("") is False


def test_translate_enums():
    p = _minimal_profile()
    exif = {
        "FocusMode": "AFC",
        "AFAreaMode": "1-Area",
        "AFSubjectDetection": "Bird",
        "BurstMode": "H",
    }
    assert p.translate_focus_mode(exif) == FocusMode.CONTINUOUS
    assert p.translate_af_area_mode(exif) == AfAreaMode.SINGLE_POINT
    assert p.translate_subject_detection(exif) == SubjectDetection.BIRD
    assert p.translate_drive_mode(exif) == DriveMode.BURST_HIGH


def test_detect_bracket():
    p = _minimal_profile()
    assert p.detect_bracket({"FocusBracket": 5}) == BracketType.FOCUS
    assert p.detect_bracket({"FocusBracket": 0}) == BracketType.NONE
    assert p.detect_bracket({}) == BracketType.NONE


# ---------------------------------------------------------------------------
# parse_brand_profile — JSON shape tolerance
# ---------------------------------------------------------------------------

def test_parse_minimal_json():
    data = {
        "brand_id": "test",
        "exiftool_make_match": ["Test"],
        "focus_mode": {
            "exif_tag": "FocusMode",
            "mapping": {"manual": ["MF"]},
            "default": "unknown",
        },
        "af_area_mode": {"exif_tag": "AFAreaMode", "mapping": {}, "default": "unknown"},
        "subject_detection": {"exif_tag": "AFSubjectDetection", "mapping": {}, "default": "none"},
        "drive_mode": {"exif_tag": "BurstMode", "mapping": {}, "default": "single"},
    }
    p = parse_brand_profile(data)
    assert p.brand_id == "test"
    assert p.display_name == "test"  # falls back to brand_id
    assert p.version == 1
    assert p.focus_bracket is None  # no bracket_detection block


def test_parse_with_bracket_detection():
    data = {
        "brand_id": "test",
        "exiftool_make_match": ["Test"],
        "focus_mode": {"exif_tag": "F", "mapping": {}, "default": "unknown"},
        "af_area_mode": {"exif_tag": "A", "mapping": {}, "default": "unknown"},
        "subject_detection": {"exif_tag": "S", "mapping": {}, "default": "none"},
        "drive_mode": {"exif_tag": "D", "mapping": {}, "default": "single"},
        "bracket_detection": {
            "focus_bracket": {"exif_tag": "FB", "is_active_when": "value > 0"},
            "exposure_bracket": {"exif_tag": "EB", "active_values": ["AEB"]},
        },
    }
    p = parse_brand_profile(data)
    assert p.focus_bracket is not None
    assert p.focus_bracket.exif_tag == "FB"
    assert p.exposure_bracket is not None
    assert p.exposure_bracket.active_values == ["AEB"]


# ---------------------------------------------------------------------------
# Built-in profiles — smoke tests against real JSON files
# ---------------------------------------------------------------------------

def test_panasonic_profile_loads():
    p = load_brand_profile("panasonic")
    assert p.brand_id == "panasonic"
    assert p.matches_make("Panasonic") is True
    assert p.focus_bracket is not None
    assert p.exposure_bracket is not None


def test_sony_profile_loads():
    p = load_brand_profile("sony")
    assert p.brand_id == "sony"
    assert p.matches_make("SONY") is True
    assert p.focus_bracket is not None


def test_panasonic_detects_exposure_bracket_via_burst_mode():
    """Regression for the 2026-04-29 finding: the G9 II writes the
    exposure-bracket signal to the ``BurstMode`` MakerNotes tag (value
    ``Auto Exposure Bracketing (AEB)``), NOT to a tag literally named
    ``Bracketing``. The brand profile previously read the wrong tag
    so brackets were never detected — Nelson's Costa Rica trip had at
    least one AEB sequence misclassified as ``orphans``.
    Confirmed empirically with H:\\DCIM\\148_PANA samples."""
    p = load_brand_profile("panasonic")
    # Real G9 II AEB EXIF — BurstMode carries the bracket signal,
    # BracketSettings carries the descriptive program info.
    aeb_exif = {
        "BurstMode": "Auto Exposure Bracketing (AEB)",
        "BracketSettings": "7 Images, Sequence 0/-/+",
        "FocusBracket": 0,
    }
    assert p.detect_bracket(aeb_exif) == BracketType.EXPOSURE

    # Normal burst (not AEB) must NOT trigger exposure bracket detection
    normal_burst_exif = {
        "BurstMode": "On",
        "BurstSpeed": 14,
        "FocusBracket": 0,
    }
    assert p.detect_bracket(normal_burst_exif) == BracketType.NONE

    # Bracket Off
    off_exif = {
        "BurstMode": "Off",
        "BracketSettings": "No Bracket",
        "FocusBracket": 0,
    }
    assert p.detect_bracket(off_exif) == BracketType.NONE


def test_panasonic_g9ii_burst_mode_on_is_continuous():
    """Regression for the 2026-05-16 misclassified-wildlife finding:
    the G9 II writes a plain ``BurstMode = "On"`` (the rate lives in
    ``BurstSpeed``, here 14 fps) when continuous high-speed burst is
    engaged. The Panasonic ``drive_mode`` mapping only had
    Low/High/SH from older bodies, so ``"On"`` matched nothing and
    fell to the ``single`` default — a 14-fps wildlife burst read as
    single-shot, breaking burst clustering. Confirmed empirically
    with the 17-frame manual-focus 100-400 series in
    ``D:\\Photos\\Para teste\\misclassified``.

    Note (design): the *classification* of such a series stays
    ``general``/flagged-for-review on purpose — "Settings beat lens"
    is frozen (docs/18 §Genre), and with subject-detection off there
    is no reliable wildlife signal. This test only pins the
    brand-EXIF interpretation, not a wildlife rule."""
    p = load_brand_profile("panasonic")
    g9ii_burst = {"BurstMode": "On", "BurstSpeed": 14, "FocusMode": "Manual"}
    assert p.translate_drive_mode(g9ii_burst) == DriveMode.BURST_HIGH
    assert p.is_continuous_shooting(g9ii_burst) is True
    # The existing Low/High/SH/Off entries must be unaffected.
    assert p.translate_drive_mode({"BurstMode": "Off"}) == DriveMode.SINGLE
    assert p.translate_drive_mode({"BurstMode": "Low"}) == DriveMode.BURST_LOW
    assert p.translate_drive_mode({"BurstMode": "SH"}) == DriveMode.BURST_HIGH


def test_gopro_profile_loads_and_matches_make():
    """The GoPro brand was added 2026-04-29 after Costa Rica field
    test surfaced an Unknown body warning for ``GoPro HERO12 Black``.
    Profile is intentionally minimal (no PhotoStyle, no AI subject
    detection, fixed focus) — verify it loads and matches the EXIF
    Make string the camera writes."""
    p = load_brand_profile("gopro")
    assert p.brand_id == "gopro"
    assert p.matches_make("GoPro") is True
    # No bracket detection — GoPro has no in-sensor brackets
    assert p.focus_bracket is None or p.focus_bracket.exif_tag == ""
    assert p.exposure_bracket is None or p.exposure_bracket.exif_tag == ""


def test_panasonic_focus_mode_translations():
    p = load_brand_profile("panasonic")
    assert p.translate_focus_mode({"FocusMode": "AFC"}) == FocusMode.CONTINUOUS
    assert p.translate_focus_mode({"FocusMode": "AFS"}) == FocusMode.SINGLE
    assert p.translate_focus_mode({"FocusMode": "MF"}) == FocusMode.MANUAL


def test_sony_focus_mode_translations():
    p = load_brand_profile("sony")
    assert p.translate_focus_mode({"FocusMode": "AF-C"}) == FocusMode.CONTINUOUS
    assert p.translate_focus_mode({"FocusMode": "AF-S"}) == FocusMode.SINGLE
    assert p.translate_focus_mode({"FocusMode": "Manual"}) == FocusMode.MANUAL


def test_panasonic_lens_canonicalization():
    p = load_brand_profile("panasonic")
    canonical = p.canonicalize_lens({"LensModel": "LUMIX G VARIO 100-400/F4.0-6.3"})
    assert canonical == "Leica DG 100-400"


def test_list_available_profiles_contains_both():
    profiles = list_available_brand_profiles()
    assert "panasonic" in profiles
    assert "sony" in profiles


def test_match_profile_for_photo_panasonic():
    exif = {"Make": "Panasonic", "Model": "DC-G9M2"}
    p = match_brand_profile_for_photo(exif)
    assert p is not None
    assert p.brand_id == "panasonic"


def test_match_profile_for_photo_sony():
    exif = {"Make": "SONY", "Model": "ILCE-6700"}
    p = match_brand_profile_for_photo(exif)
    assert p is not None
    assert p.brand_id == "sony"


def test_match_profile_for_photo_unknown():
    exif = {"Make": "Hasselblad", "Model": "X2D"}
    assert match_brand_profile_for_photo(exif) is None


def test_match_profile_for_photo_missing_make():
    assert match_brand_profile_for_photo({}) is None


# ---------------------------------------------------------------------------
# User override merge
# ---------------------------------------------------------------------------

def test_user_override_adds_mapping_entry(tmp_path, monkeypatch):
    """User overrides should be able to add new mapping substrings without
    rewriting the whole block."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    user_dir = tmp_path / "brand_profiles"
    user_dir.mkdir(parents=True)

    override = {
        "focus_mode": {
            "mapping": {
                "continuous": ["AFF-new-firmware-value"]
            }
        }
    }
    (user_dir / "panasonic.json").write_text(json.dumps(override), encoding="utf-8")

    p = load_brand_profile("panasonic")
    # Original mappings still work
    assert p.translate_focus_mode({"FocusMode": "AFC"}) == FocusMode.CONTINUOUS
    # New mapping added by user override also works
    assert p.translate_focus_mode({"FocusMode": "AFF-new-firmware-value"}) == FocusMode.CONTINUOUS


def test_user_override_wholesale_replacement_of_unknown_key(tmp_path, monkeypatch):
    """A top-level key in the override that doesn't exist in base is added."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    user_dir = tmp_path / "brand_profiles"
    user_dir.mkdir(parents=True)

    override = {"display_name": "Panasonic (customized)"}
    (user_dir / "panasonic.json").write_text(json.dumps(override), encoding="utf-8")

    p = load_brand_profile("panasonic")
    assert p.display_name == "Panasonic (customized)"


def test_load_nonexistent_profile_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        load_brand_profile("fujifilm")


# ---------------------------------------------------------------------------
# PhotoStyle translation
# ---------------------------------------------------------------------------

def test_panasonic_translate_photo_style_natural():
    p = load_brand_profile("panasonic")
    assert p.translate_photo_style({"PhotoStyle": "Natural"}) == PhotoStyle.NATURAL


def test_panasonic_translate_photo_style_portrait():
    p = load_brand_profile("panasonic")
    assert p.translate_photo_style({"PhotoStyle": "Portrait"}) == PhotoStyle.PORTRAIT


def test_panasonic_translate_photo_style_scenery():
    p = load_brand_profile("panasonic")
    assert p.translate_photo_style({"PhotoStyle": "Scenery"}) == PhotoStyle.SCENERY


def test_panasonic_translate_photo_style_standard():
    p = load_brand_profile("panasonic")
    assert p.translate_photo_style({"PhotoStyle": "Standard"}) == PhotoStyle.STANDARD


def test_panasonic_translate_photo_style_vivid():
    p = load_brand_profile("panasonic")
    assert p.translate_photo_style({"PhotoStyle": "Vivid"}) == PhotoStyle.VIVID


def test_panasonic_translate_photo_style_monochrome_variants():
    p = load_brand_profile("panasonic")
    assert p.translate_photo_style({"PhotoStyle": "Monochrome"}) == PhotoStyle.MONOCHROME
    assert p.translate_photo_style({"PhotoStyle": "L.Monochrome"}) == PhotoStyle.MONOCHROME


def test_panasonic_translate_photo_style_missing_returns_unknown():
    p = load_brand_profile("panasonic")
    assert p.translate_photo_style({}) == PhotoStyle.UNKNOWN
    assert p.translate_photo_style({"PhotoStyle": ""}) == PhotoStyle.UNKNOWN


def test_sony_translate_photo_style_creative_look_portrait():
    """A6700 uses CreativeLook; 'PT' is Sony's short code for Portrait."""
    p = load_brand_profile("sony")
    assert p.translate_photo_style({"CreativeLook": "PT"}) == PhotoStyle.PORTRAIT


def test_sony_translate_photo_style_creative_style_landscape_fallback():
    """Older Sony bodies use CreativeStyle. Should be read via the
    exif_tag_alternatives chain when CreativeLook is absent."""
    p = load_brand_profile("sony")
    result = p.translate_photo_style({
        "CreativeLook": "",
        "CreativeStyle": "Landscape",
    })
    assert result == PhotoStyle.SCENERY


def test_sony_translate_photo_style_off_maps_to_unknown():
    """Sony's 'Off' value (seen in A6700 samples) means the user is using
    Picture Profiles instead — we treat it as unknown so future rules
    don't incorrectly match."""
    p = load_brand_profile("sony")
    assert p.translate_photo_style({"CreativeStyle": "Off"}) == PhotoStyle.UNKNOWN


def test_sony_translate_photo_style_vivid_variants():
    p = load_brand_profile("sony")
    assert p.translate_photo_style({"CreativeLook": "VV"}) == PhotoStyle.VIVID
    assert p.translate_photo_style({"CreativeLook": "VV2"}) == PhotoStyle.VIVID
    assert p.translate_photo_style({"CreativeLook": "Vivid"}) == PhotoStyle.VIVID


def test_profile_without_photo_style_tag_returns_unknown():
    """A minimal brand profile that doesn't declare photo_style should
    still work and return UNKNOWN gracefully."""
    p = BrandProfile(
        brand_id="test",
        display_name="Test",
        version=1,
        exiftool_make_match=["Test"],
        focus_mode=TagMapping(exif_tag="F", mapping={}, default="unknown"),
        af_area_mode=TagMapping(exif_tag="A", mapping={}, default="unknown"),
        subject_detection=TagMapping(exif_tag="S", mapping={}, default="none"),
        drive_mode=TagMapping(exif_tag="D", mapping={}, default="single"),
        # photo_style omitted — uses default empty TagMapping
    )
    assert p.translate_photo_style({"PhotoStyle": "Portrait"}) == PhotoStyle.UNKNOWN


# ---------------------------------------------------------------------------
# Apple (iPhone) brand profile
# ---------------------------------------------------------------------------

def test_apple_loads():
    p = load_brand_profile("apple")
    assert p.brand_id == "apple"
    assert "Apple" in p.exiftool_make_match


def test_apple_subject_detection_xmp_face_region_human():
    """iPhone face detection: XMP RegionType is a list like ['Face', 'Face'].
    The brand profile should map this to SubjectDetection.HUMAN — the
    primary signal driving Tier 1 portrait classification on phone photos."""
    p = load_brand_profile("apple")
    exif = {"RegionType": ["Face", "Face"]}
    assert p.translate_subject_detection(exif) == SubjectDetection.HUMAN


def test_apple_subject_detection_no_region_none():
    """Empty / missing RegionType → SubjectDetection.NONE (lets Tier 2
    or Tier 3 fallback decide instead of falsely firing portrait)."""
    p = load_brand_profile("apple")
    assert p.translate_subject_detection({}) == SubjectDetection.NONE
    assert p.translate_subject_detection({"RegionType": []}) == SubjectDetection.NONE


def test_apple_matches_make():
    """An iPhone photo (Make=Apple) should be picked up by
    match_brand_profile_for_photo, ensuring the iPhone routing kicks in
    for any phone-source classification."""
    from core.brand_profile import match_brand_profile_for_photo
    profile = match_brand_profile_for_photo({"Make": "Apple"})
    assert profile is not None
    assert profile.brand_id == "apple"


# ---------------------------------------------------------------------------
# FocusPositionRule — normalized focus position [0, 1]
# ---------------------------------------------------------------------------


def test_focus_position_rule_step_ratio_panasonic_macro():
    """Panasonic G9: FocusStepNear=0 / FocusStepCount=46 → 0.0
    (focused at minimum focus distance, i.e. macro range)."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="step_ratio", near_tag="FocusStepNear", count_tag="FocusStepCount",
    )
    assert rule.compute({"FocusStepNear": 0, "FocusStepCount": 46}) == 0.0


def test_focus_position_rule_step_ratio_panasonic_infinity():
    """FocusStepNear == FocusStepCount → 1.0 (focused at infinity)."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="step_ratio", near_tag="FocusStepNear", count_tag="FocusStepCount",
    )
    assert rule.compute({"FocusStepNear": 46, "FocusStepCount": 46}) == 1.0


def test_focus_position_rule_step_ratio_mid_range():
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="step_ratio", near_tag="FocusStepNear", count_tag="FocusStepCount",
    )
    assert rule.compute({"FocusStepNear": 23, "FocusStepCount": 46}) == 0.5


def test_focus_position_rule_step_ratio_missing_tags_returns_none():
    """When the camera doesn't write the configured tags (e.g. LRC-
    exported JPGs with stripped maker notes), compute returns None and
    the rule layer's predicate simply doesn't fire."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="step_ratio", near_tag="FocusStepNear", count_tag="FocusStepCount",
    )
    assert rule.compute({}) is None
    assert rule.compute({"FocusStepNear": 0}) is None
    assert rule.compute({"FocusStepCount": 46}) is None
    assert rule.compute({"FocusStepNear": "abc", "FocusStepCount": 46}) is None


def test_focus_position_rule_step_ratio_clamps_out_of_range():
    """Defensive: some firmware writes step values slightly outside
    [0, count] due to motor calibration. Result still clamps to [0, 1]."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="step_ratio", near_tag="FocusStepNear", count_tag="FocusStepCount",
    )
    assert rule.compute({"FocusStepNear": -2, "FocusStepCount": 46}) == 0.0
    assert rule.compute({"FocusStepNear": 50, "FocusStepCount": 46}) == 1.0


def test_focus_position_rule_meters_scaled():
    """Sony/Canon-style: SubjectDistance in metres, normalized over
    max_meters (default 5m)."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="meters_scaled", meters_tag="SubjectDistance", max_meters=5.0,
    )
    assert rule.compute({"SubjectDistance": 0.3}) == pytest.approx(0.06)
    assert rule.compute({"SubjectDistance": 2.5}) == 0.5
    assert rule.compute({"SubjectDistance": 5.0}) == 1.0
    assert rule.compute({"SubjectDistance": 100.0}) == 1.0  # clamped
    assert rule.compute({}) is None
    assert rule.compute({"SubjectDistance": "0.5 m"}) == pytest.approx(0.1)


def test_focus_position_rule_subject_distance_range():
    """Standard EXIF 2.x enum: Unknown / Macro / Close / Distant."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="subject_distance_range", range_tag="SubjectDistanceRange",
    )
    assert rule.compute({"SubjectDistanceRange": "Macro"}) == 0.0
    assert rule.compute({"SubjectDistanceRange": "Close view"}) == 0.5
    assert rule.compute({"SubjectDistanceRange": "Distant view"}) == 0.9
    assert rule.compute({"SubjectDistanceRange": "Unknown"}) is None
    assert rule.compute({}) is None


def test_brand_profile_focus_position_normalized_panasonic_macro():
    """End-to-end: load Panasonic profile and verify the
    focus_position_normalized method reads FocusStepNear / FocusStepCount."""
    p = load_brand_profile("panasonic")
    assert p.focus_position is not None
    assert p.focus_position_normalized({"FocusStepNear": 0, "FocusStepCount": 46}) == 0.0
    # No EXIF -> None (rule layer handles by not firing)
    assert p.focus_position_normalized({}) is None


def test_brand_profile_focus_position_normalized_none_for_brands_without_config():
    """Brands that don't declare focus_position return None — the
    rule layer queries the normalized field and simply doesn't fire."""
    for brand_id in ("sony", "apple", "gopro"):
        p = load_brand_profile(brand_id)
        assert p.focus_position_normalized({"FocusStepNear": 0, "FocusStepCount": 46}) is None, (
            f"{brand_id}: expected None until its profile declares focus_position"
        )


def test_brand_profile_is_close_focus_convenience():
    p = load_brand_profile("panasonic")
    assert p.is_close_focus({"FocusStepNear": 0, "FocusStepCount": 46}) is True
    assert p.is_close_focus({"FocusStepNear": 23, "FocusStepCount": 46}) is False
    # Custom threshold
    assert p.is_close_focus(
        {"FocusStepNear": 5, "FocusStepCount": 46}, threshold=0.2,
    ) is True
    # Missing EXIF → False, not exception
    assert p.is_close_focus({}) is False


# ---------------------------------------------------------------------------
# meters_range — Apple FocusDistanceRange parsing
# ---------------------------------------------------------------------------


def test_focus_position_meters_range_apple_macro_upper_bound():
    """Apple iPhone macro shot — FocusDistanceRange='0.12 - 0.16 m',
    upper bound 0.16m. Apple uses bound='upper' to separate genuine
    tight-DOF macros from hyperfocal landscapes whose lower bound is
    close but upper bound is far."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="meters_range", meters_tag="FocusDistanceRange",
        bound="upper", max_meters=1.5,
    )
    result = rule.compute({"FocusDistanceRange": "0.12 - 0.16 m"})
    # 0.16 / 1.5 = 0.107 — fires macro (< 0.2)
    assert 0.10 < result < 0.12


def test_focus_position_meters_range_apple_landscape_does_not_misfire_macro():
    """Critical regression: an iPhone landscape with FocusDistanceRange
    '0.23 - 1.90 m' (close lower bound but hyperfocal upper) MUST NOT
    look like macro. Using bound='upper' makes 1.90m / 1.5 clamp to
    1.0, well above the 0.2 macro threshold. Real failure mode caught
    on IMG_5810.HEIC 2026-05-13."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="meters_range", meters_tag="FocusDistanceRange",
        bound="upper", max_meters=1.5,
    )
    result = rule.compute({"FocusDistanceRange": "0.23 - 1.90 m"})
    # 1.90 clamped to max_meters=1.5 → 1.0
    assert result == 1.0


def test_focus_position_meters_range_close_portrait_doesnt_misfire_macro():
    """A close-portrait at 0.49-0.56m must not fire the macro rule.
    Upper bound 0.56 / 1.5 = 0.373 > 0.2 threshold. Real case from
    IMG_5809.HEIC, identified by Nelson as portrait, not macro."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="meters_range", meters_tag="FocusDistanceRange",
        bound="upper", max_meters=1.5,
    )
    result = rule.compute({"FocusDistanceRange": "0.49 - 0.56 m"})
    assert 0.35 < result < 0.40


def test_focus_position_meters_range_lower_bound_kind():
    """bound='lower' picks the closest-plane end of the range."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="meters_range", meters_tag="FocusDistanceRange",
        bound="lower", max_meters=5.0,
    )
    result = rule.compute({"FocusDistanceRange": "0.12 - 0.16 m"})
    assert 0.02 < result < 0.03   # 0.12 / 5.0


def test_focus_position_meters_range_midpoint():
    """bound='midpoint' averages the two ends."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="meters_range", meters_tag="FocusDistanceRange",
        bound="midpoint", max_meters=5.0,
    )
    # midpoint(1.59, 6.65) = 4.12 → 4.12 / 5.0 = 0.824
    result = rule.compute({"FocusDistanceRange": "1.59 - 6.65 m"})
    assert 0.80 < result < 0.84


def test_focus_position_meters_range_missing_returns_none():
    """Front-camera selfies don't write FocusDistanceRange — None."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="meters_range", meters_tag="FocusDistanceRange",
        bound="upper", max_meters=1.5,
    )
    assert rule.compute({}) is None
    assert rule.compute({"FocusDistanceRange": ""}) is None


def test_focus_position_meters_range_single_scalar_falls_back():
    """If the brand writes a single scalar instead of a range, treat
    it as the value directly."""
    from core.brand_profile import FocusPositionRule
    rule = FocusPositionRule(
        kind="meters_range", meters_tag="FocusDistanceRange",
        bound="upper", max_meters=1.5,
    )
    assert rule.compute({"FocusDistanceRange": "0.5 m"}) == pytest.approx(1/3)


def test_apple_profile_loads_focus_position():
    """End-to-end: apple.json declares focus_position; the brand
    profile picks up the upper-bound + 1.5m-max config."""
    p = load_brand_profile("apple")
    assert p.focus_position is not None
    assert p.focus_position.kind == "meters_range"
    assert p.focus_position.bound == "upper"
    assert p.focus_position.max_meters == 1.5


def test_apple_focus_position_real_macro_exif():
    """Apple profile on a real macro EXIF: focus_position fires < 0.2."""
    p = load_brand_profile("apple")
    pos = p.focus_position_normalized({"FocusDistanceRange": "0.12 - 0.16 m"})
    assert pos is not None and pos < 0.2


def test_apple_focus_position_real_landscape_exif():
    """Apple profile on a real landscape EXIF: focus_position clamps
    to 1.0 (no macro misfire)."""
    p = load_brand_profile("apple")
    pos = p.focus_position_normalized({"FocusDistanceRange": "0.23 - 1.90 m"})
    assert pos == 1.0
