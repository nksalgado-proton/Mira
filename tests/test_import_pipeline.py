"""Tests for the import pipeline — the integration layer that ties Phase A
modules together for a real batch import."""

from datetime import datetime
from pathlib import Path

import pytest

from core.bracket_detector import DetectorConfig
from core.classifier_v2 import ClassificationResult, Rule, RuleSet
from core.import_pipeline import (
    ImportResult,
    RawExifEntry,
    _build_bracket_candidate,
    _build_photo_context,
    _parse_aperture,
    _parse_bool,
    _parse_float,
    _parse_focal_length,
    _parse_focus_distance,
    _parse_int,
    _parse_timestamp,
    _resolve_body_profile,
    classify_imported_batch,
)
from core.lens_registry import LensEntry, LensRegistry
from core.vocabulary import (
    AfAreaMode,
    BracketType,
    DriveMode,
    FocusMode,
    Scenario,
    SubjectDetection,
)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def test_parse_float_simple():
    assert _parse_float(6.3) == 6.3
    assert _parse_float("6.3") == 6.3
    assert _parse_float(7) == 7.0


def test_parse_float_fraction():
    assert _parse_float("1/2000") == pytest.approx(0.0005)
    assert _parse_float("1/500") == pytest.approx(0.002)


def test_parse_float_empty_and_none():
    assert _parse_float(None) == 0.0
    assert _parse_float("") == 0.0
    assert _parse_float("   ") == 0.0


def test_parse_float_invalid():
    assert _parse_float("abc") == 0.0
    assert _parse_float("1/0") == 0.0


def test_parse_int_various():
    assert _parse_int(400) == 400
    assert _parse_int("400") == 400
    assert _parse_int("ISO 400") == 0  # first token "ISO" is not an int
    assert _parse_int("400 ") == 400
    assert _parse_int(None) == 0
    assert _parse_int("") == 0


def test_parse_bool_flash_variants():
    assert _parse_bool("Fired") is True
    assert _parse_bool("Flash fired") is True
    assert _parse_bool("Off, Did not fire") is False
    assert _parse_bool("Off") is False
    assert _parse_bool("No flash function") is False
    assert _parse_bool(None) is False
    assert _parse_bool("") is False
    assert _parse_bool(True) is True
    assert _parse_bool(0) is False
    assert _parse_bool(1) is True


def test_parse_timestamp_exiftool_standard():
    result = _parse_timestamp("2026:04:15 10:30:00")
    assert result == datetime(2026, 4, 15, 10, 30, 0)


def test_parse_timestamp_with_fractional_seconds():
    result = _parse_timestamp("2026:04:15 10:30:00.123")
    assert result == datetime(2026, 4, 15, 10, 30, 0)


def test_parse_timestamp_with_timezone_offset():
    # The "+" or "-" after the time should be stripped
    result = _parse_timestamp("2026:04:15 10:30:00+02:00")
    assert result == datetime(2026, 4, 15, 10, 30, 0)


def test_parse_timestamp_invalid():
    assert _parse_timestamp(None) is None
    assert _parse_timestamp("") is None
    assert _parse_timestamp("not a date") is None


def test_parse_focal_length_variants():
    assert _parse_focal_length("400.0 mm") == 400.0
    assert _parse_focal_length("400") == 400.0
    assert _parse_focal_length(400.0) == 400.0
    assert _parse_focal_length(None) == 0.0


def test_parse_aperture_variants():
    assert _parse_aperture("6.3") == 6.3
    assert _parse_aperture("f/2.8") == 2.8
    assert _parse_aperture("F8.0") == 8.0
    assert _parse_aperture(6.3) == 6.3
    assert _parse_aperture(None) == 0.0


def test_parse_focus_distance_various():
    assert _parse_focus_distance("2.5 m") == 2.5
    assert _parse_focus_distance("0.5") == 0.5
    assert _parse_focus_distance(1.2) == 1.2


