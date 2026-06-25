"""Export batch submission helper (spec/66 §1.1 + spec/68 §3 +
spec/89 Model B).

Lifted from the retired ``mira/ui/exported/export_page.py`` (the flat-
grid MVP) so the redesigned per-day Export surface — the
:class:`~mira.ui.pages.days_grid_page.DaysGridPage` running in
``phase="export"`` mode (spec/68 §3) — can submit one day's green cells
to the spec/60 batch engine without duplicating the commit-closure
plumbing.

The engine + the :class:`~mira.ui.shell.batch_queue.BatchJobQueue`
contract are locked (spec/68 §4; spec/84 §2 renamed the queue from
``BatchExportQueue`` to ``BatchJobQueue`` so ingest can ride it too).
This module only re-parents the *trigger* — it builds a manifest and
enqueues it with a commit closure that records ``edit-phase`` lineage
rows under ``Exported Media/`` and flips
``Adjustment.edit_exported = True`` for the units that actually landed.

**spec/89 Model B (Slice 1).** Third-party returns no longer take a
hardlink fork through this helper — the return scanner hardlinks each
new ``Edited Media/`` file straight into ``Exported Media/`` at scan
time (see :func:`mira.gateway.event_gateway.EventGateway.scan_for_returns`).
By the time a batch reaches :func:`submit_export_batch` every cell is
a Mira-render target.

**Clips + snapshots (spec/56).** Picked SEGMENTS feed the spec/56
slice-4 :func:`~core.edit_export_walker.build_clip_units` walker and
land as :class:`~core.export_manifest.ClipUnit` rows on the same
manifest — the spec/60 §3 clip lane renders them with the workshop's
adjustments (markers ARE the trim; tone/crop/audio/speed ride the
:class:`~mira.store.models.VideoAdjustment`). Picked SNAPSHOTS get
their source frame extracted to a temp JPEG at submit time and ship
through the existing PhotoUnit path — full photo treatment (spec/56
§1).
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtWidgets import QWidget

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.ui.design import show_error
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


@dataclass
class ExportCell:
    """One unit's worth of input for the Export batch — the smallest
    shape the submit helper needs."""

    item_id: str
    path: Path
    day_number: Optional[int] = None
    # spec/118 §3 — OVERRIDE path: pin this cell's dest_dir to the
    # existing lineage row's parent so the atomic replace lands at the
    # SAME ``export_relpath`` even if the day folder name has drifted
    # since the last export. ``None`` falls back to ``day_labels`` (the
    # default keep-both path, unchanged from spec/89 §5).
    dest_dir_override: Optional[str] = None


@dataclass
class SnapshotCell:
    """A picked snapshot pending Export. The frame is extracted to a
    deterministic temp JPEG at submit time so the existing PhotoUnit
    pipeline can ship it (spec/56 §1 — full photo treatment)."""

    item_id: str
    video_item_id: str
    at_ms: int
    day_number: Optional[int] = None


def _extract_snapshot_jpeg(
    source_video: Path, at_ms: int, *, item_id: str,
) -> Optional[Path]:
    """Extract one frame from the source video as a JPEG ready to feed
    the spec/60 photo lane. The temp filename's stem is the snapshot's
    item id so :func:`record_edit_export_lineage`'s stem-keyed map can
    re-attach the dest back to the snapshot item (lineage parity with
    photos). Returns ``None`` on extraction failure — the caller drops
    the cell so a partial run never blocks the rest of the batch."""
    from core.video_extract import extract_frame

    tmp_dir = Path(tempfile.gettempdir()) / "mira_snap_export"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("snapshot extract: cannot create %s", tmp_dir)
        return None
    out_path = tmp_dir / f"{item_id}.jpg"
    try:
        extract_frame(source_video, int(at_ms), out_path, timeout=20.0)
    except Exception:                                          # noqa: BLE001
        log.exception(
            "snapshot extract failed for %s @ %dms", source_video, at_ms)
        return None
    if not out_path.is_file():
        return None
    return out_path


def _resolved_segment_params(
    eg: EventGateway, adj, video_item, event_root: Path,
):
    """Compile the segment's Look on a rep-frame from the source video
    (spec/54 §3.2). Returns identity :class:`~core.photo_render.Params`
    when the look is ``"original"`` / no adjustment row / extraction
    fails — the spec/56 fallback (the clip still trims to its marker
    bounds even if tone compilation can't run)."""
    from core.photo_render import Params

    if adj is None:
        return Params()
    look = (adj.look or "natural").lower()
    if look == "original":
        return Params()
    if not video_item or not video_item.origin_relpath:
        return Params()
    src = event_root / video_item.origin_relpath
    if not src.is_file():
        return Params()

    from core.photo_auto import compute_look_params
    from core.photo_decoder import decode_image
    from core.video_extract import extract_frame

    rep_ms = int(adj.rep_frame_ms or 0)
    if video_item.duration_ms:
        rep_ms = max(0, min(rep_ms, int(video_item.duration_ms)))
    tmp_dir = Path(tempfile.gettempdir()) / "mira_repframe_export"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Params()
    tmp = tmp_dir / f"{video_item.id}_{rep_ms}.jpg"
    try:
        extract_frame(src, rep_ms, tmp, timeout=20.0)
        arr = decode_image(tmp)
    except Exception:                                          # noqa: BLE001
        log.exception(
            "rep-frame extract/decode failed for %s @ %dms", src, rep_ms)
        return Params()
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    if arr is None:
        return Params()
    style = (adj.style or "general")
    try:
        return compute_look_params(arr, style=style, look=look)
    except Exception:                                          # noqa: BLE001
        log.exception(
            "compute_look_params failed for segment look=%r style=%r",
            look, style)
        return Params()


class _SegmentOverride:
    """The duck shape :func:`core.video_export.build_export_plan`
    consumes — built per-segment from its
    :class:`~mira.store.models.VideoAdjustment` + the
    compiled-on-rep-frame :class:`~core.photo_render.Params`. Kept here
    so the walker stays Qt-/workshop-free (it accepts the shim as a
    callable)."""

    def __init__(self, adj, params) -> None:
        from core.photo_auto import (
            creative_filter_amount,
            resolve_filter_recipe,
        )
        self.params = params
        crop = None
        if adj is not None and all(v is not None for v in (
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
            crop = (
                float(adj.crop_x), float(adj.crop_y),
                float(adj.crop_w), float(adj.crop_h),
            )
        self.crop_norm = crop
        self.box_angle = float(getattr(adj, "box_angle", 0.0) or 0.0)
        self.include_audio = (
            True if adj is None else bool(adj.include_audio))
        self.audio_volume = (
            float(adj.audio_volume) if adj is not None else 1.0)
        self.audio_fade_ms = (
            int(adj.audio_fade_ms) if adj is not None else 0)
        self.speed = (
            float(adj.speed) if adj is not None and adj.speed else 1.0)
        self.stabilise = (
            float(adj.stabilise) if adj is not None else 0.0)
        if adj is not None and adj.creative_filter:
            try:
                self.filter_recipe = resolve_filter_recipe(
                    adj.creative_filter, adj.style or "general")
            except Exception:                                  # noqa: BLE001
                log.exception(
                    "resolve_filter_recipe failed for %r",
                    adj.creative_filter)
                self.filter_recipe = None
            self.filter_amount = creative_filter_amount(
                adj.creative_filter)
        else:
            self.filter_recipe = None
            self.filter_amount = 1.0


def recipe_for_item(eg: EventGateway, item_id: str) -> dict:
    """The spec/54 §8 lineage-snapshot CHOICE for one item — read from
    its :class:`Adjustment` row. Identical to the prior MVP's
    ``_recipe_for_item``; lifted here so the helper is self-contained."""
    recipe: dict = {"look": "natural"}
    adj = eg.adjustment(item_id)
    if adj is None:
        return recipe
    recipe["look"] = adj.look or "natural"
    if adj.style:
        recipe["style"] = adj.style
    if adj.creative_filter:
        recipe["creative_filter"] = adj.creative_filter
    if abs(float(adj.look_strength or 1.0) - 1.0) > 1e-6:
        recipe["look_strength"] = float(adj.look_strength)
    if all(v is not None for v in (
            adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
        recipe["crop_norm"] = [
            adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h]
    if adj.crop_angle:
        recipe["crop_angle"] = adj.crop_angle
    if adj.rotation:
        recipe["rotation"] = adj.rotation
    if adj.aspect_label:
        recipe["aspect_label"] = adj.aspect_label
    return recipe


def submit_export_batch(
    eg: EventGateway,
    settings_repo,
    batch_queue,
    *,
    event_name: str,
    cells: List[ExportCell],
    day_labels: Dict[Optional[int], str],
    parent_widget: QWidget,
    segment_rows: Optional[List[m.VideoSegment]] = None,
    snapshot_cells: Optional[List[SnapshotCell]] = None,
    collision: str = "unique",
) -> bool:
    """Build the spec/60 manifest from ``cells`` and submit it through
    the app's :class:`BatchJobQueue` (spec/84 rename — the queue serves
    both exports and ingest now).

    * ``cells`` are the green PHOTO ship-targets (the items that should
      land under ``Exported Media/``). Each carries its source path; the
      helper reads the recipe from the gateway.
    * ``day_labels`` maps ``day_number → human folder name``
      (``"Dia 1 — Description — 2026-04-01"`` etc.); the helper uses
      this to build the per-day ``dest_dir`` under ``Exported Media/``.
    * ``segment_rows`` are picked SEGMENTS (spec/56) — each becomes a
      :class:`~core.export_manifest.ClipUnit` via the
      :func:`core.edit_export_walker.build_clip_units` walker. The
      destination follows the source video's day.
    * ``snapshot_cells`` are picked SNAPSHOTS (spec/56) — each has a
      frame extracted from the source video at ``at_ms`` and joins the
      normal :class:`~core.export_manifest.PhotoUnit` set so the spec/60
      photo lane renders the picked Adjustment (look/crop/filter) on top.
    * ``settings_repo`` is :attr:`Gateway.settings` — the repo, not the
      loaded value (the helper calls
      :func:`core.settings.load_settings` for the aspect-ratio key
      directly, matching the prior MVP).

    Returns ``True`` when the batch was enqueued, ``False`` when the
    queue is unreachable or the green set is empty after the hardlink
    partition. The render itself is async — completion fires the
    commit closure on the queue's UI thread.
    """
    from core.cull_export import ExportFileType
    from core.edit_export_walker import build_clip_units
    from core.export_manifest import ExportManifest, PhotoUnit
    from core.path_builder import exported_media_dir
    from core.settings import load_settings
    from mira.ui.edited._lineage import (
        record_edit_export_lineage,
        record_single_lineage,
    )
    from mira.ui.edited.export_job import BatchExportJob

    if eg.event_root is None:
        log.warning("submit_export_batch: event_root is None")
        return False
    segment_rows = list(segment_rows or [])
    snapshot_cells = list(snapshot_cells or [])
    if not cells and not segment_rows and not snapshot_cells:
        log.info("submit_export_batch: no cells to ship; bailing")
        return False

    settings = load_settings()
    aspect_label = str(
        settings.get("preferred_aspect_ratio") or "Original")
    event_root = Path(eg.event_root)
    default_dest = exported_media_dir(event_root)

    # spec/89 Slice 1 — third-party returns enter the ship set at scan
    # time, so every cell here is a Mira-render target. The legacy
    # partition (edit_candidate_relpath → hardlink fork) is gone.

    units: list[PhotoUnit] = []
    source_by_unit_id: Dict[str, Path] = {}

    def _photo_unit(
        item_id: str, src: Path, dest_dir: str,
    ) -> Optional[PhotoUnit]:
        """Build one PhotoUnit from an item's photo
        :class:`~mira.store.models.Adjustment`. Used for both regular
        photos AND snapshots (a snapshot is a photo item — spec/56)."""
        adj = eg.adjustment(item_id)
        look = None
        crop_norm = None
        crop_angle = 0.0
        rotation = 0
        style = None
        if adj is not None:
            look = {"look": adj.look or "natural"}
            if adj.style:
                look["style"] = adj.style
                style = adj.style
            if adj.creative_filter:
                look["creative_filter"] = adj.creative_filter
            if abs(float(adj.look_strength or 1.0) - 1.0) > 1e-6:
                look["strength"] = float(adj.look_strength)
            if all(v is not None for v in (
                    adj.crop_x, adj.crop_y,
                    adj.crop_w, adj.crop_h)):
                crop_norm = (
                    adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)
            crop_angle = float(adj.crop_angle or 0.0)
            rotation = int(adj.rotation or 0)
        return PhotoUnit(
            unit_id=item_id,
            source=str(src),
            dest_dir=dest_dir,
            # PhotoUnit.file_type holds the Enum *value*; passing the
            # name-cased ``"JPEG"`` was the Inseto silent-fail
            # (commit 4017cd8). Use .value so a member rename can't
            # reintroduce the bug.
            file_type=ExportFileType.JPEG.value,
            jpeg_quality=92,
            look=look,
            auto_on=True,
            style=style,
            crop_norm=crop_norm,
            crop_angle=crop_angle,
            rotation=rotation,
            aspect_label=aspect_label,
        )

    for c in cells:
        if c.dest_dir_override is not None:
            dest_dir = str(event_root / c.dest_dir_override)
        else:
            dest_dir = str(default_dest / day_labels.get(c.day_number, ""))
        units.append(_photo_unit(c.item_id, c.path, dest_dir))
        source_by_unit_id[c.item_id] = c.path

    # spec/56 snapshots — extract the source frame to a deterministic
    # temp JPEG (stem == snapshot item_id so the stem-keyed lineage
    # writer can re-attach), then ship via the photo lane. A failed
    # extract drops the snapshot from the batch with a warning.
    snapshot_temp_paths: list[Path] = []
    snapshot_render_cells: list[ExportCell] = []
    for s in snapshot_cells:
        video_item = eg.item(s.video_item_id)
        if video_item is None or not video_item.origin_relpath:
            log.warning(
                "submit_export_batch: snapshot %s has no source video",
                s.item_id)
            continue
        source_video = event_root / video_item.origin_relpath
        if not source_video.is_file():
            log.warning(
                "submit_export_batch: snapshot %s source missing on "
                "disk (%s)", s.item_id, source_video)
            continue
        frame_path = _extract_snapshot_jpeg(
            source_video, s.at_ms, item_id=s.item_id)
        if frame_path is None:
            continue
        snapshot_temp_paths.append(frame_path)
        dest_dir = str(default_dest / day_labels.get(s.day_number, ""))
        units.append(_photo_unit(s.item_id, frame_path, dest_dir))
        source_by_unit_id[s.item_id] = frame_path
        snapshot_render_cells.append(ExportCell(
            item_id=s.item_id, path=frame_path, day_number=s.day_number,
        ))

    # spec/56 picked segments — through the slice-4 walker. Destination
    # follows the SOURCE video's day (matching photos shipped from the
    # same day's grid). Unresolvable segments (missing source / bad
    # geometry) are logged + dropped by the walker itself.
    seg_to_video_id: Dict[str, str] = {
        sg.item_id: sg.video_item_id for sg in segment_rows}

    def _dest_dir_for_video(video_item) -> str:
        return str(default_dest / day_labels.get(
            getattr(video_item, "day_number", None), ""))

    def _resolved_for(adj):
        if adj is None:
            return None
        video_id = seg_to_video_id.get(adj.item_id)
        video_item = eg.item(video_id) if video_id else None
        return _resolved_segment_params(eg, adj, video_item, event_root)

    clip_units = build_clip_units(
        eg, segment_rows,
        event_root=event_root,
        dest_dir_for_video=_dest_dir_for_video,
        resolved_params_for=_resolved_for,
        override_shim=_SegmentOverride,
    )
    for cu in clip_units:
        source_by_unit_id[cu.unit_id] = Path(cu.source)

    if not units and not clip_units:
        # Empty manifest after snapshot-extract failures + walker drops.
        # The caller short-circuits on empty input, so this only fires
        # when every render lane lost its sources mid-build.
        return True

    # spec/118 §3 — caller picks the collision policy. "unique" stays
    # the default (today's keep-both behaviour, "(2)" suffix); "override"
    # atomically replaces the existing file in place so a re-edited
    # export refreshes without forming a versions cluster + leaves Cut
    # membership untouched (the file path / lineage row are reused).
    from core.export_manifest import COLLISION_OVERRIDE, COLLISION_UNIQUE
    norm_collision = (
        COLLISION_OVERRIDE
        if str(collision).lower() in (COLLISION_OVERRIDE, "override")
        else COLLISION_UNIQUE
    )
    manifest = ExportManifest(
        units=tuple(units), clips=tuple(clip_units),
        collision=norm_collision,
    )
    worker = BatchExportJob(manifest, source_by_unit_id)
    render_cells = list(cells) + list(snapshot_render_cells)
    clip_unit_lookup = {cu.unit_id: cu for cu in clip_units}

    def commit(result) -> None:
        """Per-unit truth (spec/60 §5). Lifted verbatim from the prior
        flat-grid MVP, including the observability log lines added by
        the Inseto fix (commit 4017cd8).

        spec/56 clips: ``BatchJobResult.ok_clip_results`` carries the
        worker's per-clip finals; each gets its own
        :func:`record_single_lineage` row + ``set_edit_exported``
        flip — the photo lineage writer's stem-keyed map is photo-only
        (clip stems are ``videostem_clipN`` and would mis-attach to
        the parent video item)."""
        ok_ids = getattr(result, "ok_unit_ids", set())
        ok_clip_results = getattr(result, "ok_clip_results", []) or []
        if not ok_ids and not ok_clip_results:
            total = len(render_cells) + len(clip_unit_lookup)
            log.warning(
                "submit_export_batch: batch finished with NO ok units "
                "(submitted %d render unit(s)); nothing committed. The "
                "Exported Media re-scan will backfill any orphans on "
                "the next entry.", total)
            _cleanup_snapshot_temps(snapshot_temp_paths)
            return
        # Photo lane (incl. snapshots) — set_edit_exported + lineage.
        ok_cells = [c for c in render_cells if c.item_id in ok_ids]
        if len(ok_cells) < len([
                i for i in ok_ids if i not in clip_unit_lookup]):
            log.warning(
                "submit_export_batch: %d ok unit(s) but only %d match "
                "render_cells — some unit_ids did not round-trip.",
                len(ok_ids), len(ok_cells))
        for c in ok_cells:
            try:
                eg.set_edit_exported(c.item_id, True)
            except Exception:                                       # noqa: BLE001
                log.exception(
                    "submit_export_batch: set_edit_exported failed "
                    "for %s", c.item_id)
        try:
            record_edit_export_lineage(
                eg, Path(eg.event_root),
                items_with_sources=[
                    (c.item_id, c.path) for c in ok_cells
                ],
                result=result,
                recipe_by_item={
                    c.item_id: recipe_for_item(eg, c.item_id)
                    for c in ok_cells
                },
                resolved_by_stem=getattr(result, "resolved_by_name", {}),
            )
        except Exception:                                           # noqa: BLE001
            log.exception(
                "submit_export_batch: record_edit_export_lineage failed")

        # Clip lane — one lineage row per shipped segment + the
        # edit-exported flip, the same shape the workshop single-clip
        # Export used (record_single_lineage). spec/54 §8 versions-
        # as-exports holds: a re-shipped segment lands as `(2).mp4`
        # via the worker's _NameReserver and gets its own lineage row.
        #
        # Belt-and-braces (Alaska 2026-06-19): wrap each clip in its
        # own try so a single bad clip — bad recipe lookup, transient
        # DB error — never breaks the survivors. The Alaska bug shipped
        # 3 .mp4 files with zero lineage rows; the most likely cause is
        # the loop crashing mid-iteration. The orphan-healer Leg D in
        # ``scan_for_returns`` is the structural safety net; this is
        # the prevention layer.
        clip_writes = clip_errors = 0
        for msg in ok_clip_results:
            uid = msg.get("unit_id")
            final_str = msg.get("final_path")
            if not uid or not final_str:
                log.warning(
                    "submit_export_batch: clip msg missing unit_id / "
                    "final_path (%s); skipping", msg)
                clip_errors += 1
                continue
            final_path = Path(final_str)
            try:
                eg.set_edit_exported(uid, True)
            except Exception:                                       # noqa: BLE001
                log.exception(
                    "submit_export_batch: set_edit_exported failed "
                    "for clip %s", uid)
            try:
                cu = clip_unit_lookup.get(uid)
                recipe = recipe_for_item(eg, uid) if cu else None
                if cu is not None and cu.style and recipe is not None:
                    recipe.setdefault("style", cu.style)
            except Exception:                                       # noqa: BLE001
                log.exception(
                    "submit_export_batch: recipe lookup failed for "
                    "clip %s — proceeding with no recipe so the "
                    "lineage row still lands", uid)
                recipe = None
            try:
                ok = record_single_lineage(
                    eg, Path(eg.event_root),
                    item_id=uid, dest_path=final_path,
                    recipe=recipe,
                    resolved_params=msg.get("params"),
                    # spec/144 — the clip's TRUE on-disk duration ms
                    # the worker computed ((out_ms - in_ms) / speed).
                    # Lands on lineage so the budget / cut-play /
                    # PTE all read the segment length, not the source
                    # video's whole duration.
                    duration_ms=msg.get("duration_ms"),
                )
                if ok:
                    clip_writes += 1
                else:
                    clip_errors += 1
                    log.warning(
                        "submit_export_batch: record_single_lineage "
                        "returned False for clip %s -> %s",
                        uid, final_path)
            except Exception:                                       # noqa: BLE001
                log.exception(
                    "submit_export_batch: record_single_lineage failed "
                    "for clip %s", uid)
                clip_errors += 1
        if ok_clip_results:
            log.info(
                "submit_export_batch: clip lane committed — %d lineage "
                "row(s) written, %d error(s) (of %d ok clip msg(s))",
                clip_writes, clip_errors, len(ok_clip_results))
        _cleanup_snapshot_temps(snapshot_temp_paths)

    if batch_queue is None:
        show_error(
            parent_widget,
            tr("Batch queue unavailable"),
            tr(
                "The app's batch queue isn't reachable — try "
                "restarting Mira."
            ),
        )
        _cleanup_snapshot_temps(snapshot_temp_paths)
        return False
    worker.finished.connect(worker.deleteLater)
    # spec/84 §2 — the progress line now owns the verb prefix
    # (``Exporting …`` for ``job_type="export"``); the label carries only
    # the descriptive tail.
    batch_queue.enqueue(
        worker,
        tr("{name} ({n})")
        .replace("{name}", event_name)
        .replace("{n}", str(len(units) + len(clip_units))),
        commit,
        job_type="export",
    )
    return True


def _cleanup_snapshot_temps(paths: List[Path]) -> None:
    """Best-effort cleanup of the temp JPEGs we extracted for snapshot
    Export. Called on commit completion AND on submit-bailout. Failures
    are swallowed — a stale temp file is benign."""
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:                                          # noqa: BLE001
            log.debug("snapshot temp cleanup failed: %s", p,
                      exc_info=True)


def day_label_for(
    eg: EventGateway, day_number: Optional[int],
) -> str:
    """Build the ``Exported Media/<this>`` folder name for a day.
    Lifted from the prior MVP so the day-grid Export trigger keeps the
    same on-disk layout."""
    if day_number is None:
        return ""
    try:
        days = {d.day_number: d for d in eg.trip_days()}
        td = days.get(day_number)
        if td is not None:
            bits = [b for b in (
                f"Dia {td.day_number}", td.description, td.date,
            ) if b]
            return " — ".join(bits) if bits else f"Dia {td.day_number}"
    except Exception:                                               # noqa: BLE001
        log.debug("day-label fallback for %s", day_number, exc_info=True)
    return f"Dia {day_number}"


__all__ = [
    "ExportCell", "SnapshotCell", "recipe_for_item",
    "submit_export_batch", "day_label_for",
]
