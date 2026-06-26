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


def test_open_to_cluster_builds_real_bucket(
        qapp, app_gateway, store_and_gateway, event_dir):
    """open_to_cluster: synthesises a CullCluster (here two members for a
    plausible cluster shape) and hands the page the real cluster bucket
    so the bucket-shaped load path lights up.

    spec/66 §1.1 keepers filter (Nelson 2026-06-21): the Editor's pool is
    "all picked keepers" — cluster members not marked picked in
    pick-phase state get stripped. The test stamps e1+e2 picked first so
    the cluster survives the filter (skipping this step is what the
    pre-2026-06-21 version of the test did, which made it fail silently
    after the spec/66 filter landed)."""
    from mira.picked import CullCluster, CullItem
    from mira.picked.status import CellColor, STATE_PICKED
    _, source_eg = store_and_gateway
    # Pre-stamp the cluster members as picked so the spec/66 keepers
    # filter admits them.
    source_eg.set_items_phase_state(["e1", "e2"], "pick", STATE_PICKED)
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


def test_prev_next_navigation_stamps_visited_on_each_landing(
        qapp, app_gateway, store_and_gateway, event_dir):
    """spec/32 §2.10 — every nav landing stamps the visited tick, not just
    the entry point. Regression for the bug where prev/next-ing through a
    day in Edit only left an eye on the first photo when the user returned
    to the Days Grid. spec/66 §1.1 keepers filter (Nelson 2026-06-21):
    every cluster member must be picked in pick-phase state to survive
    the Editor's keepers filter; pre-stamp them so the cluster opens."""
    from mira.picked import CullCluster, CullItem
    from mira.picked.status import CellColor, STATE_PICKED
    _, source_eg = store_and_gateway
    source_eg.set_items_phase_state(
        [f"e{i}" for i in range(1, N_PHOTOS + 1)], "pick", STATE_PICKED)
    page = EditorPage(app_gateway)
    members = tuple(
        CullItem(
            item_id=f"e{i}",
            path=event_dir / "Original Media" / f"e{i}.jpg",
            kind="photo",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        )
        for i in range(1, N_PHOTOS + 1)
    )
    cluster = CullCluster(
        bucket_key="1|focus_bracket|nav-stamp",
        kind="focus_bracket",
        title="Nav stamp",
        members=members,
        color=CellColor.UNTOUCHED,
        camera="G9",
        detection_source="test",
    )
    assert page.open_to_cluster("evt-e", 1, cluster, entry_idx=0)
    # Entry stamped e1.
    visited = source_eg.items_visited_for_day(1, "edit")
    assert "e1" in visited
    # Walk forward through the rest of the cluster — same path the user
    # takes pressing → or clicking the Next arrow.
    page._viewport.show_index(1)
    page._viewport.show_index(2)
    visited = source_eg.items_visited_for_day(1, "edit")
    assert {"e1", "e2", "e3"} <= visited
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
        page._surface, "_box_transpose",
        lambda: calls["box_rot"].append("transpose"))
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
    assert calls["box_rot"] == ["transpose", "transpose"]
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


def test_unpack_adjustment_defaults_to_original(qapp):
    """spec/59 §3/§9 — the baseline applies on entry: an unedited photo
    starts on the **Original** look (no processing), not Natural
    (Nelson 2026-06-18)."""
    page = EditorPage()
    style, look, cflt, crop, angle, aspect = page._unpack_adjustment(
        None, default_style="general")
    assert style == "general"
    assert look == "original"
    assert cflt is None
    assert crop is None
    assert angle == 0.0
    assert aspect == "Original"


def test_open_to_item_loads_whole_day_items_not_one(
        qapp, app_gateway, event_dir, monkeypatch):
    """Nelson 2026-06-14 eyeball #3 — open_to_item must load the whole
    day's navigable items (chronological) positioned at the clicked
    item, so prev/next walks the entire day. The legacy synthetic
    1-item bucket made nav dead — only the clicked item was in
    ``self._items``."""
    from mira.picked import CullItem
    page = EditorPage(app_gateway)
    # The test event has 3 day-1 items (e1/e2/e3); make
    # _day_navigable_items return all of them, in order, regardless
    # of whether the fixture has buckets materialised — that's a
    # separate model concern.
    day_items = [
        CullItem(
            item_id=f"e{i}",
            path=event_dir / "Original Media" / f"e{i}.jpg",
            kind="photo",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        )
        for i in range(1, 4)
    ]
    monkeypatch.setattr(
        EditorPage, "_day_navigable_items",
        lambda self, day_number: day_items)
    assert page.open_to_item("evt-e", 1, "e2")
    # All three items landed on the surface — not just the clicked one.
    assert len(page._items) == 3
    assert [it.item_id for it in page._items] == ["e1", "e2", "e3"]
    # Positioned at e2 (index 1).
    assert page._index == 1
    assert page._day_index == 2
    assert page._day_total == 3
    assert "2 / 3" in page._counter.text()
    # Pressing → advances to e3; nothing's locked at the start.
    page._on_next()
    assert page._viewport.current_index() == 2
    # Pressing ← from there returns to e2.
    page._on_prev()
    assert page._viewport.current_index() == 1


