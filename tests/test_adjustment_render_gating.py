"""spec/115 §1 — render on slider RELEASE, not on every drag tick.

The old behaviour restarted a 40 ms debounce on every ``valueChanged``
and called ``render_now`` synchronously on the UI thread — a slow drag
was a stream of blocking renders. The fix:

* during a drag (``sliderPressed`` → N× ``valueChanged`` → ``sliderReleased``)
  render fires EXACTLY ONCE, on release;
* a keyboard / field / programmatic change (no release event) renders
  after a 150 ms settle debounce — the new ``RENDER_DEBOUNCE_MS``
  ("settled", not "blinked");
* ``AdjustmentGrid`` grew a ``valueCommitted`` signal that fires on
  ``sliderReleased``, ``editingFinished`` and ``reset`` (so render-on-
  release hosts wire to it); ``valueChanged`` stays the live tick.

These tests pin BOTH the state machine and the public contract."""
from __future__ import annotations

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from mira.ui.edited.adjustment_surface import (
    RENDER_DEBOUNCE_MS,
    AdjustmentSurface,
)
from mira.ui.edited.adjustment_grid import AdjustmentGrid, AdjustmentSpec


# ── Helpers ──────────────────────────────────────────────────────


def _surface() -> AdjustmentSurface:
    s = AdjustmentSurface()
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    img[10, 20] = (200, 120, 40)
    s.load_image(img)
    # Pick a Look so Strength is meaningful.
    s.set_look("natural")
    return s


class _RenderCounter:
    """Wrap ``render_now`` to count invocations without losing the real
    render path (the early-out + signature cache depend on it)."""

    def __init__(self, surface: AdjustmentSurface) -> None:
        self.surface = surface
        self.count = 0
        self._real = surface.render_now
        surface.render_now = self._wrap  # type: ignore[assignment]

    def _wrap(self):
        self.count += 1
        return self._real()


# ── Drag gating: render fires ONCE on release ────────────────────


def test_drag_press_changes_release_renders_once(qapp):
    """The canonical drag: sliderPressed → N× valueChanged →
    sliderReleased. EXACTLY one render_now call, on release."""
    s = _surface()
    counter = _RenderCounter(s)

    s._strength_slider.sliderPressed.emit()
    assert s._dragging is True
    for tick in (95, 90, 85, 80, 75):
        s._strength_slider.setValue(tick)
    assert counter.count == 0, "mid-drag must not render"

    s._strength_slider.sliderReleased.emit()
    assert s._dragging is False
    assert counter.count == 1, "exactly one render on release"


def test_drag_release_stops_the_debounce_timer(qapp):
    """Releasing must stop the keyboard debounce. Otherwise a release-
    triggered render would be followed ~150ms later by a redundant
    timeout-triggered second render."""
    s = _surface()
    counter = _RenderCounter(s)

    s._strength_slider.sliderPressed.emit()
    s._strength_slider.setValue(80)
    s._strength_slider.sliderReleased.emit()
    assert counter.count == 1
    assert not s._render_timer.isActive()


def test_drag_renders_changed_emit_fires_once_with_tone_kind(qapp):
    """The drag-release path is what the host persists from — `changed`
    must carry the "tone" kind so the editor_page persists Strength/
    Exposure to the Adjustment row."""
    s = _surface()
    received: list[str] = []
    s.changed.connect(received.append)

    s._strength_slider.sliderPressed.emit()
    s._strength_slider.setValue(70)
    s._strength_slider.sliderReleased.emit()

    assert received == ["tone"], received


# ── Keyboard / field / programmatic: settles via the debounce ────


def test_keyboard_step_renders_after_the_debounce(qapp):
    """A keyboard arrow / programmatic setValue fires valueChanged with
    no preceding sliderPressed → render_now must fire once after the
    150 ms debounce expires (not synchronously on the tick)."""
    s = _surface()
    counter = _RenderCounter(s)

    s._strength_slider.setValue(90)        # one keyboard-style step
    assert counter.count == 0, "value tick must not render synchronously"
    assert s._render_timer.isActive()
    assert s._render_timer.interval() == RENDER_DEBOUNCE_MS

    # Simulate the timer expiring (timeout is the seam the surface
    # listens on; firing it directly avoids a flaky ~150 ms wait).
    s._render_timer.stop()
    s._on_render_timer()
    assert counter.count == 1


def test_keyboard_settle_debounce_is_150_ms_not_40(qapp):
    """spec/115 §1 — the debounce was raised 40 → ~150 ms so a held
    arrow key doesn't fire one render per repeat. Pin the constant."""
    assert RENDER_DEBOUNCE_MS == 150


def test_holding_keyboard_keeps_resetting_the_debounce(qapp):
    """A held arrow key fires repeated ticks. While the user is still
    pressing keys, the timer must keep resetting — only render once
    after they STOP."""
    s = _surface()
    counter = _RenderCounter(s)

    for tick in (95, 90, 85, 80, 75, 70):
        s._strength_slider.setValue(tick)
        assert s._render_timer.isActive(), "every tick rearms the timer"
    assert counter.count == 0

    s._on_render_timer()
    assert counter.count == 1


