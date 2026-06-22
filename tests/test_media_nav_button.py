"""spec/63 MediaNav (Nelson 2026-06-22) — prev/next chrome is an inline
ghost-styled ``‹ Prev`` / ``Next ›`` button via ``nav_button``. The old
``nav_arrow`` factory rendered as a raw native OS button (its
``#MediaNavArrow`` QSS role never propagated cleanly), making Quick
Sweep look alien next to the Picker / Editor's ghost buttons.

Tiny smoke — the directionality, the label, the underlying widget
class, and the design-system #Ghost objectName. Eyeball verification
of the three surfaces still owns the visual contract."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_nav_button_left_reads_prev(qapp):
    from mira.ui.design import nav_button
    btn = nav_button("left")
    assert isinstance(btn, QPushButton)
    assert btn.objectName() == "Ghost"
    assert "Prev" in btn.text()
    assert btn.text().startswith("‹")


def test_nav_button_right_reads_next(qapp):
    from mira.ui.design import nav_button
    btn = nav_button("right")
    assert isinstance(btn, QPushButton)
    assert btn.objectName() == "Ghost"
    assert "Next" in btn.text()
    assert btn.text().endswith("›")


def test_nav_button_rejects_unknown_direction(qapp):
    from mira.ui.design import nav_button
    with pytest.raises(ValueError):
        nav_button("bad")
