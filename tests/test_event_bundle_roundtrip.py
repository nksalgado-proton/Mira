"""End-to-end test for spec/82 Part B: export → install → register.

Slice 5 + 6 cover the pure-logic core; this file pins the gateway
helper that closes the loop — :meth:`Gateway.register_event_from_root`
— and proves the whole pipeline (event A → bundle → event A on a
fresh library) works without UI in the loop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import event_bundle, db_backup
from core.event_bundle import (
    PARTIAL_SUFFIX,
    export_event,
    inspect_bundle,
    install_bundle,
)
from mira.gateway.gateway import Gateway
from mira.gateway.index import EventsIndex, make_entry
from mira.settings.repo import SettingsRepo
from mira.store import models as sm
from mira.store.repo import EventStore


NOW = datetime(2026, 6, 17, 9, 30, tzinfo=timezone.utc)
NOW_ISO = NOW.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _real_event_db(path: Path, *, eid: str, name: str) -> None:
    """Build a real Mira event.db (full schema via EventStore.create)
    plus a populated ``event`` row so register_event_from_root has
    something to read."""
    store = EventStore.create(
        path, event_id=eid, app_version="test", created_at=NOW_ISO)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, name, NOW_ISO, NOW_ISO))
    store.close()


def _seed_event_tree(root: Path, *, eid: str, name: str) -> Path:
    """A skeleton event tree with a real event.db, ready for export."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "Original Media" / "_cameras").mkdir(parents=True)
    (root / "Original Media" / "_cameras" / "IMG_0001.jpg").write_bytes(
        b"\xff\xd8\xff\xd9" + b"\x10" * 2048)
    db_path = root / "event.db"
    _real_event_db(db_path, eid=eid, name=name)
    return db_path


def _make_gw(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings_path = tmp_path / "settings.json"
    index_path = tmp_path / "events_index.json"
    user_store_path = tmp_path / "mira.db"
    photos_base = tmp_path / "library"
    photos_base.mkdir(parents=True, exist_ok=True)
    gw = Gateway(
        settings=SettingsRepo(settings_path),
        index=EventsIndex(index_path),
        user_store_path=user_store_path, now=lambda: NOW_ISO,
        installation_profile="XMC",
    )
    _ = gw.user_store
    gw.settings.update(photos_base_path=str(photos_base))
    return gw, photos_base


# ── register_event_from_root ──────────────────────────────────────


def test_register_event_from_root_adds_to_index(tmp_path):
    """Drop an event_root with a real event.db into the library +
    register it → the index now lists it under the right id."""
    gw, library_base = _make_gw(tmp_path)
    new_root = library_base / "DroppedEvent"
    _seed_event_tree(new_root, eid="evt-dropped", name="DroppedEvent")

    event_id = gw.register_event_from_root(new_root)
    assert event_id == "evt-dropped"
    entry = gw.index.get("evt-dropped")
    assert entry is not None
    assert entry.get("name") == "DroppedEvent"
    gw.close()


def test_register_event_from_root_missing_db_raises(tmp_path):
    """No event.db inside the event_root → FileNotFoundError; the
    caller knows the root is not a real event yet."""
    gw, library_base = _make_gw(tmp_path)
    bogus = library_base / "JustAFolder"
    bogus.mkdir()
    with pytest.raises(FileNotFoundError):
        gw.register_event_from_root(bogus)
    gw.close()


# ── end-to-end transplant ─────────────────────────────────────────


def test_full_transplant_round_trip(tmp_path):
    """Export from one library, install + register into another —
    the destination library now contains the event with the same
    uuid and the same media bytes."""
    # Source library + an event in it.
    src_lib = tmp_path / "src-library"
    src_lib.mkdir()
    src_event_root = src_lib / "TripA"
    _seed_event_tree(src_event_root, eid="trip-a", name="TripA")

    # Export the bundle to a "USB drive".
    bundle_dest = tmp_path / "usb-drive"
    export_event(
        src_event_root, src_event_root / "event.db",
        bundle_dest, app_version="1.0", created_at=NOW)
    bundle = bundle_dest / "TripA"
    assert bundle.is_dir()

    # Fresh destination installation.
    gw, library_base = _make_gw(tmp_path / "dest-side")
    assert gw.index.get("trip-a") is None              # not here yet.

    # Inspect → install → register.
    plan = inspect_bundle(bundle, target_schema_version=1)
    # The bundle's real schema_version comes from the live build,
    # which the bundle picked up from _real_event_db -> EventStore.
    # Accept whatever the run produced; can_proceed should be True
    # as long as it's not strictly larger than the (huge) target.
    plan = inspect_bundle(
        bundle, target_schema_version=plan.manifest.schema_version)
    assert plan.can_proceed
    new_root = install_bundle(plan, library_base)
    event_id = gw.register_event_from_root(new_root)

    # The destination library now lists the event under the bundle's
    # uuid, and the media bytes survived end-to-end.
    assert event_id == "trip-a"
    entry = gw.index.get("trip-a")
    assert entry is not None
    assert entry.get("name") == "TripA"
    src_bytes = (
        src_event_root / "Original Media" / "_cameras"
        / "IMG_0001.jpg").read_bytes()
    dst_bytes = (
        new_root / "Original Media" / "_cameras"
        / "IMG_0001.jpg").read_bytes()
    assert dst_bytes == src_bytes
    gw.close()


def test_replace_overwrites_existing_event_in_index(tmp_path):
    """Replace path: the destination library ALREADY has an event
    with the same uuid; install + register against the existing
    event_root → the index keeps one entry, the bytes are fresh."""
    src_lib = tmp_path / "src-library"
    src_lib.mkdir()
    src_event_root = src_lib / "TripA"
    _seed_event_tree(src_event_root, eid="trip-a", name="TripA")
    bundle_dest = tmp_path / "usb-drive"
    export_event(
        src_event_root, src_event_root / "event.db",
        bundle_dest, app_version="1.0", created_at=NOW)
    bundle = bundle_dest / "TripA"

    gw, library_base = _make_gw(tmp_path / "dest-side")
    # Seed the destination with an OLDER copy of the same event.
    older_root = library_base / "TripA"
    _seed_event_tree(older_root, eid="trip-a", name="TripA (older)")
    gw.register_event_from_root(older_root)
    assert gw.index.get("trip-a") is not None

    plan = inspect_bundle(
        bundle,
        target_schema_version=inspect_bundle(
            bundle, target_schema_version=1).manifest.schema_version)
    install_bundle(
        plan, library_base, target_event_root=older_root)
    gw.register_event_from_root(older_root)

    entry = gw.index.get("trip-a")
    assert entry is not None
    # The bundle's name beat the older "(older)" name — the new
    # event.db overwrote it.
    assert entry.get("name") == "TripA"
    gw.close()
