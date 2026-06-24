"""spec/134 — item → FrameProvenance resolver and the cut-overlay
composer pipeline used by the Picker / Editor viewer pill.

Pure-logic tests over plain dataclasses; no gateway, no Qt.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.cut_overlay import (
    FIELD_HOW1, FIELD_HOW2, FIELD_WHEN, FIELD_WHERE,
    FrameProvenance, compose_overlay_lines,
)
from core.viewer_overlay import (
    item_to_frame_provenance,
    resolve_when,
    resolve_where,
)


def _item(**over) -> SimpleNamespace:
    """An item-shaped object — only the attrs the resolver reads."""
    defaults = dict(
        capture_time_corrected=None,
        capture_time_raw=None,
        camera_id=None,
        day_number=None,
        aperture_f=None,
        shutter_speed_s=None,
        iso=None,
        focal_length_mm=None,
        lens_model=None,
        flash_fired=None,
    )
    defaults.update(over)
    return SimpleNamespace(**defaults)


def _day(*, location=None, country=None, extras_json=None) -> SimpleNamespace:
    if extras_json is None:
        extras_json = json.dumps({"country": country} if country else {})
    return SimpleNamespace(location=location, extras_json=extras_json)


# ── resolve_when: corrected first, raw fallback ────────────────────────


def test_resolve_when_prefers_corrected():
    """The whole correction pipeline exists so the corrected time is
    the one shown. spec/134 §4 — `when` reads
    ``capture_time_corrected`` first."""
    item = _item(
        capture_time_corrected="2026-04-01T08:00:00",
        capture_time_raw="2026-04-01T02:15:00",
    )
    assert resolve_when(item) == "2026-04-01T08:00:00"


def test_resolve_when_falls_back_to_raw_when_corrected_absent():
    item = _item(
        capture_time_corrected=None,
        capture_time_raw="2026-04-01T02:15:00",
    )
    assert resolve_when(item) == "2026-04-01T02:15:00"


def test_resolve_when_none_when_both_missing():
    """No raw + no corrected → None; the composer omits the line."""
    item = _item()
    assert resolve_when(item) is None


def test_resolve_when_empty_corrected_falls_to_raw():
    """An empty string isn't a real value — falsy → fall through to
    raw."""
    item = _item(
        capture_time_corrected="",
        capture_time_raw="2026-04-01T02:15:00",
    )
    assert resolve_when(item) == "2026-04-01T02:15:00"


# ── resolve_where: city from location, country from extras_json ────────


def test_resolve_where_pulls_city_and_country():
    day = _day(location="La Fortuna", country="Costa Rica")
    assert resolve_where(day) == ("La Fortuna", "Costa Rica")


def test_resolve_where_handles_missing_country_in_extras():
    day = _day(location="Arenal", extras_json="{}")
    city, country = resolve_where(day)
    assert city == "Arenal"
    assert country is None


def test_resolve_where_tolerates_invalid_extras_json():
    """Defensive — a malformed extras_json shouldn't crash; country
    drops to None, city still survives."""
    day = _day(location="Arenal", extras_json="not valid json")
    assert resolve_where(day) == ("Arenal", None)


def test_resolve_where_handles_none_day():
    assert resolve_where(None) == (None, None)


def test_resolve_where_empty_strings_normalise_to_none():
    """Empty location / empty country both read as None so the
    composer omits them rather than printing 'None' or a stray comma."""
    day = _day(location="", country="")
    assert resolve_where(day) == (None, None)


# ── item_to_frame_provenance: full assembly ────────────────────────────


def test_resolver_fills_provenance_from_item_and_day():
    item = _item(
        capture_time_corrected="2026-04-01T08:00:00",
        aperture_f=2.8, shutter_speed_s=0.004, iso=400,
        focal_length_mm=85.0, lens_model="LEICA 12-60",
        flash_fired=False,
    )
    day = _day(location="Arenal", country="Costa Rica")
    prov = item_to_frame_provenance(
        item, camera_label="Panasonic G9 II", day=day)
    assert prov == FrameProvenance(
        when="2026-04-01T08:00:00",
        city="Arenal",
        country="Costa Rica",
        camera="Panasonic G9 II",
        lens_model="LEICA 12-60",
        flash_fired=False,
        aperture_f=2.8,
        shutter_speed_s=0.004,
        iso=400,
        focal_length_mm=85.0,
    )


def test_resolver_preserves_flash_tristate():
    """``flash_fired=None`` (unknown) must NOT become ``False``
    (which would print 'no flash'). The cut-overlay's how1 composer
    omits the field entirely when None."""
    item = _item(flash_fired=None)
    prov = item_to_frame_provenance(item)
    assert prov.flash_fired is None
    item = _item(flash_fired=True)
    assert item_to_frame_provenance(item).flash_fired is True
    item = _item(flash_fired=False)
    assert item_to_frame_provenance(item).flash_fired is False


def test_resolver_returns_empty_on_none_item():
    """Defensive — a None item (the gateway lookup miss path) yields
    an empty FrameProvenance; the composer then returns []."""
    assert item_to_frame_provenance(None) == FrameProvenance()


def test_resolver_drops_falsy_camera_label():
    """Empty camera label normalises to None so the composer doesn't
    print 'None' as a camera."""
    item = _item()
    assert item_to_frame_provenance(item, camera_label="").camera is None
    assert item_to_frame_provenance(item, camera_label=None).camera is None


# ── compose_overlay_lines integration over the resolver output ─────────


def test_when_plus_how2_yields_date_and_exposure():
    """Spec example — ["when", "how2"] picks the date + exposure
    quartet (focal, aperture, shutter, ISO) from the item."""
    item = _item(
        capture_time_corrected="2026-04-01T08:00:00",
        focal_length_mm=85.0, aperture_f=2.8,
        shutter_speed_s=0.004, iso=400,
    )
    prov = item_to_frame_provenance(item)
    lines = compose_overlay_lines([FIELD_WHEN, FIELD_HOW2], prov)
    assert lines == ["2026-04-01T08:00:00", "85mm · f/2.8 · 1/250 · ISO 400"]


def test_empty_selection_yields_no_lines():
    """``[]`` → ``[]`` → the overlay pill hides itself."""
    item = _item(focal_length_mm=85.0, aperture_f=2.8)
    prov = item_to_frame_provenance(item)
    assert compose_overlay_lines([], prov) == []


def test_field_with_no_data_is_omitted_gracefully():
    """Spec acceptance — a field with no data is omitted (no blank
    line). Here ``where`` is selected but the item has no day, so the
    where line is skipped."""
    item = _item(focal_length_mm=85.0, aperture_f=2.8)
    prov = item_to_frame_provenance(item, day=None)
    lines = compose_overlay_lines(
        [FIELD_WHEN, FIELD_WHERE, FIELD_HOW2], prov)
    # WHEN has no data either (no times set), so only HOW2 lands.
    assert lines == ["85mm · f/2.8"]


def test_full_selection_emits_lines_in_canonical_order():
    """All four selected → order matches OVERLAY_FIELDS, NOT the
    selection's input order."""
    item = _item(
        capture_time_corrected="2026-04-01T08:00:00",
        focal_length_mm=85.0, aperture_f=2.8,
        shutter_speed_s=0.004, iso=400,
        flash_fired=False,
    )
    day = _day(location="Arenal", country="Costa Rica")
    prov = item_to_frame_provenance(
        item, camera_label="G9 II", day=day)
    # Pass in REVERSE order — compose canonicalises.
    lines = compose_overlay_lines(
        [FIELD_HOW2, FIELD_HOW1, FIELD_WHERE, FIELD_WHEN], prov)
    assert lines == [
        "2026-04-01T08:00:00",                # when
        "Arenal, Costa Rica",                  # where
        "G9 II · no flash",                    # how1 (no lens here)
        "85mm · f/2.8 · 1/250 · ISO 400",      # how2
    ]
