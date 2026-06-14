"""spec/63 §4 on the Edit photo page (slice 6a — the key-map alignment).

Edit's decision ledger is the binary marked-for-export status (spec/59
§8: green flows to Share, red stays). The locked map lands here: P
marks (SET), X unmarks (SET), Space toggles, C degrades to the toggle
(no Compare state on this ledger). The legacy P-Preview binding moved
to F10 (the truth key — in Edit, the developed full-resolution
Preview), and the famous DEAD second Key_P branch (P-export, shadowed
since birth — spec/63's named kill) is gone.

6b (spec/63 §6.1): loading is ASYNC now — F10 is deliberately inert
while the working copy preps (nothing honest to show), so the lens
test waits for development first. The decision keys need no waiting
(the ledger never depends on pixels).

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
    STATE_PICKED,
    STATE_SKIPPED,
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


def test_p_marks_and_x_unmarks_for_export(page, gw):
    QTest.keyClick(page, Qt.Key.Key_P)
    assert _edit_state(gw, "e1") == STATE_PICKED
    QTest.keyClick(page, Qt.Key.Key_P)              # SET, not toggle
    assert _edit_state(gw, "e1") == STATE_PICKED
    QTest.keyClick(page, Qt.Key.Key_X)
    assert _edit_state(gw, "e1") == STATE_SKIPPED
    QTest.keyClick(page, Qt.Key.Key_X)
    assert _edit_state(gw, "e1") == STATE_SKIPPED


def test_space_and_c_toggle_the_binary_ledger(page, gw):
    """Born-green default: an un-decided photo reads picked, so the
    first toggle goes to skipped; C degrades to Space (spec/63 §4 —
    binary ledger, no Compare)."""
    QTest.keyClick(page, Qt.Key.Key_Space)
    assert _edit_state(gw, "e1") == STATE_SKIPPED
    QTest.keyClick(page, Qt.Key.Key_Space)
    assert _edit_state(gw, "e1") == STATE_PICKED
    QTest.keyClick(page, Qt.Key.Key_C)
    assert _edit_state(gw, "e1") == STATE_SKIPPED
    QTest.keyClick(page, Qt.Key.Key_C)
    assert _edit_state(gw, "e1") == STATE_PICKED


def test_f10_opens_the_processed_lens_preview_stays_a_button(
        page, gw, monkeypatch):
    """Nelson 2026-06-12 standardisation: F10 opens the STANDARD modal
    lens with the PROCESSED, CROPPED image at FULL resolution (what
    export produces) — clean, no zoom/peaking tools in Edit. This ADDS
    to the in-canvas Toggle-Crop preview, which keeps existing,
    button-driven, untouched. P only marks for export — it neither
    previews nor reaches the old dead export branch."""
    export_calls = []
    monkeypatch.setattr(page, "_on_export",
                        lambda: export_calls.append(True))
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

    # Point 1 confirmed in code: the in-canvas preview CONTINUES to
    # exist — the button still toggles the full-res, canvas-fit render.
    page._preview_toggle.click()
    assert page._preview_toggle.isChecked()
    page._preview_toggle.click()
    assert not page._preview_toggle.isChecked()

    QTest.keyClick(page, Qt.Key.Key_P)
    assert not page._preview_toggle.isChecked()     # P no longer previews
    assert export_calls == []                       # the dead branch is dead
    assert _edit_state(gw, "e1") == STATE_PICKED    # P marks for export


def test_decisions_persist_per_photo_across_navigation(page, gw):
    QTest.keyClick(page, Qt.Key.Key_X)              # e1 → skipped
    QTest.keyClick(page, Qt.Key.Key_Right)          # → e2
    QTest.keyClick(page, Qt.Key.Key_P)              # e2 → picked
    assert _edit_state(gw, "e1") == STATE_SKIPPED
    assert _edit_state(gw, "e2") == STATE_PICKED
    QTest.keyClick(page, Qt.Key.Key_Left)           # back on e1
    assert page._index == 0


def test_f_and_f11_both_fullscreen(page):
    flips = []
    page.fullscreen_changed.connect(flips.append)
    QTest.keyClick(page, Qt.Key.Key_F)
    assert page._fullscreen and flips == [True]
    QTest.keyClick(page, Qt.Key.Key_Escape)         # Esc level 1
    assert not page._fullscreen and flips == [True, False]
    QTest.keyClick(page, Qt.Key.Key_F11)
    assert page._fullscreen and flips == [True, False, True]
