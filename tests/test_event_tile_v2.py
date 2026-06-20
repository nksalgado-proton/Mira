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
    TILE_TOTAL_HEIGHT,
    TILE_WIDTH,
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
    """spec/77 §1 — total = title row + 4:3 content area, exact, at
    the fixed 248-px tile width."""
    tile = EventTile(_open_card())
    expected = TITLE_ROW_HEIGHT + int(TILE_WIDTH * 3 / 4)
    assert tile.height() == expected == TILE_TOTAL_HEIGHT
    assert tile.sizeHint().height() == expected
    assert tile.sizeHint().width() == TILE_WIDTH


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
# From / To removed from Event Header dialog (BUGS.md B-012, supersedes
# the spec/77 §5 mandatory-dates floor). The dates live in the DB but
# are derived purely from the trip_days table by
# ``Gateway.recompute_event_date_range`` after every trip_day batch.
# ──────────────────────────────────────────────────────────────────


def test_header_dialog_save_gates_on_name_type_subtype_only(qapp):
    """Save is enabled the moment Name + Type + Subtype are set. From/To
    fields are gone from the UI; the gateway derives them from
    trip_days.dates after every batch write."""
    from mira.ui.pages.event_header_dialog import EventHeaderDialog

    dlg = EventHeaderDialog()
    # Nothing filled → Save off.
    dlg._refresh_save_enabled()
    assert dlg._save_btn.isEnabled() is False

    # Just the name → still off (type + subtype missing).
    dlg._name_edit.setText("Sample event")
    dlg._refresh_save_enabled()
    assert dlg._save_btn.isEnabled() is False

    # Name + type → still off (subtype missing).
    dlg._type_combo.setCurrentIndex(
        max(0, dlg._type_combo.findData("trip"))
    )
    dlg._refresh_save_enabled()
    assert dlg._save_btn.isEnabled() is False

    # Name + type + subtype → Save on. No dates required.
    dlg._subtype_combo.setEditText("Wildlife")
    dlg._refresh_save_enabled()
    assert dlg._save_btn.isEnabled() is True

    # The output dict still carries start_date / end_date keys for
    # callers that read them, but they are always None — the gateway
    # fills them from trip_days after creation.
    info = dlg.header_info()
    assert info["start_date"] is None
    assert info["end_date"] is None

    # And the QDateEdit fields aren't on the dialog at all anymore.
    assert not hasattr(dlg, "_from_edit")
    assert not hasattr(dlg, "_to_edit")


# ──────────────────────────────────────────────────────────────────
# §10.1 — status badge dropped, name has the full header width
# ──────────────────────────────────────────────────────────────────


def test_open_tile_has_no_status_pill(qapp):
    """spec/77 §10.1 — the green Open / pink Closed pill is gone from
    every title row. The body (donuts ↔ photo) speaks the status."""
    open_tile = EventTile(_open_card())
    # Walk descendants — neither the legacy roles (#ChipOpen / #ChipClosed,
    # retired by spec/92 §2.5) nor the current #Chip[tone="open|closed"]
    # may appear: the status pill is gone, whichever role would carry it.
    from PyQt6.QtWidgets import QLabel
    def _is_status_pill(w: QLabel) -> bool:
        name = w.objectName()
        return name in ("ChipOpen", "ChipClosed") or (
            name == "Chip" and w.property("tone") in ("open", "closed")
        )
    for w in open_tile.findChildren(QLabel):
        assert not _is_status_pill(w), (
            "found a status pill that spec/77 §10.1 retired"
        )
    closed = EventCardData(
        event_id="ec", name="Closed", start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 5), is_closed=True, total_days=5,
    )
    closed_tile = EventTile(closed)
    for w in closed_tile.findChildren(QLabel):
        assert not _is_status_pill(w)


# ──────────────────────────────────────────────────────────────────
# §10.5 (revised) — fixed tile, slider removed
# ──────────────────────────────────────────────────────────────────


def test_events_page_has_no_size_slider(qapp, tmp_path):
    """spec/77 §10.5 revised 2026-06-16: the tile is a fixed 248-px
    box. The earlier QSlider experiment was pulled — confirm no slider
    survives on the events page."""
    from PyQt6.QtWidgets import QSlider

    from mira.gateway import EventsIndex, Gateway
    from mira.settings.repo import SettingsRepo
    from mira.ui.pages.events_page import EventsPage

    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index, now=_now)
    gw.set_photos_base_path(str(tmp_path / "lib"))

    page = EventsPage(gw)
    assert page.findChildren(QSlider) == []
    assert not hasattr(page, "_size_slider")


def test_tile_width_is_fixed(qapp):
    """No matter how often EventTile is built, the size never moves."""
    a = EventTile(_open_card())
    b = EventTile(_open_card())
    assert a.size() == b.size()
    assert a.width() == TILE_WIDTH


# ──────────────────────────────────────────────────────────────────
# Duration row (Nelson 2026-06-18) — duration moved off the meta line
# ──────────────────────────────────────────────────────────────────


def test_meta_line_omits_duration_after_split(qapp):
    """Meta line stays type · year · subtype; the trailing ``"Xd"``
    moved to its own row."""
    tile = EventTile(_open_card(total_days=14))
    meta = tile._compose_meta()
    assert "14d" not in meta
    assert "14" not in meta
    # Identity still includes type / year / subtype.
    assert "Trip" in meta
    assert "2026" in meta
    assert "wildlife" in meta


@pytest.mark.parametrize("days,expected", [
    (0, ""),                       # no plan dates → no row
    (1, "1 day"),
    (2, "2 days"),
    (7, "7 days"),
    (13, "13 days"),
    (14, "2 weeks"),
    (21, "3 weeks"),
    (28, "4 weeks"),
    (59, "8 weeks"),
    (60, "2 months"),
    (90, "3 months"),
    (180, "6 months"),
    (365, "12 months"),
    (366, "1 year"),
    (730, "2 years"),
    (1095, "3 years"),
])
def test_compose_duration_humanises_total_days(qapp, days, expected):
    tile = EventTile(_open_card(total_days=days))
    assert tile._compose_duration() == expected