# ── Double-click resets render immediately (no debounce) ─────────


def test_double_click_reset_renders_immediately(qapp):
    """Double-click is a settled gesture, not a tick — render NOW so the
    user sees the snap-back, don't wait for the debounce."""
    s = _surface()
    counter = _RenderCounter(s)

    s._strength_slider.setValue(50)        # warms the debounce
    s._strength_double_click(_FakeEvent())

    assert counter.count == 1
    assert not s._render_timer.isActive()


# ── Exposure slider mirrors the Strength state machine ───────────


def test_exposure_drag_renders_once_on_release(qapp):
    """The Exposure slider has its own valueChanged but shares the
    drag state machine. A drag releases ONE render, same as Strength."""
    s = _surface()
    counter = _RenderCounter(s)

    s._exposure_slider.sliderPressed.emit()
    for tick in (50, 80, 120, 150):
        s._exposure_slider.setValue(tick)
    assert counter.count == 0

    s._exposure_slider.sliderReleased.emit()
    assert counter.count == 1
    # The value landed at the last tick.
    assert s._user_exposure == pytest.approx(1.50)


def test_exposure_keyboard_step_renders_after_debounce(qapp):
    s = _surface()
    counter = _RenderCounter(s)

    s._exposure_slider.setValue(50)
    assert counter.count == 0
    assert s._render_timer.isActive()

    s._render_timer.stop()
    s._on_render_timer()
    assert counter.count == 1


# ── Early-out: redundant renders are dropped ─────────────────────


def test_render_signature_skips_redundant_calls(qapp):
    """Calling render_now twice in a row with NO state change in
    between must skip the second one — the cached signature catches
    it. Keeps the post-release debounce render cheap when the timer
    expires after a drag already rendered."""
    s = _surface()
    s.render_now()
    counter = _RenderCounter(s)
    s.render_now()
    assert counter.count == 1, "wrap fires once"
    # But the underlying render skipped — verify the signature equals
    # the one stamped by the previous call.
    sig_before = s._last_rendered_signature
    s.render_now()
    assert s._last_rendered_signature == sig_before


def test_render_signature_changes_on_user_exposure_tweak(qapp):
    """The user_exposure value is part of the signature; nudging it
    must invalidate the cache so the next render runs."""
    s = _surface()
    s.render_now()
    sig_before = s._last_rendered_signature
    s._user_exposure = 0.5
    sig_after = s._render_signature()
    assert sig_after != sig_before


# ── AdjustmentGrid: valueCommitted contract ──────────────────────


def _grid() -> AdjustmentGrid:
    return AdjustmentGrid([
        AdjustmentSpec(
            key="exposure", label="Exposure",
            minimum=-2.0, maximum=2.0, default=0.0,
            step=0.05, decimals=2, suffix=" EV"),
    ])


def test_grid_value_committed_fires_on_slider_released(qapp):
    g = _grid()
    received: list[tuple] = []
    g.valueCommitted.connect(lambda k, v: received.append((k, v)))

    # Drag-ish: the slider ticks live (valueChanged), THEN release.
    g._rows["exposure"].slider.setValue(500)        # mid-range
    assert received == [], "valueChanged does not fire valueCommitted"

    g._rows["exposure"].slider.sliderReleased.emit()
    assert len(received) == 1
    assert received[0][0] == "exposure"


def test_grid_value_committed_fires_on_field_commit(qapp):
    g = _grid()
    received: list[tuple] = []
    g.valueCommitted.connect(lambda k, v: received.append((k, v)))

    field = g._rows["exposure"].field
    field.setText("1.00 EV")
    field.editingFinished.emit()

    assert received == [("exposure", 1.0)]


def test_grid_value_committed_fires_on_reset(qapp):
    g = _grid()
    received: list[tuple] = []
    g.valueCommitted.connect(lambda k, v: received.append((k, v)))

    g._rows["exposure"].slider.setValue(800)
    g.reset("exposure")

    assert ("exposure", 0.0) in received


def test_grid_value_changed_still_fires_for_live_label(qapp):
    """The live tick signal must still exist — host UIs depend on it
    to update the numeric label. The render gating is on the host side
    (only valueCommitted drives render)."""
    g = _grid()
    live: list[tuple] = []
    g.valueChanged.connect(lambda k, v: live.append((k, v)))

    # Default is 0.0 EV which maps to tick 500 — moving to 750 is a
    # real change, so valueChanged fires.
    g._rows["exposure"].slider.setValue(750)
    assert len(live) >= 1, "valueChanged must fire on a tick"


# ── Helpers ──────────────────────────────────────────────────────


class _FakeEvent:
    """Minimal event stub for the slider mouseDoubleClickEvent override
    — only needs an .accept()."""

    def __init__(self) -> None:
        self.accepted = False

    def accept(self) -> None:
        self.accepted = True
