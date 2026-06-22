"""Session-wide photo cache + background decode worker for the Picker
photo surface (Nelson 2026-06-09 fast-nav redesign).

The previous flow ran ``image_loader.load_pixmap`` synchronously on
the UI thread inside :meth:`MediaCanvas.set_photo`. On big high-res
JPEGs (6 MB G9 frames, ~80–150 ms decode) this froze the wheel-event
loop: the user clicked, nothing changed, clicked again, and the photo
jumped two ahead when the queued events flushed.

The redesign:

1. **Off-thread decode.** A single :class:`_DecodeWorker` thread pulls
   jobs from a priority queue and decodes via ``load_qimage`` (QImage
   is thread-safe; the GUI thread does ``QPixmap.fromImage``). The UI
   never blocks.

2. **Two-tier cache.**
   * **Pixmap tier** — in-memory LRU of display-size :class:`QPixmap`
     objects keyed by source path. Cap set high enough for an
     ~80-photo working window without thrashing
     (:data:`_PIXMAP_CACHE_CAP`, configurable).
   * **Thumb tier** — reuses the existing on-disk 256-px JPEG cache
     (:mod:`core.photo_thumb_cache`) keyed by ``item.sha256``. Already
     warmed by the ingest pool, so a Pick session typically opens
     into a fully-populated thumb tier.

3. **Skim-then-settle.** Wheel sweep paints the thumb instantly while
   the full decode happens in the background; the photo surface's
   own settle timer kicks predecodes for the neighbours.

4. **Cancellable in-flight jobs.** Each navigation bumps a *generation*
   counter and ANY queued job older than the current generation is
   silently skipped by the worker — predecodes the user scrolled past
   AND superseded navigation targets alike (spec/63 slice 0: the old
   never-drop-priority-0 rule meant a held arrow key queued a full
   decode per repeat, all served FIFO, so the photo the user LANDED on
   decoded last — seconds of blur after every sprint). The newest
   navigation target always carries the newest generation, so the one
   photo actually on screen is never dropped.

5. **Singleton.** :func:`photo_cache` returns a process-wide instance
   so a photo decoded for the Picker is hit-cached if the user opens
   the same item in Compare / Edit later in the session.

6. **Scaled tier (spec/63 slice 0).** :meth:`request_scaled_pixmap`
   decodes AT a display target (JPEG DCT-domain downscale — measured
   ~1.5–3× faster than native) and delivers ``(path, pixmap,
   native_size)`` via :attr:`scaled_pixmap_ready`, the native size
   probed from the file header so box-zoom / 1:1 indicators keep TRUE
   dimensions (the 2026-06-09 regression that forced native-only
   decode). Nothing consumes it until the PhotoViewport (slice 1);
   :meth:`request_pixmap` behavior is unchanged for MediaCanvas.

7. **Proxy tier (spec/63 slice 7).** The SCALED path prefers the
   ~2560-px on-disk proxy (:mod:`core.photo_proxy_cache`, sha256-keyed
   under ``<event>/.cache/proxies/``) when a valid one exists — the
   small JPEG decodes in the audit's 20-40 ms class — and keeps
   reporting the ORIGINAL's native dims from the proxy sidecar, so
   1:1 / box-zoom math never learns proxies exist. Misses decode the
   original exactly as before and the worker PERSISTS the decoded
   image as the proxy when it is proxy-grade (write-on-decode: emit
   first, persist after — sharp latency never pays). A background
   :class:`~core.photo_proxy_cache.ProxyBuilder` fills the rest,
   seeded by :meth:`set_event_context` (bucket-level) and
   :meth:`seed_proxies` (whole-event, PickPage open). The NATIVE tier
   (:meth:`request_pixmap`) never touches proxies — Compare surfaces
   keep true pixels (spec/63 §7 deferral), and the F10 lens decodes
   originals directly by construction.

8. **Export-thumb tier (spec/63 slice 8).** Scaled requests at a
   Cut-grid class target (≤ the Day Grid's max cell, 280) for NON-item
   paths (no sha256 — exported files) are served from the 280-px
   on-disk thumb (:mod:`core.photo_thumb_cache` export functions,
   relpath-digest keyed) in ~2 ms; native dims stay honest via the
   original's header probe. The four lineage writers QUEUE thumbs
   onto a background builder at export time; files exported before
   the tier existed self-heal through the same write-on-decode hook
   (routed in :meth:`_persist_from_decode`). Item paths never get
   export thumbs (they have proxies); bigger targets (the Cut single
   views) bypass thumbs and decode the export file itself.
"""
from __future__ import annotations