def test_chrome_widgets_are_no_focus_so_keys_keep_firing(qapp):
    """Nelson 2026-06-14 eyeball (updated 2026-06-21) — ghost_button has
    StrongFocus by default, so clicking nav arrows / Full Screen / Full
    Resolution would steal focus from the viewport and the locked-map
    keys go silent. ``_install_keyboard_focus`` must walk every chrome
    widget and clamp it to NoFocus. Back moved out of the page chrome
    into the shared title bar (Nelson 2026-06-21), so it's no longer
    one of the buttons this test covers."""
    page = EditorPage()
    for btn in (page._prev_btn, page._next_btn,
                page._fullres_btn, page._fullscreen_btn):
        assert btn.focusPolicy() == Qt.FocusPolicy.NoFocus, (
            f"{btn.text()!r} keeps focus on click — keys will stop firing")
    # The viewport KEEPS its StrongFocus so it can host the §4 grammar.
    assert page._viewport.focusPolicy() != Qt.FocusPolicy.NoFocus


def test_bottom_bar_has_prev_and_next_arrows(qapp):
    """Nelson 2026-06-14 — the bottom bar's prev/next nav arrows are
    canonical chrome on every photo surface; without them the page
    has no visible single-photo nav target. The buttons advance the
    viewport's cursor (the same handler ← / → use)."""
    page = EditorPage()
    assert page._prev_btn is not None
    assert page._next_btn is not None
    # Wired to the same _on_prev / _on_next that the arrow keys hit.
    assert page._prev_btn.receivers(page._prev_btn.clicked) >= 1
    assert page._next_btn.receivers(page._next_btn.clicked) >= 1


def test_fullscreen_hides_chrome_and_keeps_viewport(qapp):
    """Nelson 2026-06-14 eyeball #1 (updated 2026-06-21) — F11 must put
    the PHOTO on the full screen, not the app. Toggle hides the top
    band (tools) + the bottom band (workshop reserve + footer nav) and
    zeros the layout margins so the viewport fills the screen; a second
    toggle restores everything. The old ``_toolbar_widget`` was
    consolidated into ``_top_band`` (a #SurfaceBand) by the Nelson
    2026-06-21 surface standardisation pass."""
    page = EditorPage()
    # Show + size so showFullScreen() has a window.
    page.resize(800, 600)
    page.show()
    assert page._top_band.isVisible()
    assert page._tools.isVisible()
    assert page._bottom_widget.isVisible()
    page._toggle_fullscreen()
    assert page._fullscreen
    assert not page._top_band.isVisible()
    assert not page._tools.isVisible()
    assert not page._bottom_widget.isVisible()
    assert page._viewport.isVisible()
    assert page._outer.contentsMargins().left() == 0
    page._toggle_fullscreen()
    assert not page._fullscreen
    assert page._top_band.isVisible()
    assert page._tools.isVisible()
    assert page._bottom_widget.isVisible()
    assert page._outer.contentsMargins().left() == 20
    page.close()


def test_tab_traversal_is_disabled(qapp):
    """spec/63 §4 — Tab is transport (play/pause on clips, inert on
    stills), never a focus walker. The page disables
    focusNextPrevChild so Tab can never reach the viewport's host as a
    focus tick."""
    page = EditorPage()
    assert page.focusNextPrevChild(True) is False
    assert page.focusNextPrevChild(False) is False


def test_image_rotate_button_click_persists_adj_rotation(
        qapp, app_gateway, store_and_gateway):
    """spec/59 + 2026-06-22 restoration: clicking the bottom panel's
    ``Rotate photo ↻`` runs the same path the dropped buttons used —
    ``rotate_image(90)`` fires ``changed('rotation')`` and the editor
    persists ``adj.rotation`` in one shot. Pins the commit seam so
    another silent drop on the chrome side surfaces as a test failure
    instead of a quiet regression."""
    import numpy as np
    _, eg = store_and_gateway
    page = EditorPage(app_gateway)
    assert page.open_to_item("evt-e", 1, "e1")
    # Short-circuit the async edit-prep worker — feed the surface
    # synthetic data, re-enable the tools (the page disables them
    # until the prep callback lands), and pin _cached_path so the
    # photo branch in _on_surface_changed takes the rotation commit
    # path (the prep callback would normally do these in production).
    page._surface.load_image(np.zeros((40, 60, 3), dtype=np.uint8))
    page._surface.set_tools_enabled(True)
    page._cached_path = page._items[0].path
    # Pre-state: no Adjustment row yet → reads as rotation 0.
    pre = eg.adjustment("e1")
    assert (pre is None) or (int(getattr(pre, "rotation", 0) or 0) == 0)
    # Click Rotate photo ↻ — same signal path the restored button uses.
    page._surface._img_rot_cw_btn.click()
    after_cw = eg.adjustment("e1")
    assert after_cw is not None
    assert int(after_cw.rotation) == 90
    # A ccw step from 90 → 0 commits cleanly too — round-trip.
    page._surface._img_rot_ccw_btn.click()
    after_ccw = eg.adjustment("e1")
    assert int(after_ccw.rotation) == 0
    page.close_event()


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
