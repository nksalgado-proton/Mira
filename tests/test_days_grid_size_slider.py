"""Cell-size slider on the DaysGridPage toolbar (Nelson 2026-06-18).

Pure widget shape — the slider's valueChanged calls into the grid's
``set_cell_size``; this test asserts the resize landed and the height
tracked the locked aspect ratio so the border hit zone keeps its
proportions.
"""
from __future__ import annotations

import pytest

try:
    from PyQt6.QtCore import QSize
    from PyQt6.QtWidgets import QApplication
except ImportError:                                          # pragma: no cover
    QApplication = None
    QSize = None

from mira.ui.pages.days_grid_page import (
    DaysGridPage,
    _TILE_ASPECT,
    _TILE_SIZE,
    _TILE_SIZE_MAX,
    _TILE_SIZE_MIN,
)


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def test_size_slider_default_matches_tile_size(qapp):
    page = DaysGridPage()
    try:
        assert page._size_slider.value() == _TILE_SIZE.width()
        assert page._grid.cell_size() == _TILE_SIZE
    finally:
        page.deleteLater()


def test_size_slider_resizes_the_grid_and_preserves_aspect(qapp):
    page = DaysGridPage()
    try:
        page._size_slider.setValue(240)
        cs = page._grid.cell_size()
        assert cs.width() == 240
        assert cs.height() == int(round(240 * _TILE_ASPECT))
    finally:
        page.deleteLater()


def test_size_slider_clamps_below_minimum(qapp):
    page = DaysGridPage()
    try:
        # QSlider already clamps to its setMinimum/setMaximum, but the
        # handler's own ``max(min)`` belt-and-suspenders catches any
        # later programmatic call that bypasses the slider widget.
        page._on_size_slider_changed(50)
        assert page._grid.cell_size().width() == _TILE_SIZE_MIN.width()
    finally:
        page.deleteLater()


def test_size_slider_clamps_above_maximum(qapp):
    page = DaysGridPage()
    try:
        page._on_size_slider_changed(5000)
        assert page._grid.cell_size().width() == _TILE_SIZE_MAX.width()
    finally:
        page.deleteLater()
