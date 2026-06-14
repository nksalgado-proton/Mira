"""Unit tests for core.aspect_ratio."""

from __future__ import annotations

import pytest

from core.aspect_ratio import (
    ASPECT_RATIOS,
    ORIGINAL_LABEL,
    aspect_ratio_labels,
    get_aspect_ratio,
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


def test_labels_helper_matches_constants():
    labels = aspect_ratio_labels()
    assert labels[0] == ORIGINAL_LABEL
    assert len(labels) == len(ASPECT_RATIOS)
    assert all(label == ar.label for label, ar in zip(labels, ASPECT_RATIOS))
