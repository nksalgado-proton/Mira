"""spec/152 §3 per-Cut transition_ms — gateway + draft + session
round-trip.

The Phase 1 commit added a single global Settings.default_transition_ms
the show-length math read from. Users wanted per-Cut overrides next to
``photo_s`` in the New / Adjust dialog: the dialog seeds the spinbox
from the global default but persists the user's actual choice as
``cut.transition_ms`` only when they change it (NULL otherwise, so a
future global tweak still reaches the Cuts the user never customised).

Tests pin:

* ``create_cut(transition_ms=N)`` round-trips to ``cut.transition_ms == N``.
* ``create_cut`` without the kwarg leaves ``cut.transition_ms`` NULL.
* ``update_cut_settings(transition_ms=N)`` updates an existing Cut.
* ``CutDraft.transition_ms`` flows through ``CutSession.from_draft``
  into ``session.transition_ms`` and lands on ``cut.transition_ms``
  via ``session.commit()``.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_draft import CutDraft
from mira.shared.cut_session import CutSession
from mira.store.repo import EventStore


_NOW = "2026-06-25T12:00:00"


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-t")
    counter = itertools.count(1)
    return EventGateway(
        store, event_root=tmp_path, now=lambda: _NOW,
        new_id=lambda: f"id-{next(counter)}")


# ── Gateway: create_cut / update_cut_settings ──────────────────────


def test_create_cut_without_transition_leaves_column_null(gw):
    """spec/152 §3 — the default ``transition_ms=None`` parameter
    leaves the Cut's column NULL. A NULL there is the signal that the
    Cut never overrode the global → readers fall back to
    Settings.default_transition_ms."""
    cut = gw.create_cut("untouched", photo_s=6.0)
    assert cut.transition_ms is None


def test_create_cut_with_explicit_transition_persists_it(gw):
    """spec/152 §3 — an explicit per-Cut value rides through
    create_cut to the column. ``0`` is the "hard cuts" opt-out and
    must persist as 0, not be coerced to None."""
    cut_a = gw.create_cut("smooth", photo_s=6.0, transition_ms=1500)
    cut_b = gw.create_cut("snappy", photo_s=6.0, transition_ms=0)
    assert cut_a.transition_ms == 1500
    assert cut_b.transition_ms == 0


def test_update_cut_settings_can_set_transition(gw):
    """spec/152 §3 — Adjust → Start runs through update_cut_settings;
    the per-Cut transition value lands like every other Cut field."""
    cut = gw.create_cut("c", photo_s=6.0)
    gw.update_cut_settings(cut.id, transition_ms=750)
    refreshed = gw.cut(cut.id)
    assert refreshed.transition_ms == 750


def test_update_cut_settings_can_clear_transition_to_null(gw):
    """spec/152 §3 — toggling the spinbox back to the global default
    re-emits no field on the round trip; if a caller explicitly
    passes ``transition_ms=None`` the column is reset to NULL so the
    global default takes over again."""
    cut = gw.create_cut("c", photo_s=6.0, transition_ms=1234)
    assert gw.cut(cut.id).transition_ms == 1234
    gw.update_cut_settings(cut.id, transition_ms=None)
    assert gw.cut(cut.id).transition_ms is None


# ── Draft → session → cut round-trip ───────────────────────────────


def _draft(transition_ms=None) -> CutDraft:
    return CutDraft(
        name="round-trip",
        tag="round_trip",
        target_s=None, max_s=None,
        photo_s=6.0,
        transition_ms=transition_ms,
        music_category=None,
        separators=True,
    )


def test_draft_transition_flows_through_session_commit(gw):
    """spec/152 §3 — CutDraft.transition_ms reaches the persisted
    Cut via from_draft → CutSession → commit. Mirrors the dialog
    behaviour: the user moves the spinbox, the draft carries the
    value, the session writes it on Start."""
    draft = _draft(transition_ms=1800)
    session = CutSession.from_draft(gw, draft)
    assert session.transition_ms == 1800
    cut = session.commit(gw)
    assert cut.transition_ms == 1800


def test_draft_with_no_transition_keeps_cut_column_null(gw):
    """spec/152 §3 — a draft that didn't carry a value (the user
    never touched the spinbox) leaves the Cut column NULL so the
    global default still wins at read time."""
    draft = _draft(transition_ms=None)
    session = CutSession.from_draft(gw, draft)
    assert session.transition_ms is None
    cut = session.commit(gw)
    assert cut.transition_ms is None
