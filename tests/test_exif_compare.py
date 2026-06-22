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


# --------------------------------------------------------------------------- #
# spec/96 §2 — source-chip helpers (camera + file type + size)
# --------------------------------------------------------------------------- #


def test_file_type_label_recognises_raw_family():
    from mira.picked.exif_compare import file_type_label
    for suffix in (".cr2", ".cr3", ".nef", ".arw", ".raf",
                   ".rw2", ".orf", ".pef", ".dng", ".rwl"):
        assert file_type_label(suffix) == "RAW", suffix
    # Upper-case input collapses to the same label.
    assert file_type_label(".CR3") == "RAW"


def test_file_type_label_recognises_jpeg_and_heif():
    from mira.picked.exif_compare import file_type_label
    assert file_type_label(".jpg") == "JPEG"
    assert file_type_label(".JPEG") == "JPEG"
    assert file_type_label(".heic") == "HEIF"
    assert file_type_label(".HEIF") == "HEIF"


def test_file_type_label_falls_back_to_uppercased_extension():
    """Unknown extensions render as the bare uppercase suffix —
    ``.tif`` → ``TIF``, ``.png`` → ``PNG`` — so a future format
    is still informative without a code change."""
    from mira.picked.exif_compare import file_type_label
    assert file_type_label(".tif") == "TIF"
    assert file_type_label(".png") == "PNG"
    assert file_type_label(".webp") == "WEBP"


def test_file_type_label_empty_suffix_is_blank():
    from mira.picked.exif_compare import file_type_label
    assert file_type_label("") == ""


def test_file_size_text_mb_threshold():
    """spec/96 §2 — MB for ≥ 1 MiB; KB below."""
    from mira.picked.exif_compare import file_size_text
    one_mib = 1024 * 1024
    assert file_size_text(one_mib) == "1.0 MB"
    assert file_size_text(int(24.3 * one_mib)) == "24.3 MB"
    assert file_size_text(one_mib - 1) == "1024 KB"
    assert file_size_text(2048) == "2 KB"


def test_file_size_text_blanks_when_missing_or_zero():
    from mira.picked.exif_compare import file_size_text
    assert file_size_text(None) == ""
    assert file_size_text(0) == ""
    assert file_size_text("not a number") == ""
    assert file_size_text(-1) == ""


def test_source_chip_html_joins_segments_in_order():
    """spec/96 §2 — final chip text =
    ``camera · exposure · type · size``."""
    from mira.picked.exif_compare import source_chip_html
    chip = source_chip_html(
        camera="Pana+G9M2",
        type_label="RAW",
        size_text="24.3 MB",
        exposure_html="1/250s  ·  f/2.8  ·  ISO 400  ·  85mm",
    )
    assert chip.startswith("Pana+G9M2")
    assert chip.endswith("RAW  ·  24.3 MB")
    # Exposure segment lives between camera and type/size.
    assert "1/250s" in chip and "f/2.8" in chip
    # The camera comes BEFORE the exposure.
    assert chip.index("Pana+G9M2") < chip.index("1/250s")
    # And the exposure BEFORE the type tail.
    assert chip.index("85mm") < chip.index("RAW")


def test_source_chip_html_drops_empty_segments():
    """Each segment is optional — missing camera / missing file /
    missing EXIF all collapse cleanly so the chip stays tidy."""
    from mira.picked.exif_compare import source_chip_html
    # Only exposure → chip is just the exposure.
    assert source_chip_html("", "", "", "f/2.8") == "f/2.8"
    # Only camera + type → no exposure, no size.
    assert source_chip_html("Pana+G9M2", "RAW", "", "") == \
        "Pana+G9M2  ·  RAW"
    # Type + size without camera/exposure.
    assert source_chip_html("", "RAW", "24.3 MB", "") == \
        "RAW  ·  24.3 MB"
    # Everything empty → empty string.
    assert source_chip_html("", "", "", "") == ""


def test_source_chip_html_preserves_exposure_html():
    """The exposure HTML may carry rich-text emphasis (the
    ``caption_html`` 2-photo compare highlights one or two diffs);
    ``source_chip_html`` must NOT strip / escape it — the chip
    renders as rich text by contract."""
    from mira.picked.exif_compare import source_chip_html
    exposure = caption_html(_exif(aperture=2.8), ["aperture"])
    chip = source_chip_html(
        "Pana+G9M2", "RAW", "24.3 MB", exposure)
    assert "<b" in chip
    assert "#F37021" in chip
