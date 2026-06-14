"""Tests for core.scenario_bootstrap — the drop-zone photo analyzer.

Tests mock ``culler.exif_reader.read_exif_batch`` so they run without
ExifTool or real photo files. This mirrors the hardware_identifier tests
and keeps the suite fast.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from core.scenario_bootstrap import (
    BOOTSTRAP_SCENARIO_ORDER,
    BootstrapAnalysisResult,
    DroppedPhotoResult,
    _parse_float_tolerant,
    analyze_dropped_photos,
    describe_scenario,
)
from core.vocabulary import (
    FINAL_SCENARIOS,
    INTERMEDIATE_SCENARIOS,
    PhotoStyle,
    Scenario,
)


# ---------------------------------------------------------------------------
# Fake PhotoExif stand-in for mocking the v1.x reader
# ---------------------------------------------------------------------------

@dataclass
class _FakePhoto:
    path: Path
    raw: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def fake_photos_dir(tmp_path):
    """Create a handful of real files on disk so Path.is_file() returns True.
    The actual contents don't matter — the EXIF reader is mocked."""
    paths = []
    for i in range(6):
        p = tmp_path / f"P{i:04d}.RW2"
        p.write_bytes(b"fake")
        paths.append(p)
    return paths


def _make_mock_reader(exif_by_path: dict[Path, dict[str, Any]]):
    """Return a function that mocks read_exif_batch using a pre-built EXIF map."""
    def _reader(files: list[Path]) -> list[_FakePhoto]:
        result = []
        for f in files:
            raw = dict(exif_by_path.get(f, {}))
            # Match the shape of the real v1.x reader: raw includes SourceFile
            raw["SourceFile"] = str(f)
            result.append(_FakePhoto(path=f, raw=raw))
        return result
    return _reader


# ---------------------------------------------------------------------------
# _parse_float_tolerant
# ---------------------------------------------------------------------------

def test_parse_float_tolerant_numbers():
    assert _parse_float_tolerant(6.3) == 6.3
    assert _parse_float_tolerant(400) == 400.0


def test_parse_float_tolerant_strings():
    assert _parse_float_tolerant("6.3") == 6.3
    assert _parse_float_tolerant("400.0 mm") == 400.0
    assert _parse_float_tolerant("f/2.8") == 2.8
    assert _parse_float_tolerant("F8") == 8.0


def test_parse_float_tolerant_fractions():
    assert _parse_float_tolerant("1/2000") == pytest.approx(0.0005)
    assert _parse_float_tolerant("1/125") == pytest.approx(0.008)


def test_parse_float_tolerant_bad_input():
    assert _parse_float_tolerant(None) == 0.0
    assert _parse_float_tolerant("") == 0.0
    assert _parse_float_tolerant("not a number") == 0.0
    assert _parse_float_tolerant("1/0") == 0.0  # div by zero


# ---------------------------------------------------------------------------
# BootstrapAnalysisResult aggregations
# ---------------------------------------------------------------------------

def test_empty_result_has_zero_counts():
    r = BootstrapAnalysisResult()
    assert r.accepted_count == 0
    assert r.rejected_count == 0
    assert r.lens_counts == {}


def test_result_counts_by_lens():
    r = BootstrapAnalysisResult(photos=[
        DroppedPhotoResult(path=Path("a"), lens_canonical="Lens A", accepted=True),
        DroppedPhotoResult(path=Path("b"), lens_canonical="Lens A", accepted=True),
        DroppedPhotoResult(path=Path("c"), lens_canonical="Lens B", accepted=True),
        DroppedPhotoResult(path=Path("d"), lens_canonical="", accepted=True),
        DroppedPhotoResult(path=Path("e"), accepted=False),
    ])
    assert r.accepted_count == 4
    assert r.rejected_count == 1
    assert r.lens_counts == {"Lens A": 2, "Lens B": 1, "": 1}


# ---------------------------------------------------------------------------
# analyze_dropped_photos — empty and error paths
# ---------------------------------------------------------------------------

def test_analyze_empty_list():
    result = analyze_dropped_photos([])
    assert result.accepted_count == 0
    assert result.photos == []


def test_analyze_nonexistent_file(tmp_path):
    missing = tmp_path / "does_not_exist.rw2"
    result = analyze_dropped_photos([missing])
    assert result.accepted_count == 0
    assert result.rejected_count == 1
    assert "does not exist" in result.photos[0].warning.lower()


