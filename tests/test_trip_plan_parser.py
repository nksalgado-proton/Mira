"""Tests for trip plan parser."""

from datetime import date

from core.trip_plan_parser import format_trip_plan, parse_trip_plan


def test_parse_standard_format():
    text = """Dia 1 - Chegada em San José
Dia 2 - Vulcão Arenal
Dia 3 - Rio Celeste"""
    days = parse_trip_plan(text, date(2026, 7, 15), home_timezone=-3.0)
    assert len(days) == 3
    assert days[0].day_number == 1
    assert days[0].date == date(2026, 7, 15)
    assert days[0].description == "Chegada em San José"
    assert days[1].date == date(2026, 7, 16)
    assert days[2].date == date(2026, 7, 17)


def test_parse_with_header_lines():
    text = """Viagem: Costa Rica
Datas: 15/07/2026 - 25/07/2026

Dia 1 - Chegada
Dia 2 - Passeio"""
    days = parse_trip_plan(text, date(2026, 7, 15), home_timezone=-3.0)
    assert len(days) == 2


def test_parse_english_format():
    text = "Day 1 - Arrival\nDay 2 - Tour"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    assert len(days) == 2
    assert days[0].description == "Arrival"


def test_parse_number_only():
    text = "1 - Chegada\n2 - Passeio"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    assert len(days) == 2


def test_parse_number_dot():
    text = "1. Chegada\n2. Passeio"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    assert len(days) == 2


def test_parse_accepts_duplicate_day_numbers():
    """Same day_number can legitimately appear twice — e.g. a single
    calendar day split into morning + afternoon activities, or
    sequential narrative days that happen to share a calendar
    date (a redeye flight that crosses midnight). Both entries are
    kept; the wizard parser used to dedupe but Nepal-style trips
    need the freedom."""
    text = "Dia 1 - Primeira\nDia 1 - Duplicata\nDia 2 - Segunda"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    assert len(days) == 3
    assert [d.day_number for d in days] == [1, 1, 2]
    assert {d.description for d in days if d.day_number == 1} == {
        "Primeira", "Duplicata",
    }


def test_parse_sorts_by_day():
    text = "Dia 3 - Terceiro\nDia 1 - Primeiro\nDia 2 - Segundo"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    assert [d.day_number for d in days] == [1, 2, 3]


def test_parse_empty():
    assert parse_trip_plan("", date(2026, 1, 1)) == []
    assert parse_trip_plan("  \n  \n", date(2026, 1, 1)) == []


def test_format_roundtrip():
    """``format_trip_plan`` always emits the explicit
    ``(DD/MM/YYYY)`` date hint so non-sequential plans (gap days,
    narrative-day reuse) round-trip losslessly **and** a plan
    saved one calendar year re-imports correctly the next (B-024,
    Nelson 2026-05-26). Sequential plans pay a small visual cost
    for the consistency."""
    text = "Dia 1 - Chegada\nDia 2 - Passeio\nDia 3 - Retorno"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    formatted = format_trip_plan(days)
    # First day includes TZ since it's the first occurrence
    assert "Dia 1 - Chegada (01/01/2026) [TZ:-3]" in formatted
    assert "Dia 2 - Passeio (02/01/2026)" in formatted
    assert "Dia 3 - Retorno (03/01/2026)" in formatted


# ── Timezone tests ───────────────────────────────────────────────

def test_parse_timezone_notation():
    text = """Dia 1 - São Paulo
Dia 2 - Chegada em San José [TZ:-6]
Dia 3 - La Fortuna"""
    days = parse_trip_plan(text, date(2026, 7, 15), home_timezone=-3.0)
    assert days[0].tz_offset == -3.0
    assert days[1].tz_offset == -6.0
    assert days[2].tz_offset == -6.0  # inherits from day 2


def test_timezone_inheritance():
    text = """Dia 1 - Home
Dia 2 - Travel [TZ:-6]
Dia 3 - Stay
Dia 4 - Stay
Dia 5 - Return [TZ:-3]"""
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    assert days[0].tz_offset == -3.0  # home
    assert days[1].tz_offset == -6.0  # explicit
    assert days[2].tz_offset == -6.0  # inherit from 2
    assert days[3].tz_offset == -6.0  # inherit from 2
    assert days[4].tz_offset == -3.0  # explicit return


