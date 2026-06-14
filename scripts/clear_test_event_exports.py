"""Clear export leftovers from the test events under D:\\Photos\\_mira_events.

Resets the two Inseto test events so the app reads them as un-exported,
without touching Original Media/ or losing the rest of event.db. Built
for one-off test-data hygiene; not part of the app surface.

What it does (in --apply mode):
  * DB: delete every lineage row whose export_relpath sits under
    ``Exported Media/`` or ``Edited Media/`` (cut_member cascades via
    FK). Then set ``adjustment.edit_exported = 0`` everywhere so the
    Edit-phase exported chip + the spec/59 §8 watermark never paint.
    VACUUM at the end.
  * Disk: drop ``Exported Media/`` entirely; remove the specific
    files in ``Edited Media/`` that the lineage rows pointed at (so
    the LRC roundtrip subdir + any other non-export content sitting
    in Edited Media survives); remove ``03 - Processed/`` and
    ``04 - Cuts/`` legacy folders if present. Original Media/ is
    never touched, Picked Media/ is never touched, Cuts/ (the
    spec/61 canonical share folder) is never touched.

Safety:
  * Dry-run by DEFAULT — prints the report, exits before any write.
  * --apply backs up event.db to ``event.db.bak.<ISO timestamp>``
    before any change.
  * Idempotent: a second --apply run finds nothing to do.

Usage::

    # Dry-run report:
    python scripts/clear_test_event_exports.py

    # Actually do the cleanup (creates event.db.bak.*):
    python scripts/clear_test_event_exports.py --apply
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Path patterns that DB lineage rows can have but we want gone.
_DB_PREFIXES = ("Exported Media/", "Edited Media/")

# Disk folders to wipe outright when present.
_DISK_FOLDERS_TO_DELETE = (
    "Exported Media",
    "03 - Processed",
    "04 - Cuts",
)

# Disk folder we surgically clean — only the export files appearing in
# lineage are removed; anything else (LRC roundtrip subdir, etc.) is
# left alone.
_EDITED_MEDIA = "Edited Media"

# Event-name substrings that identify the test events. Lowercase.
_TEST_NAME_HINTS = ("inseto",)

# How to address the events root.
_ROOT = Path(r"D:\Photos\_mira_events")


def _find_test_events(root: Path) -> list[Path]:
    """Return event folders under ``root`` whose name matches a test
    hint. Stable order = sorted alphabetically."""
    if not root.exists():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "event.db").exists():
            continue
        name_lower = child.name.lower()
        if any(h in name_lower for h in _TEST_NAME_HINTS):
            found.append(child)
    return found


def _human_bytes(n: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024


def _folder_stats(path: Path) -> tuple[int, int]:
    """Return ``(file_count, total_bytes)`` under ``path``, or (0, 0)
    when missing."""
    if not path.exists():
        return 0, 0
    files = 0
    size = 0
    for p in path.rglob("*"):
        if p.is_file():
            files += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return files, size


def _collect_report(event_root: Path) -> dict:
    """Build a pure-data report for one event (no writes)."""
    db_path = event_root / "event.db"
    con = sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        lineage_rows: list[tuple[str, str]] = []
        for prefix in _DB_PREFIXES:
            cur = con.execute(
                "SELECT export_relpath, phase FROM lineage "
                "WHERE export_relpath LIKE ? ORDER BY export_relpath",
                (prefix + "%",),
            )
            lineage_rows.extend((r["export_relpath"], r["phase"]) for r in cur)

        # cut_member rows that would cascade via the FK.
        cut_member_to_drop = 0
        if lineage_rows:
            placeholders = ",".join("?" * len(lineage_rows))
            cut_member_to_drop = con.execute(
                f"SELECT COUNT(*) FROM cut_member "
                f"WHERE export_relpath IN ({placeholders})",
                [r[0] for r in lineage_rows],
            ).fetchone()[0]

        adjustments_to_clear = con.execute(
            "SELECT COUNT(*) FROM adjustment WHERE edit_exported = 1"
        ).fetchone()[0]
    finally:
        con.close()

    # Disk side.
    exported_media = event_root / "Exported Media"
    exported_files, exported_bytes = _folder_stats(exported_media)

    legacy_dirs: list[tuple[Path, int, int]] = []
    for name in ("03 - Processed", "04 - Cuts"):
        p = event_root / name
        if p.exists():
            n, b = _folder_stats(p)
            legacy_dirs.append((p, n, b))

    # Files in Edited Media/ that match a DB lineage row — only those
    # are export outputs; LRC and friends stay.
    edited_media = event_root / _EDITED_MEDIA
    files_in_edited_media_to_delete: list[Path] = []
    edited_bytes = 0
    for relpath, _phase in lineage_rows:
        if not relpath.startswith("Edited Media/"):
            continue
        p = event_root / relpath
        if p.exists() and p.is_file():
            files_in_edited_media_to_delete.append(p)
            try:
                edited_bytes += p.stat().st_size
            except OSError:
                pass

    return {
        "event_root": event_root,
        "event_name": event_root.name,
        "db_path": db_path,
        "lineage_rows": lineage_rows,
        "cut_member_to_drop": cut_member_to_drop,
        "adjustments_to_clear": adjustments_to_clear,
        "exported_media_dir": exported_media,
        "exported_media_files": exported_files,
        "exported_media_bytes": exported_bytes,
        "legacy_dirs": legacy_dirs,
        "edited_media_dir": edited_media,
        "edited_files_to_delete": files_in_edited_media_to_delete,
        "edited_files_bytes": edited_bytes,
    }


def _print_report(rep: dict) -> bool:
    """Pretty-print one event's report. Returns True if there's anything
    to do (so the caller can early-exit when everything's already clean)."""
    print(f"\n=== {rep['event_name']} ===")
    print(f"  root: {rep['event_root']}")
    print(f"  db:   {rep['db_path']}")
    print()

    has_lineage = bool(rep["lineage_rows"])
    has_adj = rep["adjustments_to_clear"] > 0
    has_exported_dir = rep["exported_media_dir"].exists()
    has_edited_files = bool(rep["edited_files_to_delete"])
    has_legacy = bool(rep["legacy_dirs"])
    will_do_anything = (
        has_lineage or has_adj or has_exported_dir
        or has_edited_files or has_legacy
    )
    if not will_do_anything:
        print("  (nothing to do — already clean)")
        return False

    print(f"  DB — lineage rows to delete: {len(rep['lineage_rows'])}")
    for relpath, phase in rep["lineage_rows"][:6]:
        print(f"      [{phase}] {relpath}")
    if len(rep["lineage_rows"]) > 6:
        print(f"      ... ({len(rep['lineage_rows']) - 6} more)")

    print(
        "  DB — cut_member rows that will cascade-delete: "
        f"{rep['cut_member_to_drop']}")
    print(
        "  DB — adjustment rows to set edit_exported=0: "
        f"{rep['adjustments_to_clear']}")

    print()
    if has_exported_dir:
        print(
            f"  Disk — DELETE folder: {rep['exported_media_dir'].name}/ "
            f"({rep['exported_media_files']} files, "
            f"{_human_bytes(rep['exported_media_bytes'])})")
    else:
        print("  Disk — no Exported Media/ on disk")

    if has_edited_files:
        n = len(rep["edited_files_to_delete"])
        print(
            f"  Disk — DELETE {n} export file(s) in Edited Media/ "
            f"({_human_bytes(rep['edited_files_bytes'])})")
        # Show a few examples.
        rel_root = rep["event_root"]
        for p in rep["edited_files_to_delete"][:4]:
            try:
                print(f"      {p.relative_to(rel_root)}")
            except ValueError:
                print(f"      {p}")
        if n > 4:
            print(f"      ... ({n - 4} more)")
    else:
        print(
            "  Disk — no Edited Media/ export files to remove "
            "(LRC subdir + any other content untouched)")

    if has_legacy:
        for p, n, b in rep["legacy_dirs"]:
            print(
                f"  Disk — DELETE legacy folder: {p.name}/ "
                f"({n} files, {_human_bytes(b)})")
    else:
        print("  Disk — no legacy 03-/04- folders present")

    print()
    print("  Untouched: Original Media/, Picked Media/, "
          "Cuts/ (spec/61 canonical), event.db backups")
    return True


def _backup_db(db_path: Path) -> Path:
    """Snapshot ``event.db`` to ``event.db.bak.<UTC timestamp>``."""
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    target = db_path.with_suffix(f".db.bak.{ts}")
    shutil.copy2(db_path, target)
    return target


def _apply(rep: dict) -> dict:
    """Execute the deletes for one event. Returns a small summary dict."""
    db_path = rep["db_path"]
    backup_path = _backup_db(db_path)

    # DB pass — one transaction so a crash doesn't half-apply.
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        with con:
            n_lin = 0
            for prefix in _DB_PREFIXES:
                cur = con.execute(
                    "DELETE FROM lineage WHERE export_relpath LIKE ?",
                    (prefix + "%",))
                n_lin += cur.rowcount or 0
            cur = con.execute(
                "UPDATE adjustment SET edit_exported = 0 "
                "WHERE edit_exported = 1")
            n_adj = cur.rowcount or 0
        con.execute("VACUUM")
    finally:
        con.close()

    # Disk pass — one folder / one file at a time so a permission error
    # on one file doesn't bail out the rest.
    folders_deleted: list[Path] = []
    files_deleted = 0
    empty_dirs_deleted: list[Path] = []
    event_root = rep["event_root"]

    # Exported Media/ + legacy folders.
    for name in _DISK_FOLDERS_TO_DELETE:
        p = event_root / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=False)
            folders_deleted.append(p)

    # Specific export files in Edited Media/.
    parents_touched: set[Path] = set()
    for p in rep["edited_files_to_delete"]:
        try:
            p.unlink()
            files_deleted += 1
            parents_touched.add(p.parent)
        except FileNotFoundError:
            pass
    # Sweep day-folders that ended up empty (a re-export later rebuilds
    # them). Stops at Edited Media/ — never removes the Edited Media/
    # root itself even if empty (it's the spec/57 canonical sink).
    edited_root = event_root / _EDITED_MEDIA
    for parent in sorted(parents_touched, key=lambda p: -len(p.parts)):
        if parent == edited_root:
            continue
        try:
            if not any(parent.iterdir()):
                parent.rmdir()
                empty_dirs_deleted.append(parent)
        except OSError:
            pass

    return {
        "backup_path": backup_path,
        "lineage_deleted": n_lin,
        "adjustments_cleared": n_adj,
        "folders_deleted": folders_deleted,
        "files_deleted": files_deleted,
        "empty_dirs_deleted": empty_dirs_deleted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help=("Actually execute the cleanup. Default is dry-run "
              "(print the report and exit without changes)."))
    parser.add_argument(
        "--root", default=str(_ROOT),
        help="Events root (default: D:\\Photos\\_mira_events).")
    parser.add_argument(
        "--name", action="append",
        help=("Only target events whose name contains this substring "
              "(case-insensitive). May be repeated. Defaults to the "
              "Inseto test events."))
    args = parser.parse_args()

    global _TEST_NAME_HINTS
    root = Path(args.root)
    hints = tuple(s.lower() for s in args.name) if args.name else _TEST_NAME_HINTS
    # Swap module-level hints with the user's pick.
    _TEST_NAME_HINTS = hints
    events = _find_test_events(root)
    if not events:
        print(f"No matching events found under {root}. Hints: {hints!r}")
        return 1

    print(f"Targeting {len(events)} event(s) under {root}:")
    for e in events:
        print(f"  • {e.name}")
    print()
    print("Mode:", "APPLY (will modify disk + DB)" if args.apply else "DRY-RUN")

    reports = []
    for e in events:
        rep = _collect_report(e)
        _print_report(rep)
        reports.append(rep)

    if not args.apply:
        print()
        print("(dry-run — nothing changed. Re-run with --apply to execute.)")
        return 0

    print()
    print("=== APPLYING ===")
    for rep in reports:
        # Skip events with literally nothing to do — still produces a
        # backup, which is wasteful.
        nothing_to_do = (
            not rep["lineage_rows"]
            and rep["adjustments_to_clear"] == 0
            and not rep["exported_media_dir"].exists()
            and not rep["edited_files_to_delete"]
            and not rep["legacy_dirs"]
        )
        if nothing_to_do:
            print(f"  {rep['event_name']}: nothing to do, skipped")
            continue
        print(f"\n  {rep['event_name']}")
        summary = _apply(rep)
        print(f"    backup:           {summary['backup_path'].name}")
        print(f"    lineage deleted:  {summary['lineage_deleted']}")
        print(f"    adjustments=0:    {summary['adjustments_cleared']}")
        for f in summary["folders_deleted"]:
            print(f"    folder removed:   {f.name}/")
        print(f"    files deleted:    {summary['files_deleted']}")
        for d in summary["empty_dirs_deleted"]:
            try:
                rel = d.relative_to(rep['event_root'])
            except ValueError:
                rel = d
            print(f"    empty dir gone:   {rel}")

    print()
    print("Done. Re-run without --apply to confirm everything is clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
