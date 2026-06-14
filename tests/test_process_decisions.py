"""Tests for core.process_decisions — sidecar persistence helpers.

Pure-logic, no Qt; the journal is just a dict so tests round-trip
through the API and assert the in-memory shape.
"""

from __future__ import annotations

import pytest

from core.photo_render import Params
from core.process_decisions import (
    PROCESS_ASPECT_LABEL_KEY,
    PROCESS_DECISIONS_KEY,
    all_decision_names,
    clear_process_decision,
    get_process_aspect_label,
    get_process_crop,
    get_process_crop_angle,
    get_process_decision,
    get_process_params,
    set_process_aspect_label,
    set_process_crop,
    set_process_crop_angle,
    set_process_params,
)


# ── Per-bucket aspect label ─────────────────────────────────────


def test_aspect_label_missing_returns_none():
    assert get_process_aspect_label({}) is None


def test_aspect_label_round_trip():
    j = {}
    set_process_aspect_label(j, "16:9")
    assert get_process_aspect_label(j) == "16:9"
    assert j[PROCESS_ASPECT_LABEL_KEY] == "16:9"


def test_aspect_label_overwrites():
    j = {}
    set_process_aspect_label(j, "3:2")
    set_process_aspect_label(j, "4:3")
    assert get_process_aspect_label(j) == "4:3"


def test_aspect_label_empty_string_treated_as_none():
    """Defensive: a corrupt journal with an empty aspect string
    reads back as "no choice yet" so the page falls through to the
    settings default."""
    assert get_process_aspect_label(
        {PROCESS_ASPECT_LABEL_KEY: ""}) is None


# ── Per-photo decisions: round-trip ─────────────────────────────


def test_no_decision_returns_none():
    assert get_process_decision({}, "anything.jpg") is None
    assert get_process_params({}, "anything.jpg") is None
    assert get_process_crop({}, "anything.jpg") is None


def test_set_params_then_get_params_round_trip():
    j = {}
    p = Params(exposure=0.4, contrast=12.0, shadows=8.0)
    set_process_params(j, "a.jpg", p)
    got = get_process_params(j, "a.jpg")
    assert got == p


def test_set_crop_then_get_crop_round_trip():
    j = {}
    rect = (0.1, 0.2, 0.5, 0.6)
    set_process_crop(j, "a.jpg", rect)
    assert get_process_crop(j, "a.jpg") == rect


def test_params_and_crop_share_one_entry():
    """Setting both params + crop on the same photo writes to one
    sub-dict, not two — verified by reading the raw entry back."""
    j = {}
    p = Params(exposure=0.5)
    rect = (0.1, 0.2, 0.5, 0.6)
    set_process_params(j, "a.jpg", p)
    set_process_crop(j, "a.jpg", rect)
    entry = get_process_decision(j, "a.jpg")
    assert entry is not None
    assert "params" in entry
    assert "crop_norm" in entry
    # And reads come back independently.
    assert get_process_params(j, "a.jpg") == p
    assert get_process_crop(j, "a.jpg") == rect


def test_clear_drops_entire_entry():
    j = {}
    set_process_params(j, "a.jpg", Params(exposure=0.5))
    set_process_crop(j, "a.jpg", (0.1, 0.1, 0.8, 0.8))
    clear_process_decision(j, "a.jpg")
    assert get_process_decision(j, "a.jpg") is None
    assert get_process_params(j, "a.jpg") is None
    assert get_process_crop(j, "a.jpg") is None


def test_clear_unknown_name_is_noop():
    """Defensive: calling clear on a name with no decision must not
    raise — the caller doesn't need to pre-check."""
    j = {}
    clear_process_decision(j, "ghost.jpg")           # no exception


def test_all_decision_names_lists_only_touched_photos():
    j = {}
    set_process_params(j, "a.jpg", Params(exposure=0.5))
    set_process_crop(j, "b.jpg", (0.0, 0.0, 0.5, 0.5))
    # c.jpg never touched
    assert all_decision_names(j) == {"a.jpg", "b.jpg"}


# ── Defensive readers (corrupt / hand-edited journal) ──────────


def test_get_params_returns_none_on_garbled_entry():
    """A non-dict ``params`` (someone edited the journal by hand) →
    None, not a crash."""
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {"params": "not-a-dict"}}}
    assert get_process_params(j, "a.jpg") is None


def test_get_params_drops_unknown_keys():
    """Forward-compat: extra keys in a future schema version are
    silently dropped so the page keeps working."""
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {"params": {
        "exposure": 0.3, "future_unknown_key": 999,
    }}}}
    p = get_process_params(j, "a.jpg")
    assert p == Params(exposure=0.3)


def test_get_crop_returns_none_on_wrong_length():
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {"crop_norm": [0.1, 0.2]}}}
    assert get_process_crop(j, "a.jpg") is None


def test_get_crop_clamps_out_of_range_values():
    """A rect with values outside [0,1] (hand-edit, math drift) is
    clamped, never raised."""
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {
        "crop_norm": [-0.1, 0.5, 1.5, 0.5]}}}
    rect = get_process_crop(j, "a.jpg")
    assert rect is not None
    x, _, w, _ = rect
    assert x == 0.0
    assert w == 1.0


def test_get_crop_returns_none_on_garbage_value():
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {"crop_norm": "not-a-list"}}}
    assert get_process_crop(j, "a.jpg") is None


