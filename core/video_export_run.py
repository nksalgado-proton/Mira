"""Runner for the Process clip export (docs/26 §6–7, Phase 4).

Executes an :class:`~core.video_export.ExportPlan` against a source video,
producing the materialised clip. Kept separate from ``core/video_export.py``
(the pure plan resolver) because this module pulls in ffmpeg, numpy and live
subprocess piping.

**Pipeline (exact-colour, docs/26 §7a):**

```
[vidstabdetect pass 1]  →  transforms.trf          (only if stabilise on)

ffmpeg DECODE  ── rawvideo rgb24 ──►  python per-frame  ── rawvideo rgb24 ──►  ffmpeg ENCODE
  -ss/-t trim                          apply_params       -r src_fps             setpts=PTS/speed
  [vidstabtransform]                   extract_rotated_      (+ audio from a       atempo/volume/afade
                                       crop / crop           2nd trimmed input)    libx264 + faststart
```

Colour + crop + Box-Rotation run in numpy with the **same**
:func:`core.photo_render.apply_params` / :func:`~core.photo_render.
extract_rotated_crop` the sub-surface previews with, so the exported look is
identical to the preview by construction. ffmpeg owns decode, stabilisation,
speed and audio.
"""

from __future__ import annotations

import collections
import logging
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from core.photo_render import (
    FilterRecipe,
    apply_crop_norm,
    apply_filter,
    apply_params,
    extract_rotated_crop,
)
from core.proc import run as _run_hidden
from core.video_export import ExportPlan
from core.video_extract import _FFMPEG_EXE, probe_video

log = logging.getLogger(__name__)

_CLIP_CRF = 20
# x264 speed/quality trade-off. Trip clips don't need archival compression;
# ``veryfast`` is ~3-4× quicker than ``medium`` for a small size bump and no
# visible quality loss at CRF 20.
_X264_PRESET = "veryfast"
# Detected once: the video-encoder portion of the ffmpeg command. Prefers
# NVIDIA NVENC (h264_nvenc) when the bundled ffmpeg + a working GPU/driver are
# present — dramatically faster than CPU libx264 — and falls back to libx264
# everywhere else. See _video_encoder_args.
_ENCODER_ARGS_CACHE: Optional[list] = None
# Progress is reported per N frames so a long clip doesn't flood the callback.
_PROGRESS_EVERY = 5
# Parallel workers for the per-frame numpy colour/crop stage. apply_params is
# vectorised numpy that releases the GIL, so threads scale across cores. Capped
# to leave headroom for the decode/encode ffmpeg processes.
_NUMPY_WORKERS = max(1, min((os.cpu_count() or 4) - 1, 8))

ProgressCb = Callable[[int, int], bool]   # (done_frames, total_frames) -> keep?
#: spec/139 §2 — read-only fraction sink. ``export_processed_clip``
#: calls this on every frame-progress tick with ``done_frames /
#: total_frames`` so the UI can paint a per-file progress bar that
#: actually MOVES during a long encode (the aggregate bar only
#: advances on file completion). Sink is fire-and-forget — never
#: drives cancel (that stays with ``progress``).
FileFractionCb = Callable[[float], None]


