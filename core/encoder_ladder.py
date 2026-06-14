"""The spec/60 §4 video encoder ladder — probe → cache → ffmpeg args.

Every encoder in the ladder is verified by an actual small test encode
(being LISTED in ``ffmpeg -encoders`` doesn't mean the GPU/driver is
working). First hit wins; the result caches per process so a batch of
N clips probes once.

Order: NVIDIA NVENC → Intel Quick Sync (QSV) → AMD AMF → CPU libx264
(the floor — always works). Correctness is identical across encoders;
hardware changes speed and the byte stream, never the look — the same
numpy colour math runs upstream of every option (spec/60 §4).

Pure logic — no Qt.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.proc import run as _run_hidden
from core.video_extract import _FFMPEG_EXE

log = logging.getLogger(__name__)

# Cached per process. None = not yet probed.
_LADDER_CACHE: Optional[dict] = None

# Defaults — match the historical clip CRF + a hardware preset that
# trades a small size bump for speed (preview-grade trip clips).
_CPU_CRF = 20
_X264_PRESET = "veryfast"


def _test_encode(codec: str, extra: list[str]) -> bool:
    """Run a tiny synthetic encode through ``codec``; True only on
    exit 0. NVENC rejects frames smaller than 256×256 with "Frame
    Dimension less than the minimum supported value", so the test
    source is 256×256 for every codec — keeps the probe uniform."""
    cmd = [
        _FFMPEG_EXE, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1:r=10",
        "-c:v", codec, *extra, "-f", "null", "-",
    ]
    try:
        r = _run_hidden(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:                                       # noqa: BLE001
        return False


def _detect() -> dict:
    """Probe the ladder. Returns ``{"name": <str>, "args": [...]}``
    — the ffmpeg ``-c:v …`` portion ready to splice into a clip
    encode. Logs ONE calm INFO line per session with what the
    machine chose (§4)."""
    # NVIDIA NVENC — explicit preset/rc/cq from the historical config.
    if _test_encode("h264_nvenc", []):
        log.info("encoder ladder: using NVENC (h264_nvenc)")
        return {"name": "nvenc", "args": [
            "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
            "-cq", "23", "-pix_fmt", "yuv420p",
        ]}

    # Intel Quick Sync — VBR with global_quality matches the NVENC
    # quality target (CQ 23 ≈ ICQ 23 within yuv420p).
    if _test_encode("h264_qsv", []):
        log.info("encoder ladder: using Intel QSV (h264_qsv)")
        return {"name": "qsv", "args": [
            "-c:v", "h264_qsv", "-preset", "medium",
            "-global_quality", "23", "-pix_fmt", "nv12",
        ]}

    # AMD AMF — CQP at 23 mirrors the others' quality target.
    if _test_encode("h264_amf", []):
        log.info("encoder ladder: using AMD AMF (h264_amf)")
        return {"name": "amf", "args": [
            "-c:v", "h264_amf", "-quality", "balanced",
            "-rc", "cqp", "-qp_i", "23", "-qp_p", "23",
            "-pix_fmt", "yuv420p",
        ]}

    # CPU floor (§4 last resort for the encoder lane — every machine
    # completes every job). User-tunable CRF via Settings, with fallback.
    try:
        from mira.settings.repo import SettingsRepo
        crf = int(SettingsRepo().load().video_clip_crf)
    except Exception:                                       # noqa: BLE001
        crf = _CPU_CRF
    log.info("encoder ladder: using libx264 (no working HW encoder)")
    return {"name": "libx264", "args": [
        "-c:v", "libx264", "-crf", str(crf), "-preset", _X264_PRESET,
        "-pix_fmt", "yuv420p",
    ]}


def detect_encoder() -> dict:
    """Cached :func:`_detect` — first call probes, the rest are O(1)."""
    global _LADDER_CACHE
    if _LADDER_CACHE is None:
        _LADDER_CACHE = _detect()
    return dict(_LADDER_CACHE, args=list(_LADDER_CACHE["args"]))


def detect_encoder_args() -> list[str]:
    """The ``-c:v …`` portion — what :mod:`core.video_export_run`
    splices into the encode command line."""
    return detect_encoder()["args"]


def _reset_cache_for_tests() -> None:
    """Probe again on next call — used only by the ladder tests."""
    global _LADDER_CACHE
    _LADDER_CACHE = None


__all__ = ["detect_encoder", "detect_encoder_args"]
