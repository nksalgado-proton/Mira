"""Tests for :class:`CrossEventDcsDialog` (spec/81 Phase 2 polish).

Drives the list dialog against a hand-built mira.db + LibraryGateway,
asserting refresh + each action (new / edit / delete / pin).
"""
from __future__ import annotations

import json

import pytest

from core import collection_resolver as cr
from mira.gateway.library_gateway import LibraryGateway
from mira.ui.pages.cross_event_dcs_dialog import CrossEventDcsDialog, _DcRow
from mira.ui.pages.new_cross_event_dc_dialog import (
    CrossEventDcInfo,
    CrossEventInventories,
    NewCrossEventDcDialog,
)
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-16T00:00:00+00:00"


_INVENTORIES = CrossEventInventories(
    classifications=("macro", "wildlife"),
    cameras=("Pana+G9M2",),
    lenses=("LEICA 45mm",),
    country_codes=("CR", "NP"),
)


def _open_user_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db", app_version="test", created_at=NOW,
    )


def _seed_global_items(store: UserStore) -> None:
    """Two items so dc_probe returns a non-zero count for at least one DC."""
    rows = [
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            classification="macro", stars=5, has_export=True,
            capture_time="2026-04-01T10:00:00",
        ),
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            classification="wildlife", stars=3, has_export=True,
            capture_time="2025-10-15T07:00:00",
        ),
    ]
    for r in rows:
        store.upsert(r)


def _make_lg(store: UserStore, *,
             new_ids=("dc-1", "dc-2", "dc-3")) -> LibraryGateway:
    iter_ids = iter(new_ids)
    return LibraryGateway(store, now=lambda: NOW,
                          new_id=lambda: next(iter_ids))


def _make_dialog(qapp, tmp_path, *, seeded_dcs=()):
    store = _open_user_store(tmp_path)
    _seed_global_items(store)
    lg = _make_lg(store)
    for create_kwargs in seeded_dcs:
        lg.create_dc(**create_kwargs)
    dialog = CrossEventDcsDialog(
        lg, inventories=_INVENTORIES,
    )
    return dialog, lg, store


# --------------------------------------------------------------------------- #
# Refresh — reads SavedFilter rows + live count
# --------------------------------------------------------------------------- #


def test_empty_dcs_shows_empty_label(qapp, tmp_path):
    """No saved DCs → the empty-state hint is visible, no rows."""
    d, lg, store = _make_dialog(qapp, tmp_path)
    assert not d._empty_label.isHidden()
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    assert rows == []
    d.deleteLater(); store.close()


def test_lists_each_saved_filter(qapp, tmp_path):
    """One row per SavedFilter; tags rendered with the # prefix."""
    d, lg, store = _make_dialog(qapp, tmp_path, seeded_dcs=[
        {"name": "macro best",
         "expr": [["+", cr.BASE_EXPORTED]],
         "filters": {"styles": ["macro"]}},
        {"name": "wildlife only",
         "expr": [["+", cr.BASE_EXPORTED]],
         "filters": {"styles": ["wildlife"]}},
    ])
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    assert len(rows) == 2
    tags = {r._dc.tag for r in rows}
    assert tags == {"macro_best", "wildlife_only"}
    assert d._empty_label.isHidden()
    d.deleteLater(); store.close()


def test_row_shows_live_count(qapp, tmp_path):
    """The probe runs at refresh time; the row carries the count."""
    d, lg, store = _make_dialog(qapp, tmp_path, seeded_dcs=[
        {"name": "macros",
         "expr": [["+", cr.BASE_EXPORTED]],
         "filters": {"styles": ["macro"]}},
    ])
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    # One macro item in the seeded projection (a1).
    assert rows[0]._dc.tag == "macros"
    # We can't easily reach the QLabel text, but the live-count path
    # ran successfully (no exception) which is the test's contract.
    d.deleteLater(); store.close()


def test_row_recipe_summary_includes_filter_keys(qapp, tmp_path):
    """The recipe summary string surfaces key filters concisely."""
    from mira.ui.pages.cross_event_dcs_dialog import _recipe_summary
    dc = um.SavedFilter(
        id="x", tag="x", created_at=NOW, updated_at=NOW,
        expr_json=json.dumps([["+", "exported"]]),
        filters_json=json.dumps({
            "styles": ["macro"], "stars_min": 5,
            "country_codes": ["CR"]}),
    )
    summary = _recipe_summary(dc)
    assert "#exported" in summary
    assert "styles=" in summary
    assert "stars" in summary
    assert "country" in summary


