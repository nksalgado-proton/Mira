"""Tests for core.video_overrides — F-029 Step 5b (Nelson 2026-05-26).

Covers the per-bucket Process journal's `video_overrides` map:
get / set / remove / prune / apply round-trips, partial-update
preservation, defensive parsing of malformed entries, and the
ClipRange merge primitive used by Process Export.
"""

from __future__ import annotations

import pytest

from core.aspect_ratio import ORIGINAL_LABEL
from core.video_overrides import (
    OVERRIDES_KEY,
    VideoOverride,
    apply_override,
    get_override,
    list_overrides,
    prune_overrides,
    remove_override,
    set_override,
)
from core.video_session import ClipRange


# ── get_override + list_overrides ─────────────────────────────


def test_get_override_returns_none_for_unknown_id():
    journal: dict = {}
    assert get_override(journal, "c1") is None


def test_get_override_returns_none_for_malformed_entry():
    journal = {OVERRIDES_KEY: {"c1": "not a dict"}}
    assert get_override(journal, "c1") is None


def test_get_override_parses_all_fields():
    journal = {
        OVERRIDES_KEY: {
            "c1": {
                "include_audio": False,
                "rotation_degrees": 90,
                "aspect_ratio_label": "16:9",
                "crop_norm": [0.1, 0.2, 0.6, 0.7],
            }
        }
    }
    ov = get_override(journal, "c1")
    assert ov is not None
    assert ov.clip_id == "c1"
    assert ov.include_audio is False
    assert ov.rotation_degrees == 90
    assert ov.aspect_ratio_label == "16:9"
    assert ov.crop_norm == (0.1, 0.2, 0.6, 0.7)


def test_get_override_returns_partial_when_only_some_fields_set():
    journal = {OVERRIDES_KEY: {"c1": {"include_audio": True}}}
    ov = get_override(journal, "c1")
    assert ov is not None
    assert ov.include_audio is True
    assert ov.rotation_degrees is None
    assert ov.aspect_ratio_label is None
    assert ov.crop_norm is None


def test_get_override_drops_unparseable_field_values():
    """A bad value for one field doesn't poison the whole entry —
    the parser drops just that field and returns the rest."""
    journal = {
        OVERRIDES_KEY: {
            "c1": {
                "include_audio": True,
                "rotation_degrees": "ninety",       # nonsense
                "aspect_ratio_label": "",           # empty string
                "crop_norm": [0.1, 0.2],            # wrong arity
            }
        }
    }
    ov = get_override(journal, "c1")
    assert ov is not None
    assert ov.include_audio is True
    assert ov.rotation_degrees is None
    assert ov.aspect_ratio_label is None
    assert ov.crop_norm is None


def test_list_overrides_skips_malformed_entries():
    journal = {
        OVERRIDES_KEY: {
            "c1": {"include_audio": True},
            "c2": "stringly typed",
            "": {"include_audio": False},               # empty id dropped
        }
    }
    out = {ov.clip_id for ov in list_overrides(journal)}
    assert out == {"c1"}


def test_list_overrides_handles_missing_key():
    assert list_overrides({}) == []


def test_list_overrides_handles_non_dict_value():
    assert list_overrides({OVERRIDES_KEY: []}) == []


# ── set_override ──────────────────────────────────────────────


def test_set_override_creates_entry_when_missing():
    journal: dict = {}
    ov = set_override(journal, "c1", include_audio=False)
    assert ov.include_audio is False
    assert journal[OVERRIDES_KEY]["c1"]["include_audio"] is False


def test_set_override_partial_update_preserves_other_fields():
    journal: dict = {}
    set_override(journal, "c1", include_audio=False, rotation_degrees=90)
    set_override(journal, "c1", aspect_ratio_label="16:9")
    ov = get_override(journal, "c1")
    assert ov is not None
    assert ov.include_audio is False
    assert ov.rotation_degrees == 90
    assert ov.aspect_ratio_label == "16:9"


