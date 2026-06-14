"""Tests for the 2-photo compare-grid EXIF diff (M2.5, Nelson's approved improvement).

Pins the rule: compare set = {shutter speed, aperture, ISO, focal length}; suppress
(``None``) on different style or >2 differing params; otherwise list the 1–2 that differ.
"""
from __future__ import annotations

from types import SimpleNamespace

from mira.picked.exif_compare import (
    COMPARE_PARAMS,
    GRID_CAPTION_MAX,
    caption_html,
    exposure_diff,
    fmt_param,
)


def _exif(shutter=0.005, aperture=2.8, iso=400, focal=50.0):
    return SimpleNamespace(
        shutter_speed=shutter, aperture=aperture, iso=iso, focal_length=focal)


def test_single_param_difference_is_highlighted():
    a = _exif(aperture=2.8)
    b = _exif(aperture=8.0)            # only aperture changed (an aperture A/B)
    assert exposure_diff(a, b) == ["aperture"]


def test_two_param_difference_is_highlighted():
    a = _exif(shutter=0.005, aperture=2.8)
    b = _exif(shutter=0.001, aperture=8.0)   # shutter + aperture (aperture-priority shift)
    diff = exposure_diff(a, b)
    assert diff is not None
    assert set(diff) == {"shutter_speed", "aperture"}


def test_more_than_two_differences_suppresses():
    a = _exif(shutter=0.005, aperture=2.8, iso=400, focal=50.0)
    b = _exif(shutter=0.001, aperture=8.0, iso=1600, focal=85.0)   # all four differ
    assert exposure_diff(a, b) is None


def test_three_differences_suppresses():
    a = _exif(shutter=0.005, aperture=2.8, iso=400, focal=50.0)
    b = _exif(shutter=0.001, aperture=8.0, iso=1600, focal=50.0)   # three differ
    assert exposure_diff(a, b) is None


def test_different_style_suppresses_even_with_one_diff():
    a = _exif(aperture=2.8)
    b = _exif(aperture=8.0)
    assert exposure_diff(a, b, "wildlife", "landscape") is None


def test_one_side_unclassified_still_compares():
    a = _exif(aperture=2.8)
    b = _exif(aperture=8.0)
    # An unclassified side can't assert "different photo" → still a comparison.
    assert exposure_diff(a, b, "wildlife", None) == ["aperture"]
    assert exposure_diff(a, b, None, None) == ["aperture"]


def test_identical_returns_empty_not_none():
    a = _exif()
    b = _exif()
    assert exposure_diff(a, b) == []        # comparable, nothing to highlight (≠ suppressed)


def test_float_noise_is_not_a_difference():
    a = _exif(aperture=6.3)
    b = _exif(aperture=6.30001)             # rounding noise, not a real change
    assert exposure_diff(a, b) == []


def test_unknown_value_on_one_side_counts_as_difference():
    a = _exif(iso=0)                        # 0 = unknown in the EXIF reader
    b = _exif(iso=400)
    assert exposure_diff(a, b) == ["iso"]


def test_none_exif_suppresses():
    assert exposure_diff(None, _exif()) is None
    assert exposure_diff(_exif(), None) is None


def test_compare_set_is_the_exposure_quartet():
    assert COMPARE_PARAMS == ("shutter_speed", "aperture", "iso", "focal_length")


def test_fmt_param_formats():
    assert fmt_param("shutter_speed", 0.005) == "1/200s"
    assert fmt_param("shutter_speed", 2.0) == "2s"
    assert fmt_param("aperture", 2.8) == "f/2.8"
    assert fmt_param("iso", 400) == "ISO 400"
    assert fmt_param("focal_length", 50.0) == "50mm"
    assert fmt_param("iso", 0) == ""              # unknown → omitted


def test_caption_html_plain_and_highlighted():
    e = _exif(aperture=2.8)
    plain = caption_html(e)
    assert "f/2.8" in plain and "<b" not in plain          # no emphasis
    hl = caption_html(e, ["aperture"])
    assert "f/2.8" in hl and "<b" in hl and "#F37021" in hl  # the diff param emphasised
    assert caption_html(None) == ""                          # missing exif → empty


def test_grid_caption_max_is_four():
    assert GRID_CAPTION_MAX == 4
