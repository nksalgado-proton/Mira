"""Render-gating contracts for the Edit surface.

spec/115 §1 introduced render-on-slider-release for the Strength /
Exposure sliders. spec/157 (Nelson 2026-06-27) then replaced those
sliders with −5..+5 dropdowns — a combo pick is a single settled change
that renders immediately, so the drag / debounce state machine no longer
applies to them (its dedicated tests were retired with the sliders).

What still holds and is pinned here:

* the ``render_now`` signature cache drops redundant renders (and
  ``user_exposure`` is part of the signature);
* the reusable :class:`AdjustmentGrid` component's ``valueCommitted``
  contract (fires on slider-release / field-commit / reset, NOT on the
  live ``valueChanged`` tick) — used by other adjustment surfaces.
"""
from __future__ import annotations

import numpy as np
import pytest

from mira.ui.edited.adjustment_surface import AdjustmentSurface
from mira.ui.edited.adjustment_grid import AdjustmentGrid, AdjustmentSpec


# ── Helpers ──────────────────────────────────────────────────────


def _surface() -> AdjustmentSurface:
    s = AdjustmentSurface()
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    img[10, 20] = (200, 120, 40)
    s.load_image(img)
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


# ── Early-out: redundant renders are dropped ─────────────────────


def test_render_signature_skips_redundant_calls(qapp):
    """Calling render_now twice in a row with NO state change in
    between must skip the second one — the cached signature catches it."""
    s = _surface()
    s.render_now()
    counter = _RenderCounter(s)
    s.render_now()
    assert counter.count == 1, "wrap fires once"
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
    """The live tick signal must still exist — host UIs depend on it to
    update the numeric label."""
    g = _grid()
    live: list[tuple] = []
    g.valueChanged.connect(lambda k, v: live.append((k, v)))

    g._rows["exposure"].slider.setValue(750)
    assert len(live) >= 1, "valueChanged must fire on a tick"
