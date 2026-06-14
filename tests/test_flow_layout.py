"""FlowLayout — wraps children so a dense toolbar's min width is the widest single child,
not the sum (the width-reflow fix; Nelson 2026-06-01)."""
from __future__ import annotations

from PyQt6.QtWidgets import QPushButton, QWidget

from mira.ui.base.flow_layout import FlowLayout


def _btn(w: int) -> QPushButton:
    b = QPushButton()
    b.setFixedSize(w, 20)
    return b


def test_minimum_width_is_widest_child_not_sum(qapp):
    host = QWidget()
    fl = FlowLayout(host, spacing=6)
    for w in (80, 120, 90, 200, 60):
        fl.addWidget(_btn(w))
    # Sum would be 550+spacing; the wrapping floor is just the widest child (200).
    assert fl.minimumSize().width() == 200
    host.deleteLater()


def test_wraps_to_more_rows_when_narrow(qapp):
    host = QWidget()
    fl = FlowLayout(host, spacing=6)
    for _ in range(5):
        fl.addWidget(_btn(100))  # 5 × 100-wide, 20 tall
    # Wide enough for one row → about one button tall.
    one_row = fl.heightForWidth(1000)
    # Narrow → must wrap to multiple rows → taller.
    narrow = fl.heightForWidth(220)
    assert narrow > one_row
    host.deleteLater()
