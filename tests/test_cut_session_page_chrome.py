"""spec/94 Phase 3 — CutSessionPage chrome standardisation.

The session surface picked up the redesign-standard chrome:

* Flush full-width ``#SurfaceHeaderRail[phase="share"]`` at the top
  (the pink Share-state rail).
* Content margins 28/18/28/22 around a stack of ``#SurfaceBand`` boxes
  (top band: title + Create + budget; grid band: days/grid/single
  stack).
* Back lives in the shared title bar — the page exposes
  ``uses_titlebar_back = True``, ``back_requested`` (signal),
  ``on_titlebar_back()`` (dispatcher), and ``show_help()`` (F1 hook).
* Render smoke in both themes (dark + light) so the role swap reads
  cleanly through QSS.
"""
from __future__ import annotations

import itertools

import pytest

from PyQt6.QtWidgets import QFrame

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_draft import PIN_WEED_OUT
from mira.shared.cut_session import CutSession
from mira.store.repo import EventStore
from mira.ui.shared.cut_session_page import CutSessionPage

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


# ── Standard chrome roles ───────────────────────────────────────


def test_page_carries_share_rail(qapp, gw, tmp_path):
    """spec/61 — closed-event state. The rail object name + ``phase``
    property are the QSS seam ``redesign.qss`` keys on."""
    page = _page(gw, tmp_path)
    rails = [
        f for f in page.findChildren(QFrame)
        if f.objectName() == "SurfaceHeaderRail"
    ]
    assert len(rails) == 1
    assert rails[0].property("phase") == "share"
    assert rails[0].height() == 2


def test_page_has_two_surface_bands(qapp, gw, tmp_path):
    """spec/92 — content sits in ``#SurfaceBand`` boxes; the session
    has two (top + grid)."""
    page = _page(gw, tmp_path)
    bands = [
        f for f in page.findChildren(QFrame)
        if f.objectName() == "SurfaceBand"
    ]
    # At least two; sub-pages (single view) may use Card-style roles
    # internally but the top-level SurfaceBands are the session's two.
    assert len(bands) >= 2


def test_legacy_pageheading_role_gone(qapp, gw, tmp_path):
    """Title was a bare ``#PageHeading`` row; it's now a ``#CardTitle``
    inside the top SurfaceBand."""
    page = _page(gw, tmp_path)
    from PyQt6.QtWidgets import QLabel
    objects = [w.objectName() for w in page.findChildren(QLabel)]
    assert "PageHeading" not in objects
    # CardTitle is present somewhere (the session's title).
    assert "CardTitle" in objects


# ── Title-bar dispatcher contract ──────────────────────────────


def test_uses_titlebar_back_flag(qapp, gw, tmp_path):
    page = _page(gw, tmp_path)
    assert getattr(page, "uses_titlebar_back", False) is True


def test_back_requested_signal_exists(qapp, gw, tmp_path):
    page = _page(gw, tmp_path)
    assert hasattr(page, "back_requested")
    sig = getattr(page, "back_requested")
    # PyQt signals expose ``connect``; cheapest probe.
    assert callable(getattr(sig, "connect", None))


def test_show_help_method_exists(qapp, gw, tmp_path):
    page = _page(gw, tmp_path)
    assert callable(getattr(page, "show_help", None))


def test_on_titlebar_back_dispatches_per_stack_state(qapp, gw, tmp_path):
    """spec/94 Phase 3 — Back steps back one level inside the
    drilldown, only leaving the session at the top level. (spec/98
    revised the start landing to the day list; the per-level dispatch
    still steps single → grid → days → leave.)"""
    page = _page(gw, tmp_path)
    # spec/98 — start now lands on the day list (index 0). Drill into a
    # day so the test exercises the grid → days → leave walk.
    assert page._stack.currentIndex() == 0
    page._open_day(0)
    assert page._stack.currentIndex() == 1
    fired: list = []
    page.back_requested.connect(lambda: fired.append("leave"))
    # Title-bar Back at the grid → days panel (index 0), no signal yet.
    page.on_titlebar_back()
    assert page._stack.currentIndex() == 0
    assert fired == []
    # Title-bar Back at the days panel → emits the leave gesture.
    page.on_titlebar_back()
    assert fired == ["leave"]


def test_on_titlebar_back_from_single_view_returns_to_grid(qapp, gw, tmp_path):
    """Three-level dispatch — single → grid → days → leave."""
    page = _page(gw, tmp_path)
    # spec/98 — drill into a day's grid so ``_open_single`` has its
    # backing day_items populated.
    page._open_day(0)
    page._open_single(0)
    assert page._stack.currentIndex() == 2
    page.on_titlebar_back()
    # Back to the grid (index 1).
    assert page._stack.currentIndex() == 1


# ── Render smoke in both themes ────────────────────────────────


def _apply_theme(name: str) -> None:
    """Apply the named theme to the QApplication instance. The redesign
    template is one stylesheet driven by token substitution; we just
    repolish the active app so the role selectors fire."""
    from mira.ui.palette import build_redesign_qss
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    assert app is not None
    qss = build_redesign_qss(name)
    app.setStyleSheet(qss)


def _render_smoke(page) -> None:
    """Grab the page to a pixmap; non-empty + no exceptions = smoke
    test passes. We don't pixel-compare — the renderer must just walk
    the QSS rules without ``QPainter`` warnings or crashes."""
    page.resize(1280, 800)
    page.adjustSize()
    pm = page.grab()
    assert not pm.isNull()
    assert pm.width() > 0 and pm.height() > 0


def test_renders_dark_theme(qapp, gw, tmp_path):
    _apply_theme("dark")
    page = _page(gw, tmp_path)
    _render_smoke(page)


def test_renders_light_theme(qapp, gw, tmp_path):
    _apply_theme("light")
    page = _page(gw, tmp_path)
    _render_smoke(page)
