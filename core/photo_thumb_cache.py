"""Per-event 256-px JPEG cache for photo items (Nelson 2026-06-05).

Decoding RAW (or even JPEG) thumbnails from the origin file on every
event open was the Day Grid's wall: 50-300 ms per file × hundreds of
files = many seconds of main-thread work that the user feels as "the
day takes forever to load." This module materialises a tiny JPEG once
per item under ``<event_root>/.cache/thumbs/photos/<sha256>.jpg``;
every subsequent open reads the small JPEG in ~2 ms.

Keyed by ``item.sha256`` so:
  * The cache is content-addressed — the same RAW imported into a
    second event regenerates from disk only if it lands under a new
    event root, and a fresh copy of the same file (e.g. a redownload
    matching byte-for-byte) reuses the existing cache entry.
  * The all-or-nothing CHECK on ``item`` guarantees ``sha256 IS NOT
    NULL`` whenever ``origin_relpath`` is — i.e. for every item the
    Day Grid loader can render. Virtual items (no origin file) never
    reach this cache.

Atomic write-then-rename so a partial JPEG (interrupted write) can
never be read as the final cache entry.

Qt-free; pure I/O. The rendering layer wraps the returned path in a
QPixmap itself.

**Export-file thumbs (spec/63 slice 8)** live alongside, under
``<event_root>/.cache/thumbs/exports/<relpath-digest>.jpg``: exported
files are not Items (no sha256) — their identity is the lineage row's
``export_relpath``, so the key is its digest. The four lineage writers
(host batch · single photo · video clip via ``ui/edited/_lineage``,
the return scan, the spec/57 backfill) QUEUE thumbs onto a background
builder at export time (never inline — a 200-file batch must not stall
the foreground), and the photo-cache worker lazily self-heals thumbs
for files exported before this slice. Staleness is make-style: the
thumb serves only while ``thumb.mtime ≥ source.mtime`` (a re-export
overwriting the file invalidates; the next grid open rebuilds).
Long edge = 280 px — the Day Grid's MAX_CELL_SIZE, so grid cells never
upscale (the photo tier's 256 predates that constant; exports match
the grids they exist for).
"""
from __future__ import annotations

import hashlib
import io
import logging
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# Cache sub-tree under the event root — gitignored, safe to delete to
# regenerate. Sibling to ``core/thumb_cache.py``'s video-thumb tree at
# ``.cache/thumbs/`` (which keys by source-rel-path because videos do
# not carry sha256 on the bucket model; photos do via the item table).
PHOTO_CACHE_SUBDIR = Path(".cache") / "thumbs" / "photos"

# Long edge in pixels. DayGridCell's cell-size slider runs 80–280; 256
# reads cleanly across that range without uncomfortable upscaling at
# the max end.
THUMB_MAX_EDGE = 256

# JPEG encode quality. 80 is the sweet spot for a preview thumbnail —
# ~10–30 KB per file for a 256-px image, no visible artefacts at this
# scale.
THUMB_QUALITY = 80

# Format dispatch sets. Mirror the dispatch in
# ``mira.ui.media.image_loader.load_pixmap`` so a file the loader
# can render gets a cached thumb here too.
_RAW_EXTENSIONS = frozenset({
    ".rw2", ".arw", ".srf", ".sr2", ".cr2", ".cr3", ".crw",
    ".nef", ".nrw", ".raf", ".pef", ".rwl", ".ori", ".orf", ".dng",
})
_HEIF_EXTENSIONS = frozenset({".heic", ".heif"})


def photo_thumb_path(event_root: Path, sha256: str) -> Path:
    """Cache path for the photo with content hash ``sha256``. Pure path
    math — no I/O, no parent ``mkdir``."""
    return Path(event_root) / PHOTO_CACHE_SUBDIR / f"{sha256}.jpg"


def ensure_photo_thumb(
    event_root: Path,
    source_path: Path,
    sha256: str,
) -> Path:
    """Return the cache path for ``sha256``; generate it from
    ``source_path`` on a miss.

    On a cache hit the function returns in microseconds (single
    ``Path.exists`` call). On a miss it decodes the source, scales the
    long edge to :data:`THUMB_MAX_EDGE`, JPEG-encodes at
    :data:`THUMB_QUALITY`, and writes atomically (tmp + replace).

    On any render failure (corrupt source, missing dependency, decoder
    refusal) the source path is returned unchanged so the caller can
    still try the full ``load_pixmap`` path as a fallback — the cache
    is a perf layer, not a correctness layer.

    Args:
        event_root: The event's root directory (where ``.cache/`` lives).
        source_path: The origin file. May be JPEG / RAW / HEIF.
        sha256: ``item.sha256`` — the content-address key.

    Returns:
        The cache path on success, or ``source_path`` on render failure.
        Caller should ``load_pixmap`` the returned path either way.
    """
    dest = photo_thumb_path(event_root, sha256)
    if dest.exists():
        return dest
    try:
        jpeg_bytes = _render_photo_thumb(source_path)
    except FileNotFoundError:
        # Source itself is missing — bubble back; the loader will get
        # a null pixmap from load_pixmap and log the failure.
        return source_path
    except Exception as exc:  # noqa: BLE001 — perf layer, never crashes the loader
        log.warning("photo thumb render failed for %s: %s", source_path, exc)
        return source_path
    if not jpeg_bytes:
        return source_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".jpg.tmp")
    try:
        tmp.write_bytes(jpeg_bytes)
        tmp.replace(dest)
    except OSError as exc:
        log.warning("photo thumb write failed for %s: %s", dest, exc)
        # Clean up a stray tmp on best-effort basis.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return source_path
    return dest