def test_set_override_clear_crop_norm_resets_to_none():
    journal: dict = {}
    set_override(journal, "c1", crop_norm=(0.1, 0.1, 0.8, 0.8))
    set_override(journal, "c1", clear_crop_norm=True)
    ov = get_override(journal, "c1")
    assert ov is not None
    assert ov.crop_norm is None


def test_set_override_clear_crop_norm_wins_over_explicit_crop_norm():
    """If a caller passes both clear_crop_norm=True and crop_norm=,
    clear wins — mirrors VideoSession.update_clip's contract."""
    journal: dict = {}
    set_override(
        journal, "c1",
        crop_norm=(0.1, 0.1, 0.8, 0.8),
        clear_crop_norm=True,
    )
    ov = get_override(journal, "c1")
    assert ov is not None
    assert ov.crop_norm is None


def test_set_override_normalises_rotation_modulo_360():
    journal: dict = {}
    set_override(journal, "c1", rotation_degrees=450)
    ov = get_override(journal, "c1")
    assert ov is not None
    assert ov.rotation_degrees == 90


def test_set_override_rejects_empty_clip_id():
    with pytest.raises(ValueError):
        set_override({}, "", include_audio=False)


# ── remove_override ──────────────────────────────────────────


def test_remove_override_returns_true_on_hit():
    journal: dict = {}
    set_override(journal, "c1", include_audio=False)
    assert remove_override(journal, "c1") is True
    assert get_override(journal, "c1") is None


def test_remove_override_returns_false_on_miss():
    journal: dict = {}
    assert remove_override(journal, "c1") is False


def test_remove_override_safe_with_no_overrides_key():
    assert remove_override({}, "c1") is False


# ── prune_overrides ───────────────────────────────────────────


def test_prune_overrides_drops_only_stale_ids():
    journal: dict = {}
    set_override(journal, "c1", include_audio=False)
    set_override(journal, "c2", rotation_degrees=90)
    set_override(journal, "c3", aspect_ratio_label="1:1")
    pruned = prune_overrides(journal, {"c1", "c3"})
    assert pruned == 1
    assert get_override(journal, "c1") is not None
    assert get_override(journal, "c2") is None
    assert get_override(journal, "c3") is not None


def test_prune_overrides_with_empty_journal_is_zero():
    assert prune_overrides({}, {"c1"}) == 0


def test_prune_overrides_with_all_valid_is_zero():
    journal: dict = {}
    set_override(journal, "c1", include_audio=True)
    assert prune_overrides(journal, {"c1"}) == 0
    assert get_override(journal, "c1") is not None


# ── apply_override (ClipRange merge) ─────────────────────────


def test_apply_override_mutates_none_fields_left_alone():
    clip = ClipRange(start_ms=0, end_ms=1000)        # defaults
    override = VideoOverride(
        clip_id="c1", include_audio=False, rotation_degrees=270,
    )
    apply_override(clip, override)
    assert clip.include_audio is False
    assert clip.rotation_degrees == 270
    # Unchanged defaults.
    assert clip.aspect_ratio_label == ORIGINAL_LABEL
    assert clip.crop_norm is None


def test_apply_override_writes_crop_norm_when_present():
    clip = ClipRange(start_ms=0, end_ms=1000)
    override = VideoOverride(
        clip_id="c1",
        crop_norm=(0.05, 0.05, 0.9, 0.9),
    )
    apply_override(clip, override)
    assert clip.crop_norm == (0.05, 0.05, 0.9, 0.9)


def test_apply_override_normalises_rotation():
    clip = ClipRange(start_ms=0, end_ms=1000)
    override = VideoOverride(clip_id="c1", rotation_degrees=720)
    apply_override(clip, override)
    assert clip.rotation_degrees == 0


