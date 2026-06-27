"""spec/54 + Nelson Look Strength control — UI half. The AdjustmentSurface
carries a 0..2 Look-strength multiplier under the Look group.

spec/157 (Nelson 2026-06-27) replaced the continuous slider with a −5..+5
graduation DROPDOWN (0 = default = 1.0; +5 = 2.0; −5 = 0.0), side by side
with the Exposure dropdown. The underlying value (``_look_strength``,
0..2) + the render seam are unchanged — only the control. These tests pin
the dropdown behaviour + the unchanged render/round-trip contract.
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
    full = np.full((40, 60, 3), 100, dtype=np.uint8)
    preview = full.copy()
    natural = Params(exposure=0.4, contrast=10.0)
    s.load_prepared(full, preview, natural, style="general")
    return s


# ── construction defaults ────────────────────────────────────────────


def test_combo_constructs_at_one(surface):
    assert surface._look_strength == 1.0
    # Middle step (0) = 1.0; data is the underlying value.
    assert surface._strength_combo.currentData() == pytest.approx(1.0)
    assert surface._strength_combo.count() == 11        # −5..+5


def test_default_state_carries_strength_field(surface):
    st = surface.get_state()
    assert isinstance(st, SurfaceState)
    assert st.look_strength == 1.0


# ── picking a step ───────────────────────────────────────────────────


def test_picking_a_step_updates_strength(surface):
    # +5 step (last item) → 2.0; −5 step (first) → 0.0.
    surface._strength_combo.setCurrentIndex(surface._strength_combo.count() - 1)
    assert surface._look_strength == pytest.approx(2.0)
    surface._strength_combo.setCurrentIndex(0)
    assert surface._look_strength == pytest.approx(0.0)


def test_picking_a_step_emits_changed_tone(surface):
    """A dropdown pick is a settled change — it renders + emits
    ``changed("tone")`` immediately so the host persists look_strength."""
    captured = []
    surface.changed.connect(lambda kind: captured.append(kind))
    surface._strength_combo.setCurrentIndex(8)          # +3 step → 1.6
    assert captured == ["tone"]
    assert surface._look_strength == pytest.approx(1.6)


# ── set_state / get_state round-trip ────────────────────────────────


def test_set_state_loads_strength(surface):
    surface.set_state(
        look="punch", crop_norm=None, box_angle=0.0,
        style="wildlife", aspect_label="Original",
        look_strength=0.6,
    )
    assert surface._look_strength == pytest.approx(0.6)
    assert surface._strength_combo.currentData() == pytest.approx(0.6)


def test_set_state_clamps_out_of_range_strength(surface):
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
    surface._strength_combo.setCurrentIndex(9)          # +4 step → 1.8
    st = surface.get_state()
    assert st.look_strength == pytest.approx(1.8)


def test_set_state_does_not_emit_changed(surface):
    captured = []
    surface.changed.connect(lambda k: captured.append(k))
    surface.set_state(
        look="punch", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=0.4,
    )
    assert "tone" not in captured


# ── control × Look interaction ───────────────────────────────────────


def test_strength_disabled_on_original(surface):
    surface.set_state(
        look="original", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=1.0,
    )
    assert surface._strength_combo.isEnabled() is False
    assert surface._strength_label.isEnabled() is False


def test_strength_enabled_on_natural(surface):
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=1.0,
    )
    assert surface._strength_combo.isEnabled() is True


def test_switching_to_original_disables_strength(surface):
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=0.8,
    )
    assert surface._strength_combo.isEnabled() is True
    surface.set_look("original")
    assert surface._strength_combo.isEnabled() is False


# ── render seam threads strength through (unchanged) ─────────────────


def test_params_for_look_threads_strength_into_compile(surface):
    """The compiled Params at strength 0.5 are exactly half of those at
    strength 1.0 — the render seam goes through
    look_params_from_natural(..., strength=...). The control change is
    cosmetic; the value drives the math."""
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=1.0,
    )
    full = surface._params_for_look()
    surface.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        look_strength=0.5,
    )
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
    assert surface._strength_combo.currentData() == pytest.approx(1.0)
