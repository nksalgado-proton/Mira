"""spec/84 §3 — Collect OK enqueues the IngestJob and the queue's
``on_finished`` writes ``item`` rows on the UI thread.

Pins the slice-3 contract: OK does NOT block (no modal
``QProgressDialog``), the IngestJob lands on the shared batch queue
with ``job_type=import``, and once the queue's ``finished_result``
fires the committer's gateway writes turn the per-file payload into
``item`` rows. A crashed worker (``error`` set on the result) skips
the DB write and warns; the captured tree is untouched (CLAUDE.md
invariant #7).
"""
from __future__ import annotations

from datetime import date as _date, datetime
from pathlib import Path

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from core.ingest_pipeline import IngestPhotoJob
from core.ingest_pipeline import IngestResult as PipelineResult
from core.ingest_pipeline import JobOutcome
from core.scan_source import ScanDayRow, ScanPhotoRecord, ScanResult
from mira.gateway import Gateway
from mira.store import models as m
from mira.ui.ingest.ingest_job import IngestJobResult
from mira.ui.shell.batch_queue import JOB_TYPE_IMPORT
from mira.ui.shell.main_window import MainWindow


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def collect_main_window(qapp, tmp_path, monkeypatch):
    """``MainWindow`` against a tmp gateway with explicit settings +
    index paths. Prime the user-store first so the legacy import
    retires the empty defaults before tests add real data (otherwise
    the lazy import retires the test's just-written
    events_index.json mid-flow)."""
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


def _make_event_with_one_day(gw, event_id, event_root):
    """Materialise a minimum event the Collect committer can land
    items on. One trip day, one camera — the smallest viable shape."""
    doc = m.EventDocument(
        event=m.Event(
            uuid=event_id, name="Test", created_at="t", updated_at="t",
            start_date="2026-04-01", end_date="2026-04-01"),
        trip_days=[m.TripDay(
            day_number=1, date="2026-04-01",
            description="Lisbon", tz_minutes=0, extras_json='{}')],
        cameras=[m.Camera(camera_id="C1")],
    )
    event_root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, event_root)
    eg.close()


def _make_scan(source_path):
    """A ScanResult with one timestamped photo on day 1."""
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


# --------------------------------------------------------------------------- #
# A sync stub for IngestJob so the test stays deterministic
# --------------------------------------------------------------------------- #


class _SyncIngestJob(QObject):
    """Same shape as :class:`IngestJob` but runs the work callable
    synchronously on the calling thread. Lets the test exercise the
    queue's ``on_finished`` without a real QThread + event-loop dance."""

    progress = pyqtSignal(int, int, str)
    finished_result = pyqtSignal(object)
    finished = pyqtSignal()           # mimic QThread's auto-cleanup signal

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def start(self) -> None:
        try:
            payload = self._work(
                lambda d, t, n: self.progress.emit(d, t, n),
                lambda: self._cancel,
            )
            result = IngestJobResult(
                payload=payload, cancelled=self._cancel)
        except Exception as exc:                              # noqa: BLE001
            result = IngestJobResult(
                payload=None, cancelled=self._cancel,
                error=str(exc),
            )
        self.finished_result.emit(result)
        self.finished.emit()

    def deleteLater(self) -> None:        # part of the QThread contract
        pass


# --------------------------------------------------------------------------- #
# Test: enqueue happens, returns True, no modal progress dialog
# --------------------------------------------------------------------------- #


def test_collect_copy_all_enqueues_ingest_job_and_does_not_block(
    collect_main_window, tmp_path, monkeypatch,
):
    """spec/84 §2 + slice 3 — OK enqueues an IngestJob on the shared
    batch queue with ``job_type=import`` and returns immediately;
    ``run_with_progress`` is NOT called for the ingest copy."""
    w = collect_main_window
    event_id = "evt-84-3-enqueue"
    event_root = Path(
        w.gateway.settings.load().photos_base_path) / "test-event-enqueue"
    _make_event_with_one_day(w.gateway, event_id, event_root)

    source = tmp_path / "src" / "IMG_001.JPG"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\xff\xd8\xff\xe0FAKEJPG")
    scan = _make_scan(source)

    # Swap IngestJob for the sync stub so the work runs on this thread
    # and on_finished fires before _run_collect_copy_all returns. (The
    # test still asserts the return + the enqueue shape; the sync stub
    # only collapses the timing — the queue's on_finished path is the
    # same code that runs in production.)
    monkeypatch.setattr(
        "mira.ui.ingest.ingest_job.IngestJob", _SyncIngestJob)

    # Guard: ``run_with_progress`` MUST NOT be called for ingest in the
    # new flow (its modal QProgressDialog is what spec/84 is removing).
    rwp_called: list = []
    import mira.ui.base.progress as _progress
    monkeypatch.setattr(
        _progress, "run_with_progress",
        lambda *a, **kw: rwp_called.append(("called", a, kw))
        or (True, None),
    )

    # Avoid landing the user on a real page on completion.
    monkeypatch.setattr(w, "_on_event_created", lambda _eid: None)
    monkeypatch.setattr(w, "_spawn_classify_pass", lambda _eid: None)
    # The result dialog uses an instance ``exec`` which conftest's
    # neutraliser doesn't cover.
    monkeypatch.setattr(
        QMessageBox, "exec",
        lambda self: QMessageBox.StandardButton.Ok,
    )

    enqueued: list = []
    real_enqueue = w.batch_queue.enqueue

    def _spy_enqueue(worker, label, on_finished, *, job_type="export"):
        enqueued.append({
            "worker": worker, "label": label,
            "on_finished": on_finished, "job_type": job_type,
        })
        real_enqueue(worker, label, on_finished, job_type=job_type)

    w.batch_queue.enqueue = _spy_enqueue

    ok = w._run_collect_copy_all(
        event_id=event_id, event_root=event_root, scan=scan,
        edited_rows=_edited_rows(), edited_info={"name": "Test"},
        existing_info={"name": "Test"},
        existing_days=[m.TripDay(
            day_number=1, date="2026-04-01",
            tz_minutes=0, extras_json='{}')],
        keep_only_paths=None, calibration_decisions={},
        post_record=None, land_phase=None,
    )

    assert ok is True
    assert rwp_called == []                          # NO modal progress dialog
    assert len(enqueued) == 1
    assert enqueued[0]["job_type"] == JOB_TYPE_IMPORT
    assert "Test" in enqueued[0]["label"] or "test-event" in enqueued[0]["label"]
    # The sync stub fired finished_result, which the queue routed to
    # _on_done → _finish_collect_ingest → _record_collect_in_event_db.
    # The DB write is the "rows land on finish" property — verify it.
    eg = w.gateway.open_event(event_id)
    try:
        items = eg.items()
    finally:
        eg.close()
    assert len(items) == 1
    assert items[0].camera_id == "C1"
    assert items[0].day_number == 1
    # The captured tree was actually written to (charter §3 — verbatim
    # copy into Original Media/).
    assert (event_root / items[0].origin_relpath).is_file()


