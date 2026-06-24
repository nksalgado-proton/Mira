"""spec/131 — ``ThumbGrid.ensure_item_visible`` + ``select_item``
pin the scroll-to-anchor contract that the Days Grid restore flow
relies on.

* Locate a cell by its payload (``ThumbGridItem.payload`` == item id
  in DaysGridPage), scroll the viewport so it's visible, optionally
  give it focus.
* Apply the scroll **after** the chunked builder has placed the
  target cell — when the host calls ``ensure_item_visible`` before
  the cell is built, the request stashes + fires on the
  ``build_finished`` signal.
* Graceful no-op when the payload isn't on the grid (returns False,
  drops any prior pending anchor so a missed restore doesn't quietly
  resurrect on the next build).
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QCoreApplication, Qt

from mira.ui.design import ThumbGrid, ThumbGridItem


def _item(**over) -> ThumbGridItem:
    kw = dict(state=None, payload=None, focusable=True)
    kw.update(over)
    return ThumbGridItem(**kw)


def _drain() -> None:
    """Process the deferred singleShot(0) so ``build_finished`` fires."""
    for _ in range(5):
        QCoreApplication.processEvents()


# ── index_of_payload ───────────────────────────────────────────────────


def test_index_of_payload_finds_first_match(qapp):
    g = ThumbGrid()
    g.set_items([_item(payload="a"), _item(payload="b"), _item(payload="c")])
    assert g.index_of_payload("a") == 0
    assert g.index_of_payload("b") == 1
    assert g.index_of_payload("c") == 2


def test_index_of_payload_returns_none_when_missing(qapp):
    g = ThumbGrid()
    g.set_items([_item(payload="a")])
    assert g.index_of_payload("nope") is None


# ── ensure_item_visible — synchronous path (cell already built) ────────


def test_ensure_item_visible_locates_and_focuses_built_cell(qapp):
    """Small set (≤ 50 items) builds entirely synchronously inside
    ``set_items`` — the cell exists immediately and gets a setFocus
    call on the first invocation.

    Verified via a setFocus spy rather than ``cell.hasFocus()`` —
    Qt's focus chain depends on the active window, which other tests
    in the suite can leave in inconsistent states."""
    g = ThumbGrid()
    g.set_items([_item(payload="a"), _item(payload="b"), _item(payload="c")])
    cell = g.cell_at(1)
    focused: list = []
    cell.setFocus = lambda *_, **__: focused.append(cell)  # type: ignore[assignment]
    ensured: list = []
    g._scroll.ensureWidgetVisible = (   # type: ignore[assignment]
        lambda w, *_, **__: ensured.append(w))
    assert g.ensure_item_visible("b", select=True) is True
    assert ensured == [cell]
    assert focused == [cell]


def test_ensure_item_visible_select_false_skips_focus(qapp):
    g = ThumbGrid()
    g.set_items([_item(payload="a"), _item(payload="b")])
    cell = g.cell_at(1)
    focused: list = []
    cell.setFocus = lambda *_, **__: focused.append(cell)  # type: ignore[assignment]
    assert g.ensure_item_visible("b", select=False) is True
    # select=False skips the setFocus call.
    assert focused == []


def test_select_item_alias_focuses_cell(qapp):
    g = ThumbGrid()
    g.set_items([_item(payload="a"), _item(payload="b")])
    cell = g.cell_at(0)
    focused: list = []
    cell.setFocus = lambda *_, **__: focused.append(cell)  # type: ignore[assignment]
    assert g.select_item("a") is True
    assert focused == [cell]


def test_ensure_item_visible_missing_payload_returns_false(qapp):
    g = ThumbGrid()
    g.set_items([_item(payload="a")])
    assert g.ensure_item_visible("nope") is False


# ── Deferred path — anchor queued, applied on build_finished ──────────


def test_ensure_item_visible_defers_until_build_finished(qapp):
    """The chunked builder splits at ``_CHUNK_FIRST = 50``; an anchor
    for cell #55 is queued and applied when ``build_finished`` fires.

    Spy on ``_scroll_to_cell`` instead of asserting ``hasFocus()`` —
    the focus assertion is suite-order-flaky."""
    g = ThumbGrid()
    items = [_item(payload=f"k{i}") for i in range(60)]
    g.set_items(items)
    # Cell 55 isn't built yet on the first synchronous batch.
    assert g.cell_at(55) is None
    calls: list = []
    g._scroll_to_cell = (              # type: ignore[assignment]
        lambda cell, *, select: calls.append((cell, select)))
    ok = g.ensure_item_visible("k55", select=True)
    assert ok is True
    # The pending anchor stashed (cell not built yet).
    assert g._pending_anchor_payload == "k55"
    _drain()
    # Build finished → the deferred-apply hook ran ensure_item_visible
    # again; this time the cell is built and _scroll_to_cell fires.
    assert g.cell_at(55) is not None
    assert len(calls) == 1
    target_cell, select_arg = calls[0]
    assert target_cell is g.cell_at(55)
    assert select_arg is True
    # Pending cleared after application.
    assert g._pending_anchor_payload is None


def test_build_finished_fires_on_fully_synchronous_set_items(qapp):
    """Spec/131 — even when ``set_items`` builds every cell
    synchronously (small set), ``build_finished`` is emitted on the
    next event-loop turn so the deferred-anchor path is unconditional."""
    g = ThumbGrid()
    fired: list[bool] = []
    g.build_finished.connect(lambda: fired.append(True))
    g.set_items([_item(payload="a"), _item(payload="b")])
    assert fired == []          # deferred via singleShot(0)
    _drain()
    assert fired == [True]


def test_fresh_set_items_clears_pending_anchor(qapp):
    """A new ``set_items`` (e.g. switching days) drops any anchor
    queued from the prior contents — no stale restore on the new set."""
    g = ThumbGrid()
    items = [_item(payload=f"a{i}") for i in range(60)]
    g.set_items(items)
    g.ensure_item_visible("a55")
    assert g._pending_anchor_payload == "a55"
    # Replace contents — pending drops.
    g.set_items([_item(payload="b0"), _item(payload="b1")])
    assert g._pending_anchor_payload is None


def test_missing_payload_drops_prior_pending_anchor(qapp):
    """A graceful miss (anchor for an item not on this day) clears any
    earlier pending anchor — so the next build doesn't quietly fire
    the stale one."""
    g = ThumbGrid()
    items = [_item(payload=f"a{i}") for i in range(60)]
    g.set_items(items)
    g.ensure_item_visible("a55")
    assert g._pending_anchor_payload == "a55"
    # Now ask for an item that doesn't exist on this page.
    assert g.ensure_item_visible("nope") is False
    assert g._pending_anchor_payload is None
