"""FFmpeg wrappers for video probing, frame extraction and clip export.

The Process Videos workflow needs three FFmpeg operations:

1. **Probe** — read duration / dimensions / fps / codec so the player
   can size the timeline and the export can compute crop dimensions.
2. **Frame extract** — pull one exact frame at a marker position to
   the per-day ``extracted/`` folder so it can flow through the
   Process Photos Culler.
3. **Clip export** — write a re-encoded MP4 between two markers,
   optionally cropped to a different aspect ratio.

The bundled FFmpeg from ``imageio-ffmpeg`` is used in every case —
that way GoPro HEVC and other non-system codecs always work, with no
dependency on Windows Media Foundation.

This module is Qt-free and synchronous; the UI layer wraps these in
``QThread`` for long operations.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.proc import run as _run_hidden  # window-suppressed (core/proc)

import imageio_ffmpeg

log = logging.getLogger(__name__)


# Resolved once at import. ``imageio-ffmpeg`` lazy-extracts the binary
# on first call; subsequent calls return the cached path. Doing it
# here surfaces a clear error if the package isn't installed instead
# of failing deep inside ``probe_video``.
_FFMPEG_EXE: str = imageio_ffmpeg.get_ffmpeg_exe()


# Quality scale for JPEG output of frame extracts. FFmpeg uses 1-31
# where lower = higher quality. 2 is visually lossless and ~5x bigger
# than 31; we want lossless because these frames feed Process Photos.
_JPEG_QUALITY = 2

# x264 CRF for clip re-encode. 18-23 is the typical sweet spot; 20
# matches what most "high quality" presets use. Lower = bigger file.
_CLIP_CRF = 20

# Coarse-seek preroll for extract_frame's two-stage seek: how far before the target the
# fast input-side seek lands. Must comfortably exceed a typical GOP so the keyframe is found
# without decoding the whole file; 2 s is generous for camera/GoPro footage.
_FRAME_SEEK_PREROLL_S = 2.0


@dataclass(frozen=True)
class VideoMetadata:
    """What we need to know about a video before working with it.

    All durations in milliseconds (matches ``QMediaPlayer`` units).
    Fields default to safe zeros for the rare cases when ffmpeg
    can't parse a particular line — callers should treat duration=0
    as "unknown" rather than "empty file".

    ``rotation`` is the source's displaymatrix rotation (iPhone
    portrait videos = ±90, upside-down = 180). FFmpeg auto-rotates
    on decode, so the frame seen by ``-vf`` filters has dimensions
    swapped vs the encoded ``width`` / ``height`` when ``rotation``
    is 90 or 270. Use ``display_width`` / ``display_height`` for any
    crop math that runs against the filter input."""

    duration_ms: int
    width: int
    height: int
    fps: float
    codec: str
    rotation: int = 0

    @property
    def display_width(self) -> int:
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height


# Regex matchers for ``ffmpeg -i`` stderr. ffmpeg prints metadata to
# stderr (it's its log output, not its program output). We capture
# and pattern-match — we don't ship ffprobe with imageio-ffmpeg, so
# this is the lightweight alternative. Patterns are deliberately
# loose because ffmpeg's output format drifts between versions.
_RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)")
_RE_DIMENSIONS = re.compile(r"(\d{2,5})x(\d{2,5})")
_RE_FPS = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_RE_CODEC = re.compile(r"Video:\s*(\w+)")
# iPhone portrait MOVs print "displaymatrix: rotation of -90.00 degrees".
# Older containers (Android, dashcams) print "rotate          : 90"
# inside the stream Metadata block. Match either form.
_RE_ROTATION_DISPLAY = re.compile(
    r"displaymatrix:\s*rotation of (-?\d+(?:\.\d+)?)\s*degrees",
    re.IGNORECASE,
)
_RE_ROTATION_TAG = re.compile(r"\brotate\s*:\s*(-?\d+)", re.IGNORECASE)


def probe_video(video_path: Path, *, timeout: float = 10.0) -> VideoMetadata:
    """Read duration / dimensions / fps / codec from a video file.

    Uses ``ffmpeg -i`` and parses stderr — ``ffprobe`` would be more
    reliable but ``imageio-ffmpeg`` doesn't bundle it.

    Raises:
        FileNotFoundError: if ``video_path`` doesn't exist
        RuntimeError: if FFmpeg can't read the file at all (truncated,
            permission, etc.) — distinguished from a partially
            unparseable but readable file (which returns a
            ``VideoMetadata`` with zeros for the missing fields).
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    # ffmpeg with no output flag returns non-zero, but we still get
    # the metadata on stderr — that's the expected pattern here.
    result = _run_hidden(
        [_FFMPEG_EXE, "-hide_banner", "-i", str(video_path)],
        capture_output=True, text=True, timeout=timeout,
    )
    stderr = result.stderr or ""

    # If FFmpeg couldn't parse the file at all, stderr will say
    # "Invalid data found" or "moov atom not found" instead of the
    # input description. Detect the absence of the "Input #0" header.
    if "Input #0" not in stderr:
        raise RuntimeError(
            f"FFmpeg could not read {video_path.name}: "
            f"{stderr[:300] or '(no stderr)'}"
        )

    duration_ms = 0
    if m := _RE_DURATION.search(stderr):
        h, mn, s, frac = m.groups()
        # frac is the centiseconds (2 digits) by ffmpeg convention,
        # but some builds emit milliseconds (3 digits). Normalize
        # to ms regardless.
        frac_ms = int(frac.ljust(3, "0")[:3])
        duration_ms = (
            int(h) * 3_600_000
            + int(mn) * 60_000
            + int(s) * 1_000
            + frac_ms
        )

    width = height = 0
    if m := _RE_DIMENSIONS.search(stderr):
        width = int(m.group(1))
        height = int(m.group(2))

    fps = 0.0
    if m := _RE_FPS.search(stderr):
        fps = float(m.group(1))

    codec = ""
    if m := _RE_CODEC.search(stderr):
        codec = m.group(1)

    # Rotation: prefer the displaymatrix side-data line (modern
    # containers, iPhone) and fall back to the "rotate" metadata tag
    # (older format). Negative rotations are normalised into the
    # 0/90/180/270 range so callers don't have to handle signs.
    rotation = 0
    if m := _RE_ROTATION_DISPLAY.search(stderr):
        rotation = int(round(float(m.group(1)))) % 360
    elif m := _RE_ROTATION_TAG.search(stderr):
        rotation = int(m.group(1)) % 360

    log.debug(
        "probed %s: %dx%d %.2ffps %s rotation=%d duration=%dms",
        video_path.name, width, height, fps, codec, rotation, duration_ms,
    )
    return VideoMetadata(
        duration_ms=duration_ms, width=width, height=height,
        fps=fps, codec=codec, rotation=rotation,
    )


