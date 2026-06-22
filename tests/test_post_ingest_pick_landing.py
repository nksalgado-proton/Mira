"""spec/102 — the post-ingest auto-landing into Pick must NOT self-block.

`_finish_collect_ingest` used to clear the in-progress flag in a
``finally`` that ran AFTER the navigation tail. The Pick gate
(``_on_phase_activated('pick')``) read the still-set flag, threw the
"still importing" dialog, and refused to enter — even though the import
was actually complete by the time we got there. spec/102 clears the flag
right before the navigation tail; the ``finally`` stays as an idempotent
backstop for the early-return branches (crash / no-payload / zero-media
cleanup).

These tests drive ``_finish_collect_ingest`` directly with realistic
payloads, spy on ``_on_phase_activated`` to record ``is_ingesting`` at
the call moment, and confirm:

* successful + ``land_phase="pick"`` → flag is False when landing fires,
  no "still importing" dialog,
* error result → no navigation, flag still False afterward (backstop),
* zero-media cancel → no navigation, flag still False afterward
  (backstop).
"""
from __future__ import annotations

from datetime import date as _date, datetime
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMessageBox

from core.ingest_pipeline import IngestPhotoJob, JobOutcome
from core.ingest_pipeline import IngestResult as PipelineResult
from core.scan_source import ScanDayRow
from mira.gateway import Gateway
from mira.store import models as m
from mira.ui.ingest.ingest_job import IngestJobResult
from mira.ui.shell.main_window import MainWindow


@pytest.fixture
def collect_main_window(qapp, tmp_path, monkeypatch):
    """Mirror of test_collect_ingest_in_progress.collect_main_window —
    a MainWindow against a tmp gateway with isolated user-data dir so
    the EventsIndex/SettingsRepo don't cross-contaminate. Same pattern
    as the sibling test."""
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    base = tmp_path / "lib"
    base.mkdir()
    monkeypatch.setattr("mira.paths.user_data_dir", lambda: user_data)
    monkeypatch.setattr("core.settings.user_data_dir", lambda: user_data)
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


def _events_root(w) -> Path:
    return Path(w.gateway.settings.load().photos_base_path)


def _make_event(gw, event_id: str, event_root: Path) -> None:
    doc = m.EventDocument(
        event=m.Event(
            uuid=event_id, name="Test", created_at="t", updated_at="t",
            start_date="2026-04-01", end_date="2026-04-01"),
        trip_days=[m.TripDay(
            day_number=1, date="2026-04-01",
            tz_minutes=0, extras_json='{}')],
        cameras=[m.Camera(camera_id="C1")],
    )
    event_root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, event_root)
    eg.close()


def _success_payload(landed_abs: Path) -> tuple:
    """A minimal one-item successful ingest payload + the matching
    edited_rows / job — used by the happy-path test."""
    job = IngestPhotoJob(
        source_path=Path("src/IMG_1.JPG"),
        camera_id="C1", is_phone=False,
        day_number=1, day_date=_date(2026, 4, 1),
        day_description="",
        capture_time_raw=datetime(2026, 4, 1, 10, 0),
    )
    payload = PipelineResult(
        photos_copied=1, photos_skipped=0, photos_quarantined=0,
        photos_baked=0, photos_duplicates=0, warnings=[],
        per_job_info={
            job.source_path: JobOutcome(
                destination=landed_abs, sha256="sha-1", byte_size=7,
            ),
        },
    )
    edited_rows = [ScanDayRow(
        date=_date(2026, 4, 1), checked=True, country_code="",
        tz_minutes=0, location="", description="",
    )]
    return job, payload, edited_rows


# ── spec/102 — flag is clear by the time landing navigates ─────