def test_timezone_positive():
    text = "Dia 1 - Lisbon [TZ:+1]"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    assert days[0].tz_offset == 1.0


def test_timezone_fractional():
    text = "Dia 1 - India [TZ:+5.5]"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    assert days[0].tz_offset == 5.5


def test_format_includes_tz_changes():
    text = """Dia 1 - Home
Dia 2 - Abroad [TZ:-6]
Dia 3 - Still abroad
Dia 4 - Return [TZ:-3]"""
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    formatted = format_trip_plan(days)
    assert "[TZ:-3]" in formatted.split("\n")[0]  # day 1 shows home TZ
    assert "[TZ:-6]" in formatted.split("\n")[1]  # day 2 shows change
    assert "[TZ:" not in formatted.split("\n")[2]  # day 3 inherits, no TZ
    assert "[TZ:-3]" in formatted.split("\n")[3]  # day 4 shows return


def test_no_tz_defaults_to_home():
    text = "Dia 1 - Local trip\nDia 2 - Still local"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    assert days[0].tz_offset == -3.0
    assert days[1].tz_offset == -3.0


# ── Location parsing ─────────────────────────────────────────────


def test_location_tag_is_extracted_and_stripped_from_description():
    """[LOC:..] should be stored on the TripDay and removed from
    the visible description."""
    text = "Dia 1 - Chegada a La Fortuna [LOC:La Fortuna]"
    days = parse_trip_plan(text, date(2026, 4, 1))
    assert days[0].location == "La Fortuna"
    assert days[0].description == "Chegada a La Fortuna"
    assert "[LOC" not in days[0].description


def test_location_with_multiple_words_and_accents():
    text = "Dia 1 - Visita ao Vulcão Arenal [LOC:Vulcão Arenal]"
    days = parse_trip_plan(text, date(2026, 4, 1))
    assert days[0].location == "Vulcão Arenal"


def test_location_and_tz_can_appear_in_either_order():
    text_a = "Dia 1 - desc [LOC:La Fortuna] [TZ:-6]"
    text_b = "Dia 1 - desc [TZ:-6] [LOC:La Fortuna]"
    a = parse_trip_plan(text_a, date(2026, 4, 1))
    b = parse_trip_plan(text_b, date(2026, 4, 1))
    assert a[0].location == b[0].location == "La Fortuna"
    assert a[0].tz_offset == b[0].tz_offset == -6
    assert a[0].description == b[0].description == "desc"


def test_no_location_tag_means_none():
    text = "Dia 1 - Just a regular day"
    days = parse_trip_plan(text, date(2026, 4, 1))
    assert days[0].location is None


def test_empty_location_brackets_are_dropped():
    """``[LOC:]`` with nothing inside is treated as no location —
    but the tag still gets stripped so the description doesn't
    show garbage."""
    text = "Dia 1 - desc [LOC:]"
    days = parse_trip_plan(text, date(2026, 4, 1))
    assert days[0].location is None
    assert days[0].description == "desc"


def test_format_round_trips_location():
    text = """Dia 1 - Home base [LOC:São Paulo]
Dia 2 - Travel [LOC:San José] [TZ:-6]
Dia 3 - La Fortuna [LOC:La Fortuna]"""
    days = parse_trip_plan(text, date(2026, 4, 1), home_timezone=-3.0)
    formatted = format_trip_plan(days)
    assert "[LOC:São Paulo]" in formatted
    assert "[LOC:San José]" in formatted
    assert "[LOC:La Fortuna]" in formatted


def test_format_omits_location_when_none():
    text = "Dia 1 - No location set"
    days = parse_trip_plan(text, date(2026, 4, 1), home_timezone=-3.0)
    formatted = format_trip_plan(days)
    assert "[LOC" not in formatted


# ── Explicit date hints ──────────────────────────────────────────


def test_explicit_date_overrides_sequential():
    """``(DD/MM)`` after the description sets the day's date
    explicitly, overriding the ``start_date + (N - 1)`` fallback.
    Lets a plan have non-sequential calendar dates per day."""
    text = "Dia 1 - Katmandu (26/10) [TZ:+5.75]\nDia 2 - Lukla (29/10)"
    days = parse_trip_plan(text, date(2025, 10, 26), home_timezone=-3.0)
    assert days[0].date == date(2025, 10, 26)
    assert days[1].date == date(2025, 10, 29)  # 3-day jump preserved
    # Description does NOT include the parenthetical
    assert days[0].description == "Katmandu"
    assert days[1].description == "Lukla"


