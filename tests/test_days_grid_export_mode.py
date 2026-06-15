"""DaysGridPage in ``phase="export"`` mode — the spec/68 §3 reroute.

The Export phase no longer carries its own flat-grid surface; it
reuses the shared Phases → Days Lists → Days Grid spine like Pick and
Edit. These tests pin what the spec/68 §3 amendment specifies:

* Click toggles in place (no drill-in — ``item_activated`` doesn't fire).
* Born-green default: undecided cells render as ``picked``.
* X on a shipped cell calls
  :meth:`EventGateway.delete_exported_file` (the on-disk file
  disappears, the lineage row drops, the freshness flag clears, the
  Cut membership cascades — spec/61 §1.4).
* The Export-green trigger filters out items that are already shipped
  (re-ship is a deliberate un-export → re-export, not a hidden no-op).
"""
from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.picked.status import STATE_PICKED, STATE_SKIPPED
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.pages.days_grid_page import DaysGridPage

FIXED_NOW = "2026-06-15T12:00:00+00:00"
N_PHOTOS = 4


def _now() -> str:
    return FIXED_NOW


def _write_jpeg(path: Path, idx: int) -> None:
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
    """Event with N photos, all Pick-kept (the Export pool)."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-x", name="Export reroute fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i in range(1, N_PHOTOS + 1):
        doc.items.append(m.Item(
            id=f"x{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/x{i}.jpg",
            sha256=f"{i:064d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ))
        doc.phase_states.append(m.PhaseState(
            item_id=f"x{i}", phase="pick", state="picked"))
    return doc


@pytest.fixture
def event_dir(tmp_path):
    for i in range(1, N_PHOTOS + 1):
        _write_jpeg(tmp_path / "Original Media" / f"x{i}.jpg", i)
    return tmp_path


@pytest.fixture
def store_and_gateway(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-x")
    store.save_document(_doc())
    counter = itertools.count(1)
    eg = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield store, eg
    eg.close()


@pytest.fixture
def app_gateway(event_dir, store_and_gateway, monkeypatch, tmp_path):
    """An app-level ``Gateway`` whose ``open_event`` hands back a fresh
    EventGateway anchored at the same tmp event_root."""
    store, _ = store_and_gateway
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
    )
    counter = itertools.count(100)

    def _open_event(_event_id):
        return EventGateway(
            store, event_root=event_dir, now=_now,
            new_id=lambda: f"app-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    yield gw


def _ship_one(eg: EventGateway, event_dir: Path, item_id: str) -> Path:
    """Drop a shipped JPEG + matching lineage row + edit_exported
    flag onto ``item_id``. Returns the on-disk path."""
    ship = event_dir / "Exported Media" / "Dia 1"
    ship.mkdir(parents=True, exist_ok=True)
    dest = ship / f"{item_id}.jpg"
    dest.write_bytes(b"\xff\xd8\xff\xd9")
    eg.record_lineage(m.Lineage(
        export_relpath=f"Exported Media/Dia 1/{item_id}.jpg",
        phase="edit", source_kind="item",
        source_item_id=item_id, recipe_json='{"look": "natural"}',
        exported_at="t"))
    eg.set_edit_exported(item_id, True)
    return dest


# --------------------------------------------------------------------------- #
# open_for_day(phase="export") chrome contract
# --------------------------------------------------------------------------- #


def test_export_mode_chrome_swaps_labels_and_shows_export_button(
        qapp, app_gateway):
    """Pick all / Skip all relabel to the ship verbs, "Start a new
    pass…" hides, the Export-green primary reveals — and the storage
    phase under the hood is ``"edit"`` (spec/66 §1.1)."""
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    assert page._export_mode is True
    assert page._phase == "edit"            # shared decision storage
    assert page._identity_phase == "export"
    assert "Export" in page._pick_all_btn.text()
    assert "Drop" in page._skip_all_btn.text()
    # ``isVisibleTo(parent)`` checks the flag set by setVisible()
    # without needing the page to actually be on-screen.
    assert not page._new_pass_btn.isVisibleTo(page)
    assert page._export_btn.isVisibleTo(page)
    page.close_event()


def test_export_mode_default_state_is_born_green(qapp, app_gateway):
    """Items with no edit-phase ``phase_state`` row read as ``picked``
    in Export mode (the "everything ships unless dropped" rule, spec/71
    Export legend reminder)."""
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    states = {it.item_id: it.state for it in page._items}
    assert all(s == "picked" for s in states.values()), states
    page.close_event()


# --------------------------------------------------------------------------- #
# Click semantics — toggle in place, no item_activated emission
# --------------------------------------------------------------------------- #


def test_export_mode_click_toggles_in_place_no_drill_in(
        qapp, app_gateway):
    """Click on a photo cell in Export mode flips the state without
    emitting ``item_activated`` — the locked grammar carries the
    decision, the host has nothing to route to."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    captured: list[str] = []
    page.item_activated.connect(captured.append)
    # The cells should be born green; clicking flips to skipped.
    target = page._items[0]
    assert target.state == STATE_PICKED
    # ``QApplication.focusWidget`` for _verb_on_focused — focus the
    # target Thumb directly to mimic the click → focus sequence.
    page._thumb_widgets[0].setFocus(Qt.FocusReason.MouseFocusReason)
    page._on_thumb_clicked(target.item_id, page._thumb_widgets[0])
    assert captured == []                   # NO drill-in
    # Persisted to the shared edit-phase phase_state row.
    eg = page._eg
    ps = eg.phase_state(target.item_id, "edit")
    assert ps is not None and ps.state == STATE_SKIPPED
    # The in-cell visual flipped too.
    assert page._items[0].state == STATE_SKIPPED
    page.close_event()


