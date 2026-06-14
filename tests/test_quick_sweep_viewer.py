"""Quick Sweep's single-item viewer — the locked grammar must actually
REACH the viewport (Nelson 2026-06-12: "I tried the quick sweep. F10
does not work").

Root cause pinned here: ``_install_keyboard_focus`` NoFocus'd every
child INCLUDING the viewport, so the page's ``_viewport.setFocus()``
calls were silent no-ops and the whole spec/63 §4 grammar was dead in
the viewer. The loop now exempts the viewport; the labelled "Full
Resolution View" button (the Picker treatment) replaces the corner 🔍.

NOTE the module name dodges the conftest slice-B skip list
(test_quick_sweep_page / test_quick_sweep_buckets are on it).
"""
from __future__ import annotations

from datetime import datetime

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest

from core.fresh_source import SourceItem
from mira.ui.pages.quick_sweep_page import QuickSweepPage


@pytest.fixture(autouse=True)
def _never_write_real_settings(monkeypatch):
    import core.settings as cs
    monkeypatch.setattr(cs, "update_setting", lambda k, v: None)


def _jpeg(path, hue):
    img = QImage(320, 200, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv(hue, 120, 200))
    assert img.save(str(path), "JPG", 90)


@pytest.fixture
def page(qapp, tmp_path):
    paths = []
    for i in (1, 2):
        p = tmp_path / f"P100000{i}.jpg"
        _jpeg(p, i * 60)
        paths.append(p)
    clip = tmp_path / "C1000001.mp4"
    clip.write_bytes(b"not really a video")
    paths.append(clip)
    items = [
        SourceItem(path=p, timestamp=datetime(2026, 4, 1, 8, i),
                   camera_id="G9")
        for i, p in enumerate(paths, start=1)
    ]
    pg = QuickSweepPage(browse_mode=True)
    assert pg.load(items) is True
    pg.show()
    qapp.processEvents()
    yield pg
    pg.deleteLater()


def test_viewer_focus_reaches_the_viewport(qapp, page):
    """The regression: the NoFocus loop must EXEMPT the viewport, and
    the viewer's setFocus must actually land there — else every key of
    the locked grammar is dead on this surface.

    Pinned via ``focusWidget()`` — the WITHIN-window focus assignment,
    which both halves of the original bug broke (the NoFocus loop made
    setFocus a silent no-op; showEvent stole it back). ``hasFocus()``
    additionally requires the OS to keep this window ACTIVE, which a
    full-suite run on the NATIVE platform (verify.bat has no offscreen
    override) cannot guarantee against thousands of sibling test
    windows — it flaked there at baseline too, 2026-06-12."""
    assert page._viewport.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert page.focusWidget() is page._viewport
    if page.isActiveWindow():
        # The strong form, wherever the environment can host it.
        assert page._viewport.hasFocus()


def test_viewport_grammar_is_alive(qapp, page):
    assert page._index == 0
    QTest.keyClick(page._viewport, Qt.Key.Key_Right)
    assert page._index == 1
    QTest.keyClick(page._viewport, Qt.Key.Key_Left)
    assert page._index == 0


def test_f10_opens_the_modal_lens_from_quick_sweep(qapp, page):
    QTest.keyClick(page._viewport, Qt.Key.Key_F10)
    lens = page._viewport._truth_window
    assert lens is not None and lens.isVisible()
    assert lens.isModal()
    QTest.keyClick(lens, Qt.Key.Key_F10)
    assert not lens.isVisible()


def test_full_resolution_button_replaces_the_corner(qapp, page):
    """The Picker treatment, applied here on Nelson's report: a
    labelled nav-centre button, photo-only; the corner 🔍 hidden."""
    assert not page._viewport._inspect_btn.isVisible()
    assert page._fullres_btn.isVisible()
    page._viewport.show_index(2)                 # the clip
    assert not page._fullres_btn.isVisible()     # nothing full-res there
    page._viewport.show_index(0)
    assert page._fullres_btn.isVisible()
    page._fullres_btn.click()                    # the button = F10
    lens = page._viewport._truth_window
    assert lens is not None and lens.isVisible()
    lens.close()