def test_parse_focus_distance_unknown():
    assert _parse_focus_distance(None) is None
    assert _parse_focus_distance("") is None
    assert _parse_focus_distance("inf") is None
    assert _parse_focus_distance("Infinity") is None
    assert _parse_focus_distance("not a number") is None


# ---------------------------------------------------------------------------
# Body profile resolution (real built-in profiles)
# ---------------------------------------------------------------------------

def test_resolve_body_profile_known_g9_ii():
    exif = {"Make": "Panasonic", "Model": "DC-G9M2"}
    body = _resolve_body_profile(exif)
    assert body.body_id == "panasonic_g9_ii"
    assert body.sensor.crop_factor == 2.0


def test_resolve_body_profile_known_a6700():
    exif = {"Make": "SONY", "Model": "ILCE-6700"}
    body = _resolve_body_profile(exif)
    assert body.body_id == "sony_a6700"
    assert body.sensor.crop_factor == 1.5


def test_resolve_body_profile_unknown_creates_stub():
    exif = {"Make": "Hasselblad", "Model": "X2D"}
    body = _resolve_body_profile(exif)
    # Stub has conservative defaults
    assert body.capabilities.ibis is False
    assert body.capabilities.focus_bracket is False
    assert body.sensor.crop_factor == 1.0
    assert "X2D" in body.display_name


# ---------------------------------------------------------------------------
# _build_photo_context
# ---------------------------------------------------------------------------

def _g9_ii_exif(
    *,
    focal="400.0 mm",
    aperture="6.3",
    shutter="1/2000",
    iso="800",
    focus_mode="AFC",
    subject="Bird",
    lens="LUMIX G VARIO 100-400/F4.0-6.3",
    flash="Off, Did not fire",
    datetime_str="2026:04:15 10:30:00",
    **extra,
) -> dict:
    base = {
        "Make": "Panasonic",
        "Model": "DC-G9M2",
        "FocalLength": focal,
        "FNumber": aperture,
        "ExposureTime": shutter,
        "ISO": iso,
        "FocusMode": focus_mode,
        "AFSubjectDetection": subject,
        "LensModel": lens,
        "Flash": flash,
        "DateTimeOriginal": datetime_str,
    }
    base.update(extra)
    return base


def test_build_photo_context_g9_ii_wildlife():
    from core.brand_profile import load_brand_profile
    from core.body_profile import load_body_profile

    brand = load_brand_profile("panasonic")
    body = load_body_profile("panasonic_g9_ii")
    entry = RawExifEntry(path=Path("P001.RW2"), exif=_g9_ii_exif())

    ctx = _build_photo_context(entry, brand, body, None, source="camera")

    assert ctx.focal_length == 400.0
    assert ctx.focal_35mm == 800.0  # 400 * 2.0 crop
    assert ctx.aperture == 6.3
    assert ctx.shutter_speed == pytest.approx(0.0005)
    assert ctx.iso == 800
    assert ctx.focus_mode == FocusMode.CONTINUOUS
    assert ctx.subject_detection == SubjectDetection.BIRD
    assert ctx.iso_relative_to_body == "normal"  # 800 is below 6400 threshold
    assert ctx.source == "camera"
    assert ctx.flash_fired is False
    assert ctx.body is body


def test_build_photo_context_high_iso_marked_high():
    from core.brand_profile import load_brand_profile
    from core.body_profile import load_body_profile

    brand = load_brand_profile("panasonic")
    body = load_body_profile("panasonic_g9_ii")
    entry = RawExifEntry(path=Path("P001.RW2"), exif=_g9_ii_exif(iso="12800"))

    ctx = _build_photo_context(entry, brand, body, None, source="camera")
    assert ctx.iso_relative_to_body == "high"