def export_processed_clip(
    video_path: Path,
    output_path: Path,
    plan: ExportPlan,
    *,
    progress: Optional[ProgressCb] = None,
    on_file_fraction: Optional[FileFractionCb] = None,
    timeout: float = 1800.0,
) -> Path:
    """Materialise ``video_path``'s ``plan`` to ``output_path`` (re-encoded
    MP4). Returns ``output_path``. Raises ``FileNotFoundError`` /
    ``RuntimeError`` on bad input / ffmpeg failure. ``progress(done, total)``
    is polled per few frames; returning ``False`` cancels (a partial file is
    removed). ``on_file_fraction(0.0..1.0)`` is called on every progress
    tick — spec/139 §2 — so the host can show per-file motion during a
    long encode (purely informational; never cancels)."""
    video_path = Path(video_path)
    output_path = Path(output_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    meta = probe_video(video_path)
    src_w = int(meta.display_width or meta.width or 0)
    src_h = int(meta.display_height or meta.height or 0)
    if src_w <= 0 or src_h <= 0:
        raise RuntimeError(f"Could not probe dimensions for {video_path.name}")

    in_s = plan.in_ms / 1000.0
    dur_s = plan.duration_ms / 1000.0
    if dur_s <= 0:
        raise ValueError("Export plan has non-positive duration")

    with tempfile.TemporaryDirectory(prefix="mc_vexport_") as td:
        tmp = Path(td)
        vidstab_vf: list[str] = []
        if plan.stabilise_on:
            vidstab_vf = _vidstab_detect_and_transform(
                video_path, in_s, dur_s, plan.stabilise, tmp, timeout)

        # Fast path: nothing needs our per-frame numpy work (no colour, no
        # crop, no Box-Rotation) → let ffmpeg do it all in one pass (trim +
        # stabilise + speed + audio), skipping the rawvideo round-trip
        # entirely. Much faster for trim-only / mute / speed / stabilise.
        if not plan.has_colour and not plan.has_crop:
            return _run_ffmpeg_only(
                video_path, output_path, plan,
                in_s=in_s, dur_s=dur_s, decode_vf=vidstab_vf,
                progress=progress, on_file_fraction=on_file_fraction,
                timeout=timeout)

        return _run_pipe(
            video_path, output_path, plan,
            src_w=src_w, src_h=src_h, in_s=in_s, dur_s=dur_s,
            decode_vf=vidstab_vf, progress=progress,
            on_file_fraction=on_file_fraction, timeout=timeout)


# ── Stabilisation (two-pass) ──────────────────────────────────────────


def _vidstab_detect_and_transform(
    video_path: Path, in_s: float, dur_s: float, strength: float,
    tmp: Path, timeout: float,
) -> list[str]:
    """Run vidstabdetect (pass 1) over the trimmed range and return the
    vidstabtransform ``-vf`` token (pass 2) for the decode stage.

    ``strength`` is the schema's normalised ``[0, 1]`` value (the column
    contract — see ``VideoAdjustment.stabilise``). We re-scale to the
    vidstab parameter ranges here:

    * **shakiness** ``1..10`` — how much shake to look for
    * **smoothing** ``1..60`` — how aggressively to smooth the result

    Previously the math treated ``strength`` as ``[1, 100]`` which always
    rounded to the minimum for any real input — fixed 2026-06-09."""
    trf = tmp / "transforms.trf"
    s = max(0.0, min(1.0, float(strength)))
    shakiness = max(1, min(10, int(round(s * 10)) or 1))
    detect_cmd = [
        _FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{in_s:.3f}", "-i", str(video_path), "-t", f"{dur_s:.3f}",
        "-vf", f"vidstabdetect=shakiness={shakiness}:result={_ff(trf)}",
        "-f", "null", "-",
    ]
    res = _run_hidden(detect_cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0 or not trf.exists():
        log.warning("vidstabdetect failed (%s) — exporting without stabilise",
                    (res.stderr or "")[:300])
        return []
    smoothing = max(1, min(60, int(round(s * 50)) or 1))
    return [f"vidstabtransform=input={_ff(trf)}:smoothing={smoothing}"]


# ── The decode → numpy → encode pipe ──────────────────────────────────


def _run_pipe(
    video_path: Path, output_path: Path, plan: ExportPlan, *,
    src_w: int, src_h: int, in_s: float, dur_s: float,
    decode_vf: list[str], progress: Optional[ProgressCb],
    on_file_fraction: Optional[FileFractionCb], timeout: float,
) -> Path:
    decode_cmd = [
        _FFMPEG_EXE, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{in_s:.3f}", "-i", str(video_path), "-t", f"{dur_s:.3f}",
    ]
    if decode_vf:
        decode_cmd += ["-vf", ",".join(decode_vf)]
    decode_cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]

    total_frames = max(1, int(round(dur_s * plan.src_fps)))
    frame_bytes = src_w * src_h * 3

    decode = subprocess.Popen(
        decode_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        **_no_window())
    encode: Optional[subprocess.Popen] = None
    done = 0
    cancelled = False
    # The per-frame numpy colour/crop runs in a thread pool (apply_params
    # releases the GIL). We keep a bounded look-ahead of in-flight frames
    # and write results back in submission order so the encoder sees an
    # ordered stream. Look-ahead is capped to bound memory (rgb24 frames
    # are large at 4K).
    max_inflight = _NUMPY_WORKERS * 2
    pending: "collections.deque" = collections.deque()
    try:
        with ThreadPoolExecutor(max_workers=_NUMPY_WORKERS) as pool:
            def _submit_next() -> bool:
                raw = _read_exact(decode.stdout, frame_bytes)
                if raw is None:
                    return False
                frame = np.frombuffer(
                    raw, dtype=np.uint8).reshape(src_h, src_w, 3)
                pending.append(pool.submit(_process_frame, frame, plan))
                return True

            for _ in range(max_inflight):
                if not _submit_next():
                    break
            while pending:
                out = pending.popleft().result()
                if encode is None:
                    out_h, out_w = out.shape[:2]
                    encode = _start_encode(
                        video_path, output_path, plan,
                        out_w=out_w, out_h=out_h, in_s=in_s, dur_s=dur_s)
                encode.stdin.write(np.ascontiguousarray(out).tobytes())
                done += 1
                if done % _PROGRESS_EVERY == 0:
                    # spec/139 §2 — surface the fraction to the host
                    # FIRST so the per-file bar paints fresh data even
                    # if the cancel callback returns False on this tick.
                    if on_file_fraction is not None:
                        try:
                            on_file_fraction(done / max(1, total_frames))
                        except Exception:                      # noqa: BLE001
                            pass        # never let UI sink crash encode
                    if progress is not None:
                        if not progress(done, total_frames):
                            cancelled = True
                            break
                _submit_next()                 # keep the pipeline full
    finally:
        _finish(decode, encode, cancelled)

    if cancelled:
        output_path.unlink(missing_ok=True)
        raise _Cancelled()
    if encode is None:
        raise RuntimeError(f"No frames decoded from {video_path.name}")
    if encode.returncode not in (0, None):
        raise RuntimeError(
            f"FFmpeg encode failed for {video_path.name} (rc={encode.returncode})")
    if progress is not None:
        progress(total_frames, total_frames)
    if on_file_fraction is not None:
        try:
            on_file_fraction(1.0)
        except Exception:                                          # noqa: BLE001
            pass
    log.info("exported processed clip %s [%d,%d]ms -> %s",
             video_path.name, plan.in_ms, plan.out_ms, output_path)
    return output_path


def _process_frame(frame: np.ndarray, plan: ExportPlan) -> np.ndarray:
    """Apply the exact colour + crop/Box-Rotation a frame would get in the
    preview. Order matches the sub-surface render: colour first, then
    the spec/55 creative filter, then crop."""
    out = frame
    if plan.has_colour:
        out = apply_params(out, plan.params)
    if plan.filter_recipe is not None:
        # spec/116 §2 — videos have no per-frame AF metadata that
        # rides cleanly through ffmpeg / decode, so v1 falls back to
        # the frame-centre Spotlight anchor. ``center`` is wired
        # explicitly to keep the contract visible: a future evolution
        # could pass the source clip's first-frame AF point here.
        out = apply_filter(
            out, FilterRecipe.from_dict(plan.filter_recipe),
            plan.filter_amount, center=(0.5, 0.5))
    if plan.crop_norm is not None or abs(plan.box_angle) > 1e-3:
        rect = plan.crop_norm if plan.crop_norm is not None else (0.0, 0.0, 1.0, 1.0)
        if abs(plan.box_angle) > 1e-3:
            out = extract_rotated_crop(out, rect, plan.box_angle)
        elif plan.crop_norm is not None:
            out = np.ascontiguousarray(apply_crop_norm(out, rect))
    # libx264 + yuv420p needs even dimensions; trim a row/col if odd.
    h, w = out.shape[:2]
    if w % 2 or h % 2:
        out = out[: h - (h % 2), : w - (w % 2)]
    return out


def _start_encode(
    video_path: Path, output_path: Path, plan: ExportPlan, *,
    out_w: int, out_h: int, in_s: float, dur_s: float,
) -> subprocess.Popen:
    cmd = [
        _FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{out_w}x{out_h}", "-r", f"{plan.src_fps:g}",
        "-i", "pipe:0",
    ]
    want_audio = plan.include_audio
    if want_audio:
        cmd += ["-ss", f"{in_s:.3f}", "-i", str(video_path), "-t", f"{dur_s:.3f}"]

    vf = []
    if abs(plan.speed - 1.0) > 1e-6:
        vf.append(f"setpts=PTS/{plan.speed:g}")
    if vf:
        cmd += ["-filter:v", ",".join(vf)]

    if want_audio:
        af = _audio_filters(plan)
        if af:
            cmd += ["-filter:a", ",".join(af)]
        cmd += ["-map", "0:v", "-map", "1:a?"]
    else:
        cmd += ["-map", "0:v", "-an"]

    cmd += _video_encoder_args()
    if want_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", str(output_path)]
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, **_no_window())


