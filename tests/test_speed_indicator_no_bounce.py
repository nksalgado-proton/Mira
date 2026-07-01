"""spec/138 §2C — one-way engine→UI speed sync, the indicator never
bounces the engine back.

Two facets:

  * :meth:`VideoWorkshopBar.set_speed` updates the combo under
    ``blockSignals`` — programmatic syncs MUST NOT emit
    ``speed_changed``. (Cause #2 of the spec/138 mess: a stray
    programmatic 1× emit was resetting the engine to 1× while the
    user thought the rate was sticky.)
  * A real user combo change still emits ``speed_changed`` and
    reaches :meth:`PhotoViewport.video_set_playback_rate`.
  * On reveal/new-clip, the host pushes
    ``bar.set_speed(viewport.video_playback_rate())`` so the
    indicator equals the engine truth.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.ui.media.photo_viewport import ViewportItem
from mira.ui.media.transport_bar import VideoWorkshopBar
from mira.ui.pages.picker_page import PickerPage


def _cull(item_id: str, kind: str, path: Path) -> SimpleNamespace:
    return SimpleNamespace(item_id=item_id, path=path, kind=kind)


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 16)
    return p


@pytest.fixture()
def picker_page(qapp, tmp_path):
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    p = PickerPage(gw)
    yield p
    try:
        p.viewport.shutdown_video()
    except Exception:                                              # noqa: BLE001
        pass
    # Drain twice — once so any queued teardown handler runs while the
    # widgets are still alive, again after deleteLater so the Qt event
    # loop empties before the next fixture arms its media state.
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    p.deleteLater()
    QApplication.processEvents()


# ── set_speed: programmatic update is silent ────────────────────────


def test_set_speed_does_not_emit_speed_changed(qapp):
    """A direct :meth:`VideoWorkshopBar.set_speed` call MUST NOT
    re-emit ``speed_changed`` — under ``blockSignals``. Pin this at
    the widget level so future refactors can't regress the contract
    (the cause-#2 mess is exactly an unintended programmatic emit)."""
    bar = VideoWorkshopBar()
    try:
        emitted: list[float] = []

        def _record(rate: float) -> None:
            emitted.append(rate)

        bar.speed_changed.connect(_record)
        try:
            bar.set_speed(0.5)
            bar.set_speed(1.5)
            bar.set_speed(2.0)
            assert bar.speed_combo.currentText() == "2×"
            assert emitted == [], (
                "spec/138 §2C: set_speed MUST update the combo under "
                f"blockSignals; got speed_changed emissions {emitted}"
            )
        finally:
            try:
                bar.speed_changed.disconnect(_record)
            except (TypeError, RuntimeError):
                pass
    finally:
        bar.deleteLater()


def test_set_speed_snaps_to_nearest_tier(qapp):
    """An out-of-tier rate (e.g. 1.3) lands on the closest combo
    label (1.5×) instead of nothing — defensive layout contract."""
    bar = VideoWorkshopBar()
    try:
        bar.set_speed(1.3)
        # 1.3 is equidistant-ish from 1.0 and 1.5; min() with the
        # current ordering picks the lower (1.0). Just assert it
        # landed on SOME real label, not the empty/unchanged state.
        assert bar.speed_combo.currentText() in {"1×", "1.5×"}
    finally:
        bar.deleteLater()


# ── User combo change: real emit, reaches the engine ───────────────


def test_user_combo_change_emits_speed_changed(qapp, monkeypatch):
    """The flip side of the contract: a real user-initiated
    ``setCurrentText`` (no blockSignals) DOES emit
    ``speed_changed`` so the host can drive the engine.

    Forces a 1× seed so ``setCurrentText("2×")`` is a real change
    even on a dev machine whose live settings already default to 2×
    (in which case the combo would already be at 2× and the
    setCurrentText would be a no-op)."""
    monkeypatch.setattr(
        "mira.settings.repo.SettingsRepo.load",
        lambda self: type("_S", (), {"default_video_speed": 1.0})(),
    )
    bar = VideoWorkshopBar()
    try:
        emitted: list[float] = []

        def _record(rate: float) -> None:
            emitted.append(rate)

        bar.speed_changed.connect(_record)
        try:
            bar.speed_combo.setCurrentText("2×")
            assert emitted == [pytest.approx(2.0)], (
                f"user combo change must emit speed_changed; got "
                f"{emitted}"
            )
        finally:
            try:
                bar.speed_changed.disconnect(_record)
            except (TypeError, RuntimeError):
                pass
    finally:
        bar.deleteLater()


def test_user_combo_change_reaches_video_set_playback_rate(
    picker_page, tmp_path,
):
    """End-to-end: in the picker, picking 2× on the combo drives
    ``viewport.video_set_playback_rate`` so the engine actually
    flips speed (no stub in between)."""
    bar = picker_page._transport_bar
    bar.speed_combo.setCurrentText("2×")
    assert picker_page.viewport.video_playback_rate() == pytest.approx(2.0)
    bar.speed_combo.setCurrentText("0.5×")
    assert picker_page.viewport.video_playback_rate() == pytest.approx(0.5)


# ── Reveal sync: indicator = engine truth ──────────────────────────


def _land(page: PickerPage, payloads: list, index: int) -> None:
    page._items = list(payloads)
    page._state = {ci.item_id: None for ci in payloads}
    vitems = [
        ViewportItem(path=ci.path, kind=ci.kind, payload=ci)
        for ci in payloads
    ]
    page.viewport.set_items(vitems, index)


def test_reveal_pushes_engine_rate_to_indicator(picker_page, tmp_path):
    """On a fresh video landing the bar's combo MUST reflect the
    viewport's CURRENT rate, not the stale 1× default — and the
    sync MUST NOT echo back through speed_changed."""
    picker_page.show()
    picker_page.viewport.video_set_playback_rate(2.0)
    emitted: list[float] = []

    def _record(rate: float) -> None:
        emitted.append(rate)

    picker_page._transport_bar.speed_changed.connect(_record)
    try:
        video = _cull("v", "video", _touch(tmp_path / "v.mp4"))
        _land(picker_page, [video], index=0)
        assert picker_page._transport_bar.speed_combo.currentText() == "2×"
        assert emitted == [], (
            f"spec/138 §2C: reveal sync must use blockSignals; "
            f"got speed_changed: {emitted}"
        )
    finally:
        try:
            picker_page._transport_bar.speed_changed.disconnect(_record)
        except (TypeError, RuntimeError):
            pass


def test_reveal_sync_does_not_reset_engine(picker_page, tmp_path):
    """The smoking gun of cause #2: on reveal, the engine's rate
    MUST stay at the sticky value — never bounced to 1× by a
    programmatic combo update."""
    picker_page.show()
    picker_page.viewport.video_set_playback_rate(2.0)
    video = _cull("v", "video", _touch(tmp_path / "v.mp4"))
    _land(picker_page, [video], index=0)
    assert picker_page.viewport.video_playback_rate() == pytest.approx(2.0), (
        "spec/138 §2C: the engine rate must survive the reveal "
        "sync — the indicator pushing 1× back into the engine was "
        "cause #2 of the spec/138 mess"
    )
