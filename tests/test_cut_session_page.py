"""spec/61 slice 5 (page half) — the Cut session surface.

Driven without an event loop: construct over the real event.db fixture,
poke handlers, read widgets. Pins the separate-ledger wiring (border
click → CutSession, never phase_state), the live budget line + zones,
days→grid→single routing, undo, and the commit/cancel seam.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import CutSession
from mira.store.repo import EventStore
from mira.ui.shared.cut_session_page import CutSessionPage

from mira.shared.cut_draft import PIN_WEED_OUT
from tests.test_cut_session import _draft
from tests.test_gateway_cuts import _doc, _now


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


def _page(gw, tmp_path, **draft_over) -> CutSessionPage:
    session = CutSession.from_draft(gw, _draft(**draft_over))
    return CutSessionPage(gw, session, event_root=tmp_path)


# --------------------------------------------------------------------------- #
# Days panel
# --------------------------------------------------------------------------- #


def test_days_panel_rows_with_pick_counts(qapp, gw, tmp_path):
    page = _page(gw, tmp_path, pin_mode=PIN_WEED_OUT)   # weed-out = all-picked
    texts = [b.text() for b in page._days._buttons]
    assert len(texts) == 2
    assert "Day 1" in texts[0] and "1 of 1 picked" in texts[0]
    assert "Day 2" in texts[1] and "3 of 3 picked" in texts[1]


def test_start_lands_on_the_first_day_grid(qapp, gw, tmp_path):
    """Nelson eyeball 2026-06-12: Start must put PHOTOS on screen —
    the first day's grid opens immediately; days panel is one Back away."""
    page = _page(gw, tmp_path)
    assert page._stack.currentIndex() == 1
    payloads = [it.payload for it in page._grid.items()]
    assert payloads and payloads[0] == "Exported Media/e2.jpg"
    page._back_to_days()
    assert page._stack.currentIndex() == 0


# --------------------------------------------------------------------------- #
# Grid level — cells from the session ledger
# --------------------------------------------------------------------------- #


def test_open_day_builds_file_cells_with_session_colors(qapp, gw, tmp_path):
    page = _page(gw, tmp_path)                  # default all-skipped
    page._open_day(1)                           # day 2: e3a, e3b, v1
    assert page._stack.currentIndex() == 1
    items = page._grid.items()
    assert [it.payload for it in items] == [
        "Exported Media/e3a.jpg", "Exported Media/e3b.jpg", "Exported Media/v1.mp4"]
    # All-skipped default → every cell wears the locked "skipped"
    # state token (red 3px border).
    assert all(it.state == "skipped" for it in items)
    assert "Day 2" in page._grid_header.text()


def test_video_cells_carry_the_video_badge(qapp, gw, tmp_path):
    """Nelson 2026-06-19 — video tiles were invisible (no poster from
    the photo cache, no badge to label them as videos) so the user
    couldn't pick/skip them. Every video cell now carries the
    ``cluster_type='video'`` badge so it reads as a video at a glance,
    poster or no poster."""
    page = _page(gw, tmp_path)
    page._open_day(1)                           # day 2 has the video
    items = page._grid.items()
    by_payload = {it.payload: it for it in items}
    photo_cell = by_payload["Exported Media/e3a.jpg"]
    video_cell = by_payload["Exported Media/v1.mp4"]
    assert photo_cell.cluster_type is None
    assert video_cell.cluster_type == "video"
    assert video_cell.cluster_count == 1


def test_border_click_toggles_ledger_and_budget(qapp, gw, tmp_path):
    page = _page(gw, tmp_path, target_s=40, max_s=90)
    page._open_day(1)
    assert "0 picked" in page._budget._label.text()
    page._toggle_cell(0)                        # e3a → picked
    assert page._session.is_picked("Exported Media/e3a.jpg")
    assert page._grid.items()[0].state == "picked"
    assert "1 picked" in page._budget._label.text()
    # (1 photo + 1 separator) × 6 s = 12 s of the 40 s target → green
    assert page._budget.property("zone") == "green"
    page._toggle_cell(2)                        # +30 s video → 42 s… still day 2
    page._toggle_cell(1)                        # +6 s photo → 48 s > 40 → amber
    assert page._budget.property("zone") == "amber"


