"""spec/58 slice 3 — the Style combo's classification badge.

Pins: the confidence→band mapping (v0 thresholds pending Nelson's live
calibration), the badge property + tooltip status the surface paints,
the ``activated``-backed human-decision flip (fires even when the user
re-picks the shown style; NEVER fires while loading state in), and the
QSS grammar rule: all four band variants exist in BOTH themes.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mira.ui.edited.adjustment_surface import (
    AdjustmentSurface, classification_band, normalize_style,
)

_THEMES = Path(__file__).resolve().parents[1] / "assets" / "themes"


def _surface(qapp) -> AdjustmentSurface:
    s = AdjustmentSurface()
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    img[10, 20] = (200, 120, 40)
    s.load_image(img)
    return s


# --------------------------------------------------------------------------- #
# The band mapping (pure)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("source,confidence,band", [
    ("user", None, "human"),
    ("user", 0.99, "human"),        # user beats any score
    (None, None, "low"),            # never classified → "needs your eye"
    ("auto", None, "low"),          # pre-confidence row → same
    ("auto", 0.0, "low"),
    ("auto", 0.54, "low"),
    ("auto", 0.55, "mid"),
    ("auto", 0.79, "mid"),
    ("auto", 0.80, "high"),
    ("auto", 1.0, "high"),
])
def test_classification_band_mapping(source, confidence, band):
    assert classification_band(source, confidence) == band


def test_normalize_style_clamps_unsupported():
    assert normalize_style("wildlife") == "wildlife"
    assert normalize_style("sports") == "general"
    assert normalize_style(None) == "general"
    assert normalize_style("") == "general"


# --------------------------------------------------------------------------- #
# The surface badge
# --------------------------------------------------------------------------- #


def test_badge_sets_property_and_tooltip_status(qapp):
    s = _surface(qapp)
    s.set_classification_badge("auto", 0.9)
    assert s._style_combo.property("confidenceBand") == "high"
    assert "90%" in s._style_combo.toolTip()
    s.set_classification_badge("auto", None)
    assert s._style_combo.property("confidenceBand") == "low"
    s.set_classification_badge("user", None)
    assert s._style_combo.property("confidenceBand") == "human"
    # The base hint survives in front of the status line.
    assert s._style_combo.toolTip().startswith(s._style_tooltip_base)


def test_activated_emits_style_decided_and_flips_human(qapp):
    """spec/58 §2 — picking a style (EVEN the one already shown) IS the
    human decision: ``style_decided`` carries the style, the badge flips
    to ``human`` without waiting for the host's write-back."""
    s = _surface(qapp)
    s.set_classification_badge("auto", 0.9)
    decided: list[str] = []
    s.style_decided.connect(decided.append)
    # Same-index pick — exactly what ``currentIndexChanged`` can't see.
    s._style_combo.activated.emit(s._style_combo.currentIndex())
    assert decided == [s._style_combo.currentData()]
    assert s._style_combo.property("confidenceBand") == "human"


def test_loading_or_imageless_pick_never_decides(qapp):
    s = _surface(qapp)
    decided: list[str] = []
    s.style_decided.connect(decided.append)
    s._loading = True
    s._style_combo.activated.emit(0)
    s._loading = False
    s.clear()                                   # no image loaded
    s._style_combo.activated.emit(0)
    assert decided == []


def test_set_state_never_emits_style_decided(qapp):
    """Programmatic seeding (host loads an item in) must not read as a
    human decision."""
    s = _surface(qapp)
    decided: list[str] = []
    s.style_decided.connect(decided.append)
    s.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="portrait", aspect_label="Original")
    assert decided == []


# --------------------------------------------------------------------------- #
# QSS grammar — the four band variants exist in BOTH themes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("theme", ["light.qss", "dark.qss"])
def test_badge_band_roles_exist_in_theme(theme):
    qss = (_THEMES / theme).read_text(encoding="utf-8")
    for band in ("low", "mid", "high", "human"):
        sel = f'QComboBox#ProcessStyleCombo[confidenceBand="{band}"]'
        assert sel in qss, f"{theme} misses the {band} band rule"
