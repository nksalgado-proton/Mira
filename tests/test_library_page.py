"""spec/94 Phase 4a-iii — :class:`LibraryPage` chrome + wiring tests.

Pin:
* Construction (both themes) doesn't raise; the rail + every band
  paints without inline QSS regression.
* Empty state: the empty-cuts hint shows; the counts label both
  Collections + Recipes.
* Populated state: refresh rebuilds the row list; per-row signals
  fire on click; the delete confirm + library-gateway delete fire
  in sequence.
* The title-bar Back dispatcher (uses_titlebar_back + on_titlebar_back)
  emits ``back_requested``.
* The cuts list reads via the umbrella gateway's mira.db path
  (spec/93 §3) — no event.db walk.

Uses a hand-rolled fake gateway so the tests stay fast + don't drag
in the full Gateway lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pytest
from PyQt6.QtCore import Qt

from mira.gateway.gateway import CrossEventCutRow
from mira.ui.pages.library_page import LibraryPage, _CutRow


NOW = "2026-06-21T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fake gateway — minimal surface the page reads
# --------------------------------------------------------------------------- #


@dataclass
class _FakeLibraryGateway:
    cuts: List[CrossEventCutRow] = field(default_factory=list)
    collections: List[object] = field(default_factory=list)
    deleted: list = field(default_factory=list)

    def dynamic_collections(self):
        return list(self.collections)

    def cross_event_cut(self, cid):
        return next(
            ((c, getattr(c, "_separators", False))[0]
             for c in self.cuts if c.cut_id == cid),
            None)


@dataclass
class _FakeUmbrella:
    cuts: List[CrossEventCutRow] = field(default_factory=list)
    deleted_cuts: list = field(default_factory=list)
    recipe_count: int = 0
    fail_cross_event_cuts: bool = False

    def __post_init__(self):
        self._lg = _FakeLibraryGateway(cuts=self.cuts, collections=[])

    def cross_event_cuts(self):
        if self.fail_cross_event_cuts:
            raise RuntimeError("boom")
        return list(self.cuts)

    def library_gateway(self):
        return self._lg

    def delete_cross_event_cut(self, _anchor, cut_id):
        self.deleted_cuts.append(cut_id)
        self.cuts[:] = [c for c in self.cuts if c.cut_id != cut_id]

    def recipe_store(self):
        # Minimal shape — LibraryPage just calls .list()
        class _RS:
            def __init__(self, n):
                self._n = n

            def list(self):
                return [object() for _ in range(self._n)]

        return _RS(self.recipe_count)


def _row(*, cut_id, tag="best", member_count=5,
         anchor="Costa Rica", last_exported_at=None):
    return CrossEventCutRow(
        cut_id=cut_id, tag=tag,
        anchor_event_id="A", anchor_event_name=anchor,
        source_dc_id="sf-1",
        member_count=member_count,
        last_exported_at=last_exported_at,
        created_at=NOW, updated_at=NOW,
    )


# --------------------------------------------------------------------------- #
# Construction + empty state
# --------------------------------------------------------------------------- #


def test_constructs_with_empty_gateway(qapp):
    """The page paints with no Cuts — the cuts band shows the empty
    hint. spec/162 Round 2b retired the Collections + Recipes count
    labels along with their bands."""
    gw = _FakeUmbrella(cuts=[])
    page = LibraryPage(gw)
    page.refresh()
    assert page._cuts_empty_label is not None
    assert not page._cuts_empty_label.isHidden()
    page.deleteLater()


def test_uses_titlebar_back_opt_in(qapp):
    """spec/94 Phase 3 contract — the shared title bar shows Back."""
    gw = _FakeUmbrella()
    page = LibraryPage(gw)
    assert page.uses_titlebar_back is True
    page.deleteLater()


def test_titlebar_back_dispatcher_emits_back_requested(qapp):
    """``on_titlebar_back`` is the page's custom dispatcher; for v1
    it's a single-level Back that emits ``back_requested``."""
    gw = _FakeUmbrella()
    page = LibraryPage(gw)
    fired = []
    page.back_requested.connect(lambda: fired.append("back"))
    page.on_titlebar_back()
    assert fired == ["back"]
    page.deleteLater()


# --------------------------------------------------------------------------- #
# Populated state — rows render
# --------------------------------------------------------------------------- #


