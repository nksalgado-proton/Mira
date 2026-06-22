"""Tests for read-only library mode — spec/76 §B.1.

When another writer owns the library lock, this Mira opens
read-only: the session flag is raised, gateway mutators refuse
writes defensively, the banner names the editing machine. Tests
pin all three together because the brief calls them out as ONE
single source of truth ("do not scatter per-widget guards").
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.library_lock import LockInfo
from mira import session as mira_session
from mira.session import (
    ReadOnlyLibraryError,
    is_read_only,
    read_only_holder,
    set_read_only,
)


@pytest.fixture(autouse=True)
def _reset_session_flag():
    """Read-only is a process-wide singleton — every test resets it
    so its state never leaks to the next test."""
    mira_session.reset_for_tests()
    yield
    mira_session.reset_for_tests()


def _make_holder(host: str = "studio-pc") -> LockInfo:
    """A plausible foreign writer for the conflict path."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return LockInfo(
        hostname=host, pid=4242, app_version="dev",
        acquired_at=now, heartbeat_at=now, mtime=0.0,
    )


# ── Flag module ───────────────────────────────────────────────────


def test_default_is_writeable():
    assert is_read_only() is False
    assert read_only_holder() is None


def test_set_read_only_carries_holder():
    holder = _make_holder()
    set_read_only(True, holder)
    assert is_read_only() is True
    assert read_only_holder() is holder


def test_writeable_clears_holder_even_if_passed():
    """A writeable session never carries a foreign holder."""
    set_read_only(False, _make_holder())
    assert read_only_holder() is None


# ── Gateway-level defensive guard ─────────────────────────────────


def _open_minimal_event(tmp_path: Path):
    """Create a per-event SQLite + open one EventGateway. The tests
    don't need a populated event — they just need a mutator to call.
    """
    from mira.gateway.event_gateway import EventGateway
    from mira.store.repo import EventStore
    db_path = tmp_path / "event.db"
    store = EventStore.create(db_path, event_id="evt_test")
    return EventGateway(store), store


def test_gateway_refuses_mutation_when_read_only(tmp_path):
    """spec/76 §B.1 — every mutator funnels through
    :meth:`EventGateway._touch`, which raises when the session flag
    is set. The defensive net catches surfaces that forgot to gate.

    ``save_trip_days([])`` is the cheapest write that funnels through
    ``_touch`` without tripping FK constraints — no rows touched, but
    the ``updated_at`` stamp still runs through the guard.
    """
    eg, store = _open_minimal_event(tmp_path)
    try:
        set_read_only(True, _make_holder())
        with pytest.raises(ReadOnlyLibraryError):
            eg.save_trip_days([])
    finally:
        store.close()


def test_gateway_allows_mutation_when_writeable(tmp_path):
    """When the session is writeable, the guard is transparent — the
    same ``save_trip_days([])`` write goes through without raising."""
    eg, store = _open_minimal_event(tmp_path)
    try:
        assert is_read_only() is False
        eg.save_trip_days([])
    finally:
        store.close()


# ── Banner — picks up the flag at construction ────────────────────


def test_banner_hidden_when_writeable(qapp):
    from mira.ui.shell.read_only_banner import ReadOnlyBanner
    banner = ReadOnlyBanner()
    try:
        assert banner.isVisible() is False
    finally:
        banner.deleteLater()


def test_banner_shown_with_holder_when_read_only(qapp):
    from mira.ui.shell.read_only_banner import ReadOnlyBanner
    holder = _make_holder("studio-pc")
    set_read_only(True, holder)
    banner = ReadOnlyBanner()
    try:
        # Hidden initially because parent isn't shown, but the widget's
        # own visibility state is True. ``isVisibleTo(None)`` would
        # require event-loop ticks; the role/state we care about is
        # the call to ``setVisible(True)`` and the label text.
        assert banner._label.text() != ""
        assert "studio-pc" in banner._label.text()
    finally:
        banner.deleteLater()


def test_banner_refresh_picks_up_runtime_flip(qapp):
    """spec/76 §A heartbeat-loss handler — when the writer lock is
    wrested away mid-session, calling banner.refresh() flips the
    visible state without rebuilding the widget."""
    from mira.ui.shell.read_only_banner import ReadOnlyBanner
    # Built while writeable → hidden.
    banner = ReadOnlyBanner()
    try:
        assert banner.isVisible() is False
        # Mid-session, the heartbeat handler flips the flag + refreshes.
        set_read_only(True, _make_holder("studio-pc"))
        banner.refresh()
        assert "studio-pc" in banner._label.text()
    finally:
        banner.deleteLater()


