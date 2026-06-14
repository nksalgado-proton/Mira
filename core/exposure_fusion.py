"""Exposure-bracket fusion — a DECISION-AID preview, not an output.

docs/18 §"Bracket surfaces": the exposure-bracket surface is the
normal photo canvas plus a non-destructive **Combined** toggle that
shows what merging the bracket's frames would look like, so the user
can judge *"is this bracket worth keeping?"* at a glance — without
leaving the culler.

Phase-0 reconciliation (CLAUDE.md — "*no in-app HDR merging in v1;
external for HDR*"): this is **preview-only**, never saved, never the
deliverable — exactly the role docs/18 already gives the focus-
bracket film ("decision support, not output. The merge is
**external**"). The real HDR / tonemap still happens in Process /
Lightroom. Implementation = **Mertens exposure fusion**: no
exposure-time metadata, no camera-response calibration — robust,
fast, produces a tonemapped LDR directly. Ideal for a throwaway
look, deliberately **not** a radiance-accurate HDR pipeline (that
would be the banned in-app merge).

Pure logic — ``list[QPixmap] -> QPixmap``. **Never raises** (a
preview must never crash the culler): no frames → a null pixmap;
fewer than two frames or no cv2 → the first frame unchanged; any
pipeline failure → the first frame. Qt is imported here for the
QPixmap boundary, mirroring ``core/focus_peaking.py`` (the one
sanctioned Qt-in-core seam — both are display-feeding image kernels).
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


def fuse_exposures(frames: list[QPixmap]) -> QPixmap:
    """Exposure-fuse ``frames`` into one preview pixmap.

    Mertens fusion (``cv2.createMergeMertens``) — picks the
    best-exposed, best-contrast, most-saturated pixels across the
    bracket and blends them into a single tonemapped LDR image. No
    exposure times needed, no response-curve calibration: fast and
    robust, which is all a decision-aid preview needs.

    Contract — never raises:

    * ``[]``                       → a null ``QPixmap``.
    * one frame / ``cv2`` missing  → that frame, unchanged.
    * frames of differing sizes    → all resized to the first
      frame's size before fusion (a bracket should be identical
      framing; this just keeps a stray mismatch from crashing).
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
            bgr = _pixmap_to_bgr(pm)
            if bgr.shape[1] != tw or bgr.shape[0] != th:
                bgr = cv2.resize(bgr, (tw, th),
                                 interpolation=cv2.INTER_AREA)
            arrays.append(bgr)
        if len(arrays) < 2:
            return frames[0]
        merge = cv2.createMergeMertens()
        fused = merge.process(arrays)              # float32 ~[0,1]
        fused = np.clip(fused, 0.0, 1.0)
        out_bgr = (fused * 255.0).astype(np.uint8)
        return _bgr_to_pixmap(out_bgr)
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
