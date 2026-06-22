"""Per-event ~2560-px JPEG proxy cache for photo items (spec/63 §5,
slice 7 — the browse-speed tier).

Browsing decodes the ORIGINAL file (24 MP JPEG ~80-150 ms, RAW embedded
thumb extraction ~50-150 ms) for every sharp delivery. This module
materialises a medium "screen copy" once per item under
``<event_root>/.cache/proxies/<sha256>.jpg``; subsequent browse decodes
read the small JPEG instead (audit-measured 20-40 ms — held-arrow at
key-repeat speed, spec/62's FastStone bar).

Two files per entry, both content-addressed by ``item.sha256``:

* ``<sha256>.jpg``  — the proxy pixels: long edge ≤ :data:`PROXY_MAX_EDGE`,
  quality :data:`PROXY_QUALITY`, upright (EXIF orientation baked in).
* ``<sha256>.json`` — the sidecar: source identity (``mtime_ns`` +
  ``size``, the invalidation key — covers the spec/57 external round
  trip) and the ORIGINAL's true post-orientation dimensions. The scaled
  tier must keep reporting the original's native dims through
  ``sharp_pixmap_info`` while displaying proxy pixels (spec/63 §7.7);
  the sidecar is where that truth survives.

Write order is proxy-then-sidecar, each atomic (tmp + replace): the
sidecar is the COMMIT MARKER — a proxy JPEG without its sidecar is
invisible to :func:`resolve_proxy`, so a crash between the two writes
can never serve pixels with unverified identity/dims.

Proxies are derived data: deletable, regenerable, excluded from any
backup story (the read path treats every miss/mismatch as "decode the
original" — the cache is a perf layer, not a correctness layer).

The honest paths NEVER read proxies: the F10 inspection lens and the
Compare surfaces decode originals by construction (spec/63 §7 deferral),
and RAW peaking keeps its half-res sensor demosaic.

Qt-free; pure I/O + Pillow. The rendering layer (``photo_cache``) wraps
returned paths in QImage/QPixmap itself.
"""
from __future__ import annotations

import io
import json
import logging
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

log = logging.getLogger(__name__)


# Cache sub-tree under the event root — sibling of the 256-px thumb
# trees (``core/photo_thumb_cache.py`` / ``core/thumb_cache.py``).
# Gitignored, safe to delete to regenerate.
PROXY_CACHE_SUBDIR = Path(".cache") / "proxies"

# Long edge in pixels (spec/63 §5). 2560 matches/exceeds fit-view on
# the target monitors; real pixels stay one F10 away.
PROXY_MAX_EDGE = 2560

# JPEG encode quality (spec/63 §5). ~0.4-0.8 MB per proxy → the
# "~3 GB per 5 000 photos" disk-honesty number.
PROXY_QUALITY = 85

# Build paths guarantee a proxy is "complete" (carries the full quality
# the browse decode of the original would give). Aspect-fit rounding
# can be off by a pixel either side; tolerate it.
_EDGE_TOLERANCE = 2

# Sidecar schema version — bump when the JSON shape changes so stale
# sidecars read as invalid instead of misread.
_SIDECAR_VERSION = 1

# Format dispatch sets — mirror ``image_loader`` / ``photo_thumb_cache``
# so a file the loader can browse gets a proxy here too.
_RAW_EXTENSIONS = frozenset({
    ".rw2", ".arw", ".srf", ".sr2", ".cr2", ".cr3", ".crw",
    ".nef", ".nrw", ".raf", ".pef", ".rwl", ".ori", ".orf", ".dng",
})
_HEIF_EXTENSIONS = frozenset({".heic", ".heif"})
# EXIF orientation values whose transpose swaps width/height (the
# Rotate90 family).
_ORIENTATION_SWAPS = {5, 6, 7, 8}


@dataclass(frozen=True)
class ProxyHit:
    """A valid proxy: the small JPEG to decode + the ORIGINAL's true
    post-orientation dimensions for 1:1 / zoom math."""
    path: Path
    native_w: int
    native_h: int


# ── Path math (no I/O) ───────────────────────────────────────────────


def proxy_path(event_root: Path, sha256: str) -> Path:
    return Path(event_root) / PROXY_CACHE_SUBDIR / f"{sha256}.jpg"