# ── LibraryGateway defensive net (cross-event mutators) ──────────


def _open_library_gateway(tmp_path):
    """An empty LibraryGateway against a fresh mira.db. Mirrors the
    test_library_gateway.py fixture but without the seeded universe —
    these tests are about the read-only guard, not query results."""
    from mira.gateway.library_gateway import LibraryGateway
    from mira.user_store.repo import UserStore
    store = UserStore.create(
        tmp_path / "mira.db", app_version="test",
        created_at="2026-06-21T00:00:00+00:00",
    )
    lg = LibraryGateway(store, now=lambda: "2026-06-21T00:00:00+00:00")
    return lg, store


def test_library_gateway_create_dc_refuses_when_read_only(tmp_path):
    """spec/76 §B.1 — cross-event Collection creation must refuse in
    read-only mode. The defensive guard catches paths that slipped
    past the UI."""
    lg, store = _open_library_gateway(tmp_path)
    try:
        set_read_only(True, _make_holder())
        with pytest.raises(ReadOnlyLibraryError):
            lg.create_dc("test-collection")
    finally:
        store.close()


def test_library_gateway_create_cross_event_cut_refuses_when_read_only(tmp_path):
    """A cross-event Cut create writes to mira.db's ``cut`` table —
    must be refused under read-only."""
    lg, store = _open_library_gateway(tmp_path)
    try:
        set_read_only(True, _make_holder())
        with pytest.raises(ReadOnlyLibraryError):
            lg.create_cross_event_cut("test-cut")
    finally:
        store.close()


def test_library_gateway_delete_cross_event_cut_refuses_when_read_only(tmp_path):
    """Delete is the surface the Library page's per-row trash icon
    triggers — explicitly tested here so a stray click in a read-only
    window can't drop a cross-event Cut."""
    lg, store = _open_library_gateway(tmp_path)
    try:
        # Create the Cut while writeable, then flip and try to delete.
        cut = lg.create_cross_event_cut("victim")
        set_read_only(True, _make_holder())
        with pytest.raises(ReadOnlyLibraryError):
            lg.delete_cross_event_cut(cut.id)
    finally:
        store.close()


def test_library_gateway_stamp_exported_refuses_when_read_only(tmp_path):
    """``last_exported_at`` stamps from the cross-event export pipeline
    are still mira.db writes — refuse under read-only."""
    lg, store = _open_library_gateway(tmp_path)
    try:
        cut = lg.create_cross_event_cut("victim")
        set_read_only(True, _make_holder())
        with pytest.raises(ReadOnlyLibraryError):
            lg.stamp_cross_event_cut_exported(cut.id)
    finally:
        store.close()


def test_library_gateway_set_gear_active_refuses_when_read_only(tmp_path):
    """Gear-profile rows are user-level state in mira.db — wizard
    toggles must refuse under read-only."""
    lg, store = _open_library_gateway(tmp_path)
    try:
        set_read_only(True, _make_holder())
        with pytest.raises(ReadOnlyLibraryError):
            lg.set_gear_active("camera", "Test+Camera", True)
    finally:
        store.close()


# ── Maintenance methods SKIP rather than raise ────────────────────


def test_library_gateway_sync_event_is_no_op_in_read_only(tmp_path):
    """``sync_event`` is a maintenance write triggered by event close;
    the writer-half machine owns it. Read-only sessions should skip
    silently (return 0) — raising would crash the close path."""
    from mira.store.repo import EventStore
    lg, store = _open_library_gateway(tmp_path)
    event_store = EventStore.create(
        tmp_path / "event.db", event_id="evt_test")
    try:
        set_read_only(True, _make_holder())
        # No-op return; no raise.
        n = lg.sync_event(
            event_store=event_store,
            event_uuid="evt_test",
            event_name="Test event",
        )
        assert n == 0
    finally:
        event_store.close()
        store.close()


def test_library_gateway_reconcile_all_is_no_op_in_read_only(tmp_path):
    """Reconcile is the startup maintenance pass — also belongs to
    the writer-half machine. Read-only sessions skip with the empty
    shape the caller's bookkeeping expects."""
    lg, store = _open_library_gateway(tmp_path)
    try:
        set_read_only(True, _make_holder())
        result = lg.reconcile_all(
            open_event_store=lambda _uuid: None,
            known_events=[],
        )
        assert result == {"synced": 0, "dropped": 0, "skipped": []}
    finally:
        store.close()
