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
