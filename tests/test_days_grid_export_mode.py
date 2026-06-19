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


def test_export_two_plus_versions_reshape_into_cluster_compare_orange(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 Slice 5 / Block 1 D2 — when a source item carries 2+
    ``Exported Media/`` lineage rows, the day grid replaces the flat
    cell with a versions cluster cover. Fresh members default
    ``compare``; the cover's state machine paints Compare orange as
    long as any member is undecided."""
    _, eg = store_and_gateway
    # Two third-party returns for the same source item — born compare.
    for rel, ext in (
        ("Exported Media/x1-Lightroom.jpg", "lr"),
        ("Exported Media/x1-Helicon.tif", "hl"),
    ):
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state="compare",
        ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-Lightroom.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "x1-Helicon.tif").write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    by_id = {it.item_id: it for it in page._items}
    cluster_id = "cluster:versions:x1"
    assert cluster_id in by_id, list(by_id)
    cover = by_id[cluster_id]
    assert cover.item_kind == "cluster"
    assert cover.cluster_type == "versions"
    assert cover.cluster_count == 2
    assert cover.state == "compare"
    # The original flat x1 cell is replaced — only the cluster remains.
    assert "x1" not in by_id
    page.close_event()


def test_export_versions_sub_grid_p_writes_intent_picked(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Drilling into a versions cluster surfaces every row; pressing P
    on a member writes ``lineage.intent_state='picked'`` via
    set_lineage_intent. The cover's state machine updates on the
    next refresh."""
    _, eg = store_and_gateway
    for rel in (
        "Exported Media/x1-LRC-a.jpg",
        "Exported Media/x1-LRC-b.jpg",
    ):
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state="compare",
        ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    for name in ("x1-LRC-a.jpg", "x1-LRC-b.jpg"):
        (event_dir / "Exported Media" / name).write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    cover = next(
        it for it in page._items
        if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    page._open_cluster(cover._cull_cluster)
    # In versions sub-grid: every member starts compare.
    assert all(it.state == "compare" for it in page._items)
    # Press P on the first member.
    page._apply_verb_at_index(0, "pick")
    # The intent_state landed on the lineage row.
    rows = {r.export_relpath: r for r in eg.versions_for_item("x1")}
    picked_relpath = page._items[0].item_id
    assert rows[picked_relpath].intent_state == "picked"
    page.close_event()


def test_export_versions_cover_state_machine(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Verify the Block 1 D3 cover state machine derives from member
    intent_states. The helper is exercised on a freshly-built page."""
    _, eg = store_and_gateway
    states = ["picked", "skipped", "compare"]
    for i, s in enumerate(states):
        rel = f"Exported Media/x1-v{i}.jpg"
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state=s,
        ))
        (event_dir / "Exported Media").mkdir(exist_ok=True)
        (event_dir / "Exported Media" / f"x1-v{i}.jpg").write_bytes(
            b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    # Any compare → cover Compare orange.
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    cover = next(
        it for it in page._items if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    assert cover.state == "compare"
    # All picked → cover green.
    for s in eg.versions_for_item("x1"):
        eg.set_lineage_intent(s.export_relpath, "picked")
    page._refresh_from_gateway()
    cover = next(
        it for it in page._items if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    assert cover.state == "picked"
    # All skipped → cover red.
    for s in eg.versions_for_item("x1"):
        eg.set_lineage_intent(s.export_relpath, "skipped")
    page._refresh_from_gateway()
    cover = next(
        it for it in page._items if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    assert cover.state == "skipped"
    # Mixed picked + skipped → cover yellow (mixed).
    rows = eg.versions_for_item("x1")
    eg.set_lineage_intent(rows[0].export_relpath, "picked")
    eg.set_lineage_intent(rows[1].export_relpath, "skipped")
    eg.set_lineage_intent(rows[2].export_relpath, "picked")
    page._refresh_from_gateway()
    cover = next(
        it for it in page._items if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    assert cover.state == "mixed"
    page.close_event()


def test_export_mode_stamps_origin_wordmark_on_single_version_cells(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §2.1 / Block 2 — flat cells with one shipped row get the
    origin wordmark (LRC / Helicon / CO / Mira / ext) so the user sees
    at a glance which editor produced the file. Multi-version items
    become a versions cluster (Slice 5) — at the flat-cell layer
    cell_origin_label returns None for those and no badge is drawn."""
    _, eg = store_and_gateway
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x1-Lightroom-edit.jpg",
        phase="edit", source_kind="item", source_item_id="x1",
        recipe_json=None, exported_at="2026-06-19T08:00:00",
        provenance="third_party"))
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/x2.jpg",
        phase="edit", source_kind="item", source_item_id="x2",
        recipe_json="{}", exported_at="2026-06-19T08:00:00"))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-Lightroom-edit.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "Dia 1").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "Dia 1" / "x2.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    by_id = {it.item_id: it for it in page._items}
    assert by_id["x1"].origin == "LRC"   # filename-inferred from "Lightroom"
    assert by_id["x2"].origin == "Mira"  # provenance default
    # The 0-version keepers carry no badge.
    assert by_id["x3"].origin is None
    assert by_id["x4"].origin is None
    page.close_event()


