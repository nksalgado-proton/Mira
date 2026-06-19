"""One-shot: backfill missing lineage rows for orphan exported videos.

Symptom this fixes — Alaska 2026-06-19 (Nelson):

* The Edit export job rendered video clips into ``Exported Media/<day>/``
  and the .mp4 files exist on disk.
* But the lineage row for each clip was never written (commit-closure
  path that drove ``record_single_lineage`` didn't fire — root cause
  to be determined separately).
* Result: ``gw.exported_files()`` doesn't see them; the New Cut dialog
  shows zero videos; the Cut session picker has nothing to pick.

The external-returns scanner only sweeps ``Edited Media/`` (its Leg B,
spec/72 Model B), so it can't self-heal a file that landed directly
under ``Exported Media/``. This script does the missing sweep.

What it does for one event:

1. Walk every video file (``.mp4`` / ``.mov`` / ``.m4v`` / ``.webm``)
   under ``Exported Media/`` recursively.
2. Skip files that already have a lineage row (idempotent — safe to
   re-run).
3. For each orphan, match the filename stem against the gateway's
   item-stem map (the same matcher external_returns uses). A clip
   named ``<src_stem>_clip<N>.mp4`` prefix-matches the SOURCE video
   item — that's the correct lineage target (the clip is a
   derivative of the source video).
4. Write the lineage row with ``phase='edit'``, ``source_kind='item'``,
   ``provenance='mira_render'`` (default — these are Mira's own
   render outputs, not third-party returns).

Usage::

    python scripts/backfill_exported_video_lineage.py <event_root> [--commit]

Without ``--commit`` the script only prints what it would do (dry-run).
Pass ``--commit`` to actually write the rows.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Project import path — script runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mira.gateway.event_gateway import EventGateway  # noqa: E402
from mira.picked.external_returns import (              # noqa: E402
    _all_item_stems, _match_stem,
)
from mira.store import models as m                       # noqa: E402
from mira.store.repo import EventStore                   # noqa: E402

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}

log = logging.getLogger("backfill_exported_video_lineage")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("event_root", type=Path,
                        help="Path to the event folder (contains event.db).")
    parser.add_argument("--commit", action="store_true",
                        help="Actually write the lineage rows. Without this "
                             "flag the script only prints what it would do.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    event_root: Path = args.event_root.resolve()
    db = event_root / "event.db"
    if not db.is_file():
        print(f"error: no event.db under {event_root}", file=sys.stderr)
        return 2

    exported_root = event_root / "Exported Media"
    if not exported_root.is_dir():
        print(f"error: no Exported Media/ under {event_root}", file=sys.stderr)
        return 2

    store = EventStore.open(db)
    gw = EventGateway(store, event_root=event_root)

    known_exports = {ln.export_relpath for ln in gw.lineage()}
    stems = _all_item_stems(gw, event_root)

    orphans = []
    for f in sorted(exported_root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS:
            continue
        rel = f.relative_to(event_root).as_posix()
        if rel in known_exports:
            continue
        source_id = _match_stem(f.stem, stems)
        orphans.append((rel, source_id))

    if not orphans:
        print("nothing to backfill — every exported video already has a "
              "lineage row.")
        gw.close()
        return 0

    print(f"found {len(orphans)} orphan exported video(s):")
    for rel, source_id in orphans:
        if source_id is None:
            print(f"  UNMATCHED  {rel}  (no source item stem matched)")
        else:
            print(f"  match      {rel}  -> source_item_id={source_id}")

    writable = [(rel, src) for rel, src in orphans if src is not None]
    if not args.commit:
        print(f"\ndry-run: would write {len(writable)} lineage row(s). "
              f"Pass --commit to apply.")
        gw.close()
        return 0

    if not writable:
        print("\nno matchable rows to write; nothing committed.")
        gw.close()
        return 0

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    for rel, source_id in writable:
        try:
            gw.record_lineage(m.Lineage(
                export_relpath=rel,
                phase="edit",
                source_kind="item",
                source_item_id=source_id,
                recipe_json=None,
                exported_at=stamp,
                provenance="mira_render",
            ))
            written += 1
        except Exception as exc:                                       # noqa: BLE001
            log.warning("failed to record lineage for %s: %s", rel, exc)
    print(f"\ncommitted {written}/{len(writable)} lineage row(s).")
    gw.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
