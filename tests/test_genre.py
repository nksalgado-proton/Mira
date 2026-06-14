"""Tests for core.genre (E8b core — classify + journal override)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.classifier_v2 import ClassificationResult, Scenario
from core.genre import (
    all_scenarios,
    cached_auto_genre,
    classify_exif,
    clear_genre_override,
    effective_genre,
    genre_label,
    genre_tooltip,
    get_genre_override,
    peek_auto_genre,
    reset_rules_cache,
    set_genre_override,
)


def _res(scn: Scenario, conf: float) -> ClassificationResult:
    return ClassificationResult(
        scenario=scn, confidence=conf, reason="", rule_id=None,
        source="camera",  # type: ignore[arg-type]
    )


# ── sticky override (sparse journal) ─────────────────────────────


def test_override_set_get_clear_sparse():
    j: dict = {}
    assert get_genre_override(j, "a.rw2") is None
    set_genre_override(j, "a.rw2", Scenario.WILDLIFE.value)
    assert get_genre_override(j, "a.rw2") == "wildlife"
    assert j["genre"] == {"a.rw2": "wildlife"}      # sparse
    clear_genre_override(j, "a.rw2")
    assert get_genre_override(j, "a.rw2") is None
    assert j["genre"] == {}


def test_override_rejects_unknown_scenario():
    with pytest.raises(ValueError):
        set_genre_override({}, "x.jpg", "not_a_genre")


# ── auto-classification cache (perf, like sharpness) ─────────────


def test_cached_auto_genre_computes_once_then_caches():
    j: dict = {}
    calls = {"n": 0}

    def compute() -> ClassificationResult:
        calls["n"] += 1
        return _res(Scenario.MACRO, 0.95)

    assert cached_auto_genre(j, "m.rw2", compute) == ("macro", False)
    assert cached_auto_genre(j, "m.rw2", compute) == ("macro", False)
    assert calls["n"] == 1                           # computed once
    # Low confidence → needs_review True propagates + caches.
    assert cached_auto_genre(
        j, "g.rw2", lambda: _res(Scenario.GENERAL, 0.0)
    ) == ("general", True)


def test_peek_auto_genre_never_computes():
    j: dict = {}
    assert peek_auto_genre(j, "x.rw2") is None       # nothing cached
    cached_auto_genre(j, "x.rw2", lambda: _res(Scenario.PORTRAIT, 0.9))
    assert peek_auto_genre(j, "x.rw2") == ("portrait", False)


def test_effective_genre_is_override_else_auto():
    j: dict = {}
    assert effective_genre(j, "p.rw2", "landscape") == "landscape"
    set_genre_override(j, "p.rw2", Scenario.WILDLIFE.value)
    assert effective_genre(j, "p.rw2", "landscape") == "wildlife"


# ── labels ───────────────────────────────────────────────────────


def test_labels_and_scenarios():
    assert genre_label("focus_bracket") == "Focus Bracket"
    assert genre_label("wildlife") == "Wildlife"
    assert "wildlife" in all_scenarios()
    assert genre_tooltip("macro")                    # non-empty desc
    assert genre_tooltip("bogus") == ""              # safe on junk


# ── classify_exif: never raises, sane fallback ───────────────────


def test_classify_exif_empty_is_general_not_raises():
    res = classify_exif(Path("nope.rw2"), {})
    assert isinstance(res, ClassificationResult)
    assert res.scenario == Scenario.GENERAL          # graceful fallback


def test_classify_exif_real_panasonic_runs():
    """A realistic Panasonic EXIF classifies without raising and
    returns a valid Scenario (exact genre depends on the built-in
    rules — we assert it ran, not which)."""
    exif = {
        "Make": "Panasonic", "Model": "DC-G9M2",
        "FocalLength": 300.0, "FNumber": 4.0, "ISO": 800,
        "FocusMode": "AF-C", "AFAreaMode": "Tracking",
    }
    res = classify_exif(Path("P1.RW2"), exif)
    assert isinstance(res.scenario, Scenario)


def test_reset_rules_cache_is_safe():
    classify_exif(Path("a.rw2"), {})                 # warms cache
    reset_rules_cache()                              # must not raise
    res = classify_exif(Path("b.rw2"), {})
    assert isinstance(res, ClassificationResult)


# ── 00.090: cache invalidation when rules version changes ────────


def test_cache_entry_stamped_with_version_and_source():
    """Every cache write records the rules version + source so the
    entry can be validated on later reads."""
    from core.genre import _rules_version

    j: dict = {}
    cached_auto_genre(j, "x.rw2", lambda: _res(Scenario.WILDLIFE, 0.9))
    entry = j["genre_auto"]["x.rw2"]
    # All four fields present.
    assert entry["s"] == "wildlife"
    assert entry["r"] is False
    assert entry["src"] == "camera"               # from _res default
    assert entry["v"] == _rules_version("camera")


def test_legacy_entry_without_stamps_is_stale_and_recomputes():
    """Cache entries written before 00.090 had only s + r (no v, no
    src). The new validator treats them as stale → cached_auto_genre
    recomputes + re-stamps; peek_auto_genre returns None."""
    j: dict = {"genre_auto": {"a.rw2": {"s": "general", "r": True}}}
    # peek refuses to use the un-stamped entry.
    assert peek_auto_genre(j, "a.rw2") is None
    # cached recomputes and re-stamps with the correct genre.
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return _res(Scenario.WILDLIFE, 0.95)
    s, r = cached_auto_genre(j, "a.rw2", compute)
    assert calls["n"] == 1                          # forced recompute
    assert s == "wildlife"
    # And the new entry IS stamped, so the next read is a cache hit.
    assert peek_auto_genre(j, "a.rw2") == ("wildlife", False)
    assert calls["n"] == 1                          # no second compute


def test_stale_version_stamp_is_invalidated():
    """An entry stamped with an older rules version (e.g. journal
    written under v5 then app upgraded to v6) gets invalidated and
    recomputes."""
    from core.genre import _rules_version

    current = _rules_version("camera")
    j: dict = {"genre_auto": {"a.rw2": {
        "s": "wildlife", "r": False,
        "v": current - 1,                          # stale
        "src": "camera",
    }}}
    assert peek_auto_genre(j, "a.rw2") is None     # invalidated
    s, r = cached_auto_genre(
        j, "a.rw2", lambda: _res(Scenario.PORTRAIT, 0.9))
    assert s == "portrait"                          # recomputed
    assert j["genre_auto"]["a.rw2"]["v"] == current


def test_phone_source_uses_phone_rules_version():
    """When a phone classification is cached, the stamp records
    ``src=phone`` and the phone rules version — NOT the camera
    version. Catches the bug where a phone cache entry would be
    invalidated whenever the camera rules bump."""
    from core.genre import _rules_version

    def compute():
        return ClassificationResult(
            scenario=Scenario.SELFIE, confidence=0.95,
            reason="", rule_id="phone_selfie",
            source="phone",  # type: ignore[arg-type]
        )
    j: dict = {}
    cached_auto_genre(j, "x.jpg", compute)
    entry = j["genre_auto"]["x.jpg"]
    assert entry["src"] == "phone"
    assert entry["v"] == _rules_version("phone")


def test_classify_exif_auto_derives_phone_source_from_body():
    """Without explicit ``source=``, classify_exif should detect an
    iPhone via body.kind and route to phone rules → phone_selfie
    fires for a front-camera shot.

    Regression for the UI culler bug where every classify_exif call
    in the codebase defaulted to source='camera', so phone rules
    NEVER fired in the UI even though they fired correctly in the
    CLI tools that passed source='phone' explicitly."""
    exif = {
        "Make": "Apple",
        "Model": "iPhone 11",
        "LensModel": "iPhone 11 front camera 2.71mm f/2.2",
        "LensInfo": "2.71mm f/2.2",
        "FocalLength": 2.71,
        "FNumber": 2.2,
    }
    res = classify_exif(Path("selfie.jpeg"), exif)
    # Phone rules ran (no explicit source kwarg).
    assert res.scenario == Scenario.SELFIE
    assert res.rule_id == "phone_selfie"
    assert res.source == "phone"


def test_classify_exif_auto_derives_camera_source_from_body():
    """Companion to the phone case — a Panasonic shot derives
    source=camera and runs camera rules."""
    exif = {
        "Make": "Panasonic", "Model": "DC-G9M2",
        "LensModel": "LUMIX G 35-100/F2.8",
        "FocalLength": 100.0, "FNumber": 4.0, "ISO": 800,
        "FocusMode": "AF-C",
    }
    res = classify_exif(Path("g9.rw2"), exif)
    assert res.source == "camera"


# ── Bucket-level style (docs/18 §"Bucket cull surfaces") ─────────


from core.genre import (  # noqa: E402
    bucket_style_tiebreak,
    clear_bucket_genre_override,
    dominant_scenario,
    effective_bucket_style,
    get_bucket_genre_override,
    set_bucket_genre_override,
)


def test_settings_default_preferred_burst_genre_is_wildlife():
    from core.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["preferred_burst_genre"] == "wildlife"


def test_bucket_override_set_get_clear():
    j: dict = {}
    assert get_bucket_genre_override(j) is None
    set_bucket_genre_override(j, "macro")
    assert get_bucket_genre_override(j) == "macro"
    clear_bucket_genre_override(j)
    assert get_bucket_genre_override(j) is None


def test_bucket_override_rejects_unknown():
    with pytest.raises(ValueError):
        set_bucket_genre_override({}, "not_a_scenario")


def test_dominant_scenario_rules():
    assert dominant_scenario([]) is None              # empty
    assert dominant_scenario(["general", "general"]) is None  # all GENERAL
    assert dominant_scenario(["macro", "macro", "general"]) == "macro"
    assert dominant_scenario(["macro", "wildlife"]) is None    # tie
    # GENERAL ignored, not counted as a competitor.
    assert dominant_scenario(["macro", "general", "general"]) == "macro"


def test_bucket_style_tiebreak_table():
    assert bucket_style_tiebreak(
        "video", preferred_burst_genre="wildlife") == "general"
    assert bucket_style_tiebreak(
        "focus_bracket", preferred_burst_genre="wildlife") == "macro"
    assert bucket_style_tiebreak(
        "exposure_bracket", preferred_burst_genre="wildlife"
    ) == "landscape"
    assert bucket_style_tiebreak(
        "burst", preferred_burst_genre="sports") == "sports"
    assert bucket_style_tiebreak(
        "unknown_kind", preferred_burst_genre="wildlife") == "general"


def test_effective_bucket_style_precedence():
    # tie-breaker when ambiguous (all GENERAL).
    assert effective_bucket_style(
        {}, "focus_bracket", ["general", "general"],
        preferred_burst_genre="wildlife",
    ) == "macro"
    # dominant beats the tie-breaker.
    assert effective_bucket_style(
        {}, "focus_bracket", ["macro", "macro", "wildlife"],
        preferred_burst_genre="wildlife",
    ) == "macro"
    # bucket override beats everything.
    j = {}
    set_bucket_genre_override(j, "portrait")
    assert effective_bucket_style(
        j, "focus_bracket", ["macro", "macro"],
        preferred_burst_genre="wildlife",
    ) == "portrait"
    # burst → the preferred-genre tie-breaker when ambiguous.
    assert effective_bucket_style(
        {}, "burst", [], preferred_burst_genre="sports",
    ) == "sports"
