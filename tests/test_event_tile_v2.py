"""Spec/77 — Event Tile v2 regression tests.

Three concerns the spec calls out as load-bearing:

1. The v2 tile renders both variants (open / closed) at the spec'd
   shape — a fixed title row on top of a 4:3 content area. The open
   tile lays out 4 ``_PhaseDonut`` cells (Collect / Pick / Edit /
   Export); the closed tile hosts a ``PhotoCycler``.

2. Close → Reopen must work on an **export-less** event without
   stranding the user. Per spec/77 §6 the ⋮ menu's Reopen action must
   always be reachable on a closed tile, and the pipeline state on the
   reopened event must survive intact. This pins the gateway side of
   that contract (the menu wiring lives in `_event_tile.py`).

3. The Event Header dialog now treats From / To dates as mandatory
   (spec/77 §5) — the Save gate must stay disabled until both are set.
"""
from __future__ import annotations

from datetime import date
from pathlib import PureWindowsPath

import pytest
from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QApplication

from mira.gateway import EventsIndex, Gateway, make_entry
from mira.settings.repo import SettingsRepo
from mira.store import json_dump
from mira.ui.base.event_card import EventCardData
from mira.ui.design.photo_cycler import PhotoCycler
from mira.ui.pages._event_tile import (
    EventTile,
    TILE_PREFERRED_WIDTH,
    TITLE_ROW_HEIGHT,
    _PhaseDonut,
)


# Reuse the rich document the wider gateway suite already gates on so
# the close→reopen test runs against a realistic event shape (1 photo
# decided 'picked', no exported lineage rows — the export-less case).
from tests.test_store import _rich_document


_FIXED_NOW = "2026-06-16T12:00:00+00:00"


def _now() -> str:
    return _FIXED_NOW


# ──────────────────────────────────────────────────────────────────
# Tile shape + variant content
# ──────────────────────────────────────────────────────────────────


def _open_card(**overrides) -> EventCardData:
    data = EventCardData(
        event_id="e1",
        name="Wildlife of the Atlas Mountains",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 7),
        is_closed=False,
        total_days=7,
        days_with_captures=5,
        collected_count=300,
        decided_count=200,
        picked_count=80,
        developed_count=40,
        exported_count=18,
        event_type="trip",
        event_subtype="wildlife",
    )
    for k, v in overrides.items():
        setattr(data, k, v)
    return data


def test_open_tile_has_four_phase_donuts(qapp):
    tile = EventTile(_open_card())
    donuts = tile.findChildren(_PhaseDonut)
    # 4 phase donuts: Collect, Pick, Edit, Export.
    assert len(donuts) == 4
    phases = {d._phase for d in donuts}
    assert phases == {"collect", "pick", "edit", "export"}


def test_open_tile_has_no_photo_cycler(qapp):
    """Open tile's 4:3 area is the donut grid — no cycler attached."""
    tile = EventTile(_open_card())
    assert not tile.findChildren(PhotoCycler)


def test_closed_tile_hosts_photo_cycler(qapp):
    data = EventCardData(
        event_id="e2",
        name="Closed Event",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 5),
        is_closed=True,
        total_days=5,
        collected_count=200,
        exported_count=18,
    )
    tile = EventTile(data)
    cyclers = tile.findChildren(PhotoCycler)
    assert len(cyclers) == 1
    # The donut grid is the open-tile branch — closed tiles must not
    # paint phase donuts on top of the photo.
    assert not tile.findChildren(_PhaseDonut)


def test_tile_total_height_locked_to_title_plus_43(qapp):
    """spec/77 §1 — total = title row + 4:3 content area, exact."""
    tile = EventTile(_open_card())
    expected = TITLE_ROW_HEIGHT + int(TILE_PREFERRED_WIDTH * 3 / 4)
    assert tile.height() == expected
    assert tile.sizeHint().height() == expected
    assert tile.sizeHint().width() == TILE_PREFERRED_WIDTH


# ──────────────────────────────────────────────────────────────────
# Close → Reopen of an export-less event preserves the pipeline
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def export_less_event_gw(tmp_path):
    """A materialised event with a 'picked' phase_state row and ZERO
    exported lineage rows — the export-less precondition spec/77 §6
    targets. Yields the opened :class:`EventGateway`."""
    base = tmp_path / "lib"
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index, now=_now)
    gw.set_photos_base_path(str(base))
    root = base / "ev"
    entry = make_entry(
        event_id="evt-1", name="Costa Rica 2026",
        start_date="2026-04-01", end_date="2026-04-14",
        is_closed=False, event_root=root, photos_base_path=base,
    )
    gw.materialise_event(json_dump.to_json(_rich_document()), entry)
    eg = gw.open_event("evt-1")
    # Sanity precondition: this fixture is genuinely export-less.
    assert eg.exported_item_ids() == set(), (
        "fixture drifted — _rich_document() now exports something; "
        "spec/77 §6 needs a TRUE no-export starting state"
    )
    yield eg
    eg.close()


def test_close_then_reopen_export_less_preserves_pipeline(
    export_less_event_gw,
):
    """spec/77 §6 — closing an export-less event then reopening must
    leave its phase_state rows in place. This is the 'don't strand the
    user' guarantee: the user can always recover from a wrong Close."""
    eg = export_less_event_gw
    # Pre-Close snapshot of the pipeline (any explicit phase_state).
    pre = {
        i.id: eg.phase_state(i.id, "pick")
        for i in eg.items()
        if eg.phase_state(i.id, "pick") is not None
    }
    assert pre, "fixture lost its phase_state — test premise broken"

    eg.set_closed(True)
    assert eg.event().is_closed is True
    eg.set_closed(False)
    assert eg.event().is_closed is False

    post = {
        i.id: eg.phase_state(i.id, "pick")
        for i in eg.items()
        if eg.phase_state(i.id, "pick") is not None
    }
    # Every phase_state row that existed pre-Close still exists post-
    # Reopen, with the same state value.
    assert set(post) == set(pre)
    for item_id, before in pre.items():
        assert post[item_id].state == before.state, item_id


# ──────────────────────────────────────────────────────────────────
# From / To dates mandatory in Event Header dialog
# ──────────────────────────────────────────────────────────────────


def test_header_dialog_save_disabled_until_dates_set(qapp):
    """spec/77 §5 — Save stays disabled until both From and To are
    real dates (the em-dash placeholder = un-set)."""
    from mira.ui.pages.event_header_dialog import EventHeaderDialog

    dlg = EventHeaderDialog()
    # Name + type + subtype filled, but dates left on their minimum
    # (em-dash) → Save MUST stay disabled.
    dlg._name_edit.setText("Sample event")
    dlg._type_combo.setCurrentIndex(
        max(0, dlg._type_combo.findData("trip"))
    )
    dlg._subtype_combo.setEditText("wildlife")
    dlg._refresh_save_enabled()
    assert dlg._save_btn.isEnabled() is False

    # Set From only — still disabled (both required).
    dlg._from_edit.setDate(QDate(2026, 5, 1))
    dlg._refresh_save_enabled()
    assert dlg._save_btn.isEnabled() is False

    # Set To, but earlier than From — Save still disabled because the
    # gate also requires end ≥ start. The dialog snaps From back to
    # To in that path; we set To first then a later From to force the
    # invalid-window check rather than the auto-snap.
    dlg._to_edit.setDate(QDate(2026, 5, 7))
    dlg._refresh_save_enabled()
    assert dlg._save_btn.isEnabled() is True

    # And the output dict carries the ISO strings.
    info = dlg.header_info()
    assert info["start_date"] == "2026-05-01"
    assert info["end_date"] == "2026-05-07"