def test_pick_landing_sees_flag_cleared_no_still_importing_dialog(
    collect_main_window, monkeypatch, tmp_path,
):
    """The bug: the Pick gate read ``is_ingesting == True`` because
    the finally that cleared the flag hadn't run yet, so the app
    blocked its own landing with the "Still importing" dialog. After
    spec/102, the flag is cleared just before the navigation tail and
    the gate sees False."""
    w = collect_main_window
    event_id = "evt-102-happy"
    event_root = _events_root(w) / "happy-event"
    _make_event(w.gateway, event_id, event_root)
    w._current_event_id = event_id
    w._mark_ingest_started(event_id)

    # Stage a copied file so _record_collect_in_event_db's row write
    # succeeds (mirrors the partial-cancel pattern in the sibling test).
    landed_rel = (
        "Original Media/00 - Captured/_cameras/"
        "Dia 1 - 2026-04-01/C1/IMG_1.JPG"
    )
    landed_abs = event_root / landed_rel
    landed_abs.parent.mkdir(parents=True, exist_ok=True)
    landed_abs.write_bytes(b"\xff\xd8\xff\xe0FAKEJPG")
    job, payload, edited_rows = _success_payload(landed_abs)

    # Suppress side-tail UI so the test focuses on the gate ordering.
    monkeypatch.setattr(w, "_on_event_created", lambda _eid: None)
    monkeypatch.setattr(w, "_spawn_classify_pass", lambda _eid: None)
    monkeypatch.setattr(
        QMessageBox, "exec",
        lambda self_: QMessageBox.StandardButton.Ok,
    )
    # Record any "Still importing" / other info dialog calls. If the
    # spec/102 fix is in place this list stays empty for the landing
    # path; before the fix, the Pick gate would have populated it.
    info_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: info_calls.append(("info", a))
        or QMessageBox.StandardButton.Ok,
    )

    # Spy on _on_phase_activated: record the flag at the call moment
    # AND record the phase we were asked to land on.
    landings: list = []

    def _spy_phase(phase: str) -> None:
        landings.append((phase, w.is_ingesting(event_id)))

    monkeypatch.setattr(w, "_on_phase_activated", _spy_phase)

    w._finish_collect_ingest(
        result=IngestJobResult(payload=payload, cancelled=False),
        event_id=event_id, event_root=event_root,
        jobs=[job], edited_rows=edited_rows,
        date_to_day_num={_date(2026, 4, 1): 1},
        existing_day_nums={_date(2026, 4, 1): 1},
        calibration_decisions={},
        post_record=None, land_phase="pick",
    )

    # The landing fired exactly once with "pick" — AND the flag was
    # cleared by the time it ran. Without spec/102 the value here
    # would have been True (the spec/102 bug).
    assert landings == [("pick", False)], (
        "spec/102: the flag must be cleared BEFORE the landing tail "
        "so the Pick gate doesn't self-block — got %r" % (landings,))
    # And the finally backstop also leaves it cleared post-call.
    assert not w.is_ingesting(event_id)
    # No "Still importing" dialog (the gate inside the real
    # _on_phase_activated would have produced one; we spied on it, so
    # only an unrelated info dialog would land here — and there's none).
    assert info_calls == [], (
        "spec/102: no auxiliary info dialog should fire on the happy "
        "landing path — got %r" % (info_calls,))


def test_pick_landing_only_fires_when_current_event_matches(
    collect_main_window, monkeypatch, tmp_path,
):
    """If the user navigated away to a different event between the
    queue's finished signal and the UI tail, the landing must NOT
    fire (that guard predates spec/102). spec/102 still cleared the
    flag — both invariants together."""
    w = collect_main_window
    event_id = "evt-102-other-current"
    event_root = _events_root(w) / "other-current-event"
    _make_event(w.gateway, event_id, event_root)
    # The user is now sitting on a different event when the tail fires.
    w._current_event_id = "evt-some-other"
    w._mark_ingest_started(event_id)

    landed_abs = event_root / "Original Media/00 - Captured/_cameras/Dia 1 - 2026-04-01/C1/IMG_1.JPG"
    landed_abs.parent.mkdir(parents=True, exist_ok=True)
    landed_abs.write_bytes(b"\xff\xd8\xff\xe0FAKEJPG")
    job, payload, edited_rows = _success_payload(landed_abs)

    monkeypatch.setattr(w, "_on_event_created", lambda _eid: None)
    monkeypatch.setattr(w, "_spawn_classify_pass", lambda _eid: None)
    monkeypatch.setattr(
        QMessageBox, "exec",
        lambda self_: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: QMessageBox.StandardButton.Ok,
    )
    landings: list = []
    monkeypatch.setattr(
        w, "_on_phase_activated",
        lambda phase: landings.append(phase))

    w._finish_collect_ingest(
        result=IngestJobResult(payload=payload, cancelled=False),
        event_id=event_id, event_root=event_root,
        jobs=[job], edited_rows=edited_rows,
        date_to_day_num={_date(2026, 4, 1): 1},
        existing_day_nums={_date(2026, 4, 1): 1},
        calibration_decisions={},
        post_record=None, land_phase="pick",
    )

    # Current event != ingested event → no landing. The flag still
    # clears (both the spec/102 explicit clear and the finally
    # backstop run).
    assert landings == []
    assert not w.is_ingesting(event_id)


