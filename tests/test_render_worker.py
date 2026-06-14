"""The spec/60 render worker — manifest round-trip, capacity math,
the N-wide photo lane, per-unit truth, and the real-subprocess smoke.

Everything but the last test drives :func:`core.render_worker.
worker_main` in-process with an injected ``out`` stream — same code
path the process runs, without interpreter-spawn latency. The final
test spawns the worker exactly as the app will
(``python -m mira --render-worker …``).
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.export_manifest import (
    COLLISION_OVERRIDE,
    COLLISION_UNIQUE,
    ExportManifest,
    PhotoUnit,
)
from core.render_worker import (
    _pool_width,
    worker_command,
    worker_main,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── helpers ───────────────────────────────────────────────────────────


def _make_jpeg(path: Path, *, size=(64, 48), color=(100, 100, 100)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(str(path), "JPEG", quality=95)
    return path


def _run_main(manifest_path: Path) -> tuple[int, list[dict]]:
    out = io.StringIO()
    rc = worker_main([str(manifest_path)], out=out)
    lines = [json.loads(ln) for ln in out.getvalue().splitlines()
             if ln.strip()]
    return rc, lines


def _unit_msgs(lines: list[dict]) -> dict[str, dict]:
    return {m["unit_id"]: m for m in lines if m["type"] == "unit"}


def _done_msg(lines: list[dict]) -> dict:
    return next(m for m in lines if m["type"] == "done")


def _mean(path: Path) -> float:
    return float(np.asarray(Image.open(str(path))).mean())


# ── manifest ──────────────────────────────────────────────────────────


def test_manifest_round_trip(tmp_path):
    unit = PhotoUnit(
        unit_id="it-1", source=r"C:\src\IMG_0001.jpg",
        dest_dir=r"C:\out\Dia 1", file_type="jpeg", jpeg_quality=92,
        params={"exposure": 0.5, "vibrance": 10.0},
        look={"look": "punch", "style": "wildlife"},
        auto_on=False, style="wildlife",
        crop_norm=(0.1, 0.2, 0.5, 0.5), crop_angle=1.5, rotation=90,
        aspect_label="16:9",
    )
    m = ExportManifest(units=(unit,), collision=COLLISION_OVERRIDE)
    p = m.save(tmp_path / "job.json")
    loaded = ExportManifest.load(p)
    assert loaded == m
    assert isinstance(loaded.units[0].crop_norm, tuple)


def test_manifest_load_drops_unknown_fields(tmp_path):
    # Forward compatibility: a newer app may write fields an older
    # worker doesn't know — they must be ignored, not fatal.
    m = ExportManifest(units=(PhotoUnit(
        unit_id="u", source="s", dest_dir="d"),))
    d = json.loads(m.to_json())
    d["units"][0]["from_the_future"] = True
    p = tmp_path / "job.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    loaded = ExportManifest.load(p)
    assert loaded.units[0].unit_id == "u"


# ── capacity (spec/60 §2/§4) ─────────────────────────────────────────


def test_pool_width_floor_and_ram_cap():
    assert _pool_width(1, None) == 1          # dual-core floor
    assert _pool_width(2, None) == 1
    assert _pool_width(8, None) == 6          # cores − 2
    assert _pool_width(8, 600 * 2**20 * 3) == 3   # RAM-capped
    assert _pool_width(8, 1) == 1             # floor: one in flight
    assert _pool_width(None, None) == 1


def test_worker_command_source_mode(tmp_path):
    cmd = worker_command(tmp_path / "job.json")
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "mira"]
    assert cmd[3] == "--render-worker"


# ── the photo lane, in-process ───────────────────────────────────────


def test_identity_and_params_units(tmp_path):
    src_a = _make_jpeg(tmp_path / "src" / "flat.jpg")
    src_b = _make_jpeg(tmp_path / "src" / "lift.jpg")
    out = tmp_path / "out"
    m = ExportManifest(units=(
        PhotoUnit(unit_id="identity", source=str(src_a),
                  dest_dir=str(out), auto_on=False),
        PhotoUnit(unit_id="lifted", source=str(src_b),
                  dest_dir=str(out), auto_on=False,
                  params={"exposure": 1.0}),
    ))
    rc, lines = _run_main(m.save(tmp_path / "job.json"))
    assert rc == 0
    units = _unit_msgs(lines)
    assert {u["status"] for u in units.values()} == {"ok"}
    assert _done_msg(lines) == {
        "type": "done", "ok": 2, "errors": 0, "skipped": 0}
    start = next(ln for ln in lines if ln["type"] == "start")
    assert start["total"] == 2 and start["width"] >= 1

    ident = Path(units["identity"]["final_path"])
    lifted = Path(units["lifted"]["final_path"])
    assert ident.is_file() and lifted.is_file()
    # Identity render = source pixels (modulo JPEG re-encode).
    assert abs(_mean(ident) - 100.0) < 4
    # +1 EV on mid-gray is clearly brighter.
    assert _mean(lifted) > _mean(ident) + 20
    # The resolved tone numbers ride back for the lineage snapshot.
    assert units["lifted"]["params"]["exposure"] == 1.0
    assert units["identity"]["params"]["exposure"] == 0.0


def test_crop_and_rotation_unit(tmp_path):
    src = _make_jpeg(tmp_path / "src" / "geo.jpg", size=(64, 48))
    out = tmp_path / "out"
    m = ExportManifest(units=(PhotoUnit(
        unit_id="geo", source=str(src), dest_dir=str(out),
        auto_on=False, rotation=90, crop_norm=(0.0, 0.0, 0.5, 0.5)),))
    rc, lines = _run_main(m.save(tmp_path / "job.json"))
    assert rc == 0
    final = Path(_unit_msgs(lines)["geo"]["final_path"])
    # 64×48 rotated 90° → 48×64; the half crop is normalised against
    # the rotated frame → 24×32.
    assert Image.open(str(final)).size == (24, 32)


def test_per_unit_truth(tmp_path):
    # One good file, one missing, one unsupported, one corrupt —
    # the job runs to "done", exits 0, and each unit reports its own
    # outcome (spec/60 §5).
    good = _make_jpeg(tmp_path / "src" / "good.jpg")
    notes = tmp_path / "src" / "notes.txt"
    notes.write_text("not a photo", encoding="utf-8")
    corrupt = tmp_path / "src" / "corrupt.jpg"
    corrupt.write_bytes(b"this is no jpeg")
    out = tmp_path / "out"

    def unit(uid, p):
        return PhotoUnit(unit_id=uid, source=str(p),
                         dest_dir=str(out), auto_on=False)

    m = ExportManifest(units=(
        unit("good", good),
        unit("missing", tmp_path / "src" / "gone.jpg"),
        unit("unsupported", notes),
        unit("corrupt", corrupt),
    ))
    rc, lines = _run_main(m.save(tmp_path / "job.json"))
    assert rc == 0
    units = _unit_msgs(lines)
    assert units["good"]["status"] == "ok"
    assert units["missing"] == {
        "type": "unit", "unit_id": "missing", "kind": "photo",
        "status": "skipped", "reason": "source missing"}
    assert units["unsupported"]["reason"] == "unsupported format"
    assert units["corrupt"]["status"] == "error"
    assert units["corrupt"]["error"].startswith("render failed")
    assert _done_msg(lines) == {
        "type": "done", "ok": 1, "errors": 1, "skipped": 2}


def test_collision_unique_renames(tmp_path):
    src = _make_jpeg(tmp_path / "src" / "photo.jpg")
    out = tmp_path / "out"
    _make_jpeg(out / "photo.jpg", color=(1, 2, 3))     # pre-existing
    m = ExportManifest(units=(PhotoUnit(
        unit_id="u", source=str(src), dest_dir=str(out),
        auto_on=False),), collision=COLLISION_UNIQUE)
    rc, lines = _run_main(m.save(tmp_path / "job.json"))
    assert rc == 0
    msg = _unit_msgs(lines)["u"]
    assert msg["renamed"] is True
    assert Path(msg["final_path"]).name == "photo (2).jpg"
    # The pre-existing file is untouched.
    assert _mean(out / "photo.jpg") < 10


def test_collision_override_replaces(tmp_path):
    src = _make_jpeg(tmp_path / "src" / "photo.jpg")
    out = tmp_path / "out"
    _make_jpeg(out / "photo.jpg", color=(1, 2, 3))
    m = ExportManifest(units=(PhotoUnit(
        unit_id="u", source=str(src), dest_dir=str(out),
        auto_on=False),), collision=COLLISION_OVERRIDE)
    rc, lines = _run_main(m.save(tmp_path / "job.json"))
    assert rc == 0
    msg = _unit_msgs(lines)["u"]
    assert msg["existed_before"] is True and msg["renamed"] is False
    assert Path(msg["final_path"]).name == "photo.jpg"
    assert abs(_mean(out / "photo.jpg") - 100.0) < 4   # replaced


def test_same_name_units_never_clobber(tmp_path):
    # Two in-flight units writing the same output name in the same
    # dir: the disk can't arbitrate (neither file exists yet) — the
    # claim set must (the N-wide hazard the serial engine never had).
    src_a = _make_jpeg(tmp_path / "a" / "photo.jpg", color=(50, 50, 50))
    src_b = _make_jpeg(tmp_path / "b" / "photo.jpg", color=(200, 200, 200))
    out = tmp_path / "out"
    m = ExportManifest(units=(
        PhotoUnit(unit_id="a", source=str(src_a), dest_dir=str(out),
                  auto_on=False),
        PhotoUnit(unit_id="b", source=str(src_b), dest_dir=str(out),
                  auto_on=False),
    ))
    rc, lines = _run_main(m.save(tmp_path / "job.json"))
    assert rc == 0
    units = _unit_msgs(lines)
    finals = {Path(u["final_path"]).name for u in units.values()}
    assert finals == {"photo.jpg", "photo (2).jpg"}
    assert _done_msg(lines)["ok"] == 2


def test_original_is_byte_copy(tmp_path):
    src = _make_jpeg(tmp_path / "src" / "raw_stand_in.jpg")
    out = tmp_path / "out"
    m = ExportManifest(units=(PhotoUnit(
        unit_id="u", source=str(src), dest_dir=str(out),
        file_type="original"),))
    rc, lines = _run_main(m.save(tmp_path / "job.json"))
    assert rc == 0
    msg = _unit_msgs(lines)["u"]
    assert msg["status"] == "ok" and msg["params"] is None
    assert Path(msg["final_path"]).read_bytes() == src.read_bytes()


def test_fatal_on_unusable_manifest(tmp_path):
    out = io.StringIO()
    assert worker_main([str(tmp_path / "missing.json")], out=out) == 2
    msg = json.loads(out.getvalue().splitlines()[0])
    assert msg["type"] == "fatal"
    out = io.StringIO()
    assert worker_main([], out=out) == 2


# ── the real process, exactly as the app spawns it ───────────────────


def test_worker_subprocess_end_to_end(tmp_path):
    src = _make_jpeg(tmp_path / "src" / "real.jpg")
    out = tmp_path / "out"
    m = ExportManifest(units=(
        PhotoUnit(unit_id="ok", source=str(src), dest_dir=str(out),
                  auto_on=False, params={"exposure": 0.5}),
        PhotoUnit(unit_id="gone", source=str(tmp_path / "nope.jpg"),
                  dest_dir=str(out)),
    ))
    manifest_path = m.save(tmp_path / "job.json")
    proc = subprocess.run(
        worker_command(manifest_path), capture_output=True, text=True,
        cwd=str(REPO_ROOT), timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(ln) for ln in proc.stdout.splitlines()
             if ln.strip()]
    assert _done_msg(lines) == {
        "type": "done", "ok": 1, "errors": 0, "skipped": 1}
    units = _unit_msgs(lines)
    assert Path(units["ok"]["final_path"]).is_file()
    assert units["ok"]["params"]["exposure"] == 0.5
    assert units["gone"]["status"] == "skipped"
