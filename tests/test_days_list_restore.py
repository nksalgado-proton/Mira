"""spec/131 — Days List restores to the last day on return.

``DaysListsPage.setEventForPreview(..., anchor_day_number=N)`` scrolls
the inner list so DayRow N is visible and gives it focus (highlight).
No anchor → top. An anchor for a day not in the list → graceful no-op.
The page also records ``day_activated`` as a fallback entry anchor the
host can read via ``current_entry_anchor``.
"""
from __future__ import annotations

import pytest

from mira.ui.pages.days_lists_page import (
    DayRow,
    DaysListsPage,
    DaySnapshot,
)


def _snaps(*day_numbers: int) -> list[DaySnapshot]:
    """Build a quick list of plausible DaySnapshot rows."""
    out: list[DaySnapshot] = []
    for n in day_numbers:
        out.append(DaySnapshot(
            day_number=n,
            title=f"Day {n}",
            date_iso=f"2026-04-{n:02d}",
            picked=2, skipped=1, items=5, buckets=2,
        ))
    return out


def _day_row_for(page: DaysListsPage, day_number: int):
    """Walk the rows layout and return the DayRow widget for
    ``day_number`` (or None)."""
    for i in range(page._rows.count()):
        item = page._rows.itemAt(i)
        w = item.widget() if item is not None else None
        if isinstance(w, DayRow) and w._snapshot.day_number == day_number:
            return w
    return None


# ── anchor_day_number kwarg ────────────────────────────────────────────


def test_anchor_day_number_scrolls_and_selects_matching_row(qapp):
    """The setEventForPreview anchor calls ensure_day_visible on the
    matching DayRow; the scroll area's ensureWidgetVisible runs with
    that row + the row receives a setFocus call.

    Qt's focus chain depends on the active window, which is finicky
    in headless tests — verify the intent by spying on the row's
    setFocus + the scroll area's ensureWidgetVisible. (The "highlight"
    is whatever the focus-ring QSS paints on the focused row.)"""
    page = DaysListsPage()
    try:
        page.setEventForPreview("Test event", _snaps(1, 2, 3, 4, 5))
        ensured: list = []
        focused: list = []
        page._scroll.ensureWidgetVisible = (   # type: ignore[assignment]
            lambda w, *_, **__: ensured.append(w))
        target = _day_row_for(page, 3)
        target.setFocus = (                    # type: ignore[assignment]
            lambda *_, **__: focused.append(target))
        assert page.ensure_day_visible(3) is True
        assert ensured == [target]
        assert focused == [target]
    finally:
        page.deleteLater()


def test_no_anchor_means_no_scroll(qapp):
    page = DaysListsPage()
    try:
        ensured: list = []
        page.setEventForPreview("Test event", _snaps(1, 2, 3))
        page._scroll.ensureWidgetVisible = (   # type: ignore[assignment]
            lambda w, *_, **__: ensured.append(w))
        # No anchor → no scroll triggered on entry.
        page.setEventForPreview("Test event", _snaps(1, 2, 3))
        assert ensured == []
    finally:
        page.deleteLater()


def test_anchor_for_missing_day_is_graceful(qapp):
    page = DaysListsPage()
    try:
        ensured: list = []
        page.setEventForPreview("Test event", _snaps(1, 2, 3))
        page._scroll.ensureWidgetVisible = (   # type: ignore[assignment]
            lambda w, *_, **__: ensured.append(w))
        # Day 99 doesn't exist → ensure_day_visible returns False,
        # ensureWidgetVisible is never called, no crash.
        assert page.ensure_day_visible(99) is False
        assert ensured == []
    finally:
        page.deleteLater()


# ── ensure_day_visible standalone ──────────────────────────────────────


def test_ensure_day_visible_returns_false_for_missing(qapp):
    page = DaysListsPage()
    try:
        page.setEventForPreview("Test event", _snaps(1, 2))
        assert page.ensure_day_visible(99) is False
    finally:
        page.deleteLater()


def test_ensure_day_visible_can_skip_focus(qapp):
    page = DaysListsPage()
    try:
        page.setEventForPreview("Test event", _snaps(1, 2, 3))
        target = _day_row_for(page, 2)
        focused: list = []
        target.setFocus = (                    # type: ignore[assignment]
            lambda *_, **__: focused.append(target))
        assert page.ensure_day_visible(2, select=False) is True
        # select=False skips the setFocus call.
        assert focused == []
    finally:
        page.deleteLater()


# ── Entry anchor on day_activated ──────────────────────────────────────


def test_day_activation_records_entry_anchor(qapp):
    """``current_entry_anchor`` returns the last day the user clicked,
    so the host has a fallback restore target if the grid reports no
    current day."""
    page = DaysListsPage()
    try:
        page.setEventForPreview("Test event", _snaps(1, 2, 3))
        emitted: list[int] = []
        page.day_activated.connect(emitted.append)
        # Trigger via the underlying DayRow.activated signal (the
        # mousePressEvent on the card emits this).
        row = _day_row_for(page, 2)
        row.activated.emit(2)
        assert emitted == [2]
        assert page.current_entry_anchor() == 2
    finally:
        page.deleteLater()


def test_current_entry_anchor_is_none_until_activation(qapp):
    page = DaysListsPage()
    try:
        page.setEventForPreview("Test event", _snaps(1, 2, 3))
        assert page.current_entry_anchor() is None
    finally:
        page.deleteLater()
