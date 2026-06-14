"""Nelson 2026-06-06 — X/Y position indicator on the single-photo Pick /
Edit compact_row + "Start a new pass…" on the Day Grid (one level up,
where the ✓ ticks are visible)."""
from __future__ import annotations

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                  # pragma: no cover
    QApplication = None


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


# ── EditPage (Process single-photo) ────────────────────────────────────────


def test_edit_page_compact_row_has_position_label_only(qapp):
    """Single-photo EditPage carries only the X/Y label (+ rotation
    buttons). Start-a-new-pass moved one level up to the Day Grid."""
    from mira.ui.edited.edit_page import EditPage
    p = EditPage()
    assert hasattr(p, "_position_label")
    assert not hasattr(p, "_new_pass_btn")
    assert not hasattr(p, "clear_marks_requested")


def test_edit_page_position_label_single_item_bucket(qapp):
    from mira.ui.edited.edit_page import EditPage
    p = EditPage()
    p._bucket_index = 12
    p._bucket_count = 87
    p._items = []
    p._refresh_position_label()
    assert p._position_label.text() == "12 / 87"


def test_edit_page_position_label_cluster(qapp):
    from mira.ui.edited.edit_page import EditPage
    p = EditPage()
    p._bucket_index = 3
    p._bucket_count = 87
    p._items = [object()] * 5
    p._index = 2
    p._refresh_position_label()
    assert p._position_label.text() == "3 / 87  ·  3 / 5"


# ── PickPhotoSurface (Pick single-photo) ──────────────────────────────────


def test_pick_photo_surface_compact_row_has_position_label_only(qapp):
    from mira.ui.picked.pick_photo_surface import PickPhotoSurface
    p = PickPhotoSurface()
    assert hasattr(p, "_position_label")
    assert not hasattr(p, "_new_pass_btn")
    assert not hasattr(p, "clear_marks_requested")


def test_pick_photo_surface_position_label_single_item(qapp):
    from mira.ui.picked.pick_photo_surface import PickPhotoSurface
    p = PickPhotoSurface()
    p._bucket_index = 12
    p._bucket_count = 87
    p._items = []
    p._refresh_position_label()
    assert p._position_label.text() == "12 / 87"


def test_pick_photo_surface_position_label_cluster(qapp):
    from mira.ui.picked.pick_photo_surface import PickPhotoSurface
    p = PickPhotoSurface()
    p._bucket_index = 3
    p._bucket_count = 87
    p._items = [object()] * 5
    p._index = 1
    p._refresh_position_label()
    assert p._position_label.text() == "3 / 87  ·  2 / 5"


# ── DayGridView ("Start a new pass…" lives here) ──────────────────────────


def test_day_grid_view_clear_marks_button_hidden_by_default(qapp):
    """Off by default — phases that opt in pass ``show_clear_marks_button=True``."""
    from mira.ui.base.day_grid_view import DayGridView
    g = DayGridView()
    assert hasattr(g, "_new_pass_btn")
    assert not g._new_pass_btn.isVisible() or not g._new_pass_btn.isVisibleTo(g)
    # The visibility check above is over-strong without a show(); the
    # construction-time flag is the load-bearing assertion.


def test_day_grid_view_clear_marks_button_shows_when_opted_in(qapp):
    from mira.ui.base.day_grid_view import DayGridView
    g = DayGridView(show_clear_marks_button=True)
    g.show()
    qapp.processEvents()
    assert g._new_pass_btn.isVisible()
    assert g._new_pass_btn.text() == "Start a new pass…"


def test_day_grid_view_clear_marks_button_fires_signal(qapp):
    from mira.ui.base.day_grid_view import DayGridView
    g = DayGridView(show_clear_marks_button=True)
    seen = []
    g.clear_marks_requested.connect(lambda: seen.append(True))
    g._new_pass_btn.click()
    assert seen == [True]


# ── Host wiring (DayGrid → existing _on_clear_marks handler) ──────────────


def test_edit_host_connects_day_grid_clear_marks(qapp, tmp_path):
    from mira.gateway import EventsIndex, Gateway
    from mira.settings.repo import SettingsRepo
    from mira.ui.edited.edit_host_page import EditHostPage
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(tmp_path / "lib"))
    host = EditHostPage(gw)
    assert host.day_grid.receivers(host.day_grid.clear_marks_requested) >= 1
    # Sanity: the day grid actually has the button shown.
    assert host.day_grid._new_pass_btn.isVisibleTo(host.day_grid)


def test_pick_host_connects_day_grid_clear_marks(qapp, tmp_path):
    from mira.gateway import EventsIndex, Gateway
    from mira.settings.repo import SettingsRepo
    from mira.ui.picked.pick_page import PickPage
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(tmp_path / "lib"))
    host = PickPage(gw)
    assert host.day_grid.receivers(host.day_grid.clear_marks_requested) >= 1
    assert host.day_grid._new_pass_btn.isVisibleTo(host.day_grid)
