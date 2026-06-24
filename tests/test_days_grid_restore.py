"""spec/131 — Days Grid restores to the last item on return.

``DaysGridPage.open_for_day(..., anchor_item_id=X)`` scrolls the
ThumbGrid so cell X is visible + selected after the chunked build
finishes. No anchor → top (default behaviour). An anchor for an item
not on the day → graceful no-op (the grid stays at top instead of
crashing or looping forever).
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.pages.days_grid_page import DaysGridPage

FIXED_NOW = "2026-06-15T12:00:00+00:00"
N_PHOTOS = 6


def _now() -> str:
    return FIXED_NOW


def _drain() -> None:
    """Drive the chunked-build deferred ticks + the build_finished
    singleShot through the event loop."""
    for _ in range(10):
        QCoreApplication.processEvents()


def _write_jpeg(path: Path, idx: int) -> None:
    img = QImage(320, 214, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv((idx * 47) % 360, 120, 200))
    p = QPainter(img)
    p.setPen(QColor(20, 20, 20))
    p.setFont(QFont("Arial", 48, QFont.Weight.Bold))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, f"P{idx}")
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 90)


def _doc() -> m.EventDocument:
    """Single-day event with N items, all Pick-undecided so they end
    up in the Pick grid."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-131", name="Restore fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i in range(1, N_PHOTOS + 1):
        doc.items.append(m.Item(
            id=f"p{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/p{i}.jpg",
            sha256=f"{i:064d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ))
    return doc


@pytest.fixture
def event_dir(tmp_path):
    for i in range(1, N_PHOTOS + 1):
        _write_jpeg(tmp_path / "Original Media" / f"p{i}.jpg", i)
    return tmp_path


@pytest.fixture
def app_gateway(event_dir, tmp_path, monkeypatch):
    store = EventStore.create(event_dir / "event.db", event_id="evt-131")
    store.save_document(_doc())
    counter = itertools.count(100)
    gw = Gateway(settings=SettingsRepo(tmp_path / "settings.json"))

    def _open_event(_event_id):
        return EventGateway(
            store, event_root=event_dir, now=_now,
            new_id=lambda: f"app-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    yield gw


# ── open_for_day(anchor_item_id=X) ─────────────────────────────────────


def test_open_for_day_with_anchor_calls_ensure_item_visible(
    qapp, app_gateway,
):
    """Anchor on a real item id → the page asks the ThumbGrid to
    scroll to + select that cell after the chunked builder finishes.

    Verified by spying on ``ThumbGrid.ensure_item_visible`` rather
    than via ``cell.hasFocus()`` — Qt's focus chain depends on the
    active window, which is finicky when other tests in the suite
    have left state behind."""
    page = DaysGridPage(gateway=app_gateway)
    try:
        seen: list[object] = []
        original = page._grid.ensure_item_visible

        def _spy(payload, **kw):
            seen.append(payload)
            return original(payload, **kw)
        page._grid.ensure_item_visible = _spy   # type: ignore[assignment]
        assert page.open_for_day(
            "evt-131", 1, title="Day 1", date_iso="2026-04-01",
            anchor_item_id="p4",
        )
        _drain()
        # The grid was asked to scroll to p4 (via the page's anchor
        # plumbing).
        assert "p4" in seen
        # And the page records the anchor as the fallback entry too.
        assert page.current_entry_anchor() == "p4"
    finally:
        page.close_event()
        page.deleteLater()


def test_open_for_day_without_anchor_leaves_focus_unchanged(
    qapp, app_gateway,
):
    """No anchor → the page renders at the top; no cell takes focus
    automatically."""
    page = DaysGridPage(gateway=app_gateway)
    try:
        page.show()
        page.resize(900, 600)
        assert page.open_for_day(
            "evt-131", 1, title="Day 1", date_iso="2026-04-01")
        _drain()
        # No queued anchor.
        assert page._grid._pending_anchor_payload is None
        # No cell auto-focused.
        focused = [c for c in page._grid.cells() if c.hasFocus()]
        assert focused == []
        # Entry anchor stays empty until the user clicks.
        assert page.current_entry_anchor() is None
    finally:
        page.close_event()
        page.deleteLater()


def test_open_for_day_with_unknown_anchor_is_graceful(qapp, app_gateway):
    """Anchor for an item not on this day → no scroll, no crash, no
    queued anchor. The grid stays at the top."""
    page = DaysGridPage(gateway=app_gateway)
    try:
        page.show()
        page.resize(900, 600)
        assert page.open_for_day(
            "evt-131", 1, title="Day 1", date_iso="2026-04-01",
            anchor_item_id="not_on_this_day",
        )
        _drain()
        # The pending anchor was either applied (False on miss → cleared)
        # or never queued — in both cases it's None now.
        assert page._grid._pending_anchor_payload is None
        # Entry anchor still records the requested id (host may use it
        # as a fallback when re-opening other days).
        assert page.current_entry_anchor() == "not_on_this_day"
    finally:
        page.close_event()
        page.deleteLater()


# ── Entry anchor: recorded on item activation ───────────────────────────


def test_clicking_a_cell_records_entry_anchor(qapp, app_gateway):
    """The fallback restore anchor — the last item the user dove into
    from THIS grid. ``current_entry_anchor`` returns it for the host."""
    page = DaysGridPage(gateway=app_gateway)
    try:
        page.show()
        page.resize(900, 600)
        assert page.open_for_day(
            "evt-131", 1, title="Day 1", date_iso="2026-04-01")
        _drain()
        emitted: list[str] = []
        page.item_activated.connect(emitted.append)
        # Find the cell for p3 and synthesise the click via the
        # cell-handler path (skips the two-zone hit-test).
        idx = next(
            i for i, gi in enumerate(page._items) if gi.item_id == "p3")
        cell = page._grid.cell_at(idx)
        page._on_thumb_clicked("p3", cell)
        assert emitted == ["p3"]
        assert page.current_entry_anchor() == "p3"
    finally:
        page.close_event()
        page.deleteLater()