def test_build_photo_context_without_brand_falls_back_to_unknowns():
    from core.body_profile import load_body_profile

    body = load_body_profile("panasonic_g9_ii")
    entry = RawExifEntry(path=Path("P001.RW2"), exif=_g9_ii_exif())

    ctx = _build_photo_context(entry, brand=None, body=body, lens=None, source="camera")
    assert ctx.focus_mode == FocusMode.UNKNOWN
    assert ctx.af_area_mode == AfAreaMode.UNKNOWN
    assert ctx.subject_detection == SubjectDetection.NONE
    assert ctx.drive_mode == DriveMode.UNKNOWN
    # But numeric values are still parsed from EXIF
    assert ctx.focal_35mm == 800.0
    assert ctx.iso == 800


def test_build_photo_context_populates_lens_model_raw_and_focus_distance():
    """The classifier needs raw LensModel for the phone selfie rule
    (`"front" in lens_model_raw`) and focus_distance for the close-focus
    macro disambiguation rule."""
    from core.brand_profile import load_brand_profile
    from core.body_profile import load_body_profile

    brand = load_brand_profile("panasonic")
    body = load_body_profile("panasonic_g9_ii")
    entry = RawExifEntry(
        path=Path("P001.RW2"),
        exif=_g9_ii_exif(
            lens="LUMIX G MACRO 30/F2.8",
            FocusDistance="0.18 m",
        ),
    )

    ctx = _build_photo_context(entry, brand, body, None, source="camera")
    assert ctx.lens_model_raw == "LUMIX G MACRO 30/F2.8"
    assert ctx.focus_distance == pytest.approx(0.18)


def test_build_photo_context_brackets_inactive_for_normal_shot():
    """A regular shot (no bracket mode active) reports both bracket
    flags as False — Tier 1 bracket rules will not fire."""
    from core.brand_profile import load_brand_profile
    from core.body_profile import load_body_profile

    brand = load_brand_profile("panasonic")
    body = load_body_profile("panasonic_g9_ii")
    entry = RawExifEntry(path=Path("P001.RW2"), exif=_g9_ii_exif())

    ctx = _build_photo_context(entry, brand, body, None, source="camera")
    assert ctx.focus_bracket_active is False
    assert ctx.exposure_bracket_active is False


# ---------------------------------------------------------------------------
# _build_bracket_candidate
# ---------------------------------------------------------------------------

def test_build_bracket_candidate_with_focus_bracket_tag():
    from core.brand_profile import load_brand_profile
    from core.body_profile import load_body_profile

    brand = load_brand_profile("panasonic")
    body = load_body_profile("panasonic_g9_ii")

    exif = _g9_ii_exif(BurstMode="Focus Bracketing")  # Panasonic active when > 0
    entry = RawExifEntry(path=Path("P001.RW2"), exif=exif)
    candidate = _build_bracket_candidate(entry, brand, body, "Leica DG 100-400")

    assert candidate.focus_bracket_tag_active is True
    assert candidate.exposure_bracket_tag_active is False
    assert candidate.lens_name == "Leica DG 100-400"
    assert candidate.body_id == "panasonic_g9_ii"
    assert candidate.timestamp == datetime(2026, 4, 15, 10, 30, 0)
    assert candidate.focal_length == 400.0


def test_build_bracket_candidate_default_orientation():
    from core.brand_profile import load_brand_profile
    from core.body_profile import load_body_profile

    brand = load_brand_profile("panasonic")
    body = load_body_profile("panasonic_g9_ii")
    entry = RawExifEntry(path=Path("P001.RW2"), exif=_g9_ii_exif())

    candidate = _build_bracket_candidate(entry, brand, body, "any_lens")
    # Orientation defaults to 1 when missing
    assert candidate.orientation == 1


# ---------------------------------------------------------------------------
# End-to-end: classify_imported_batch
# ---------------------------------------------------------------------------

def _wildlife_ruleset() -> RuleSet:
    return RuleSet(
        rules=[
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
        ],
    )


def _empty_lens_registry() -> LensRegistry:
    return LensRegistry()


def test_classify_imported_batch_empty_input():
    result = classify_imported_batch(
        [],
        camera_rules=_wildlife_ruleset(),
        lens_registry=_empty_lens_registry(),
        detector_config=DetectorConfig(),
    )
    assert result.sequences == []
    assert result.classified == []
    assert result.errors == []
    assert result.total_processed == 0