def proxy_meta_path(event_root: Path, sha256: str) -> Path:
    return Path(event_root) / PROXY_CACHE_SUBDIR / f"{sha256}.json"


def qualifies_as_proxy(
    image_w: int, image_h: int,
    native_w: int, native_h: int,
) -> bool:
    """Whether decoded pixels of ``image`` size are proxy-grade for a
    source of ``native`` size: the long edge reaches
    ``min(native_long, PROXY_MAX_EDGE)`` (within rounding tolerance).
    Used by write-on-decode — a decode at a small window target must
    NOT be persisted (serving it later at a bigger target would be
    soft); the builder fills those in at full proxy size instead."""
    image_long = max(int(image_w), int(image_h))
    native_long = max(int(native_w), int(native_h))
    if image_long <= 0 or native_long <= 0:
        return False
    return image_long + _EDGE_TOLERANCE >= min(native_long, PROXY_MAX_EDGE)


# ── Read side ────────────────────────────────────────────────────────


def read_proxy_meta(event_root: Path, sha256: str) -> Optional[dict]:
    """The sidecar dict, or ``None`` when absent/unreadable/foreign-
    version. Never raises."""
    meta_file = proxy_meta_path(event_root, sha256)
    try:
        with open(meta_file, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    if meta.get("version") != _SIDECAR_VERSION:
        return None
    return meta


def resolve_proxy(
    event_root: Path, sha256: str, source_path: Path,
) -> Optional[ProxyHit]:
    """The valid proxy for ``sha256``, or ``None``.

    Valid = sidecar present + readable, proxy JPEG present, and the
    source file's ``(mtime_ns, size)`` matches the sidecar (a replaced
    source — the spec/57 external round trip — invalidates). A missing
    source resolves to ``None`` too: the caller's original-decode
    fallback owns that failure honestly. Never raises."""
    meta = read_proxy_meta(event_root, sha256)
    if meta is None:
        return None
    try:
        stat = Path(source_path).stat()
    except OSError:
        return None
    if (int(meta.get("src_mtime_ns", -1)) != stat.st_mtime_ns
            or int(meta.get("src_size", -1)) != stat.st_size):
        return None
    native_w = int(meta.get("native_w", 0))
    native_h = int(meta.get("native_h", 0))
    if native_w <= 0 or native_h <= 0:
        return None
    pfile = proxy_path(event_root, sha256)
    if not pfile.exists():
        return None
    return ProxyHit(path=pfile, native_w=native_w, native_h=native_h)


# ── Write side ───────────────────────────────────────────────────────


def write_proxy(
    event_root: Path,
    sha256: str,
    source_path: Path,
    jpeg_bytes: bytes,
    native_w: int,
    native_h: int,
) -> bool:
    """Persist ``jpeg_bytes`` + sidecar for ``sha256``. Atomic, sidecar
    last (the commit marker). ``False`` on any I/O failure (the cache
    is a perf layer — callers never branch on it). Never raises."""
    if not jpeg_bytes or native_w <= 0 or native_h <= 0:
        return False
    try:
        stat = Path(source_path).stat()
    except OSError:
        return False
    dest = proxy_path(event_root, sha256)
    meta_dest = proxy_meta_path(event_root, sha256)
    tmp = dest.with_suffix(".jpg.tmp")
    meta_tmp = meta_dest.with_suffix(".json.tmp")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(jpeg_bytes)
        tmp.replace(dest)
        meta = {
            "version": _SIDECAR_VERSION,
            "src_mtime_ns": stat.st_mtime_ns,
            "src_size": stat.st_size,
            "native_w": int(native_w),
            "native_h": int(native_h),
        }
        meta_tmp.write_text(
            json.dumps(meta, separators=(",", ":")), encoding="utf-8")
        meta_tmp.replace(meta_dest)
    except OSError as exc:
        log.warning("proxy write failed for %s: %s", dest, exc)
        for stray in (tmp, meta_tmp):
            try:
                if stray.exists():
                    stray.unlink()
            except OSError:
                pass
        return False
    return True


def invalidate_proxy(event_root: Path, sha256: str) -> None:
    """Drop the pair (self-heal path for a corrupt proxy JPEG). The
    sidecar goes FIRST so a partial delete can't leave a servable
    entry. Never raises."""
    for victim in (proxy_meta_path(event_root, sha256),
                   proxy_path(event_root, sha256)):
        try:
            victim.unlink(missing_ok=True)
        except OSError:
            pass


# ── Builder render (Pillow — the background path) ────────────────────


def ensure_photo_proxy(
    event_root: Path, source_path: Path, sha256: str,
) -> bool:
    """Build the proxy for ``sha256`` from ``source_path`` unless a
    valid one already exists. ``True`` when a valid proxy exists on
    return. Never raises — any render/write failure logs and returns
    ``False`` (the browse path keeps decoding the original)."""
    source_path = Path(source_path)
    if resolve_proxy(event_root, sha256, source_path) is not None:
        return True
    # Defence-in-depth: a video file accidentally seeded into the
    # proxy builder (e.g. via a stale ``set_event_context`` batch)
    # used to log "cannot identify image file" on every build.
    # Short-circuit on extension — no decode, no log spam.
    from core.photo_decoder import is_supported as _is_image_supported

    if not _is_image_supported(source_path):
        return False
    try:
        jpeg_bytes, native_w, native_h = _render_proxy(source_path)
    except FileNotFoundError:
        return False
    except Exception as exc:                                       # noqa: BLE001
        log.warning("proxy render failed for %s: %s", source_path, exc)
        return False
    if not jpeg_bytes:
        return False
    return write_proxy(
        event_root, sha256, source_path, jpeg_bytes, native_w, native_h)


def _render_proxy(source: Path) -> Tuple[bytes, int, int]:
    """Decode ``source`` → (≤2560-px upright JPEG bytes, native_w,
    native_h). ``native`` = the ORIGINAL's true post-orientation pixel
    dims for JPEG-family sources; for RAW/HEIF it is the dims of the
    unbounded browse decode (embedded preview / full decode) — exactly
    what the scaled tier reports as native for those formats today."""
    suffix = source.suffix.lower()
    if suffix in _RAW_EXTENSIONS:
        return _render_raw_proxy(source)
    if suffix in _HEIF_EXTENSIONS:
        return _render_heif_proxy(source)
    return _render_pillow_proxy(source)


def _scale_and_encode(im) -> bytes:
    """Common tail: orient via EXIF, bound to the proxy edge, encode."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(im)
    im.thumbnail((PROXY_MAX_EDGE, PROXY_MAX_EDGE), Image.Resampling.LANCZOS)
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=PROXY_QUALITY, optimize=True)
    return buf.getvalue()


def _native_dims_post_orientation(im) -> Tuple[int, int]:
    """The image's full-size dims with the EXIF orientation applied —
    WITHOUT decoding pixels (``im.size`` is header data)."""
    w, h = im.size
    try:
        orientation = int(im.getexif().get(0x0112, 1) or 1)
    except Exception:                                              # noqa: BLE001
        orientation = 1
    if orientation in _ORIENTATION_SWAPS:
        return h, w
    return w, h


def _render_pillow_proxy(source: Path) -> Tuple[bytes, int, int]:
    """JPEG / TIFF / PNG / BMP / WEBP. ``draft`` gives the libjpeg
    DCT-domain downscale (decode lands near the proxy size cheaply);
    LANCZOS finishes to the exact bound."""
    from PIL import Image
    with Image.open(source) as im:
        native_w, native_h = _native_dims_post_orientation(im)
        try:
            im.draft("RGB", (PROXY_MAX_EDGE, PROXY_MAX_EDGE))
        except Exception:                                          # noqa: BLE001
            pass                      # non-JPEG formats simply ignore draft
        im.load()
        return _scale_and_encode(im), native_w, native_h


def _render_raw_proxy(source: Path) -> Tuple[bytes, int, int]:
    """RAW: the embedded preview (what browsing shows — in-camera lens
    corrections included), half-res demosaic fallback. Native dims =
    the decoded preview's dims, matching the scaled tier's RAW
    contract (header probing can't see inside RAW containers)."""
    import rawpy
    from PIL import Image, ImageOps
    try:
        with rawpy.imread(str(source)) as raw:
            thumb = raw.extract_thumb()
    except Exception as exc:                                       # noqa: BLE001
        log.info("RAW %s has no embedded thumb (%s); demosaic proxy",
                 source.name, exc)
        return _render_raw_demosaic_proxy(source)
    if thumb.format == rawpy.ThumbFormat.JPEG:
        with Image.open(io.BytesIO(thumb.data)) as im:
            im.load()
            im = ImageOps.exif_transpose(im)
            native_w, native_h = im.size
            return _encode_loaded(im), native_w, native_h
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        im = Image.fromarray(thumb.data)
        native_w, native_h = im.size
        return _encode_loaded(im), native_w, native_h
    return _render_raw_demosaic_proxy(source)


def _render_raw_demosaic_proxy(source: Path) -> Tuple[bytes, int, int]:
    import rawpy
    from PIL import Image
    with rawpy.imread(str(source)) as raw:
        rgb = raw.postprocess(
            use_camera_wb=True, output_bps=8, half_size=True)
    im = Image.fromarray(rgb)
    native_w, native_h = im.size
    return _encode_loaded(im), native_w, native_h


def _render_heif_proxy(source: Path) -> Tuple[bytes, int, int]:
    import pillow_heif
    from PIL import Image, ImageOps
    pillow_heif.register_heif_opener()   # idempotent
    with Image.open(str(source)) as im:
        im.load()
        im = ImageOps.exif_transpose(im)
        native_w, native_h = im.size
        return _encode_loaded(im), native_w, native_h


def _encode_loaded(im) -> bytes:
    """Encode an ALREADY-upright PIL image, bounding to the proxy edge."""
    from PIL import Image
    im.thumbnail((PROXY_MAX_EDGE, PROXY_MAX_EDGE), Image.Resampling.LANCZOS)
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=PROXY_QUALITY, optimize=True)
    return buf.getvalue()


# ── Disk honesty ─────────────────────────────────────────────────────


def proxy_cache_stats(event_root: Path) -> Tuple[int, int]:
    """``(proxy_count, total_bytes)`` for the event's proxy dir —
    sidecars included in bytes (they're part of the cost), only ``.jpg``
    counted as proxies. ``(0, 0)`` when the dir doesn't exist."""
    cache_dir = Path(event_root) / PROXY_CACHE_SUBDIR
    count = 0
    total = 0
    try:
        for entry in cache_dir.iterdir():
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            total += size
            if entry.suffix.lower() == ".jpg":
                count += 1
    except OSError:
        return 0, 0
    return count, total


def clear_proxy_cache(event_root: Path) -> int:
    """Delete every cache entry (derived data — regenerates on browse /
    via the builder). Returns the number of files removed."""
    cache_dir = Path(event_root) / PROXY_CACHE_SUBDIR
    removed = 0
    try:
        entries = list(cache_dir.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            entry.unlink()
            removed += 1
        except OSError:
            pass
    return removed


# ── Background builder ───────────────────────────────────────────────


class ProxyBuilder:
    """Polite daemon thread that builds missing derived files in the
    background (spec/63 §7.7 v1 — seeded from ``set_event_context`` /
    event open; write-on-decode covers what the user actually browses
    first).

    Politeness: between items the builder asks ``is_busy()`` (wired to
    "does the decode worker have queued jobs") and waits while the
    foreground is decoding — browsing always wins the disk + CPU.

    Thread-safe API: :meth:`seed`, :meth:`clear`, :meth:`stop`. Seeding
    a different event root drops the previous queue (those paths are no
    longer the open event's). Jobs dedupe by key across the queue's
    lifetime for the current root — a re-seed of the same bucket is
    free.

    The work itself is the injected ``ensure(event_root, source_path,
    key)`` — defaults to :func:`ensure_photo_proxy` (key = sha256).
    The export-thumb tier (spec/63 slice 8) runs a second instance
    with its own ensure (key = relpath digest)."""

    #: Pause between builds even when idle — keeps the thread from
    #: monopolising the disk right after a big seed.
    _IDLE_GAP_S = 0.02
    #: Re-check cadence while the foreground decoder is busy.
    _BUSY_WAIT_S = 0.2

    def __init__(
        self,
        is_busy: Optional[Callable[[], bool]] = None,
        ensure: Optional[Callable[[Path, Path, str], object]] = None,
    ) -> None:
        self._is_busy = is_busy or (lambda: False)
        self._ensure = ensure or ensure_photo_proxy
        self._cond = threading.Condition()
        self._queue: deque = deque()      # (event_root, source_path, sha256)
        self._queued_shas: set = set()    # dedupe for the current root
        self._root: Optional[Path] = None
        self._stopping = False
        # spec/100 §A — set while ``_ensure`` runs (which holds the
        # source file open via ``Image.open``). :meth:`quiesce` waits on
        # the condition for this to drop to False so the delete path
        # can rmtree without a stray Windows open-handle lock.
        self._building = False
        self._thread: Optional[threading.Thread] = None

    # ── API (any thread) ──────────────────────────────────────────

    def seed(
        self,
        event_root: Path,
        pairs: Iterable[Tuple[Path, str]],
    ) -> int:
        """Queue ``(source_path, key)`` pairs for ``event_root`` (key =
        sha256 for proxies, relpath digest for export thumbs).
        Returns how many were newly queued. Starts the thread lazily on
        first use (an app run that never browses photos never pays)."""
        event_root = Path(event_root)
        queued = 0
        with self._cond:
            if self._stopping:
                return 0
            if self._root != event_root:
                self._queue.clear()
                self._queued_shas.clear()
                self._root = event_root
            for source_path, sha256 in pairs:
                if not sha256 or sha256 in self._queued_shas:
                    continue
                self._queued_shas.add(sha256)
                self._queue.append((event_root, Path(source_path), sha256))
                queued += 1
            if queued:
                self._ensure_thread()
                self._cond.notify()
        return queued

    def clear(self) -> None:
        with self._cond:
            self._queue.clear()
            self._queued_shas.clear()
            self._root = None

    def stop(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._stopping = True
            self._queue.clear()
            self._cond.notify_all()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def quiesce(self, timeout: float = 2.0) -> bool:
        """Spec/100 §A — drain queued work AND wait for the in-flight
        build (the one holding the source file open inside
        ``Image.open``) to finish, WITHOUT setting ``_stopping``. Unlike
        :meth:`stop`, later :meth:`seed` calls still queue, so the
        builder is fully reusable after a "delete the event next door"
        gesture.

        Returns ``True`` when the builder fell idle within ``timeout``,
        ``False`` on timeout (caller logs; the resilient rmtree §B
        rides out a residual handle either way).

        ``clear()`` alone is not enough — it empties the queue but the
        ``Image.open`` already in progress keeps the source open until
        ``ensure`` returns, so the Windows file handle would survive
        the wipe."""
        deadline = None
        with self._cond:
            self._queue.clear()
            self._queued_shas.clear()
            if not self._building:
                return True
            # Wake the run loop in case it's blocked on the empty-queue
            # condition; not strictly needed (the build itself will
            # notify on exit), but it costs nothing.
            self._cond.notify_all()
            import time
            deadline = time.monotonic() + max(0.0, float(timeout))
            while self._building:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def pending_count(self) -> int:
        with self._cond:
            return len(self._queue)

    # ── Internals ─────────────────────────────────────────────────

    def _ensure_thread(self) -> None:
        # Caller holds the lock.
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, name="photo-proxy-builder", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return
                job = self._queue.popleft()
                # Dedupe means "currently queued", not "ever seen": a
                # later re-seed of the same key must be able to queue a
                # REBUILD (stale proxy after a source change; re-export
                # overwriting the same relpath). ensure() resolves-hit
                # in ~0.1 ms when nothing changed, so re-seeds are
                # nearly free.
                self._queued_shas.discard(job[2])
            # Yield to foreground decoding — re-queue the job rather
            # than holding it hostage across the wait.
            while not self._stopping and self._is_busy_safe():
                threading.Event().wait(self._BUSY_WAIT_S)
            if self._stopping:
                return
            event_root, source_path, key = job
            with self._cond:
                self._building = True
            try:
                self._ensure(event_root, source_path, key)
            except Exception:                                      # noqa: BLE001
                log.exception("proxy builder failed on %s", source_path)
            finally:
                # spec/100 §A — drop the flag + notify so a concurrent
                # :meth:`quiesce` wakes the moment the source handle
                # is released by ``ensure`` returning.
                with self._cond:
                    self._building = False
                    self._cond.notify_all()
            threading.Event().wait(self._IDLE_GAP_S)

    def _is_busy_safe(self) -> bool:
        try:
            return bool(self._is_busy())
        except Exception:                                          # noqa: BLE001
            return False
