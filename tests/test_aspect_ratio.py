"""Unit tests for core.aspect_ratio."""

from __future__ import annotations

import pytest

from core.aspect_ratio import (
    ASPECT_RATIOS,
    ORIGINAL_LABEL,
    aspect_ratio_labels,
    get_aspect_ratio,
    transpose_label,
)


def test_original_is_first_and_marked_as_such():
    assert ASPECT_RATIOS[0].label == ORIGINAL_LABEL
    assert ASPECT_RATIOS[0].is_original is True


def test_known_ratio_round_trips():
    for label in ("4:3", "3:2", "16:9", "1:1", "5:4"):
        ar = get_aspect_ratio(label)
        assert ar.label == label
        assert ar.is_original is False
        assert ar.value > 0


def test_unknown_label_falls_back_to_original():
    assert get_aspect_ratio("21:9").label == ORIGINAL_LABEL
    assert get_aspect_ratio("garbage").label == ORIGINAL_LABEL


def test_empty_or_missing_label_falls_back():
    assert get_aspect_ratio("").label == ORIGINAL_LABEL
    assert get_aspect_ratio(None).label == ORIGINAL_LABEL  # type: ignore[arg-type]


def test_values_match_label_math():
    assert get_aspect_ratio("4:3").value == pytest.approx(4 / 3)
    assert get_aspect_ratio("16:9").value == pytest.approx(16 / 9)
    assert get_aspect_ratio("1:1").value == 1.0


def test_transpose_swaps_orientation():
    """The Edit crop tool's ±90° action: swap a ratio's orientation. The
    transpose must itself be a registered ratio (so it resolves + persists
    + shows in the combo) and be involutive."""
    pairs = [("16:9", "9:16"), ("4:3", "3:4"), ("3:2", "2:3"), ("5:4", "4:5")]
    for landscape, portrait in pairs:
        assert transpose_label(landscape) == portrait
        assert transpose_label(portrait) == landscape          # involutive
        # Both directions resolve to real ratios with reciprocal values.
        assert get_aspect_ratio(portrait).value == pytest.approx(
            1.0 / get_aspect_ratio(landscape).value)


def test_transpose_is_noop_for_square_and_original():
    """A square or no-crop has no orientation to flip — transpose returns
    the same label so the caller can apply it unconditionally."""
    assert transpose_label("1:1") == "1:1"
    assert transpose_label(ORIGINAL_LABEL) == ORIGINAL_LABEL
    # An unknown label resolves to Original first, then no-ops.
    assert transpose_label("garbage") == ORIGINAL_LABEL


def test_labels_helper_matches_constants():
    labels = aspect_ratio_labels()
    assert labels[0] == ORIGINAL_LABEL
    assert len(labels) == len(ASPECT_RATIOS)
    assert all(label == ar.label for label, ar in zip(labels, ASPECT_RATIOS))
