"""Tests for the new-app settings foundation — spec/04 / charter §5.7.

Logic-only (no Qt). Covers the green gate in spec/04 §7: defaults present + tier
partition, round-trip, missing-file → defaults, corrupt-file → defaults, tolerant
merge, the protection contract (sidecar + history + no stray .tmp), and update().
"""
from __future__ import annotations

import json

import pytest

from mira import protect
from mira.settings import (
    SETTINGS_SCHEMA_VERSION,
    Settings,
    SettingsRepo,
    app_keys,
    user_keys,
)
# dataclasses.fields for the partition test.
from dataclasses import fields as dc_fields


@pytest.fixture
def repo(tmp_path):
    return SettingsRepo(tmp_path / "settings.json")


# --------------------------------------------------------------------------- #
# 1. Defaults present + tier partition
# --------------------------------------------------------------------------- #


def test_defaults_present():
    s = Settings()
    assert s.theme == "dark"
    assert s.language == "en"
    # spec/48 + spec/52: cull + select collapsed into one 'pick' phase;
    # cull_default_state was renamed pick_default_state.
    assert s.pick_default_state == "skipped"
    assert s.edit_default_state == "picked"
    assert s.preferred_genres == ["macro", "wildlife"]
    assert s.slideshow_max_minutes_long == 30.0
    assert s.tool_preferences == {"focus_stack": "auto", "denoise": "builtin", "video_trim": "ffmpeg"}
    assert s.plan_editor_column_widths == [110, 70, 160]
    # mutable defaults are per-instance, not shared
    assert Settings().tool_preferences is not s.tool_preferences


def test_tier_partition_covers_all_fields_no_overlap():
    u, a = set(user_keys()), set(app_keys())
    assert u.isdisjoint(a)
    all_fields = {f.name for f in dc_fields(Settings)}
    assert u | a == all_fields
    # every field declares a tier
    for f in dc_fields(Settings):
        assert f.metadata.get("tier") in {"user", "app"}, f.name
        assert f.metadata.get("help"), f.name


# --------------------------------------------------------------------------- #
# 2. Round-trip
# --------------------------------------------------------------------------- #


def test_from_dict_to_dict_round_trip():
    s = Settings(theme="light", peaking_sensitivity=70)
    assert Settings.from_dict(s.to_dict()) == s


def test_save_then_load_equal(repo):
    s = Settings(theme="light", saved_camera_offsets={"G9M2": -3.0})
    repo.save(s)
    assert repo.load() == s


def test_on_disk_carries_schema_version(repo):
    repo.save(Settings())
    on_disk = json.loads(repo.path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == SETTINGS_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# 3. Missing file → defaults (and seeded)
# --------------------------------------------------------------------------- #


def test_missing_file_returns_defaults_and_seeds(repo):
    assert not repo.path.exists()
    loaded = repo.load()
    assert loaded == Settings()
    assert repo.path.exists()  # seeded
    assert protect.verify(repo.path).valid  # sidecar written


# --------------------------------------------------------------------------- #
# 4. Corrupt file → defaults (preserves bad bytes)
# --------------------------------------------------------------------------- #


def test_corrupt_file_falls_back_to_defaults(repo):
    repo.path.write_text("{ this is not json", encoding="utf-8")
    loaded = repo.load()
    assert loaded == Settings()
    bak = repo.path.with_suffix(repo.path.suffix + ".bak")
    assert bak.exists()
    assert "not json" in bak.read_text(encoding="utf-8")


def test_non_object_json_falls_back(repo):
    repo.path.write_text("[1, 2, 3]", encoding="utf-8")
    assert repo.load() == Settings()


# --------------------------------------------------------------------------- #
# 5. Tolerant merge
# --------------------------------------------------------------------------- #


def test_tolerant_merge_unknown_and_missing_keys(repo):
    repo.path.write_text(json.dumps({
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "theme": "light",          # known override
        "totally_unknown_key": 42,  # ignored
        # language deliberately absent → default
    }), encoding="utf-8")
    loaded = repo.load()
    assert loaded.theme == "light"
    assert loaded.language == "en"
    assert not hasattr(loaded, "totally_unknown_key")


# --------------------------------------------------------------------------- #
# 6. Protection contract
# --------------------------------------------------------------------------- #


def test_sidecar_verifies_and_history_rotates(repo):
    repo.save(Settings(theme="dark"))
    assert protect.verify(repo.path).valid
    repo.save(Settings(theme="light"))  # second save rotates a history copy
    assert len(protect.list_history(repo.path)) == 1
    # atomic rename leaves no temp file
    assert not repo.path.with_suffix(repo.path.suffix + ".tmp").exists()


# --------------------------------------------------------------------------- #
# 7. update()
# --------------------------------------------------------------------------- #


def test_update_persists_only_changed_key(repo):
    repo.save(Settings(theme="dark", language="en"))
    updated = repo.update(theme="light")
    assert updated.theme == "light"
    assert updated.language == "en"
    assert repo.load().theme == "light"


