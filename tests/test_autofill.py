"""Tests for ``core.autofill`` — spec/52 §3.

Logic-only (no Qt). Synthesises :class:`PhotoExif` instances rather than
running ExifTool — the autofill engine is the unit under test, not the
EXIF reader.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core import autofill
from core.autofill import (
    DayAutofill,
    autofill_description_from_subdir,
    autofill_for_day,
    autofill_phone_for_day,
    common_immediate_subdir,
    is_phone_photo,
)
from core.exif_reader import PhotoExif


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #


def _phone(
    path: str = "P1.JPG",
    *,
    make: str = "Apple",
    model: str = "iPhone 15 Pro",
    tz: int | None = -180,
    gps: tuple[float, float] | None = (-23.5, -46.6),       # São Paulo
    timestamp: datetime | None = None,
) -> PhotoExif:
    """A synthesised phone photo. Defaults to a São Paulo iPhone."""
    return PhotoExif(
        path=Path(path),
        timestamp=timestamp,
        model=model,
        tz_offset_minutes=tz,
        gps_lat=gps[0] if gps else None,
        gps_lon=gps[1] if gps else None,
        raw={"Make": make, "Model": model},
    )


def _camera(
    path: str = "C1.RW2",
    *,
    make: str = "Panasonic",
    model: str = "DC-G9M2",
) -> PhotoExif:
    """A synthesised camera photo — Panasonic G9 II by default. No GPS, no TZ."""
    return PhotoExif(
        path=Path(path),
        model=model,
        raw={"Make": make, "Model": model},
    )


# --------------------------------------------------------------------------- #
# is_phone_photo
# --------------------------------------------------------------------------- #


def test_is_phone_photo_uses_make_and_model_together(tmp_path):
    """Wraps ``phone_detector.is_phone(make, model)`` — Sony Alpha must NOT
    classify as a phone even though the Make matches."""
    iphone = _phone(model="iPhone 15 Pro")
    sony_alpha = _camera(make="Sony", model="ILCE-7RM5")
    sony_xperia = _phone(make="Sony", model="Xperia 1 V")
    assert is_phone_photo(iphone) is True
    assert is_phone_photo(sony_alpha) is False
    assert is_phone_photo(sony_xperia) is True


def test_is_phone_photo_returns_false_when_raw_make_is_missing(tmp_path):
    """A PhotoExif with no raw['Make'] still gets is_phone() asked with
    None — the maker check fails, the result is False even if the model
    name looks phoney."""
    weird = PhotoExif(path=Path("P.JPG"), model="iPhone 15", raw={})
    # Apple rule requires make='Apple' to fire.
    assert is_phone_photo(weird) is False


# --------------------------------------------------------------------------- #
# autofill_phone_for_day — TZ / GPS / country / location independence
# --------------------------------------------------------------------------- #


def test_phone_autofill_pulls_tz_from_first_phone_with_signal(tmp_path):
    photos = [
        _phone("P1.JPG", tz=-180, gps=None),
        _phone("P2.JPG", tz=-120, gps=None),
    ]
    out = autofill_phone_for_day(photos)
    assert out.tz_minutes == -180
    assert out.tz_source == "phone_exif"


def test_phone_autofill_walks_per_signal_independently(tmp_path):
    """A phone with GPS off (airplane mode) still contributes its TZ; a
    later phone with GPS but no TZ contributes location. Each signal walks
    independently."""
    photos = [
        _phone("airplane.JPG", tz=-180, gps=None),
        _phone("withgps.JPG", tz=None, gps=(-23.5, -46.6)),
    ]
    out = autofill_phone_for_day(photos)
    assert out.tz_minutes == -180
    assert out.location is not None and "São Paulo" in out.location or "Sao Paulo" in out.location
    assert out.country_code == "BR"


def test_phone_autofill_empty_when_no_phone_photos(tmp_path):
    """A day of camera-only photos yields an empty autofill — there's
    nothing to pull from."""
    out = autofill_phone_for_day([_camera(), _camera("C2.RW2")])
    assert out.is_empty() is True


def test_phone_autofill_empty_when_phone_photos_have_no_signals(tmp_path):
    """A stripped-EXIF phone photo (WhatsApp share, per spec/52 §9) has no
    TZ + no GPS — the autofill is empty even though phone is detected."""
    stripped = _phone("WA.JPG", tz=None, gps=None)
    out = autofill_phone_for_day([stripped])
    assert out.is_empty() is True


def test_phone_autofill_sets_description_initially_equal_to_location():
    """spec/52 §3.1 — description starts as the location text; subdir
    autofill may override it (covered separately below)."""
    photos = [_phone(gps=(-23.5, -46.6))]   # São Paulo
    out = autofill_phone_for_day(photos)
    assert out.location is not None
    assert out.description == out.location
    assert out.description_source == "phone_exif"


def test_phone_autofill_country_code_is_iso_alpha2():
    """Spot-check that country_code_for delivers a 2-letter ISO code."""
    photos = [_phone(gps=(-23.5, -46.6))]
    out = autofill_phone_for_day(photos)
    assert out.country_code == "BR"


def test_phone_autofill_country_is_none_for_ocean_coords():
    """Open-ocean coords fall outside every country polygon — country stays
    None. The location-string lookup may still produce nearest-city text."""
    photos = [_phone(gps=(0.0, -30.0))]      # mid-Atlantic
    out = autofill_phone_for_day(photos)
    assert out.country_code is None


# --------------------------------------------------------------------------- #
# common_immediate_subdir — strict-detection rule
# --------------------------------------------------------------------------- #


def test_common_immediate_subdir_finds_shared_folder(tmp_path):
    src = tmp_path / "Trip 2024"
    src.mkdir()
    day_dir = src / "Day 1 - Lisbon"
    day_dir.mkdir()
    photos = [day_dir / "P1.JPG", day_dir / "P2.JPG", day_dir / "P3.JPG"]
    assert common_immediate_subdir(photos, src) == "Day 1 - Lisbon"


def test_common_immediate_subdir_returns_none_on_mixed_spread(tmp_path):
    """Spec/52 §3.2 strict-detection — a day's photos split across two
    folders skip the autofill (no guessing on noisy structure)."""
    src = tmp_path / "Trip"
    src.mkdir()
    day_a = src / "Day 1 - Lisbon"
    day_b = src / "Stray"
    day_a.mkdir()
    day_b.mkdir()
    photos = [day_a / "P1.JPG", day_a / "P2.JPG", day_b / "Stray.JPG"]
    assert common_immediate_subdir(photos, src) is None


def test_common_immediate_subdir_returns_none_for_root_photos(tmp_path):
    """A photo directly in the source root has no subdir — no autofill."""
    src = tmp_path / "Trip"
    src.mkdir()
    day = src / "Day 1"
    day.mkdir()
    photos = [day / "P1.JPG", src / "Loose.JPG"]   # one is in source_root
    assert common_immediate_subdir(photos, src) is None


def test_common_immediate_subdir_returns_none_outside_source(tmp_path):
    """A photo path that isn't under source_root at all yields None
    (defence — the scan should never produce this, but a hand-crafted
    list could)."""
    src = tmp_path / "Trip"
    src.mkdir()
    elsewhere = tmp_path / "elsewhere" / "P.JPG"
    elsewhere.parent.mkdir()
    assert common_immediate_subdir([elsewhere], src) is None


def test_common_immediate_subdir_returns_none_for_empty_input(tmp_path):
    src = tmp_path / "Trip"
    src.mkdir()
    assert common_immediate_subdir([], src) is None


def test_common_immediate_subdir_walks_into_nested_folder_structure(tmp_path):
    """The autofill key is the IMMEDIATE subdir; deeper structure underneath
    doesn't matter as long as every photo shares the same first-level folder."""
    src = tmp_path / "Trip"
    src.mkdir()
    day = src / "Day 1 - Lisbon"
    (day / "morning").mkdir(parents=True)
    (day / "evening").mkdir()
    photos = [day / "morning" / "P1.JPG", day / "evening" / "P2.JPG"]
    assert common_immediate_subdir(photos, src) == "Day 1 - Lisbon"


