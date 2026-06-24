"""spec/126 §A + spec/127 §6 — Camera Clock Correction guard during
background ingest, on the unified handler.

While the event is still in ``_ingesting_event_ids``, the unified
correction menu handler (spec/127, replacing the two old ones) must
show the "still finishing import" notice and NOT open its dialog (the
dialog would read a half-written event.db). Once the flag clears, the
handler runs normally.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMessageBox

from mira.gateway import Gateway
from mira.store import models as m
from mira.ui.shell.main_window import MainWindow


@pytest.fixture
def correction_main_window(qapp, tmp_path, monkeypatch):
    """A ``MainWindow`` against a tmp gateway whose user-data + photos
    base live entirely under ``tmp_path`` so the EventsIndex doesn't
    cross-contaminate from the real user dir."""
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    base = tmp_path / "lib"
    base.mkdir()
    monkeypatch.setattr(
        "mira.paths.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "core.settings.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "mira.gateway.index.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "mira.settings.repo.user_data_dir", lambda: user_data)
    gw = Gateway(
        settings=SettingsRepo(user_data / "settings.json"),
        index=EventsIndex(user_data / "events_index.json"),
        user_store_path=user_data / "mira.db",
    )
    _ = gw.user_store
    gw.settings.update(photos_base_path=str(base))
    w = MainWindow(gateway=gw)
    yield w
    w.deleteLater()


def _make_event_with_camera(gw, event_id, event_root, *, applied_offset=0):
    """Create an event with one camera that already carries an applied
    offset — so the handler's "read cameras" branch has something to
    return when it does open."""
    doc = m.EventDocument(
        event=m.Event(
            uuid=event_id, name="Test", created_at="t", updated_at="t",
            start_date="2026-04-01", end_date="2026-04-01"),
        trip_days=[m.TripDay(
            day_number=1, date="2026-04-01",
            tz_minutes=0, extras_json='{}')],
        cameras=[m.Camera(
            camera_id="C1",
            applied_offset_seconds=applied_offset,
        )],
    )
    event_root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, event_root)
    eg.close()


def _events_root(w):
    return Path(w.gateway.settings.load().photos_base_path)


# ── §A: unified handler's guard fires while ingesting ────────────────────


def test_unified_handler_guards_while_ingesting(
    correction_main_window, monkeypatch,
):
    w = correction_main_window
    event_id = "evt-126-127-guard"
    _make_event_with_camera(
        w.gateway, event_id, _events_root(w) / "guard-event")
    w._current_event_id = event_id
    w._mark_ingest_started(event_id)

    info_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: info_calls.append(("info", a))
        or QMessageBox.StandardButton.Ok,
    )
    # If the guard fails, the handler would construct
    # CameraClockCorrectionDialog and start reading the half-written db.
    # Block construction so a regression surfaces here, not as a Qt hang
    # on a real modal.
    opened: list = []

    def _trip(*_a, **_kw):
        opened.append(True)
        raise RuntimeError("dialog must not open during ingest")
    monkeypatch.setattr(
        "mira.ui.pages.camera_clock_dialog.CameraClockCorrectionDialog",
        _trip, raising=False,
    )

    w._open_camera_clock_correction_for_event()

    assert opened == []
    assert len(info_calls) == 1
    args = info_calls[0][1]
    assert "Still importing" in args[1]
    assert "Try again in a moment" in args[2]


# ── §A: handler opens normally once the flag clears ─────────────────────


def test_unified_handler_opens_when_not_ingesting(
    correction_main_window, monkeypatch,
):
    """The non-ingesting path opens the dialog (regression — guard must
    not fire on a settled event)."""
    w = correction_main_window
    event_id = "evt-126-127-open"
    _make_event_with_camera(
        w.gateway, event_id, _events_root(w) / "open-event",
        applied_offset=3600,                  # +1h pre-committed
    )
    w._current_event_id = event_id
    # No _mark_ingest_started — flag stays cleared.
    assert not w.is_ingesting(event_id)

    constructed: list = []

    class _StubDialog:
        def __init__(self, gateway, eid, parent):
            constructed.append((eid,))
        def exec(self):
            from PyQt6.QtWidgets import QDialog
            return QDialog.DialogCode.Rejected
    monkeypatch.setattr(
        "mira.ui.pages.camera_clock_dialog.CameraClockCorrectionDialog",
        _StubDialog, raising=False,
    )

    info_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: info_calls.append(a)
        or QMessageBox.StandardButton.Ok,
    )

    w._open_camera_clock_correction_for_event()

    # Dialog DID open with the right event id.
    assert constructed == [(event_id,)]
    # Guard did not fire ("Still importing" body absent).
    assert all("Still importing" not in args[1] for args in info_calls)


def test_unified_handler_noop_without_current_event(
    correction_main_window, monkeypatch,
):
    """No current event → handler is a clean no-op (no dialog, no
    notice)."""
    w = correction_main_window
    w._current_event_id = None

    constructed: list = []
    monkeypatch.setattr(
        "mira.ui.pages.camera_clock_dialog.CameraClockCorrectionDialog",
        lambda *a, **kw: constructed.append(True),
        raising=False,
    )
    info_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: info_calls.append(a)
        or QMessageBox.StandardButton.Ok,
    )

    w._open_camera_clock_correction_for_event()

    assert constructed == []
    assert info_calls == []
