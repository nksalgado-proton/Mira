"""CollectPhotoPicker — dedicated single-photo picker filtered by camera
(Nelson 2026-06-09). Two-stage day → grid + preview-pane flow tuned for
2k-photo days: lazy thumbnail loading, video + large-file filtering, rich
plan-info day labels."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
except ImportError:                                          # pragma: no cover
    QApplication = None
    Qt = None

from mira.ui.pages.collect_photo_picker import (
    _MAX_FILE_BYTES,
    _STAGE_DAYS,
    _STAGE_THUMBS,
    CollectPhotoPicker,
)


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


# ── Stage 1 — day list ────────────────────────────────────────────────────


def test_day_list_one_row_per_day(qapp, tmp_path):
    a, b, c = (tmp_path / f"{i}.jpg" for i in range(3))
    for p in (a, b, c):
        p.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={
            date(2026, 4, 1): [a, b],
            date(2026, 4, 2): [c],
        },
    )
    try:
        assert dlg._day_list.count() == 2
        first = dlg._day_list.item(0).data(Qt.ItemDataRole.UserRole)
        second = dlg._day_list.item(1).data(Qt.ItemDataRole.UserRole)
        assert first == date(2026, 4, 1)
        assert second == date(2026, 4, 2)
    finally:
        dlg.deleteLater()


def test_day_list_drops_empty_days(qapp, tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={
            date(2026, 4, 1): [a],
            date(2026, 4, 2): [],
        },
    )
    try:
        assert dlg._day_list.count() == 1
        only = dlg._day_list.item(0).data(Qt.ItemDataRole.UserRole)
        assert only == date(2026, 4, 1)
    finally:
        dlg.deleteLater()


def test_day_list_default_label_when_no_day_labels_provided(qapp, tmp_path):
    """Without day_labels, the fallback "YYYY-MM-DD — N photo(s)" renders."""
    a, b = (tmp_path / f"{i}.jpg" for i in range(2))
    for p in (a, b):
        p.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [a, b]},
    )
    try:
        label = dlg._day_list.item(0).text()
        assert "2026-04-01" in label
        assert "2" in label
    finally:
        dlg.deleteLater()


def test_day_list_uses_provided_day_labels(qapp, tmp_path):
    """When day_labels carries a rich plan-formatted label, the dialog
    renders THAT (the caller's plan info wins)."""
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\x00")
    rich = "Day 3 · 2026-04-01 · Sintra, Portugal\nA walk through the gardens."
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [a]},
        day_labels={date(2026, 4, 1): rich},
    )
    try:
        text = dlg._day_list.item(0).text()
        # Caller's label is preserved as the bulk of the row text. The
        # picker may suffix a count when the label doesn't include one.
        assert "Day 3" in text
        assert "Sintra" in text
        assert "A walk through the gardens" in text
    finally:
        dlg.deleteLater()


# ── Stage 2 — filter videos + large files ─────────────────────────────────


def test_thumb_stage_filters_out_videos(qapp, tmp_path):
    """Videos (by extension) are excluded from the grid — pair-pick is for
    stills."""
    still = tmp_path / "a.jpg"
    video = tmp_path / "b.mp4"
    for p in (still, video):
        p.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [still, video]},
    )
    try:
        dlg._on_day_chosen(dlg._day_list.item(0))
        assert dlg._thumb_list.count() == 1
        only = dlg._thumb_list.item(0).data(Qt.ItemDataRole.UserRole)
        assert only == still
    finally:
        dlg.deleteLater()


def test_thumb_stage_filters_out_large_files(qapp, tmp_path):
    """Files at or above _MAX_FILE_BYTES are skipped — oversized RAWs are
    a thumbnail-decode penalty the user doesn't want at 2k scale."""
    small = tmp_path / "small.jpg"
    big = tmp_path / "big.jpg"
    small.write_bytes(b"\x00")
    big.write_bytes(b"\x00" * (_MAX_FILE_BYTES + 1))
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [small, big]},
    )
    try:
        dlg._on_day_chosen(dlg._day_list.item(0))
        assert dlg._thumb_list.count() == 1
        only = dlg._thumb_list.item(0).data(Qt.ItemDataRole.UserRole)
        assert only == small
    finally:
        dlg.deleteLater()


def test_thumb_header_mentions_skipped_count(qapp, tmp_path):
    still = tmp_path / "a.jpg"
    video = tmp_path / "b.mp4"
    still.write_bytes(b"\x00")
    video.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [still, video]},
    )
    try:
        dlg._on_day_chosen(dlg._day_list.item(0))
        header = dlg._thumb_header.text()
        assert "1" in header                # 1 shown
        assert "skipped" in header.lower()  # mentions skip
    finally:
        dlg.deleteLater()


# ── Stage 2 — lazy thumbnail loader ───────────────────────────────────────


def test_thumb_pending_queue_populated_for_unloaded_paths(qapp, tmp_path):
    """When the grid is populated, every uncached path gets queued for
    lazy decode and the QTimer is started."""
    a, b = (tmp_path / f"{i}.jpg" for i in range(2))
    for p in (a, b):
        p.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [a, b]},
    )
    try:
        dlg._on_day_chosen(dlg._day_list.item(0))
        assert len(dlg._thumb_pending) == 2
        assert dlg._thumb_timer.isActive()
    finally:
        dlg.deleteLater()


