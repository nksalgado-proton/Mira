"""Quick Sweep (paths-mode) per-cell verbs land in the QS ledger.

In Quick Sweep the DaysGridPage runs without a gateway (`_eg is None`)
and decisions live in an in-memory ledger, not the DB. A border / P / X
verb used to be a silent no-op there: ``_apply_verb_at_index`` returned
early on ``_eg is None`` and nothing was recorded (Nelson 2026-06-28 —
"clicking the border to change status does not work"). These tests pin
that a registered ``state_write`` paths-mode callback now receives the
decision and the cell state updates in place.
"""
from __future__ import annotations

from pathlib import Path

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                          # pragma: no cover
    QApplication = None

from mira.ui.pages.days_grid_page import DaysGridPage, GridItem


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    yield QApplication.instance() or QApplication([])


def _page_with_item(state=None):
    page = DaysGridPage()
    writes: list = []
    page.set_paths_mode_callbacks(
        state_write=lambda p, s: writes.append((p, s)))
    path = Path("Card/DCIM/photo1.jpg")
    page.setDay(1, "Day 1", "", [GridItem(
        item_id="a", item_kind="photo", state=state, _path=path)])
    return page, writes, path


def test_paths_verb_cycle_writes_picked(qapp):
    """A border click (cycle) on a default (None) cell → Pick, written
    to the ledger via the paths-mode callback, cell state updated."""
    page, writes, path = _page_with_item(state=None)
    try:
        assert page._apply_verb_at_index(0, "cycle") is True
        assert writes == [(path, "picked")]
        assert page._items[0].state == "picked"
    finally:
        page.deleteLater()


def test_paths_verb_skip_then_pick(qapp):
    """Explicit X then P land as skipped then picked in the ledger."""
    page, writes, path = _page_with_item(state="picked")
    try:
        page._apply_verb_at_index(0, "skip")
        page._apply_verb_at_index(0, "pick")
        assert writes == [(path, "skipped"), (path, "picked")]
        assert page._items[0].state == "picked"
    finally:
        page.deleteLater()


def test_paths_bulk_skip_all_persists_to_ledger(qapp):
    """spec — Skip all / Pick all in Quick Sweep must PERSIST to the QS
    ledger, not just repaint. Otherwise opening the viewer or stepping
    back rebuilds from the unchanged ledger and reverts to the green
    default (Nelson 2026-06-28). Each flat item's path is written."""
    page = DaysGridPage()
    try:
        writes: list = []
        page.set_paths_mode_callbacks(
            state_write=lambda p, s: writes.append((p, s)))
        p1, p2 = Path("Card/a.jpg"), Path("Card/b.jpg")
        page.setDay(1, "Day 1", "", [
            GridItem(item_id="a", item_kind="photo", state=None, _path=p1),
            GridItem(item_id="b", item_kind="photo", state="picked", _path=p2),
        ])
        page._on_skip_all_clicked()
        assert set(writes) == {(p1, "skipped"), (p2, "skipped")}
        assert all(it.state == "skipped" for it in page._items)
    finally:
        page.deleteLater()


def test_paths_verb_no_callback_is_safe_noop(qapp):
    """With no write callback registered (a read-only paths page), the
    verb is still 'handled' and never raises — it just doesn't persist."""
    page = DaysGridPage()
    try:
        page.setDay(1, "Day 1", "", [GridItem(
            item_id="a", item_kind="photo", state=None,
            _path=Path("x.jpg"))])
        assert page._apply_verb_at_index(0, "cycle") is True
        # In-memory cell state still advances so the UI reflects the click.
        assert page._items[0].state == "picked"
    finally:
        page.deleteLater()
