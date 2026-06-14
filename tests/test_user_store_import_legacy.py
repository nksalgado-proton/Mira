"""Tests for the one-shot first-launch import — spec/53 §4.

Logic-only (no Qt). Builds legacy JSON fixtures matching the production
shapes (``settings.rebuild.json`` from :mod:`mira.settings.repo` and
``events_index.json`` from :mod:`mira.gateway.index`), runs the
importer, and asserts the resulting ``mira.db`` state + the file-
retire side effects.
"""
from __future__ import annotations

import json

import pytest

from mira.user_store import models as m
from mira.user_store.import_legacy import (
    ImportOutcome,
    import_legacy_state,
)
from mira.user_store.repo import UserStore


NOW = "2026-06-08T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fixtures — the legacy JSON shapes
# --------------------------------------------------------------------------- #


def _write_legacy_settings(path, body: dict) -> None:
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")


def _write_legacy_events_index(path, events: list, base: str = "D:/Photos/_mira") -> None:
    doc = {
        "schema_version": 2,
        "photos_base_path": base,
        "events": events,
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _legacy_settings_payload() -> dict:
    """A realistic legacy settings.rebuild.json with a mix of user-tier
    settings, app-managed state, and (synthetic) wizard answers in a sub-tree
    — exercises every code path in the importer."""
    return {
        "schema_version": 1,
        "photos_base_path": "D:/Photos/_mira",
        "theme": "dark",
        "language": "en",
        "home_timezone": -3.0,
        "preferred_genres": ["macro", "wildlife"],
        "tool_preferences": {
            "focus_stack": "auto",
            "denoise": "builtin",
            "video_trim": "ffmpeg",
        },
        "saved_camera_offsets": {"DC-G9M2": -3.0},
        "wizard": {
            "primary_use_case": "trips",
            "skill_level": "enthusiast",
            "home_country_code": "BR",
        },
    }


def _legacy_events_index_payload() -> list:
    return [
        {
            "id": "evt-1",
            "name": "Costa Rica 2026",
            "event_relpath": "2026 - Costa Rica",
            "event_root_abs": None,
            "is_closed": False,
            "event_type": "trip",
            "start_date": "2026-04-01",
            "end_date": "2026-04-14",
            "country_code": "CR",
        },
        {
            "id": "evt-2",
            "name": "Cross-volume Trip",
            "event_relpath": None,
            "event_root_abs": "E:/elsewhere/Trip",
            "is_closed": True,
            "event_type": "trip",
        },
    ]


# --------------------------------------------------------------------------- #
# End-to-end import
# --------------------------------------------------------------------------- #


def test_import_writes_settings_and_wizard_answers(tmp_path):
    """Top-level keys land in ``setting``; the ``wizard`` sub-tree lands in
    ``wizard_answer`` (spec/53 §2.2 — wizard concern separated)."""
    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    _write_legacy_settings(legacy_settings, _legacy_settings_payload())
    _write_legacy_events_index(legacy_events, [])

    store = UserStore.create(tmp_path / "mira.db", app_version="test")
    try:
        outcome = import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            now=NOW,
        )

        # Settings rows: every top-level key except 'schema_version' + 'wizard'.
        settings = {s.key: json.loads(s.value_json) for s in store.all(m.Setting)}
        assert "schema_version" not in settings
        assert "wizard" not in settings
        assert settings["photos_base_path"] == "D:/Photos/_mira"
        assert settings["theme"] == "dark"
        assert settings["home_timezone"] == -3.0
        assert settings["preferred_genres"] == ["macro", "wildlife"]
        assert settings["tool_preferences"]["focus_stack"] == "auto"
        assert settings["saved_camera_offsets"]["DC-G9M2"] == -3.0
        assert outcome.settings_count == len(settings) == 7

        # Wizard answers: the wizard sub-tree's keys.
        answers = {w.question_id: json.loads(w.answer_json) for w in store.all(m.WizardAnswer)}
        assert answers == {
            "primary_use_case": "trips",
            "skill_level": "enthusiast",
            "home_country_code": "BR",
        }
        assert outcome.wizard_answers_count == 3
    finally:
        store.close()


