"""Exposure-bracket fusion — Mertens, two callers, one core.

Mertens fusion (``cv2.createMergeMertens``) picks the best-exposed,
best-contrast, most-saturated pixels across an exposure bracket and
blends them into a single tonemapped LDR image. No exposure-time
metadata, no camera-response calibration — fast and robust, ideal
both for a throwaway decision-aid preview AND for a materialized
master.

Two callers, one core:

* :func:`fuse_exposure_arrays` — the Qt-free numpy kernel (BGR uint8
  in, BGR uint8 out). The merge job (spec/109 §3, on the batch
  engine) feeds it full-res frames decoded from JPEG / RAW and
  writes the result to ``Original Media/Merged/`` via
  ``EventGateway.adopt_stack_output``. ``align=True`` runs
  ``cv2.AlignMTB`` first — safe for handheld brackets; tripod sets
  can opt out.
* :func:`fuse_exposures` — the Qt adapter (``list[QPixmap] ->
  QPixmap``) the Picker's "Combined" toggle uses for its
  decision-aid preview (docs/18 §"Bracket surfaces"). Thin wrapper
  around the kernel — alignment off by default so the preview stays
  snappy.

Charter invariant #8 (spec/00): :func:`fuse_exposure_arrays` is
**Qt-free** — the kernel imports only numpy + cv2. Qt lives behind
the adapter, mirroring ``core/focus_peaking.py`` (the other display-
feeding image kernel).
"""

from __future__ import annotations

import logging

import numpy as np

try:  # pragma: no cover — cv2 is a hard dep in pyproject; keep the
      # import guarded so trimmed test envs can still import the
      # module (it then degrades to the first-frame fallback).
    import cv2  # type: ignore
except ImportError:    # pragma: no cover
    cv2 = None        # type: ignore

from PyQt6.QtGui import QImage, QPixmap


log = logging.getLogger(__name__)


# ── Qt-free kernel ───────────────────────────────────────────────


def fuse_exposure_arrays(
    arrays: list[np.ndarray], *, align: bool = True,
) -> np.ndarray:
    """Mertens-fuse a list of BGR uint8 frames into one BGR uint8
    array — the Qt-free core (spec/109 §3, charter inv. #8).

    Input shape: each frame is ``(H, W, 3)`` BGR uint8 (cv2's native
    order). Frames that don't match the first frame's ``(H, W)``
    are resized to it (a bracket is captured at one framing; a stray
    mismatch is normalised, not fatal). ``align=True`` runs
    ``cv2.AlignMTB`` over the bracket first — handheld-safe; tripod
    sets can opt out.

    Output shape: a single ``(H, W, 3)`` BGR uint8 frame, the
    tonemapped fusion.

    Degrades, never raises:

    * ``[]``                       → a ``(0, 0, 3)`` empty array.
    * one frame                    → that frame, unchanged.
    * cv2 missing                  → the first frame, unchanged.
    * any pipeline failure         → the first frame.
    """
    if not arrays:
        return np.zeros((0, 0, 3), dtype=np.uint8)
    if len(arrays) < 2 or cv2 is None:
        if cv2 is None:
            log.warning("cv2 unavailable — exposure fusion returns the "
                        "first frame unchanged")
        return arrays[0]
    try:
        target_h, target_w = arrays[0].shape[:2]
        normalised: list[np.ndarray] = []
        for arr in arrays:
            if arr is None or arr.size == 0:
                continue
            if arr.shape[:2] != (target_h, target_w):
                arr = cv2.resize(
                    arr, (target_w, target_h),
                    interpolation=cv2.INTER_AREA,
                )
            normalised.append(arr)
        if len(normalised) < 2:
            return normalised[0] if normalised else arrays[0]
        if align:
            normalised = _align_mtb(normalised)
        merge = cv2.createMergeMertens()
        fused = merge.process(normalised)              # float32 ~[0,1]
        fused = np.clip(fused, 0.0, 1.0)
        return (fused * 255.0).astype(np.uint8)
    except Exception as exc:    # noqa: BLE001 — must not crash callers
        log.warning("Exposure fusion failed: %s", exc)
        return arrays[0]


