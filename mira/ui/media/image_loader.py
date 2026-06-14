"""``load_pixmap(path)`` — robust still-image decode for the cull photo surface.

Ported from the legacy ``ui/media/media_canvas.py`` decode helpers (charter §5.2 — reuse
the part; pure image decode, no data tendril). Dispatch: native Qt formats (orientation-
corrected) → Pillow JPEG/MPO fallback (G9 II HighRes-Mode) → RAW embedded thumb (rawpy)
with a half-res demosaic fallback → HEIF (pillow-heif). Every path returns an upright
QPixmap; an unreadable file yields a null QPixmap (the surface shows a "preview
unavailable" hint) — never raises.

This is the *display* tier (screen-res preview). Full-res RAW demosaic for the focus
verdict (peaking / box-zoom) is deferred with the rest of the rich canvas (spec/11 §6
step 4 is the *minimal* loop).
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QSize
from PyQt6.QtGui import QImage, QImageReader, QPixmap, QTransform

log = logging.getLogger(__name__)

_QPIXMAP_NATIVE = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"})
_RAW_EXTENSIONS = frozenset({
    ".rw2", ".arw", ".srf", ".sr2", ".cr2", ".cr3", ".crw",
    ".nef", ".nrw", ".raf", ".pef", ".rwl", ".ori", ".orf", ".dng",
})
_HEIF_EXTENSIONS = frozenset({".heic", ".heif"})

_PILLOW_FALLBACK_MAX_SIDE = 4096
# libraw flip → CW rotation to upright (libraw convention).
_LIBRAW_FLIP_ROTATION = {3: 180, 5: 270, 6: 90}


def load_qimage(
    path: Path, target_size: QSize | None = None,
) -> QImage:
    """Decode ``path`` to an upright display QImage (null on failure, never raises).

    This is the THREAD-SAFE decode core: ``QImage`` may be created and
    used on any thread, unlike ``QPixmap`` (GUI-thread only). The decode
    worker (:mod:`photo_cache`) calls this off-thread and the GUI thread
    converts the result with ``QPixmap.fromImage`` — building a QPixmap
    on the worker thread is undefined behaviour and produced a rare
    headed crash (2026-06-12).

    ``target_size`` (Nelson 2026-06-09 fast-nav redesign): when given,
    the native-Qt JPEG path hands the size to ``QImageReader.setScaledSize``
    so the JPEG decoder outputs the scaled image directly (DCT-domain
    downscale — typically 3-4× faster than decode-full-then-scale on
    24 MP JPEGs). ``None`` preserves the legacy full-size decode.

    Aspect ratio is preserved: the caller's rectangle is interpreted as
    a maximum bounding box; the actual decoded size fits inside it.
    RAW + HEIF + Pillow-fallback paths ignore the hint — RAW embedded
    thumbs are already small (~1620 px), HEIF/Pillow are rare and
    don't benefit from scaled-decode tricks (their decoders return
    full-size anyway)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in _QPIXMAP_NATIVE:
        img = _read_oriented_qimage(path, target_size=target_size)
        if not img.isNull():
            return img
        if suffix in {".jpg", ".jpeg"}:
            img = _load_jpeg_via_pillow(path)
            if not img.isNull():
                return img
    if suffix in _RAW_EXTENSIONS:
        return _load_raw_thumbnail(path)
    if suffix in _HEIF_EXTENSIONS:
        return _load_heif(path)
    return _read_oriented_qimage(path, target_size=target_size)


def load_pixmap(
    path: Path, target_size: QSize | None = None,
) -> QPixmap:
    """GUI-thread QPixmap wrapper over :func:`load_qimage`. Call this
    only on the GUI thread (grid loaders, direct paints); background
    decoders must use :func:`load_qimage` and convert on the GUI side."""
    return QPixmap.fromImage(load_qimage(path, target_size))


