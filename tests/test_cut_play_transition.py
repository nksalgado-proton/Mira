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


def test_explicit_transition_ms_overrides_parent_lookup(qapp):
    """spec/152 §3 — when the host passes ``transition_ms=`` at
    construction, that value wins over the parent-walking heuristic
    that the legacy path uses. This is what stops the rehearsal
    from disagreeing with the PTE generator + budget on the per-Cut
    transition: PTE reads the per-Cut value via
    ``share_cuts_page._transition_ms(cut)``; the dialog now receives
    the same value explicitly. Without this, a per-Cut override
    would silently fall through to the global default in the
    rehearsal — yielding the 13-min cut_play vs PTE shortfall the
    user reported."""
    payload = SimpleNamespace(
        kind="photo", export_relpath="a.jpg", duration_ms=0)
    entries = [("file", payload), ("file", payload)]
    dlg = CutPlayerDialog(
        entries, event_root=Path("/ignored"),
        photo_s=6.0, day_meta={}, aspect="16:9",
        transition_ms=1500)
    try:
        assert dlg._transition_ms_value() == 1500
        # And the per-entry slot picks it up — 6_000 + 1_500 = 7_500.
        assert dlg._entry_total_ms(0) == 7_500
    finally:
        dlg._teardown_media()
        dlg.deleteLater()


def test_explicit_transition_ms_zero_means_hard_cut(qapp):
    """``transition_ms=0`` (the explicit "no transition" choice)
    yields a slot of just ``photo_ms`` and a 0 boundary — same
    behaviour as before spec/152."""
    payload = SimpleNamespace(
        kind="photo", export_relpath="a.jpg", duration_ms=0)
    entries = [("file", payload), ("file", payload)]
    dlg = CutPlayerDialog(
        entries, event_root=Path("/ignored"),
        photo_s=6.0, day_meta={}, aspect="16:9",
        transition_ms=0)
    try:
        assert dlg._transition_ms_value() == 0
        assert dlg._entry_total_ms(0) == 6_000
        assert dlg._boundary_transition_ms(0, 1) == 0
    finally:
        dlg._teardown_media()
        dlg.deleteLater()


def test_boundary_transition_ms_photo_to_photo_is_full(qapp):
    """spec/152 Phase 3 — a photo→photo boundary keeps the full
    transition_ms (the in-app crossfade matches PTE's [Times] slot
    contribution from the outgoing photo)."""
    dlg = _make_dialog(transition_ms=2000)
    try:
        # entries[0] and entries[1] are both photos in _make_dialog.
        assert dlg._boundary_transition_ms(0, 1) == 2000
    finally:
        dlg._teardown_media()
        dlg.deleteLater()


def test_boundary_transition_ms_photo_to_video_is_half(qapp):
    """spec/152 Phase 3 — a photo→video boundary uses HALF
    transition_ms; only the photo side does its part of the fade
    (the video hard-cuts in). The wall-clock shortfall is
    reclaimed by the global video_rate slowdown."""
    payload_p = SimpleNamespace(
        kind="photo", export_relpath="p.jpg", duration_ms=0)
    payload_v = SimpleNamespace(
        kind="video", export_relpath="v.mp4", duration_ms=10_000)
    entries = [("file", payload_p), ("file", payload_v)]
    dlg = CutPlayerDialog(
        entries, event_root=Path("/ignored"),
        photo_s=6.0, day_meta={}, aspect="16:9")
    dlg._transition_ms_value = lambda: 2000
    try:
        assert dlg._boundary_transition_ms(0, 1) == 1000
    finally:
        dlg._teardown_media()
        dlg.deleteLater()


def test_boundary_transition_ms_video_to_video_is_zero(qapp):
    """spec/152 Phase 3 — a video→video boundary skips the overlay
    entirely. The bridge is the frozen last-frame on the photo
    widget while the new clip's first frame loads (no black gap)."""
    payload_a = SimpleNamespace(
        kind="video", export_relpath="a.mp4", duration_ms=10_000)
    payload_b = SimpleNamespace(
        kind="video", export_relpath="b.mp4", duration_ms=8_000)
    entries = [("file", payload_a), ("file", payload_b)]
    dlg = CutPlayerDialog(
        entries, event_root=Path("/ignored"),
        photo_s=6.0, day_meta={}, aspect="16:9")
    dlg._transition_ms_value = lambda: 2000
    try:
        assert dlg._boundary_transition_ms(0, 1) == 0
    finally:
        dlg._teardown_media()
        dlg.deleteLater()


def test_boundary_transition_ms_zero_setting_short_circuits(qapp):
    """When the global transition setting is 0 ("hard cut"),
    every boundary returns 0 regardless of kind."""
    dlg = _make_dialog(transition_ms=0)
    try:
        assert dlg._boundary_transition_ms(0, 1) == 0
    finally:
        dlg._teardown_media()
        dlg.deleteLater()


def test_overlay_pixmap_preserves_logical_size_on_hidpi_capture(qapp):
    """spec/152 Phase 2 (regression for the "pop to smaller" reported
    against commit 33708f0): on a HiDPI display ``self._photo.grab()``
    returns a pixmap whose LOGICAL size matches the canvas (its device
    pixel count is ``logical * dpr``). The pre-fix ``_show_transition_
    overlay`` called ``QPixmap.scaled(canvas_logical_size, …)``, which
    Qt6 interprets as DEVICE pixels — the result rendered at half
    logical size on DPR=2 screens, which the user saw as the photo
    "popping smaller" the instant the overlay raised. The fix bypasses
    the scale when the source's logical size already matches the
    canvas, so the overlay paints at exactly the canvas's logical size.

    Note we check LOGICAL size, not the DPR attribute: ``QLabel``
    internally normalises the stored pixmap to the screen's DPR, so
    ``QLabel.pixmap().devicePixelRatio()`` reflects the screen, not
    the source. The load-bearing invariant is that the logical size
    (``pixmap.size() / pixmap.devicePixelRatio()``) equals the canvas
    — which is what controls how big the photo appears."""
    dlg = _make_dialog(transition_ms=400)
    try:
        dlg._stack_widget.resize(QSize(1000, 600))
        # Simulate a HiDPI grab: 2000×1200 device pixels at DPR=2.0
        # → logical 1000×600 (matching the canvas).
        hidpi = QPixmap(2000, 1200)
        hidpi.fill()
        hidpi.setDevicePixelRatio(2.0)
        dlg._raw_pixmap = hidpi
        dlg._index = 0
        dlg._start_transition_fade(hidpi)
        pm = dlg._transition_overlay.pixmap()
        assert pm is not None and not pm.isNull()
        dpr = pm.devicePixelRatio() or 1.0
        logical_w = int(round(pm.width() / dpr))
        logical_h = int(round(pm.height() / dpr))
        assert logical_w == 1000, (
            f"spec/152 Phase 2: overlay pixmap logical width "
            f"{logical_w} ≠ canvas width 1000 — the 'pop to smaller' "
            f"regression. devicePixelRatio={dpr}, "
            f"pixel size={pm.size()}"
        )
        assert logical_h == 600, (
            f"spec/152 Phase 2: overlay pixmap logical height "
            f"{logical_h} ≠ canvas height 600 — the 'pop to smaller' "
            f"regression. devicePixelRatio={dpr}, "
            f"pixel size={pm.size()}"
        )
    finally:
        dlg._teardown_media()
        dlg.deleteLater()