def test_import_writes_event_index_rows(tmp_path):
    """events_index.json rows become event_index rows with the cached fields
    carried through; the legacy ``photos_base_path`` mirror at the top is
    deliberately not duplicated (lives in setting)."""
    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    _write_legacy_settings(legacy_settings, {})
    _write_legacy_events_index(legacy_events, _legacy_events_index_payload())

    store = UserStore.create(tmp_path / "mira.db")
    try:
        outcome = import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            now=NOW,
        )

        rows = {r.event_uuid: r for r in store.all(m.EventIndex)}
        assert set(rows) == {"evt-1", "evt-2"}
        assert outcome.event_index_count == 2

        cr = rows["evt-1"]
        assert cr.relpath_to_base == "2026 - Costa Rica"
        assert cr.abs_path is None                  # the normal case
        assert cr.name_cached == "Costa Rica 2026"
        assert cr.type_cached == "trip"
        assert cr.country_cached == "CR"
        assert cr.start_date_cached == "2026-04-01"
        assert cr.is_closed_cached is False

        # Cross-volume fallback: relpath empty, abs_path populated.
        cross = rows["evt-2"]
        assert cross.relpath_to_base == ""
        assert cross.abs_path == "E:/elsewhere/Trip"
        assert cross.is_closed_cached is True
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Installation profile + flag seeding
# --------------------------------------------------------------------------- #


def test_import_stamps_installation_profile_and_seeds_flags(tmp_path):
    """Step 1: the profile + per-profile default feature_flag rows land,
    keyed for the runtime ``core.feature_flags.load_flags`` precedence."""
    from core.feature_flags import FLAG_KEYS

    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    _write_legacy_settings(legacy_settings, {})
    _write_legacy_events_index(legacy_events, [])

    store = UserStore.create(tmp_path / "mira.db")
    try:
        outcome = import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            profile_name="XMC",
            now=NOW,
        )
        profile = store.get(m.InstallationProfile, 1)
        assert profile is not None and profile.name == "XMC"

        flags = {f.key: f for f in store.all(m.FeatureFlag)}
        # Every key in FLAG_KEYS got an install_profile row.
        assert set(flags) == set(FLAG_KEYS)
        assert outcome.flags_seeded_count == len(FLAG_KEYS)
        # XMC profile defaults — every Premium gate is on.
        assert all(f.enabled for f in flags.values())
        assert all(f.source == "install_profile" for f in flags.values())
    finally:
        store.close()


def test_import_with_mc_profile_seeds_flags_off(tmp_path):
    """MC profile — every Premium gate is off after seeding."""
    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    _write_legacy_settings(legacy_settings, {})
    _write_legacy_events_index(legacy_events, [])

    store = UserStore.create(tmp_path / "mira.db")
    try:
        import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            profile_name="MC",
            now=NOW,
        )
        flags = store.all(m.FeatureFlag)
        assert not any(f.enabled for f in flags)
    finally:
        store.close()


def test_import_then_load_flags_matches_profile_defaults(tmp_path):
    """End-to-end check that the importer + the runtime load_flags reader
    agree: feature_flags.load_flags after import returns the same shape as
    DEFAULTS_BY_PROFILE[profile_name]."""
    from core.feature_flags import DEFAULTS_BY_PROFILE, load_flags

    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    _write_legacy_settings(legacy_settings, {})
    _write_legacy_events_index(legacy_events, [])

    store = UserStore.create(tmp_path / "mira.db")
    try:
        import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            profile_name="MC",
            now=NOW,
        )
        flags = load_flags(store)
        # Compare each key against the MC default.
        mc_defaults = DEFAULTS_BY_PROFILE["MC"]
        for key, expected in mc_defaults.items():
            attr = key.removeprefix("feature.")
            assert getattr(flags, attr) is expected, key
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Step 4 — retire the legacy files
# --------------------------------------------------------------------------- #


def test_import_retires_legacy_files_with_timestamp_suffix(tmp_path):
    """Step 4 renames the legacy files with ``.imported-<stamp>`` so they
    survive on disk as a safety net for one or two app versions, but the
    next launch reads from mira.db only."""
    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    _write_legacy_settings(legacy_settings, _legacy_settings_payload())
    _write_legacy_events_index(legacy_events, _legacy_events_index_payload())

    store = UserStore.create(tmp_path / "mira.db")
    try:
        outcome = import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            now=NOW,
        )
    finally:
        store.close()

    # The originals are gone…
    assert not legacy_settings.exists()
    assert not legacy_events.exists()
    # …and the retired counterparts are present.
    assert len(outcome.retired_files) == 2
    for retired in outcome.retired_files:
        assert retired.exists()
        assert ".imported-" in retired.name