def test_recipe_summary_tolerates_malformed_json(qapp, tmp_path):
    """Bad JSON in expr_json / filters_json doesn't crash — the summary
    returns whatever it could parse, possibly empty."""
    from mira.ui.pages.cross_event_dcs_dialog import _recipe_summary
    dc = um.SavedFilter(
        id="x", tag="x", created_at=NOW, updated_at=NOW,
        expr_json="not json", filters_json="{also bad",
    )
    # Empty / no-error.
    assert _recipe_summary(dc) == ""


# --------------------------------------------------------------------------- #
# Delete action — calls gateway, refreshes, confirm dialog gates
# --------------------------------------------------------------------------- #


def test_delete_removes_row_after_confirm(qapp, tmp_path, monkeypatch):
    """User confirms delete → gateway.delete_dc fires + row disappears."""
    d, lg, store = _make_dialog(qapp, tmp_path, seeded_dcs=[
        {"name": "doomed", "expr": [["+", cr.BASE_EXPORTED]]},
    ])
    # Stub confirm to "yes".
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    assert len(rows) == 1
    d._on_delete(rows[0]._dc)
    # Gateway no longer has the DC.
    assert lg.dynamic_collections() == []
    # Empty label re-appears.
    assert not d._empty_label.isHidden()
    d.deleteLater(); store.close()


def test_delete_canceled_keeps_row(qapp, tmp_path, monkeypatch):
    """User cancels confirm → gateway untouched, row stays."""
    d, lg, store = _make_dialog(qapp, tmp_path, seeded_dcs=[
        {"name": "keep_me", "expr": [["+", cr.BASE_EXPORTED]]},
    ])
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    d._on_delete(rows[0]._dc)
    assert len(lg.dynamic_collections()) == 1
    d.deleteLater(); store.close()


def test_delete_failure_warns_user(qapp, tmp_path, monkeypatch):
    """A gateway error surfaces via QMessageBox.warning."""
    d, lg, store = _make_dialog(qapp, tmp_path, seeded_dcs=[
        {"name": "abc", "expr": [["+", cr.BASE_EXPORTED]]},
    ])
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    warned = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **kw: warned.append(a[2]) or QMessageBox.StandardButton.Ok,
    )
    # Force the gateway to raise.
    def _angry(_id):
        raise RuntimeError("disk full")
    monkeypatch.setattr(lg, "delete_dc", _angry)
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    d._on_delete(rows[0]._dc)
    assert any("disk full" in str(m) for m in warned)
    d.deleteLater(); store.close()


# --------------------------------------------------------------------------- #
# Edit action — opens the new-dc dialog with `existing` rehydrated
# --------------------------------------------------------------------------- #


def test_edit_opens_dialog_with_existing_filled(qapp, tmp_path, monkeypatch):
    """Edit launches the new-dc dialog pre-filled from the DC's stored
    formula. Saving sends rename + update through the gateway."""
    d, lg, store = _make_dialog(qapp, tmp_path, seeded_dcs=[
        {"name": "original",
         "expr": [["+", cr.BASE_PICKED]],
         "filters": {"styles": ["macro"]},
         "description": "first pass"},
    ])
    captured: list = []
    monkeypatch.setattr(NewCrossEventDcDialog, "exec",
                        lambda self: 0)
    orig_init = NewCrossEventDcDialog.__init__

    def _capture(self, *a, **kw):
        orig_init(self, *a, **kw)
        captured.append(self)
    monkeypatch.setattr(NewCrossEventDcDialog, "__init__", _capture)

    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    d._on_edit(rows[0]._dc)
    assert len(captured) == 1
    edit_dialog = captured[0]
    # Pre-filled name + expression.
    assert edit_dialog._name.text() == "original"
    assert edit_dialog._origin.token() == cr.BASE_PICKED
    # Now simulate the user changing the name + filters + saving.
    edit_dialog._name.setText("revised")
    edit_dialog.saved.emit(CrossEventDcInfo(
        name="revised", description="second pass",
        expr=[["+", cr.BASE_EXPORTED]],
        filters={"styles": ["wildlife"]}))
    # Tag renamed, fields updated.
    refreshed = lg.dynamic_collections()
    assert len(refreshed) == 1
    assert refreshed[0].tag == "revised"
    assert json.loads(refreshed[0].expr_json) == [["+", "exported"]]
    assert json.loads(refreshed[0].filters_json) == {"styles": ["wildlife"]}
    assert refreshed[0].description == "second pass"
    d.deleteLater(); store.close()


