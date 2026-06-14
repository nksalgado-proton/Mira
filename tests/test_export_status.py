"""spec/59 §8 — export status + the batch queue.

Pins: the born-green shipped default; the video cell's aggregate
colour (green all / red none / yellow partial — the cluster grammar);
segment birth honouring the configured edit default; the app-level
queue running jobs strictly one at a time with the commit callback on
the UI thread and cancel routed to the running worker.
"""
from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal

from mira.picked.model import video_edit_color
from mira.picked.status import CellColor
from mira.settings.model import Settings
from mira.store import models as m
from mira.store.repo import EventStore
from mira.gateway.event_gateway import EventGateway
from mira.ui.shell.batch_queue import BatchExportQueue


def test_edit_default_ships_born_green():
    """Nelson 2026-06-11: configurable, but born green out of the box."""
    assert Settings().edit_default_state == "picked"


# --------------------------------------------------------------------------- #
# The video cell aggregate (the cluster grammar at Edit)
# --------------------------------------------------------------------------- #


def _fake_gateway(seg_ids, snap_ids):
    return SimpleNamespace(
        video_segments=lambda _vid: [
            SimpleNamespace(item_id=i) for i in seg_ids],
        video_snapshots=lambda _vid: [
            SimpleNamespace(item_id=i) for i in snap_ids],
    )


def _states(**kv):
    return {k: SimpleNamespace(state=v) for k, v in kv.items()}


def test_video_cell_green_when_everything_marked():
    gw = _fake_gateway(["s1", "s2"], ["n1"])
    states = _states(s1="picked", s2="picked", n1="picked")
    assert video_edit_color(gw, "v", states, "skipped") == CellColor.KEPT


def test_video_cell_red_when_nothing_marked():
    gw = _fake_gateway(["s1", "s2"], [])
    states = _states(s1="skipped", s2="skipped")
    assert video_edit_color(gw, "v", states, "picked") == CellColor.DISCARDED


def test_video_cell_yellow_when_partial():
    gw = _fake_gateway(["s1", "s2"], ["n1"])
    states = _states(s1="picked", s2="skipped", n1="picked")
    assert video_edit_color(gw, "v", states, "skipped") == CellColor.MIXED


def test_video_cell_unborn_children_read_the_default():
    gw = _fake_gateway([], [])
    assert video_edit_color(gw, "v", {}, "picked") == CellColor.KEPT
    assert video_edit_color(gw, "v", {}, "skipped") == CellColor.DISCARDED


def test_video_cell_undecided_children_fold_to_default():
    gw = _fake_gateway(["s1", "s2"], [])
    # s1 explicit green, s2 no row + born-green default → all green.
    states = _states(s1="picked")
    assert video_edit_color(gw, "v", states, "picked") == CellColor.KEPT
    # Same rows under a born-red default → partial → yellow.
    assert video_edit_color(gw, "v", states, "skipped") == CellColor.MIXED


# --------------------------------------------------------------------------- #
# Segment birth honours the configured default (spec/59 supersedes the
# spec/56 fixed default-Skip)
# --------------------------------------------------------------------------- #


def _make_eg(tmp_path) -> EventGateway:
    store = EventStore.create(tmp_path / "event.db", event_id="evt-es")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-es", name="ES", created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="C1"))
    store.upsert(m.TripDay(day_number=1, date="2026-04-01"))
    store.upsert(m.Item(
        id="v1", kind="video", origin_relpath="d/v1.mp4",
        sha256="s", byte_size=2, materialized_at="t",
        materialized_phase="ingest", camera_id="C1",
        capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00",
        duration_ms=10_000, created_at="t", day_number=1,
        provenance="captured",
    ))
    return EventGateway(store, event_root=tmp_path, now=lambda: "t")


def test_segment_birth_honours_edit_default(tmp_path):
    eg = _make_eg(tmp_path)
    try:
        segs = eg.ensure_video_segments("v1", default_state="picked")
        assert len(segs) == 1
        assert eg.phase_state(segs[0].item_id, "edit").state == "picked"
    finally:
        eg.close()


def test_segment_birth_defaults_to_skip_when_unset(tmp_path):
    eg = _make_eg(tmp_path)
    try:
        segs = eg.ensure_video_segments("v1")
        assert eg.phase_state(segs[0].item_id, "edit").state == "skipped"
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# The batch queue — strictly one at a time, commit on finish, cancel
# --------------------------------------------------------------------------- #


class _FakeWorker(QObject):
    """Synchronous stand-in honouring the worker contract."""
    progress = pyqtSignal(int, int, str)
    finished_result = pyqtSignal(object)

    def __init__(self, name: str, journal: list) -> None:
        super().__init__()
        self._name = name
        self._journal = journal
        self.cancelled = False

    def start(self) -> None:
        self._journal.append(f"start:{self._name}")
        self.progress.emit(1, 1, self._name)
        self.finished_result.emit(SimpleNamespace(ok_count=1))

    def cancel(self) -> None:
        self.cancelled = True


def test_queue_runs_jobs_strictly_one_at_a_time(qapp):
    journal: list = []
    q = BatchExportQueue()
    w1 = _FakeWorker("one", journal)
    w2 = _FakeWorker("two", journal)
    q.enqueue(w1, "job one", lambda r: journal.append("commit:one"))
    q.enqueue(w2, "job two", lambda r: journal.append("commit:two"))
    # Synchronous workers: one fully finishes (commit included) before
    # two ever starts.
    assert journal == [
        "start:one", "commit:one", "start:two", "commit:two"]
    assert q.idle


def test_queue_commit_failure_never_stalls_the_line(qapp):
    journal: list = []
    q = BatchExportQueue()

    def bad_commit(_r):
        raise RuntimeError("boom")

    q.enqueue(_FakeWorker("one", journal), "job one", bad_commit)
    q.enqueue(_FakeWorker("two", journal),
              "job two", lambda r: journal.append("commit:two"))
    assert "start:two" in journal and "commit:two" in journal
    assert q.idle


def test_queue_cancel_routes_to_the_running_worker(qapp):
    journal: list = []

    class _Stalled(_FakeWorker):
        def start(self) -> None:           # never finishes on its own
            self._journal.append(f"start:{self._name}")

    q = BatchExportQueue()
    w = _Stalled("stuck", journal)
    q.enqueue(w, "stuck job", None)
    assert not q.idle
    q.cancel_current()
    assert w.cancelled