def test_apply_override_with_empty_label_falls_back_to_original():
    """Defensive: an empty string label is meaningless. Apply
    normalises it to ORIGINAL_LABEL rather than propagating the
    empty value (which would confuse the encoder + the UI combo).
    Can't reach via set_override — `set_override` only stores
    truthy labels — but a hand-crafted journal or a future schema
    bump might. The override IS treated as "present" (overriding
    the prior "4:3") because override.aspect_ratio_label is not None."""
    clip = ClipRange(start_ms=0, end_ms=1000, aspect_ratio_label="4:3")
    override = VideoOverride(clip_id="c1", aspect_ratio_label="")
    apply_override(clip, override)
    assert clip.aspect_ratio_label == ORIGINAL_LABEL


def test_round_trip_set_get_apply():
    """End-to-end: set fields, parse back, apply to a fresh ClipRange.
    Pins the journal-shape↔ClipRange contract Process Export depends on."""
    journal: dict = {}
    set_override(
        journal, "c7",
        include_audio=False, rotation_degrees=180,
        aspect_ratio_label="9:16", crop_norm=(0.0, 0.1, 1.0, 0.8),
    )
    ov = get_override(journal, "c7")
    assert ov is not None
    clip = ClipRange(start_ms=100, end_ms=2500)
    apply_override(clip, ov)
    assert clip.include_audio is False
    assert clip.rotation_degrees == 180
    assert clip.aspect_ratio_label == "9:16"
    assert clip.crop_norm == (0.0, 0.1, 1.0, 0.8)


# ── docs/26 §8: video-in-Process colour/crop look + temporal tools ──

def test_set_get_colour_and_temporal_fields():
    from core.photo_render import Params
    journal: dict = {}
    p = Params(exposure=0.5, contrast=20, vibrance=15)
    set_override(
        journal, "c1",
        params=p, style="wildlife", auto_on=True, box_angle=18.0,
        rep_frame_ms=4200, trim_start_delta_ms=120, trim_end_delta_ms=-80,
        audio_volume=0.5, audio_fade_ms=300, speed=0.5, stabilise=40.0,
    )
    ov = get_override(journal, "c1")
    assert ov is not None
    assert ov.params == p                       # full Params round-trips
    assert ov.style == "wildlife"
    assert ov.auto_on is True
    assert ov.box_angle == 18.0
    assert ov.rep_frame_ms == 4200
    assert ov.trim_start_delta_ms == 120
    assert ov.trim_end_delta_ms == -80
    assert ov.audio_volume == 0.5
    assert ov.audio_fade_ms == 300
    assert ov.speed == 0.5
    assert ov.stabilise == 40.0
    assert ov.has_adjustment is True


def test_new_fields_default_none_and_partial_update():
    from core.photo_render import Params
    journal: dict = {}
    set_override(journal, "c2", include_audio=True)     # geometry only
    ov = get_override(journal, "c2")
    assert ov.params is None and ov.box_angle is None
    assert ov.has_adjustment is False
    # Partial update adds colour without disturbing include_audio.
    set_override(journal, "c2", params=Params(shadows=10), box_angle=90.0)
    ov = get_override(journal, "c2")
    assert ov.include_audio is True
    assert ov.params == Params(shadows=10)
    assert ov.box_angle == 90.0
    assert ov.has_adjustment is True


def test_apply_override_ignores_new_fields():
    """apply_override merges ONLY geometry+audio into ClipRange — the
    colour/temporal fields are read by the Phase-4 engine directly, not
    via ClipRange (so apply_override must not choke on them)."""
    from core.photo_render import Params
    journal: dict = {}
    set_override(journal, "c3", rotation_degrees=90, params=Params(exposure=1.0),
                 speed=2.0)
    ov = get_override(journal, "c3")
    clip = ClipRange(start_ms=0, end_ms=1000)
    apply_override(clip, ov)
    assert clip.rotation_degrees == 90
    assert not hasattr(clip, "params")          # untouched by the merge