# ── Task #117 — crop_angle persistence ───────────────────────────


def test_crop_angle_missing_returns_none():
    """Old journals (and any photo the user hasn't touched the
    spinner on) carry no ``crop_angle`` — None signals "not
    recorded" so callers can distinguish that from an explicit 0.0."""
    j = {}
    assert get_process_crop_angle(j, "a.jpg") is None
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {"crop_norm": [0, 0, 1, 1]}}}
    assert get_process_crop_angle(j, "a.jpg") is None


def test_crop_angle_round_trip():
    j = {}
    set_process_crop_angle(j, "a.jpg", 3.5)
    assert get_process_crop_angle(j, "a.jpg") == 3.5
    set_process_crop_angle(j, "a.jpg", -7.25)
    assert get_process_crop_angle(j, "a.jpg") == -7.25


def test_crop_angle_clamps_extreme_values():
    """Hand-edited journal with an absurd angle must not crash the
    render pipeline — the reader clamps to ±360° (Box Rotation accepts
    any real angle; this is just the defensive ceiling)."""
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {"crop_angle": 9999.0}}}
    assert get_process_crop_angle(j, "a.jpg") == 360.0
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {"crop_angle": -9999.0}}}
    assert get_process_crop_angle(j, "a.jpg") == -360.0
    # A normal box rotation (e.g. 90°) passes through unclamped.
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {"crop_angle": 90.0}}}
    assert get_process_crop_angle(j, "a.jpg") == 90.0


def test_crop_angle_returns_none_on_garbage_value():
    """Defensive: a non-numeric ``crop_angle`` reads back as None,
    not a crash — caller treats it as "not recorded" and falls back
    to 0.0."""
    j = {PROCESS_DECISIONS_KEY: {"a.jpg": {"crop_angle": "not-a-number"}}}
    assert get_process_crop_angle(j, "a.jpg") is None


def test_crop_angle_coexists_with_crop_and_params():
    """The three sub-keys (params / crop_norm / crop_angle) live
    side by side in the same per-photo entry — setting one must
    not wipe the others."""
    j = {}
    set_process_crop(j, "a.jpg", (0.1, 0.2, 0.6, 0.6))
    set_process_crop_angle(j, "a.jpg", 4.2)
    set_process_params(j, "a.jpg", Params(exposure=0.5))
    assert get_process_crop(j, "a.jpg") == (0.1, 0.2, 0.6, 0.6)
    assert get_process_crop_angle(j, "a.jpg") == 4.2
    assert get_process_params(j, "a.jpg") == Params(exposure=0.5)


# ── Rotation (docs/25 §4) ──────────────────────────────────────


def test_rotation_round_trip():
    from core.process_decisions import (
        get_process_rotation,
        set_process_rotation,
    )
    j = {}
    assert get_process_rotation(j, "a.jpg") is None     # not recorded
    set_process_rotation(j, "a.jpg", 90)
    assert get_process_rotation(j, "a.jpg") == 90


def test_rotation_normalises_and_wraps():
    from core.process_decisions import (
        get_process_rotation,
        set_process_rotation,
    )
    j = {}
    set_process_rotation(j, "a.jpg", 450)               # → 90
    assert get_process_rotation(j, "a.jpg") == 90
    set_process_rotation(j, "b.jpg", -90)               # → 270
    assert get_process_rotation(j, "b.jpg") == 270
    set_process_rotation(j, "c.jpg", 360)               # → 0
    assert get_process_rotation(j, "c.jpg") == 0


# ── Exported tracking (docs/25 §9) ─────────────────────────────


def test_exported_tracking_round_trip():
    from core.process_decisions import (
        get_edit_exported,
        is_edit_exported,
        mark_process_exported as mark_edit_exported,
    )
    j = {}
    assert get_edit_exported(j) == set()
    assert is_edit_exported(j, "a.jpg") is False
    mark_edit_exported(j, ["a.jpg", "b.jpg"])
    assert get_edit_exported(j) == {"a.jpg", "b.jpg"}
    assert is_edit_exported(j, "a.jpg") is True


def test_exported_mark_is_idempotent_and_sorted():
    from core.process_decisions import (
        PROCESS_EXPORTED_KEY,
        get_edit_exported,
        mark_process_exported as mark_edit_exported,
    )
    j = {}
    mark_edit_exported(j, ["b.jpg"])
    mark_edit_exported(j, ["a.jpg", "b.jpg"])         # b again
    assert get_edit_exported(j) == {"a.jpg", "b.jpg"}
    assert j[PROCESS_EXPORTED_KEY] == ["a.jpg", "b.jpg"]  # sorted list


def test_exported_clear():
    from core.process_decisions import (
        clear_edit_exported,
        is_edit_exported,
        mark_process_exported as mark_edit_exported,
    )
    j = {}
    mark_edit_exported(j, ["a.jpg", "b.jpg"])
    clear_edit_exported(j, "a.jpg")
    assert is_edit_exported(j, "a.jpg") is False
    assert is_edit_exported(j, "b.jpg") is True
    clear_edit_exported(j, "missing.jpg")            # no-op, no raise


def test_params_round_trip_includes_vibrance():
    j = {}
    set_process_params(j, "a.jpg", Params(exposure=0.5, vibrance=15.0))
    got = get_process_params(j, "a.jpg")
    assert got == Params(exposure=0.5, vibrance=15.0)
