"""Tests for ``core.tz_calibration`` — spec/52 §8.2 / §8.4 (slice D.3.a).

Pure-logic coverage of the calibration trigger: home-TZ short-circuit,
phone-camera skip, already-calibrated skip, missing-day-TZ skip,
multi-day border-crossing case, summary helpers.
"""
from __future__ import annotations

from datetime import date

from core.tz_calibration import (
    CalibrationCandidate,
    CameraDayPresence,
    needs_calibration,
    summarize,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _trip(
    *,
    days: dict[int, tuple[date, int | None]],
) -> tuple[dict[int, date], dict[int, int | None]]:
    """Compact builder. ``days = {day_number: (date, tz_minutes_or_None)}``."""
    date_lookup = {n: d for n, (d, _) in days.items()}
    tz_lookup = {n: tz for n, (_, tz) in days.items()}
    return date_lookup, tz_lookup


def _presence(
    camera_id: str, day_number: int, *, is_phone: bool = False,
) -> CameraDayPresence:
    return CameraDayPresence(
        camera_id=camera_id, day_number=day_number, is_phone=is_phone,
    )


# --------------------------------------------------------------------------- #
# Empty cases
# --------------------------------------------------------------------------- #


def test_empty_presences_returns_no_candidates():
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup={},
        day_date_lookup={},
        presences=[],
        existing_offsets={},
    )
    assert out == []


def test_no_presences_for_an_existing_day_returns_no_candidates():
    """Day exists with foreign TZ, but no cameras → no calibration needed."""
    date_lookup, tz_lookup = _trip(days={1: (date(2026, 4, 1), 60)})
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[],
        existing_offsets={},
    )
    assert out == []


# --------------------------------------------------------------------------- #
# Home-TZ short-circuit (spec §8.2 condition 1)
# --------------------------------------------------------------------------- #


def test_day_at_home_tz_emits_no_candidate():
    """Even with a non-phone camera + no existing offset, a day whose TZ
    matches the home TZ doesn't need calibration."""
    date_lookup, tz_lookup = _trip(days={1: (date(2026, 4, 1), 0)})
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[_presence("DSC-RX100", 1)],
        existing_offsets={},
    )
    assert out == []


def test_negative_home_tz_handled_correctly():
    """São Paulo home (-180 min) → a day at -180 should NOT calibrate."""
    date_lookup, tz_lookup = _trip(days={1: (date(2026, 4, 1), -180)})
    out = needs_calibration(
        home_tz_minutes=-180,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[_presence("DSC-RX100", 1)],
        existing_offsets={},
    )
    assert out == []


# --------------------------------------------------------------------------- #
# Phone-camera skip (spec §8.2 condition 2)
# --------------------------------------------------------------------------- #


def test_phone_camera_never_emits_candidate():
    """Phones carry TZ in EXIF — no calibration needed even when the day
    is in a foreign TZ."""
    date_lookup, tz_lookup = _trip(days={1: (date(2026, 4, 1), 60)})
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[_presence("iPhone 15 Pro", 1, is_phone=True)],
        existing_offsets={},
    )
    assert out == []


def test_phone_and_camera_on_same_day_emits_camera_only():
    date_lookup, tz_lookup = _trip(days={1: (date(2026, 4, 1), 60)})
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[
            _presence("iPhone 15 Pro", 1, is_phone=True),
            _presence("DSC-RX100", 1),
        ],
        existing_offsets={},
    )
    assert len(out) == 1
    assert out[0].camera_id == "DSC-RX100"


# --------------------------------------------------------------------------- #
# Already-calibrated skip
# --------------------------------------------------------------------------- #


def test_already_calibrated_pair_is_skipped():
    """A (camera, day) row in existing_offsets means the user already
    answered — don't re-ask."""
    date_lookup, tz_lookup = _trip(days={1: (date(2026, 4, 1), 60)})
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[_presence("DSC-RX100", 1)],
        existing_offsets={("DSC-RX100", 1): 60},
    )
    assert out == []


