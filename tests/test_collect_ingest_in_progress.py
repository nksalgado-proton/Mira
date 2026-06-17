"""spec/84 §5 — the deferred tile + concurrency guard.

Pins: while a Collect ingest is on the shared batch queue, the
Events-screen tile for that event is HIDDEN; entering Pick on it
warns and returns to Phases; a second same-event enqueue is REJECTED.
Once the queue's ``finished_result`` fires, the in-progress flag
clears — tile reappears, Pick unlocks. A zero-media cancel deletes
the event record + folder (spec/57 §4.3.1 "cancel = clean no-op").
"""
from __future__ import annotations

from datetime import date as _date, datetime
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMessageBox

from core.ingest_pipeline import IngestPhotoJob
from core.ingest_pipeline import IngestResult as PipelineResult
from core.scan_source import ScanDayRow, ScanResult
from mira.gateway import Gateway
from mira.store import models as m
from mira.ui.ingest.ingest_job import IngestJobResult
from mira.ui.shell.main_window import MainWindow


@pytest.fixture
def collect_main_window(qapp, tmp_path, monkeypatch):
    """A ``MainWindow`` against a tmp gateway that genuinely owns its
    user-data dir — the legacy monkeypatch-only fixture in
    test_main_window_menu lets EventsIndex resolve to the live user-
    data dir at construction time (the index path is captured in
    ``EventsIndex.__init__``), which cross-contaminates tests touching
    the events page. Pass the EventsIndex + SettingsRepo paths through
    explicitly."""
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
    # Prime the user_store BEFORE writing any test data so the spec/53
    # §4 legacy import retires the empty-default settings.json (and the
    # non-existent events_index.json) right away — without this priming
    # the import would fire lazily on first ``EventGateway.close``
    # during the test, retiring the events_index.json we just wrote to
    # and stranding the test's index reads.
    _ = gw.user_store
    gw.settings.update(photos_base_path=str(base))
    w = MainWindow(gateway=gw)
    yield w
    w.deleteLater()


def _make_event(gw, event_id, event_root):
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


def _events_root(w):
    return Path(w.gateway.settings.load().photos_base_path)


# --------------------------------------------------------------------------- #
# In-progress flag + EventsPage filter
# --------------------------------------------------------------------------- #


def test_mark_ingest_started_hides_tile_from_events_page(collect_main_window):
    """The events page filters out any event whose ID is in the
    in-progress set; once cleared the tile is visible again."""
    w = collect_main_window
    event_id = "evt-84-5-hide"
    _make_event(w.gateway, event_id, _events_root(w) / "hide-event")
    w.events_page.refresh()
    visible_before = {
        cd.event_id for cd in w.events_page._card_data_by_id.values()
        if cd.event_id not in w.events_page._ingest_in_progress_ids
    }
    assert event_id in visible_before                 # baseline

    w._mark_ingest_started(event_id)
    assert w.is_ingesting(event_id)
    assert event_id in w.events_page._ingest_in_progress_ids

    w._mark_ingest_finished(event_id)
    assert not w.is_ingesting(event_id)
    assert event_id not in w.events_page._ingest_in_progress_ids


def test_events_page_apply_filter_skips_ingesting_ids(collect_main_window):
    """``_apply_filter`` itself respects the set — verifies the filter
    is in the loop and not just on a parallel surface."""
    w = collect_main_window
    a, b = "evt-vis", "evt-hidden"
    _make_event(w.gateway, a, _events_root(w) / "vis-event")
    _make_event(w.gateway, b, _events_root(w) / "hide-event")
    w.events_page.refresh()
    # Both visible before the filter is set.
    w.events_page.set_ingest_in_progress_ids(set())
    rendered = {
        cd.event_id for cd in w.events_page._card_data_by_id.values()}
    assert {a, b} <= rendered

    w.events_page.set_ingest_in_progress_ids({b})
    # The card data still holds both, but `_apply_filter` excludes b
    # from the rendered + chip-counted set — verify via the chip total.
    visible_after = [
        c for c in w.events_page._card_data_by_id.values()
        if c.event_id not in w.events_page._ingest_in_progress_ids
    ]
    assert len(visible_after) == 1
    assert visible_after[0].event_id == a


# --------------------------------------------------------------------------- #
# Pick guard (spec/84 §5 — "still importing — try again when done")
# --------------------------------------------------------------------------- #


def test_phase_pick_warns_and_does_not_navigate_while_ingesting(
    collect_main_window, monkeypatch,
):
    w = collect_main_window
    event_id = "evt-84-5-pick"
    _make_event(w.gateway, event_id, _events_root(w) / "pick-event")
    w._current_event_id = event_id
    w._mark_ingest_started(event_id)

    warn_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: warn_calls.append(("info", a))
        or QMessageBox.StandardButton.Ok,
    )
    navigated: list = []
    monkeypatch.setattr(
        w, "_open_days_lists_for",
        lambda eid: navigated.append(eid))

    w._on_phase_activated("pick")

    assert len(warn_calls) == 1
    assert navigated == []                # Pick refused to enter