import heapq
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from PyQt6.QtCore import (
    QBuffer, QByteArray, QIODevice, QObject, QSize, QThread, pyqtSignal)
from PyQt6.QtGui import QImage, QPixmap

from core import photo_proxy_cache, photo_thumb_cache
from core.photo_proxy_cache import ProxyBuilder
from core.photo_thumb_cache import EXPORT_THUMB_MAX_EDGE
from mira.ui.media.image_loader import load_qimage, native_image_size

log = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────────────────

#: LRU cap for the display-pixmap tier. Pixmaps are stored at NATIVE
#: source resolution so the box-zoom code (which reads
#: ``_source_pixmap.width()/height()`` as the "full-res" fallback for
#: JPEG / HEIC) shows correct 1:1 indicator rectangles and the actual
#: zoom crop has native pixels to draw. At ~80-100 MB per 24 MP QPixmap
#: a cap of 20 entries → ~2 GB peak, which fits comfortably on a 16 GB
#: machine while still covering the typical "current photo + 2 ahead +
#: 1 behind, repeated for the last few buckets the user touched" set.
_PIXMAP_CACHE_CAP = 20

#: Byte budget (MB) for the scaled-pixmap tier (spec/95 §3).
#: spec/63 capped the LRU at 32 entries sized for a 2560 target
#: (~17 MB each ≈ 0.5 GB), but at ``display_quality='high'`` on a
#: 4K+ display an entry can be ~30-38 MB; 32 of them would hit
#: ~1.2 GB. A byte budget keeps the memory ceiling stable
#: regardless of the display-quality setting — large entries take
#: more of the budget; small entries take less. ~512 MB is enough
#: for a wide working window at every tier the spec admits.
_SCALED_CACHE_BUDGET_MB = 512
_SCALED_CACHE_BUDGET_BYTES = _SCALED_CACHE_BUDGET_MB * 1024 * 1024


