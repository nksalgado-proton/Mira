"""spec/152 §3 — the show length budget includes the crossfade
transition time PTE actually spends on every non-video slide.

Before spec/152 ``ShowTotals.seconds(photo_s)`` summed:

    (photo_count + separator_count) * photo_s + video_ms_total / 1000

with NO transition term. PTE's ``[Times]`` cumulative meanwhile added
``transition_ms`` to every photo / opener / separator slot (per
spec/150 §1: videos got ``clip_ms`` only). The budget undercounted
the show by ``(photos + seps + opener) * transition_s`` and the audio
playlist (built off the budget) ran short of the visible slideshow.

Pinned here:

* ``ShowTotals.seconds(photo_s)`` (old signature, no transition arg)
  still returns the legacy formula — back-compat with pre-152 callers.
* ``ShowTotals.seconds(photo_s, transition_s)`` adds
  ``transition_s`` to each photo / separator / opener slot, NOT to
  video slots (spec/150 §1 invariant preserved).
* ``ShowTotals.opener_count`` exists as a separate field; ``0`` =
  no opener slide (the typical no-separators-on case),
  ``1`` = opener will render. Both contribute symmetrically.
* The formula scales linearly with each count.
"""
from __future__ import annotations

import pytest

from core.cut_budget import ShowTotals


def test_legacy_signature_unchanged_no_transition_term():
    """A caller from before spec/152 (no second arg) still gets the
    pre-152 formula. The deprecation path matters because show-totals
    consumers are scattered across many surfaces and most stay on
    the legacy signature."""
    t = ShowTotals(photo_count=3, separator_count=2,
                   video_ms_total=10_000)
    # 3 photos + 2 separators = 5 photo-slots at 6 s = 30 s + 10 s
    # of video = 40 s.
    assert t.seconds(6.0) == pytest.approx(40.0)


def test_transition_seconds_add_to_non_video_slots_only():
    """spec/152 §3 — transition_s is added to every photo / separator
    / opener slot. Videos stay at clip_ms (spec/150 §1 invariant)."""
    t = ShowTotals(photo_count=3, separator_count=2,
                   video_ms_total=10_000)
    # Each photo / sep slot now = (6 + 2) = 8 s. 5 slots * 8 = 40 s.
    # Video unchanged at 10 s. Total = 50 s.
    assert t.seconds(6.0, 2.0) == pytest.approx(50.0)


def test_opener_counts_one_extra_slot():
    """spec/152 §3 — opener_count contributes one additional slot
    (photo_s + transition_s) when it's 1."""
    no_opener = ShowTotals(photo_count=2, separator_count=2,
                           video_ms_total=0, opener_count=0)
    with_opener = ShowTotals(photo_count=2, separator_count=2,
                             video_ms_total=0, opener_count=1)
    delta = with_opener.seconds(6.0, 2.0) - no_opener.seconds(6.0, 2.0)
    assert delta == pytest.approx(8.0), (
        "spec/152 §3: opener_count=1 must add (photo_s + "
        f"transition_s) = 8.0 s; got {delta}"
    )


def test_zero_transition_matches_pre_152_total():
    """``transition_s=0`` reverts the formula to the legacy total —
    the Settings ``default_transition_ms = 0`` "hard cuts" path."""
    t = ShowTotals(photo_count=4, separator_count=3,
                   video_ms_total=15_000, opener_count=1)
    # opener_count contributes a slot even at transition=0 (it's
    # still a slide — photo_s of it). 4 + 3 + 1 = 8 slots * 6 s = 48
    # + 15 video = 63.
    assert t.seconds(6.0, 0.0) == pytest.approx(63.0)


def test_video_only_show_is_unaffected_by_transition():
    """A photo-free Cut (videos only) spends no transition time —
    spec/150 §1 says video slots don't get the transition slot."""
    t = ShowTotals(photo_count=0, separator_count=0,
                   video_ms_total=30_000, opener_count=0)
    assert t.seconds(6.0, 0.0) == pytest.approx(30.0)
    assert t.seconds(6.0, 2.0) == pytest.approx(30.0)


def test_formula_pinned_with_explicit_arithmetic():
    """The spec/152 formula in long form:
    (photos + separators + opener_count) * (photo_s + transition_s)
    + video_ms_total / 1000."""
    t = ShowTotals(photo_count=5, separator_count=2,
                   video_ms_total=8_500, opener_count=1)
    photo_s = 7.0
    transition_s = 1.5
    expected = (5 + 2 + 1) * (7.0 + 1.5) + 8.5
    assert t.seconds(photo_s, transition_s) == pytest.approx(expected)