def native_image_size(path: Path) -> QSize | None:
    """TRUE post-orientation pixel dimensions from the file HEADER —
    no pixel decode (spec/63 slice 0: scaled-tier consumers keep
    honest 1:1 / box-zoom math while displaying display-size pixmaps).

    ``QImageReader.size()`` reads dimensions without decoding;
    EXIF-rotated files report pre-rotation dims, so 90°-family
    transforms swap width/height here. ``None`` when the header can't
    say (RAW/HEIF — for those the decoded pixmap's own size IS the
    display contract, same as the pre-spec/63 behavior)."""
    try:
        reader = QImageReader(str(Path(path)))
        reader.setAutoTransform(True)
        size = reader.size()
        if not size.isValid() or size.isEmpty():
            return None
        # Rotate90 family (Rotate90 / MirrorAndRotate90 / FlipAndRotate90
        # / Rotate270) all carry the Rotate90 bit — width/height swap.
        # (.value: PyQt6 pure enums don't support int() directly.)
        from PyQt6.QtGui import QImageIOHandler
        rotate90 = QImageIOHandler.Transformation.TransformationRotate90.value
        if reader.transformation().value & rotate90:
            size.transpose()
        return size
    except Exception:                                                # noqa: BLE001
        log.debug("native_image_size failed for %s", path)
        return None


# ── orientation-correct readers ───────────────────────────────────────────


def _qimagereader_oriented(
    reader: QImageReader, target_size: QSize | None = None,
) -> QImage:
    reader.setAutoTransform(True)
    # ``setScaledSize`` must run BEFORE ``read()`` — it tells the
    # decoder to emit at the requested size (DCT-domain downscale for
    # JPEGs). We respect aspect ratio: the source size is read first
    # (cheap header-only call) and a bounding box is computed.
    if target_size is not None and target_size.isValid():
        src = reader.size()
        if src.isValid() and src.width() > 0 and src.height() > 0:
            tw = target_size.width()
            th = target_size.height()
            sw = src.width()
            sh = src.height()
            if sw > tw or sh > th:
                if sw * th > sh * tw:
                    new_w = tw
                    new_h = max(1, round(sh * tw / sw))
                else:
                    new_h = th
                    new_w = max(1, round(sw * th / sh))
                reader.setScaledSize(QSize(new_w, new_h))
    return reader.read()


def _read_oriented_qimage(
    path: Path, target_size: QSize | None = None,
) -> QImage:
    return _qimagereader_oriented(
        QImageReader(str(path)), target_size=target_size)


def _oriented_qimage_from_jpeg_bytes(data: object) -> QImage:
    buf = QBuffer()
    buf.setData(QByteArray(bytes(data)))  # type: ignore[arg-type]
    buf.open(QBuffer.OpenModeFlag.ReadOnly)
    try:
        return _qimagereader_oriented(QImageReader(buf))
    finally:
        buf.close()


def _apply_libraw_flip(img: QImage, flip: object) -> QImage:
    try:
        angle = _LIBRAW_FLIP_ROTATION.get(int(flip or 0), 0)
    except (TypeError, ValueError):
        angle = 0
    if angle == 0 or img.isNull():
        return img
    return img.transformed(QTransform().rotate(angle))


# ── format-specific fallbacks ─────────────────────────────────────────────


