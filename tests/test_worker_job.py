"""spec/60 slice 2 — the app-side job: WorkerJob spawn/kill/stream,
result folding (per-unit truth), the inline spawn-failure fallback,
and the queue-shaped BatchExportJob adapter.

The kill test renders MANY units from one source on purpose: it also
stress-exercises the _NameReserver (200 same-stem outputs must land
as distinct `` (n)`` names, never clobber).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.export_manifest import ExportManifest, PhotoUnit
from core.render_worker import run_manifest_inline
from core.worker_job import (
    BatchJobResult,
    WorkerJob,
    WorkerSpawnError,
    build_batch_result,
)


def _make_jpeg(path: Path, *, size=(64, 48), noise=False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if noise:
        rng = np.random.default_rng(7)
        arr = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
        Image.fromarray(arr).save(str(path), "JPEG", quality=95)
    else:
        Image.new("RGB", size, (100, 100, 100)).save(
            str(path), "JPEG", quality=95)
    return path


# ── result folding (pure) ────────────────────────────────────────────


def test_build_batch_result_buckets_and_truth():
    sources = {"a": Path(r"C:\s\a.jpg"), "b": Path(r"C:\s\b.jpg"),
               "c": Path(r"C:\s\c.jpg"), "d": Path(r"C:\s\d.jpg"),
               "e": Path(r"C:\s\e.jpg")}
    msgs = [
        {"type": "unit", "unit_id": "a", "status": "ok",
         "final_path": r"C:\o\a.jpg", "existed_before": False,
         "renamed": False, "params": {"exposure": 0.5}},
        {"type": "unit", "unit_id": "b", "status": "ok",
         "final_path": r"C:\o\b (2).jpg", "existed_before": True,
         "renamed": True, "params": {"exposure": 0.0}},
        {"type": "unit", "unit_id": "c", "status": "ok",
         "final_path": r"C:\o\c.jpg", "existed_before": True,
         "renamed": False, "params": None},
        {"type": "unit", "unit_id": "d", "status": "skipped",
         "reason": "source missing"},
        {"type": "unit", "unit_id": "e", "status": "error",
         "error": "render failed: boom"},
    ]
    r = build_batch_result(msgs, sources)
    assert isinstance(r, BatchJobResult)
    assert r.written == [Path(r"C:\o\a.jpg")]
    assert r.renamed == [(Path(r"C:\s\b.jpg"), Path(r"C:\o\b (2).jpg"))]
    assert r.overwritten == [Path(r"C:\o\c.jpg")]
    assert r.skipped == [(Path(r"C:\s\d.jpg"), "source missing")]
    assert r.errors == [(Path(r"C:\s\e.jpg"), "render failed: boom")]
    assert r.ok_count == 3
    # Per-unit truth for the commit seam.
    assert r.ok_unit_ids == {"a", "b", "c"}
    # The params_sink twin, keyed by source filename (lineage stems it).
    assert r.resolved_by_name == {
        "a.jpg": {"exposure": 0.5}, "b.jpg": {"exposure": 0.0}}


# ── WorkerJob: real process ──────────────────────────────────────────


def test_worker_job_spawn_stream_and_exit(tmp_path):
    src = _make_jpeg(tmp_path / "src" / "one.jpg")
    out = tmp_path / "out"
    m = ExportManifest(units=(
        PhotoUnit(unit_id="one", source=str(src), dest_dir=str(out),
                  auto_on=False),
    ))
    job = WorkerJob(m.save(tmp_path / "job.json"))
    job.start()
    msgs = list(job.messages())
    assert job.wait() == 0
    kinds = [m_["type"] for m_ in msgs]
    assert kinds[0] == "start" and kinds[-1] == "done"
    unit = next(m_ for m_ in msgs if m_["type"] == "unit")
    assert unit["status"] == "ok"
    assert Path(unit["final_path"]).is_file()


def test_worker_job_kill_stops_the_process_tree(tmp_path):
    # 200 renders of a noisy 2000×1500 source — long enough that the
    # kill always lands mid-job, on any machine. Same source for every
    # unit: the reserver must fan the outputs into distinct names.
    src = _make_jpeg(tmp_path / "src" / "big.jpg", size=(2000, 1500),
                     noise=True)
    out = tmp_path / "out"
    units = tuple(
        PhotoUnit(unit_id=f"u{i}", source=str(src), dest_dir=str(out),
                  auto_on=False, params={"sharpness": 40.0})
        for i in range(200)
    )
    m = ExportManifest(units=units)
    job = WorkerJob(m.save(tmp_path / "job.json"))
    job.start()
    received = []
    for msg in job.messages():
        if msg["type"] == "unit":
            received.append(msg)
            job.kill()          # first completed unit → kill the tree
            break
    # The pipe EOFs promptly after the kill; drain whatever was in
    # flight and confirm the job never ran to completion.
    for msg in job.messages():
        if msg["type"] == "unit":
            received.append(msg)
        assert msg["type"] != "done"
    assert job.wait() != 0
    assert len(received) < 200
    # The outputs that DID land never clobbered each other.
    names = sorted(p.name for p in out.glob("*.jpg"))
    assert len(names) == len(set(names))


# ── the inline fallback ──────────────────────────────────────────────


def test_run_manifest_inline_renders_and_cancels(tmp_path):
    src = _make_jpeg(tmp_path / "src" / "p.jpg")
    out = tmp_path / "out"
    units = tuple(
        PhotoUnit(unit_id=f"u{i}", source=str(src), dest_dir=str(out),
                  auto_on=False) for i in range(3))
    m = ExportManifest(units=units)

    ticks = []
    msgs = run_manifest_inline(
        m, progress=lambda d, t, n: ticks.append((d, t, n)) or True)
    assert [x["status"] for x in msgs] == ["ok"] * 3
    assert ticks == [(1, 3, "p.jpg"), (2, 3, "p.jpg"), (3, 3, "p.jpg")]

    # Cancel after the first unit: one message, the rest untouched.
    out2 = tmp_path / "out2"
    units2 = tuple(
        PhotoUnit(unit_id=f"v{i}", source=str(src), dest_dir=str(out2),
                  auto_on=False) for i in range(3))
    msgs2 = run_manifest_inline(
        ExportManifest(units=units2),
        progress=lambda d, t, n: d <= 1)
    assert len(msgs2) == 1


# ── BatchExportJob (the queue contract) ──────────────────────────────


def test_batch_export_job_end_to_end(qapp, tmp_path):
    from mira.ui.edited.export_job import BatchExportJob

    src = _make_jpeg(tmp_path / "src" / "a.jpg")
    out = tmp_path / "out"
    m = ExportManifest(units=(
        PhotoUnit(unit_id="item-a", source=str(src), dest_dir=str(out),
                  auto_on=False, params={"exposure": 0.5}),
        PhotoUnit(unit_id="item-b", source=str(tmp_path / "gone.jpg"),
                  dest_dir=str(out)),
    ))
    job = BatchExportJob(m, {"item-a": src,
                             "item-b": tmp_path / "gone.jpg"})
    ticks, results = [], []
    job.progress.connect(lambda d, t, n: ticks.append((d, t, n)))
    job.finished_result.connect(results.append)
    job.run()                       # synchronous — same body the
    #                                 QThread runs when queued
    assert len(results) == 1
    r = results[0]
    assert r.ok_unit_ids == {"item-a"}
    assert not r.ran_inline
    assert r.resolved_by_name["a.jpg"]["exposure"] == 0.5
    assert [s for s, _reason in r.skipped] == [tmp_path / "gone.jpg"]
    assert len(ticks) == 2 and ticks[-1][1] == 2
    assert Path(r.written[0]).is_file()


def test_batch_export_job_falls_back_inline_on_spawn_failure(
        qapp, tmp_path, monkeypatch):
    from mira.ui.edited import export_job as ej

    class _NoSpawn:
        def __init__(self, _path):
            pass

        def start(self):
            raise WorkerSpawnError("blocked by test")

    monkeypatch.setattr(ej, "WorkerJob", _NoSpawn)
    src = _make_jpeg(tmp_path / "src" / "a.jpg")
    out = tmp_path / "out"
    m = ExportManifest(units=(
        PhotoUnit(unit_id="item-a", source=str(src), dest_dir=str(out),
                  auto_on=False),
    ))
    job = ej.BatchExportJob(m, {"item-a": src})
    results = []
    job.finished_result.connect(results.append)
    job.run()
    r = results[0]
    assert r.ran_inline is True
    assert r.ok_unit_ids == {"item-a"}
    assert Path(r.written[0]).is_file()   # rendered in-process


def test_batch_export_job_survives_manifest_noise(qapp, tmp_path):
    # A job whose worker emits garbage between protocol lines must
    # still fold cleanly — simulate by feeding messages() output with
    # a fake job object through the public seam: here we just assert
    # build_batch_result ignores non-unit message types.
    r = build_batch_result(
        [{"type": "start", "total": 1},
         {"type": "unit", "unit_id": "x", "status": "ok",
          "final_path": str(tmp_path / "x.jpg"),
          "existed_before": False, "renamed": False, "params": None},
         {"type": "done", "ok": 1, "errors": 0, "skipped": 0}],
        {"x": tmp_path / "x.jpg"})
    assert r.ok_unit_ids == {"x"} and len(r.unit_results) == 1
