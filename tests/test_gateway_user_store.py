"""Tests for the Gateway.user_store integration — spec/53 slice 6 (shim mode).

Logic-only (no Qt). Verifies that the lazy ``user_store`` property opens an
existing ``mira.db`` (preserving state across sessions), creates +
imports from legacy JSON on first launch (when both legacy files exist), and
does the right thing on a truly fresh install (no legacy files present).

The existing SettingsRepo + EventsIndex paths stay on the public surface
unchanged — these tests don't touch them; they cover only the new seam.
"""
from __future__ import annotations

import json

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.user_store import models as user_m


def _gateway(tmp_path, *, profile: str = "XMC") -> Gateway:
    """Construct a Gateway whose three artefacts (settings, index, user-store)
    live colocated under ``tmp_path`` — the production layout."""
    return Gateway(
        settings=SettingsRepo(tmp_path / "settings.rebuild.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
        user_store_path=tmp_path / "mira.db",
        installation_profile=profile,
    )


def _write_legacy_settings(path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_legacy_events_index(path, events: list, base: str = "D:/Photos") -> None:
    doc = {"schema_version": 2, "photos_base_path": base, "events": events}
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Lazy access
# --------------------------------------------------------------------------- #


def test_user_store_property_is_lazy(tmp_path):
    """No file should appear at the user-store path until somebody actually
    reads the property — Gateway construction stays cheap."""
    gw = _gateway(tmp_path)
    assert not (tmp_path / "mira.db").exists()
    # First access materialises it.
    _ = gw.user_store
    assert (tmp_path / "mira.db").is_file()
    gw.close()


def test_user_store_returns_same_instance_across_calls(tmp_path):
    gw = _gateway(tmp_path)
    try:
        a = gw.user_store
        b = gw.user_store
        assert a is b
    finally:
        gw.close()


def test_close_releases_user_store(tmp_path):
    """Close clears the cached handle so a subsequent access reopens the file
    (testing the clean-close path: sidecar + backup must be present after)."""
    gw = _gateway(tmp_path)
    store = gw.user_store
    assert store is not None
    gw.close()
    assert gw._user_store is None
    # Sidecar + .bak.1 produced by the close path.
    assert (tmp_path / "mira.db.sha256").is_file()
    assert (tmp_path / "mira.db.bak.1").is_file()


def test_close_is_safe_when_user_store_was_never_accessed(tmp_path):
    """close() must be a no-op if nothing ever opened the lazy handle."""
    gw = _gateway(tmp_path)
    gw.close()                                       # no crash
    assert not (tmp_path / "mira.db").exists()


# --------------------------------------------------------------------------- #
# First launch — file missing, legacy present
# --------------------------------------------------------------------------- #


def test_first_launch_with_legacy_settings_imports_them(tmp_path):
    """Both legacy files present + mira.db missing → the importer
    runs on first user_store access, lifting every setting + event into the
    new store and retiring the originals (spec/53 §4)."""
    settings_path = tmp_path / "settings.rebuild.json"
    events_path = tmp_path / "events_index.json"
    _write_legacy_settings(settings_path, {
        "schema_version": 1,
        "photos_base_path": "D:/Photos/_mira",
        "theme": "dark",
        "wizard": {"home_country_code": "BR"},
    })
    _write_legacy_events_index(events_path, [
        {
            "id": "evt-1",
            "name": "Costa Rica 2026",
            "event_relpath": "2026 - Costa Rica",
            "is_closed": False,
        },
    ])

    gw = _gateway(tmp_path)
    try:
        store = gw.user_store

        settings = {s.key for s in store.all(user_m.Setting)}
        assert "photos_base_path" in settings and "theme" in settings
        # Wizard sub-tree split out into wizard_answer rows.
        assert {w.question_id for w in store.all(user_m.WizardAnswer)} == {
            "home_country_code",
        }
        # The event row landed.
        evt = store.get(user_m.EventIndex, "evt-1")
        assert evt is not None
        assert evt.relpath_to_base == "2026 - Costa Rica"
    finally:
        gw.close()

    # Step 4: legacy files retired by the importer.
    assert not settings_path.exists()
    assert not events_path.exists()
    # The retired counterparts survive on disk under the .imported-* suffix.
    retired_names = {p.name for p in tmp_path.iterdir()}
    assert any(n.startswith("settings.rebuild.json.imported-") for n in retired_names)
    assert any(n.startswith("events_index.json.imported-") for n in retired_names)


def test_first_launch_seeds_installation_profile_and_flags(tmp_path):
    """The importer's step-1 always runs — even on a truly fresh install with
    no legacy JSON — so feature_flags.load_flags has something to read."""
    from core.feature_flags import FLAG_KEYS, load_flags

    gw = _gateway(tmp_path, profile="MC")
    try:
        store = gw.user_store
        # installation_profile stamped per the gateway's installation_profile arg.
        profile = store.get(user_m.InstallationProfile, 1)
        assert profile is not None and profile.name == "MC"
        # Every flag key got a row.
        keys = {f.key for f in store.all(user_m.FeatureFlag)}
        assert keys == set(FLAG_KEYS)
        # And the runtime reader agrees with the profile (MC = everything off).
        flags = load_flags(store)
        assert flags.cross_event_cuts is False
        assert flags.maps is False
    finally:
        gw.close()


def test_first_launch_fresh_install_no_legacy_files(tmp_path):
    """No legacy JSON on disk → import_legacy_state's tolerant path: profile
    + flags seeded, settings / wizard / events tables empty. New install just
    works."""
    gw = _gateway(tmp_path)
    try:
        store = gw.user_store
        assert store.get(user_m.InstallationProfile, 1) is not None
        assert len(store.all(user_m.Setting)) == 0
        assert len(store.all(user_m.WizardAnswer)) == 0
        assert len(store.all(user_m.EventIndex)) == 0
        assert len(store.all(user_m.FeatureFlag)) > 0
    finally:
        gw.close()


# --------------------------------------------------------------------------- #
# Subsequent launches — file exists
# --------------------------------------------------------------------------- #


def test_subsequent_launch_opens_existing_store_without_reimport(tmp_path):
    """An existing mira.db is opened (not re-created + re-imported);
    a setting written in session 1 survives session 2."""
    NOW = "2026-06-08T00:00:00+00:00"

    # Session 1: first launch creates + writes a setting.
    gw = _gateway(tmp_path)
    try:
        store = gw.user_store
        store.upsert(user_m.Setting(
            key="theme", value_json='"light"', updated_at=NOW,
        ))
    finally:
        gw.close()

    # Session 2: same path, but a fresh Gateway instance — reads back the
    # setting via the open path (not the create+import path).
    gw2 = _gateway(tmp_path)
    try:
        store2 = gw2.user_store
        got = store2.get(user_m.Setting, "theme")
        assert got is not None and got.value_json == '"light"'
    finally:
        gw2.close()


def test_subsequent_launch_does_not_re_retire_already_imported_files(tmp_path):
    """A second launch with an existing mira.db must NOT touch the
    retired .imported-* files (they stay on disk as the spec/53 safety net).
    """
    settings_path = tmp_path / "settings.rebuild.json"
    events_path = tmp_path / "events_index.json"
    _write_legacy_settings(settings_path, {"theme": "dark"})
    _write_legacy_events_index(events_path, [])

    # Session 1: import runs.
    gw = _gateway(tmp_path)
    try:
        _ = gw.user_store
    finally:
        gw.close()

    retired_paths_session1 = sorted(p.name for p in tmp_path.iterdir() if ".imported-" in p.name)
    assert retired_paths_session1, "session 1 must have produced the retired files"

    # Session 2: open path, no importer.
    gw2 = _gateway(tmp_path)
    try:
        _ = gw2.user_store
    finally:
        gw2.close()

    retired_paths_session2 = sorted(p.name for p in tmp_path.iterdir() if ".imported-" in p.name)
    assert retired_paths_session1 == retired_paths_session2
