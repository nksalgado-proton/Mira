"""spec/162 §3.2 / §7.2 Round 3d — library-scope DCDetailPage.

Pins the scope-parameterized DCDetailPage:

* Constructing with ``scope=SCOPE_CROSS_EVENT`` puts the FilterBar
  into cross-event mode (four extra dimension widgets visible).
* :meth:`open_library_pool` binds to the umbrella gateway + walks
  :meth:`Gateway.library_exported_grid_items` to build the union
  grid.
* :meth:`open_pool` at cross-event scope raises (wrong shape); vice
  versa at event scope.
* At cross-event scope, cluster machinery is bypassed — every row
  gets its own flat cell. Write-side toolbar buttons stay hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import List

import pytest

from mira.ui.exported.filter_bar import (
    SCOPE_CROSS_EVENT as _BAR_SCOPE_CROSS,
    SCOPE_EVENT as _BAR_SCOPE_EVENT,
)
from mira.ui.shared.dc_detail_page import (
    SCOPE_CROSS_EVENT,
    SCOPE_EVENT,
    DCDetailPage,
)


@dataclass
class _FakeUmbrella:
    grid_items: List[object] = field(default_factory=list)

    def library_exported_grid_items(self):
        return list(self.grid_items)


def _row(rel, event_id="e1", event_name="Event", *,
         camera=None, lens_model=None, capture_date=None,
         stars=None, color_label=None, flag=False, to_delete=False):
    return SimpleNamespace(
        export_relpath=rel,
        event_id=event_id,
        event_name=event_name,
        stars=stars,
        color_label=color_label,
        flag=flag,
        to_delete=to_delete,
        camera=camera,
        lens_model=lens_model,
        capture_date=capture_date,
        source_item_id=None,
        provenance="mira_render",
    )


def test_default_scope_is_event(qapp):
    page = DCDetailPage()
    assert page._scope == SCOPE_EVENT
    # The bar renders in event mode — the four cross-event group
    # boxes stay hidden.
    assert page._filter_bar.scope == _BAR_SCOPE_EVENT
    page.deleteLater()


def test_cross_event_scope_flags_bar(qapp):
    page = DCDetailPage(scope=SCOPE_CROSS_EVENT)
    assert page._scope == SCOPE_CROSS_EVENT
    assert page._filter_bar.scope == _BAR_SCOPE_CROSS
    page.deleteLater()


def test_bad_scope_raises(qapp):
    with pytest.raises(ValueError):
        DCDetailPage(scope="bogus")


def test_open_pool_rejects_cross_event_scope(qapp):
    """open_pool is the event-scope entry — using it under cross-
    event scope is a caller bug and should raise."""
    page = DCDetailPage(scope=SCOPE_CROSS_EVENT)
    with pytest.raises(RuntimeError):
        page.open_pool(object())
    page.deleteLater()


def test_open_library_pool_rejects_event_scope(qapp):
    page = DCDetailPage(scope=SCOPE_EVENT)
    with pytest.raises(RuntimeError):
        page.open_library_pool(object())
    page.deleteLater()


def test_open_library_pool_builds_flat_grid_over_all_events(qapp):
    gw = _FakeUmbrella(grid_items=[
        _row("Exported Media/Dia 1/p1.jpg", "e1", "Alaska",
             camera="G9", lens_model="12-35", capture_date="2026-04-01"),
        _row("Exported Media/Dia 1/p2.jpg", "e2", "Bali",
             camera="R5", lens_model="24-70", capture_date="2026-05-10"),
        _row("Exported Media/Dia 1/p3.jpg", "e2", "Bali",
             camera="R5", lens_model="24-70", capture_date="2026-05-11"),
    ])
    page = DCDetailPage(scope=SCOPE_CROSS_EVENT)
    page.open_library_pool(gw)
    # Every row lands as a flat cell — no cluster folding across
    # events (source_item_id may collide across events).
    assert len(page._cells) == 3
    assert all(c.kind == "flat" for c in page._cells)
    page.close_event()
    page.deleteLater()


def test_open_library_pool_populates_camera_lens_inventory(qapp):
    gw = _FakeUmbrella(grid_items=[
        _row("a.jpg", camera="G9", lens_model="12-35"),
        _row("b.jpg", camera="R5", lens_model="24-70"),
        _row("c.jpg", camera="G9", lens_model="35 1.4"),
    ])
    page = DCDetailPage(scope=SCOPE_CROSS_EVENT)
    page.open_library_pool(gw)
    # The FilterBar's inventory pickers carry every unique value
    # the union grid contains.
    cameras = {
        page._filter_bar._camera_combo._model.item(i).text()
        for i in range(page._filter_bar._camera_combo._model.rowCount())
    }
    lenses = {
        page._filter_bar._lens_combo._model.item(i).text()
        for i in range(page._filter_bar._lens_combo._model.rowCount())
    }
    assert cameras == {"G9", "R5"}
    assert lenses == {"12-35", "24-70", "35 1.4"}
    page.close_event()
    page.deleteLater()


def test_open_library_pool_hides_write_side_toolbar(qapp):
    """spec/162 §3.2 — library scope is search + review; delete +
    clear marks stay hidden. The write-side surfaces still live on
    the per-event DCDetailPage."""
    gw = _FakeUmbrella(grid_items=[_row("a.jpg", to_delete=True)])
    page = DCDetailPage(scope=SCOPE_CROSS_EVENT)
    page.open_library_pool(gw)
    assert page._delete_btn.isVisibleTo(page) is False
    assert page._clear_btn.isVisibleTo(page) is False
    page.close_event()
    page.deleteLater()


def test_cross_event_filter_matches_camera_narrows_grid(qapp):
    from mira.ui.exported.filter_popup import LineageFilter
    gw = _FakeUmbrella(grid_items=[
        _row("a.jpg", camera="G9"),
        _row("b.jpg", camera="R5"),
        _row("c.jpg", camera="G9"),
    ])
    page = DCDetailPage(scope=SCOPE_CROSS_EVENT)
    page.open_library_pool(gw)
    page._on_filter_changed(LineageFilter(cameras={"G9"}))
    assert len(page._cells) == 2
    page.close_event()
    page.deleteLater()
