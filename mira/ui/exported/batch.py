"""Export batch submission helper (spec/66 §1.1 + spec/68 §3).

Lifted from the retired ``mira/ui/exported/export_page.py`` (the flat-
grid MVP) so the redesigned per-day Export surface — the
:class:`~mira.ui.pages.days_grid_page.DaysGridPage` running in
``phase="export"`` mode (spec/68 §3) — can submit one day's green cells
to the spec/60 batch engine without duplicating the partition /
hardlink / commit-closure plumbing.

The engine + the :class:`~mira.ui.shell.batch_queue.BatchExportQueue`
contract are locked (spec/68 §4). This module only re-parents the
*trigger* — it builds a manifest, partitions third-party returns into
the hardlink path, and enqueues the rest with a commit closure that
records ``edit-phase`` lineage rows under ``Exported Media/`` and flips
``Adjustment.edit_exported = True`` for the units that actually
landed.

The hardlink + render contract is the same one the prior MVP
established: a Pick-kept item with an ``Edited Media/`` return (a
third-party tool's already-finished render, spec/57 §3) gets
hardlinked into ``Exported Media/`` instead of going through Mira's
tone pipeline; everything else renders.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtWidgets import QWidget

from mira.gateway.event_gateway import EventGateway
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
) -> bool:
    """Build the spec/60 manifest from ``cells`` and submit it through
    the app's :class:`BatchExportQueue`.

    * ``cells`` are the green ship-targets (the items that should land
      under ``Exported Media/``). Each carries its source path; the
      helper reads the recipe from the gateway.
    * ``day_labels`` maps ``day_number → human folder name``
      (``"Dia 1 — Description — 2026-04-01"`` etc.); the helper uses
      this to build the per-day ``dest_dir`` under ``Exported Media/``.
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
    from core.export_manifest import ExportManifest, PhotoUnit
    from core.path_builder import exported_media_dir
    from core.settings import load_settings
    from mira.ui.edited._lineage import (
        record_edit_export_lineage,
    )
    from mira.ui.edited.export_job import BatchExportJob

    if eg.event_root is None:
        log.warning("submit_export_batch: event_root is None")
        return False
    if not cells:
        log.info("submit_export_batch: no cells to ship; bailing")
        return False

    settings = load_settings()
    aspect_label = str(
        settings.get("preferred_aspect_ratio") or "Original")
    event_root = Path(eg.event_root)
    default_dest = exported_media_dir(event_root)

    # spec/66 §1.2 — partition: items with an ``Edited Media/`` return
    # get hardlinked synchronously; the rest go through the render
    # queue. (The hardlink path is its own commit + lineage write; the
    # render path's commit closure handles the render units only.)
    to_hardlink: List[tuple[ExportCell, str]] = []
    to_render: List[ExportCell] = []
    for c in cells:
        return_rel = eg.edit_candidate_relpath(c.item_id)
        if return_rel:
            to_hardlink.append((c, return_rel))
        else:
            to_render.append(c)

    if to_hardlink:
        _hardlink_third_party_returns(
            eg, to_hardlink, event_root, default_dest, day_labels)

    units: list[PhotoUnit] = []
    source_by_unit_id: Dict[str, Path] = {}
    for c in to_render:
        dest_dir = str(default_dest / day_labels.get(c.day_number, ""))
        adj = eg.adjustment(c.item_id)
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
        units.append(PhotoUnit(
            unit_id=c.item_id,
            source=str(c.path),
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
        ))
        source_by_unit_id[c.item_id] = c.path

    if not units:
        # Every green cell was a third-party return; the hardlink path
        # already shipped + committed lineage. Nothing left for the
        # render queue.
        return True

    manifest = ExportManifest(
        units=tuple(units), clips=(), collision="unique")
    worker = BatchExportJob(manifest, source_by_unit_id)
    render_cells = list(to_render)

    def commit(result) -> None:
        """Per-unit truth (spec/60 §5). Lifted verbatim from the prior
        flat-grid MVP, including the observability log lines added by
        the Inseto fix (commit 4017cd8)."""
        ok_ids = getattr(result, "ok_unit_ids", set())
        if not ok_ids:
            total = len(render_cells)
            log.warning(
                "submit_export_batch: batch finished with NO ok units "
                "(submitted %d render unit(s)); nothing committed. The "
                "Exported Media re-scan will backfill any orphans on "
                "the next entry.", total)
            return
        ok_cells = [c for c in render_cells if c.item_id in ok_ids]
        if len(ok_cells) < len(ok_ids):
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

    if batch_queue is None:
        show_error(
            parent_widget,
            tr("Batch queue unavailable"),
            tr(
                "The app's batch queue isn't reachable — try "
                "restarting Mira."
            ),
        )
        return False
    worker.finished.connect(worker.deleteLater)
    batch_queue.enqueue(
        worker,
        tr("Export — {name} ({n})")
        .replace("{name}", event_name)
        .replace("{n}", str(len(units))),
        commit,
    )
    return True


def _hardlink_third_party_returns(
    eg: EventGateway,
    to_hardlink: List[tuple],
    event_root: Path,
    dest_root: Path,
    day_labels: Dict[Optional[int], str],
) -> None:
    """spec/66 §1.2 — hardlink each third-party return from
    ``Edited Media/`` into ``Exported Media/<day>/`` and record an
    ``Exported Media/`` lineage row + ``set_edit_exported``. Copy
    fallback when hardlink fails (cross-volume), mirroring the spec/57
    return-scan policy. Lifted verbatim from the prior MVP."""
    from os import link as _hardlink

    for cell, src_relpath in to_hardlink:
        src_path = event_root / src_relpath
        if not src_path.exists():
            log.warning(
                "submit_export_batch: third-party return missing on "
                "disk: %s — falling back to render", src_path)
            continue
        day_dir = dest_root / day_labels.get(cell.day_number, "")
        try:
            day_dir.mkdir(parents=True, exist_ok=True)
        except Exception:                                           # noqa: BLE001
            log.exception(
                "submit_export_batch: cannot create %s — skipping "
                "hardlink", day_dir)
            continue
        dest_path = day_dir / src_path.name
        stem, ext = dest_path.stem, dest_path.suffix
        i = 2
        while dest_path.exists():
            dest_path = day_dir / f"{stem} ({i}){ext}"
            i += 1
        try:
            _hardlink(str(src_path), str(dest_path))
        except OSError:
            try:
                import shutil
                shutil.copy2(str(src_path), str(dest_path))
            except Exception:                                       # noqa: BLE001
                log.exception(
                    "submit_export_batch: hardlink + copy fallback "
                    "failed for %s -> %s", src_path, dest_path)
                continue
        try:
            eg.set_edit_exported(cell.item_id, True)
        except Exception:                                           # noqa: BLE001
            log.exception(
                "submit_export_batch: set_edit_exported failed for %s",
                cell.item_id)
        try:
            from mira.ui.edited._lineage import record_single_lineage
            record_single_lineage(
                eg, event_root,
                item_id=cell.item_id, dest_path=dest_path,
                recipe=recipe_for_item(eg, cell.item_id),
            )
        except Exception:                                           # noqa: BLE001
            log.exception(
                "submit_export_batch: record_single_lineage failed "
                "for %s", cell.item_id)


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
    "ExportCell", "recipe_for_item", "submit_export_batch",
    "day_label_for",
]
