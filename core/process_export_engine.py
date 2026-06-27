"""Process-phase export engine — multi-bucket, multi-day walker.

Walks the in-scope photos across an iterable of buckets, runs each
through the Process pipeline (decode → rotation → AUTO/sliders →
crop → encode), and writes the output as JPEG/TIFF (or Original =
byte copy) under ``<destination>/<day_label>/<name>``.

**Output layout (docs/25 §8, 2026-05-28):** flat **per day** — no
``<style>`` sub-folder. The style is persisted in the database; the
output tree needn't carry it. ``ProcessBucketInput.style_label`` is
retained for back-compat but no longer affects the path.

**Scope (docs/25 §9):** Process has no Keep/Compare/Discard. The
caller passes exactly the files to export (photo / bucket / day /
event scope) and sets ``gate_kept=False``; the legacy default
(``gate_kept=True``) preserves the old "export only STATE_KEPT"
behaviour for any caller still relying on it.

Per-photo overrides:

* ``params_by_filename`` — manual slider state for specific photos
  (the page's currently-edited photo; future-Phase sidecar values).
  Falls back to fresh AUTO for any photo not in the dict.
* ``crop_by_filename`` — per-photo rects the user drew this
  session. Falls back to the centred max-area crop for the chosen
  aspect ratio.

Collision policy mirrors the Cull export engine
(:mod:`core.cull_export`): ``UNIQUE`` writes under ``stem (2).jpg``
etc.; ``OVERRIDE`` replaces atomically. The same ``ExportResult``
dataclass is reused so reporting code stays uniform.

Pure logic — no Qt. The host (``BucketCullShell`` / ``IngestProcess
Page``) builds the bucket list and wraps :func:`run_process_export`
in a QThreadPool task for off-thread execution.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
from PIL import Image

from core.aspect_ratio import get_aspect_ratio
from core.cull_export import (
    CollisionPolicy,
    ExportFileType,
    ExportResult,
    _unique_target,
)
from core.cull_state import is_kept
from core.photo_auto import (
    compute_auto_params,
    compute_look_params,
    creative_filter_amount,
    filter_strength_scale,
    resolve_filter_recipe,
)
from core.photo_decoder import decode_image, is_supported
from core.photo_render import (
    FilterRecipe,
    Params,
    apply_crop_norm,
    apply_filter,
    apply_params,
    apply_rotation,
    compute_default_crop,
    extract_rotated_crop,
)
from core.process_decisions import (
    get_process_aspect_label,
    get_process_crop,
    get_process_crop_angle,
    get_process_look,
    get_process_params,
    get_process_rotation,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessBucketInput:
    """One in-scope bucket for Process Export.

    Mirrors :class:`core.cull_export_gather.BucketExportInput` but
    adds ``style_label`` (the sub-folder name under the day, e.g.
    ``"wildlife"`` / ``"uncategorized"``). The Process layout under
    ``03 - Processed/`` is ``<day>/<style>/<file>`` — the engine
    plumbs the style straight from this field rather than
    re-classifying.
    """

    files: tuple[Path, ...]
    journal: dict
    day_label: str
    style_label: str = "uncategorized"


# Progress callback: ``(done, total, current_name) -> keep_going``.
# Returning False tells the engine to stop. Errors raised in the
# callback are caught and logged — the engine continues so a flaky
# UI thread can't lose the export.
ProgressCB = Callable[[int, int, str], bool]
# spec/139 §2 — separate per-file fraction sink. Fired AFTER each
# file completes (this engine is photo-only, so a write is near-
# instant — fraction snaps to 1.0 per file). The clip lane lives in
# :mod:`core.render_worker` and surfaces fraction via its own sink
# during the encode. Sink is fire-and-forget; never cancels.
FileFractionCB = Callable[[str, float], None]


def _count_total(
    buckets: Iterable[ProcessBucketInput], *, gate_kept: bool,
) -> int:
    """Count the files the buckets will export — used so the progress
    callback can render an accurate "n/N" out of the box. With
    ``gate_kept`` only STATE_KEPT files count (legacy); without it
    every file the caller passed counts (docs/25 scope model)."""
    total = 0
    for b in buckets:
        for p in b.files:
            if not gate_kept or is_kept(b.journal, p.name):
                total += 1
    return total


def _af_center_for(source_path: Path) -> tuple[float, float]:
    """spec/116 §2 — read the AF point from EXIF + brand profile and
    return ``(cx, cy)`` in normalised image coords. ``(0.5, 0.5)`` on
    any failure or missing AF data — never raises (the Subject
    Spotlight falls back to the frame centre)."""
    try:
        from core.brand_profile import match_brand_profile_for_photo
        from core.exif_reader import read_exif_single
        exif = read_exif_single(Path(source_path))
        raw = getattr(exif, "raw", None) if exif is not None else None
        if not raw:
            return (0.5, 0.5)
        prof = match_brand_profile_for_photo(raw)
        if prof is None:
            return (0.5, 0.5)
        af = prof.read_af_point(raw)
        if af is None:
            return (0.5, 0.5)
        return (float(af.cx), float(af.cy))
    except Exception:                                              # noqa: BLE001
        log.debug(
            "process-export: AF resolve failed for %s", source_path)
        return (0.5, 0.5)


def _apply_crop_tilt_np(arr: np.ndarray, angle_degrees: float) -> np.ndarray:
    """numpy → PIL → rotate(expand=False) → numpy (task #117).

    Mirrors :func:`core.process_render.apply_crop_tilt` for the
    export engine, which runs in numpy. Near-zero angles short-
    circuit. ``expand=False`` keeps the canvas the same shape;
    corners that swing past the original frame are filled black
    (PIL default).
    """
    if abs(angle_degrees) < 1e-3:
        return arr
    pil = Image.fromarray(arr)
    rotated = pil.rotate(
        -float(angle_degrees),
        resample=Image.Resampling.BICUBIC,
        expand=False,
    )
    return np.asarray(rotated)


def _render_one(
    src: Path,
    *,
    auto_on: bool,
    cached_params: Optional[Params],
    look_choice: Optional[dict],
    crop_norm: Optional[tuple[float, float, float, float]],
    crop_angle: float,
    rotation: int,
    aspect_label: str,
    style: Optional[str],
) -> tuple[np.ndarray, Params]:
    """Decode → rotation → tone → tilt → crop. Returns the final RGB
    uint8 array AND the resolved :class:`Params` the tone step used —
    the lineage snapshot records them (spec/54 §8).

    Tone resolution order:

    1. ``cached_params`` — explicit slider values (legacy callers /
       legacy journals). Applied regardless of ``auto_on``.
    2. ``look_choice`` — the spec/54 CHOICE
       (``{"look", "style", "creative_filter"}``): compiled
       deterministically via :func:`core.photo_auto.compute_look_params`
       on the decoded image. The choice's own style beats the
       resolver's. (``creative_filter`` is carried but not yet
       rendered — the filter engine is the spec/54 §8 pending phase.)
    3. ``auto_on`` — fresh AUTO, which under the routed engine IS
       Natural (the spec/54 §6 no-row default).
    4. identity (untouched).

    ``rotation`` (docs/25 §4): 90° clockwise steps, applied FIRST so
    the crop rect (normalised against the displayed/rotated frame)
    lands correctly. ``crop_angle`` (task #117): free-angle tilt after
    tone, before crop.

    Raises on decode failure (caller catches and records the error
    against the source path).
    """
    img = decode_image(src)
    if rotation:
        img = apply_rotation(img, rotation)
    if cached_params is not None and not cached_params.is_identity:
        params = cached_params
    elif look_choice is not None:
        params = compute_look_params(
            img,
            style=look_choice.get("style") or style,
            look=look_choice["look"],
            strength=float(look_choice.get("strength", 1.0)),
        )
    elif auto_on:
        params = compute_auto_params(img, style=style)
    else:
        params = Params()                     # untouched
    out = apply_params(img, params) if not params.is_identity else img

    # Creative filter (spec/55) — after the Look's tone, before crop
    # (pipeline: correction → mood → filter → crop, spec/54 §8).
    if look_choice is not None and look_choice.get("creative_filter"):
        key = look_choice["creative_filter"]
        recipe = resolve_filter_recipe(
            key, look_choice.get("style") or style)
        if recipe is not None:
            # spec/116 §2 — the Subject Spotlight anchors at the
            # photo's AF point. ``_af_center_for`` reads EXIF + brand
            # profile; missing data falls back to the frame centre.
            center = _af_center_for(src)
            # spec/156 — scale the filter by the per-image strength the
            # CHOICE carries (absent → 0.0 = medium, the new default).
            strength = float(look_choice.get("filter_strength", 0.0) or 0.0)
            out = apply_filter(
                out, FilterRecipe.from_dict(recipe),
                creative_filter_amount(key) * filter_strength_scale(strength),
                center=center)

    # Crop. ``crop_angle`` is the Box Rotation (docs/25 §4): the crop
    # box spins about its own centre, and the output is the box content
    # rectified upright. With no angle it's a plain axis-aligned crop.
    ratio = get_aspect_ratio(aspect_label)
    if crop_norm is None and not ratio.is_original:
        h, w = out.shape[:2]
        crop_norm = compute_default_crop(w, h, ratio)
    if crop_norm is not None:
        if crop_angle:
            out = extract_rotated_crop(out, crop_norm, crop_angle)
        else:
            out = np.ascontiguousarray(apply_crop_norm(out, crop_norm))
    return out, params


def _write_image(
    arr: np.ndarray,
    dest: Path,
    *,
    file_type: ExportFileType,
    jpeg_quality: int,
) -> None:
    """Atomic encode + write of a rendered array. ``dest.parent`` is
    created if missing. Uses a sibling temp file + ``os.replace`` so
    a crash never leaves a half-file under ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.part-{os.getpid()}")
    img = Image.fromarray(arr)
    try:
        if file_type is ExportFileType.JPEG:
            img.save(
                str(tmp), "JPEG",
                quality=int(jpeg_quality),
                # 4:4:4 chroma at quality ≥ 90 (matches LRC); 4:2:0
                # below.
                subsampling=0 if int(jpeg_quality) >= 90 else 2,
                optimize=True,
            )
        elif file_type is ExportFileType.TIFF:
            img.save(str(tmp), "TIFF", compression="tiff_lzw")
        else:
            raise ValueError(
                f"_write_image cannot encode {file_type!r}; "
                "ORIGINAL is handled by the copy path."
            )
        os.replace(str(tmp), str(dest))
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def _record_write(
    result: ExportResult,
    src: Path,
    final: Path,
    *,
    existed_before: bool,
    renamed: bool,
) -> None:
    """Bookkeeping for one successful write — picks the right
    ExportResult bucket so the summary dialog reads honest counts."""
    if renamed:
        result.renamed.append((src, final))
    elif existed_before:
        result.overwritten.append(final)
    else:
        result.written.append(final)


def run_process_export(
    buckets: Iterable[ProcessBucketInput],
    destination: Path,
    *,
    file_type: ExportFileType,
    jpeg_quality: int = 90,
    collision: CollisionPolicy = CollisionPolicy.UNIQUE,
    auto_on: bool = True,
    aspect_label: str = "Original",
    params_by_filename: Optional[dict[str, Params]] = None,
    crop_by_filename: Optional[
        dict[str, tuple[float, float, float, float]]
    ] = None,
    crop_angle_by_filename: Optional[dict[str, float]] = None,
    rotation_by_filename: Optional[dict[str, int]] = None,
    style_resolver: Optional[Callable[[Path], Optional[str]]] = None,
    gate_kept: bool = True,
    progress: Optional[ProgressCB] = None,
    on_file_fraction: Optional[FileFractionCB] = None,
    params_sink: Optional[dict[str, dict]] = None,
) -> ExportResult:
    """Execute the Process export across all ``buckets``.

    Returns an :class:`ExportResult` summarising the run. Errors
    against single photos are collected, never raised — one bad file
    must not abort a 1000-photo export.

    ``gate_kept`` (docs/25 §9): when True (legacy default) only
    STATE_KEPT files export; when False every file the caller passed
    exports (the redesigned Process surface has no Kept gate — the
    scope picker already chose the files).

    ``params_sink`` (spec/54 §8): when provided, filled with
    ``{filename: resolved-params-dict}`` for every rendered photo —
    the exact tone numbers each export baked, for the lineage
    snapshot. Filenames are source names (the lineage writer keys by
    stem).
    """
    params_by_filename = params_by_filename or {}
    crop_by_filename = crop_by_filename or {}
    crop_angle_by_filename = crop_angle_by_filename or {}
    rotation_by_filename = rotation_by_filename or {}
    result = ExportResult()
    buckets = list(buckets)
    total = _count_total(buckets, gate_kept=gate_kept)
    done = 0
    cancelled = False

    for b in buckets:
        if cancelled:
            break
        # docs/25 §8 — flat per-day output; no <style> sub-folder.
        dest_dir = Path(destination) / b.day_label
        # docs/25: the journal is the source of truth for each photo's
        # non-destructive edits. Each bucket carries its own aspect;
        # the caller's aspect_label is only the fallback default (so a
        # day/event-scope export honours every bucket's own ratio).
        bucket_aspect = get_process_aspect_label(b.journal) or aspect_label

        for src in b.files:
            if cancelled:
                break
            if gate_kept and not is_kept(b.journal, src.name):
                continue
            done += 1
            if progress is not None:
                try:
                    keep_going = bool(progress(done, total, src.name))
                except Exception:                     # noqa: BLE001
                    keep_going = True
                if not keep_going:
                    cancelled = True
                    break
            # spec/139 §2 — photo writes are near-instant, so per-file
            # fraction snaps to 1.0 alongside the aggregate tick. The
            # video lane (clips) lives in render_worker.py and surfaces
            # fraction during the encode.
            if on_file_fraction is not None:
                try:
                    on_file_fraction(src.name, 1.0)
                except Exception:                     # noqa: BLE001
                    pass

            if not src.is_file():
                result.skipped.append((src, "source missing"))
                continue

            # ORIGINAL = byte-copy with collision policy. Doesn't
            # decode / re-encode so RAW / HEIC stay intact.
            if file_type is ExportFileType.ORIGINAL:
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest_name = src.name
                    final = dest_dir / dest_name
                    existed = final.exists()
                    renamed = existed and collision is CollisionPolicy.UNIQUE
                    if renamed:
                        final = _unique_target(dest_dir, dest_name)
                    shutil.copy2(str(src), str(final))
                    _record_write(
                        result, src, final,
                        existed_before=existed, renamed=renamed,
                    )
                except OSError as exc:
                    log.warning("Process ORIGINAL copy failed for %s: %s",
                                src, exc)
                    result.errors.append((src, str(exc)))
                continue

            # JPEG / TIFF — full Process pipeline.
            if not is_supported(src):
                # Files we can't decode (e.g. videos) skip the
                # render path entirely; for ORIGINAL above they get
                # byte-copied without issue.
                result.skipped.append((src, "unsupported format"))
                continue

            try:
                style = (
                    style_resolver(src) if style_resolver is not None else None
                )
                # Per-photo decisions: the caller's live override (the
                # photo being edited right now) wins; otherwise read the
                # bucket journal (every other photo's saved edits);
                # otherwise fresh AUTO / no crop (handled downstream).
                rendered, used_params = _render_one(
                    src,
                    auto_on=auto_on,
                    cached_params=(
                        params_by_filename.get(src.name)
                        or get_process_params(b.journal, src.name)),
                    look_choice=get_process_look(b.journal, src.name),
                    crop_norm=(
                        crop_by_filename.get(src.name)
                        or get_process_crop(b.journal, src.name)),
                    crop_angle=float(
                        crop_angle_by_filename.get(src.name)
                        or get_process_crop_angle(b.journal, src.name)
                        or 0.0),
                    rotation=int(
                        rotation_by_filename.get(src.name)
                        or get_process_rotation(b.journal, src.name)
                        or 0),
                    aspect_label=bucket_aspect,
                    style=style,
                )
                if params_sink is not None:
                    params_sink[src.name] = {
                        f: getattr(used_params, f)
                        for f in used_params.__dataclass_fields__}
            except Exception as exc:                  # noqa: BLE001
                log.warning("Process render failed for %s: %s", src, exc)
                result.errors.append((src, f"render failed: {exc}"))
                continue

            try:
                suffix = (
                    ".jpg" if file_type is ExportFileType.JPEG else ".tif"
                )
                dest_name = src.stem + suffix
                final = dest_dir / dest_name
                existed = final.exists()
                renamed = existed and collision is CollisionPolicy.UNIQUE
                if renamed:
                    final = _unique_target(dest_dir, dest_name)
                _write_image(
                    rendered, final,
                    file_type=file_type, jpeg_quality=jpeg_quality,
                )
                _record_write(
                    result, src, final,
                    existed_before=existed, renamed=renamed,
                )
            except OSError as exc:
                log.warning("Process write failed for %s: %s", src, exc)
                result.errors.append((src, str(exc)))
            except Exception as exc:                  # noqa: BLE001
                log.warning("Process encode failed for %s: %s", src, exc)
                result.errors.append((src, f"encode failed: {exc}"))

    return result