def extract_frame(
    video_path: Path,
    position_ms: int,
    output_path: Path,
    *,
    timeout: float = 60.0,
) -> Path:
    """Extract a single frame at exact ``position_ms`` and save as JPEG.

    **Two-stage seek (fast + frame-accurate).** A coarse input-side seek (``-ss`` *before*
    ``-i``) jumps to the keyframe ~``_FRAME_SEEK_PREROLL_S`` before the target, then a fine
    output-side seek (``-ss`` *after* ``-i``) advances the small remainder to the exact frame.
    Pure output-side seek (the old path) decodes the video **from the start** to the position —
    a snapshot 40 s into a 4K clip decoded 40 s for one frame (Nelson 2026-06-03, the ~12 min
    Nepal materialise). This decodes only ~2 s regardless of position while landing on exactly
    the requested frame (the user paused the player there for a reason). For early positions
    (< preroll) it collapses to a plain fast output-seek, unchanged.

    Returns the output path (same as input ``output_path`` — for chaining).

    Raises:
        FileNotFoundError: if the source video is missing
        RuntimeError: if FFmpeg's exit code is non-zero
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    seconds = position_ms / 1000.0
    pre = max(0.0, seconds - _FRAME_SEEK_PREROLL_S)   # coarse input seek (keyframe ≤ pre)
    fine = seconds - pre                              # fine output seek (== min(seconds, preroll))
    cmd = [_FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error"]
    if pre > 0:
        cmd += ["-ss", f"{pre:.3f}"]                  # BEFORE -i: fast jump near the target
    cmd += [
        "-i", str(video_path),
        "-ss", f"{fine:.3f}",                         # AFTER -i: exact landing on the frame
        "-frames:v", "1",
        "-q:v", str(_JPEG_QUALITY),
        str(output_path),
    ]
    result = _run_hidden(
        cmd, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg frame extract failed for {video_path.name} @ "
            f"{position_ms}ms: {result.stderr[:500]}"
        )
    log.info("extracted frame %s @ %dms -> %s",
             video_path.name, position_ms, output_path)
    return output_path


def export_clip(
    video_path: Path,
    start_ms: int,
    end_ms: int,
    output_path: Path,
    *,
    crop_pixels: Optional[tuple[int, int, int, int]] = None,
    include_audio: bool = True,
    rotation_degrees: int = 0,
    crf: int = _CLIP_CRF,
    timeout: float = 600.0,
) -> Path:
    """Export ``[start_ms, end_ms)`` of ``video_path`` to ``output_path``.

    Re-encodes with libx264 + AAC. Stream copy (``-c copy``) would be
    faster but snaps to the nearest keyframe at the start, typically
    ~1s of imprecision — bad for cuts the user picked manually.

    ``crop_pixels`` is ``(x, y, width, height)`` in source-image
    coordinates (post-rotation if any). Pass ``None`` for no crop.

    ``include_audio`` toggles between an AAC-encoded audio track
    (default) and a silent-only output (``-an``). Useful for clips
    where ambient audio is distracting or copyrighted music would
    block sharing.

    Output gets ``+faststart`` so MP4 plays smoothly when streamed
    or read partially (the moov atom moves to the front).

    Raises:
        FileNotFoundError: if the source video is missing
        ValueError: if the time range is invalid (end <= start)
        RuntimeError: on FFmpeg failure
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if end_ms <= start_ms:
        raise ValueError(
            f"Invalid clip range: start={start_ms}ms end={end_ms}ms"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    start_s = start_ms / 1000.0
    duration_s = (end_ms - start_ms) / 1000.0

    # Input-side -ss is fast but lands on the keyframe before the
    # request; output-side -ss is exact. Putting -ss before -i AND
    # -t after -i hits the sweet spot: fast input seek to a nearby
    # keyframe, then the encoder produces frames starting at the
    # requested position because we re-encode anyway.
    cmd: list[str] = [
        _FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_s:.3f}",
        "-i", str(video_path),
        "-t", f"{duration_s:.3f}",
    ]
    # Build the -vf filter chain. Crop runs first in source-pixel
    # coordinates; rotation runs after so the user composes the
    # crop on the orientation they see in the player and rotation
    # is applied to the already-cropped region. Same semantics as
    # Process Photos (crop in source space, rotate on export).
    filter_chain: list[str] = []
    if crop_pixels is not None:
        x, y, w, h = crop_pixels
        if w <= 0 or h <= 0:
            raise ValueError(f"Invalid crop dimensions: {crop_pixels}")
        filter_chain.append(f"crop={w}:{h}:{x}:{y}")
    # FFmpeg transpose codes:
    #   1 = 90° clockwise, 2 = 90° counter-clockwise.
    # 180° is two clockwise transposes — equivalent to hflip+vflip
    # but transpose doesn't fight with the YUV420 chroma siting on
    # odd-dimension sources the way flip can.
    rot = rotation_degrees % 360
    if rot == 90:
        filter_chain.append("transpose=1")
    elif rot == 270:  # also covers -90
        filter_chain.append("transpose=2")
    elif rot == 180:
        filter_chain.extend(["transpose=1", "transpose=1"])
    elif rot != 0:
        raise ValueError(
            f"rotation_degrees must be a multiple of 90 (got {rotation_degrees})"
        )
    if filter_chain:
        cmd += ["-vf", ",".join(filter_chain)]
    cmd += [
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", "medium",
        "-pix_fmt", "yuv420p",  # broadest player compatibility
    ]
    if include_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    else:
        # ``-an`` strips the audio stream entirely. Smaller output
        # and avoids any encode-time audio sync issues.
        cmd += ["-an"]
    cmd += [
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = _run_hidden(
        cmd, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg clip export failed for {video_path.name} "
            f"[{start_ms}, {end_ms}]ms: {result.stderr[:500]}"
        )
    log.info("exported clip %s [%d, %d]ms -> %s",
             video_path.name, start_ms, end_ms, output_path)
    return output_path


def copy_clip(
    video_path: Path,
    start_ms: int,
    end_ms: int,
    output_path: Path,
    *,
    timeout: float = 600.0,
) -> Path:
    """Stream-copy ``[start_ms, end_ms)`` of ``video_path`` to ``output_path`` — NO
    re-encode (``-c copy``). The fast path for **Cull-exit materialisation**, where a clip
    is a pure trim of the master (no crop / rotation / colour — those belong to Process).

    Trade-off vs :func:`export_clip`: stream copy snaps the start to the keyframe at or
    *before* ``start_ms``, so the output is a slight **superset** of the requested range
    (≈0.5–1 s of pre-roll, no frames lost). That is exactly what we want at Cull — the master
    disappears after this, and Process can tighten the cut precisely within the copied clip.
    Runs at ~20–50× realtime and is light on CPU (demux/remux only), so the background
    materialiser never competes with the culler.

    ``-avoid_negative_ts make_zero`` rebases timestamps so the clip plays from 0;
    ``+faststart`` moves the moov atom to the front for smooth scrubbing.

    Raises:
        FileNotFoundError: if the source video is missing
        ValueError: if the time range is invalid (end <= start)
        RuntimeError: on FFmpeg failure
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if end_ms <= start_ms:
        raise ValueError(f"Invalid clip range: start={start_ms}ms end={end_ms}ms")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    start_s = start_ms / 1000.0
    duration_s = (end_ms - start_ms) / 1000.0
    # -ss before -i = fast input seek to the keyframe ≤ start; -t after = copy that span.
    cmd = [
        _FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_s:.3f}",
        "-i", str(video_path),
        "-t", f"{duration_s:.3f}",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = _run_hidden(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg stream-copy failed for {video_path.name} "
            f"[{start_ms}, {end_ms}]ms: {result.stderr[:500]}"
        )
    log.info("stream-copied clip %s [%d, %d]ms -> %s",
             video_path.name, start_ms, end_ms, output_path)
    return output_path


def _make_test_video(
    path: Path, *,
    duration_s: float = 1.0,
    color: str = "red",
    size: str = "320x240",
    fps: int = 30,
) -> Path:
    """Synthesize a flat-color video for tests. Public-ish helper —
    leading underscore signals "tests use this, app code shouldn't"."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c={color}:s={size}:d={duration_s}:r={fps}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(path),
    ]
    _run_hidden(cmd, check=True, capture_output=True, timeout=30)
    return path
