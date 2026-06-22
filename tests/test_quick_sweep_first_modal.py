"""spec/97 — the new-event Quick Sweep modal must expose a visible
Finish/Cancel control. The bug it fixes: the modal had no app title
bar, so the only finish trigger (``lists_page.back_requested`` →
``finalize``) was unreachable from the days list — the user could
only close the window, which silently rejected the dialog and dropped
the import. These tests pin the new contract:

  1. The footer's ``Finish & import…`` button runs ``finalize`` and
     ``_run_quick_sweep_first`` returns the kept set (not ``None``).
  2. The kept set then reaches ``_run_collect_copy_all`` via the
     ``_open_collect_ingest_gate`` seam — the import actually runs
     instead of Collect aborting.
  3. The footer's ``Cancel`` button and the window-close [X]/Esc path
     all route through the discard-confirm; confirming returns ``None``
     (Collect-abort signal), dismissing keeps the modal open.
"""
from __future__ import annotations

from datetime import date as _date, datetime
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QDialog, QMessageBox

from core.scan_source import ScanDayRow, ScanPhotoRecord, ScanResult
from mira.gateway import Gateway
from mira.ui.shell.main_window import MainWindow


# ──────────────────────────── Fixtures ─────────────────────────────────


@pytest.fixture
def qs_modal_window(qapp, tmp_path, monkeypatch):
    """MainWindow against a tmp gateway, primed so the QS ledger
    defaults to ``picked`` — that way Finish commits a non-empty kept
    set without needing per-item Pick clicks in the test."""
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    base = tmp_path / "lib"
    base.mkdir()
    monkeypatch.setattr("mira.paths.user_data_dir", lambda: user_data)
    monkeypatch.setattr("core.settings.user_data_dir", lambda: user_data)
    monkeypatch.setattr("mira.gateway.index.user_data_dir", lambda: user_data)
    monkeypatch.setattr("mira.settings.repo.user_data_dir", lambda: user_data)
    gw = Gateway(
        settings=SettingsRepo(user_data / "settings.json"),
        index=EventsIndex(user_data / "events_index.json"),
        user_store_path=user_data / "mira.db",
    )
    _ = gw.user_store
    gw.settings.update(photos_base_path=str(base))
    gw.settings.update(quick_sweep_default_state="picked")
    w = MainWindow(gateway=gw)
    yield w
    w.deleteLater()


def _make_scan(source_path):
    """A one-photo ScanResult on a single day."""
    raw_t = datetime(2026, 4, 1, 10, 0)
    return ScanResult(
        scan_rows=[],
        candidates_by_date={},
        day_date_lookup={1: _date(2026, 4, 1)},
        day_tz_lookup={1: 0},
        presences=[],
        per_photo_records=[ScanPhotoRecord(
            source_path=source_path, camera_id="C1",
            is_phone=False, day_number=1,
            capture_time_raw=raw_t,
        )],
        total_photos=1,
    )


def _edited_rows():
    return [ScanDayRow(
        date=_date(2026, 4, 1), checked=True, country_code="PT",
        tz_minutes=0, location="Lisbon", description="Lisbon",
    )]


def _bypass_progress(monkeypatch):
    """Skip the prep ``run_with_progress`` dialog so the prep runs
    synchronously without popping a window in tests."""
    import mira.ui.base.progress as _progress
    monkeypatch.setattr(
        _progress, "run_with_progress",
        lambda parent, title, work, **kwargs: (
            True, work(lambda *a, **k: None)),
    )


def _write_source(tmp_path) -> Path:
    src = tmp_path / "src" / "IMG.JPG"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\xff\xd8FAKEJPG")
    return src


# ──────────────────────────── Tests ────────────────────────────────────