def test_phase_state_untouched_by_grid_clicks(qapp, gw, tmp_path):
    before = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM phase_state").fetchone()["n"]
    page = _page(gw, tmp_path)
    page._open_day(1)
    page._toggle_cell(0)
    page._toggle_cell(1)
    after = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM phase_state").fetchone()["n"]
    assert after == before


# --------------------------------------------------------------------------- #
# Single level + undo
# --------------------------------------------------------------------------- #


def test_single_view_set_state_and_step(qapp, gw, tmp_path):
    page = _page(gw, tmp_path)
    page._open_day(1)
    page._open_single(0)
    assert page._stack.currentIndex() == 2
    assert "not in the Cut" in page._single._state.text()
    assert page._single._frame.property("status") == "skipped"
    # P = pick (SET, not toggle — the Picker's grammar); twice stays picked
    page._set_relpath_state("Exported Media/e3a.jpg", True)
    page._set_relpath_state("Exported Media/e3a.jpg", True)
    assert "✓ in the Cut" in page._single._state.text()
    assert page._single._frame.property("status") == "picked"
    assert page._session.is_picked("Exported Media/e3a.jpg")
    page._set_relpath_state("Exported Media/e3a.jpg", False)
    assert page._single._frame.property("status") == "skipped"
    page._single._viewport._go(+1)              # arrows live in the viewport
    assert page._single.current_file().export_relpath == "Exported Media/e3b.jpg"
    page._single._viewport._go(+5)              # past the edge → stays
    assert page._single.current_file().export_relpath == "Exported Media/e3b.jpg"


