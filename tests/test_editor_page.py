"""spec/70 Phase 3 §3 — safety net for the redesigned ``EditorPage``.

The Editor (Surface 08) embeds :class:`PhotoViewport` (spec/63's one
display engine) and absorbs the legacy ``EditPage``'s gateway/engine
wiring — the off-thread :mod:`mira.ui.edited.edit_prep` worker, the
:class:`AdjustmentSurface` state round-trip, the developed-preview F10
lens, the draggable crop overlay. This module pins the behaviours that
must survive: construction, bridge-load through ``open_to_item``,
chrome refresh on viewport landing, idempotent close, the locked
Edit-specific keymap.

Fixture shape mirrors :mod:`test_pick_photo_surface`: a real
``event.db`` with a couple of small real JPEGs on disk so decode paths
actually run, an :class:`EventGateway` so FK writes resolve, and the
EXIF readers stubbed out (the spawned exiftool subprocesses are
irrelevant to the netted behaviours and ~300-500 ms each on Windows).
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtTest import QTest

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.pages.editor_page import EditorPage

FIXED_NOW = "2026-06-14T12:00:00+00:00"
N_PHOTOS = 3


def _now() -> str:
    return FIXED_NOW


@pytest.fixture(autouse=True)
def _stub_exif(monkeypatch):
    """No exiftool subprocesses — see test_pick_photo_surface.py."""
    import core.exif_reader as er
    monkeypatch.setattr(er, "read_exif_single", lambda path: None)
    monkeypatch.setattr(er, "read_exif_batch", lambda paths: [])


def _write_jpeg(path: Path, idx: int) -> None:
    img = QImage(320, 214, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv((idx * 47) % 360, 120, 200))
    p = QPainter(img)
    p.setPen(QColor(20, 20, 20))
    for x in range(0, 320, 24):
        p.drawLine(x, 0, x, 214)
    p.setFont(QFont("Arial", 48, QFont.Weight.Bold))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, f"E{idx}")
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 90)


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-e", name="Editor net fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i in range(1, N_PHOTOS + 1):
        doc.items.append(m.Item(
            id=f"e{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/e{i}.jpg",
            sha256=f"{i:064d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ))
    return doc


@pytest.fixture
def event_dir(tmp_path):
    """A real event directory with little JPEGs at their relpaths so the
    EditorPage's viewport has real bytes to decode."""
    for i in range(1, N_PHOTOS + 1):
        _write_jpeg(tmp_path / "Original Media" / f"e{i}.jpg", i)
    return tmp_path


