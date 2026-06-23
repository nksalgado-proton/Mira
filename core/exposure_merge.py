"""Qt-free engine for the in-app exposure-bracket merge (spec/109 §3).

The Picker's "Combined" toggle and the materialized in-app merge share
``core.exposure_fusion.fuse_exposure_arrays``. This module wraps that
kernel with the **decode + write-scratch** plumbing the merge job (the
``mira/`` layer's batch-engine adapter) needs:

* :func:`fuse_bracket_request` — decode each member's full-res frame
  (JPEG / RAW / HEIC via :func:`core.photo_decoder.decode_image`),
  Mertens-fuse with optional ``cv2.AlignMTB`` pre-align, write a
  scratch TIFF, return its path.
* :func:`run_exposure_merge` — orchestrates a list of requests with a
  progress callback + cancel poll. The batch-queue adapter
  (``mira/ui/...``) is engine-agnostic and feeds this through
  :class:`mira.ui.ingest.ingest_job.IngestJob`.

Charter inv. #8: pure logic. No Qt imports. The output format default
is **high-quality TIFF** (spec/109 §6 — clean develop source, not a
re-compressed JPEG); the merged file is adopted into
``Original Media/Merged/`` by ``EventGateway.adopt_stack_output``.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np
from PIL import Image

from core.exposure_fusion import fuse_exposure_arrays
from core.photo_decoder import decode_image

log = logging.getLogger(__name__)


#: Engine-side progress: ``(done_units, total_units, message)``. The
#: batch-queue adapter relays these to the under-menubar progress line.
ProgressCb = Callable[[int, int, str], None]
ShouldCancel = Callable[[], bool]


@dataclass
class ExposureMergeRequest:
    """One bracket to merge. Member paths must be full-res files under
    ``Original Media/`` — the engine decodes them at native resolution.
    ``member_item_ids`` flows through to ``adopt_stack_output`` so the
    ``stack_member`` rows wire up correctly."""

    bracket_key: str
    bracket_kind: str            # "exposure_bracket" or "exposure"
    member_paths: List[Path]
    member_item_ids: List[str]
    label: str = ""              # for progress messages / logs


@dataclass
class ExposureMergeResult:
    """One bracket's outcome. ``scratch_path`` is the file the caller
    feeds to ``EventGateway.adopt_stack_output``; ``error`` is non-None
    when the bracket failed (skipped, not fatal — other brackets in the
    batch still run)."""

    request: ExposureMergeRequest
    scratch_path: Optional[Path] = None
    error: Optional[str] = None
    cancelled: bool = False


def _rgb_to_bgr(rgb: np.ndarray) -> np.ndarray:
    """RGB uint8 → BGR uint8 (``cv2.createMergeMertens`` is BGR-native,
    matching the Picker preview's pixmap-to-BGR path). Cheap channel
    flip; no copy when the array is already contiguous on the last axis."""
    return np.ascontiguousarray(rgb[:, :, ::-1])


def _scratch_path_for(
    request: ExposureMergeRequest, scratch_dir: Path,
) -> Path:
    """Pick a stable scratch filename. The TIFF stem encodes the
    bracket key + first member's stem so ``adopt_stack_output`` lands a
    meaningful filename on disk (it preserves the source filename,
    spec/109 §6 — no new naming rule)."""
    anchor_stem = (request.member_paths[0].stem
                   if request.member_paths else request.bracket_key)
    safe_stem = "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in f"{anchor_stem}_merged"
    )
    return scratch_dir / f"{safe_stem}.tif"


def _write_tiff(bgr: np.ndarray, dest: Path) -> None:
    """Write a BGR uint8 fusion result as a high-quality TIFF
    (spec/109 §6 — clean develop source). LZW for lossless compression;
    8-bit per channel for now (16-bit is a later option per §6.1)."""
    rgb = bgr[:, :, ::-1]
    rgb = np.ascontiguousarray(rgb)
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, mode="RGB").save(
        str(dest), format="TIFF", compression="tiff_lzw",
    )


def fuse_bracket_request(
    request: ExposureMergeRequest,
    *,
    scratch_dir: Path,
    align: bool = True,
) -> Path:
    """Fuse one bracket: decode each member, run Mertens, write a TIFF.

    Returns the scratch file's path. Raises on decode failure (callers
    treat it as a per-bracket skip; the batch loop catches and records
    the error)."""
    arrays: List[np.ndarray] = []
    for p in request.member_paths:
        rgb = decode_image(p)
        arrays.append(_rgb_to_bgr(rgb))
    fused = fuse_exposure_arrays(arrays, align=align)
    if fused.size == 0:
        raise RuntimeError(
            f"bracket {request.bracket_key}: fusion produced no pixels")
    out = _scratch_path_for(request, scratch_dir)
    _write_tiff(fused, out)
    return out


def run_exposure_merge(
    requests: List[ExposureMergeRequest],
    *,
    scratch_dir: Optional[Path] = None,
    align: bool = True,
    progress_cb: ProgressCb = lambda *_: None,
    should_cancel: ShouldCancel = lambda: False,
) -> List[ExposureMergeResult]:
    """Run a batch of bracket merges. Per-bracket failures are recorded
    on the result and the loop continues to the next bracket — one bad
    file never kills the whole batch (mirrors the spec/57 §3 scan).

    Cancel is polled between brackets, not mid-decode: half-decoded
    brackets are cheap to abandon, but a half-written TIFF would need
    its own cleanup, so we stop AT the next bracket boundary.

    ``scratch_dir`` defaults to a fresh ``tempfile.mkdtemp`` directory;
    the caller is responsible for cleanup AFTER adoption succeeds
    (adopt deletes the source on copy + sha verify, but a per-bracket
    failure leaves its scratch behind for inspection)."""
    if scratch_dir is None:
        scratch_dir = Path(tempfile.mkdtemp(prefix="mira_exposure_merge_"))
    else:
        scratch_dir.mkdir(parents=True, exist_ok=True)
    total = len(requests)
    results: List[ExposureMergeResult] = []
    for i, req in enumerate(requests):
        if should_cancel():
            results.append(ExposureMergeResult(request=req, cancelled=True))
            for remaining in requests[i + 1:]:
                results.append(
                    ExposureMergeResult(request=remaining, cancelled=True))
            log.info("exposure merge: cancelled at bracket %d/%d",
                     i, total)
            return results
        progress_cb(i, total, req.label or req.bracket_key)
        try:
            scratch = fuse_bracket_request(
                req, scratch_dir=scratch_dir, align=align)
            results.append(
                ExposureMergeResult(request=req, scratch_path=scratch))
            log.info("exposure merge: bracket %s -> %s",
                     req.bracket_key, scratch.name)
        except Exception as exc:  # noqa: BLE001 — one bad bracket never stops the batch
            log.exception("exposure merge failed for bracket %s",
                          req.bracket_key)
            results.append(
                ExposureMergeResult(request=req, error=str(exc)))
        # Tick "done" up after the bracket completes so the line shows
        # the user counting through the batch — and so a UI Cancel
        # arriving here lands on the next-bracket boundary check.
        progress_cb(i + 1, total, "")
    return results


__all__ = [
    "ExposureMergeRequest", "ExposureMergeResult",
    "fuse_bracket_request", "run_exposure_merge",
    "ProgressCb", "ShouldCancel",
]