def test_one_camera_calibrated_other_still_pending():
    """Two cameras on the same day; one's been calibrated already, the
    other hasn't."""
    date_lookup, tz_lookup = _trip(days={1: (date(2026, 4, 1), 60)})
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[
            _presence("DSC-RX100", 1),
            _presence("ILCE-7M5", 1),
        ],
        existing_offsets={("DSC-RX100", 1): 60},
    )
    assert len(out) == 1
    assert out[0].camera_id == "ILCE-7M5"


# --------------------------------------------------------------------------- #
# Missing-day-TZ skip
# --------------------------------------------------------------------------- #


def test_day_without_autofilled_tz_is_skipped():
    """A day with no phone autofill (tz_lookup → None) can't be checked.
    Don't emit a candidate — the host has no plausible default to seed,
    and forcing a user choice without phone EXIF defeats the §1.2 phone-
    is-ground-truth principle."""
    date_lookup, tz_lookup = _trip(days={1: (date(2026, 4, 1), None)})
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[_presence("DSC-RX100", 1)],
        existing_offsets={},
    )
    assert out == []


def test_day_not_in_date_lookup_is_skipped():
    """Defensive: a presence references a day_number missing from the
    date_lookup. Should skip without crashing."""
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup={1: 60},                                    # date_lookup missing 1
        day_date_lookup={},
        presences=[_presence("DSC-RX100", 1)],
        existing_offsets={},
    )
    assert out == []


# --------------------------------------------------------------------------- #
# Border-crossing — multi-day, same camera, different TZs (spec §8.4)
# --------------------------------------------------------------------------- #


def test_border_crossing_emits_one_candidate_per_day():
    """Trip crosses from PT to ES; same camera, different days, different
    TZs. Both days need calibration."""
    date_lookup, tz_lookup = _trip(days={
        1: (date(2026, 4, 1), 60),                                # Portugal
        2: (date(2026, 4, 2), 120),                               # Spain
    })
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[
            _presence("DSC-RX100", 1),
            _presence("DSC-RX100", 2),
        ],
        existing_offsets={},
    )
    assert len(out) == 2
    by_day = {c.day_number: c for c in out}
    assert by_day[1].day_tz_minutes == 60
    assert by_day[2].day_tz_minutes == 120


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #


def test_candidates_ordered_by_day_then_camera():
    date_lookup, tz_lookup = _trip(days={
        1: (date(2026, 4, 1), 60),
        2: (date(2026, 4, 2), 60),
    })
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[
            _presence("ZZZ", 2),
            _presence("AAA", 1),
            _presence("BBB", 1),
            _presence("AAA", 2),
        ],
        existing_offsets={},
    )
    keys = [(c.day_number, c.camera_id) for c in out]
    assert keys == [(1, "AAA"), (1, "BBB"), (2, "AAA"), (2, "ZZZ")]


# --------------------------------------------------------------------------- #
# Candidate carries the day's date
# --------------------------------------------------------------------------- #


def test_candidate_carries_date_for_display():
    date_lookup, tz_lookup = _trip(days={3: (date(2026, 4, 12), 60)})
    out = needs_calibration(
        home_tz_minutes=0,
        day_tz_lookup=tz_lookup,
        day_date_lookup=date_lookup,
        presences=[_presence("DSC-RX100", 3)],
        existing_offsets={},
    )
    assert len(out) == 1
    assert out[0].date == date(2026, 4, 12)
    assert out[0].day_number == 3
    assert out[0].day_tz_minutes == 60


# --------------------------------------------------------------------------- #
# summarize
# --------------------------------------------------------------------------- #


def test_summarize_empty_list():
    s = summarize([])
    assert s.total_pairs == 0
    assert s.distinct_days == 0
    assert s.distinct_cameras == 0
    assert s.is_empty


def test_summarize_counts_distinct_days_and_cameras():
    candidates = [
        CalibrationCandidate(
            camera_id="A", day_number=1, date=date(2026, 4, 1), day_tz_minutes=60),
        CalibrationCandidate(
            camera_id="B", day_number=1, date=date(2026, 4, 1), day_tz_minutes=60),
        CalibrationCandidate(
            camera_id="A", day_number=2, date=date(2026, 4, 2), day_tz_minutes=60),
    ]
    s = summarize(candidates)
    assert s.total_pairs == 3
    assert s.distinct_days == 2
    assert s.distinct_cameras == 2
    assert not s.is_empty