def test_explicit_date_with_year_in_hint():
    """``(DD/MM/YYYY)`` is also accepted — useful for trips that
    cross a year boundary where the inferred year would be
    ambiguous."""
    text = "Dia 1 - NYE (31/12/2025)\nDia 2 - NYD (01/01/2026)"
    days = parse_trip_plan(text, date(2025, 12, 31), home_timezone=0.0)
    assert days[0].date == date(2025, 12, 31)
    assert days[1].date == date(2026, 1, 1)


def test_explicit_date_year_inferred_from_start_date():
    """When the hint omits the year, it's filled in from
    ``start_date.year``."""
    text = "Dia 1 - Day one (15/06)"
    days = parse_trip_plan(text, date(2024, 6, 15), home_timezone=-3.0)
    assert days[0].date == date(2024, 6, 15)


def test_explicit_date_year_inferred_from_other_hints_when_no_start_date():
    """Without ``start_date``, year inference falls back to any
    line that DID specify a full date. Lets the user write the
    year just on Day 1 and keep DD/MM thereafter."""
    text = "Dia 1 - One (15/06/2024)\nDia 2 - Two (16/06)"
    days = parse_trip_plan(text)
    assert days[0].date == date(2024, 6, 15)
    assert days[1].date == date(2024, 6, 16)


def test_no_start_date_and_no_explicit_dates_raises():
    """Can't compute a calendar from nothing."""
    import pytest
    text = "Dia 1 - Placeholder"
    with pytest.raises(ValueError):
        parse_trip_plan(text)


def test_duplicate_day_numbers_with_different_dates_both_kept():
    """Nepal narrative: ``Dia 7 - EBC Flight (03/11)`` and
    ``Dia 8 - Lukla a Kathmandu (03/11)`` both happen on the same
    calendar day. The parser keeps both as distinct TripDay objects
    — downstream consumers handle the same-date pair however they
    want (slideshow folders distinguish by day_number)."""
    text = (
        "Dia 7 - EBC Flight (03/11)\n"
        "Dia 8 - Lukla a Kathmandu (03/11)"
    )
    days = parse_trip_plan(text, date(2025, 11, 3), home_timezone=5.75)
    assert len(days) == 2
    assert all(d.date == date(2025, 11, 3) for d in days)
    assert {d.day_number for d in days} == {7, 8}


def test_comments_starting_with_hash_are_ignored():
    """Skeleton emits ``# no anchor — date inferred`` comments;
    the parser must ignore those when reading user-edited
    versions."""
    text = (
        "Dia 1 - Katmandu (26/10) [TZ:+5.75]\n"
        "# this is a comment line, ignored\n"
        "Dia 2 - Lukla (29/10)   # trailing comment also ignored"
    )
    days = parse_trip_plan(text, date(2025, 10, 26))
    assert len(days) == 2
    assert days[0].description == "Katmandu"
    assert days[1].description == "Lukla"


def test_hash_inside_loc_is_preserved_as_transport_marker():
    """Task #127 — the ``[LOC:A > B # bus]`` transport-mode marker
    survives the comment-stripping pass. Previously the parser
    naively split on the first ``#``, gutting any LOC value with a
    transport marker. The bracket-aware comment-strip preserves
    them.

    Pin both forms: LOC with ``#`` mid-bracket and LOC followed by
    a real trailing ``#``-comment outside the bracket — the bracket
    contents survive while the post-bracket comment is dropped.
    """
    text = (
        "Dia 1 - Drive (26/10) [LOC:San Jose > La Fortuna # car]\n"
        "Dia 2 - Walk (27/10) [LOC:Kathmandu # walking] # trailing comment"
    )
    days = parse_trip_plan(text, date(2025, 10, 26))
    assert len(days) == 2
    assert days[0].location == "San Jose > La Fortuna # car"
    assert days[1].location == "Kathmandu # walking"
    assert days[1].description == "Walk"   # trailing comment stripped


