"""Tests for the shared read-only UI helper + the surface sweep
(spec/76 §B.1).

The helper :func:`mira.ui.read_only.disable_if_read_only` is the
single seam every mutation control opts into. The tests pin:

* the helper itself — disables + tooltips a button when the session
  flag is True, no-op when writeable;
* representative surface controls — confirm they call into the
  helper at construction time and pick up the read-only state.

The deeper defensive net (gateway-level guards) is covered in
``tests/test_read_only_mode.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.library_lock import LockInfo
from mira import session as mira_session
from mira.session import set_read_only


@pytest.fixture(autouse=True)
def _reset_session_flag():
    mira_session.reset_for_tests()
    yield
    mira_session.reset_for_tests()


def _make_holder(host: str = "studio-pc") -> LockInfo:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return LockInfo(
        hostname=host, pid=4242, app_version="dev",
        acquired_at=now, heartbeat_at=now, mtime=0.0,
    )


# ── The helper itself ────────────────────────────────────────────


def test_disable_if_read_only_is_noop_when_writeable(qapp):
    from PyQt6.QtWidgets import QPushButton
    from mira.ui.read_only import disable_if_read_only

    btn = QPushButton("Save")
    try:
        assert disable_if_read_only(btn) is False
        assert btn.isEnabled() is True
        assert btn.toolTip() == ""
    finally:
        btn.deleteLater()


def test_disable_if_read_only_disables_button_with_tooltip(qapp):
    from PyQt6.QtWidgets import QPushButton
    from mira.ui.read_only import disable_if_read_only

    set_read_only(True, _make_holder("studio-pc"))
    btn = QPushButton("Save")
    try:
        assert disable_if_read_only(btn) is True
        assert btn.isEnabled() is False
        # Tooltip names the editing machine — the read_only_hint
        # contract.
        assert "studio-pc" in btn.toolTip()
    finally:
        btn.deleteLater()


def test_disable_if_read_only_supports_actions(qapp):
    """QAction (menu items) is the other accepted target."""
    from PyQt6.QtGui import QAction
    from mira.ui.read_only import disable_if_read_only

    set_read_only(True, _make_holder())
    action = QAction("Delete")
    try:
        assert disable_if_read_only(action) is True
        assert action.isEnabled() is False
        assert action.toolTip() != ""
    finally:
        action.deleteLater()


def test_read_only_hint_falls_back_when_no_holder(qapp):
    """When the holder is unknown (corrupt lock re-read in the §A
    heartbeat-loss path), the hint stays informative without
    naming a host."""
    from mira.ui.read_only import read_only_hint

    set_read_only(True, None)
    hint = read_only_hint()
    assert hint
    assert "host" not in hint  # placeholder not leaked


def test_refresh_read_only_controls_walks_collection(qapp):
    """The bulk-refresh helper disables every target in the passed
    iterable when read-only is on."""
    from PyQt6.QtWidgets import QPushButton
    from mira.ui.read_only import refresh_read_only_controls

    btns = [QPushButton(f"Save{i}") for i in range(3)]
    try:
        set_read_only(True, _make_holder())
        n = refresh_read_only_controls(btns)
        assert n == 3
        for b in btns:
            assert b.isEnabled() is False
    finally:
        for b in btns:
            b.deleteLater()


def test_refresh_read_only_controls_writeable_is_noop(qapp):
    """A writeable session doesn't re-enable previously disabled
    controls (we never go back to writeable in the same session)."""
    from PyQt6.QtWidgets import QPushButton
    from mira.ui.read_only import refresh_read_only_controls

    btn = QPushButton("Save")
    btn.setEnabled(False)
    try:
        assert refresh_read_only_controls([btn]) == 0
        # Untouched.
        assert btn.isEnabled() is False
    finally:
        btn.deleteLater()


# ── Surface sweep — pin the representative controls ─────────────


def test_library_page_new_cut_button_disabled_when_read_only(
    qapp, monkeypatch,
):
    """spec/76 §B.1 — the cross-event Library page's `+ New Cut`
    button is the highest-traffic mira.db mutator entry. Set the
    read-only flag, build the page, assert the button is greyed."""
    set_read_only(True, _make_holder("studio-pc"))

    # Stub the gateway — the page only reads counts at build time;
    # it doesn't need a live one.
    class _StubGateway:
        def cross_event_cuts(self):
            return []

        def library_gateway(self):
            return _StubLibrary()

        def index(self):
            return None

    class _StubLibrary:
        def cross_event_cuts(self):
            return []

        def dynamic_collections(self):
            return []

        def recipe_definitions(self):
            return []

        def cross_event_cut_member_count(self, _id):
            return 0

    from mira.ui.pages.library_page import LibraryPage

    page = LibraryPage(_StubGateway())
    try:
        # The page builds a "+ New Cut" primary_button in the Cuts
        # band header. Find it by its visible label.
        from PyQt6.QtWidgets import QPushButton
        new_buttons = [
            b for b in page.findChildren(QPushButton)
            if b.text() == "+ New Cut"
        ]
        assert new_buttons, "library page missing the + New Cut button"
        for b in new_buttons:
            assert b.isEnabled() is False
            assert "studio-pc" in b.toolTip()
    finally:
        page.deleteLater()