def test_classify_imported_batch_single_wildlife_photo():
    entry = RawExifEntry(
        path=Path("P001.RW2"),
        exif=_g9_ii_exif(),  # 400mm, bird detection, CONTINUOUS focus
    )
    result = classify_imported_batch(
        [entry],
        camera_rules=_wildlife_ruleset(),
        lens_registry=_empty_lens_registry(),
        detector_config=DetectorConfig(),
    )

    assert len(result.sequences) == 0  # single photo, no bracket
    assert len(result.classified) == 1
    path, classification = result.classified[0]
    assert path == Path("P001.RW2")
    assert classification.scenario == Scenario.WILDLIFE
    assert classification.rule_id == "wildlife_long_lens"
    assert classification.source == "camera"


def test_classify_imported_batch_detects_focus_bracket():
    # 5 photos 0.5s apart, same lens/body/orientation, focus bracket tag active
    entries = [
        RawExifEntry(
            path=Path(f"P{i:03d}.RW2"),
            exif=_g9_ii_exif(
                datetime_str=f"2026:04:15 10:30:{i:02d}",
                BurstMode="Focus Bracketing",  # active per panasonic.json brand profile
            ),
        )
        for i in range(5)
    ]
    result = classify_imported_batch(
        entries,
        camera_rules=_wildlife_ruleset(),
        lens_registry=_empty_lens_registry(),
        detector_config=DetectorConfig(),
    )

    assert len(result.sequences) == 1
    seq = result.sequences[0]
    assert seq.sequence_type == BracketType.FOCUS
    assert seq.photo_count == 5
    assert seq.detection_source == "exif_tag"
    # No orphans — all 5 photos are in the sequence
    assert result.classified == []


def test_classify_imported_batch_mixed_sequence_and_orphans():
    entries = [
        # Focus bracket sequence (5 photos with focus bracket tag)
        RawExifEntry(
            path=Path(f"bracket_{i}.RW2"),
            exif=_g9_ii_exif(
                datetime_str=f"2026:04:15 10:30:{i:02d}",
                BurstMode="Focus Bracketing",
            ),
        )
        for i in range(5)
    ] + [
        # Isolated wildlife photo (much later)
        RawExifEntry(
            path=Path("wildlife.RW2"),
            exif=_g9_ii_exif(
                datetime_str="2026:04:15 14:00:00",
            ),
        ),
    ]
    result = classify_imported_batch(
        entries,
        camera_rules=_wildlife_ruleset(),
        lens_registry=_empty_lens_registry(),
        detector_config=DetectorConfig(),
    )

    assert len(result.sequences) == 1
    assert len(result.classified) == 1
    path, classification = result.classified[0]
    assert path == Path("wildlife.RW2")
    assert classification.scenario == Scenario.WILDLIFE


def test_classify_imported_batch_phone_source_skips_bracket_detector():
    entries = [
        RawExifEntry(
            path=Path(f"IMG_{i:04d}.JPG"),
            exif={
                "Make": "Apple",
                "Model": "iPhone 15 Pro",
                "FocalLength": "6.0 mm",
                "FNumber": "1.8",
                "ExposureTime": "1/120",
                "ISO": "100",
                "LensModel": "iPhone 15 Pro back triple camera 6mm f/1.8",
                "DateTimeOriginal": f"2026:04:15 10:30:{i:02d}",
            },
        )
        for i in range(3)
    ]
    # Phone rules: minimal; rely on fallbacks
    phone_rules = RuleSet(rules=[])
    result = classify_imported_batch(
        entries,
        source="phone",
        phone_rules=phone_rules,
        lens_registry=_empty_lens_registry(),
    )

    # No sequences because bracket detector is skipped for phone source
    assert result.sequences == []
    # All photos classified (general fallback with unknown_lens flag)
    assert len(result.classified) == 3
    assert all(c.source == "phone" for _, c in result.classified)


