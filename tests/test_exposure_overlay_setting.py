"""spec/96 §2 — ``show_photo_overlays`` gates the exposure pill
in the Picker and Quick Sweep single views.

Pin the contract that:
* default True (preserves today's behaviour),
* False → the overlay is never populated with content (set_html(""))
  in Quick Sweep,
* the chip composition includes camera + exposure + type + size
  segments in order.

Picker is tested via the same setting helper since the dialog wiring
goes through `SettingsRepo().load().show_photo_overlays`.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PyQt6.QtGui import QColor, QImage

from core.fresh_source import SourceItem
from mira.ui.pages.quick_sweep_page import QuickSweepPage


@pytest.fixture(autouse=True)
def _never_write_real_settings(monkeypatch):
    import core.settings as cs
    monkeypatch.setattr(cs, "update_setting", lambda k, v: None)


def _jpeg(path: Path, hue: int = 120, w: int = 320, h: int = 200) -> None:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv(hue, 120, 200))
    assert img.save(str(path), "JPG", 90)


@pytest.fixture
def page(qapp, tmp_path):
    photo = tmp_path / "P100001.jpg"
    _jpeg(photo)
    items = [SourceItem(
        path=photo, timestamp=datetime(2026, 4, 1, 9, 0),
        camera_id="Pana+G9M2",
        shutter_speed=0.004, aperture=2.8, iso=400, focal_length=85.0,
    )]
    pg = QuickSweepPage(browse_mode=True)
    assert pg.load(items) is True
    pg.show()
    qapp.processEvents()
    yield pg
    pg.deleteLater()


# ── default ON → overlay populated ──────────────────────────────


def test_default_setting_populates_overlay_with_camera_exposure_type_size(
    qapp, page, monkeypatch,
):
    """spec/96 §2 default — when ``show_photo_overlays`` is True
    (the default), the Quick Sweep single view fills the chip with
    camera + exposure + type + size."""
    # Force the default explicitly so the test isn't sensitive to the
    # ambient settings file.
    monkeypatch.setattr(QuickSweepPage, "_show_photo_overlays",
                        staticmethod(lambda: True))
    page._on_viewport_current_changed(0)             # noqa: SLF001
    text = page._expo_overlay.text()                 # noqa: SLF001
    assert "Pana+G9M2" in text
    assert "f/2.8" in text and "ISO 400" in text and "85mm" in text
    assert "JPEG" in text
    # The size segment is non-empty (real file on disk).
    # KB/MB tail — the test JPEG is small, so KB.
    assert " KB" in text or " MB" in text


# ── False → overlay cleared ─────────────────────────────────────


def test_setting_off_hides_overlay(qapp, page, monkeypatch):
    """spec/96 §2 — when ``show_photo_overlays`` is False, the
    Quick Sweep never populates the pill (set_html(``""``)). The pill
    widget hides itself on empty content."""
    monkeypatch.setattr(QuickSweepPage, "_show_photo_overlays",
                        staticmethod(lambda: False))
    page._on_viewport_current_changed(0)             # noqa: SLF001
    assert page._expo_overlay.text() == ""           # noqa: SLF001
    assert page._expo_overlay.isVisible() is False   # noqa: SLF001


# ── source-chip composition order ──────────────────────────────


def test_source_chip_keeps_segment_order_in_live_pill(
    qapp, page, monkeypatch,
):
    """The live pill renders camera FIRST and the type/size LAST,
    with the exposure quartet in the middle (spec/96 §2 target
    shape)."""
    monkeypatch.setattr(QuickSweepPage, "_show_photo_overlays",
                        staticmethod(lambda: True))
    page._on_viewport_current_changed(0)             # noqa: SLF001
    text = page._expo_overlay.text()                 # noqa: SLF001
    cam_idx = text.index("Pana+G9M2")
    exposure_idx = text.index("85mm")
    type_idx = text.index("JPEG")
    assert cam_idx < exposure_idx < type_idx


# ── helper: settings load failure → default True ───────────────


def test_show_photo_overlays_defaults_true_on_load_failure(monkeypatch):
    """The helper guards against an early-boot ``SettingsRepo``
    failure (e.g. mid-first-run) by defaulting to True so the chip
    still appears for a brand-new install."""
    from mira.settings import repo as repo_mod

    class _BoomRepo:
        def load(self_inner):                        # noqa: ANN101
            raise RuntimeError("simulated load failure")

    monkeypatch.setattr(repo_mod, "SettingsRepo", _BoomRepo)
    assert QuickSweepPage._show_photo_overlays() is True
