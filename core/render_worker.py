"""The spec/60 batch render worker — our own binary in worker mode.

Spawned once per batch job (``Mira.exe --render-worker
<manifest.json>`` packaged, ``python -m mira --render-worker
<manifest.json>`` from source — :func:`worker_command` picks the right
one). Reads the fully-resolved :class:`~core.export_manifest.
ExportManifest`, renders the photo lane N-wide, and streams per-unit
results back as JSON lines on stdout:

    {"type": "start", "total": N, "width": W}
    {"type": "unit", "unit_id": …, "status": "ok",
     "final_path": …, "existed_before": …, "renamed": …,
     "params": {…}}                  # resolved tone numbers (lineage)
    {"type": "unit", "unit_id": …, "status": "skipped", "reason": …}
    {"type": "unit", "unit_id": …, "status": "error", "error": …}
    {"type": "done", "ok": N, "errors": N, "skipped": N}
    {"type": "fatal", "error": …}    # manifest unusable; exit code 2

stdout carries ONLY protocol lines — worker logging goes to stderr,
and the app-side reader skips anything that doesn't parse. The wire
is pure ASCII (json escapes), so console code pages can't corrupt it.

The render is the EXACT preview pipeline — units feed
:func:`core.process_export_engine._render_one` /
:func:`~core.process_export_engine._write_image` unchanged, so colour
parity holds by construction on every machine (spec/60 §1).

Photos render N-wide on a thread pool (numpy/PIL release the GIL —
spec/60 §3); width = cores−2 (floor 1) capped by an available-RAM
budget (spec/60 §2/§4). The worker lowers ITSELF to below-normal
priority — every ffmpeg child it ever spawns inherits the class, so
the Windows scheduler is the yield-to-foreground mechanism (§2).
Cancel is process-shaped: the app kills the worker tree; there is no
cooperative cancel protocol in here (§6).

Per-unit truth (§5): one bad file is one ``error`` line — the job
always runs to ``done`` and exits 0.

Pure logic — no Qt, no gateway, no ``event.db``.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import IO, Optional

from core.export_manifest import (
    COLLISION_OVERRIDE,
    ClipUnit,
    ExportManifest,
    PhotoUnit,
)

log = logging.getLogger(__name__)

# Memory budget per in-flight photo (spec/60 §2): a 24 MP float
# intermediate is ~300 MB; doubled for the decode + output copies
# alive around it.
_UNIT_RAM_BYTES = 600 * 2**20

_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


# ── spawn-side helpers (used by the app, kept here so the command and
#    the dispatch can never drift apart) ───────────────────────────────


def _is_compiled() -> bool:
    """True under a packaged (Nuitka/frozen) build, where
    ``sys.executable`` IS the app binary."""
    main_mod = sys.modules.get("__main__")
    return bool(
        getattr(sys, "frozen", False)
        or (main_mod is not None and "__compiled__" in dir(main_mod))
    )


def worker_command(manifest_path: Path) -> list[str]:
    """The argv that starts THIS app in worker mode — identical
    behaviour from source and packaged (spec/60 §1)."""
    if _is_compiled():
        return [sys.executable, "--render-worker", str(manifest_path)]
    return [sys.executable, "-m", "mira",
            "--render-worker", str(manifest_path)]


# ── capacity (spec/60 §2/§4 — sized from the actual machine) ─────────


def _pool_width(cores: Optional[int],
                available_ram: Optional[int]) -> int:
    """cores−2 (floor 1), capped by the RAM budget (floor 1 — one
    unit in flight always proceeds)."""
    width = max(1, (cores or 1) - 2)
    if available_ram is not None:
        width = min(width, max(1, int(available_ram // _UNIT_RAM_BYTES)))
    return width


def _available_ram_bytes() -> Optional[int]:
    """Available physical RAM via GlobalMemoryStatusEx; ``None`` when
    it can't be read (no cap applied then)."""
    if sys.platform != "win32":
        return None
    import ctypes

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint32),
            ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    try:
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(
                ctypes.byref(stat)):
            return int(stat.ullAvailPhys)
    except Exception:  # noqa: BLE001
        pass
    return None


