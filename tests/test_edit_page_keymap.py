"""spec/66 §1.1 — Edit is creative-only.

Slice 4 of the spec/66 implementation pass strips the marking-for-export
grammar from EditPage. The locked-map decision keys (P / X / Space / C)
are deliberately inert on this surface — the viewport still fires the
verbs, the page just doesn't connect to them, since Edit no longer drives
a Pick/Skip ledger. The truth key (F10) keeps opening the processed
preview lens, and F / F11 stay fullscreen — those aren't ledger writes,
they're view tools.

NOTE this module's name is deliberately NOT on the conftest slice-B
skip list (test_edit_page / test_edit_page_rebuild are) — these run.
"""
from __future__ import annotations

import itertools
import time

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtTest import QTest

from mira.gateway.event_gateway import EventGateway
from mira.picked.model import CullBucket, CullItem
from mira.picked.status import (
    BADGE_UNTOUCHED,
    BucketStatus,
)
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.edited.edit_page import EditPage

FIXED_NOW = "2026-06-12T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _write_jpeg(path, idx: int) -> None:
    img = QImage(320, 214, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv((idx * 47) % 360, 120, 200))
    p = QPainter(img)
    p.setPen(QColor(20, 20, 20))
    p.setFont(QFont("Arial", 48, QFont.Weight.Bold))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, f"E{idx}")
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 90)


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-e", name="Edit keymap fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i in (1, 2):
        doc.items.append(m.Item(
            id=f"e{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/e{i}.jpg",
            sha256=f"{i:064d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ))
    return doc


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-e")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=tmp_path,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def page(qapp, gw, tmp_path):
    items = []
    for i in (1, 2):
        p = tmp_path / "Original Media" / f"e{i}.jpg"
        _write_jpeg(p, i)
        items.append(CullItem(
            item_id=f"e{i}", path=p, kind="photo",
            capture_time_corrected=f"2026-04-01T08:0{i}:00"))
    bucket = CullBucket(
        bucket_key="1|individual|net", kind="individual",
        title="edit net", items=tuple(items),
        status=BucketStatus(
            total=2, kept=0, candidate=0, discarded=0, untouched=2,
            reviewed=False, browsed=False, badge=BADGE_UNTOUCHED))
    pg = EditPage()
    pg.load(gw, bucket)
    yield pg
    pg.shutdown()                    # the defined lifecycle end (6b)
    pg.deleteLater()


def _edit_state(gw, item_id):
    ps = gw.phase_state(item_id, "edit")
    return ps.state if ps else None


def _wait_developed(page, timeout_s: float = 8.0) -> None:
    """Spin until the current photo's working copy landed (6b: the
    settle-gated off-thread prep)."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if page._surface._preview_array is not None:
            return
        time.sleep(0.01)
    raise AssertionError("working copy never landed")


def test_p_and_x_are_inert_on_edit(page, gw):
    """spec/66 §1.1 — Edit is creative-only. P and X fire on the viewport
    but the page does NOT connect to them, so no phase_state row appears."""
    QTest.keyClick(page, Qt.Key.Key_P)
    assert _edit_state(gw, "e1") is None
    QTest.keyClick(page, Qt.Key.Key_X)
    assert _edit_state(gw, "e1") is None


def test_space_and_c_are_inert_on_edit(page, gw):
    """spec/66 §1.1 — no toggle either: Space / C don't write phase_state
    rows from this surface (Edit's ledger moved to the Export surface)."""
    QTest.keyClick(page, Qt.Key.Key_Space)
    assert _edit_state(gw, "e1") is None
    QTest.keyClick(page, Qt.Key.Key_C)
    assert _edit_state(gw, "e1") is None


def test_f10_opens_the_processed_lens_preview_stays_a_button(page, gw):
    """Nelson 2026-06-12 standardisation: F10 opens the STANDARD modal
    lens with the PROCESSED, CROPPED image at FULL resolution (what
    export produces) — clean, no zoom/peaking tools in Edit. The
    in-canvas Toggle-Crop preview keeps existing, button-driven."""
    _wait_developed(page)        # F10 is inert in the 6b prep gap
    assert not page._preview_toggle.isChecked()
    QTest.keyClick(page, Qt.Key.Key_F10)
    lens = page._lens
    assert lens is not None and lens.isVisible()
    assert lens.isModal()
    assert not lens._with_tools                     # clean lens in Edit
    assert not lens._bar.isVisibleTo(lens)
    assert not page._preview_toggle.isChecked()     # the button untouched
    QTest.keyClick(lens, Qt.Key.Key_F10)
    assert not lens.isVisible()

    # The nav-centre pair (the standard) drives the same actions.
    assert page._fullscreen_btn.text() == "Full Screen"
    page._fullres_btn.click()
    assert page._lens is not None and page._lens.isVisible()
    page._lens.close()

    # The in-canvas preview CONTINUES to exist — the button still toggles
    # the full-res, canvas-fit render.
    page._preview_toggle.click()
    assert page._preview_toggle.isChecked()
    page._preview_toggle.click()
    assert not page._preview_toggle.isChecked()

    # And P / X still write nothing — they're inert here.
    QTest.keyClick(page, Qt.Key.Key_P)
    assert _edit_state(gw, "e1") is None


def test_navigation_still_works(page, gw):
    """The arrow / wheel browse keys are not part of the decision ledger
    so they survive Slice 4 untouched. Cursor moves; no marks written."""
    QTest.keyClick(page, Qt.Key.Key_Right)          # → e2
    assert page._index == 1
    QTest.keyClick(page, Qt.Key.Key_Left)           # → e1
    assert page._index == 0
    # No phase-state rows landed during the walk.
    assert _edit_state(gw, "e1") is None
    assert _edit_state(gw, "e2") is None


def test_f_and_f11_both_fullscreen(page):
    flips = []
    page.fullscreen_changed.connect(flips.append)
    QTest.keyClick(page, Qt.Key.Key_F)
    assert page._fullscreen and flips == [True]
    QTest.keyClick(page, Qt.Key.Key_Escape)         # Esc level 1
    assert not page._fullscreen and flips == [True, False]
    QTest.keyClick(page, Qt.Key.Key_F11)
    assert page._fullscreen and flips == [True, False, True]