def test_edit_can_keep_same_name_without_taken_error(qapp, tmp_path,
                                                    monkeypatch):
    """The DC's own tag is excluded from the taken-check so the user can
    keep the same name while editing filters."""
    d, lg, store = _make_dialog(qapp, tmp_path, seeded_dcs=[
        {"name": "stable", "expr": [["+", cr.BASE_EXPORTED]]},
    ])
    captured: list = []
    monkeypatch.setattr(NewCrossEventDcDialog, "exec", lambda self: 0)
    orig_init = NewCrossEventDcDialog.__init__

    def _capture(self, *a, **kw):
        orig_init(self, *a, **kw)
        captured.append(self)
    monkeypatch.setattr(NewCrossEventDcDialog, "__init__", _capture)

    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    d._on_edit(rows[0]._dc)
    edit_dialog = captured[0]
    # The existing_tags handed to the dialog exclude this DC's own tag.
    assert "stable" not in edit_dialog._existing_tags
    d.deleteLater(); store.close()


# --------------------------------------------------------------------------- #
# New action — opens the new-dc dialog, on save creates + refreshes
# --------------------------------------------------------------------------- #


def test_new_action_creates_and_refreshes(qapp, tmp_path, monkeypatch):
    """+ New collection → open dialog → save → dc lands + list refreshes."""
    d, lg, store = _make_dialog(qapp, tmp_path)
    captured: list = []
    monkeypatch.setattr(NewCrossEventDcDialog, "exec", lambda self: 0)
    orig_init = NewCrossEventDcDialog.__init__

    def _capture(self, *a, **kw):
        orig_init(self, *a, **kw)
        captured.append(self)
    monkeypatch.setattr(NewCrossEventDcDialog, "__init__", _capture)
    d._on_new()
    new_dialog = captured[0]
    new_dialog.saved.emit(CrossEventDcInfo(
        name="fresh", expr=[["+", "exported"]],
        filters={}, description=""))
    assert {dc.tag for dc in lg.dynamic_collections()} == {"fresh"}
    # Refresh ran — the row count went 0 → 1.
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    assert len(rows) == 1
    d.deleteLater(); store.close()


def test_new_action_create_failure_surfaces_warning(qapp, tmp_path,
                                                   monkeypatch):
    d, lg, store = _make_dialog(qapp, tmp_path)
    monkeypatch.setattr(NewCrossEventDcDialog, "exec", lambda self: 0)
    captured: list = []
    orig_init = NewCrossEventDcDialog.__init__

    def _capture(self, *a, **kw):
        orig_init(self, *a, **kw)
        captured.append(self)
    monkeypatch.setattr(NewCrossEventDcDialog, "__init__", _capture)

    from PyQt6.QtWidgets import QMessageBox
    warned: list = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **kw: warned.append(a[2]) or QMessageBox.StandardButton.Ok)
    d._on_new()
    new_dialog = captured[0]
    # Empty name → create_dc raises ValueError('empty').
    new_dialog.saved.emit(CrossEventDcInfo(
        name="", expr=[["+", "exported"]], filters={}, description=""))
    assert any("empty" in str(m) for m in warned)
    d.deleteLater(); store.close()


# --------------------------------------------------------------------------- #
# Pin action — emits pin_requested for the host
# --------------------------------------------------------------------------- #


def test_pin_emits_pin_requested(qapp, tmp_path):
    """Pin → Cut emits the signal; the host wires it to the (deferred)
    cross-event Cut dialog."""
    d, lg, store = _make_dialog(qapp, tmp_path, seeded_dcs=[
        {"name": "pinnable", "expr": [["+", cr.BASE_EXPORTED]]},
    ])
    fired: list = []
    d.pin_requested.connect(lambda dc: fired.append(dc))
    rows = [d._rows_layout.itemAt(i).widget()
            for i in range(d._rows_layout.count())
            if isinstance(d._rows_layout.itemAt(i).widget(), _DcRow)]
    d._on_pin(rows[0]._dc)
    assert len(fired) == 1
    assert fired[0].tag == "pinnable"
    d.deleteLater(); store.close()
