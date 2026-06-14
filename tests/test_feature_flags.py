"""Tests for ``core.feature_flags`` — the runtime "lego assembly" (spec/53 §2.7).

Logic-only (no Qt). Covers: the closed v1 flag-key vocabulary, the per-profile
DEFAULTS_BY_PROFILE map, the load_flags precedence (user > install_profile >
default), the Flags dataclass shape + ``is_on`` accessor, and the missing-
profile-row fallback.

Mirrors :mod:`tests.test_user_store` discipline — uses the fresh UserStore
fixture pattern so flag tests run against a real mira.db.
"""
from __future__ import annotations

import pytest

from core import feature_flags
from core.feature_flags import (
    DEFAULTS_BY_PROFILE,
    FLAG_KEYS,
    Flags,
    default_for,
    load_flags,
)
from mira.user_store import models as m
from mira.user_store.repo import UserStore


def _store(tmp_path) -> UserStore:
    return UserStore.create(tmp_path / "mira.db", app_version="test")


NOW = "2026-06-08T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Flag-key vocabulary
# --------------------------------------------------------------------------- #


def test_flag_keys_match_spec_53_section_2_7_1():
    """Lock the v1 flag-key set. Adding/removing keys is a deliberate spec/53
    decision; this test surfaces the change."""
    assert FLAG_KEYS == frozenset({
        "feature.cross_event_cuts",
        "feature.tz_correction",
        "feature.quick_sweep",
        "feature.video_clips_snapshots",
        "feature.third_party_roundtrip",
        "feature.audio_export",
        "feature.maps",
        "feature.collages",
        "feature.people_tagging",
        "feature.bracket_detection",
        "feature.bracket_stacking",
        "feature.wizard_custom_rules",
        "feature.advanced_pick_overlays",
        "feature.plan_save_load_csv",
        "feature.advanced_edit_controls",
        "feature.event_lifecycle_close",
        "feature.detailed_event_types",
    })


def test_flags_dataclass_has_attribute_for_every_key():
    """The Flags dataclass must carry one attribute per FLAG_KEYS entry (with
    the ``feature.`` prefix stripped). Drift here breaks load_flags."""
    expected_attrs = {key.removeprefix("feature.") for key in FLAG_KEYS}
    actual_attrs = {f.name for f in feature_flags.Flags.__dataclass_fields__.values()}
    assert expected_attrs == actual_attrs


# --------------------------------------------------------------------------- #
# DEFAULTS_BY_PROFILE
# --------------------------------------------------------------------------- #


def test_xmc_defaults_all_on():
    """XMC bundle — every Premium gate is on by default."""
    assert all(DEFAULTS_BY_PROFILE["XMC"].values())
    # Every FLAG_KEYS entry must be present.
    assert set(DEFAULTS_BY_PROFILE["XMC"]) == FLAG_KEYS


def test_mc_defaults_all_off():
    """MC bundle — every Premium gate is off by default."""
    assert not any(DEFAULTS_BY_PROFILE["MC"].values())
    assert set(DEFAULTS_BY_PROFILE["MC"]) == FLAG_KEYS


def test_custom_defaults_match_xmc():
    """``custom`` bundle falls back to the enthusiast defaults so the user
    opts out of features individually via the Settings dialog."""
    assert DEFAULTS_BY_PROFILE["custom"] == DEFAULTS_BY_PROFILE["XMC"]


def test_default_for_known_key_known_profile():
    assert default_for("feature.cross_event_cuts", "XMC") is True
    assert default_for("feature.cross_event_cuts", "MC") is False


def test_default_for_unknown_key_returns_false():
    """A typo in the key returns False — it is NOT a Premium gate, so don't
    pretend it's "on by default"."""
    assert default_for("feature.totally_made_up", "XMC") is False


def test_default_for_unknown_profile_falls_back_to_xmc():
    """An unknown / corrupted profile name still gets the most generous bundle
    (defence-in-depth — user can still opt out via explicit toggles)."""
    assert default_for("feature.cross_event_cuts", "INVALID") is True


# --------------------------------------------------------------------------- #
# load_flags precedence — user > install_profile > default
# --------------------------------------------------------------------------- #