# --------------------------------------------------------------------------- #
# X-on-shipped → un-export (the spec/68 §3 "no separate delete UI" rule)
# --------------------------------------------------------------------------- #


def test_x_on_shipped_cell_unlinks_file_drops_lineage_clears_flag(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Toggling a SHIPPED cell green→red in Export mode also calls
    ``delete_exported_file``: the on-disk JPEG vanishes, the lineage
    row drops, ``edit_exported`` flips back to False. This is the
    delete-export affordance — no separate verb, the locked X grammar
    carries it (spec/68 §3 second bullet)."""
    _, source_eg = store_and_gateway
    shipped_path = _ship_one(source_eg, event_dir, "x2")
    assert shipped_path.is_file()
    assert source_eg.exported_item_ids() == {"x2"}

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # The cell wears the shipped badge because the indicator wiring
    # already feeds the page (Commit A).
    by_id = {it.item_id: it for it in page._items}
    assert by_id["x2"].exported is True

    # Focus the shipped cell, click → toggle green→red. The handler
    # detects it was shipped and calls delete_exported_file.
    idx = next(i for i, it in enumerate(page._items) if it.item_id == "x2")
    page._thumb_widgets[idx].setFocus(Qt.FocusReason.MouseFocusReason)
    page._on_thumb_clicked("x2", page._thumb_widgets[idx])

    # File gone, lineage row gone, edit_exported cleared.
    assert not shipped_path.is_file()
    assert source_eg.exported_item_ids() == set()
    adj = source_eg.adjustment("x2")
    assert adj is not None and adj.edit_exported is False
    # The cell's badge cleared too.
    assert page._items[idx].exported is False
    page.close_event()


def test_x_on_unshipped_cell_does_not_call_delete_exported_file(
        qapp, app_gateway):
    """Toggling an un-shipped cell green→red is a pure phase_state
    write — no spurious ``delete_exported_file`` call for an item
    that has nothing on disk."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # Spy on the gateway helper.
    eg = page._eg
    called: list[str] = []
    real = eg.delete_exported_file
    eg.delete_exported_file = lambda iid: called.append(iid) or real(iid)
    page._thumb_widgets[0].setFocus(Qt.FocusReason.MouseFocusReason)
    page._on_thumb_clicked(page._items[0].item_id, page._thumb_widgets[0])
    assert called == []                     # no shipped state → no call
    page.close_event()


# --------------------------------------------------------------------------- #
# Pick mode is unchanged — the reroute is additive
# --------------------------------------------------------------------------- #


def test_pick_mode_chrome_unchanged(qapp, app_gateway):
    """Pick mode still shows the Pick verbs and emits item_activated on
    photo click — the Export reroute is additive, not a takeover."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="pick")
    assert page._export_mode is False
    assert page._phase == "pick"
    assert page._identity_phase == "pick"
    assert "Pick" in page._pick_all_btn.text()
    assert "Skip" in page._skip_all_btn.text()
    assert page._new_pass_btn.isVisibleTo(page)
    assert not page._export_btn.isVisibleTo(page)
    captured: list[str] = []
    page.item_activated.connect(captured.append)
    page._thumb_widgets[0].setFocus(Qt.FocusReason.MouseFocusReason)
    page._on_thumb_clicked(page._items[0].item_id, page._thumb_widgets[0])
    assert captured == [page._items[0].item_id]    # drill-in fires
    page.close_event()
