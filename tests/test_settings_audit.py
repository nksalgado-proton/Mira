"""Regression tests for the 2026-06-09 settings audit (Nelson):
- tab structure matches the App-menu vocab
- previously-orphaned settings (font_scale, prefer_helicon_for_focus,
  preferred_burst_genre) are exposed in the dialog
- newly-promoted settings have the right defaults
- consumers honour the promoted settings (where wiring lands here)
"""
from __future__ import annotations

import pytest

from mira.settings.model import Settings


# ─── tab structure ───────────────────────────────────────────────────────────


def test_tab_structure_matches_app_menu_vocab():
    """Tabs are the App-menu phase names + cross-cutting categories
    (Nelson 2026-06-09 design; + Calibration 2026-06-10, the spec/54
    §4.1 tone-trims drawer). Old vocab (Picker / Select / Process /
    Curate / Import) is gone."""
    from mira.ui.base.settings_dialog import SETTINGS_SCHEMA
    titles = [t["tab"] for t in SETTINGS_SCHEMA]
    assert titles == [
        "General", "Appearance", "Paths", "Collect",
        "Pick", "Edit", "Calibration", "Share", "Video", "Advanced",
    ]
    # Legacy vocab must NOT survive.
    for old in ("Picker", "Select", "Process", "Curate", "Import"):
        assert old not in titles, f"legacy tab still present: {old}"


def test_no_duplicate_tabs():
    from mira.ui.base.settings_dialog import SETTINGS_SCHEMA
    titles = [t["tab"] for t in SETTINGS_SCHEMA]
    assert len(titles) == len(set(titles)), (
        f"duplicate tab(s) in schema: {titles}"
    )


def test_dead_cull_default_state_key_removed():
    """The Picker tab used to reference ``cull_default_state``, a key
    that doesn't exist in the rebuild Settings model. After the
    redesign only ``pick_default_state`` survives. (``info`` rows are
    keyless by design — spec/63 slice 7 — hence ``.get``.)"""
    from mira.ui.base.settings_dialog import SETTINGS_SCHEMA
    keys = {
        f.get("key")
        for tab in SETTINGS_SCHEMA
        for f in tab["fields"]
    }
    assert "cull_default_state" not in keys
    assert "pick_default_state" in keys


# ─── orphan settings now exposed ─────────────────────────────────────────────


def test_font_scale_exposed_on_appearance():
    from mira.ui.base.settings_dialog import SETTINGS_SCHEMA
    appearance = next(t for t in SETTINGS_SCHEMA if t["tab"] == "Appearance")
    keys = {f["key"] for f in appearance["fields"]}
    assert "font_scale" in keys


def test_prefer_helicon_for_focus_exposed_on_paths():
    from mira.ui.base.settings_dialog import SETTINGS_SCHEMA
    paths = next(t for t in SETTINGS_SCHEMA if t["tab"] == "Paths")
    keys = {f["key"] for f in paths["fields"]}
    assert "prefer_helicon_for_focus" in keys


def test_preferred_burst_genre_exposed_on_pick():
    from mira.ui.base.settings_dialog import SETTINGS_SCHEMA
    pick = next(t for t in SETTINGS_SCHEMA if t["tab"] == "Pick")
    keys = {f["key"] for f in pick["fields"]}
    assert "preferred_burst_genre" in keys


# ─── newly-promoted settings have the right defaults ────────────────────────


def test_promoted_settings_have_expected_defaults():
    """Defaults must match the hardcoded values they replaced so existing
    installs don't shift behavior on upgrade."""
    s = Settings()
    assert s.font_scale == 1.0
    assert s.repeat_window_seconds == 2.0           # core/repeat_detector
    assert s.peek_target_photos == 20               # core/peek_select.DEFAULT_TARGET
    assert s.jpeg_export_quality == 95              # core/process_render.JPEG_OUTPUT_QUALITY
    assert s.video_clip_crf == 20                   # core/video_export_run._CLIP_CRF
    assert s.focus_peaking_opacity == 0.7           # core/focus_peaking.PEAKING_OPACITY_DEFAULT
    assert s.default_day_grid_cell_size == 140      # legacy DayGridView default (preserved)
    assert s.log_rotate_keep_days == 14             # core/logging_setup.LOG_ROTATE_KEEP_DAYS


# ─── consumer wiring honours the settings ───────────────────────────────────


def test_font_scale_applies_to_app_font(qapp, monkeypatch):
    """``apply_font_scale`` scales the QApplication font and is
    idempotent across re-application against the cached baseline."""
    from mira.ui.app import apply_font_scale
    apply_font_scale(qapp, 1.0)            # establish baseline
    baseline = qapp.property("_font_baseline_pt")
    assert baseline is not None
    apply_font_scale(qapp, 1.25)
    assert qapp.font().pointSizeF() == pytest.approx(float(baseline) * 1.25)
    apply_font_scale(qapp, 1.0)
    assert qapp.font().pointSizeF() == pytest.approx(float(baseline))


# DayGridView retired with the Brief-B ThumbGrid migration; its
# ``default_day_grid_cell_size`` Setting reader tests are gone with
# the widget. The Setting itself is still exposed through the
# Settings dialog (preserved for users who configured a value); the
# day-grid surfaces now use the redesigned :class:`Thumb`'s fixed
# tile size, so the slider/Setting no longer drives a live widget
# but the field stays a no-op for migration compatibility.