def test_classify_imported_batch_no_rule_match_falls_to_general():
    """When the rule set is empty (or no rule matches), classification
    falls to general — the lens-registry-based fallback was removed
    2026-05-13 (pure-EXIF rule). The lens registry being populated
    no longer affects classification; users reclassify via the Type
    override in the culler UI."""
    registry = LensRegistry()
    registry.add(LensEntry(
        id="leica_100_400",
        display_name="Leica DG 100-400",
        lens_model_contains=["100-400"],
        potential_scenarios=[Scenario.WILDLIFE],
        confidence=1.0,
        source="manual",
        evidence={"wildlife": 5},
    ))

    entry = RawExifEntry(
        path=Path("P001.RW2"),
        exif=_g9_ii_exif(AFSubjectDetection="Off"),
    )
    empty_rules = RuleSet(rules=[])
    result = classify_imported_batch(
        [entry],
        camera_rules=empty_rules,
        lens_registry=registry,
        detector_config=DetectorConfig(),
    )

    assert len(result.classified) == 1
    _, classification = result.classified[0]
    assert classification.scenario == Scenario.GENERAL
    assert classification.rule_id is None


def test_classify_imported_batch_error_per_photo_does_not_abort_batch():
    good = RawExifEntry(path=Path("good.RW2"), exif=_g9_ii_exif())
    # A photo whose EXIF dict itself would cause a parse problem — construct
    # a minimal broken entry. Since we catch all exceptions, even weird
    # inputs should produce an error entry rather than crash the batch.
    bad = RawExifEntry(path=Path("bad.RW2"), exif={"Make": "Panasonic", "Model": None})

    result = classify_imported_batch(
        [good, bad],
        camera_rules=_wildlife_ruleset(),
        lens_registry=_empty_lens_registry(),
        detector_config=DetectorConfig(),
    )

    # At minimum, the good photo makes it through
    paths_classified = {p for p, _ in result.classified}
    assert Path("good.RW2") in paths_classified


def test_classify_imported_batch_unknown_body_creates_stub():
    entry = RawExifEntry(
        path=Path("fuji.RAF"),
        exif={
            "Make": "FUJIFILM",
            "Model": "X-T5",
            "FocalLength": "23.0 mm",
            "FNumber": "2.0",
            "ExposureTime": "1/500",
            "ISO": "400",
            "LensModel": "XF23mm F2",
            "DateTimeOriginal": "2026:04:15 10:30:00",
        },
    )
    empty_rules = RuleSet(rules=[])
    result = classify_imported_batch(
        [entry],
        camera_rules=empty_rules,
        lens_registry=_empty_lens_registry(),
        detector_config=DetectorConfig(),
    )

    # Should still classify (via stub body + unknown lens fallback)
    assert len(result.classified) == 1
    _, classification = result.classified[0]
    # No lens registry entry, no matching rule → GENERAL with unknown_lens tag
    assert classification.scenario == Scenario.GENERAL
    assert classification.tag == "needs_review"


# ---------------------------------------------------------------------------
# classify_folder — end-to-end folder scan + classify
# ---------------------------------------------------------------------------

def test_classify_folder_raises_for_missing(tmp_path):
    from core.import_pipeline import classify_folder
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        classify_folder(missing)


def test_classify_folder_raises_for_file(tmp_path):
    from core.import_pipeline import classify_folder
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        classify_folder(f)


def test_classify_folder_empty_returns_empty_result(tmp_path):
    from core.import_pipeline import classify_folder
    result = classify_folder(tmp_path)
    assert result.total_processed == 0
    assert result.sequences == []
    assert result.classified == []


