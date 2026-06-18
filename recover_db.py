"""recover_db.py — diagnose Mira SQLite corruption and find the newest CLEAN backup.

READ-ONLY. This script never modifies, deletes, or restores anything. It opens
each database (and each backup) read-only, runs ``PRAGMA integrity_check``, and
prints which files are CLEAN vs CORRUPT plus the exact copy command to restore
the newest clean one. You do the actual restore by hand (so nothing is lost by
surprise).

Context (2026-06-17): the user-store ``mira.db`` reported "database disk image
is malformed" / "Rowid out of order", which crashed the app when opening a day
to Pick. This finds a good backup to roll back to.

Usage (na raiz do repo, com o mesmo Python que roda o Mira):

    python recover_db.py
    python recover_db.py --library "D:\\Photos\\_mira_events" --appdata "%LOCALAPPDATA%\\Mira"
    python recover_db.py --full          # integrity_check completo (mais lento)

Defaults: library = D:\\Photos\\_mira_events ; appdata = %LOCALAPPDATA%\\Mira
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _check(db: Path, full: bool) -> tuple[str, str]:
    """Return (verdict, detail). verdict ∈ {CLEAN, CORRUPT, UNREADABLE}."""
    try:
        # immutable=1 opens read-only and never writes a -wal/-shm sidecar.
        uri = f"file:{db.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except Exception as exc:  # noqa: BLE001
        return "UNREADABLE", f"{type(exc).__name__}: {exc}"
    try:
        pragma = "integrity_check" if full else "quick_check"
        rows = conn.execute(f"PRAGMA {pragma}").fetchall()
        msg = "; ".join(str(r[0]) for r in rows) if rows else "ok"
        if msg.strip().lower() == "ok":
            return "CLEAN", "ok"
        return "CORRUPT", msg[:200]
    except Exception as exc:  # noqa: BLE001
        return "CORRUPT", f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()


def _mtime(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S")
    except OSError:
        return "?"


def _size(p: Path) -> str:
    try:
        return f"{p.stat().st_size/1024:.0f} KB"
    except OSError:
        return "?"


def _report(title: str, live: Path | None, backups: list[Path], full: bool):
    print("=" * 78)
    print(title)
    print("=" * 78)
    candidates: list[tuple[Path, bool]] = []  # (path, is_live)
    if live is not None:
        candidates.append((live, True))
    for b in sorted(backups, key=lambda p: p.stat().st_mtime if p.exists()
                    else 0, reverse=True):
        candidates.append((b, False))

    if not candidates:
        print("  (nenhum ficheiro encontrado)")
        print()
        return

    clean_backups: list[Path] = []
    for p, is_live in candidates:
        if not p.exists():
            continue
        verdict, detail = _check(p, full)
        tag = "LIVE  " if is_live else "backup"
        mark = {"CLEAN": "[OK]   ", "CORRUPT": "[CORR] ",
                "UNREADABLE": "[????] "}[verdict]
        print(f"  {mark}{tag}  {_mtime(p)}  {_size(p):>9}  {p.name}")
        if verdict != "CLEAN":
            print(f"           └─ {detail}")
        if verdict == "CLEAN" and not is_live:
            clean_backups.append(p)
        if verdict == "CLEAN" and is_live:
            # Live is clean — nothing to restore for this db.
            clean_backups.insert(0, p)

    print()
    # Recommend the newest CLEAN backup (excluding the live file).
    newest_clean = None
    for p in sorted(clean_backups, key=lambda p: p.stat().st_mtime,
                    reverse=True):
        if live is not None and p == live:
            continue
        newest_clean = p
        break
    if live is not None:
        live_verdict, _ = _check(live, full) if live.exists() else ("MISSING", "")
        if live.exists() and live_verdict == "CLEAN":
            print(f"  → O ficheiro ATIVO está ÍNTEGRO ({live.name}). "
                  f"Nada a restaurar.")
        elif newest_clean is not None:
            print(f"  → RESTAURAR do backup íntegro mais recente:")
            print(f"        {newest_clean}")
            print(f"     (de {_mtime(newest_clean)})")
            print(f"     Passos seguros (com o Mira FECHADO):")
            print(f"        1) guarde o corrompido:  copy \"{live}\" \"{live}.corrupt\"")
            print(f"        2) restaure:             copy /Y \"{newest_clean}\" \"{live}\"")
            print(f"        3) apague sidecars WAL:  del \"{live}-wal\" \"{live}-shm\" 2>nul")
            sha = live.with_suffix(live.suffix + ".sha256")
            print(f"        4) (se existir) apague o sidecar de hash p/ ser "
                  f"recalculado: del \"{sha}\" 2>nul")
        else:
            print("  → NENHUM backup íntegro encontrado para este banco. "
                  "NÃO sobrescreva o ativo ainda — peça ajuda antes.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default=r"D:\Photos\_mira_events")
    ap.add_argument("--appdata", default=os.path.expandvars(
        r"%LOCALAPPDATA%\Mira"))
    ap.add_argument("--full", action="store_true",
                    help="integrity_check completo (lento) em vez de quick_check")
    args = ap.parse_args()

    library = Path(args.library)
    appdata = Path(args.appdata)
    full = args.full

    print(f"Library : {library}   (existe={library.is_dir()})")
    print(f"AppData : {appdata}   (existe={appdata.is_dir()})")
    print(f"Modo    : {'integrity_check (completo)' if full else 'quick_check (rápido)'}")
    print()

    # ── user-store mira.db (vive no AppData; snapshots na library) ──
    live_userstore = appdata / "mira.db"
    us_backups: list[Path] = []
    us_backups += sorted(appdata.glob("mira.db.bak*"))
    us_snap_dir = library / ".mira-backups" / "user-store"
    if us_snap_dir.is_dir():
        us_backups += sorted(us_snap_dir.glob("*.db"))
    _report("USER-STORE  (mira.db — o índice de eventos)",
            live_userstore, us_backups, full)

    # ── cada event.db ──
    backups_root = library / ".mira-backups"
    if library.is_dir():
        for event_dir in sorted(p for p in library.iterdir() if p.is_dir()):
            if event_dir.name == ".mira-backups":
                continue
            event_db = event_dir / "event.db"
            if not event_db.exists():
                continue
            snaps: list[Path] = []
            # snapshots are keyed by event id (a folder under .mira-backups);
            # we don't know the id from the folder name, so scan all snap dirs
            # and match by being a valid event.db (cheap: just offer all).
            ev_backups: list[Path] = []
            if backups_root.is_dir():
                for sub in backups_root.iterdir():
                    if sub.is_dir() and sub.name != "user-store":
                        ev_backups += sorted(sub.glob("*.db"))
            _report(f"EVENT  {event_dir.name}  ({event_db})",
                    event_db, ev_backups, full)

    print("=" * 78)
    print("Nada foi alterado. Restaure manualmente seguindo os passos acima,")
    print("com o Mira FECHADO. Depois cole a saída aqui se quiser conferência.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