def _run_ffmpeg_only(
    video_path: Path, output_path: Path, plan: ExportPlan, *,
    in_s: float, dur_s: float, decode_vf: list[str],
    progress: Optional[ProgressCb],
    on_file_fraction: Optional[FileFractionCb], timeout: float,
) -> Path:
    """Fast path for clips with no colour and no crop/Box-Rotation: a single
    ffmpeg pass (trim + stabilise + speed + audio) — no rawvideo round-trip,
    no numpy. Same encoder settings as the pipe so output matches."""
    cmd = [
        _FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{in_s:.3f}", "-i", str(video_path), "-t", f"{dur_s:.3f}",
    ]
    vf = list(decode_vf)
    if abs(plan.speed - 1.0) > 1e-6:
        vf.append(f"setpts=PTS/{plan.speed:g}")
    if vf:
        cmd += ["-filter:v", ",".join(vf)]
    if plan.include_audio:
        af = _audio_filters(plan)
        if af:
            cmd += ["-filter:a", ",".join(af)]
    else:
        cmd += ["-an"]
    cmd += _video_encoder_args()
    if plan.include_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", str(output_path)]

    if progress is not None and not progress(0, 1):
        raise _Cancelled()
    # Popen + poll so a long fast-path export is still cancellable: every
    # 100ms we re-check the progress callback (which carries the cancel
    # flag) and kill ffmpeg if asked to stop.
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **_no_window())
    while True:
        try:
            proc.wait(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            if progress is not None and not progress(0, 1):
                proc.kill()
                proc.wait()
                output_path.unlink(missing_ok=True)
                raise _Cancelled()
    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else b""
        raise RuntimeError(
            f"FFmpeg fast-path export failed for {video_path.name}: "
            f"{err.decode('utf-8', 'replace')[:500]}")
    if progress is not None:
        progress(1, 1)
    if on_file_fraction is not None:
        # spec/139 §2 — the fast path is single-shot ffmpeg (no
        # frame-by-frame poll), so the most honest fraction signal is
        # 0→1 on completion. Photos already snap; this matches.
        try:
            on_file_fraction(1.0)
        except Exception:                                          # noqa: BLE001
            pass
    log.info("exported processed clip (fast path) %s [%d,%d]ms -> %s",
             video_path.name, plan.in_ms, plan.out_ms, output_path)
    return output_path


def _video_encoder_args() -> list[str]:
    """The ffmpeg ``-c:v …`` portion for the clip encode — delegates
    to the spec/60 §4 encoder ladder (NVENC → QSV → AMF → libx264).
    Cached at module scope; the ladder also caches per process."""
    global _ENCODER_ARGS_CACHE
    if _ENCODER_ARGS_CACHE is None:
        from core.encoder_ladder import detect_encoder_args
        _ENCODER_ARGS_CACHE = detect_encoder_args()
    return list(_ENCODER_ARGS_CACHE)


def _audio_filters(plan: ExportPlan) -> list[str]:
    af: list[str] = []
    if abs(plan.speed - 1.0) > 1e-6:
        af += _atempo_chain(plan.speed)
    if abs(plan.audio_volume - 1.0) > 1e-6:
        af.append(f"volume={plan.audio_volume:g}")
    if plan.audio_fade_ms > 0:
        fade_s = plan.audio_fade_ms / 1000.0
        out_dur = plan.duration_ms / 1000.0 / max(0.01, plan.speed)
        af.append(f"afade=t=in:st=0:d={fade_s:.3f}")
        af.append(
            f"afade=t=out:st={max(0.0, out_dur - fade_s):.3f}:d={fade_s:.3f}")
    return af


def _atempo_chain(speed: float) -> list[str]:
    """atempo accepts 0.5..2.0 per stage — chain for factors outside that."""
    stages: list[float] = []
    s = float(speed)
    while s > 2.0 + 1e-9:
        stages.append(2.0)
        s /= 2.0
    while s < 0.5 - 1e-9:
        stages.append(0.5)
        s /= 0.5
    stages.append(s)
    return [f"atempo={x:.6g}" for x in stages]


# ── subprocess plumbing ───────────────────────────────────────────────


class _Cancelled(RuntimeError):
    """Raised internally when the progress callback asks to stop."""


def _read_exact(stream, n: int) -> Optional[bytes]:
    """Read exactly ``n`` bytes from a pipe; ``None`` at clean EOF. A short
    final read (truncated frame) also returns ``None``."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None if not buf else None
        buf += chunk
    return bytes(buf)


def _finish(decode, encode, cancelled: bool) -> None:
    try:
        if decode.stdout:
            decode.stdout.close()
    except OSError:
        pass
    if cancelled:
        decode.kill()
        if encode is not None:
            encode.kill()
        decode.wait()
        if encode is not None:
            encode.wait()
        return
    decode.wait()
    if encode is not None:
        try:
            if encode.stdin:
                encode.stdin.close()
        except OSError:
            pass
        encode.wait()


def _no_window() -> dict:
    """Suppress the console window on Windows (mirrors core/proc)."""
    import sys
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _ff(path: Path) -> str:
    """ffmpeg filter args don't like backslashes / colons — normalise the
    transforms-file path to forward slashes and escape the drive colon."""
    s = str(path).replace("\\", "/")
    return s.replace(":", "\\:")


__all__ = ["export_processed_clip"]