def test_finish_button_runs_finalize_and_returns_kept_set(
    qs_modal_window, tmp_path, monkeypatch,
):
    """spec/97 §4 — clicking the modal's ``Finish & import…`` button
    runs ``finalize`` (which pops the import-summary confirm + accepts
    the host). ``_run_quick_sweep_first`` returns the kept set, NOT
    ``None`` — the dead-end the spec fixes."""
    w = qs_modal_window
    _bypass_progress(monkeypatch)
    source = _write_source(tmp_path)
    scan = _make_scan(source)
    rows = _edited_rows()

    # ``finalize`` pops a ``QMessageBox(host)``; click its AcceptRole
    # button so finalize sets ``result['kept']`` and ``host.accept()``
    # fires.
    def _qmb_clicks_accept(self):
        for btn in self.buttons():
            if self.buttonRole(btn) == QMessageBox.ButtonRole.AcceptRole:
                btn.click()
                return 0
        return -1
    monkeypatch.setattr(QMessageBox, "exec", _qmb_clicks_accept)

    # Intercept ``host.exec()`` — click Finish in place of an event
    # loop. The click fires ``finalize`` synchronously (QMessageBox.exec
    # is stubbed above), which calls ``host.accept()``.
    def _qs_exec(self):
        if hasattr(self, "_qs_finish_btn"):
            self._qs_finish_btn.click()
            return self.result()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, "exec", _qs_exec)

    kept = w._run_quick_sweep_first(scan=scan, edited_rows=rows)

    assert kept is not None
    assert isinstance(kept, set)
    assert source in kept


def test_finish_reaches_collect_copy_all_through_the_gate(
    qs_modal_window, tmp_path, monkeypatch,
):
    """spec/97 §4 — end-to-end: when the user picks 'Quick Sweep
    first…' on the ingest-mode gate and clicks Finish in the modal,
    the kept set threads into ``_run_collect_copy_all`` as
    ``keep_only_paths`` — the import actually runs instead of Collect
    aborting."""
    w = qs_modal_window
    _bypass_progress(monkeypatch)
    source = _write_source(tmp_path)
    scan = _make_scan(source)
    rows = _edited_rows()

    # The outer gate ALSO pops a QMessageBox with three buttons. Click
    # the "Quick Sweep first…" button for that one; the inner finalize
    # confirm gets the AcceptRole click.
    qmb_calls = {"n": 0}

    def _qmb_route(self):
        qmb_calls["n"] += 1
        if qmb_calls["n"] == 1:
            for btn in self.buttons():
                if btn.text() == "Quick Sweep first…":
                    btn.click()
                    return 0
        for btn in self.buttons():
            if self.buttonRole(btn) == QMessageBox.ButtonRole.AcceptRole:
                btn.click()
                return 0
        return -1
    monkeypatch.setattr(QMessageBox, "exec", _qmb_route)

    def _qs_exec(self):
        if hasattr(self, "_qs_finish_btn"):
            self._qs_finish_btn.click()
            return self.result()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, "exec", _qs_exec)

    captured: dict = {}

    def _fake_copy(self, **kwargs):
        captured.update(kwargs)
        return True
    monkeypatch.setattr(MainWindow, "_run_collect_copy_all", _fake_copy)

    ok = w._open_collect_ingest_gate(
        event_id="evt-spec97",
        event_name="Spec97",
        event_root=tmp_path / "ev",
        scan=scan,
        edited_rows=rows,
        edited_info={"name": "Spec97"},
        existing_info={"name": "Spec97"},
        existing_days=[],
    )

    assert ok is True
    assert "keep_only_paths" in captured
    assert captured["keep_only_paths"] is not None
    assert source in captured["keep_only_paths"]