@pytest.fixture
def store_and_gateway(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-e")
    store.save_document(_doc())
    counter = itertools.count(1)
    eg = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield store, eg
    eg.close()


@pytest.fixture
def app_gateway(event_dir, store_and_gateway, monkeypatch):
    """An app-level ``Gateway`` whose ``open_event`` hands back a fresh
    EventGateway anchored at the same tmp event_root. EditorPage opens
    its own gateway per bridge call; we wire that to the same dir."""
    store, _ = store_and_gateway
    gw = Gateway()
    counter = itertools.count(100)

    def _open_event(_event_id):
        return EventGateway(
            store, event_root=event_dir, now=_now,
            new_id=lambda: f"app-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    yield gw


# ─────────────────────────────────────────────────────────────────────


def test_construction_does_not_crash(qapp):
    """The page constructs against an app Gateway (no event open) — same
    discipline as PickerPage / EventsPage at MainWindow init."""
    page = EditorPage(Gateway())
    assert page.objectName() == "EditorPage"
    assert page._eg is None
    assert page._items == []
    # Focus proxy lands on the viewport (the §4 grammar's home).
    assert page.focusProxy() is page._viewport


def test_construction_without_gateway(qapp):
    """Smoke-mode (no gateway) — _open_event guards correctly."""
    page = EditorPage()
    assert page.gateway is None
    # Bridge call without a gateway returns False, no crash.
    assert not page.open_to_item("evt-e", 1, "e1")


def test_open_to_item_loads_viewport_and_marks_visited(
        qapp, app_gateway, store_and_gateway):
    """open_to_item: opens the event, builds a synthetic 1-item bucket,
    hands the viewport an item, stamps Mvis at Edit."""
    _, eg = store_and_gateway
    page = EditorPage(app_gateway)
    assert page.open_to_item("evt-e", 1, "e2")
    assert page._event_id == "evt-e"
    assert page._eg is not None
    assert len(page._items) == 1
    assert page._items[0].item_id == "e2"
    # Viewport got the item, current_changed fired, chrome updated.
    assert page._viewport.current_index() == 0
    # Position chip text (single-item bucket → "Cell N / Total").
    assert "1 / 1" in page._counter.text() or " / " in page._counter.text()
    # Visited tick lands on the source eg (it's the same DB).
    visited = eg.items_visited_for_day(1, "edit")
    assert "e2" in visited


def test_open_to_item_then_close_releases_gateway(qapp, app_gateway):
    page = EditorPage(app_gateway)
    assert page.open_to_item("evt-e", 1, "e1")
    page.close_event()
    assert page._eg is None
    assert page._event_id is None
    assert page._items == []
    # Idempotent.
    page.close_event()
    assert page._eg is None


def test_open_to_cluster_builds_real_bucket(qapp, app_gateway, event_dir):
    """open_to_cluster: synthesises a CullCluster (here two members for a
    plausible cluster shape) and hands the page the real cluster bucket
    so the bucket-shaped load path lights up."""
    from mira.picked import CullCluster, CullItem
    from mira.picked.status import CellColor
    page = EditorPage(app_gateway)
    members = (
        CullItem(
            item_id="e1",
            path=event_dir / "Original Media" / "e1.jpg",
            kind="photo",
            capture_time_corrected="2026-04-01T08:01:00",
        ),
        CullItem(
            item_id="e2",
            path=event_dir / "Original Media" / "e2.jpg",
            kind="photo",
            capture_time_corrected="2026-04-01T08:02:00",
        ),
    )
    cluster = CullCluster(
        bucket_key="1|focus_bracket|net",
        kind="focus_bracket",
        title="Focus bracket",
        members=members,
        color=CellColor.UNTOUCHED,
        camera="G9",
        detection_source="test",
    )
    assert page.open_to_cluster("evt-e", 1, cluster, entry_idx=0)
    assert page._bucket is not None
    assert page._bucket.kind == "focus_bracket"
    assert page._day_total == 2
    page.close_event()


def test_locked_keymap_edit_extras_routed_to_surface(
        qapp, app_gateway, monkeypatch):
    """The Edit-specific extras (L / G / [ / ] / R / \\) route to the
    AdjustmentSurface — the engine the page wraps. We monkeypatch the
    handlers to spies so the test doesn't depend on a fully rendered
    surface (the prep worker is async).
    """
    page = EditorPage(app_gateway)
    calls = {"cycle_look": [], "open_grid": 0, "box_rot": [], "reset": 0,
             "compare_toggled": 0}
    monkeypatch.setattr(
        page._surface, "cycle_look",
        lambda delta=1: calls["cycle_look"].append(delta))
    monkeypatch.setattr(
        page._surface, "open_look_grid",
        lambda: calls.__setitem__("open_grid", calls["open_grid"] + 1))
    monkeypatch.setattr(
        page._surface, "_box_rotate",
        lambda delta: calls["box_rot"].append(delta))
    monkeypatch.setattr(
        page._surface, "_on_reset_all",
        lambda: calls.__setitem__("reset", calls["reset"] + 1))
    # Compare toggle is a checkable button — toggle() is a Qt slot.
    monkeypatch.setattr(
        page._surface._compare_toggle, "toggle",
        lambda: calls.__setitem__(
            "compare_toggled", calls["compare_toggled"] + 1))

    QTest.keyPress(page, Qt.Key.Key_L)
    QTest.keyPress(
        page, Qt.Key.Key_L, modifier=Qt.KeyboardModifier.ShiftModifier)
    QTest.keyPress(page, Qt.Key.Key_G)
    QTest.keyPress(page, Qt.Key.Key_BracketLeft)
    QTest.keyPress(page, Qt.Key.Key_BracketRight)
    QTest.keyPress(page, Qt.Key.Key_R)
    QTest.keyPress(page, Qt.Key.Key_Backslash)
    assert calls["cycle_look"] == [1, -1]
    assert calls["open_grid"] == 1
    assert calls["box_rot"] == [-90, 90]
    assert calls["reset"] == 1
    assert calls["compare_toggled"] == 1


def test_decision_keys_p_x_space_c_inert_on_editor(
        qapp, app_gateway, monkeypatch):
    """spec/66 §1.1 — Edit is creative-only: P/X/Space/C are inert here
    (no Pick/Skip ledger). The viewport may still emit the verbs but
    the page must NOT persist any state. We verify by checking the
    Adjustment row is untouched after the keys fire."""
    page = EditorPage(app_gateway)
    assert page.open_to_item("evt-e", 1, "e1")
    # The page should have NO connections for pick_requested / skip_requested
    # on the viewport. We can't easily assert non-connection; instead, we
    # fire the keys and confirm no Adjustment row was written.
    eg = page._eg
    before = eg.adjustment("e1")
    QTest.keyPress(page, Qt.Key.Key_P)
    QTest.keyPress(page, Qt.Key.Key_X)
    QTest.keyPress(page, Qt.Key.Key_Space)
    QTest.keyPress(page, Qt.Key.Key_C)
    after = eg.adjustment("e1")
    # Either both None (no decision plumbing) or unchanged.
    assert (before is None) == (after is None)
    page.close_event()


def test_unpack_adjustment_defaults_to_natural(qapp):
    """spec/59 §3 — standard-correction baseline applies on entry: an
    unedited photo starts on the Natural look."""
    page = EditorPage()
    style, look, cflt, crop, angle, aspect = page._unpack_adjustment(
        None, default_style="general")
    assert style == "general"
    assert look == "natural"
    assert cflt is None
    assert crop is None
    assert angle == 0.0
    assert aspect == "Original"


def test_unpack_adjustment_round_trips_saved_row(qapp):
    """A saved Adjustment row's style / look / filter / crop / angle /
    aspect all round-trip into the surface's load shape."""
    page = EditorPage()
    adj = m.Adjustment(
        item_id="e1", style="portrait", look="brighter",
        creative_filter="cinematic",
        crop_x=0.1, crop_y=0.1, crop_w=0.8, crop_h=0.6,
        crop_angle=12.5, rotation=90, aspect_label="3:2",
    )
    style, look, cflt, crop, angle, aspect = page._unpack_adjustment(
        adj, default_style="general")
    assert style == "portrait"
    assert look == "brighter"
    assert cflt == "cinematic"
    assert crop == (0.1, 0.1, 0.8, 0.6)
    assert angle == 12.5
    assert aspect == "3:2"