def test_export_pool_includes_skipped_with_shipped_file_and_flags_it(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §4.2 / Block 7 D1.B & D2.B — the Export grid's pool is
    picked keepers ∪ items with a file in ``Exported Media/``. A photo
    the user skipped in Pick but with a third-party return on disk
    still appears (so they can drop the file or re-Pick), carrying a
    ``skipped_in_pick`` flag for the indicator chip."""
    _, eg = store_and_gateway
    # Item x2 was Pick-picked; flip it to skipped, then drop a ship
    # row to simulate a third-party return for a skipped photo.
    eg.set_phase_state("x2", "pick", STATE_SKIPPED)
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x2-LRC.jpg", phase="edit",
        source_kind="item", source_item_id="x2",
        recipe_json=None, exported_at="2026-06-19T08:00:00",
        provenance="third_party"))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x2-LRC.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    by_id = {it.item_id: it for it in page._items}
    # x2 (Pick-skipped + shipped) is in the pool and flagged.
    assert "x2" in by_id
    assert by_id["x2"].skipped_in_pick is True
    # x2 has a shipped row → 1-version default = green.
    assert by_id["x2"].state == STATE_PICKED
    # The other picked keepers (x1/x3/x4) still appear, not flagged,
    # and default red because they have no versions on disk.
    for iid in ("x1", "x3", "x4"):
        assert by_id[iid].skipped_in_pick is False
        assert by_id[iid].state == STATE_SKIPPED
    page.close_event()


def test_export_mode_default_state_is_red_when_no_versions(
        qapp, app_gateway):
    """spec/89 §1.1 / Block 1 D1.C — Export's flat-cell default is
    version-count-driven: a 0-version cell starts **red** (no intent
    to export), a 1-version cell starts green. The fixture's keepers
    carry no lineage rows yet, so every cell reads as ``skipped``."""
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    states = {it.item_id: it.state for it in page._items}
    assert all(s == "skipped" for s in states.values()), states
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
    # spec/89 §1.1 Block 1 D1.C — the fixture's keepers have 0 versions
    # so they start red; clicking flips to picked (intent-to-render).
    target = page._items[0]
    assert target.state == STATE_SKIPPED
    # ``QApplication.focusWidget`` for _verb_on_focused — focus the
    # target Thumb directly to mimic the click → focus sequence.
    page._thumb_widgets[0].setFocus(Qt.FocusReason.MouseFocusReason)
    page._on_thumb_clicked(target.item_id, page._thumb_widgets[0])
    assert captured == []                   # NO drill-in
    # Persisted to the shared edit-phase phase_state row.
    eg = page._eg
    ps = eg.phase_state(target.item_id, "edit")
    assert ps is not None and ps.state == STATE_PICKED
    # The in-cell visual flipped too.
    assert page._items[0].state == STATE_PICKED
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


# --------------------------------------------------------------------------- #
# Bulk Drop-all in Export mode — the cascade (the correctness bug
# that landed in 51e57b7 and was caught by the user's eyeball).
# --------------------------------------------------------------------------- #


def test_bulk_drop_all_cascades_un_export_for_shipped_items(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """The bulk Drop-all button (``✗ Drop all``) in Export mode must
    delete the on-disk files for every shipped item it touches AND
    drop their lineage rows. The pre-fix bug: it wrote
    ``phase_state = skipped`` and stopped, leaving the JPEGs on disk
    and the corner badge still painting — the surface lied about
    its state."""
    _, source_eg = store_and_gateway
    shipped_x2 = _ship_one(source_eg, event_dir, "x2")
    shipped_x4 = _ship_one(source_eg, event_dir, "x4")
    assert shipped_x2.is_file() and shipped_x4.is_file()
    assert source_eg.exported_item_ids() == {"x2", "x4"}

    # Auto-confirm the destructive prompt for the test.
    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm",
        lambda *args, **kwargs: True)

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_skip_all_clicked()

    # Shipped files are gone, lineage is empty, exported_item_ids
    # returns empty, and the corner badge cleared on every cell.
    assert not shipped_x2.is_file()
    assert not shipped_x4.is_file()
    assert source_eg.exported_item_ids() == set()
    for it in page._items:
        assert it.exported is False
        assert it.state == STATE_SKIPPED
    page.close_event()


def test_bulk_drop_all_confirm_text_names_shipped_count(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """The Drop-all confirm dialog in Export mode names the shipped
    count explicitly so the user knows the on-disk blast before
    confirming. (Without that, the prompt reads identically to a
    Pick-mode bulk Skip — invisible blast.)"""
    _, source_eg = store_and_gateway
    _ship_one(source_eg, event_dir, "x1")
    _ship_one(source_eg, event_dir, "x3")
    captured: list[tuple] = []

    def _capture_confirm(parent, title, body, primary_text=None):
        captured.append((title, body, primary_text))
        return False                        # cancel — keeps the test clean

    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm", _capture_confirm)
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_skip_all_clicked()

    assert len(captured) == 1
    title, body, primary = captured[0]
    assert "Drop all" in title and "4" in title       # 4 items total
    assert "2" in body                                  # 2 are shipped
    assert "Exported Media" in body
    assert "Original Media" in body                     # charter pin
    assert primary == "Drop"
    page.close_event()


def test_bulk_export_all_in_export_mode_uses_ship_verb_in_confirm(
        qapp, app_gateway, monkeypatch):
    """Pick-all in Export mode reads as "Export all" in the
    confirm — the user shouldn't see Pick-mode chrome on the
    Export-mode action."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm",
        lambda parent, title, body, primary_text=None:
            captured.append((title, body, primary_text)) or False)
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_pick_all_clicked()

    assert len(captured) == 1
    title, body, primary = captured[0]
    assert "Export all" in title
    assert "ship" in body.lower()
    assert primary == "Export"
    page.close_event()


