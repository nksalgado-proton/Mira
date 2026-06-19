"""spec/89 Slice 8 — Days List "Export now" toolbar trigger.

Pins the contract for the all-days variant:

* Button visibility tracks the identity phase (hidden under Pick / Edit
  / Collect; visible only under Export).
* Click emits ``export_now_requested`` so MainWindow can fan out
  per-day plans + run the locked confirm modal.
"""
from __future__ import annotations

import pytest

from mira.ui.pages.days_lists_page import DaysListsPage


def test_export_now_button_hidden_under_pick_identity(qapp):
    page = DaysListsPage()
    page.set_phase_identity("pick")
    assert not page._export_now_btn.isVisibleTo(page)


def test_export_now_button_visible_under_export_identity(qapp):
    page = DaysListsPage()
    page.set_phase_identity("export")
    assert page._export_now_btn.isVisibleTo(page)
    assert "Export now" in page._export_now_btn.text()


def test_export_now_button_emits_signal_on_click(qapp):
    page = DaysListsPage()
    page.set_phase_identity("export")
    received: list[bool] = []
    page.export_now_requested.connect(lambda: received.append(True))
    page._export_now_btn.click()
    assert received == [True]


def test_export_now_button_hidden_after_phase_swap_back(qapp):
    page = DaysListsPage()
    page.set_phase_identity("export")
    assert page._export_now_btn.isVisibleTo(page)
    page.set_phase_identity("pick")
    assert not page._export_now_btn.isVisibleTo(page)