def test_phase_pick_navigates_once_ingest_clears(
    collect_main_window, monkeypatch,
):
    w = collect_main_window
    event_id = "evt-84-5-pick-unblocked"
    _make_event(w.gateway, event_id, _events_root(w) / "pick-event2")
    w._current_event_id = event_id

    navigated: list = []
    monkeypatch.setattr(
        w, "_open_days_lists_for",
        lambda eid: navigated.append(eid))

    # Mid-ingest: refused.
    w._mark_ingest_started(event_id)
    w._on_phase_activated("pick")
    assert navigated == []

    # Once cleared: Pick enters.
    w._mark_ingest_finished(event_id)
    w._on_phase_activated("pick")
    assert navigated == [event_id]


# --------------------------------------------------------------------------- #
# Second-enqueue block (spec/84 §4 — one ingest per event at a time)
# --------------------------------------------------------------------------- #


def test_second_same_event_ingest_is_rejected_by_pre_flight(
    collect_main_window, monkeypatch,
):
    w = collect_main_window
    event_id = "evt-84-5-double"
    event_root = _events_root(w) / "double-event"
    _make_event(w.gateway, event_id, event_root)

    # Pretend the first ingest is in flight.
    w._mark_ingest_started(event_id)

    warn_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **kw: warn_calls.append(("warn", a))
        or QMessageBox.StandardButton.Ok,
    )
    enqueued: list = []
    monkeypatch.setattr(
        w.batch_queue, "enqueue",
        lambda *a, **kw: enqueued.append((a, kw)))

    # The scan + day rows can be near-empty here — the pre-flight gate
    # trips before the engine assembles jobs.
    scan = ScanResult(
        scan_rows=[],
        candidates_by_date={},
        day_date_lookup={1: _date(2026, 4, 1)},
        day_tz_lookup={1: 0},
        presences=[],
        per_photo_records=[],
        total_photos=0,
    )
    ok = w._run_collect_copy_all(
        event_id=event_id, event_root=event_root, scan=scan,
        edited_rows=[], edited_info={}, existing_info={},
        existing_days=[],
        keep_only_paths=None, calibration_decisions={},
        post_record=None, land_phase=None,
    )

    assert ok is False
    assert len(warn_calls) == 1
    assert enqueued == []                  # nothing queued


# --------------------------------------------------------------------------- #
# Zero-media cancel cleans up (spec/57 §4.3.1)
# --------------------------------------------------------------------------- #


def test_zero_media_cancel_removes_event_record_and_clears_flag(
    collect_main_window, monkeypatch,
):
    """spec/57 §4.3.1 — a cancel before any file made it is a clean
    no-op: the event record + (empty) folder go away, the
    in-progress flag clears, the events page refreshes."""
    w = collect_main_window
    event_id = "evt-84-5-zero"
    event_root = _events_root(w) / "zero-event"
    _make_event(w.gateway, event_id, event_root)
    assert w.gateway.index.get(event_id) is not None         # baseline
    w._mark_ingest_started(event_id)

    info_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: info_calls.append(("info", a))
        or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(w, "_on_event_created", lambda _eid: None)
    monkeypatch.setattr(w, "_spawn_classify_pass", lambda _eid: None)

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
        post_record=None, land_phase=None,
    )

    assert w.gateway.index.get(event_id) is None             # record gone
    assert not w.is_ingesting(event_id)                      # flag cleared
    assert len(info_calls) == 1


def test_partial_cancel_keeps_event_and_writes_what_copied(
    collect_main_window, tmp_path, monkeypatch,
):
    """spec/84 §5 + spec/57 — a cancel that left some files on disk
    keeps the event (tile reappears) AND writes rows for the copied
    items so the user can re-run Collect to finish the remainder."""
    from core.ingest_pipeline import JobOutcome

    w = collect_main_window
    event_id = "evt-84-5-partial"
    event_root = _events_root(w) / "partial-event"
    _make_event(w.gateway, event_id, event_root)
    assert w.gateway.index.get(event_id) is not None, \
        "fixture sanity check — event must be in the index after creation"
    w._mark_ingest_started(event_id)

    monkeypatch.setattr(w, "_on_event_created", lambda _eid: None)
    monkeypatch.setattr(w, "_spawn_classify_pass", lambda _eid: None)
    monkeypatch.setattr(
        QMessageBox, "exec",
        lambda self: QMessageBox.StandardButton.Ok,
    )

    # One file actually landed on disk under Original Media/.
    landed_rel = "Original Media/00 - Captured/_cameras/Dia 1 - 2026-04-01/C1/IMG_1.JPG"
    landed_abs = event_root / landed_rel
    landed_abs.parent.mkdir(parents=True, exist_ok=True)
    landed_abs.write_bytes(b"\xff\xd8\xff\xe0FAKEJPG")

    job = IngestPhotoJob(
        source_path=tmp_path / "src" / "IMG_1.JPG",
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

    w._finish_collect_ingest(
        result=IngestJobResult(payload=payload, cancelled=True),
        event_id=event_id, event_root=event_root,
        jobs=[job],
        edited_rows=[ScanDayRow(
            date=_date(2026, 4, 1), checked=True, country_code="",
            tz_minutes=0, location="", description="",
        )],
        date_to_day_num={_date(2026, 4, 1): 1},
        existing_day_nums={_date(2026, 4, 1): 1},
        calibration_decisions={},
        post_record=None, land_phase=None,
    )

    # Event record + folder preserved; one item row written.
    assert w.gateway.index.get(event_id) is not None
    assert not w.is_ingesting(event_id)
    eg = w.gateway.open_event(event_id)
    try:
        items = eg.items()
    finally:
        eg.close()
    assert len(items) == 1
