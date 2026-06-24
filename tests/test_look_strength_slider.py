"""spec/54 + Nelson Look Strength slider — Commit 2 UI half. The
AdjustmentSurface gains a 0..2 slider under the Look group:

- Default 1.0 at construct time (the Look as authored).
- Continuous, ticks at 1.0 (the snap point); double-click on the
  slider snaps back.
- Inert (disabled) when Look is Original — strength has no effect
  there (Params() × strength is still identity).
- get_state / set_state carry it round-trip alongside the existing
  SurfaceState fields.
- Reset all snaps strength back to 1.0.
- _params_for_look threads strength into look_params_from_natural,
  so the rendered preview = export by construction.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.photo_render import Params
from mira.ui.edited.adjustment_surface import (
    AdjustmentSurface,
    SurfaceState,
)


@pytest.fixture
def surface(qapp) -> AdjustmentSurface:
    s = AdjustmentSurface()
    # The slider's enabled state + the params compile both need a
    # loaded image. load_prepared takes (full, preview, natural).
    full = np.full((40, 60, 3), 100, dtype=np.uint8)
    preview = full.copy()
    natural = Params(exposure=0.4, contrast=10.0)
    s.load_prepared(full, preview, natural, style="general")
    return s


# ── construction defaults ────────────────────────────────────────────


def test_slider_constructs_at_one(surface):
    assert surface._strength_slider.value() == 100
    assert surface._look_strength == 1.0
    assert surface._strength_value.text() == "1.00"


def test_slider_default_state_carries_strength_field(surface):
    st = surface.get_state()
    assert isinstance(st, SurfaceState)
    assert st.look_strength == 1.0


# ── moving the slider ────────────────────────────────────────────────


def test_moving_the_slider_updates_strength_and_label(surface):
    surface._strength_slider.setValue(150)
    assert surface._look_strength == pytest.approx(1.5)
    assert surface._strength_value.text() == "1.50"
    surface._strength_slider.setValue(0)
    assert surface._look_strength == 0.0
    assert surface._strength_value.text() == "0.00"


def test_moving_the_slider_emits_changed_tone_on_release(surface, qtbot=None):
    """spec/115 §1 — ``changed("tone")`` now fires on the COMMIT event
    (slider release for a drag, or the debounce timer for a keyboard /
    programmatic change) rather than per-tick. A bare ``setValue``
    counts as the keyboard-style path; the surface arms its render
    timer and emits ``changed("tone")`` from the timer's slot."""
    captured = []
    surface.changed.connect(lambda kind: captured.append(kind))
    surface._strength_slider.setValue(120)
    # Programmatic tick → live label only, no emit yet.
    assert captured == []
    assert surface._render_timer.isActive()
    # Fire the debounce manually to avoid waiting ~150 ms.
    surface._render_timer.stop()
    surface._on_render_timer()
    assert "tone" in captured


def test_drag_release_emits_changed_tone_once(surface):
    """The drag path also fires ``changed("tone")`` exactly once on
    release — the host persists the new look_strength from there."""
    captured = []
    surface.changed.connect(lambda kind: captured.append(kind))
    surface._strength_slider.sliderPressed.emit()
    surface._strength_slider.setValue(140)
    surface._strength_slider.setValue(160)
    assert captured == []
    surface._strength_slider.sliderReleased.emit()
    assert captured == ["tone"]


def test_double_click_snaps_slider_to_one(surface):
    surface._strength_slider.setValue(150)
    assert surface._strength_slider.value() == 150
    # Simulate the double-click handler (no real mouse event needed).
    class _E:
        accepted = False
        def accept(self):
            self.accepted = True
    e = _E()
    surface._strength_double_click(e)
    assert surface._strength_slider.value() == 100
    assert surface._look_strength == 1.0
    assert e.accepted is True


# ── set_state / get_state round-trip ────────────────────────────────


def test_set_state_loads_strength(surface):
    surface.set_state(
        look="punch", crop_norm=None, box_angle=0.0,
        style="wildlife", aspect_label="Original",
        look_strength=0.7,
    )
    assert surface._look_strength == 0.7
    assert surface._strength_slider.value() == 70
    assert surface._strength_value.text() == "0.70"


def test_set_state_clamps_out_of_range_strength(surface):
    """A stale Adjustment row with a wild strength (e.g. a downgraded
    v5→v4→v5 round-trip that lost the CHECK) must clamp on load —
    the gateway seam mirror."""
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=5.0,
    )
    assert surface._look_strength == 2.0
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=-1.0,
    )
    assert surface._look_strength == 0.0


def test_get_state_returns_current_strength(surface):
    surface._strength_slider.setValue(170)
    st = surface.get_state()
    assert st.look_strength == pytest.approx(1.7)


def test_set_state_does_not_emit_changed(surface):
    captured = []
    surface.changed.connect(lambda k: captured.append(k))
    surface.set_state(
        look="punch", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=0.5,
    )
    # set_state is the "host pushes saved CHOICE in" path. The
    # slider's valueChanged fires but the _loading guard suppresses
    # the changed emission — otherwise opening a photo would re-
    # write its own Adjustment row.
    assert "tone" not in captured


# ── slider × Look interaction ────────────────────────────────────────


def test_strength_disabled_on_original(surface):
    surface.set_state(
        look="original", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=1.0,
    )
    assert surface._strength_slider.isEnabled() is False
    assert surface._strength_label.isEnabled() is False


def test_strength_enabled_on_natural(surface):
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=1.0,
    )
    assert surface._strength_slider.isEnabled() is True


def test_switching_to_original_disables_strength(surface):
    # Start on natural with the slider enabled.
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=0.8,
    )
    assert surface._strength_slider.isEnabled() is True
    # Click "original" — surface uses set_look.
    surface.set_look("original")
    assert surface._strength_slider.isEnabled() is False


# ── render seam threads strength through ────────────────────────────


def test_params_for_look_threads_strength_into_compile(surface):
    """The compiled Params at strength 0.5 are exactly half of those
    at strength 1.0 — the render seam goes through
    look_params_from_natural(..., strength=...) and the math wins."""
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=1.0,
    )
    full = surface._params_for_look()
    surface._strength_slider.setValue(50)
    half = surface._params_for_look()
    for f in full.__dataclass_fields__:
        if abs(getattr(full, f)) > 1e-6:
            assert abs(getattr(half, f) - 0.5 * getattr(full, f)) < 1e-4


def test_params_for_look_at_strength_zero_is_identity(surface):
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=0.0,
    )
    assert surface._params_for_look().is_identity is True


# ── Reset all snaps strength back to 1.0 ─────────────────────────────


def test_reset_all_snaps_strength_to_one(surface):
    surface.set_state(
        look="punch", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=1.6,
    )
    assert surface._look_strength == 1.6
    surface._on_reset_all()
    assert surface._look_strength == 1.0
    assert surface._strength_slider.value() == 100