# ── Regression: early-return branches still clear via the finally ──


def test_error_result_does_not_navigate_and_flag_clears(
    collect_main_window, monkeypatch,
):
    """A crash inside the worker (result.error set) is handled by the
    early-return branch — no DB writes, no navigation, but the flag
    MUST still be False afterward (the finally backstop)."""
    w = collect_main_window
    event_id = "evt-102-error"
    event_root = _events_root(w) / "error-event"
    _make_event(w.gateway, event_id, event_root)
    w._current_event_id = event_id
    w._mark_ingest_started(event_id)

    crit_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *a, **kw: crit_calls.append(("crit", a))
        or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(w, "_on_event_created", lambda _eid: None)
    landings: list = []
    monkeypatch.setattr(
        w, "_on_phase_activated",
        lambda phase: landings.append(phase))

    w._finish_collect_ingest(
        result=IngestJobResult(
            payload=None, cancelled=False,
            error=RuntimeError("simulated worker crash")),
        event_id=event_id, event_root=event_root,
        jobs=[], edited_rows=[],
        date_to_day_num={},
        existing_day_nums={},
        calibration_decisions={},
        post_record=None, land_phase="pick",
    )

    assert len(crit_calls) == 1
    assert landings == []                              # no navigation
    assert not w.is_ingesting(event_id), (
        "spec/102 backstop: the finally still clears the flag on the "
        "error early-return path")


def test_zero_media_cancel_does_not_navigate_and_flag_clears(
    collect_main_window, monkeypatch,
):
    """spec/57 §4.3.1 — zero-media cancel = clean no-op (event +
    folder removed). No navigation; the finally backstop leaves the
    flag False."""
    w = collect_main_window
    event_id = "evt-102-zero"
    event_root = _events_root(w) / "zero-event"
    _make_event(w.gateway, event_id, event_root)
    w._current_event_id = event_id
    w._mark_ingest_started(event_id)

    info_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: info_calls.append(("info", a))
        or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(w, "_on_event_created", lambda _eid: None)
    monkeypatch.setattr(w, "_spawn_classify_pass", lambda _eid: None)
    landings: list = []
    monkeypatch.setattr(
        w, "_on_phase_activated",
        lambda phase: landings.append(phase))

    zero_payload = PipelineResult(
        photos_copied=0, photos_skipped=0, photos_quarantined=0,
        photos_baked=0, photos_duplicates=0,
        warnings=[], per_job_info={},
    )
    w._finish_collect_ingest(
        result=IngestJobResult(payload=zero_payload, cancelled=True),
        event_id=event_id, event_root=event_root,
        jobs=[], edited_rows=[],
        date_to_day_num={},
        existing_day_nums={},
        calibration_decisions={},
        post_record=None, land_phase="pick",
    )

    assert landings == []                              # no navigation
    assert not w.is_ingesting(event_id), (
        "spec/102 backstop: the finally still clears the flag on the "
        "zero-media cancel path")
    # The "Import cancelled" info dialog from the zero-media branch
    # is expected — sanity check it fired.
    assert len(info_calls) == 1
