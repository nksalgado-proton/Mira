"""spec/162 §3.2 Round 3c — LibraryPage collapse to ShareCutsPage
mirror shape.

Pins:
* The library-scope Base Collection card renders under the header,
  showing the aggregate file count + event count.
* The + New Cut header button + the Base Collection card ``Open``
  button fire the right signals.
* The Cut row now exposes Open / Edit Cut / Play / Export / Delete /
  Publish signals; the row shape matches ShareCutsPage's row (kebab
  on rare actions, Open primary + Edit Cut ghost).
* Gateway aggregate is consumed via
  :meth:`Gateway.library_exported_summary` — the returning dict
  drives the card subtitle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pytest

from mira.gateway.gateway import CrossEventCutRow
from mira.ui.pages.library_page import (
    LibraryPage,
    _CutRow,
    _LibraryPoolCard,
)


NOW = "2026-06-21T00:00:00+00:00"


@dataclass
class _FakeLibraryGateway:
    cuts: List[CrossEventCutRow] = field(default_factory=list)


@dataclass
class _FakeUmbrella:
    cuts: List[CrossEventCutRow] = field(default_factory=list)
    summary: dict = field(default_factory=lambda: {
        "file_count": 42, "event_count": 3})
    deleted_cuts: list = field(default_factory=list)

    def __post_init__(self):
        self._lg = _FakeLibraryGateway(cuts=self.cuts)

    def cross_event_cuts(self):
        return list(self.cuts)

    def library_exported_summary(self):
        return dict(self.summary)

    def library_gateway(self):
        return self._lg

    def delete_cross_event_cut(self, _a, cut_id):
        self.deleted_cuts.append(cut_id)
        self.cuts[:] = [c for c in self.cuts if c.cut_id != cut_id]


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


def test_base_collection_card_renders_with_aggregate(qapp):
    gw = _FakeUmbrella(summary={"file_count": 812, "event_count": 5})
    page = LibraryPage(gw)
    page.refresh()
    # The pool slot carries exactly one _LibraryPoolCard widget.
    slot = page._pool_slot
    assert slot is not None
    cards = [
        slot.itemAt(i).widget()
        for i in range(slot.count())
        if isinstance(slot.itemAt(i).widget(), _LibraryPoolCard)
    ]
    assert len(cards) == 1
    # The card's subtitle carries both counts.
    card = cards[0]
    labels = card.findChildren(type(card.findChild(object, "Sub")))
    # At least one label must mention "812" and "5".
    sub_texts = [
        lbl.text() for lbl in card.findChildren(object)
        if hasattr(lbl, "text") and lbl.text
    ]
    joined = " ".join(t for t in sub_texts if isinstance(t, str))
    assert "812" in joined
    assert "5" in joined
    page.deleteLater()


def test_base_collection_open_signal_fires(qapp):
    gw = _FakeUmbrella()
    page = LibraryPage(gw)
    page.refresh()
    fired = []
    page.library_pool_open_requested.connect(
        lambda: fired.append("open-pool"))
    # Trigger the card's open button directly by simulating the signal.
    slot = page._pool_slot
    for i in range(slot.count()):
        w = slot.itemAt(i).widget()
        if isinstance(w, _LibraryPoolCard):
            w.open_requested.emit()
            break
    assert fired == ["open-pool"]
    page.deleteLater()


def test_cut_row_signals_open_and_adjust(qapp):
    row = _CutRow(
        cut_id="c1", tag="best",
        anchor_event_name="Costa Rica",
        member_count=5,
        last_exported_at=None,
    )
    captured: dict = {}
    row.open_requested.connect(lambda cid: captured.setdefault("open", cid))
    row.adjust_requested.connect(
        lambda cid: captured.setdefault("adjust", cid))
    row.open_requested.emit(row._cut_id)
    row.adjust_requested.emit(row._cut_id)
    assert captured == {"open": "c1", "adjust": "c1"}
    row.deleteLater()


def test_cut_row_has_edit_cut_and_kebab_actions(qapp):
    """The row exposes Open + Edit Cut + kebab menu (with Play /
    Export / Publish / Delete)."""
    row = _CutRow(
        cut_id="c1", tag="best",
        anchor_event_name="Costa Rica",
        member_count=5,
        last_exported_at=None,
    )
    # All the signals we expect exist on the row.
    for name in (
        "open_requested", "adjust_requested", "play_requested",
        "export_requested", "delete_requested", "publish_requested",
    ):
        assert hasattr(row, name), f"row missing {name}"
    row.deleteLater()


def test_new_cut_button_emits_signal(qapp):
    gw = _FakeUmbrella()
    page = LibraryPage(gw)
    fired = []
    page.new_cut_requested.connect(lambda: fired.append("new"))
    page._on_new_cut()
    assert fired == ["new"]
    page.deleteLater()


def test_page_no_bands_no_manage_collections(qapp):
    """spec/162 Round 3c retired the Collections + Recipes bands
    (Round 2b already retired the count labels) and the cross-event
    Cuts band header. The page should have no widget matching those
    retired role names."""
    gw = _FakeUmbrella(cuts=[_row(cut_id="c1")])
    page = LibraryPage(gw)
    page.refresh()
    # No band frames labelled Collections / Recipes / Cross-event
    # Cuts. The one SurfaceBand of the old shape is gone.
    from PyQt6.QtWidgets import QFrame, QLabel
    for lbl in page.findChildren(QLabel):
        text = lbl.text() or ""
        assert "Cross-event Cuts" not in text
        assert "Manage Collections" not in text
        assert "Recipes" not in text
    page.deleteLater()
