"""spec/137 — transport bar speed dropdown is synced to the engine on reveal.

The engine rate is sticky (``PhotoViewport._video_rate`` carries across
clips), but the transport bar's combo defaults to 1× on (re)appear. The
fix: on reveal / new-video, the host pushes
``bar.set_speed(viewport.video_playback_rate())`` alongside the spec/130
``set_playing`` reveal-resync. ``set_speed`` writes under
``blockSignals`` so the sync never echoes back through ``speed_changed``
into the engine.

Coverage:

  * Picker (``PickerPage`` reveal on video landing) — the primary
    consumer of the shared :class:`VideoWorkshopBar` (spec/130).
  * Editor (``EditorPage`` reveal on ``open_to_item`` for a video) —
    same shared bar, the second surface that consumes the engine
    truth at reveal time.
"""
from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.gateway.event_gateway import EventGateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.media.photo_viewport import ViewportItem
from mira.ui.pages.editor_page import EditorPage
from mira.ui.pages.picker_page import PickerPage


FIXED_NOW = "2026-06-23T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _cull(item_id: str, kind: str, path: Path) -> SimpleNamespace:
    return SimpleNamespace(item_id=item_id, path=path, kind=kind)


def _land(page: PickerPage, payloads: list, index: int) -> None:
    """Drive the Picker's viewport directly without a gateway round-trip."""
    page._items = list(payloads)
    page._state = {ci.item_id: None for ci in payloads}
    vitems = [
        ViewportItem(path=ci.path, kind=ci.kind, payload=ci)
        for ci in payloads
    ]
    page.viewport.set_items(vitems, index)


@pytest.fixture()
def picker_page(qapp, tmp_path):
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    p = PickerPage(gw)
    yield p
    # Drain anything the viewport queued (ExifTool prefetch on a video
    # path that doesn't exist would otherwise error at teardown after
    # the page is gone). Then disarm the video and delete.
    try:
        p.viewport.shutdown_video()
    except Exception:                                              # noqa: BLE001
        pass
    try:
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
    except Exception:                                              # noqa: BLE001
        pass
    p.deleteLater()


