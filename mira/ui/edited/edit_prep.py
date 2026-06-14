"""``edit_prep`` — the Edit page's off-thread working-copy prep
(spec/63 §6.1, Nelson's checkpoint 2026-06-12).

The old pipeline ran decode (130–615 ms) + downsample + auto-params
ON the UI thread per navigation — the freeze the spec/62 audit
measured. The prep happens back-of-house instead:

* ``decode_image(path, raw_half_size=True)`` — JPEG/HEIC full decode;
  RAW at libraw half-size (the Q2 ruling: tone choices and the 1280
  preview are resolution-insensitive; export re-decodes full
  independently; F10's developed preview develops this copy — the
  lens's existing honest-RAW definition).
* the 1280-px preview downsample (the fast ``reducing_gap`` path).
* the A-routed Natural (``compute_auto_params``) for the style the
  page resolved at request time.

ARCHITECTURE = the PhotoCache shape, deliberately: ONE process-wide
:class:`EditPrep` singleton (GUI-thread QObject) owns the worker
thread; the worker emits ONLY to the singleton (cross-thread, both
ends outliving every page), and the singleton re-emits ``prepared``
ON THE GUI THREAD — so page death can never race a cross-thread
emission (the 0xC0000409 fail-fast class a per-page QThread hit:
destroyed-while-running AND emit-into-a-dying-receiver). Pages
connect/disconnect with their own lifetime and drop results whose
path no longer matches their current item.

NEWEST-WINS, one slot: a navigation that outruns the worker simply
replaces the pending request (the Q3 ruling — develop only where the
user stops; the settle timer upstream means fly-bys never even
request). The worker thread is self-terminating: it exists only
while a job is pending, so an idle app holds no extra thread.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from core.photo_decoder import decode_image
from core.photo_render import Params
from core.photo_auto import compute_auto_params
from mira.ui.edited.adjustment_surface import (
    PREVIEW_MAX_WIDTH, _downsample)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrepResult:
    """One prepared working copy — what ``load_prepared`` adopts."""
    path: Path
    full_array: np.ndarray
    preview_array: np.ndarray
    natural_params: Params
    style: str


class _PrepThread(QThread):
    """Self-terminating single-slot worker. Emits ONLY to the
    long-lived :class:`EditPrep` singleton — never to a page."""

    done = pyqtSignal(object)            # PrepResult
    failed = pyqtSignal(object)          # Path

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._pending: Optional[tuple[Path, str]] = None
        self._active = False             # run loop alive (lock-guarded)
        self._stopping = False

    def request(self, path: Path, style: str) -> None:
        with self._lock:
            if self._stopping:
                return
            self._pending = (Path(path), style or "general")
            need_start = not self._active
            if need_start:
                self._active = True
        if need_start:
            # The previous run may be mid-return (it cleared _active
            # under the lock just before exiting) — join it briefly so
            # QThread.start() never overlaps a dying run.
            self.wait(100)
            self.start()

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._pending = None
        self.wait(2000)

    def run(self) -> None:
        while True:
            with self._lock:
                job = self._pending
                self._pending = None
                if job is None or self._stopping:
                    self._active = False
                    return
            path, style = job
            try:
                # ``decode_image`` resolved late through this module's
                # global so tests can intercept it (the net's decode
                # counter), mirroring the page-era seam.
                full = decode_image(path, raw_half_size=True)
                preview = _downsample(full, PREVIEW_MAX_WIDTH)
                routed = style if style and style != "general" else None
                natural = compute_auto_params(preview, style=routed)
            except Exception:                                      # noqa: BLE001
                log.exception("edit prep failed for %s", path)
                self.failed.emit(path)
                continue
            # A newer request may already be pending — deliver anyway;
            # the page drops stale paths (cheap), and the loop services
            # the newer one immediately after.
            self.done.emit(PrepResult(
                path=path, full_array=full, preview_array=preview,
                natural_params=natural, style=style))


class EditPrep(QObject):
    """The GUI-thread relay (see module docstring). Pages connect to
    :attr:`prepared` / :attr:`prep_failed` — delivery is same-thread,
    so a page's death auto-disconnects without a cross-thread race."""

    prepared = pyqtSignal(object)        # PrepResult
    prep_failed = pyqtSignal(object)     # Path

    def __init__(self) -> None:
        super().__init__()
        self._worker = _PrepThread(self)
        # Cross-thread, worker → THIS singleton (queued): both ends
        # live until shutdown, so the emission can never race a dying
        # receiver. SIGNAL-TO-SIGNAL chaining, deliberately: connecting
        # to ``self.prepared.emit`` (a plain callable) loses the
        # receiver QObject, so PyQt would invoke it DIRECTLY on the
        # worker thread — and every page slot downstream would build
        # QPixmaps off the GUI thread (the 0xC0000409 fail-fast).
        # Connecting to the bound SIGNAL keeps the queued hop onto
        # this object's (GUI) thread; pages then get same-thread
        # delivery.
        self._worker.done.connect(self.prepared)
        self._worker.failed.connect(self.prep_failed)

    def request(self, path: Path, style: str) -> None:
        self._worker.request(path, style)

    def shutdown(self) -> None:
        """Stop the worker thread (application exit / test session
        teardown — the PhotoCache discipline)."""
        self._worker.stop()


_singleton: Optional[EditPrep] = None
_singleton_lock = threading.Lock()


def edit_prep() -> EditPrep:
    """The process-wide :class:`EditPrep` (lazy — an app run that
    never opens Edit never pays for it)."""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = EditPrep()
        return _singleton


def shutdown_edit_prep() -> None:
    global _singleton
    with _singleton_lock:
        prep = _singleton
        _singleton = None
    if prep is not None:
        prep.shutdown()