def test_back_to_days_stops_thumbnail_decode(qapp, tmp_path):
    """Navigating back from the grid cancels in-flight thumbnail work so
    the user isn't paying for off-screen decodes."""
    a, b = (tmp_path / f"{i}.jpg" for i in range(2))
    for p in (a, b):
        p.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [a, b]},
    )
    try:
        dlg._on_day_chosen(dlg._day_list.item(0))
        assert dlg._thumb_timer.isActive()
        dlg._on_back_to_days()
        assert not dlg._thumb_timer.isActive()
        assert dlg._thumb_pending == []
        assert dlg._stack.currentIndex() == _STAGE_DAYS
    finally:
        dlg.deleteLater()


# ── Stage 2 — preview pane + selection ────────────────────────────────────


def test_use_button_disabled_until_a_thumb_is_highlighted(qapp, tmp_path):
    """The preview pane only enables "Use this photo" once a thumbnail
    is highlighted (and the highlight handler sets a valid path)."""
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [a]},
    )
    try:
        # Before navigating to stage 2, the button is disabled.
        assert dlg._use_btn.isEnabled() is False
        dlg._on_day_chosen(dlg._day_list.item(0))
        # The grid auto-highlights the first row → preview fires → button on.
        assert dlg._use_btn.isEnabled() is True
    finally:
        dlg.deleteLater()


def test_use_button_commits_currently_highlighted_thumb(qapp, tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [a]},
    )
    try:
        dlg._on_day_chosen(dlg._day_list.item(0))
        dlg._on_use_current()
        assert dlg.selected_path == a
        assert dlg.result() == dlg.DialogCode.Accepted
    finally:
        dlg.deleteLater()


def test_double_click_on_thumb_commits_immediately(qapp, tmp_path):
    """Double-click / Enter on a thumbnail is a shortcut for the
    "Use this photo" button."""
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [a]},
    )
    try:
        dlg._on_day_chosen(dlg._day_list.item(0))
        dlg._on_thumb_chosen(dlg._thumb_list.item(0))
        assert dlg.selected_path == a
        assert dlg.result() == dlg.DialogCode.Accepted
    finally:
        dlg.deleteLater()


# ── Empty input ───────────────────────────────────────────────────────────


def test_empty_input_hides_stack_shows_empty_label(qapp):
    dlg = CollectPhotoPicker(camera_id="G9M2", photos_by_day={})
    try:
        assert dlg._stack.isHidden()
        assert dlg.selected_path is None
    finally:
        dlg.deleteLater()


def test_cancel_leaves_selected_path_none(qapp, tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\x00")
    dlg = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [a]},
    )
    try:
        dlg.reject()
        assert dlg.selected_path is None
    finally:
        dlg.deleteLater()


# ── Wait-feedback during preview decode (Nelson 2026-06-18) ──────────────


def test_show_preview_displays_loading_message_before_blocking_decode(
    qapp, tmp_path, monkeypatch,
):
    """``_show_preview`` runs ``load_pixmap`` synchronously on the GUI
    thread; a multi-MB RAW takes 10-20s. Without feedback the user can't
    tell their click registered. The fix: set "Loading {name}…" in the
    preview pane + a wait cursor BEFORE the load. This test confirms the
    label is set when the decode begins."""
    from PyQt6.QtGui import QPixmap
    a = tmp_path / "raw_photo.rw2"
    a.write_bytes(b"\x00")

    captured_text_during_load: list[str] = []

    def fake_load(path, target_size=None):
        # By the time the decode starts, the label should already say
        # "Loading {name}…" — captured here so the test can verify the
        # pre-decode UI update landed.
        dlg_ref = the_dialog
        captured_text_during_load.append(dlg_ref._preview.text())
        return QPixmap()  # null pixmap → triggers the unavailable path

    monkeypatch.setattr(
        "mira.ui.pages.collect_photo_picker.load_pixmap", fake_load,
    )

    the_dialog = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [a]},
    )
    try:
        the_dialog._show_preview(a)
        assert captured_text_during_load, "fake_load was not called"
        assert "raw_photo.rw2" in captured_text_during_load[0]
        # The pre-load message uses the i18n "Loading {name}…" template;
        # the substring "raw_photo.rw2" plus *some* status word (Loading
        # / Carregando — pt-BR is the other locale) is the load-bearing
        # signal. The filename + a non-empty label proves the user got
        # feedback before the 20-second freeze.
        assert captured_text_during_load[0].strip() != ""
    finally:
        the_dialog.deleteLater()


def test_show_preview_discards_stale_result_when_user_clicks_another_thumb(
    qapp, tmp_path, monkeypatch,
):
    """A 20-second RAW decode can be interrupted by the user clicking
    another (faster) thumb. The late-arriving slow decode must NOT enable
    the Use button or render its pixmap — the monotonic request id
    discards stale results. Forward-compatible with a later off-thread
    load; right now decodes are sync but the guard is cheap and correct."""
    from PyQt6.QtGui import QPixmap
    slow_path = tmp_path / "slow.rw2"
    fast_path = tmp_path / "fast.jpg"
    for p in (slow_path, fast_path):
        p.write_bytes(b"\x00")

    def fake_load(path, target_size=None):
        # Simulate the user clicking another thumb while we were
        # "decoding" by bumping the request id mid-call.
        the_dialog._preview_request_id += 1
        return QPixmap()

    monkeypatch.setattr(
        "mira.ui.pages.collect_photo_picker.load_pixmap", fake_load,
    )

    the_dialog = CollectPhotoPicker(
        camera_id="G9M2",
        photos_by_day={date(2026, 4, 1): [slow_path, fast_path]},
    )
    try:
        the_dialog._show_preview(slow_path)
        # The stale-discard branch returns early before the success-side
        # enables the Use button. If the guard misfired, _use_btn would be
        # enabled (the unavailable-preview path still enables it).
        assert not the_dialog._use_btn.isEnabled()
    finally:
        the_dialog.deleteLater()
