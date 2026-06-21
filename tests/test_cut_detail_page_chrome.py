"""spec/94 Phase 3 — CutDetailPage SurfaceBand pass.

The detail page already carried the Share rail (commit a4c2a12); Phase 3
completes the SurfaceBand wrap and the title-bar Back dispatcher
contract so the page reads the same as the rebuilt CutSessionPage and
matches the redesign standard end-to-end.
"""
from __future__ import annotations

import itertools

import pytest

from PyQt6.QtWidgets import QFrame

from mira.gateway.event_gateway import EventGateway
from mira.store.repo import EventStore
from mira.ui.shared.cut_detail_page import CutDetailPage

from tests.test_gateway_cuts import _doc, _now


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


def _cut_with_member(gw):
    """Create one Cut against the fixture event so show_cut() has
    something to render."""
    cut = gw.create_cut("detail_smoke", expr_snapshot=[["+", "exported"]])
    gw.set_cut_members(cut.id, ["Exported Media/e1.jpg"])
    return cut


# ── Standard chrome ─────────────────────────────────────────────


def test_page_carries_share_rail(qapp, gw):
    page = CutDetailPage()
    rails = [
        f for f in page.findChildren(QFrame)
        if f.objectName() == "SurfaceHeaderRail"
    ]
    assert len(rails) == 1
    assert rails[0].property("phase") == "share"
    assert rails[0].height() == 2


def test_page_has_two_surface_bands(qapp, gw):
    page = CutDetailPage()
    bands = [
        f for f in page.findChildren(QFrame)
        if f.objectName() == "SurfaceBand"
    ]
    assert len(bands) >= 2


# ── Title-bar dispatcher contract ───────────────────────────────


def test_uses_titlebar_back_flag(qapp, gw):
    page = CutDetailPage()
    assert getattr(page, "uses_titlebar_back", False) is True


def test_show_help_method_exists(qapp, gw):
    page = CutDetailPage()
    assert callable(getattr(page, "show_help", None))


def test_on_titlebar_back_from_grid_emits_back_requested(qapp, gw):
    page = CutDetailPage()
    fired: list = []
    page.back_requested.connect(lambda: fired.append("leave"))
    page.on_titlebar_back()
    assert fired == ["leave"]


def test_on_titlebar_back_from_single_view_returns_to_grid(qapp, gw):
    page = CutDetailPage()
    cut = _cut_with_member(gw)
    page.show_cut(gw, cut, separators_on=True, aspect="16:9")
    page._open_single(0)
    assert page._stack.currentIndex() == 1
    page.on_titlebar_back()
    assert page._stack.currentIndex() == 0


# ── Render smoke in both themes ────────────────────────────────


def _apply_theme(name: str) -> None:
    from mira.ui.palette import build_redesign_qss
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    assert app is not None
    app.setStyleSheet(build_redesign_qss(name))


def _render_smoke(page) -> None:
    page.resize(1280, 800)
    page.adjustSize()
    pm = page.grab()
    assert not pm.isNull()
    assert pm.width() > 0 and pm.height() > 0


def test_renders_dark_theme(qapp, gw):
    _apply_theme("dark")
    page = CutDetailPage(show_play=True, show_export=True)
    cut = _cut_with_member(gw)
    page.show_cut(gw, cut, separators_on=True, aspect="16:9")
    _render_smoke(page)


def test_renders_light_theme(qapp, gw):
    _apply_theme("light")
    page = CutDetailPage(show_play=True, show_export=True)
    cut = _cut_with_member(gw)
    page.show_cut(gw, cut, separators_on=True, aspect="16:9")
    _render_smoke(page)