# ── Format-specific renderers ────────────────────────────────────────


def _render_photo_thumb(source: Path) -> bytes:
    """Decode ``source`` → 256-px upright JPEG bytes. Format-aware."""
    suffix = source.suffix.lower()
    if suffix in _RAW_EXTENSIONS:
        return _render_raw_thumb(source)
    if suffix in _HEIF_EXTENSIONS:
        return _render_heif_thumb(source)
    return _render_pillow_thumb(source)


def _scale_and_encode(im) -> bytes:
    """Common tail: orient via EXIF, shrink to max edge, JPEG-encode."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(im)
    im.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE), Image.Resampling.LANCZOS)
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    return buf.getvalue()


def _render_pillow_thumb(source: Path) -> bytes:
    """JPEG / TIFF / PNG / BMP / WEBP — anything Pillow opens natively."""
    from PIL import Image
    with Image.open(source) as im:
        im.load()
        return _scale_and_encode(im)


def _render_raw_thumb(source: Path) -> bytes:
    """RAW: prefer the embedded JPEG (fast — most cameras ship one);
    BITMAP-embedded thumbs decode via PIL; if neither is present, fall
    back to a half-res demosaic."""
    import rawpy
    from PIL import Image
    try:
        with rawpy.imread(str(source)) as raw:
            thumb = raw.extract_thumb()
    except (rawpy.LibRawError, AttributeError) as exc:
        log.info("RAW %s has no embedded thumb (%s); demosaic fallback",
                 source.name, exc)
        return _render_raw_demosaic_thumb(source)
    if thumb.format == rawpy.ThumbFormat.JPEG:
        with Image.open(io.BytesIO(thumb.data)) as im:
            im.load()
            return _scale_and_encode(im)
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        im = Image.fromarray(thumb.data)
        return _scale_and_encode(im)
    return _render_raw_demosaic_thumb(source)


def _render_raw_demosaic_thumb(source: Path) -> bytes:
    """RAW with no embedded thumb — half-res postprocess via libraw."""
    import rawpy
    from PIL import Image
    with rawpy.imread(str(source)) as raw:
        rgb = raw.postprocess(use_camera_wb=True, output_bps=8, half_size=True)
    im = Image.fromarray(rgb)
    return _scale_and_encode(im)


def _render_heif_thumb(source: Path) -> bytes:
    """HEIF / HEIC via pillow-heif."""
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()   # idempotent
    with Image.open(str(source)) as im:
        im.load()
        return _scale_and_encode(im)


# ── Export-file thumbs (spec/63 slice 8) ─────────────────────────────

EXPORT_CACHE_SUBDIR = Path(".cache") / "thumbs" / "exports"

#: Long edge for export thumbs — the Day Grid's MAX_CELL_SIZE (280):
#: the Cut grids request exactly that, and a 256 thumb would upscale
#: at the slider's top end.
EXPORT_THUMB_MAX_EDGE = 280

#: What an export thumb can be rendered FROM. Exports are JPEG|TIFF by
#: the Edit dialog; the return scan and the spec/57 backfill may bring
#: other still formats. Videos (clip exports) are NOT thumbed here —
#: their posters ride the video thumb cache.
_EXPORT_IMAGE_SUFFIXES = frozenset({
    ".jpg", ".jpeg", ".tif", ".tiff", ".png", ".bmp", ".webp",
    ".heic", ".heif",
})


def _export_key(export_relpath) -> str:
    """Digest of the lineage row's relpath (DB convention: forward
    slashes) — the export file's identity key."""
    rel = str(export_relpath).replace("\\", "/")
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()


def export_thumb_path(event_root: Path, export_relpath) -> Path:
    return (Path(event_root) / EXPORT_CACHE_SUBDIR
            / f"{_export_key(export_relpath)}.jpg")


