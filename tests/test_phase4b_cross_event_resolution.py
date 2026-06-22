"""spec/94 Phase 4b end-to-end cross-event resolution test.

The Phase 4a gate hid EXIF / gear filters even though the resolver
already understood them. Phase 4b lifted the gate. This test pins
that the cross-event resolver — driven through ``LibraryGateway``,
not the lower-level :mod:`cross_event_resolver` directly — composes
``camera_ids`` and ``aperture_max`` filters across events and
returns the right items.

The resolver-level coverage lives in
:mod:`tests.test_cross_event_resolver`; this file is the Phase 4b
acceptance test for the lifted dims through the production seam.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway.library_gateway import LibraryGateway
from mira.gateway import cross_event_resolver as cev
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-21T00:00:00+00:00"


def _open_user_store(tmp_path: Path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db", app_version="test", created_at=NOW,
    )


def _seed_two_events(store: UserStore) -> None:
    """Two events spanning two cameras + four apertures. The intersection
    of "camera = Pana+G9M2" and "aperture <= 2.8" returns one item per
    event.

    Layout:

    ============  ====================  ===========  ============
    item key      camera                 aperture     survives?
    ============  ====================  ===========  ============
    (A, a-wide)   Pana+G9M2 (target)    f/2.0        ✓ wide + target
    (A, a-narrow) Pana+G9M2 (target)    f/8.0        ✗ too narrow
    (A, a-other)  Pana+S5 (off-target)  f/1.8        ✗ wrong camera
    (B, b-wide)   Pana+G9M2 (target)    f/2.8        ✓ wide + target
    (B, b-narrow) Pana+G9M2 (target)    f/11.0       ✗ too narrow
    (B, b-other)  Pana+S5 (off-target)  f/2.0        ✗ wrong camera
    ============  ====================  ===========  ============
    """
    rows = [
        # Event A — Italy.
        um.GlobalItem(
            event_uuid="A", item_id="a-wide", synced_at=NOW,
            event_name="Italy 2026",
            capture_time="2026-04-01T10:00:00",
            kind="photo", classification="landscape",
            aperture_f=2.0, camera_id="Pana+G9M2",
            lens_model="LEICA 45mm",
        ),
        um.GlobalItem(
            event_uuid="A", item_id="a-narrow", synced_at=NOW,
            event_name="Italy 2026",
            capture_time="2026-04-01T11:00:00",
            kind="photo", classification="landscape",
            aperture_f=8.0, camera_id="Pana+G9M2",
            lens_model="LEICA 45mm",
        ),
        um.GlobalItem(
            event_uuid="A", item_id="a-other", synced_at=NOW,
            event_name="Italy 2026",
            capture_time="2026-04-01T12:00:00",
            kind="photo", classification="landscape",
            aperture_f=1.8, camera_id="Pana+S5",
            lens_model="LUMIX 24-105",
        ),
        # Event B — Japan.
        um.GlobalItem(
            event_uuid="B", item_id="b-wide", synced_at=NOW,
            event_name="Japan 2026",
            capture_time="2026-05-01T10:00:00",
            kind="photo", classification="landscape",
            aperture_f=2.8, camera_id="Pana+G9M2",
            lens_model="LEICA 45mm",
        ),
        um.GlobalItem(
            event_uuid="B", item_id="b-narrow", synced_at=NOW,
            event_name="Japan 2026",
            capture_time="2026-05-01T11:00:00",
            kind="photo", classification="landscape",
            aperture_f=11.0, camera_id="Pana+G9M2",
            lens_model="LEICA 45mm",
        ),
        um.GlobalItem(
            event_uuid="B", item_id="b-other", synced_at=NOW,
            event_name="Japan 2026",
            capture_time="2026-05-01T12:00:00",
            kind="photo", classification="landscape",
            aperture_f=2.0, camera_id="Pana+S5",
            lens_model="LUMIX 24-105",
        ),
    ]
    for r in rows:
        store.upsert(r)


# ── Library-gateway-driven resolution with the lifted dims ──────


def test_camera_and_aperture_filters_compose_across_events(tmp_path):
    """spec/94 Phase 4b acceptance — a Collection composed with
    ``camera_ids`` + ``aperture_max`` over the cross-event projection
    returns the right items from each event. Drives the resolution
    through :meth:`LibraryGateway.resolve_dc_keys` (the production
    entry the dialog wires to) rather than the lower-level resolver
    directly."""
    store = _open_user_store(tmp_path)
    _seed_two_events(store)
    lg = LibraryGateway(store, now=lambda: NOW)
    try:
        keys = lg.resolve_dc_keys(
            [["+", "collected"]],
            {
                "camera_ids": ["Pana+G9M2"],
                "aperture_max": 2.8,
            },
        )
        assert {cev.unpack_key(k) for k in keys} == {
            ("A", "a-wide"), ("B", "b-wide"),
        }
    finally:
        store.close()


def test_camera_filter_alone_narrows_across_events(tmp_path):
    """Sanity for the gear half: with ``camera_ids`` only, every
    item shot on the target body survives — across both events."""
    store = _open_user_store(tmp_path)
    _seed_two_events(store)
    lg = LibraryGateway(store, now=lambda: NOW)
    try:
        keys = lg.resolve_dc_keys(
            [["+", "collected"]],
            {"camera_ids": ["Pana+G9M2"]},
        )
        assert {cev.unpack_key(k) for k in keys} == {
            ("A", "a-wide"), ("A", "a-narrow"),
            ("B", "b-wide"), ("B", "b-narrow"),
        }
    finally:
        store.close()


def test_aperture_max_filter_alone_narrows_across_events(tmp_path):
    """Sanity for the EXIF half: ``aperture_max`` returns wide-open
    frames regardless of which body shot them."""
    store = _open_user_store(tmp_path)
    _seed_two_events(store)
    lg = LibraryGateway(store, now=lambda: NOW)
    try:
        keys = lg.resolve_dc_keys(
            [["+", "collected"]],
            {"aperture_max": 2.8},
        )
        assert {cev.unpack_key(k) for k in keys} == {
            ("A", "a-wide"), ("A", "a-other"),
            ("B", "b-wide"), ("B", "b-other"),
        }
    finally:
        store.close()


def test_dc_probe_counts_the_intersection(tmp_path):
    """``LibraryGateway.dc_probe`` is what the dialog's live count
    reads. Confirm it returns the same intersection size for the
    Phase 4b dims as resolve_dc_keys produces."""
    store = _open_user_store(tmp_path)
    _seed_two_events(store)
    lg = LibraryGateway(store, now=lambda: NOW)
    try:
        n = lg.dc_probe(
            [["+", "collected"]],
            {"camera_ids": ["Pana+G9M2"], "aperture_max": 2.8},
        )
        assert n == 2  # ("A", "a-wide") + ("B", "b-wide")
    finally:
        store.close()