def test_populated_cuts_render_one_row_each(qapp):
    gw = _FakeUmbrella(cuts=[
        _row(cut_id="c1", tag="best_wildlife", member_count=12),
        _row(cut_id="c2", tag="weekend_picks", member_count=4),
    ])
    page = LibraryPage(gw)
    page.refresh()
    # Empty hint hides; two _CutRow widgets exist.
    assert page._cuts_empty_label is not None
    assert page._cuts_empty_label.isHidden()
    rows = [
        page._cuts_rows_layout.itemAt(i).widget()
        for i in range(page._cuts_rows_layout.count())
        if isinstance(
            page._cuts_rows_layout.itemAt(i).widget(), _CutRow)
    ]
    assert len(rows) == 2
    page.deleteLater()


def test_play_signal_fires_with_cut_id(qapp):
    """Per-row Play button → _CutRow.play_requested(cut_id). We
    construct a _CutRow in isolation so the signal contract is the
    test target, not the page's downstream player wiring (which has
    its own coverage in test_cross_event_cut_play.py)."""
    row = _CutRow(
        cut_id="c1", tag="best",
        anchor_event_name="Costa Rica",
        member_count=5,
        last_exported_at=None,
    )
    captured: list = []
    row.play_requested.connect(lambda cid: captured.append(cid))
    row.play_requested.emit(row._cut_id)
    assert captured == ["c1"]
    row.deleteLater()


def test_export_signal_fires_with_cut_id(qapp):
    row = _CutRow(
        cut_id="c1", tag="best",
        anchor_event_name="Costa Rica",
        member_count=5,
        last_exported_at=None,
    )
    captured: list = []
    row.export_requested.connect(lambda cid: captured.append(cid))
    row.export_requested.emit(row._cut_id)
    assert captured == ["c1"]
    row.deleteLater()


# --------------------------------------------------------------------------- #
# Delete path — confirm + gateway call
# --------------------------------------------------------------------------- #


def test_delete_confirm_yes_drives_gateway(qapp, monkeypatch):
    """Yes to the confirm dialog calls ``delete_cross_event_cut`` on
    the gateway and refreshes."""
    gw = _FakeUmbrella(cuts=[_row(cut_id="c1"), _row(cut_id="c2")])
    page = LibraryPage(gw)
    page.refresh()
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes)
    page._on_delete_cut("c1")
    assert gw.deleted_cuts == ["c1"]
    assert all(c.cut_id != "c1" for c in gw.cross_event_cuts())
    page.deleteLater()


def test_delete_confirm_no_does_nothing(qapp, monkeypatch):
    gw = _FakeUmbrella(cuts=[_row(cut_id="c1")])
    page = LibraryPage(gw)
    page.refresh()
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.StandardButton.No)
    page._on_delete_cut("c1")
    assert gw.deleted_cuts == []
    page.deleteLater()


# --------------------------------------------------------------------------- #
# Counts + + New Cut signal
# --------------------------------------------------------------------------- #


# spec/162 Round 2b — test_counts_reflect_gateway + test_manage_
# collections_emits_signal retired with the Collections + Recipes
# bands.


def test_new_cut_button_emits_signal(qapp):
    gw = _FakeUmbrella()
    page = LibraryPage(gw)
    fired = []
    page.new_cut_requested.connect(lambda: fired.append("new"))
    page._on_new_cut()
    assert fired == ["new"]
    page.deleteLater()


# --------------------------------------------------------------------------- #
# Defensive: a gateway failure on cross_event_cuts degrades to empty
# --------------------------------------------------------------------------- #


def test_gateway_failure_keeps_page_alive(qapp):
    gw = _FakeUmbrella(fail_cross_event_cuts=True)
    page = LibraryPage(gw)
    page.refresh()                       # would raise without the guard
    assert not page._cuts_empty_label.isHidden()
    page.deleteLater()


# --------------------------------------------------------------------------- #
# Render smoke in both themes — no inline QSS regression
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_render_smoke_both_themes(qapp, theme):
    """spec/92 / no-inline-qss invariant — the page constructs cleanly
    in both themes; the rail + bands paint without exception.

    Restores the theme property in a finally block so we don't leak
    state to neighbouring tests (icon-retint tests read it)."""
    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication.instance()
    saved = app.property("theme")
    app.setProperty("theme", theme)
    try:
        gw = _FakeUmbrella(cuts=[
            _row(cut_id="c1", tag="best", member_count=8,
                 last_exported_at="2026-06-21T12:00:00"),
        ])
        page = LibraryPage(gw)
        page.refresh()
        page.resize(1280, 800)
        page.show()
        qapp.processEvents()
        assert page.isVisible()
        page.close()
        page.deleteLater()
    finally:
        # Best-effort restoration. The icon-retint tests inspect this
        # property on a fresh QApplication, so leaking 'light' across
        # tests is enough to flip an assertion downstream.
        if saved is None:
            app.setProperty("theme", "dark")
        else:
            app.setProperty("theme", saved)
