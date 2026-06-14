"""Tests for the brand-aware AF-point extraction (E7a, docs/18).

Pure-logic: synthetic EXIF dicts → normalized AfPoint. Mirrors the
FocusPositionRule test style. No Qt, no real RAW.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.brand_profile import (
    AfPoint,
    AfPointRule,
    _parse_af_point_rule,
    load_brand_profile,
)


# ── AfPointRule.compute — normalized_xy (Panasonic) ──────────────


def test_normalized_xy_centre():
    r = AfPointRule(kind="normalized_xy", xy_tag="AFPointPosition")
    p = r.compute({"AFPointPosition": "0.5 0.5"})
    assert p == AfPoint(0.5, 0.5, 0.08, 0.08)


def test_normalized_xy_off_centre_and_list_form():
    r = AfPointRule(kind="normalized_xy", xy_tag="AFPointPosition",
                    default_box=0.1)
    assert r.compute({"AFPointPosition": [0.25, 0.75]}) == AfPoint(
        0.25, 0.75, 0.1, 0.1
    )


def test_normalized_xy_clamps_out_of_range():
    r = AfPointRule(kind="normalized_xy", xy_tag="AFPointPosition")
    p = r.compute({"AFPointPosition": "-0.3 1.4"})
    assert p == AfPoint(0.0, 1.0, 0.08, 0.08)


def test_normalized_xy_missing_or_garbage_returns_none():
    r = AfPointRule(kind="normalized_xy", xy_tag="AFPointPosition")
    assert r.compute({}) is None
    assert r.compute({"AFPointPosition": ""}) is None
    assert r.compute({"AFPointPosition": "abc"}) is None
    assert r.compute({"AFPointPosition": "0.5"}) is None  # need 2 nums


# ── pixel_xy_image_size (generic) ────────────────────────────────


def test_pixel_xy_normalizes_by_frame():
    r = AfPointRule(
        kind="pixel_xy_image_size",
        x_tag="AFX", y_tag="AFY",
        image_w_tag="IW", image_h_tag="IH",
    )
    p = r.compute({"AFX": 1000, "AFY": 500, "IW": 4000, "IH": 2000})
    assert p == AfPoint(0.25, 0.25, 0.08, 0.08)


def test_pixel_xy_zero_frame_returns_none():
    r = AfPointRule(
        kind="pixel_xy_image_size",
        x_tag="AFX", y_tag="AFY", image_w_tag="IW", image_h_tag="IH",
    )
    assert r.compute({"AFX": 1, "AFY": 1, "IW": 0, "IH": 100}) is None


# ── sony_focus_location ──────────────────────────────────────────


def test_sony_focus_location_4_tuple():
    r = AfPointRule(kind="sony_focus_location", location_tag="FocusLocation")
    # "imgW imgH afX afY"
    p = r.compute({"FocusLocation": "6000 4000 3000 1000"})
    assert p == AfPoint(0.5, 0.25, 0.08, 0.08)


def test_sony_focus_location_with_frame_size():
    r = AfPointRule(
        kind="sony_focus_location",
        location_tag="FocusLocation",
        frame_size_tag="FocusFrameSize",
    )
    p = r.compute({
        "FocusLocation": "6000 4000 3000 2000",
        "FocusFrameSize": "600 400",
    })
    assert p == AfPoint(0.5, 0.5, 0.1, 0.1)


def test_sony_focus_location_short_or_bad_returns_none():
    r = AfPointRule(kind="sony_focus_location", location_tag="FocusLocation")
    assert r.compute({"FocusLocation": "6000 4000 3000"}) is None  # 3 nums
    assert r.compute({"FocusLocation": "0 0 1 1"}) is None          # zero img
    assert r.compute({}) is None


def test_unknown_kind_returns_none():
    assert AfPointRule(kind="bogus").compute({"x": 1}) is None


# ── _parse_af_point_rule ─────────────────────────────────────────


def test_parse_none_when_omitted():
    assert _parse_af_point_rule(None) is None
    assert _parse_af_point_rule({}) is None


def test_parse_normalized_xy_requires_tag():
    assert _parse_af_point_rule({"kind": "normalized_xy"}) is None
    rule = _parse_af_point_rule(
        {"kind": "normalized_xy", "xy_tag": "AFPointPosition",
         "default_box": 0.05}
    )
    assert rule.kind == "normalized_xy"
    assert rule.xy_tag == "AFPointPosition"
    assert rule.default_box == 0.05


def test_parse_sony_focus_location():
    rule = _parse_af_point_rule(
        {"kind": "sony_focus_location", "location_tag": "FocusLocation",
         "frame_size_tag": "FocusFrameSize"}
    )
    assert rule.kind == "sony_focus_location"
    assert rule.location_tag == "FocusLocation"
    assert rule.frame_size_tag == "FocusFrameSize"


def test_parse_unknown_kind_none():
    assert _parse_af_point_rule({"kind": "weird", "xy_tag": "X"}) is None


# ── Wired into the real bundled profiles ─────────────────────────


def test_panasonic_profile_declares_af_point():
    prof = load_brand_profile("panasonic")
    assert prof.af_point is not None
    assert prof.af_point.kind == "normalized_xy"
    p = prof.read_af_point({"AFPointPosition": "0.6 0.4"})
    assert p == AfPoint(0.6, 0.4, 0.08, 0.08)


def test_sony_profile_declares_af_point():
    prof = load_brand_profile("sony")
    assert prof.af_point is not None
    assert prof.af_point.kind == "sony_focus_location"
    p = prof.read_af_point({"FocusLocation": "6000 4000 1500 1000"})
    assert p == AfPoint(0.25, 0.25, 0.08, 0.08)


def test_af_point_tags_are_in_exif_whitelist():
    """Integration regression (Nelson 2026-05-16): the brand-AF
    rule reads tags that core.exif_reader must actually fetch from
    exiftool, or the overlay never draws on ANY body. These were
    missing from the E7 whitelist."""
    from core.exif_reader import TAGS
    for tag in ("AFPointPosition", "FocusLocation", "FocusFrameSize",
                "RegionAreaX", "RegionAreaY", "RegionAreaW",
                "RegionAreaH", "RegionType"):
        assert tag in TAGS, f"{tag} missing from exif_reader.TAGS"


def test_apple_profile_declares_mwg_face_regions():
    prof = load_brand_profile("apple")
    assert prof.af_point is not None
    assert prof.af_point.kind == "mwg_face_regions"


def test_mwg_picks_largest_face():
    """iPhone HEIC: parallel RegionArea* arrays; X/Y are the
    region centre. Pick the largest Face (the subject the camera
    locked) — matches the real IMG_5771 probe."""
    prof = load_brand_profile("apple")
    p = prof.read_af_point({
        "RegionType": ["Face", "Face"],
        "RegionAreaX": ["0.6175", "0.5985"],
        "RegionAreaY": ["0.7135", "0.2845"],
        "RegionAreaW": ["0.151", "0.209"],
        "RegionAreaH": ["0.201", "0.279"],
    })
    assert p == AfPoint(0.5985, 0.2845, 0.209, 0.279)   # bigger face


def test_mwg_prefers_focus_region_over_bigger_face():
    """MWG 'Focus' is literally the AF point — it wins even over a
    larger Face region."""
    prof = load_brand_profile("apple")
    p = prof.read_af_point({
        "RegionType": ["Face", "Focus"],
        "RegionAreaX": ["0.2", "0.8"],
        "RegionAreaY": ["0.2", "0.8"],
        "RegionAreaW": ["0.5", "0.02"],     # Face huge, Focus tiny
        "RegionAreaH": ["0.5", "0.02"],
    })
    assert p == AfPoint(0.8, 0.8, 0.02, 0.02)           # the Focus one


def test_mwg_single_scalar_region():
    """One face → exiftool returns scalars, not lists."""
    prof = load_brand_profile("apple")
    p = prof.read_af_point({
        "RegionType": "Face",
        "RegionAreaX": 0.5, "RegionAreaY": 0.4,
        "RegionAreaW": 0.1, "RegionAreaH": 0.12,
    })
    assert p == AfPoint(0.5, 0.4, 0.1, 0.12)


def test_mwg_default_box_when_no_size():
    prof = load_brand_profile("apple")
    p = prof.read_af_point({
        "RegionType": "Face", "RegionAreaX": 0.5, "RegionAreaY": 0.5,
    })
    assert p == AfPoint(0.5, 0.5, 0.10, 0.10)            # apple default_box


def test_mwg_none_when_no_regions():
    prof = load_brand_profile("apple")
    assert prof.read_af_point({}) is None
    assert prof.read_af_point({"RegionType": "Face"}) is None  # no coords


def test_orientation_code_parsing():
    from core.brand_profile import AfPointRule as R
    assert R._orientation_code("Horizontal (normal)") == 1
    assert R._orientation_code("Rotate 180") == 3
    assert R._orientation_code("Rotate 90 CW") == 6
    assert R._orientation_code("rotate 270 cw") == 8
    assert R._orientation_code(6) == 6
    assert R._orientation_code(None) == 1          # absent → identity
    assert R._orientation_code("garbage") == 1     # unknown → identity


def test_orient_norm_cases():
    from core.brand_profile import AfPointRule as R
    # identity
    assert R._orient_norm(0.6, 0.3, 0.1, 0.2, 1) == (0.6, 0.3, 0.1, 0.2)
    # 180: mirror both axes, size unchanged
    assert R._orient_norm(0.6, 0.3, 0.1, 0.2, 3) == (
        pytest.approx(0.4), pytest.approx(0.7), 0.1, 0.2)
    # 90 CW: (1-cy, cx) and w/h swap (portrait display)
    assert R._orient_norm(0.6, 0.3, 0.1, 0.2, 6) == (
        pytest.approx(0.7), pytest.approx(0.6), 0.2, 0.1)
    # 270 CW: (cy, 1-cx) and w/h swap
    assert R._orient_norm(0.6, 0.3, 0.1, 0.2, 8) == (
        pytest.approx(0.3), pytest.approx(0.4), 0.2, 0.1)


def test_mwg_applies_exif_orientation():
    """Nelson 2026-05-16: the AF box was misplaced on every rotated
    iPhone shot. MWG coords are in the un-rotated sensor frame; the
    canvas shows the EXIF-upright image, so the point must be
    transformed by Orientation. Real values from IMG_5771(1)
    (Rotate 180) and IMG_3468 (Rotate 90 CW)."""
    prof = load_brand_profile("apple")
    # Orientation 1 → unchanged (the CORRECT-landing shots).
    base = {"RegionType": "Face", "RegionAreaX": 0.6, "RegionAreaY": 0.3,
            "RegionAreaW": 0.1, "RegionAreaH": 0.2}
    assert prof.read_af_point({**base, "Orientation": "Horizontal (normal)"}) \
        == AfPoint(0.6, 0.3, 0.1, 0.2)
    # Rotate 180 (IMG_5771-style) → mirror both axes.
    p = prof.read_af_point({**base, "Orientation": "Rotate 180"})
    assert p == AfPoint(pytest.approx(0.4), pytest.approx(0.7), 0.1, 0.2)
    # Rotate 90 CW (IMG_3468-style) → (1-cy, cx), w/h swap.
    p = prof.read_af_point({**base, "Orientation": "Rotate 90 CW"})
    assert p == AfPoint(pytest.approx(0.7), pytest.approx(0.6), 0.2, 0.1)


def test_af_point_sentinels_are_no_af():
    """Lumix G9 II / DC-G9M2 writes the literal 'n/a' (newer) or
    'none' (older) into AFPointPosition even for AF-S/AF-C — it
    records no AF coordinate. Those must degrade to None (→ global
    default), never raise or fabricate a box (docs/18 §AF)."""
    prof = load_brand_profile("panasonic")
    for sentinel in ("n/a", "none", "", "N/A", "None"):
        assert prof.read_af_point({"AFPointPosition": sentinel}) is None
    # The real-coordinate path still works (older bodies / Sony).
    assert prof.read_af_point({"AFPointPosition": "0.6 0.4"}) == AfPoint(
        0.6, 0.4, 0.08, 0.08
    )


def test_read_af_point_none_when_profile_has_no_rule():
    """A profile without an af_point block (e.g. gopro) returns None
    rather than raising — graceful per docs/18."""
    prof = load_brand_profile("gopro")
    assert prof.read_af_point({"AFPointPosition": "0.5 0.5"}) is None


def test_af_point_is_normalized_and_brand_agnostic_shape():
    """The whole point: consumers get one shape regardless of brand
    encoding. Panasonic normalized pair and Sony pixel 4-tuple both
    yield the same AfPoint type with 0..1 fields."""
    pan = load_brand_profile("panasonic").read_af_point(
        {"AFPointPosition": "0.5 0.5"}
    )
    sony = load_brand_profile("sony").read_af_point(
        {"FocusLocation": "6000 4000 3000 2000"}
    )
    for p in (pan, sony):
        assert isinstance(p, AfPoint)
        assert 0.0 <= p.cx <= 1.0 and 0.0 <= p.cy <= 1.0
        assert 0.0 <= p.w <= 1.0 and 0.0 <= p.h <= 1.0


def test_all_bundled_profiles_still_parse():
    """The new af_point field must not break loading any profile."""
    d = Path(__file__).resolve().parent.parent / "assets" / "brand_profiles"
    for jf in d.glob("*.json"):
        prof = load_brand_profile(jf.stem)
        assert prof.brand_id
        # af_point is optional — just must not raise.
        json.loads(jf.read_text(encoding="utf-8"))