def _load_jpeg_via_pillow(path: Path) -> QImage:
    """Decode a JPEG/MPO Qt's reader gives up on (G9 II HighRes-Mode), bounded in size."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        log.warning("Pillow not available — can't decode JPEG variant: %s", path)
        return QImage()
    try:
        with Image.open(path) as im:
            im.load()
            im = ImageOps.exif_transpose(im)
            im.thumbnail(
                (_PILLOW_FALLBACK_MAX_SIDE, _PILLOW_FALLBACK_MAX_SIDE),
                Image.Resampling.LANCZOS,
            )
            if im.mode != "RGB":
                im = im.convert("RGB")
            width, height = im.size
            raw = im.tobytes("raw", "RGB")
    except Exception as exc:  # noqa: BLE001
        log.warning("Pillow JPEG fallback failed for %s: %s", path, exc)
        return QImage()
    return QImage(raw, width, height, width * 3, QImage.Format.Format_RGB888).copy()


def _load_heif(path: Path) -> QImage:
    try:
        import pillow_heif
        from PIL import Image, ImageOps
    except ImportError:
        log.warning("pillow-heif/Pillow unavailable; cannot preview %s", path)
        return QImage()
    try:
        pillow_heif.register_heif_opener()  # idempotent
        with Image.open(str(path)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            data = im.tobytes("raw", "RGB")
            qimg = QImage(data, im.width, im.height, 3 * im.width, QImage.Format.Format_RGB888)
            return qimg.copy()
    except Exception as exc:  # noqa: BLE001
        log.warning("HEIF decode failed for %s: %s", path, exc)
        return QImage()


def _load_raw_thumbnail(path: Path) -> QImage:
    """Embedded JPEG/BITMAP thumb (fast) → half-res demosaic (reliable) → empty."""
    try:
        import rawpy
    except ImportError:
        log.warning("rawpy not available; cannot preview RAW %s", path)
        return QImage()
    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
    except Exception as exc:  # noqa: BLE001
        log.info("RAW %s has no embedded thumb (%s); demosaic fallback", path.name, exc)
        return _load_raw_thumbnail_via_demosaic(path)

    if thumb.format == rawpy.ThumbFormat.JPEG:
        img = _oriented_qimage_from_jpeg_bytes(thumb.data)
        if not img.isNull():
            return img
        return _load_raw_thumbnail_via_demosaic(path)
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        try:
            from PIL import Image
            img = Image.fromarray(thumb.data)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            qimg = QImage.fromData(buf.getvalue(), "JPG")
            if not qimg.isNull():
                return qimg
        except Exception as exc:  # noqa: BLE001
            log.info("BITMAP thumb path failed for %s (%s); demosaic fallback", path.name, exc)
        return _load_raw_thumbnail_via_demosaic(path)
    return _load_raw_thumbnail_via_demosaic(path)


def _load_raw_thumbnail_via_demosaic(path: Path) -> QImage:
    qimg = _load_raw_half_res(path)
    if qimg is None or qimg.isNull():
        log.warning("RAW %s could not be previewed by any path; rendering blank", path)
        return QImage()
    return qimg


def load_raw_half_res(path: Path) -> QImage:
    """Public seam: the half-res demosaic (real sensor pixels) as a
    QImage, or a null QImage on failure / non-RAW. Used for honest
    RAW focus-peaking (the embedded thumb carries in-camera lens
    correction the demosaic lacks, so peaking the mask onto the thumb
    misregisters at the edges — Nelson 2026-05-16)."""
    img = _load_raw_half_res(path)
    return img if img is not None else QImage()


def load_raw_full_res(path: Path) -> QImage:
    """Public seam: the FULL demosaic (true sensor resolution) as a
    QImage, or null on failure. The honest 1:1 source for the F10
    inspection zoom — half-res isn't enough to peep individual sensor
    pixels. Heavy (~0.6 s, ~24 MP); only the deliberate zoom calls it."""
    try:
        import numpy as np
        import rawpy
    except ImportError:
        log.warning("rawpy/numpy unavailable; cannot full-res %s", path)
        return QImage()
    try:
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
            flip = getattr(raw.sizes, "flip", 0)
    except Exception as exc:                                     # noqa: BLE001
        log.warning("rawpy full-res decode failed for %s: %s", path, exc)
        return QImage()
    try:
        rgb = np.ascontiguousarray(rgb)
        h, w = rgb.shape[:2]
        img = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888)
        return _apply_libraw_flip(img.copy(), flip)
    except Exception as exc:                                     # noqa: BLE001
        log.warning("RAW→QImage (full) failed for %s: %s", path, exc)
        return QImage()


def _load_raw_half_res(path: Path):
    try:
        import numpy as np
        import rawpy
    except ImportError:
        log.warning("rawpy/numpy unavailable; cannot half-res %s", path)
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8, half_size=True)
            flip = getattr(raw.sizes, "flip", 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("rawpy half-res decode failed for %s: %s", path, exc)
        return None
    try:
        rgb = np.ascontiguousarray(rgb)
        h, w = rgb.shape[:2]
        img = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888)
        return _apply_libraw_flip(img.copy(), flip)
    except Exception as exc:  # noqa: BLE001
        log.warning("RAW→QImage (half) failed for %s: %s", path, exc)
        return None