def test_analyze_directory_rejected(tmp_path):
    result = analyze_dropped_photos([tmp_path])
    assert result.rejected_count == 1
    assert "not a file" in result.photos[0].warning.lower()


def test_analyze_exif_reader_crash(fake_photos_dir, monkeypatch):
    def boom(_files):
        raise RuntimeError("exiftool failed")
    monkeypatch.setattr("core.exif_reader.read_exif_batch", boom)

    result = analyze_dropped_photos(fake_photos_dir[:2])
    assert result.accepted_count == 0
    assert result.rejected_count == 2
    for p in result.photos:
        assert "EXIF read failed" in p.warning


# ---------------------------------------------------------------------------
# analyze_dropped_photos — happy path (G9II recognized + full metadata)
# ---------------------------------------------------------------------------

def test_analyze_g9_ii_with_olympus_lens(fake_photos_dir, monkeypatch):
    # Simulate G9II samples with OM lens (from the user's real sample set)
    exif_by_path = {
        p: {
            "Make": "Panasonic",
            "Model": "DC-G9M2",
            "LensModel": "OM 90mm F3.5",
            "FocalLength": "90.0 mm",
            "FNumber": 4.0,
            "ExposureTime": "1/500",
            "FocusMode": "Auto",
        }
        for p in fake_photos_dir[:3]
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos(
        fake_photos_dir[:3],
        expected_body_id="panasonic_g9_ii",
    )

    assert result.accepted_count == 3
    assert result.rejected_count == 0
    for p in result.photos:
        assert p.lens_canonical == "OM 90mm F3.5"
        assert p.focal_length == 90.0
        assert p.aperture == 4.0
        assert p.focus_mode_raw == "Auto"
        assert p.warning == ""  # no mismatch, no missing lens
    assert result.lens_counts == {"OM 90mm F3.5": 3}


# ---------------------------------------------------------------------------
# analyze_dropped_photos — G9I fallback to LensType (the bug fix!)
# ---------------------------------------------------------------------------

def test_analyze_g9_original_with_lens_in_lens_type_tag(fake_photos_dir, monkeypatch):
    """Regression test for the G9I lens fallback fix.

    Panasonic G9 (2017 firmware) leaves LensModel empty when a third-party
    lens is mounted, but populates LensType and LensID via maker notes.
    The scenario bootstrap must read from the fallback chain.
    """
    exif_by_path = {
        p: {
            "Make": "Panasonic",
            "Model": "DC-G9",
            "LensModel": "",  # empty — this is the bug trigger
            "LensType": "Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
            "LensID": "Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
            "FocalLength": "60.0 mm",
            "FNumber": 8.0,
            "ExposureTime": "1/250",
            "FocusMode": "Manual",
        }
        for p in fake_photos_dir[:3]
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos(
        fake_photos_dir[:3],
        expected_body_id="panasonic_g9",
    )

    assert result.accepted_count == 3
    # Lens canonicalized via the panasonic.json alias ("M.ZUIKO DIGITAL ED 60mm")
    # — so all three should have a non-empty lens_canonical
    for p in result.photos:
        assert p.lens_canonical != ""
        assert "60mm" in p.lens_canonical.lower()
        # And critically, no "no lens info" warning:
        assert "no lens info" not in p.warning.lower()


# ---------------------------------------------------------------------------
# analyze_dropped_photos — body mismatch warning
# ---------------------------------------------------------------------------

def test_analyze_body_mismatch_warning(fake_photos_dir, monkeypatch):
    # Photo is from G9II but the user dropped it into the G9 scenario zone
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "Panasonic",
            "Model": "DC-G9M2",
            "LensModel": "OM 90mm F3.5",
            "FocalLength": "90.0 mm",
            "FNumber": 4.0,
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos(
        [fake_photos_dir[0]],
        expected_body_id="panasonic_g9",  # wrong body
    )

    # Photo is still accepted (advisory warning) but the warning is set
    assert result.accepted_count == 1
    photo = result.photos[0]
    assert photo.body_id_detected == "panasonic_g9_ii"
    assert "not the current hardware" in photo.warning.lower()


def test_analyze_no_body_mismatch_when_expected_matches(fake_photos_dir, monkeypatch):
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "Panasonic",
            "Model": "DC-G9M2",
            "LensModel": "OM 90mm F3.5",
            "FocalLength": "90.0 mm",
            "FNumber": 4.0,
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos(
        [fake_photos_dir[0]],
        expected_body_id="panasonic_g9_ii",
    )

    assert result.photos[0].warning == ""


def test_analyze_no_expected_body_skips_mismatch_check(fake_photos_dir, monkeypatch):
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "SONY",
            "Model": "ILCE-6700",
            "LensModel": "18-50mm F2.8 DC DN",
            "FocalLength": "35.0 mm",
            "FNumber": 2.8,
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos(
        [fake_photos_dir[0]],
        expected_body_id=None,  # no expected body → no mismatch check
    )

    assert result.accepted_count == 1
    assert result.photos[0].warning == ""


# ---------------------------------------------------------------------------
# analyze_dropped_photos — no lens info
# ---------------------------------------------------------------------------

def test_analyze_photo_without_any_lens_tag(fake_photos_dir, monkeypatch):
    """A photo with NO lens tags at all (vintage manual lens, no chip)
    should still be accepted but lens_canonical is empty and a warning
    is set."""
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "Panasonic",
            "Model": "DC-G9M2",
            # No LensModel, no LensType, no LensID
            "FocalLength": "50.0 mm",
            "FNumber": 2.0,
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos([fake_photos_dir[0]])
    assert result.accepted_count == 1
    photo = result.photos[0]
    assert photo.lens_canonical == ""
    assert "no lens info" in photo.warning.lower()


def test_analyze_unknown_brand_falls_back_to_raw_lens_model(fake_photos_dir, monkeypatch):
    """Photo from a camera with no matching brand profile still extracts
    lens info from the raw EXIF (defensive fallback)."""
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "FUJIFILM",  # no brand profile for this
            "Model": "X-T5",
            "LensModel": "XF23mm F2",
            "FocalLength": "23.0 mm",
            "FNumber": 2.0,
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos([fake_photos_dir[0]])
    assert result.photos[0].lens_canonical == "XF23mm F2"


# ---------------------------------------------------------------------------
# analyze_dropped_photos — batch with mix of results
# ---------------------------------------------------------------------------

def test_analyze_batch_with_mixed_results(fake_photos_dir, monkeypatch):
    """Realistic batch: some G9II with Olympus lens, some G9I with no
    lens info, one from a different body. All accepted, different warnings."""
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "Panasonic", "Model": "DC-G9M2",
            "LensModel": "OM 90mm F3.5", "FocalLength": "90.0", "FNumber": 4.0,
        },
        fake_photos_dir[1]: {
            "Make": "Panasonic", "Model": "DC-G9M2",
            "LensModel": "OM 90mm F3.5", "FocalLength": "90.0", "FNumber": 4.0,
        },
        fake_photos_dir[2]: {
            # G9I via LensType fallback
            "Make": "Panasonic", "Model": "DC-G9",
            "LensModel": "",
            "LensType": "Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
            "FocalLength": "60.0", "FNumber": 8.0,
        },
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos(
        fake_photos_dir[:3],
        expected_body_id="panasonic_g9_ii",
    )
    assert result.accepted_count == 3
    # The third photo triggers body mismatch warning since it's G9I
    mismatches = [p for p in result.photos if "not the current hardware" in p.warning]
    assert len(mismatches) == 1
    assert mismatches[0].body_id_detected == "panasonic_g9"


# ---------------------------------------------------------------------------
# Scenario ordering and descriptions
# ---------------------------------------------------------------------------

def test_bootstrap_scenario_order_contains_all_scenarios():
    """The UI grid iterates BOOTSTRAP_SCENARIO_ORDER and expects every
    scenario to be present exactly once."""
    assert set(BOOTSTRAP_SCENARIO_ORDER) == set(FINAL_SCENARIOS) | set(INTERMEDIATE_SCENARIOS)
    # Count tracks the Scenario enum — extending the enum (e.g. adding
    # sports / street / etc. for wizard scenarios in 2026-05-13) just
    # grows the order tuple, not a regression.
    assert len(BOOTSTRAP_SCENARIO_ORDER) == (
        len(FINAL_SCENARIOS) + len(INTERMEDIATE_SCENARIOS)
    )


def test_bootstrap_scenario_order_finals_first():
    """Final scenarios come before intermediates in the UI grid so the
    user sees the common ones first."""
    final_indexes = [i for i, s in enumerate(BOOTSTRAP_SCENARIO_ORDER) if s in FINAL_SCENARIOS]
    intermediate_indexes = [i for i, s in enumerate(BOOTSTRAP_SCENARIO_ORDER) if s in INTERMEDIATE_SCENARIOS]
    assert max(final_indexes) < min(intermediate_indexes)


def test_describe_scenario_has_text_for_every_scenario():
    for scenario in Scenario:
        desc = describe_scenario(scenario)
        assert isinstance(desc, str)
        assert len(desc) > 0


# ---------------------------------------------------------------------------
# PhotoStyle extraction through the bootstrap pipeline
# ---------------------------------------------------------------------------

def test_analyze_extracts_panasonic_photo_style(fake_photos_dir, monkeypatch):
    """G9II sample with PhotoStyle=Portrait should come through as
    PhotoStyle.PORTRAIT on the DroppedPhotoResult."""
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "Panasonic",
            "Model": "DC-G9M2",
            "LensModel": "OM 90mm F3.5",
            "FocalLength": "90.0 mm",
            "FNumber": 4.0,
            "PhotoStyle": "Portrait",
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos([fake_photos_dir[0]])
    assert result.accepted_count == 1
    assert result.photos[0].photo_style == PhotoStyle.PORTRAIT


def test_analyze_extracts_panasonic_natural_style(fake_photos_dir, monkeypatch):
    """Regression for the actual user sample pattern — G9I/G9II all had
    PhotoStyle=Natural in the initial dataset."""
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "Panasonic",
            "Model": "DC-G9",
            "LensModel": "",
            "LensType": "Olympus M.Zuiko Digital ED 60mm F2.8 Macro",
            "PhotoStyle": "Natural",
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos([fake_photos_dir[0]])
    assert result.photos[0].photo_style == PhotoStyle.NATURAL


def test_analyze_extracts_sony_creative_look_portrait(fake_photos_dir, monkeypatch):
    """Newer Sony bodies (A6700) use CreativeLook with short codes."""
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "SONY",
            "Model": "ILCE-6700",
            "LensModel": "E 70-350mm F4.5-6.3 G OSS",
            "FocalLength": "200.0 mm",
            "FNumber": 5.6,
            "CreativeLook": "PT",
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos([fake_photos_dir[0]])
    assert result.photos[0].photo_style == PhotoStyle.PORTRAIT


def test_analyze_sony_creative_style_off_is_unknown(fake_photos_dir, monkeypatch):
    """Regression for the real A6700 sample: CreativeStyle='Off' means the
    user is using Picture Profiles instead — treat as unknown so no future
    rule accidentally matches."""
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "SONY",
            "Model": "ILCE-6700",
            "LensModel": "18-50mm F2.8 DC DN | Contemporary 021",
            "FocalLength": "35.0 mm",
            "FNumber": 2.8,
            "CreativeStyle": "Off",
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos([fake_photos_dir[0]])
    assert result.photos[0].photo_style == PhotoStyle.UNKNOWN


def test_analyze_missing_photo_style_tag_is_unknown(fake_photos_dir, monkeypatch):
    """When the tag is simply not present (older photos or unusual camera),
    photo_style defaults to UNKNOWN without raising."""
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "Panasonic",
            "Model": "DC-G9M2",
            "LensModel": "OM 90mm F3.5",
            "FocalLength": "90.0",
            "FNumber": 4.0,
            # No PhotoStyle tag
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos([fake_photos_dir[0]])
    assert result.photos[0].photo_style == PhotoStyle.UNKNOWN


def test_analyze_unknown_brand_leaves_photo_style_unknown(fake_photos_dir, monkeypatch):
    """For brands without a profile, we can't translate PhotoStyle even
    if the tag is present — default to UNKNOWN."""
    exif_by_path = {
        fake_photos_dir[0]: {
            "Make": "FUJIFILM",
            "Model": "X-T5",
            "LensModel": "XF23mm F2",
            "FilmMode": "Velvia",  # Fuji's equivalent, but we have no profile
        }
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _make_mock_reader(exif_by_path),
    )

    result = analyze_dropped_photos([fake_photos_dir[0]])
    assert result.photos[0].photo_style == PhotoStyle.UNKNOWN