def test_load_flags_no_profile_row_defaults_to_xmc(tmp_path):
    """A brand-new store with no installation_profile row yet (the order of
    operations in the first-launch importer may not have stamped it) still
    produces a sensible Flags object via the XMC fallback."""
    store = _store(tmp_path)
    try:
        flags = load_flags(store)
        # XMC fallback — everything on.
        assert flags.cross_event_cuts is True
        assert flags.maps is True
    finally:
        store.close()


def test_load_flags_uses_profile_defaults(tmp_path):
    """No feature_flag rows — every value comes from the profile defaults."""
    store = _store(tmp_path)
    try:
        store.upsert(m.InstallationProfile(name="MC", created_at=NOW))
        flags = load_flags(store)
        # MC profile — every Premium gate off.
        assert flags.cross_event_cuts is False
        assert flags.maps is False
        assert flags.bracket_detection is False
    finally:
        store.close()


def test_load_flags_install_profile_row_overrides_default(tmp_path):
    """An explicit install_profile row beats the coded default. Lets an MC
    install opt INTO one Premium feature without flipping the whole profile."""
    store = _store(tmp_path)
    try:
        store.upsert(m.InstallationProfile(name="MC", created_at=NOW))
        # MC default is off — an install_profile row turns one feature on.
        store.upsert(m.FeatureFlag(
            key="feature.tz_correction",
            enabled=True, source="install_profile", set_at=NOW,
        ))
        flags = load_flags(store)
        assert flags.tz_correction is True
        # Others stay at MC default (off).
        assert flags.cross_event_cuts is False
    finally:
        store.close()


def test_load_flags_user_row_overrides_install_profile_row(tmp_path):
    """A user toggle (rare; restart-required) takes precedence over both
    coded default and the installer-written row."""
    store = _store(tmp_path)
    try:
        store.upsert(m.InstallationProfile(name="XMC", created_at=NOW))
        # XMC default on; installer wrote an explicit "on"; user turned it off.
        store.upsert(m.FeatureFlag(
            key="feature.advanced_edit_controls",
            enabled=True, source="install_profile", set_at=NOW,
        ))
        store.upsert(m.FeatureFlag(
            key="feature.advanced_edit_controls",
            enabled=False, source="user", set_at=NOW,
        ))
        # NB: a real install would not have two rows under the same PK — the
        # PK collapses; the test exercises the "user row replaces
        # install_profile row" upsert behaviour deliberately.
        flags = load_flags(store)
        assert flags.advanced_edit_controls is False
    finally:
        store.close()


def test_load_flags_ignores_unknown_keys_in_db(tmp_path):
    """A stale / hand-edited feature_flag row with a key not in FLAG_KEYS is
    silently ignored — the Flags object is built from the closed vocabulary
    only."""
    store = _store(tmp_path)
    try:
        store.upsert(m.InstallationProfile(name="XMC", created_at=NOW))
        store.upsert(m.FeatureFlag(
            key="feature.legacy_thing_we_removed",
            enabled=True, source="user", set_at=NOW,
        ))
        flags = load_flags(store)        # no crash
        # And the known keys still resolve correctly.
        assert flags.cross_event_cuts is True
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Flags accessor (typed attribute + dynamic is_on)
# --------------------------------------------------------------------------- #


def test_flags_is_on_returns_attribute_value(tmp_path):
    """``flags.is_on('feature.X')`` matches ``flags.X`` — same value, different
    access shape (config-driven vs typed)."""
    store = _store(tmp_path)
    try:
        store.upsert(m.InstallationProfile(name="XMC", created_at=NOW))
        flags = load_flags(store)
        assert flags.is_on("feature.cross_event_cuts") is flags.cross_event_cuts
        assert flags.is_on("feature.maps") is flags.maps
    finally:
        store.close()


def test_flags_is_on_unknown_key_returns_false(tmp_path):
    store = _store(tmp_path)
    try:
        flags = load_flags(store)
        assert flags.is_on("feature.totally_made_up") is False
    finally:
        store.close()


def test_flags_is_frozen(tmp_path):
    """The Flags dataclass is frozen — surfaces cannot mutate it at construct
    time. Drift here breaks the "read once at startup" discipline."""
    store = _store(tmp_path)
    try:
        flags = load_flags(store)
        with pytest.raises(Exception):  # FrozenInstanceError, dataclasses-specific
            flags.cross_event_cuts = False  # type: ignore[misc]
    finally:
        store.close()