def test_cancel_button_runs_discard_confirm_and_returns_none(
    qs_modal_window, tmp_path, monkeypatch,
):
    """spec/97 §4 — clicking the footer's Cancel pops the discard
    confirm; on Discard the modal rejects and
    ``_run_quick_sweep_first`` returns ``None`` (the existing
    Collect-abort signal)."""
    w = qs_modal_window
    _bypass_progress(monkeypatch)
    source = _write_source(tmp_path)
    scan = _make_scan(source)
    rows = _edited_rows()

    confirm_calls: list[tuple[str, str]] = []

    def _confirm_yes(parent, title, message, *, primary_text="Continue"):
        confirm_calls.append((title, primary_text))
        return True

    import mira.ui.design as _design
    monkeypatch.setattr(_design, "confirm", _confirm_yes)

    def _qs_exec(self):
        if hasattr(self, "_qs_cancel_btn"):
            self._qs_cancel_btn.click()
            return self.result()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, "exec", _qs_exec)

    kept = w._run_quick_sweep_first(scan=scan, edited_rows=rows)

    assert kept is None
    assert len(confirm_calls) == 1
    assert "Discard" in confirm_calls[0][0]


def test_window_close_routes_through_discard_confirm(
    qs_modal_window, tmp_path, monkeypatch,
):
    """spec/97 §2 — the window-close [X]/Esc path funnels through the
    same discard confirm as the Cancel button. The dialog's ``reject``
    override is the shared chokepoint, so an accidental close can't
    silently drop the sweep."""
    w = qs_modal_window
    _bypass_progress(monkeypatch)
    source = _write_source(tmp_path)
    scan = _make_scan(source)
    rows = _edited_rows()

    confirm_calls: list[str] = []

    def _confirm_yes(parent, title, message, *, primary_text="Continue"):
        confirm_calls.append(title)
        return True

    import mira.ui.design as _design
    monkeypatch.setattr(_design, "confirm", _confirm_yes)

    def _qs_exec(self):
        if hasattr(self, "_qs_finish_btn"):
            # Simulate the [X] click / Esc — Qt would call host.reject()
            # internally. Our override is the shared chokepoint, so the
            # discard-confirm runs here too.
            self.reject()
            return self.result()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, "exec", _qs_exec)

    kept = w._run_quick_sweep_first(scan=scan, edited_rows=rows)

    assert kept is None
    assert len(confirm_calls) == 1
    assert "Discard" in confirm_calls[0]


def test_cancel_dismiss_keeps_modal_open(
    qs_modal_window, tmp_path, monkeypatch,
):
    """spec/97 §4 — if the user picks 'Cancel' in the discard confirm
    (dismissing it), the QS modal stays open: its ``reject`` is
    short-circuited so the user can keep sweeping. A subsequent Finish
    click still commits."""
    w = qs_modal_window
    _bypass_progress(monkeypatch)
    source = _write_source(tmp_path)
    scan = _make_scan(source)
    rows = _edited_rows()

    confirm_calls: list[str] = []

    def _confirm_no(parent, title, message, *, primary_text="Continue"):
        confirm_calls.append(title)
        return False

    import mira.ui.design as _design
    monkeypatch.setattr(_design, "confirm", _confirm_no)

    # The eventual Finish click pops finalize's QMessageBox; auto-accept
    # it so the test terminates.
    def _qmb_clicks_accept(self):
        for btn in self.buttons():
            if self.buttonRole(btn) == QMessageBox.ButtonRole.AcceptRole:
                btn.click()
                return 0
        return -1
    monkeypatch.setattr(QMessageBox, "exec", _qmb_clicks_accept)

    def _qs_exec(self):
        if hasattr(self, "_qs_cancel_btn"):
            # Cancel click → confirm-no → modal stays open.
            self._qs_cancel_btn.click()
            assert self.result() == 0   # neither Accepted nor Rejected yet
            # The modal is still alive — click Finish to commit so the
            # test terminates.
            self._qs_finish_btn.click()
            return self.result()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, "exec", _qs_exec)

    kept = w._run_quick_sweep_first(scan=scan, edited_rows=rows)

    assert kept is not None         # Finish committed after the dismiss
    assert len(confirm_calls) == 1  # only the dismissed Cancel asked
