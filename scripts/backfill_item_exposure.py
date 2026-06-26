"""One-shot: backfill missing EXIF exposure facets on existing item rows.

Symptom this fixes (spec/134 overlay regression):

* Events created via "Create from Past Photos" or the Capture (back-up-a-
  card) flow stored their item rows WITHOUT the exposure quartet
  (``aperture_f`` / ``shutter_speed_s`` / ``iso`` / ``focal_length_mm``),
  plus ``lens_model`` / ``flash_fired``. The ingest paths read the EXIF
  but dropped these fields before persisting (fixed going forward).
* Result: the Picker / Editor / Cut overlay's **Exposure** line (and the
  lens / flash half of **Camera**) never renders for those events — the
  columns are NULL.

The ingest bug is fixed for NEW events; this script repairs the EXISTING
rows by re-reading EXIF from each item's captured original on disk and
filling ONLY the columns that are currently NULL. The captured tree is
read-only here — we touch the DB, never the photos.

What it does per event.db:

1. List every item (including hidden days).
2. Select items whose exposure quartet is entirely NULL (the lost-info
   signature) and that still have a readable ``origin_relpath`` on disk.
3. Re-read EXIF (``read_exif_single``) and fill each NULL column that the
   file actually carries. Existing non-NULL values are never overwritten;
   files with no exposure data (screenshots, some phone clips) are skipped.

Idempotent — a second run finds nothing left to fill.

Usage::

    python scripts/backfill_item_exposure.py <path> [--commit]

``<path>`` may be a single event folder (contains ``event.db``) OR any
parent directory — the script finds every ``event.db`` beneath it, so you
can point it at your whole library root. Without ``--commit`` it is a
dry-run (prints what it would change); pass ``--commit`` to write.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

# Project import path — script runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.exif_reader import read_exif_batch  # noqa: E402
from mira.gateway.event_gateway import EventGateway  # noqa: E402
from mira.store.repo import EventStore  # noqa: E402

log = logging.getLogger("backfill_item_exposure")

#: The columns we repair. Order is cosmetic (report only).
_EXPOSURE_COLS = ("aperture_f", "shutter_speed_s", "iso", "focal_length_mm")


def _facets_from_exif(pe) -> dict:
    """The exposure quartet + lens + flash from a ``PhotoExif``, in the units
    the ``item`` columns expect, with 0 / empty → ``None`` (so unknowns stay
    NULL). Mirrors ``mira.ingest.engine._exif_facets`` exactly so a backfilled
    row is indistinguishable from a freshly-ingested one."""
    if pe is None:
        return {}
    return dict(
        aperture_f=(getattr(pe, "aperture", 0.0) or 0.0) or None,
        shutter_speed_s=(getattr(pe, "shutter_speed", 0.0) or 0.0) or None,
        iso=(getattr(pe, "iso", 0) or 0) or None,
        focal_length_mm=(getattr(pe, "focal_length", 0.0) or 0.0) or None,
        flash_fired=bool(getattr(pe, "flash_fired", False)),
        lens_model=(str(getattr(pe, "lens", "") or "").strip() or None),
    )


def _needs_backfill(item) -> bool:
    """True iff the item carries the lost-info signature — every exposure
    column NULL. We don't gate on lens / flash: an item missing all four
    exposure values is the unambiguous ingest-drop case, and we only ever
    fill NULLs, so re-reading is harmless for the rest."""
    return all(getattr(item, c, None) is None for c in _EXPOSURE_COLS)


def _backfill_event(db: Path, *, commit: bool) -> tuple[int, int, int]:
    """Repair one event.db. Returns (candidates, filled, skipped_no_data)."""
    event_root = db.parent
    store = EventStore.open(db)
    gw = EventGateway(store, event_root=event_root)
    candidates = filled = skipped = 0
    try:
        # Gather the items that need repair AND still have a file on disk.
        targets: list[tuple[object, Path]] = []
        for item in gw.items(include_hidden=True):
            if not _needs_backfill(item):
                continue
            rel = getattr(item, "origin_relpath", None)
            if not rel:
                continue  # virtual / unmaterialised — nothing on disk to read
            src = event_root / rel
            if src.is_file():
                targets.append((item, src))
        candidates = len(targets)
        if not targets:
            return 0, 0, 0

        # ONE ExifTool invocation for the whole event (read_exif_batch) — a
        # per-file read would spawn ExifTool thousands of times on a big
        # library. Map results back by path (batch order isn't guaranteed).
        exif_by_path = {
            Path(pe.path): pe for pe in read_exif_batch([p for _, p in targets])
            if pe is not None
        }

        for item, src in targets:
            facets = _facets_from_exif(exif_by_path.get(src))
            # Fill only columns that are currently NULL and that the file
            # actually carries (never clobber existing data, never write a
            # bare flash=False onto a frame that has no real exposure data).
            updates = {
                k: v for k, v in facets.items()
                if v is not None and getattr(item, k, None) is None
            }
            # ``flash_fired`` is a valid False — but only meaningful when the
            # frame has SOME exposure data; skip it for data-free files.
            has_real = any(updates.get(c) is not None for c in _EXPOSURE_COLS)
            if not has_real:
                skipped += 1
                continue
            if facets.get("flash_fired") is not None and item.flash_fired is None:
                updates["flash_fired"] = facets["flash_fired"]
            log.info("  %s  <- %s", item.origin_relpath,
                     ", ".join(f"{k}={updates[k]}" for k in sorted(updates)))
            if commit:
                gw.save_item(replace(item, **updates))
            filled += 1
    finally:
        gw.close()
    return candidates, filled, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path,
                        help="An event folder (with event.db) OR a parent / "
                             "library root containing many events.")
    parser.add_argument("--commit", action="store_true",
                        help="Actually write the rows. Without this flag the "
                             "script only prints what it would change.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    root: Path = args.path.resolve()
    if not root.exists():
        print(f"error: path does not exist: {root}", file=sys.stderr)
        return 2

    if (root / "event.db").is_file():
        dbs = [root / "event.db"]
    else:
        dbs = sorted(root.rglob("event.db"))
    if not dbs:
        print(f"error: no event.db found under {root}", file=sys.stderr)
        return 2

    print(f"{'COMMIT' if args.commit else 'DRY-RUN'}: scanning "
          f"{len(dbs)} event(s) under {root}\n")

    tot_cand = tot_fill = tot_skip = 0
    for db in dbs:
        print(f"event: {db.parent.name}")
        try:
            cand, fill, skip = _backfill_event(db, commit=args.commit)
        except Exception as exc:                                   # noqa: BLE001
            log.warning("  ERROR opening/scanning %s: %s", db, exc)
            continue
        if cand == 0:
            print("  nothing to backfill")
        else:
            verb = "filled" if args.commit else "would fill"
            print(f"  {verb} {fill} item(s); skipped {skip} with no EXIF "
                  f"exposure data ({cand} candidate(s))")
        tot_cand += cand
        tot_fill += fill
        tot_skip += skip

    verb = "filled" if args.commit else "would fill"
    print(f"\ntotal: {verb} {tot_fill} item(s) across {len(dbs)} event(s); "
          f"skipped {tot_skip} data-free; {tot_cand} candidate(s).")
    if not args.commit and tot_fill:
        print("dry-run — pass --commit to write these changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
