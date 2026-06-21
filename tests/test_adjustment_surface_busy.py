"""Honest-UI-during-render pins (Nelson 2026-06-10): switching the
Look / Filter / Style must never leave stale control states through the
render lag, and the wait override cursor must always come back down.

The repaint flushes themselves can't be observed headlessly; what these
tests pin is the state machine around them — exclusive look buttons
after a switch, the cursor stack balanced after every render path, the
combo reflecting the new filter immediately after the handler runs.
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtWidgets import QApplication

from core.photo_auto import available_filters, available_looks
from mira.ui.edited.adjustment_surface import AdjustmentSurface


def _surface(qapp) -> AdjustmentSurface:
    s = AdjustmentSurface()
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    img[10, 20] = (200, 120, 40)
    s.load_image(img)
    return s


def test_tone_choices_live_in_named_boxes(qapp):
    """Nelson 2026-06-11 (updated 2026-06-21) — the top grid: each tone
    choice lives in its own named box (Look · Style · Filter); Crop has
    its own box on line 2; titles are MIXED CASE, always (the UPPERCASE
    experiment is reverted); no label-beside-input anywhere. The
    redundant outer "Style, Look & Filter" wrapper that previously
    encircled Look + Style + Filter was dropped by the 2026-06-21
    surface standardisation pass — the three inner boxes are siblings
    now, no double-frame."""
    from PyQt6.QtWidgets import QFrame, QLabel

    s = _surface(qapp)
    titles = {
        lbl.text()
        for f in s.findChildren(QFrame)
        if f.objectName() == "ProcessGroupBox"
        for lbl in f.findChildren(QLabel)
        if lbl.objectName() == "ProcessGroupTitle"
    }
    assert {"Look", "Style", "Filter", "Crop"} <= titles
    # Mixed case, always — no ALL-CAPS box titles survive.
    assert not any(t.isupper() and len(t) > 1 for t in titles)
    # The old label-beside-input pattern is gone.
    inline = [
        lbl.text() for lbl in s.findChildren(QLabel)
        if lbl.text() in ("Style:", "Filter:")
    ]
    assert inline == []


def test_aspect_combo_shows_no_crop_keeps_original_label(qapp):
    """Nelson 2026-06-11 — display/data split: the no-crop entry SHOWS
    "No Crop"; the persisted label stays "Original" everywhere."""
    s = _surface(qapp)
    combo = s._aspect_combo
    idx = combo.findData("Original")
    assert idx >= 0
    assert combo.itemText(idx) == "No Crop"
    combo.set_selected_label("Original")
    assert combo.selected_label == "Original"


def test_set_look_keeps_exactly_one_button_checked(qapp):
    s = _surface(qapp)
    looks = list(available_looks())
    s.set_look(looks[1])
    checked = [k for k, b in s._look_buttons.items() if b.isChecked()]
    assert checked == [looks[1]]
    s.set_look(looks[2])
    checked = [k for k, b in s._look_buttons.items() if b.isChecked()]
    assert checked == [looks[2]]


def test_cursor_stack_balanced_after_look_render(qapp):
    s = _surface(qapp)
    s.set_look(list(available_looks())[1])
    assert QApplication.overrideCursor() is None


def test_cursor_stack_balanced_after_filter_change(qapp):
    s = _surface(qapp)
    s._filter_combo.setCurrentIndex(1)        # fires _on_filter_changed
    assert QApplication.overrideCursor() is None
    assert s._creative_filter == list(available_filters())[0]


def test_cursor_stack_balanced_after_style_change(qapp):
    s = _surface(qapp)
    s._style_combo.setCurrentIndex(
        (s._style_combo.currentIndex() + 1) % s._style_combo.count())
    assert QApplication.overrideCursor() is None


def test_cursor_stack_balanced_when_render_raises(qapp, monkeypatch):
    """The finally must drop the cursor even on a render explosion."""
    s = _surface(qapp)
    monkeypatch.setattr(
        "mira.ui.edited.adjustment_surface._array_to_pixmap",
        lambda arr: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        s.render_now()
    except RuntimeError:
        pass
    assert QApplication.overrideCursor() is None