def resolve_export_thumb(
    event_root: Path, source_abs: Path,
) -> Optional[Path]:
    """The servable thumb for an exported file, or ``None``.

    Servable = ``source_abs`` lives under ``event_root``, the thumb
    exists, and ``thumb.mtime ≥ source.mtime`` (make-style staleness —
    a re-export overwriting the file invalidates; hardlinked backfill
    sources keep their old mtimes, which the later-written thumb
    always beats). Never raises."""
    try:
        rel = Path(source_abs).relative_to(Path(event_root))
    except (ValueError, OSError):
        return None
    thumb = export_thumb_path(event_root, rel.as_posix())
    try:
        if thumb.stat().st_mtime_ns >= Path(source_abs).stat().st_mtime_ns:
            return thumb
    except OSError:
        return None
    return None


def write_export_thumb_bytes(
    event_root: Path, source_abs: Path, jpeg_bytes: bytes,
) -> bool:
    """Persist already-rendered thumb bytes for an exported file (the
    photo-cache worker's lazy write-on-decode — it holds the decoded
    image). Atomic; ``False`` on any failure. Never raises."""
    if not jpeg_bytes:
        return False
    try:
        rel = Path(source_abs).relative_to(Path(event_root))
    except (ValueError, OSError):
        return False
    dest = export_thumb_path(event_root, rel.as_posix())
    tmp = dest.with_suffix(".jpg.tmp")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(jpeg_bytes)
        tmp.replace(dest)
    except OSError as exc:
        log.warning("export thumb write failed for %s: %s", dest, exc)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False
    return True


def ensure_export_thumb(
    event_root: Path, export_relpath, _key: str = "",
) -> Optional[Path]:
    """Build the thumb for ``export_relpath`` unless a fresh one
    exists. Returns the thumb path on success, ``None`` for non-image
    files (clips) or any render/write failure — the cache is a perf
    layer, never a correctness layer. Signature is builder-compatible
    (``ensure(event_root, source_or_rel, key)``)."""
    rel = Path(str(export_relpath))
    source = Path(event_root) / rel
    if source.suffix.lower() not in _EXPORT_IMAGE_SUFFIXES:
        return None
    hit = resolve_export_thumb(event_root, source)
    if hit is not None:
        return hit
    try:
        from PIL import Image, ImageOps
        with Image.open(source) as im:
            try:
                im.draft("RGB", (EXPORT_THUMB_MAX_EDGE,
                                 EXPORT_THUMB_MAX_EDGE))
            except Exception:                                      # noqa: BLE001
                pass              # non-JPEG formats simply ignore draft
            im.load()
            im = ImageOps.exif_transpose(im)
            im.thumbnail(
                (EXPORT_THUMB_MAX_EDGE, EXPORT_THUMB_MAX_EDGE),
                Image.Resampling.LANCZOS)
            if im.mode != "RGB":
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=THUMB_QUALITY,
                    optimize=True)
    except FileNotFoundError:
        return None
    except Exception as exc:                                       # noqa: BLE001
        log.warning("export thumb render failed for %s: %s", source, exc)
        return None
    if not write_export_thumb_bytes(event_root, source, buf.getvalue()):
        return None
    return export_thumb_path(event_root, rel.as_posix())


# One process-wide background builder for export thumbs — the four
# lineage writers queue here so thumb rendering NEVER runs inline on
# an export flow (a 200-file batch must not stall the foreground).
# Reuses the proxy tier's polite builder with the thumb ensure
# injected; the relpath digest is the dedupe key.
_export_builder = None
_export_builder_lock = threading.Lock()


def _ensure_for_builder(event_root: Path, source_path: Path, _key: str):
    try:
        rel = Path(source_path).relative_to(Path(event_root))
    except (ValueError, OSError):
        return None
    return ensure_export_thumb(event_root, rel.as_posix())


def queue_export_thumb(event_root: Path, export_relpath) -> None:
    """Queue one exported file for background thumb materialisation
    (the lineage writers' call). Non-blocking; never raises."""
    try:
        rel = str(export_relpath).replace("\\", "/")
        source = Path(event_root) / rel
        if source.suffix.lower() not in _EXPORT_IMAGE_SUFFIXES:
            return
        global _export_builder
        with _export_builder_lock:
            if _export_builder is None:
                from core.photo_proxy_cache import ProxyBuilder
                _export_builder = ProxyBuilder(ensure=_ensure_for_builder)
            builder = _export_builder
        builder.seed(Path(event_root), [(source, _export_key(rel))])
    except Exception:                                              # noqa: BLE001
        log.exception("queue_export_thumb failed for %s", export_relpath)


def stop_export_thumb_builder() -> None:
    """Application-exit hook (symmetry with PhotoCache.shutdown)."""
    global _export_builder
    with _export_builder_lock:
        builder = _export_builder
        _export_builder = None
    if builder is not None:
        builder.stop()
