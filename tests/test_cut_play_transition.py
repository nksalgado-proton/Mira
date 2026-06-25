"""spec/152 Phase 2 — cut-play renders a crossfade between consecutive
entries.

The pre-Phase-2 cut_play had a hard-cut between every entry, which on
the WMF / Qt6 Windows backend showed a ~1 s black flash on
video → media boundaries (the ``QVideoWidget`` surface drops out
before the next entry's pixmap paints). Phase 2 fixes this by:

* Capturing the outgoing entry's pixels (a ``QPixmap`` for
  photo / sep / opener; ``QVideoSink.videoFrame()`` for video) at the
  top of ``_show_index``, BEFORE the player.stop() teardown.
* Showing a ``QLabel`` overlay above the canvas with the captured
  pixmap.
* Animating its opacity 1.0 → 0.0 over the Settings'
  ``default_transition_ms`` via ``QPropertyAnimation``.
* Hiding the overlay when the animation finishes.

Tests pin:

* ``transition_ms == 0`` (the "hard cut" Settings choice) skips the
  overlay entirely — the legacy behaviour.
* A non-zero ``transition_ms`` constructs the overlay lazily on the
  first swap and starts the animation with the configured duration.
* The overlay is hidden again after the animation finishes (no leak,
  no stale frame sitting on top of the next entry).
* ``_teardown_media`` stops a half-finished fade so the dialog can
  close without the animation outliving it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QPixmap

from mira.ui.shared.cut_play import CutPlayerDialog


# ── Fixtures ───────────────────────────────────────────────────────


def _make_dialog(transition_ms: int) -> CutPlayerDialog:
    """A minimal CutPlayerDialog with two photo entries and a stubbed
    ``_transition_ms_value`` returning ``transition_ms``. We stub the
    method directly instead of plumbing a fake parent widget so the
    test doesn't have to mock out the host page's ``_settings()``
    chain."""
    payload_a = SimpleNamespace(
        kind="photo", export_relpath="a.jpg",
        duration_ms=0)
    payload_b = SimpleNamespace(
        kind="photo", export_relpath="b.jpg",
        duration_ms=0)
    entries = [("file", payload_a), ("file", payload_b)]
    dlg = CutPlayerDialog(
        entries, event_root=Path("/ignored"),
        photo_s=6.0, day_meta={}, aspect="16:9")
    # The dialog's ``_transition_ms_value`` walks parent()._settings()
    # which doesn't exist in this stub context — override directly.
    dlg._transition_ms_value = lambda: int(transition_ms)
    return dlg


def _seed_outgoing_pixmap(dlg: CutPlayerDialog) -> None:
    """Pretend a previous entry was painted — set ``_raw_pixmap`` and
    advance ``_index`` so ``_show_index`` captures something on the
    next swap."""
    pm = QPixmap(64, 32)
    pm.fill()
    dlg._raw_pixmap = pm
    dlg._index = 0


# ── transition_ms == 0: hard cut (legacy) ──────────────────────────


def test_zero_transition_ms_skips_overlay(qapp):
    """spec/152 §3 — the Settings ``default_transition_ms = 0`` path
    is the explicit "no transition" opt-out. The overlay must NOT
    be constructed (no opacity effect, no animation running)."""
    dlg = _make_dialog(transition_ms=0)
    try:
        _seed_outgoing_pixmap(dlg)
        # Force the swap to entry 1 — the photo path inside
        # _show_index calls load_pixmap which won't find the file,
        # but _start_transition_fade still runs ABOVE that with the
        # captured outgoing pixmap. We just need to verify the fade
        # path is skipped.
        dlg._start_transition_fade(dlg._raw_pixmap)
        assert dlg._transition_overlay is None, (
            "spec/152 §3: transition_ms = 0 must NOT construct the "
            "overlay — the legacy hard-cut path is preserved"
        )
    finally:
        dlg._teardown_media()
        dlg.deleteLater()


# ── transition_ms > 0: overlay + animation ─────────────────────────


def test_nonzero_transition_starts_animation(qapp):
    """spec/152 Phase 2 — a non-zero transition_ms constructs the
    overlay lazily and starts the opacity 1 → 0 animation with the
    configured duration. (The widget's ``isVisible()`` lands True
    only once the parent dialog is shown on screen, which we don't
    do in this stub test; the running animation is the load-
    bearing assertion.)"""
    dlg = _make_dialog(transition_ms=400)
    try:
        dlg._stack_widget.resize(QSize(320, 180))
        _seed_outgoing_pixmap(dlg)
        dlg._start_transition_fade(dlg._raw_pixmap)
        assert dlg._transition_overlay is not None, (
            "spec/152 Phase 2: a fade must construct the overlay"
        )
        assert dlg._transition_anim is not None
        assert dlg._transition_anim.duration() == 400, (
            "spec/152 §3: the animation duration equals the "
            "Settings transition_ms value"
        )
        # The animation is Running (state value 2) right after start.
        assert dlg._transition_anim.state().value == 2, (
            "spec/152 Phase 2: the animation must be running after "
            "_start_transition_fade returns"
        )
        # The overlay carries the captured pixmap (non-null).
        pm = dlg._transition_overlay.pixmap()
        assert pm is not None and not pm.isNull(), (
            "spec/152 Phase 2: the overlay's pixmap is what fades "
            "out — it must be non-null while the fade runs"
        )
    finally:
        dlg._teardown_media()
        dlg.deleteLater()


def test_animation_finished_clears_overlay_pixmap(qapp):
    """spec/152 Phase 2 — when the QPropertyAnimation reaches the
    end the overlay's pixmap is cleared so the next swap captures
    fresh bytes instead of reusing the stale one (and the overlay
    stops painting the old frame on top of the next entry)."""
    dlg = _make_dialog(transition_ms=300)
    try:
        dlg._stack_widget.resize(QSize(320, 180))
        _seed_outgoing_pixmap(dlg)
        dlg._start_transition_fade(dlg._raw_pixmap)
        # Simulate the animation finish callback directly — the
        # animation itself runs on the Qt event loop and would
        # require a wait; firing the slot directly keeps the test
        # synchronous.
        dlg._on_transition_finished()
        pm = dlg._transition_overlay.pixmap()
        assert pm is None or pm.isNull(), (
            "spec/152 Phase 2: _on_transition_finished must clear "
            "the overlay's pixmap so the next swap starts fresh"
        )
    finally:
        dlg._teardown_media()
        dlg.deleteLater()


def test_teardown_stops_transition_animation(qapp):
    """spec/152 Phase 2 — closing the dialog mid-fade stops the
    animation cleanly. Otherwise the animation's ``finished`` slot
    could fire on a half-destroyed widget."""
    dlg = _make_dialog(transition_ms=500)
    try:
        dlg._stack_widget.resize(QSize(320, 180))
        _seed_outgoing_pixmap(dlg)
        dlg._start_transition_fade(dlg._raw_pixmap)
        assert dlg._transition_anim.state().value != 0  # Running
        dlg._teardown_media()
        # Animation must be Stopped (state value 0) after teardown.
        assert dlg._transition_anim.state().value == 0, (
            "spec/152 Phase 2: _teardown_media must stop the fade "
            "animation so its finished slot doesn't fire on a "
            "destroyed widget"
        )
    finally:
        dlg.deleteLater()


def test_null_outgoing_pixmap_is_a_no_op(qapp):
    """spec/152 Phase 2 — when the outgoing capture comes back null
    (e.g. first slide / video frame capture failure on some Qt
    backends) the fade is silently skipped. The new entry hard-cuts
    in — same behaviour as the pre-Phase-2 code."""
    dlg = _make_dialog(transition_ms=400)
    try:
        # Don't set _raw_pixmap; _capture_outgoing_pixmap returns None.
        dlg._start_transition_fade(QPixmap())  # null pixmap
        assert dlg._transition_overlay is None, (
            "spec/152 Phase 2: a null outgoing pixmap must short-"
            "circuit so we don't construct an overlay we can't "
            "actually fade meaningfully"
        )
    finally:
        dlg._teardown_media()
        dlg.deleteLater()
