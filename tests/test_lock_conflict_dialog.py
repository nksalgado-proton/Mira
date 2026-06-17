"""Tests for the §A.4 conflict dialog — spec/76 §A.4.

The dialog text + buttons match the final §A.4 contract: it names
the editing machine, offers **Open read-only** (→ §B.1) and
**Cancel**, and never shows a "Take over editing" button —
``core.library_lock.acquire`` auto-takes-over stale locks before
startup reaches this code path (Nelson 2026-06-17 confirmation).

The dialog is driven via :class:`mira.ui.design.dialogs.MessageDialog`,
which we monkeypatch so the test can simulate a button click without
the modal actually showing on a real screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from core.library_lock import LockInfo


@dataclass
class _DialogCall:
    """Captures the constructor args of the patched MessageDialog so
    the test can assert on title / message / button text."""
    intent: str
    title: str
    message: str
    primary_text: str
    ghost_text: str | None
    secondary_text: str | None
    result_kind: str = "cancel"


def _make_holder(host: str = "studio-pc") -> LockInfo:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return LockInfo(
        hostname=host, pid=4242, app_version="dev",
        acquired_at=now, heartbeat_at=now, mtime=0.0,
    )


def _patch_message_dialog(monkeypatch, click: str):
    """Replace ``MessageDialog`` with a recorder that doesn't show
    a real window. ``click`` is "primary" (Open read-only) or
    "cancel"."""
    import mira.ui.app as app_mod
    captured: list[_DialogCall] = []

    class _FakeDialog:
        def __init__(self, *, intent, title, message, primary_text,
                     ghost_text=None, secondary_text=None, **kw):
            captured.append(_DialogCall(
                intent=intent, title=title, message=message,
                primary_text=primary_text, ghost_text=ghost_text,
                secondary_text=secondary_text,
            ))
            self._kind = click

        def exec(self):
            return 1

        def result_kind(self):
            return self._kind

    monkeypatch.setattr(
        "mira.ui.design.dialogs.MessageDialog", _FakeDialog)
    return captured


def test_dialog_names_holder_and_offers_read_only(qapp, monkeypatch):
    """Primary click returns ``"read_only"`` and the message names the
    editing host + acquire time (§A.4 contract)."""
    import mira.ui.app as app_mod
    captured = _patch_message_dialog(monkeypatch, click="primary")

    holder = _make_holder("studio-pc")
    result = app_mod._show_lock_conflict_dialog(holder)
    assert result == "read_only"

    assert len(captured) == 1
    call = captured[0]
    assert call.intent == "warning"
    assert "studio-pc" in call.message
    assert holder.acquired_at in call.message
    assert call.primary_text == "Open read-only"
    assert call.ghost_text == "Cancel"
    # §A.4 (final): no "Take over editing" button.
    assert call.secondary_text is None


def test_dialog_cancel_returns_cancel(qapp, monkeypatch):
    """Ghost click returns ``"cancel"`` — main() aborts launch."""
    import mira.ui.app as app_mod
    _patch_message_dialog(monkeypatch, click="cancel")
    assert app_mod._show_lock_conflict_dialog(_make_holder()) == "cancel"