def _align_mtb(frames: list[np.ndarray]) -> list[np.ndarray]:
    """``cv2.AlignMTB`` pre-align — handheld-safe (no exposure-time
    or response calibration needed). Bails to the unaligned bracket on
    any failure so the fusion still runs.

    OpenCV's Python binding writes into a PRE-ALLOCATED output list
    (each entry an ndarray of matching shape); passing an empty list
    is a silent no-op. We allocate per-frame buffers and check the
    result was actually populated before swapping in."""
    try:
        aligner = cv2.createAlignMTB()
        out = [np.empty_like(f) for f in frames]
        aligner.process(list(frames), out)
        # Sanity: AlignMTB writes meaningful pixels — a frame stuck at
        # all-zero suggests the binding skipped it (mismatched
        # dtype/shape) and we should fall back to unaligned.
        if any(o is None or o.size == 0 for o in out):
            return frames
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("AlignMTB failed: %s — using unaligned bracket", exc)
    return frames


# ── Qt adapter (Picker preview, spec/109 §3) ─────────────────────


def fuse_exposures(frames: list[QPixmap]) -> QPixmap:
    """The Picker "Combined" preview (docs/18 §"Bracket surfaces") —
    a thin Qt adapter over :func:`fuse_exposure_arrays`. Decision aid,
    never an output. Alignment OFF so the preview stays snappy; the
    merge job (spec/109 §3) runs the kernel with ``align=True``.

    Contract — never raises:

    * ``[]``                       → a null ``QPixmap``.
    * one frame / ``cv2`` missing  → that frame, unchanged.
    * frames of differing sizes    → all resized to the first
      frame's size before fusion (the kernel handles it).
    * any failure in the pipeline  → the first frame (a degraded
      preview beats a dead culler).
    """
    if not frames:
        return QPixmap()
    if len(frames) < 2 or cv2 is None:
        if cv2 is None:
            log.warning("cv2 unavailable — Combined preview is the "
                        "first frame only")
        return frames[0]
    try:
        target = frames[0].size()
        tw, th = target.width(), target.height()
        arrays: list[np.ndarray] = []
        for pm in frames:
            if pm is None or pm.isNull():
                continue
            arrays.append(_pixmap_to_bgr(pm))
        if len(arrays) < 2:
            return frames[0]
        fused = fuse_exposure_arrays(arrays, align=False)
        # The kernel returns ``arrays[0]`` unchanged on any internal
        # failure — preserve the existing adapter contract (the same
        # ``QPixmap`` reference, not a re-encoded copy) by detecting
        # that identity and short-circuiting to ``frames[0]``.
        if fused is arrays[0]:
            return frames[0]
        if fused.size == 0:
            return frames[0]
        return _bgr_to_pixmap(fused)
    except Exception as exc:    # noqa: BLE001 — must not crash the UI
        log.warning("Exposure fusion failed: %s", exc)
        return frames[0]


# ── Internals ────────────────────────────────────────────────────


def _pixmap_to_bgr(pixmap: QPixmap) -> np.ndarray:
    """QPixmap → contiguous BGR uint8 array for cv2 (row-padding
    safe — same discipline as ``focus_peaking._pixmap_to_array``)."""
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
    width = image.width()
    height = image.height()
    bytes_per_line = image.bytesPerLine()
    ptr = image.bits()
    ptr.setsize(bytes_per_line * height)
    raw = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape(
        (height, bytes_per_line)
    )
    rgb = raw[:, : width * 3].reshape((height, width, 3))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _bgr_to_pixmap(bgr: np.ndarray) -> QPixmap:
    """BGR uint8 array → QPixmap (copy() detaches from the numpy
    buffer before it goes out of scope)."""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    height, width, _ = rgb.shape
    qimage = QImage(
        rgb.tobytes(), width, height, 3 * width,
        QImage.Format.Format_RGB888,
    )
    return QPixmap.fromImage(qimage.copy())
