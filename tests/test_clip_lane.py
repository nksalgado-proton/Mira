"""The spec/60 §3 clip lane in the render worker. The actual ffmpeg
runner is heavyweight; ``export_processed_clip`` is replaced with a
stub that writes a small file so the harness is fast and offline.
The wire (PhotoUnit + ClipUnit) is also exercised end-to-end through
the manifest round-trip and the result fold."""
from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest
from PIL import Image

from core.export_manifest import (
    ClipUnit,
    ExportManifest,
    PhotoUnit,
)
from core.worker_job import build_batch_result


def _make_jpeg(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), (120, 120, 120)).save(
        str(path), "JPEG", quality=85)
    return path


def _make_clip_plan(in_ms=0, out_ms=1000, src_fps=24.0):
    return {
        "in_ms": in_ms, "out_ms": out_ms,
        "params": {},
        "crop_norm": None, "box_angle": 0.0,
        "include_audio": True, "audio_volume": 1.0, "audio_fade_ms": 0,
        "speed": 1.0, "stabilise": 0.0,
        "src_fps": src_fps,
        "filter_recipe": None, "filter_amount": 1.0,
    }


# ── manifest with clips ──────────────────────────────────────────────


def test_manifest_round_trip_with_clips(tmp_path):
    m = ExportManifest(
        units=(PhotoUnit(unit_id="p", source="s", dest_dir="d"),),
        clips=(ClipUnit(unit_id="c", source="v.mp4",
                        dest_dir="d", base_name="v_clip1",
                        plan=_make_clip_plan(), style="wildlife"),),
        collision="unique",
    )
    loaded = ExportManifest.from_json(m.to_json())
    assert loaded.units == m.units
    assert loaded.clips == m.clips


def test_legacy_manifest_without_clips_loads_clean(tmp_path):
    # Manifests written by an older app must remain readable — slice
    # 1 + 2 binaries should still hydrate against slice-3 readers.
    text = json.dumps({"version": 1, "collision": "unique",
                       "units": [{"unit_id": "p", "source": "s",
                                  "dest_dir": "d"}]})
    loaded = ExportManifest.from_json(text)
    assert loaded.clips == ()
    assert loaded.units[0].unit_id == "p"


# ── result folding: clips don't pollute the photo lineage buckets ────


def test_build_result_keeps_clips_out_of_photo_buckets(tmp_path):
    src_photo = Path(r"C:\s\p.jpg")
    src_video = Path(r"C:\s\v.mp4")
    msgs = [
        {"type": "unit", "unit_id": "p", "kind": "photo",
         "status": "ok", "final_path": r"C:\o\p.jpg",
         "existed_before": False, "renamed": False,
         "params": {"exposure": 0.0}},
        {"type": "unit", "unit_id": "c", "kind": "clip",
         "status": "ok", "final_path": r"C:\o\v_clip1.mp4",
         "existed_before": False, "renamed": False,
         "params": {"exposure": 0.5}},
    ]
    r = build_batch_result(msgs, {"p": src_photo, "c": src_video})
    # Photo bucket has the photo only — folding the clip in would
    # mislead the photo lineage walker, which keys by stem.
    assert r.written == [Path(r"C:\o\p.jpg")]
    assert r.ok_unit_ids == {"p", "c"}
    # The clip results stand out for the host's clip-lineage seam.
    assert len(r.ok_clip_results) == 1
    assert r.ok_clip_results[0]["final_path"] == r"C:\o\v_clip1.mp4"
    # resolved_by_name is photo-only too — clips have no stem key.
    assert "v.mp4" not in r.resolved_by_name


# ── worker_main: clip lane invokes the runner with the right plan ────