# --------------------------------------------------------------------------- #
# Test: a crashed worker skips the DB write and surfaces a warn
# --------------------------------------------------------------------------- #


def test_copy_work_threads_should_cancel_through_to_run_ingest(
    collect_main_window, tmp_path, monkeypatch,
):
    """spec/84 §6 — the IngestJob's cancel flag MUST reach
    ``run_ingest``'s ``should_cancel`` parameter so the engine's copy
    loop can bail at the next file. Pin the wiring; without this hook
    Cancel would only be observed at the end of the run."""
    w = collect_main_window
    event_id = "evt-84-6-wire"
    event_root = Path(
        w.gateway.settings.load().photos_base_path) / "wire-event"
    _make_event_with_one_day(w.gateway, event_id, event_root)

    source = tmp_path / "src" / "IMG.JPG"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\xff\xd8\xff\xe0FAKEJPG")
    scan = _make_scan(source)

    captured: dict = {}

    def _spy_run_ingest(jobs, event_root, *, bake_corrections,
                        progress, should_cancel):
        captured["should_cancel"] = should_cancel
        captured["initial_cancel"] = should_cancel()
        # Return an empty-but-valid result so _finish_collect_ingest
        # has a payload to commit on.
        from core.ingest_pipeline import IngestResult as PR
        return PR()

    monkeypatch.setattr(
        "core.ingest_pipeline.run_ingest", _spy_run_ingest)
    monkeypatch.setattr(
        "mira.ui.ingest.ingest_job.IngestJob", _SyncIngestJob)
    monkeypatch.setattr(w, "_on_event_created", lambda _eid: None)
    monkeypatch.setattr(w, "_spawn_classify_pass", lambda _eid: None)
    monkeypatch.setattr(
        QMessageBox, "exec",
        lambda self: QMessageBox.StandardButton.Ok,
    )

    w._run_collect_copy_all(
        event_id=event_id, event_root=event_root, scan=scan,
        edited_rows=_edited_rows(), edited_info={"name": "Test"},
        existing_info={"name": "Test"},
        existing_days=[m.TripDay(
            day_number=1, date="2026-04-01",
            tz_minutes=0, extras_json='{}')],
        keep_only_paths=None, calibration_decisions={},
        post_record=None, land_phase=None,
    )

    assert captured.get("should_cancel") is not None
    # The flag starts False; the IngestJob's _is_cancelled callable
    # threads through to the engine's poll.
    assert captured["initial_cancel"] is False


def test_finish_collect_ingest_skips_db_write_on_worker_crash(
    collect_main_window, tmp_path, monkeypatch,
):
    """spec/84 §6 — a worker crash → log + warn dialog; no DB writes.
    The captured tree is unchanged (CLAUDE.md invariant #7)."""
    w = collect_main_window
    event_id = "evt-84-3-crash"
    event_root = Path(
        w.gateway.settings.load().photos_base_path) / "test-event-crash"
    _make_event_with_one_day(w.gateway, event_id, event_root)

    monkeypatch.setattr(w, "_on_event_created", lambda _eid: None)
    monkeypatch.setattr(w, "_spawn_classify_pass", lambda _eid: None)

    warn_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *a, **kw: warn_calls.append(("critical", a, kw))
        or QMessageBox.StandardButton.Ok,
    )

    job = IngestPhotoJob(
        source_path=Path("ignored"), camera_id="C1", is_phone=False,
        day_number=1, day_date=_date(2026, 4, 1),
        day_description="Lisbon",
        capture_time_raw=datetime(2026, 4, 1, 10, 0),
    )
    crash_result = IngestJobResult(
        payload=None, cancelled=False,
        error="RuntimeError: disk gone",
    )
    w._finish_collect_ingest(
        result=crash_result, event_id=event_id, event_root=event_root,
        jobs=[job], edited_rows=_edited_rows(),
        date_to_day_num={_date(2026, 4, 1): 1},
        existing_day_nums={_date(2026, 4, 1): 1},
        calibration_decisions={},
        post_record=None, land_phase=None,
    )

    # Critical surfaced; NO item rows recorded.
    assert len(warn_calls) == 1
    eg = w.gateway.open_event(event_id)
    try:
        assert eg.items() == []
    finally:
        eg.close()