def test_single_view_keys_speak_the_locked_map(qapp, gw, tmp_path):
    """spec/63 §4 on a real surface: P picks, X skips, Space toggles,
    C cycles (binary ledger → degrades to the toggle), arrows step,
    and the chrome follows the ledger."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    page = _page(gw, tmp_path)
    page._open_day(1)
    page._open_single(0)
    vp = page._single._viewport
    QTest.keyClick(vp, Qt.Key.Key_P)
    assert page._session.is_picked("Exported Media/e3a.jpg")
    assert page._single._frame.property("status") == "picked"
    QTest.keyClick(vp, Qt.Key.Key_X)
    assert not page._session.is_picked("Exported Media/e3a.jpg")
    assert page._single._frame.property("status") == "skipped"
    QTest.keyClick(vp, Qt.Key.Key_Space)        # toggle → picked
    assert page._session.is_picked("Exported Media/e3a.jpg")
    QTest.keyClick(vp, Qt.Key.Key_C)            # cycle ≡ toggle here
    assert not page._session.is_picked("Exported Media/e3a.jpg")
    QTest.keyClick(vp, Qt.Key.Key_Right)        # step → chrome follows
    assert page._single.current_file().export_relpath == "Exported Media/e3b.jpg"
    assert "e3b" in page._single._title.text()


def test_single_view_wheel_steps_like_the_picker(qapp, gw, tmp_path):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent

    def _wheel(widget, dy):
        widget.wheelEvent(QWheelEvent(
            QPointF(1, 1), QPointF(1, 1), QPoint(0, 0), QPoint(0, dy),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False))

    page = _page(gw, tmp_path)
    page._open_day(1)
    page._open_single(0)
    _wheel(page._single._viewport, -120)        # wheel down = next
    assert page._single.current_file().export_relpath == "Exported Media/e3b.jpg"
    _wheel(page._single._viewport, +120)        # wheel up = previous
    assert page._single.current_file().export_relpath == "Exported Media/e3a.jpg"


def test_single_view_shows_standard_f10_f11_buttons(qapp, gw, tmp_path):
    """spec/63 — the Cut single view follows the same PhotoViewport
    chrome pattern as picker_page / quick_sweep_page / editor_page:
    the corner 🔍 magnifier hides and a labelled Full Resolution F10 +
    Full Screen F11 button pair sits at the bottom."""
    page = _page(gw, tmp_path)
    page._open_day(1)
    page._open_single(0)
    single = page._single
    # The labelled buttons exist and are visible.
    assert hasattr(single, "_fullres_btn")
    assert hasattr(single, "_fullscreen_btn")
    assert single._fullres_btn.isVisibleTo(single)
    assert single._fullscreen_btn.isVisibleTo(single)
    # The corner inspect glyph is suppressed (the labelled button is
    # the canonical affordance on every PhotoViewport host).
    assert single._viewport._corner_inspect_visible is False
    assert not single._viewport._inspect_btn.isVisible()


def test_single_view_fullres_button_emits_truth_requested(qapp, gw, tmp_path):
    """Clicking Full Resolution fires the viewport's ``truth_requested``
    signal — the same signal F10 produces inside the viewport."""
    page = _page(gw, tmp_path)
    page._open_day(1)
    page._open_single(0)
    seen = []
    page._single._viewport.truth_requested.connect(lambda: seen.append(True))
    page._single._fullres_btn.click()
    assert seen == [True]


def test_single_view_fullscreen_button_emits_fullscreen_requested(qapp, gw, tmp_path):
    """Clicking Full Screen fires the SingleView's ``fullscreen_requested``
    signal — the same signal the F11 shortcut + the viewport's F-key
    bubble up."""
    page = _page(gw, tmp_path)
    page._open_day(1)
    page._open_single(0)
    seen = []
    page._single.fullscreen_requested.connect(lambda: seen.append(True))
    page._single._fullscreen_btn.click()
    assert seen == [True]


def test_touched_decisions_repaint_their_cells_on_back(qapp, gw, tmp_path):
    """Decisions made while stepping the single view repaint their grid
    cells on Back — ALL of them, not just the last (the Day-Grid
    touched-set rule)."""
    page = _page(gw, tmp_path)
    page._open_day(1)                           # all skipped
    page._open_single(0)
    page._set_relpath_state("Exported Media/e3a.jpg", True)
    page._single._viewport._go(+1)
    page._set_relpath_state("Exported Media/e3b.jpg", True)
    assert page._grid.items()[0].state == "skipped"  # not yet repainted
    page._back_to_grid()
    assert page._grid.items()[0].state == "picked"
    assert page._grid.items()[1].state == "picked"
    assert page._touched == set()


def test_undo_reverts_and_repaints(qapp, gw, tmp_path):
    page = _page(gw, tmp_path)
    page._open_day(1)
    page._toggle_cell(0)
    assert page._grid.items()[0].state == "picked"
    page._on_undo()
    assert page._grid.items()[0].state == "skipped"
    assert not page._session.is_picked("Exported Media/e3a.jpg")


# --------------------------------------------------------------------------- #
# Commit / cancel seam
# --------------------------------------------------------------------------- #


def test_create_commits_and_emits_finished(qapp, gw, tmp_path):
    page = _page(gw, tmp_path)
    page._open_day(1)
    page._toggle_cell(0)
    got = []
    page.finished.connect(got.append)
    page._on_create()
    assert len(got) == 1
    cut = got[0]
    assert cut.tag == "passaros_2026"
    assert [ln.export_relpath for ln in gw.cut_member_files(cut.id)] == [
        "Exported Media/e3a.jpg"]


def test_cancel_emits_without_writing(qapp, gw, tmp_path):
    page = _page(gw, tmp_path)                  # no decisions made
    got = []
    page.cancelled.connect(lambda: got.append(True))
    page._on_cancel()
    assert got == [True]
    assert [c.tag for c in gw.cuts()] == ["short_version"]