def _touch_video_file(path: Path) -> Path:
    """A non-empty placeholder so any async file-stat / probe spawned
    on landing finds the file (its CONTENT need not be valid video —
    the tests pin layout/UI contracts, not playback)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 16)
    return path


# ── Picker: reveal pushes engine rate to the bar ─────────────────────


def _label_from_rate(rate: float) -> str:
    return {0.25: "0.25×", 0.5: "0.5×", 1.0: "1×", 1.5: "1.5×", 2.0: "2×"}[
        float(rate)
    ]


def test_picker_reveal_pushes_engine_rate_to_speed_combo(
    picker_page, tmp_path,
):
    """The user set 2× on a previous clip; ``_video_rate`` carries
    across to the next video. On reveal, the bar's combo must show 2×
    — not the stale 1× default — because the host pushed
    ``viewport.video_playback_rate()`` alongside the spec/130
    reveal-resync."""
    picker_page.show()
    picker_page.viewport.video_set_playback_rate(2.0)
    assert picker_page.viewport.video_playback_rate() == pytest.approx(2.0)

    video = _cull("v1", "video", _touch_video_file(tmp_path / "v1.mp4"))
    _land(picker_page, [video], index=0)
    assert picker_page._transport_bar.isVisible() is True
    assert picker_page._transport_bar.speed_combo.currentText() == "2×", (
        "spec/137: revealing the bar on a new clip must show the "
        "engine's CURRENT rate, not the stale 1× default"
    )


def test_picker_reveal_sync_does_not_re_emit_speed_changed(
    picker_page, tmp_path,
):
    """The set_speed sync writes under blockSignals — the dropdown
    update MUST NOT echo back through ``speed_changed`` and re-drive
    the engine (a tight loop, and visible to anyone wiring an
    external listener)."""
    picker_page.show()
    picker_page.viewport.video_set_playback_rate(2.0)
    emitted: list[float] = []

    def _record(rate: float) -> None:
        emitted.append(rate)

    picker_page._transport_bar.speed_changed.connect(_record)
    try:
        video = _cull("v1", "video", _touch_video_file(tmp_path / "v1.mp4"))
        _land(picker_page, [video], index=0)
        assert emitted == [], (
            f"spec/137: the reveal-time set_speed sync must use "
            f"blockSignals; the bar emitted speed_changed: {emitted}"
        )
    finally:
        try:
            picker_page._transport_bar.speed_changed.disconnect(_record)
        except (TypeError, RuntimeError):
            pass


def test_picker_reveal_with_default_rate_shows_1x(picker_page, tmp_path):
    """A viewport that's never seen ``video_set_playback_rate`` reports
    1.0×; the bar reveal then shows '1×' (and still doesn't emit)."""
    picker_page.show()
    emitted: list[float] = []

    def _record(rate: float) -> None:
        emitted.append(rate)

    picker_page._transport_bar.speed_changed.connect(_record)
    try:
        video = _cull("v1", "video", _touch_video_file(tmp_path / "v1.mp4"))
        _land(picker_page, [video], index=0)
        assert picker_page._transport_bar.speed_combo.currentText() == "1×"
        assert emitted == []
    finally:
        try:
            picker_page._transport_bar.speed_changed.disconnect(_record)
        except (TypeError, RuntimeError):
            pass


def test_picker_reveal_carries_rate_across_video_sweeps(
    picker_page, tmp_path,
):
    """The whole point of the carry-over: after the user picks 2× on
    video A, landing on video B must show 2× — both engine truth
    AND dropdown indicator agree on the next clip."""
    picker_page.show()
    a = _cull("a", "video", _touch_video_file(tmp_path / "a.mp4"))
    b = _cull("b", "video", _touch_video_file(tmp_path / "b.mp4"))
    # Land A → user sets 2× live → land B → bar must show 2×.
    _land(picker_page, [a, b], index=0)
    picker_page._transport_bar.speed_combo.setCurrentText("2×")
    assert picker_page.viewport.video_playback_rate() == pytest.approx(2.0)
    picker_page.viewport.show_index(1)
    assert picker_page.viewport.video_playback_rate() == pytest.approx(2.0)
    assert picker_page._transport_bar.speed_combo.currentText() == "2×"


# ── Editor: same reveal contract on the shared bar ───────────────────


def _doc_with_video() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-v", name="Speed sync fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items.append(m.Item(
        id="v1", kind="video", created_at=FIXED_NOW,
        provenance="captured",
        origin_relpath="Original Media/v1.mp4",
        sha256="v" * 64, byte_size=16,
        materialized_at=FIXED_NOW, materialized_phase="ingest",
        camera_id="G9", day_number=1,
        capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00",
        duration_ms=3000,
    ))
    doc.phase_states.append(m.PhaseState(
        item_id="v1", phase="pick", state="picked"))
    doc.phase_states.append(m.PhaseState(
        item_id="v1", phase="edit", state="picked"))
    return doc


@pytest.fixture
def editor_event_dir(tmp_path):
    p = tmp_path / "Original Media" / "v1.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 16)
    return tmp_path


@pytest.fixture
def editor_gateway(editor_event_dir, monkeypatch):
    store = EventStore.create(
        editor_event_dir / "event.db", event_id="evt-v")
    store.save_document(_doc_with_video())
    counter = itertools.count(1)
    gw = Gateway()

    def _open_event(_event_id):
        return EventGateway(
            store, event_root=editor_event_dir, now=_now,
            new_id=lambda: f"app-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    yield gw


def test_editor_reveal_pushes_engine_rate_to_workshop_bar(
    qapp, editor_gateway,
):
    """spec/137 — the Editor uses the same shared bar (spec/130);
    its open_to_item reveal must push the engine rate too. Build a
    page, pre-arm 2× on the viewport, then land on the video — the
    workshop bar's combo MUST show 2× without any speed_changed
    re-emit (blockSignals contract)."""
    page = EditorPage(editor_gateway)
    try:
        page._viewport.video_set_playback_rate(2.0)
        emitted: list[float] = []
        page._workshop_bar.speed_changed.connect(emitted.append)
        assert page.open_to_item("evt-v", 1, "v1")
        assert page._workshop_bar.speed_combo.currentText() == "2×", (
            "spec/137: the Editor reveal must show the engine's "
            "current rate, mirroring the Picker"
        )
        assert emitted == [], (
            f"spec/137: the Editor set_speed sync must use "
            f"blockSignals; got speed_changed emissions {emitted}"
        )
    finally:
        page.deleteLater()