# --------------------------------------------------------------------------- #
# Single X-on-shipped — silent, fast, UNDOABLE (spec/63 §4 Ctrl+Z
# + re-press-P quick-recover).
# --------------------------------------------------------------------------- #


def test_ctrl_z_after_x_on_shipped_restores_file_and_lineage(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Ctrl+Z after a silent X-on-shipped puts the on-disk file back,
    re-inserts the lineage row, and re-sets ``edit_exported`` — the
    silent unlink is trivially recoverable."""
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent

    _, source_eg = store_and_gateway
    shipped = _ship_one(source_eg, event_dir, "x2")
    original_bytes = shipped.read_bytes()
    assert source_eg.exported_item_ids() == {"x2"}

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")

    # X on the shipped cell → file vanishes.
    idx = next(i for i, it in enumerate(page._items) if it.item_id == "x2")
    page._thumb_widgets[idx].setFocus(Qt.FocusReason.MouseFocusReason)
    page._apply_verb_at_index(idx, "skip")
    assert not shipped.is_file()
    assert source_eg.exported_item_ids() == set()

    # Ctrl+Z → file + lineage + flag come back.
    ev = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier, "z")
    page.keyPressEvent(ev)

    assert shipped.is_file()
    assert shipped.read_bytes() == original_bytes
    assert source_eg.exported_item_ids() == {"x2"}
    by_id = {it.item_id: it for it in page._items}
    assert by_id["x2"].exported is True
    assert by_id["x2"].state == STATE_PICKED
    page.close_event()


def test_repress_p_after_x_on_shipped_restores_file_and_lineage(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Pressing P (or otherwise re-greening the cell) after X-on-
    shipped IS the undo of that un-export — the user doesn't need to
    know about Ctrl+Z to recover one stray X."""
    _, source_eg = store_and_gateway
    shipped = _ship_one(source_eg, event_dir, "x3")
    original_bytes = shipped.read_bytes()

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    idx = next(i for i, it in enumerate(page._items) if it.item_id == "x3")
    page._thumb_widgets[idx].setFocus(Qt.FocusReason.MouseFocusReason)
    page._apply_verb_at_index(idx, "skip")
    assert not shipped.is_file()

    # Re-press P → re-pick should restore the file.
    page._apply_verb_at_index(idx, "pick")
    assert shipped.is_file()
    assert shipped.read_bytes() == original_bytes
    assert source_eg.exported_item_ids() == {"x3"}
    page.close_event()


def test_ctrl_z_with_empty_stack_is_a_no_op(qapp, app_gateway):
    """A Ctrl+Z with nothing to undo doesn't crash and doesn't mutate
    state — the no-op makes the key safe to press idly."""
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    states_before = [it.state for it in page._items]
    ev = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier, "z")
    page.keyPressEvent(ev)
    states_after = [it.state for it in page._items]
    assert states_before == states_after
    page.close_event()


def test_undo_stack_clears_on_close_event(qapp, app_gateway):
    """The captured JPEG bytes don't outlive the event session —
    closing the gateway drops the undo stack so memory doesn't
    leak across day re-opens."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # The fixture's keepers carry no shipped rows, so cells default red
    # (spec/89 Block 1 D1.C); a "pick" verb flips the first cell green
    # and pushes the undo entry.
    page._apply_verb_at_index(0, "pick")
    assert len(page._undo_stack) == 1
    page.close_event()
    assert page._undo_stack == []


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