def test_worker_main_clip_lane(tmp_path, monkeypatch):
    """A photo + a clip ride side-by-side; the clip lane calls
    ``export_processed_clip`` with a reconstructed ExportPlan and the
    stubbed runner writes a placeholder file. The protocol carries
    ``kind`` on each unit message and a clip count in start."""
    from core import render_worker

    src_photo = _make_jpeg(tmp_path / "src" / "p.jpg")
    # The clip lane never opens the video file itself — it hands it to
    # the (stubbed) runner. A stub file satisfies the is_file() check.
    src_video = tmp_path / "src" / "v.mp4"
    src_video.parent.mkdir(parents=True, exist_ok=True)
    src_video.write_bytes(b"stub mp4")

    out_dir = tmp_path / "out"
    captured = {}

    def _fake_export_processed_clip(src, dest, plan, **_kw):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake mp4 payload")
        captured["src"] = Path(src)
        captured["dest"] = Path(dest)
        captured["plan"] = plan
        return dest

    # Patch the import the worker reaches into (module is imported
    # inside _render_clip_unit, so monkeypatch the symbol there).
    import core.video_export_run as ver
    monkeypatch.setattr(
        ver, "export_processed_clip", _fake_export_processed_clip)

    m = ExportManifest(
        units=(PhotoUnit(unit_id="p", source=str(src_photo),
                         dest_dir=str(out_dir), auto_on=False),),
        clips=(ClipUnit(unit_id="c", source=str(src_video),
                        dest_dir=str(out_dir), base_name="v_clip1",
                        plan=_make_clip_plan(in_ms=0, out_ms=2000,
                                             src_fps=24.0)),),
    )
    out = io.StringIO()
    rc = render_worker.worker_main([str(m.save(tmp_path / "job.json"))],
                                   out=out)
    assert rc == 0
    lines = [json.loads(ln) for ln in out.getvalue().splitlines()
             if ln.strip()]
    start = next(ln for ln in lines if ln["type"] == "start")
    assert start["total"] == 2 and start["clips"] == 1
    done = next(ln for ln in lines if ln["type"] == "done")
    assert done == {"type": "done", "ok": 2, "errors": 0, "skipped": 0}
    units = {ln["unit_id"]: ln for ln in lines if ln["type"] == "unit"}
    assert units["p"]["kind"] == "photo"
    assert units["c"]["kind"] == "clip"
    assert units["c"]["status"] == "ok"
    assert Path(units["c"]["final_path"]).read_bytes() == \
        b"fake mp4 payload"
    # The plan re-hydrated correctly on the worker side.
    assert captured["plan"].in_ms == 0
    assert captured["plan"].out_ms == 2000
    assert captured["plan"].src_fps == 24.0


def test_clip_lane_missing_source_skipped(tmp_path, monkeypatch):
    """No source video → unit skipped, never reaches the runner."""
    from core import render_worker

    called = []

    def _fake(*_a, **_kw):
        called.append(1)
        return _a[1]

    import core.video_export_run as ver
    monkeypatch.setattr(ver, "export_processed_clip", _fake)

    m = ExportManifest(
        units=(),
        clips=(ClipUnit(unit_id="c",
                        source=str(tmp_path / "gone.mp4"),
                        dest_dir=str(tmp_path / "out"),
                        base_name="missing",
                        plan=_make_clip_plan()),),
    )
    out = io.StringIO()
    assert render_worker.worker_main(
        [str(m.save(tmp_path / "job.json"))], out=out) == 0
    lines = [json.loads(ln) for ln in out.getvalue().splitlines()
             if ln.strip()]
    msg = next(ln for ln in lines if ln["type"] == "unit")
    assert msg["status"] == "skipped" and msg["reason"] == "source missing"
    assert called == []


def test_clip_lane_runner_error_per_unit_truth(tmp_path, monkeypatch):
    """A clip whose runner raises → the unit lands as ``error`` and
    the job runs to ``done`` (per-unit truth, spec/60 §5)."""
    from core import render_worker

    def _boom(*_a, **_kw):
        raise RuntimeError("FFmpeg encode failed")

    import core.video_export_run as ver
    monkeypatch.setattr(ver, "export_processed_clip", _boom)

    src_video = tmp_path / "src" / "v.mp4"
    src_video.parent.mkdir(parents=True, exist_ok=True)
    src_video.write_bytes(b"stub")

    m = ExportManifest(
        units=(),
        clips=(ClipUnit(unit_id="c", source=str(src_video),
                        dest_dir=str(tmp_path / "out"),
                        base_name="v_clip1",
                        plan=_make_clip_plan()),),
    )
    out = io.StringIO()
    assert render_worker.worker_main(
        [str(m.save(tmp_path / "job.json"))], out=out) == 0
    lines = [json.loads(ln) for ln in out.getvalue().splitlines()
             if ln.strip()]
    done = next(ln for ln in lines if ln["type"] == "done")
    assert done == {"type": "done", "ok": 0, "errors": 1, "skipped": 0}
    unit = next(ln for ln in lines if ln["type"] == "unit")
    assert unit["status"] == "error"
    assert "FFmpeg" in unit["error"]


def test_inline_fallback_runs_photos_then_clips(tmp_path, monkeypatch):
    from core import render_worker

    def _fake(src, dest, plan, **_kw):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake")
        return dest

    import core.video_export_run as ver
    monkeypatch.setattr(ver, "export_processed_clip", _fake)

    src_photo = _make_jpeg(tmp_path / "s" / "p.jpg")
    src_video = tmp_path / "s" / "v.mp4"
    src_video.parent.mkdir(parents=True, exist_ok=True)
    src_video.write_bytes(b"stub")

    m = ExportManifest(
        units=(PhotoUnit(unit_id="p", source=str(src_photo),
                         dest_dir=str(tmp_path / "out"),
                         auto_on=False),),
        clips=(ClipUnit(unit_id="c", source=str(src_video),
                        dest_dir=str(tmp_path / "out"),
                        base_name="v_clip1",
                        plan=_make_clip_plan()),),
    )
    msgs = render_worker.run_manifest_inline(m)
    kinds = [m_["kind"] for m_ in msgs]
    assert kinds == ["photo", "clip"]
    assert all(m_["status"] == "ok" for m_ in msgs)
