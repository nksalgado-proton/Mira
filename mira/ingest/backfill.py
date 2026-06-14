"""Backfill level writes — spec/57 §4.3, "the system works backwards".

``apply_edited_level`` turns a freshly-ingested event into one whose
Pick AND Edit phases read as done — the wizard's "Already edited"
landing (spec/57 §4.3.1):

* every captured item gets explicit ``phase_state`` picks at BOTH
  phases (``decided_at`` stamped — the configured defaults can never
  flip them);
* the same bytes stand under ``Edited Media/Imported/`` as NTFS
  hardlinks (copy fallback cross-volume; an existing foreign file is
  never overwritten — same posture as ``core.picked_media``);
* each item gets a ``lineage`` row in the external-return shape
  (``recipe_json`` NULL) with ``exported_at`` = the backfill moment, so
  the Cut picker reads the finals exactly like in-app exports
  (spec/54 §8) and the return scanner skips them (already in lineage).

Idempotent: a re-run (interrupted-backfill recovery) keeps in-place
links, re-creates missing ones at their recorded relpaths, and never
suffix-spirals. No Qt; gateway-facing (``mira.ingest`` may
import the gateway — ``core/`` may not).
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from core.path_builder import EDITED_MEDIA_DIR_NAME, edited_media_dir
from core.photo_thumb_cache import queue_export_thumb
from mira.store import models as m

log = logging.getLogger(__name__)

# Fixed English on disk (spec/57 §1) — the one subdir backfill places
# under Edited Media; LRC-class returns land in sibling subdirs later.
IMPORTED_SUBDIR_NAME = "Imported"


@dataclass
class EditedLevelReport:
    """What one ``apply_edited_level`` pass did."""

    items: int = 0             # captured items processed
    linked: int = 0            # Imported links in place (incl. kept)
    copied: int = 0            # cross-volume copy fallbacks among them
    lineage_rows: int = 0      # lineage rows written this pass
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _place(src: Path, dest: Path, report: EditedLevelReport) -> bool:
    """Hardlink ``src`` → ``dest`` (copy fallback). True on success."""
    try:
        os.link(src, dest)
    except OSError:
        try:
            shutil.copy2(src, dest)
            report.copied += 1
        except OSError as exc:
            report.errors.append(f"could not place {dest.name}: {exc}")
            return False
    return True


def apply_edited_level(eg, event_root: Path, *, now: str) -> EditedLevelReport:
    """Apply the "Already edited" landing level to a fresh event.

    ``eg`` is an open :class:`EventGateway`; ``event_root`` the event
    folder; ``now`` the ISO timestamp stamped into ``exported_at``.
    """
    report = EditedLevelReport()
    items = eg.items(provenance="captured")
    report.items = len(items)

    ids = [it.id for it in items]
    eg.set_items_phase_state(ids, "pick", "picked")
    eg.set_items_phase_state(ids, "edit", "picked")

    dest_dir = edited_media_dir(Path(event_root)) / IMPORTED_SUBDIR_NAME
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Re-run guard: the Imported lineage rows already recorded — each
    # item re-uses its recorded relpath instead of claiming a new name.
    prefix = f"{EDITED_MEDIA_DIR_NAME}/{IMPORTED_SUBDIR_NAME}/"
    recorded = {
        ln.source_item_id: ln.export_relpath
        for ln in eg.store.all(m.Lineage)
        if ln.phase == "edit" and ln.source_item_id
        and ln.export_relpath.startswith(prefix)
    }
    claimed = {Path(rel).name for rel in recorded.values()}

    def _same(a: Path, b: Path) -> bool:
        try:
            return os.path.samefile(a, b)
        except OSError:
            return False

    for it in items:
        if not it.origin_relpath:
            continue
        src = Path(event_root) / it.origin_relpath
        if not src.exists():
            report.errors.append(f"source missing: {it.origin_relpath}")
            continue

        rel = recorded.get(it.id)
        if rel is None:
            # Claim a free name — same-name finals from different
            # days/cameras flatten into one dir, so divert like the
            # ingest engine does ("name (2).ext"). An existing file
            # that already IS this item's bytes (samefile) is kept;
            # a foreign file blocks the name, never overwritten.
            base = Path(it.origin_relpath).name
            stem, suffix = Path(base).stem, Path(base).suffix
            name, n = base, 2
            while True:
                candidate = dest_dir / name
                if name not in claimed and (
                    not candidate.exists() or _same(src, candidate)
                ):
                    break
                name = f"{stem} ({n}){suffix}"
                n += 1
            claimed.add(name)
            dest = dest_dir / name
            if not dest.exists() and not _place(src, dest, report):
                continue
            rel = str(dest.relative_to(event_root)).replace("\\", "/")
            eg.record_lineage(m.Lineage(
                export_relpath=rel,
                phase="edit",
                source_kind="item",
                source_item_id=it.id,
                recipe_json=None,
                exported_at=now,
            ))
            report.lineage_rows += 1
            report.linked += 1
            # spec/63 slice 8 — Cut-grid thumb, background builder.
            queue_export_thumb(event_root, rel)
        else:
            # Recorded on a prior pass — keep or restore the link.
            dest = Path(event_root) / rel
            if dest.exists():
                report.linked += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if _place(src, dest, report):
                report.linked += 1

    log.info(
        "backfill edited level: %d item(s) → %d link(s) (%d copied), "
        "%d lineage row(s), %d error(s)",
        report.items, report.linked, report.copied,
        report.lineage_rows, len(report.errors),
    )
    return report
