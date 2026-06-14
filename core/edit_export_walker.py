"""The spec/56 slice-4 walker — picked segments (and their sources)
collected as :class:`~core.export_manifest.ClipUnit` for the spec/60
batch engine.

A picked SEGMENT (provenance ``clip``) becomes one clip unit; its
source is the parent video item. The plan is built where the gateway
lives (spec/60 §1 — the worker never re-resolves anything), using
the same :func:`core.video_export.build_export_plan` the workshop
single-clip Export uses (parity by construction).

Snapshots (photo-shaped items, full photo treatment per spec/56)
travel through the PHOTO walker — they're already there as `kind ==
"photo"` items in the host's day buckets.

Pure logic — no Qt. The gateway is read-only here; resolution
calls (look compile, rep-frame extract) stay where they belong.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from core.export_manifest import ClipUnit
from core.video_export import ExportPlan, build_export_plan
from core.video_segments import segment_bounds

log = logging.getLogger(__name__)


def _segment_range_ms(
    eg, video_item_id: str, seg_index: int,
) -> Optional[tuple[int, int]]:
    """``(in_ms, out_ms)`` for one segment of a source video, derived
    from the stored markers (spec/56 §1). ``None`` when the geometry
    can't be reconstructed (missing item / duration)."""
    video_item = eg.item(video_item_id)
    if video_item is None or not video_item.duration_ms:
        return None
    try:
        markers = eg.video_markers(video_item_id)
        bounds = segment_bounds(
            [mk.at_ms for mk in markers], int(video_item.duration_ms))
    except Exception:                                       # noqa: BLE001
        log.exception("walker: bad marker geometry for %s",
                      video_item_id)
        return None
    if not bounds or not (0 <= seg_index < len(bounds)):
        return None
    return bounds[seg_index]


def _plan_dict(
    plan: ExportPlan,
) -> dict:
    """ExportPlan → JSON-friendly dict (the wire shape :func:`core.
    render_worker._render_clip_unit` reverses)."""
    d = {
        "in_ms": plan.in_ms,
        "out_ms": plan.out_ms,
        "params": asdict(plan.params),
        "crop_norm": (list(plan.crop_norm)
                      if plan.crop_norm is not None else None),
        "box_angle": plan.box_angle,
        "include_audio": plan.include_audio,
        "audio_volume": plan.audio_volume,
        "audio_fade_ms": plan.audio_fade_ms,
        "speed": plan.speed,
        "stabilise": plan.stabilise,
        "src_fps": plan.src_fps,
        "filter_recipe": plan.filter_recipe,
        "filter_amount": plan.filter_amount,
    }
    return d


def _segment_source_path(video_item) -> Optional[Path]:
    """Resolve a source video Item's bytes-on-disk. Items use
    ``origin_relpath`` against the event root — captured-clip exports
    must read the ORIGINAL bytes, not any derived form."""
    relpath = getattr(video_item, "origin_relpath", None)
    return Path(relpath) if relpath else None


def build_clip_units(
    eg,
    segment_rows: Iterable,
    *,
    event_root: Path,
    dest_dir_for_video: Callable[[object], str],
    resolved_params_for: Optional[Callable[[object], object]] = None,
    override_shim: Optional[Callable[[object, object], object]] = None,
) -> List[ClipUnit]:
    """Translate picked SEGMENT rows (:class:`mira.store.models.
    VideoSegment`) into ClipUnits.

    Each row carries ``item_id`` (the segment Item) + ``video_item_id``
    (the source video) + ``seg_index`` — segment identity per spec/56.

    The caller supplies:

    * ``event_root`` — segment sources resolve as ``event_root /
      video_item.origin_relpath`` (the path convention all
      photo-walker paths already use).
    * ``dest_dir_for_video(video_item) -> str`` — the day folder
      (``Edited Media/<Dia N>/``) for the SOURCE video's day; the
      destination follows the source.
    * ``resolved_params_for(video_adjustment)`` — the workshop's tone
      compiler (rep-frame extract + ``compute_look_params``); the
      walker stays Qt-free by accepting the callable instead of
      doing the work itself.
    * ``override_shim(video_adjustment, params)`` — the duck shape
      :func:`build_export_plan` consumes (kept inside the workshop
      so the walker doesn't pull in its module surface).

    Segments with missing geometry or missing source files are
    skipped with a log line — they never appear in the manifest,
    so the worker can never trip on them mid-batch.
    """
    out: List[ClipUnit] = []
    for seg in segment_rows:
        video_item = eg.item(seg.video_item_id)
        if video_item is None:
            log.info("walker: source video missing for segment %s",
                     seg.item_id)
            continue
        rel = _segment_source_path(video_item)
        if rel is None:
            log.info("walker: video item %s has no origin path",
                     seg.video_item_id)
            continue
        source = Path(event_root) / rel
        if not source.is_file():
            log.info("walker: source video file missing for %s (%s)",
                     seg.item_id, source)
            continue
        rng = _segment_range_ms(eg, seg.video_item_id, seg.seg_index)
        if rng is None:
            log.info("walker: bad segment geometry for %s", seg.item_id)
            continue
        in_ms, out_ms = rng

        adj = eg.video_adjustment(seg.item_id)
        params = (resolved_params_for(adj)
                  if (resolved_params_for is not None and adj is not None)
                  else None)
        override = (override_shim(adj, params)
                    if override_shim is not None else None)
        # Source fps — probe_video would touch ffmpeg, deliberately
        # NOT done here (the walker is gateway-only and called inside
        # the UI thread); the runner re-probes at render time, the
        # plan's src_fps is a hint only.
        src_fps = 30.0
        try:
            plan = build_export_plan(
                override, clip_start_ms=in_ms, clip_end_ms=out_ms,
                src_fps=src_fps)
        except Exception:                                   # noqa: BLE001
            log.exception("walker: build_export_plan failed for %s",
                          seg.item_id)
            continue

        # Deterministic name: source stem + 1-based seg_index. A day
        # with multiple picks from the same source never collides; the
        # collision policy still arbitrates re-exports across runs.
        base_name = f"{source.stem}_clip{seg.seg_index + 1}"
        out.append(ClipUnit(
            unit_id=seg.item_id,
            source=str(source),
            dest_dir=dest_dir_for_video(video_item),
            base_name=base_name,
            plan=_plan_dict(plan),
            style=(adj.style if adj is not None else None),
        ))
    return out


__all__ = ["build_clip_units"]