def test_import_with_retire_false_leaves_legacy_files_in_place(tmp_path):
    """``retire=False`` skips step 4 — useful for tests that want to assert
    on file state without consuming the legacy fixtures."""
    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    _write_legacy_settings(legacy_settings, _legacy_settings_payload())
    _write_legacy_events_index(legacy_events, _legacy_events_index_payload())

    store = UserStore.create(tmp_path / "mira.db")
    try:
        outcome = import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            retire=False,
            now=NOW,
        )
    finally:
        store.close()

    assert legacy_settings.exists()
    assert legacy_events.exists()
    assert outcome.retired_files == []


# --------------------------------------------------------------------------- #
# Tolerance — missing / unparseable legacy files
# --------------------------------------------------------------------------- #


def test_import_with_no_legacy_files_is_a_fresh_install(tmp_path):
    """A truly fresh install (no legacy files on disk) still produces a
    well-formed mira.db with installation_profile + seeded flags —
    just no setting / wizard_answer / event_index rows."""
    store = UserStore.create(tmp_path / "mira.db")
    try:
        outcome = import_legacy_state(
            store,
            settings_path=tmp_path / "no_settings.json",
            events_index_path=tmp_path / "no_events.json",
            now=NOW,
        )
        assert outcome.settings_count == 0
        assert outcome.wizard_answers_count == 0
        assert outcome.event_index_count == 0
        # Profile + flags are still seeded.
        assert store.get(m.InstallationProfile, 1) is not None
        assert outcome.flags_seeded_count > 0
        # Nothing to retire.
        assert outcome.retired_files == []
    finally:
        store.close()


def test_import_with_unparseable_legacy_settings_logs_and_continues(tmp_path, caplog):
    """A bad legacy file is logged + skipped — first-launch never wedges on
    a corrupted JSON in the user's old install."""
    import logging as _logging

    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    legacy_settings.write_text("{not json", encoding="utf-8")
    _write_legacy_events_index(legacy_events, _legacy_events_index_payload())

    store = UserStore.create(tmp_path / "mira.db")
    try:
        with caplog.at_level(_logging.WARNING):
            outcome = import_legacy_state(
                store,
                settings_path=legacy_settings,
                events_index_path=legacy_events,
                now=NOW,
            )
        # Bad settings → zero setting rows; events_index still imported.
        assert outcome.settings_count == 0
        assert outcome.event_index_count == 2
        assert any("failed to parse" in r.message for r in caplog.records)
    finally:
        store.close()


def test_import_skips_event_entries_without_id(tmp_path):
    """Malformed legacy rows (missing id) are silently skipped — better than
    crashing first launch on a hand-edited file."""
    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    _write_legacy_settings(legacy_settings, {})
    _write_legacy_events_index(legacy_events, [
        {"id": "ok", "event_relpath": "A", "name": "OK"},
        {"event_relpath": "B", "name": "no id"},          # skipped
        {"id": "", "event_relpath": "C", "name": "empty id"},   # also skipped
    ])

    store = UserStore.create(tmp_path / "mira.db")
    try:
        outcome = import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            now=NOW,
        )
        assert outcome.event_index_count == 1
        assert {r.event_uuid for r in store.all(m.EventIndex)} == {"ok"}
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Idempotency — running twice is safe
# --------------------------------------------------------------------------- #


def test_import_is_idempotent_for_settings_and_event_index(tmp_path):
    """Running the importer a second time (without retire — the legacy
    files are gone after the first call) overwrites the install_profile
    flag rows + re-upserts every legacy row without duplicating."""
    legacy_settings = tmp_path / "settings.rebuild.json"
    legacy_events = tmp_path / "events_index.json"
    _write_legacy_settings(legacy_settings, _legacy_settings_payload())
    _write_legacy_events_index(legacy_events, _legacy_events_index_payload())

    store = UserStore.create(tmp_path / "mira.db")
    try:
        first = import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            now=NOW,
            retire=False,
        )
        second = import_legacy_state(
            store,
            settings_path=legacy_settings,
            events_index_path=legacy_events,
            now="2026-06-09T00:00:00+00:00",
            retire=False,
        )
        assert first.settings_count == second.settings_count
        assert first.event_index_count == second.event_index_count
        # Counts are stable across runs (no duplicates).
        assert len(store.all(m.Setting)) == first.settings_count
        assert len(store.all(m.EventIndex)) == first.event_index_count
    finally:
        store.close()
