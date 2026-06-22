"""Tests for the §A heartbeat-loss handler — spec/76 §A.

When the heartbeat ``refresh()`` returns False mid-session (another
machine took the writer lock), the handler in :mod:`mira.ui.app`:

  1. flips :func:`mira.session.set_read_only` against the new holder,
  2. refreshes every :class:`ReadOnlyBanner` in the widget tree, and
  3. shows a modal naming the new editor.

The closure inside ``main()`` additionally stops the heartbeat QTimer;
that part isn't exercised here (it'd need a wired QApplication + a
running timer to be meaningful).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import library_lock
from mira import session as mira_session


@pytest.fixture(autouse=True)
def _reset_session_flag():
    mira_session.reset_for_tests()
    yield
    mira_session.reset_for_tests()


def test_lock_lost_handler_flips_session_and_refreshes_banner(
    qapp, tmp_path: Path, monkeypatch,
):
    """End-to-end: acquire the lock, simulate another writer taking
    over (overwrite the lock file with foreign identity), call the
    handler, assert the session flag flipped and the banner refreshed.

    The modal is monkeypatched out — its own contract is covered in
    :mod:`tests.test_lock_conflict_dialog` and the dialog is exercised
    by ``test_lock_lost_handler_show_modal_call`` below.
    """
    import json
    import socket

    import mira.ui.app as app_mod
    from mira.ui.shell.read_only_banner import ReadOnlyBanner

    # Acquire the lock so the session starts writeable.
    result = library_lock.acquire(tmp_path)
    assert result.acquired is True

    # Plant a different writer's identity on top of the lock — what
    # another machine successfully taking the lock looks like.
    lock_file = tmp_path / library_lock.LOCK_DIRNAME / library_lock.LOCK_FILENAME
    lock_file.write_text(json.dumps({
        "hostname": "other-host",
        "pid": 99999,
        "app_version": "dev",
        "acquired_at": "2026-06-21T17:30:00Z",
        "heartbeat_at": "2026-06-21T17:30:00Z",
    }), encoding="utf-8")

    # Build a banner widget so we can verify it switched on.
    banner = ReadOnlyBanner()
    try:
        assert banner.isVisible() is False
        # Suppress the modal — pinned separately below.
        monkeypatch.setattr(app_mod, "_show_lock_lost_dialog",
                            lambda holder: None)

        flipped = app_mod._handle_lock_lost(tmp_path)
        assert flipped is True
        assert mira_session.is_read_only() is True

        holder = mira_session.read_only_holder()
        assert holder is not None
        assert holder.hostname == "other-host"
        assert holder.pid == 99999
        # We are not the holder anymore.
        assert holder.hostname != socket.gethostname()

        # Banner reflects the foreign holder.
        assert "other-host" in banner._label.text()
    finally:
        banner.deleteLater()


def test_lock_lost_handler_is_noop_when_lock_is_still_ours(
    qapp, tmp_path: Path, monkeypatch,
):
    """Sanity: a heartbeat tick that succeeds (lock still ours) leaves
    the session writeable. The handler returns ``False`` and nothing
    flips."""
    import mira.ui.app as app_mod

    result = library_lock.acquire(tmp_path)
    assert result.acquired is True

    modal_calls: list = []
    monkeypatch.setattr(app_mod, "_show_lock_lost_dialog",
                        lambda holder: modal_calls.append(holder))

    assert app_mod._handle_lock_lost(tmp_path) is False
    assert mira_session.is_read_only() is False
    assert modal_calls == []


def test_lock_lost_handler_show_modal_arg_when_disabled(
    qapp, tmp_path: Path, monkeypatch,
):
    """``show_modal=False`` is the test seam: the read-only flip
    happens but the dialog is skipped. Used by tests that don't have
    a usable display."""
    import json
    import mira.ui.app as app_mod

    library_lock.acquire(tmp_path)
    lock_file = tmp_path / library_lock.LOCK_DIRNAME / library_lock.LOCK_FILENAME
    lock_file.write_text(json.dumps({
        "hostname": "other-host", "pid": 99999, "app_version": "dev",
        "acquired_at": "2026-06-21T17:30:00Z",
        "heartbeat_at": "2026-06-21T17:30:00Z",
    }), encoding="utf-8")

    modal_calls: list = []
    monkeypatch.setattr(app_mod, "_show_lock_lost_dialog",
                        lambda holder: modal_calls.append(holder))

    flipped = app_mod._handle_lock_lost(tmp_path, show_modal=False)
    assert flipped is True
    assert mira_session.is_read_only() is True
    # Skipped despite the flip.
    assert modal_calls == []


def test_refresh_read_only_banners_finds_every_top_level(
    qapp,
):
    """Spec/76 §A banner refresh — the helper walks every top-level
    widget and calls :meth:`ReadOnlyBanner.refresh` on each banner it
    finds. Exercises the contract the heartbeat handler relies on
    without forcing a MainWindow build."""
    import mira.ui.app as app_mod
    from PyQt6.QtWidgets import QWidget
    from mira.ui.shell.read_only_banner import ReadOnlyBanner

    # Build a top-level widget hosting a banner.
    host = QWidget()
    banner = ReadOnlyBanner(host)
    host.show()
    try:
        # Flip the flag and call the helper directly — banner should
        # pick it up the next time refresh() runs.
        mira_session.set_read_only(True, None)
        app_mod._refresh_read_only_banners()
        assert banner.isVisible() is True
    finally:
        host.deleteLater()


def test_show_lock_lost_dialog_names_new_holder(qapp, monkeypatch):
    """The modal text names the host + acquire time of the writer who
    took over, matching the §A contract."""
    from dataclasses import dataclass

    import mira.ui.app as app_mod
    from core.library_lock import LockInfo

    @dataclass
    class _DialogCall:
        intent: str
        title: str
        message: str
        primary_text: str
        ghost_text: str | None
        secondary_text: str | None

    captured: list = []

    class _FakeDialog:
        def __init__(self, *, intent, title, message, primary_text,
                     ghost_text=None, secondary_text=None, **kw):
            captured.append(_DialogCall(
                intent=intent, title=title, message=message,
                primary_text=primary_text, ghost_text=ghost_text,
                secondary_text=secondary_text,
            ))

        def exec(self):
            return 1

        def result_kind(self):
            return "primary"

    monkeypatch.setattr(
        "mira.ui.design.dialogs.MessageDialog", _FakeDialog)

    holder = LockInfo(
        hostname="studio-pc", pid=4242, app_version="dev",
        acquired_at="2026-06-21T17:30:00Z",
        heartbeat_at="2026-06-21T17:30:00Z",
        mtime=0.0,
    )
    app_mod._show_lock_lost_dialog(holder)

    assert len(captured) == 1
    call = captured[0]
    assert call.intent == "warning"
    assert "studio-pc" in call.message
    assert "2026-06-21T17:30:00Z" in call.message
    assert call.primary_text == "OK"
    # No "Cancel" — the user can only acknowledge.
    assert call.ghost_text is None