def _pixmap_byte_cost(pixmap: QPixmap) -> int:
    """Approximate the in-RAM byte cost of a ``QPixmap`` for budget
    accounting. Qt stores most pixmaps at 4 bytes per pixel (32-bit
    BGRA); ``depth()`` is the truth on the rare 24-bit / 16-bit
    formats. Edge cases (null pixmap, missing depth) collapse to 0
    so the budget never goes negative."""
    if pixmap is None or pixmap.isNull():
        return 0
    bpp = max(8, pixmap.depth())
    return max(0, pixmap.width() * pixmap.height() * (bpp // 8))


# ── Job model ─────────────────────────────────────────────────────────────


@dataclass(order=True)
class _Job:
    """Decode job for :class:`_DecodeWorker`.

    Ordered by ``(priority, seq)`` so the heap pops priority 0 (current
    target) first, then priority 1 (predecode), then FIFO within a
    priority bucket. ``path`` / ``size_w`` / ``size_h`` / ``generation``
    are payload — excluded from the sort tuple via :func:`field(compare=False)`.
    """
    priority: int
    seq: int
    path: Path = field(compare=False)
    size_w: int = field(compare=False)
    size_h: int = field(compare=False)
    generation: int = field(compare=False)
    # Scaled jobs decode AT the target size and report native dims via
    # ``scaled_pixmap_ready``; plain jobs keep native-resolution decode.
    scaled: bool = field(compare=False, default=False)


# ── Worker ────────────────────────────────────────────────────────────────


class _DecodeWorker(QThread):
    """Background thread that discharges decode jobs from a priority queue.

    The thread runs until :meth:`stop` is called. It never touches Qt
    widgets OR QPixmap — results are delivered as QImage via the
    ``image_ready`` / ``scaled_image_ready`` signals, which the receiver
    connects with ``Qt.QueuedConnection`` (the default for cross-thread
    signals) and converts to QPixmap on the GUI thread.
    """

    # The worker decodes to QImage (thread-safe) and emits it; the cache
    # converts to QPixmap on the GUI thread. Building a QPixmap on this
    # worker thread is undefined behaviour (QPixmap is GUI-thread only)
    # and produced a rare headed crash (2026-06-12).
    image_ready = pyqtSignal(Path, QImage)
    scaled_image_ready = pyqtSignal(Path, QImage, QSize)
    decode_failed = pyqtSignal(Path)

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        resolve_proxy: Optional[
            Callable[[Path], Optional[Tuple[Path, QSize]]]] = None,
        drop_proxy: Optional[Callable[[Path], None]] = None,
        persist_decode: Optional[
            Callable[[Path, QImage, QSize, QSize], None]] = None,
        resolve_export_thumb: Optional[
            Callable[[Path], Optional[Path]]] = None,
    ) -> None:
        super().__init__(parent)
        self._heap: list[_Job] = []
        self._cond = threading.Condition()
        self._seq = 0
        self._stopping = False
        # Disk-tier seams (spec/63 slices 7+8), all run ON THIS THREAD
        # and must never raise (the cache wraps them): resolve a valid
        # proxy / export thumb for a source path, drop a corrupt proxy,
        # persist a decode (routed to proxy or export thumb by the
        # cache). ``None`` (tests, MediaCanvas-era callers) = no tiers.
        self._resolve_proxy = resolve_proxy
        self._drop_proxy = drop_proxy
        self._persist_decode = persist_decode
        self._resolve_export_thumb = resolve_export_thumb
        # The "current generation" of work. Each navigation increments
        # the cache's generation counter; queued jobs from older
        # generations are dropped when the worker pops them.
        self._current_generation = 0

    # ── Job submission (cache thread) ─────────────────────────────

    def submit(
        self,
        path: Path,
        target_size: QSize,
        priority: int,
        generation: int,
        *,
        scaled: bool = False,
    ) -> None:
        with self._cond:
            self._seq += 1
            job = _Job(
                priority=priority, seq=self._seq,
                path=path,
                size_w=target_size.width(),
                size_h=target_size.height(),
                generation=generation,
                scaled=scaled,
            )
            heapq.heappush(self._heap, job)
            self._current_generation = max(
                self._current_generation, generation)
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()

    def has_pending(self) -> bool:
        """Whether decode jobs are queued — the proxy builder's
        politeness probe (any thread)."""
        with self._cond:
            return bool(self._heap)

    # ── Run loop (worker thread) ───────────────────────────────────

    def run(self) -> None:
        while True:
            with self._cond:
                while not self._heap and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return
                job = heapq.heappop(self._heap)
                generation = self._current_generation
            # Stale job — predecode OR superseded navigation target:
            # the user is past it. The job for the photo actually on
            # screen carries the newest generation, so it never drops
            # (spec/63 slice 0 — the old keep-all-priority-0 rule made
            # a held arrow key backlog seconds of decodes, served FIFO
            # with the landed photo LAST).
            if job.generation < generation:
                continue
            try:
                if job.scaled:
                    # spec/95 §B — when the requested target exceeds
                    # the proxy long edge (PROXY_MAX_EDGE = 2560), the
                    # on-disk proxy cannot satisfy it: serving the
                    # proxy and letting Qt upscale produces the "soft
                    # on a 4K monitor" path. Instead, decode the
                    # ORIGINAL down to target (DCT-domain downscale,
                    # bounded by the viewport's display-quality
                    # ceiling — never the full 24-45 MP path
                    # spec/62 removed). The viewport keeps this off
                    # the nav hot path by sending nav-bounded targets
                    # (capped at the proxy edge) on first-paint /
                    # held-arrow; only settle / prefetch requests
                    # actually reach the upgrade branch.
                    target_long = max(job.size_w, job.size_h)
                    upgrade_target = (
                        target_long > photo_proxy_cache.PROXY_MAX_EDGE)
                    if not upgrade_target:
                        # Export-thumb tier (spec/63 slice 8): a
                        # Cut-grid class request (target ≤ the grid's
                        # max cell) for an exported file decodes the
                        # 280-px thumb in ~2 ms. Native dims stay
                        # honest via the cheap header probe of the
                        # ORIGINAL. Stale/absent → fall through.
                        if (self._resolve_export_thumb is not None
                                and target_long <= EXPORT_THUMB_MAX_EDGE):
                            thumb = self._resolve_export_thumb(job.path)
                            if thumb is not None:
                                image = load_qimage(
                                    thumb, QSize(job.size_w, job.size_h))
                                if not image.isNull():
                                    native = native_image_size(job.path)
                                    self.scaled_image_ready.emit(
                                        job.path, image,
                                        native if native is not None
                                        else image.size())
                                    continue
                        # Proxy tier (spec/63 slice 7): a valid on-disk
                        # proxy decodes at the display target in the
                        # 20-40 ms class; the sidecar keeps the
                        # ORIGINAL's native dims so 1:1 / box-zoom
                        # math never learns proxies exist. A corrupt
                        # proxy self-heals: drop the pair, decode the
                        # original below (which re-persists it).
                        if self._resolve_proxy is not None:
                            hit = self._resolve_proxy(job.path)
                            if hit is not None:
                                proxy_file, native = hit
                                image = load_qimage(
                                    proxy_file,
                                    QSize(job.size_w, job.size_h))
                                if not image.isNull():
                                    self.scaled_image_ready.emit(
                                        job.path, image, native)
                                    continue
                                log.warning(
                                    "corrupt proxy for %s — rebuilding "
                                    "from the original", job.path)
                                if self._drop_proxy is not None:
                                    self._drop_proxy(job.path)
                    # Decode AT the display target (JPEG DCT-domain
                    # downscale) and probe true dimensions from the
                    # header — consumers keep honest 1:1 / box-zoom
                    # math without paying for native pixels. For
                    # upgrade-target jobs, this is the §B settle-only
                    # original-decode that gives true 4K-class sharp.
                    image = load_qimage(
                        job.path, QSize(job.size_w, job.size_h))
                else:
                    # Native-resolution decode — the pre-spec/63
                    # contract (box-zoom reads the pixmap's own dims;
                    # display-size decode broke that on JPEGs, Nelson
                    # 2026-06-09 follow-up). MediaCanvas stays on this
                    # path until the PhotoViewport migration. NEVER
                    # proxy-served: Compare keeps true pixels
                    # (spec/63 §7 deferral).
                    image = load_qimage(job.path)
            except Exception:                                       # noqa: BLE001
                log.exception("decode worker failed on %s", job.path)
                self.decode_failed.emit(job.path)
                continue
            if image.isNull():
                self.decode_failed.emit(job.path)
                continue
            if job.scaled:
                native = native_image_size(job.path)
                reported = native if native is not None else image.size()
                self.scaled_image_ready.emit(job.path, image, reported)
                # Write-on-decode (spec/63 slices 7+8): the image is
                # already in hand — persist it as a proxy or export
                # thumb when it qualifies (the cache routes). AFTER
                # the emit: sharp latency never pays for the encode.
                #
                # spec/95 §B: skip the proxy write when the decode
                # was an upgrade target (the served image is larger
                # than ``PROXY_MAX_EDGE`` — persisting it would
                # blow up the on-disk proxy size, which the spec
                # explicitly does NOT change). The builder still
                # fills the canonical 2560 proxy via its own path.
                target_long = max(job.size_w, job.size_h)
                if (self._persist_decode is not None
                        and target_long
                        <= photo_proxy_cache.PROXY_MAX_EDGE):
                    self._persist_decode(
                        job.path, image, reported,
                        QSize(job.size_w, job.size_h))
            else:
                self.image_ready.emit(job.path, image)


# ── Cache ─────────────────────────────────────────────────────────────────


class PhotoCache(QObject):
    """Session-wide photo cache (singleton via :func:`photo_cache`).

    Public API in three groups:

    * **Lifecycle / context** —
      :meth:`set_event_context`, :meth:`clear`.
    * **Sync lookups** —
      :meth:`get_pixmap_if_cached`, :meth:`get_thumb_pixmap_sync`.
    * **Async requests** —
      :meth:`request_pixmap`, with the result delivered via
      :attr:`pixmap_ready` (path-keyed; consumer filters for the path
      it currently cares about).
    """

    pixmap_ready = pyqtSignal(Path, QPixmap)
    scaled_pixmap_ready = pyqtSignal(Path, QPixmap, QSize)
    decode_failed = pyqtSignal(Path)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # OrderedDict gives O(1) move-to-end for LRU touch.
        self._pixmaps: "OrderedDict[Path, QPixmap]" = OrderedDict()
        self._pixmap_lock = threading.Lock()
        # Scaled tier (spec/63): keyed by (path, (w, h)); the value
        # carries the probed NATIVE size alongside the scaled pixmap.
        # Budget-evicted (spec/95 §3) — see ``_scaled_bytes``.
        self._scaled: "OrderedDict[Tuple[Path, Tuple[int, int]], Tuple[QPixmap, QSize]]" = OrderedDict()  # noqa: E501
        #: Running total of the in-RAM bytes the scaled LRU holds.
        #: Spec/95 §3 — the cap is a byte budget (not entry count) so
        #: large entries at ``display_quality='high'`` (~30-38 MB)
        #: don't blow past the memory ceiling the 32-entry cap was
        #: tuned for. Mirrors what :attr:`_scaled` accumulates over
        #: time and is the deciding number in eviction.
        self._scaled_bytes: int = 0
        # Last requested scaled target per path — lets the delivery
        # handler key the LRU without threading the target through the
        # worker signal. Main-thread only.
        self._scaled_target_by_path: Dict[Path, Tuple[int, int]] = {}
        # Pending-decode dedupe: (path, scaled-target-or-None) → the
        # generation it was queued under. A re-request at the SAME
        # generation is a duplicate (the queued job will deliver);
        # a newer generation must resubmit (the old job gets dropped).
        # Main-thread only — no lock.
        self._pending: Dict[Tuple[Path, Optional[Tuple[int, int]]], int] = {}
        # Per-path generation counter — incremented on every navigation
        # so the worker can drop stale predecodes.
        self._generation = 0
        # Path → sha256 (populated by :meth:`set_event_context`). Used
        # to look up the on-disk thumb via ``core/photo_thumb_cache``
        # and the proxy via ``core/photo_proxy_cache``.
        self._sha256_by_path: Dict[Path, str] = {}
        # The active event root, for the on-disk cache lookups.
        self._event_root: Optional[Path] = None
        # The (event_root, sha256_by_path) pair is now ALSO read from
        # the decode-worker thread (proxy resolve/persist) — this lock
        # keeps the pair consistent across an event switch (a cleared
        # root with the old sha map could serve a cross-event proxy).
        self._context_lock = threading.Lock()
        # Background proxy builder (spec/63 slice 7) — polite: yields
        # while the decode worker has queued jobs. Thread starts lazily
        # on first seed.
        self._proxy_builder = ProxyBuilder(
            is_busy=lambda: self._worker.has_pending())
        # The thumb tier is in-memory too — disk lookup is fast (~2 ms)
        # but a session-scope dict eats even that cost. No cap: thumbs
        # are tiny (~30 KB per QPixmap at 256 px) and an event rarely
        # exceeds a few thousand photos.
        self._thumb_pixmaps: Dict[Path, QPixmap] = {}
        # Worker (singleton lives as long as the cache). It emits QImage;
        # these slots run on the GUI thread (queued cross-thread signal)
        # and do the QPixmap.fromImage conversion safely here.
        self._worker = _DecodeWorker(
            self,
            resolve_proxy=self._resolve_proxy_for,
            drop_proxy=self._drop_proxy_for,
            persist_decode=self._persist_from_decode,
            resolve_export_thumb=self._resolve_export_thumb_for)
        self._worker.image_ready.connect(self._on_worker_image_ready)
        self._worker.scaled_image_ready.connect(
            self._on_worker_scaled_ready)
        self._worker.decode_failed.connect(self._on_worker_decode_failed)
        self._worker.start()

    # ── Lifecycle / context ────────────────────────────────────────

    def set_event_context(
        self,
        event_root: Optional[Path],
        sha256_by_path: Dict[Path, str],
    ) -> None:
        """Tell the cache which event is open and how to map source
        paths to ``item.sha256`` (the thumb-cache key).

        Same event root → the path → sha256 map merges with the
        existing one (each photo-surface ``load()`` only knows its
        current bucket's items; merging means later cluster-drill
        ``load`` calls retain the day grid's earlier mapping).
        Different event root → the in-memory thumb cache is dropped
        (paths from a previous event are no longer reachable; their
        on-disk thumbs live under a different ``.cache/`` tree).

        Registered items also seed the background proxy builder
        (spec/63 slice 7) — the bucket the user is browsing gets its
        proxies built quietly even before the whole-event seed
        (:meth:`seed_proxies`) reaches them."""
        new_root = Path(event_root) if event_root else None
        with self._context_lock:
            if new_root != self._event_root:
                self._thumb_pixmaps.clear()
                self._sha256_by_path = {}
                self._event_root = new_root
                # The proxy builder is NOT cleared here: its own
                # ``seed`` drops cross-root jobs, and the whole-event
                # seed (PickPage open) lands BEFORE the first surface
                # ``load()`` registers this context — clearing would
                # wipe it. Builds for a closed event are harmless
                # (still-valid derived data on that event's disk).
            self._sha256_by_path.update(sha256_by_path)
        if new_root is not None and sha256_by_path:
            self._proxy_builder.seed(
                new_root,
                [(path, sha) for path, sha in sha256_by_path.items()])

    def seed_proxies(
        self,
        event_root: Path,
        pairs: Iterable[Tuple[Path, str]],
    ) -> int:
        """Queue ``(source_path, sha256)`` pairs for background proxy
        builds (the whole-event seed at event open — spec/63 §5).
        Returns how many were newly queued."""
        return self._proxy_builder.seed(Path(event_root), pairs)

    def clear(self) -> None:
        with self._pixmap_lock:
            self._pixmaps.clear()
            self._scaled.clear()
            self._scaled_bytes = 0
        self._thumb_pixmaps.clear()
        self._scaled_target_by_path.clear()
        self._pending.clear()

    def shutdown(self) -> None:
        """Stop the worker + builder threads (including the core
        export-thumb builder — this is the app-exit hook). Called at
        application exit."""
        self._proxy_builder.stop()
        photo_thumb_cache.stop_export_thumb_builder()
        self._worker.stop()
        if not self._worker.wait(2000):
            log.warning("PhotoCache worker did not stop within 2s")

    # ── Proxy-tier seams (run on the DECODE WORKER thread) ─────────

    def _context_snapshot(
        self, path: Path,
    ) -> Tuple[Optional[Path], Optional[str]]:
        """A consistent (event_root, sha256) pair for ``path`` —
        consistency matters across event switches (a cleared root with
        the stale sha map could serve a cross-event proxy)."""
        with self._context_lock:
            return self._event_root, self._sha256_by_path.get(Path(path))

    def _resolve_proxy_for(
        self, path: Path,
    ) -> Optional[Tuple[Path, QSize]]:
        """Valid proxy for ``path`` as ``(proxy_file, native_size)``,
        or ``None``. Never raises (worker-loop safety)."""
        try:
            root, sha256 = self._context_snapshot(path)
            if root is None or not sha256:
                return None
            hit = photo_proxy_cache.resolve_proxy(root, sha256, Path(path))
            if hit is None:
                return None
            return hit.path, QSize(hit.native_w, hit.native_h)
        except Exception:                                            # noqa: BLE001
            log.exception("proxy resolve failed for %s", path)
            return None

    def _drop_proxy_for(self, path: Path) -> None:
        """Self-heal: drop a corrupt proxy pair. Never raises."""
        try:
            root, sha256 = self._context_snapshot(path)
            if root is None or not sha256:
                return
            photo_proxy_cache.invalidate_proxy(root, sha256)
        except Exception:                                            # noqa: BLE001
            log.exception("proxy invalidate failed for %s", path)

    def _resolve_export_thumb_for(self, path: Path) -> Optional[Path]:
        """Servable export thumb for ``path`` (spec/63 slice 8), or
        ``None``. Only meaningful for files that are NOT items (no
        sha256) — item paths get proxies. Never raises."""
        try:
            root, sha256 = self._context_snapshot(path)
            if root is None or sha256:
                return None
            return photo_thumb_cache.resolve_export_thumb(root, Path(path))
        except Exception:                                            # noqa: BLE001
            log.exception("export thumb resolve failed for %s", path)
            return None

    @staticmethod
    def _encode_jpeg(image: QImage, quality: int) -> bytes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = image.save(buf, "JPG", quality)
        buf.close()
        return bytes(data) if ok else b""

    def _persist_from_decode(
        self, path: Path, image: QImage, native: QSize, target: QSize,
    ) -> None:
        """Write-on-decode routing (spec/63 slices 7+8). Never raises.

        * Item path (sha256 known) → persist as the PROXY when the
          decode is proxy-grade — long edge ≥ min(native,
          PROXY_MAX_EDGE), so serving it at any later browse target is
          never softer than the original decode. Small-window decodes
          do NOT persist; the builder fills those at full proxy size.
        * Non-item path under the event root, decoded at a Cut-grid
          class target → persist as the EXPORT THUMB (the lazy
          self-heal for files exported before slice 8; stale thumbs
          refresh here too)."""
        try:
            root, sha256 = self._context_snapshot(path)
            if root is None:
                return
            if sha256:
                if not photo_proxy_cache.qualifies_as_proxy(
                        image.width(), image.height(),
                        native.width(), native.height()):
                    return
                if photo_proxy_cache.resolve_proxy(
                        root, sha256, Path(path)) is not None:
                    return                  # the builder beat us to it
                data = self._encode_jpeg(
                    image, photo_proxy_cache.PROXY_QUALITY)
                if data:
                    photo_proxy_cache.write_proxy(
                        root, sha256, Path(path), data,
                        native.width(), native.height())
                return
            if max(target.width(), target.height()) > EXPORT_THUMB_MAX_EDGE:
                return
            if photo_thumb_cache.resolve_export_thumb(
                    root, Path(path)) is not None:
                return                      # fresh thumb already there
            data = self._encode_jpeg(
                image, photo_thumb_cache.THUMB_QUALITY)
            if data:
                photo_thumb_cache.write_export_thumb_bytes(
                    root, Path(path), data)
        except Exception:                                            # noqa: BLE001
            log.exception("write-on-decode persist failed for %s", path)

    # ── Sync lookups ───────────────────────────────────────────────

    def get_pixmap_if_cached(self, path: Path) -> Optional[QPixmap]:
        """Display-tier hit. ``None`` on miss. Touches LRU on hit."""
        path = Path(path)
        with self._pixmap_lock:
            pix = self._pixmaps.get(path)
            if pix is None:
                return None
            self._pixmaps.move_to_end(path)
            return pix

    def get_thumb_pixmap_sync(self, path: Path) -> Optional[QPixmap]:
        """Thumb-tier hit. Loads from the on-disk 256-px cache on first
        touch (sha256 → path via :mod:`core.photo_thumb_cache`).

        Returns ``None`` when no sha256 is known for ``path`` (item
        not registered via :meth:`set_event_context`), when the on-disk
        thumb hasn't been materialised yet, or when the JPEG decode
        fails. The caller's fallback is the in-flight async decode."""
        path = Path(path)
        cached = self._thumb_pixmaps.get(path)
        if cached is not None:
            return cached
        event_root, sha256 = self._context_snapshot(path)
        if event_root is None or not sha256:
            return None
        try:
            from core.photo_thumb_cache import photo_thumb_path
        except Exception:                                            # noqa: BLE001
            log.exception("photo_thumb_cache import failed")
            return None
        thumb_path = photo_thumb_path(event_root, sha256)
        if not thumb_path.exists():
            return None
        pixmap = QPixmap(str(thumb_path))
        if pixmap.isNull():
            return None
        self._thumb_pixmaps[path] = pixmap
        return pixmap

    # ── Async requests ─────────────────────────────────────────────

    def request_pixmap(
        self,
        path: Path,
        target_size: QSize,
        *,
        priority: int = 0,
    ) -> None:
        """Queue an async NATIVE-resolution decode. ``priority=0`` is
        the current navigation target; ``priority=1`` is predecode for
        neighbours. Any queued job is dropped once a newer navigation
        bumps the generation — only the newest target survives a held
        arrow key (spec/63 slice 0).

        Hits the LRU first: a cached entry re-emits ``pixmap_ready``
        synchronously via the next event-loop tick (the caller doesn't
        special-case cache vs decode)."""
        path = Path(path)
        cached = self.get_pixmap_if_cached(path)
        if cached is not None and not cached.isNull():
            # Cache hit at native res — always sufficient. Re-emit
            # so the caller's normal path runs (same code for hit + miss).
            self.pixmap_ready.emit(path, cached)
            return
        if priority == 0:
            self._generation += 1
        if self._pending.get((path, None)) == self._generation:
            return                      # identical decode already queued
        self._pending[(path, None)] = self._generation
        self._worker.submit(
            path, target_size, priority, self._generation)

    def request_scaled_pixmap(
        self,
        path: Path,
        target_size: QSize,
        *,
        priority: int = 0,
    ) -> None:
        """Queue an async decode AT ``target_size`` (spec/63). Delivery
        via :attr:`scaled_pixmap_ready` as ``(path, pixmap, native)``,
        the native size probed from the file header so consumers keep
        true-dimension math (box-zoom 1:1) without native pixels.
        Same generation/drop semantics as :meth:`request_pixmap`."""
        path = Path(path)
        key = (target_size.width(), target_size.height())
        hit = self.get_scaled_pixmap_if_cached(path, target_size)
        if hit is not None:
            self.scaled_pixmap_ready.emit(path, hit[0], hit[1])
            return
        if priority == 0:
            self._generation += 1
        if self._pending.get((path, key)) == self._generation:
            return
        self._pending[(path, key)] = self._generation
        self._scaled_target_by_path[path] = key
        self._worker.submit(
            path, target_size, priority, self._generation, scaled=True)

    def get_scaled_pixmap_if_cached(
        self, path: Path, target_size: QSize,
    ) -> Optional[Tuple[QPixmap, QSize]]:
        """Scaled-tier hit as ``(pixmap, native_size)``; ``None`` on
        miss. Touches LRU on hit."""
        key = (Path(path), (target_size.width(), target_size.height()))
        with self._pixmap_lock:
            entry = self._scaled.get(key)
            if entry is None:
                return None
            self._scaled.move_to_end(key)
            return entry

    # ── Worker callbacks (GUI thread — safe to build QPixmap here) ──

    def _on_worker_image_ready(self, path: Path, image: QImage) -> None:
        self._pending.pop((path, None), None)
        pixmap = QPixmap.fromImage(image)
        with self._pixmap_lock:
            self._pixmaps[path] = pixmap
            self._pixmaps.move_to_end(path)
            while len(self._pixmaps) > _PIXMAP_CACHE_CAP:
                self._pixmaps.popitem(last=False)
        self.pixmap_ready.emit(path, pixmap)

    def _on_worker_scaled_ready(
        self, path: Path, image: QImage, native: QSize,
    ) -> None:
        pixmap = QPixmap.fromImage(image)
        target = self._scaled_target_by_path.get(path)
        if target is not None:
            self._pending.pop((path, target), None)
            with self._pixmap_lock:
                key = (path, target)
                # spec/95 §3 — byte-budget eviction. Account for the
                # incoming entry first, then evict from the LRU front
                # until total bytes ≤ budget. The bookkeeping accepts
                # an entry that exceeds the budget by itself (a
                # corner case: 5K decode > 512 MB / 1) so the live
                # display never goes blank.
                old = self._scaled.pop(key, None)
                if old is not None:
                    self._scaled_bytes -= _pixmap_byte_cost(old[0])
                cost = _pixmap_byte_cost(pixmap)
                self._scaled[key] = (pixmap, native)
                self._scaled_bytes += cost
                while (self._scaled_bytes > _SCALED_CACHE_BUDGET_BYTES
                        and len(self._scaled) > 1):
                    _evicted_key, evicted = self._scaled.popitem(
                        last=False)
                    self._scaled_bytes -= _pixmap_byte_cost(evicted[0])
                if self._scaled_bytes < 0:
                    self._scaled_bytes = 0
        self.scaled_pixmap_ready.emit(path, pixmap, native)

    def _on_worker_decode_failed(self, path: Path) -> None:
        self._pending.pop((path, None), None)
        target = self._scaled_target_by_path.get(path)
        if target is not None:
            self._pending.pop((path, target), None)
        self.decode_failed.emit(path)


# ── Singleton ────────────────────────────────────────────────────────────

_singleton: Optional[PhotoCache] = None
_singleton_lock = threading.Lock()


def photo_cache() -> PhotoCache:
    """Return the process-wide :class:`PhotoCache` instance. Constructs
    it on first call (lazy — the worker thread spins up here)."""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PhotoCache()
        return _singleton