def _lower_own_priority() -> None:
    """Below-normal priority for THIS process (spec/60 §2). Set here
    rather than by the spawner so every ffmpeg child the worker ever
    starts inherits the class and the guarantee holds no matter who
    spawned us. Best-effort — failure never blocks a render."""
    try:
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(),
                                 _BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except Exception:  # noqa: BLE001
        pass


# ── output-name resolution under concurrency ─────────────────────────


class _NameReserver:
    """Concurrent-safe collision resolution. The serial engine
    resolved names one file at a time against the disk; N-wide
    rendering adds in-flight names the disk can't see yet — claims
    make two same-named units in one job land as ``name`` and
    ``name (2)`` instead of silently clobbering each other."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claimed: set[str] = set()

    def claim(self, dest_dir: Path, name: str,
              collision: str) -> tuple[Path, bool, bool]:
        """→ ``(final, existed_before, renamed)``. UNIQUE walks
        ``stem (n)`` past both disk and in-flight claims; OVERRIDE
        keeps the requested name (replace is the intent)."""
        with self._lock:
            cand = dest_dir / name
            existed = cand.exists()
            if collision == COLLISION_OVERRIDE:
                self._claimed.add(str(cand))
                return cand, existed, False
            if not existed and str(cand) not in self._claimed:
                self._claimed.add(str(cand))
                return cand, False, False
            stem, suffix = Path(name).stem, Path(name).suffix
            n = 2
            while True:
                cand = dest_dir / f"{stem} ({n}){suffix}"
                if not cand.exists() and str(cand) not in self._claimed:
                    self._claimed.add(str(cand))
                    return cand, existed, True
                n += 1


# ── the clip lane (spec/60 §3 — one at a time, frame-parallel) ───────


def _render_clip_unit(clip: ClipUnit, collision: str,
                      reserver: _NameReserver) -> dict:
    """Render one clip via :func:`core.video_export_run.
    export_processed_clip` — the same ffmpeg + per-frame numpy
    pipeline the workshop preview uses. ``plan`` is hydrated from
    the worker side so :class:`core.video_export.ExportPlan` never
    crosses the JSON wire (no Params dataclass on the other side)."""
    from core.photo_render import Params
    from core.video_export import ExportPlan
    from core.video_export_run import export_processed_clip

    src = Path(clip.source)
    base = {"type": "unit", "unit_id": clip.unit_id, "kind": "clip"}
    if not src.is_file():
        return {**base, "status": "skipped", "reason": "source missing"}

    plan_d = dict(clip.plan or {})
    try:
        params_d = plan_d.get("params") or {}
        known = Params.__dataclass_fields__
        params = Params(**{k: v for k, v in params_d.items()
                           if k in known})
        crop = plan_d.get("crop_norm")
        crop_norm = tuple(float(v) for v in crop) if crop else None
        plan = ExportPlan(
            in_ms=int(plan_d.get("in_ms", 0)),
            out_ms=int(plan_d.get("out_ms", 0)),
            params=params,
            crop_norm=crop_norm,
            box_angle=float(plan_d.get("box_angle", 0.0)),
            include_audio=bool(plan_d.get("include_audio", True)),
            audio_volume=float(plan_d.get("audio_volume", 1.0)),
            audio_fade_ms=int(plan_d.get("audio_fade_ms", 0)),
            speed=float(plan_d.get("speed", 1.0)),
            stabilise=float(plan_d.get("stabilise", 0.0)),
            src_fps=float(plan_d.get("src_fps", 30.0)),
            filter_recipe=plan_d.get("filter_recipe"),
            filter_amount=float(plan_d.get("filter_amount", 1.0)),
        )
    except Exception as exc:                                # noqa: BLE001
        log.warning("worker clip plan invalid for %s: %s", src, exc)
        return {**base, "status": "error",
                "error": f"plan invalid: {exc}"}

    dest_dir = Path(clip.dest_dir)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        final, existed, renamed = reserver.claim(
            dest_dir, (clip.base_name or src.stem) + ".mp4", collision)
        export_processed_clip(src, final, plan)
        return {**base, "status": "ok", "final_path": str(final),
                "existed_before": existed, "renamed": renamed,
                "params": {f: getattr(params, f)
                           for f in params.__dataclass_fields__}}
    except Exception as exc:                                # noqa: BLE001
        log.warning("worker clip render failed for %s: %s", src, exc)
        return {**base, "status": "error",
                "error": f"clip render failed: {exc}"}


# ── the photo lane ────────────────────────────────────────────────────


def _render_unit(unit: PhotoUnit, collision: str,
                 reserver: _NameReserver) -> dict:
    """Render one unit to disk. Never raises — every outcome is a
    result dict (per-unit truth, spec/60 §5). Statuses and reason
    texts mirror the serial engine so summaries read the same."""
    from core.cull_export import ExportFileType
    from core.photo_decoder import is_supported
    from core.photo_render import Params
    from core.process_export_engine import _render_one, _write_image

    src = Path(unit.source)
    base = {"type": "unit", "unit_id": unit.unit_id, "kind": "photo"}
    if not src.is_file():
        return {**base, "status": "skipped", "reason": "source missing"}

    dest_dir = Path(unit.dest_dir)

    # ORIGINAL = byte copy — RAW/HEIC stay intact (engine parity).
    if unit.file_type == ExportFileType.ORIGINAL.value:
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            final, existed, renamed = reserver.claim(
                dest_dir, src.name, collision)
            shutil.copy2(str(src), str(final))
            return {**base, "status": "ok", "final_path": str(final),
                    "existed_before": existed, "renamed": renamed,
                    "params": None}
        except OSError as exc:
            log.warning("worker ORIGINAL copy failed for %s: %s",
                        src, exc)
            return {**base, "status": "error", "error": str(exc)}

    if not is_supported(src):
        return {**base, "status": "skipped",
                "reason": "unsupported format"}

    try:
        cached = None
        if unit.params is not None:
            known = Params.__dataclass_fields__
            cached = Params(**{k: v for k, v in unit.params.items()
                               if k in known})
        rendered, used_params = _render_one(
            src,
            auto_on=unit.auto_on,
            cached_params=cached,
            look_choice=unit.look,
            crop_norm=unit.crop_norm,
            crop_angle=float(unit.crop_angle or 0.0),
            rotation=int(unit.rotation or 0),
            aspect_label=unit.aspect_label,
            style=unit.style,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("worker render failed for %s: %s", src, exc)
        return {**base, "status": "error",
                "error": f"render failed: {exc}"}

    try:
        file_type = ExportFileType(unit.file_type)
        suffix = ".jpg" if file_type is ExportFileType.JPEG else ".tif"
        final, existed, renamed = reserver.claim(
            dest_dir, src.stem + suffix, collision)
        _write_image(rendered, final, file_type=file_type,
                     jpeg_quality=int(unit.jpeg_quality))
        return {**base, "status": "ok", "final_path": str(final),
                "existed_before": existed, "renamed": renamed,
                "params": {f: getattr(used_params, f)
                           for f in used_params.__dataclass_fields__}}
    except OSError as exc:
        log.warning("worker write failed for %s: %s", src, exc)
        return {**base, "status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("worker encode failed for %s: %s", src, exc)
        return {**base, "status": "error", "error": f"encode failed: {exc}"}


def run_manifest_inline(manifest: ExportManifest,
                        progress=None) -> list[dict]:
    """The spec/60 §4 last resort: when the worker process cannot
    spawn at all, the SAME manifest renders in-process — sequential
    (one at a time, like the legacy path; an in-process pool would
    soak cores at normal priority and break the §2 no-lag promise).
    Photos first, then clips.

    ``progress(done, total, name) -> keep_going`` is polled before
    each unit; returning False stops (the units already written stay
    — per-unit truth covers partial runs). Returns the unit-result
    messages, same shape the process streams."""
    reserver = _NameReserver()
    messages: list[dict] = []
    total = len(manifest.units) + len(manifest.clips)
    done = 0
    for unit in manifest.units:
        done += 1
        if progress is not None:
            try:
                keep_going = bool(progress(
                    done, total, Path(unit.source).name))
            except Exception:                               # noqa: BLE001
                keep_going = True
            if not keep_going:
                return messages
        messages.append(_render_unit(unit, manifest.collision, reserver))
    for clip in manifest.clips:
        done += 1
        if progress is not None:
            try:
                keep_going = bool(progress(
                    done, total, Path(clip.source).name))
            except Exception:                               # noqa: BLE001
                keep_going = True
            if not keep_going:
                return messages
        messages.append(
            _render_clip_unit(clip, manifest.collision, reserver))
    return messages


# ── the protocol + main ───────────────────────────────────────────────


def _emit(out: IO[str], message: dict) -> None:
    # ensure_ascii (the json default) keeps the wire pure ASCII —
    # accented paths survive any pipe encoding as \uXXXX escapes.
    out.write(json.dumps(message) + "\n")
    out.flush()


def worker_main(argv: list[str], out: Optional[IO[str]] = None) -> int:
    """The ``--render-worker`` entry. ``argv`` = [manifest_path].
    ``out`` is injectable for in-process tests; the real process uses
    stdout. Returns 0 when the job ran (failed units included — §5),
    2 when the manifest itself is unusable."""
    out = out if out is not None else sys.stdout
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    _lower_own_priority()

    try:
        if not argv:
            raise ValueError("usage: --render-worker <manifest.json>")
        manifest = ExportManifest.load(Path(argv[0]))
    except Exception as exc:  # noqa: BLE001
        _emit(out, {"type": "fatal", "error": str(exc)})
        return 2

    width = _pool_width(os.cpu_count(), _available_ram_bytes())
    total = len(manifest.units) + len(manifest.clips)
    _emit(out, {"type": "start", "total": total,
                "width": width, "clips": len(manifest.clips)})
    log.info("render worker: %d photo(s) + %d clip(s), width %d",
             len(manifest.units), len(manifest.clips), width)

    counts = {"ok": 0, "errors": 0, "skipped": 0}
    reserver = _NameReserver()
    # Photo lane (N-wide) + clip lane (1-wide) run side by side under
    # one as_completed loop (spec/60 §3 — when one lane empties the
    # other takes the full width; fast-path clips are pure ffmpeg/GPU
    # while photos soak the cores). Failures inside the executors
    # never bubble — _render_unit / _render_clip_unit return result
    # dicts on every path.
    with ThreadPoolExecutor(max_workers=width,
                            thread_name_prefix="photo") as photo_pool, \
            ThreadPoolExecutor(max_workers=1,
                               thread_name_prefix="clip") as clip_pool:
        futures = [
            photo_pool.submit(
                _render_unit, u, manifest.collision, reserver)
            for u in manifest.units
        ] + [
            clip_pool.submit(
                _render_clip_unit, c, manifest.collision, reserver)
            for c in manifest.clips
        ]
        for fut in as_completed(futures):
            msg = fut.result()
            key = {"ok": "ok", "error": "errors",
                   "skipped": "skipped"}[msg["status"]]
            counts[key] += 1
            _emit(out, msg)

    _emit(out, {"type": "done", **counts})
    log.info("render worker: done (%s)", counts)
    return 0


__all__ = ["run_manifest_inline", "worker_command", "worker_main"]