def test_common_immediate_subdir_preserves_verbatim_name(tmp_path):
    """spec/52 §3.3 — no date-prefix stripping. ``"2024-07-12 Sintra"`` is
    copied verbatim into description."""
    src = tmp_path / "Trip"
    src.mkdir()
    day = src / "2024-07-12 Sintra hike"
    day.mkdir()
    photos = [day / "P1.JPG"]
    assert common_immediate_subdir(photos, src) == "2024-07-12 Sintra hike"


# --------------------------------------------------------------------------- #
# autofill_description_from_subdir — the PhotoExif-shaped wrapper
# --------------------------------------------------------------------------- #


def test_autofill_description_from_subdir_pulls_paths_from_photoexif(tmp_path):
    src = tmp_path / "Trip"
    src.mkdir()
    day = src / "Day 1"
    day.mkdir()
    photos = [
        PhotoExif(path=day / "A.JPG"),
        PhotoExif(path=day / "B.JPG"),
    ]
    assert autofill_description_from_subdir(photos, src) == "Day 1"


# --------------------------------------------------------------------------- #
# autofill_for_day — combined engine + the §3.3 subdir-beats-phone rule
# --------------------------------------------------------------------------- #


def test_combined_autofill_subdir_beats_phone_default_description(tmp_path):
    """The combined autofill keeps country/TZ/location from the phone pass
    but the subdir name overrides description (spec/52 §3.3)."""
    src = tmp_path / "Trip"
    src.mkdir()
    day_dir = src / "Day 1 - Lisbon"
    day_dir.mkdir()
    photos = [
        _phone(str(day_dir / "P1.JPG"), gps=(38.7, -9.1)),  # Lisbon
        _phone(str(day_dir / "P2.JPG"), gps=(38.7, -9.1)),
    ]
    out = autofill_for_day(photos, source_root=src)
    # Phone pass populates country / TZ / location.
    assert out.country_code == "PT"
    assert out.tz_minutes == -180   # default from _phone
    assert out.location is not None
    assert out.location_source == "phone_exif"
    # Subdir overrides description.
    assert out.description == "Day 1 - Lisbon"
    assert out.description_source == "subdir"


def test_combined_autofill_keeps_phone_description_when_subdir_is_mixed(tmp_path):
    """When the subdir-name autofill doesn't fire (photos spread), the
    description from the phone pass survives."""
    src = tmp_path / "Trip"
    src.mkdir()
    day_a = src / "Day 1"
    day_b = src / "Stray"
    day_a.mkdir()
    day_b.mkdir()
    photos = [
        _phone(str(day_a / "P1.JPG"), gps=(38.7, -9.1)),
        _phone(str(day_b / "P2.JPG"), gps=(38.7, -9.1)),
    ]
    out = autofill_for_day(photos, source_root=src)
    assert out.description == out.location
    assert out.description_source == "phone_exif"


def test_combined_autofill_skips_subdir_pass_when_source_root_is_none():
    """source_root=None — used for SD-card sources where the per-day-subdir
    layout doesn't apply. Description stays from the phone pass."""
    photos = [_phone("P1.JPG", gps=(38.7, -9.1))]
    out = autofill_for_day(photos, source_root=None)
    assert out.description == out.location
    assert out.description_source == "phone_exif"