def test_classify_folder_processes_real_photo(tmp_path, monkeypatch):
    """End-to-end: a single RW2 file in a folder goes through the whole
    pipeline (scan → brand profile → body profile → lens registry →
    refinement rules) and produces a ClassificationResult."""
    from core.import_pipeline import classify_folder

    # Create a fake photo file
    photo = tmp_path / "P001.RW2"
    photo.write_bytes(b"fake")

    # Mock the v1.x EXIF reader to return realistic G9II wildlife data
    class _FakePhoto:
        def __init__(self, path, raw):
            self.path = path
            self.raw = raw

    def mock_reader(files):
        return [
            _FakePhoto(
                path=f,
                raw={
                    "SourceFile": str(f),
                    "Make": "Panasonic",
                    "Model": "DC-G9M2",
                    "LensModel": "LUMIX G VARIO 100-400/F4.0-6.3",
                    "FocalLength": "400.0 mm",
                    "FNumber": 6.3,
                    "ExposureTime": "1/2000",
                    "ISO": 800,
                    "FocusMode": "AFC",
                    "AFSubjectDetection": "Bird",
                    "AFAreaMode": "Tracking",
                    "DateTimeOriginal": "2026:04:15 10:30:00",
                },
            )
            for f in files
        ]

    monkeypatch.setattr("core.exif_reader.read_exif_batch", mock_reader)

    result = classify_folder(
        tmp_path,
        lens_registry=_empty_lens_registry(),
    )

    assert result.total_processed == 1
    assert len(result.classified) == 1
    _, classification = result.classified[0]
    assert classification.scenario == Scenario.WILDLIFE


def test_classify_folder_honors_non_recursive(tmp_path, monkeypatch):
    """When recursive=False, only top-level photos are scanned."""
    from core.import_pipeline import classify_folder

    top = tmp_path / "top.rw2"
    top.write_bytes(b"x")
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "inner.rw2").write_bytes(b"x")

    class _FakePhoto:
        def __init__(self, path, raw):
            self.path = path
            self.raw = raw

    def mock_reader(files):
        return [
            _FakePhoto(
                path=f,
                raw={
                    "SourceFile": str(f),
                    "Make": "Panasonic",
                    "Model": "DC-G9M2",
                    "LensModel": "OM 90mm F3.5",
                    "FocalLength": "90.0",
                    "FNumber": 4.0,
                    "DateTimeOriginal": "2026:04:15 10:30:00",
                },
            )
            for f in files
        ]

    monkeypatch.setattr("core.exif_reader.read_exif_batch", mock_reader)

    result = classify_folder(
        tmp_path,
        recursive=False,
        lens_registry=_empty_lens_registry(),
    )
    # Only the top-level photo — subdir's inner.rw2 is skipped
    assert result.total_processed == 1


def test_classify_folder_phone_source_skips_bracket_detection(tmp_path, monkeypatch):
    from core.classifier_v2 import RuleSet
    from core.import_pipeline import classify_folder

    for i in range(3):
        (tmp_path / f"IMG_{i:04d}.JPG").write_bytes(b"x")

    class _FakePhoto:
        def __init__(self, path, raw):
            self.path = path
            self.raw = raw

    def mock_reader(files):
        return [
            _FakePhoto(
                path=f,
                raw={
                    "SourceFile": str(f),
                    "Make": "Apple",
                    "Model": "iPhone 15 Pro",
                    "FocalLength": "6.0 mm",
                    "FNumber": 1.8,
                    "ExposureTime": "1/120",
                    "ISO": 100,
                    "DateTimeOriginal": "2026:04:15 10:30:00",
                },
            )
            for f in files
        ]

    monkeypatch.setattr("core.exif_reader.read_exif_batch", mock_reader)

    result = classify_folder(
        tmp_path,
        source="phone",
        phone_rules=RuleSet(rules=[]),
        lens_registry=_empty_lens_registry(),
    )

    # Phone source skips bracket detection entirely
    assert result.sequences == []
    # All 3 classified as individual photos
    assert len(result.classified) == 3


def test_import_result_properties():
    result = ImportResult(
        sequences=[],
        classified=[(Path("a.RW2"), ClassificationResult(
            scenario=Scenario.WILDLIFE,
            confidence=0.9,
            reason="",
            rule_id=None,
            source="camera",
        ))],
        errors=[(Path("b.RW2"), "parse error")],
    )
    assert result.total_processed == 1
    assert result.total_failed == 1