def test_invalid_date_hint_falls_back_to_sequential():
    """An impossible date like ``(31/02)`` should be ignored as a
    hint (and ideally remain in the description for the user to
    fix), with the sequential fallback used for the actual date."""
    text = "Dia 5 - Day five (31/02)"
    days = parse_trip_plan(text, date(2026, 1, 1), home_timezone=-3.0)
    # Sequential fallback: start + 4 days = Jan 5
    assert days[0].date == date(2026, 1, 5)


def test_format_emits_explicit_dates():
    """``format_trip_plan`` always emits ``(DD/MM/YYYY)`` so a
    parse → format → parse cycle preserves explicit dates AND
    survives a year boundary (B-024)."""
    text = (
        "Dia 1 - Katmandu (26/10) [TZ:+5.75]\n"
        "Dia 2 - Lukla (29/10)"
    )
    days = parse_trip_plan(text, date(2025, 10, 26))
    formatted = format_trip_plan(days)
    # Writer emits the year (B-024); the parser tolerates both
    # year-bearing and year-less import forms.
    assert "(26/10/2025)" in formatted
    assert "(29/10/2025)" in formatted
    # Round trip preserves the gap AND the year — even if the
    # round-trip happens in a different calendar year (which would
    # have re-anchored the year-less form to "today's year").
    days2 = parse_trip_plan(formatted, date(2025, 10, 26))
    assert days2[0].date == date(2025, 10, 26)
    assert days2[1].date == date(2025, 10, 29)


def test_b024_save_load_survives_year_boundary():
    """B-024 (Nelson 2026-05-26) — headline regression: a plan
    saved in calendar year N must re-import correctly when
    reopened in year N+M. The year-less ``(DD/MM)`` form would
    silently re-anchor against the importer's reference year
    (``start_date`` or today), landing dates in the wrong year.
    The year-bearing ``(DD/MM/YYYY)`` form survives.
    """
    # Author the plan as a user typically does — only specifies
    # description, parser fills dates from start_date.
    original_text = "Dia 1 - Chegada\nDia 2 - Trilha\nDia 3 - Retorno"
    days = parse_trip_plan(
        original_text, date(2026, 1, 1), home_timezone=-3.0,
    )
    # Save → text on disk now carries the year.
    saved = format_trip_plan(days)
    assert "(01/01/2026)" in saved
    # Re-import in a much later year, with NO start_date hint.
    # The year-bearing form holds; without it the parser would
    # have re-anchored to year 2099.
    reloaded = parse_trip_plan(saved, date(2099, 1, 1))
    assert reloaded[0].date == date(2026, 1, 1)
    assert reloaded[1].date == date(2026, 1, 2)
    assert reloaded[2].date == date(2026, 1, 3)


def test_parse_falls_back_to_current_year_when_no_year_anywhere():
    """User-friendly default: plan with only ``(DD/MM)`` entries and
    no ``start_date`` parameter should not yield year-1 dates. The
    parser substitutes the current calendar year.

    Costa Rica case 2026-05-14: Nelson's file uses ``(12/04)`` everywhere
    and the dialog called parse_trip_plan() without a start_date —
    the dates were coming out as year 1 (clamped to 2000 by the
    QDateEdit min-date floor)."""
    text = (
        "Dia 1 - Chegada em San José (12/04) [LOC:San José] [TZ:-6]\n"
        "Dia 2 - Drake Bay (13/04) [LOC:Drake Bay]\n"
        "Dia 3 - Cano Island (14/04) [LOC:Cano Island]"
    )
    days = parse_trip_plan(text)   # start_date=None, no year on any line
    this_year = date.today().year
    assert days[0].date == date(this_year, 4, 12)
    assert days[1].date == date(this_year, 4, 13)
    assert days[2].date == date(this_year, 4, 14)


def test_parse_prefers_explicit_year_over_current_year():
    """When a line in the plan does include a year, it wins over the
    current-year fallback for the remaining year-less lines."""
    text = (
        "Dia 1 - Día uno (12/04/2024)\n"
        "Dia 2 - Día dos (13/04)\n"   # year-less → inherits 2024
        "Dia 3 - Día tres (14/04)"
    )
    days = parse_trip_plan(text)
    assert days[0].date == date(2024, 4, 12)
    assert days[1].date == date(2024, 4, 13)
    assert days[2].date == date(2024, 4, 14)
