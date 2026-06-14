"""``Picked Media/`` — the derived links projection (spec/57 §2).

External software cannot read ``event.db``, so the Pick decisions get ONE
filesystem projection: a flat root of links to every picked, byte-bearing
item, plus one subdir per focus/exposure bracket holding links to the
picked members only (members never at the root — the root is for whole
items and merged results). Link names carry a deterministic
day+camera prefix (``D03_DC-G9M2_P1000001.RW2``) so they are
collision-free, sort by day in any tool, and identify their source for
the return leg (spec/57 §3.2 starts-with matching).

Lifecycle (locked): built on entering Edit + a manual Refresh action.
A rebuild may be brutal about ITS OWN links but must never touch real
bytes — external tools drop their outputs (merged stacks) at the root,
and those are the only irreplaceable files ever inside this dir until
ingest adopts them (spec/57 §2.3). Ownership is tracked in a manifest
(``.mira_links.json``: path + inode), and an entry is only deleted
while its recorded inode still matches — a foreign file occupying or
replacing a manifest path is always preserved.

Links are NTFS hardlinks (same volume by construction); a cross-volume
event falls back to copies (spec/57 §7.3 — the warning surface is a
deferred design; callers read ``RebuildResult.copied``).

Pure logic + filesystem — no Qt, no gateway import (CLAUDE.md
invariant #8). Callers assemble :class:`PickedEntry` rows (see
``mira.picked.edit_model.picked_media_entries``) and hand them
in with the event root.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from core.path_builder import picked_media_dir, sanitize_folder_name

log = logging.getLogger(__name__)

#: Ownership manifest, hidden-ish at the projection root. Records every
#: link/copy this module created as ``{"relpath", "ino", "dev"}`` so a
#: rebuild can tell its own artifacts from real bytes a tool dropped.
MANIFEST_NAME = ".mira_links.json"


@dataclass(frozen=True)
class PickedEntry:
    """One picked, byte-bearing item to project."""

    source_path: Path                       # absolute path to the real bytes
    filename: str                           # original filename (kept as the stem tail)
    day_number: Optional[int] = None        # None = undated → D00
    camera_id: Optional[str] = None
    bracket_group_id: Optional[str] = None  # focus/exposure bracket membership
    item_id: Optional[str] = None           # source item id (return-leg association)


@dataclass
class RebuildResult:
    linked: int = 0            # hardlinks in place after the rebuild
    copied: int = 0            # cross-volume copy fallbacks among them
    bracket_dirs: int = 0      # bracket subdirs in the projection
    removed: int = 0           # stale owned links removed
    preserved: int = 0         # foreign real files left untouched
    skipped_missing: int = 0   # entries whose source bytes are gone
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def link_name(entry: PickedEntry) -> str:
    """The deterministic projection name: ``D{day:02d}_{camera}_{filename}``.
    Undated items take ``D00`` (spec/57 §7.1 call); the camera id is
    sanitised for Windows but otherwise verbatim (spaces are legal)."""
    day = f"D{entry.day_number:02d}" if entry.day_number is not None else "D00"
    cam = sanitize_folder_name(entry.camera_id or "").strip() or "NOCAM"
    return f"{day}_{cam}_{entry.filename}"


def bracket_dir_name(bracket_group_id: str) -> str:
    """Subdir name for one focus/exposure bracket — the (sanitised)
    detector group id: stable across rebuilds, unique per bracket."""
    return sanitize_folder_name(bracket_group_id).strip() or "bracket"


def _load_manifest(root: Path) -> List[dict]:
    path = root / MANIFEST_NAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        return [e for e in entries if isinstance(e, dict) and "relpath" in e]
    except Exception as exc:  # noqa: BLE001 — a corrupt manifest must never block
        log.warning("picked-media manifest unreadable (%s) — treating as empty", exc)
        return []


def _write_manifest(root: Path, entries: List[dict]) -> None:
    """Atomic write-then-rename (CLAUDE.md invariant #6)."""
    path = root / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"version": 1, "entries": entries}, indent=1),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _stat_ino(path: Path) -> Optional[tuple]:
    try:
        st = path.stat()
        return (st.st_ino, st.st_dev)
    except OSError:
        return None


def foreign_root_files(event_root: Path) -> List[Path]:
    """Real files at the projection ROOT that this module does not own —
    i.e. external-tool outputs awaiting ingest (spec/57 §2.3: stackers
    write their merged result to the root). A file is *foreign* when it
    is absent from the manifest or its inode no longer matches the
    recorded one (replaced in place). The manifest itself and subdirs
    are excluded — bracket subdirs hold only links, and anything
    foreign inside them is preserved-but-ignored."""
    root = picked_media_dir(Path(event_root))
    if not root.is_dir():
        return []
    owned = {}
    for e in _load_manifest(root):
        owned[e["relpath"]] = (e.get("ino"), e.get("dev"))
    out: List[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_file() or child.name == MANIFEST_NAME:
            continue
        if child.name.endswith(".json.tmp"):
            continue                       # our own atomic-write residue
        rec = owned.get(child.name)
        if rec is not None and _stat_ino(child) == rec:
            continue                       # ours, unchanged
        out.append(child)
    return out


def rebuild_picked_media(
    event_root: Path, entries: Iterable[PickedEntry],
) -> RebuildResult:
    """(Re)build the projection at ``<event_root>/Picked Media/``.

    1. Remove every manifest-owned artifact whose recorded inode still
       matches (ours, unchanged). Anything else — tool outputs at the
       root, a foreign file occupying an owned path, an owned path whose
       inode changed under us — is preserved, always.
    2. Create the links for ``entries``: bracket members under one
       subdir per ``bracket_group_id``, everything else at the flat
       root. An existing path that already IS the right link (samefile)
       is kept as-is; an existing foreign file blocks that name (error,
       never overwritten).
    3. Prune now-empty bracket subdirs and write the fresh manifest.
    """
    result = RebuildResult()
    root = picked_media_dir(Path(event_root))
    root.mkdir(parents=True, exist_ok=True)

    # ── 1. Sweep owned artifacts (inode-guarded) ───────────────────────
    for old in _load_manifest(root):
        path = root / old["relpath"]
        current = _stat_ino(path)
        if current is None:
            continue                          # already gone
        recorded = (old.get("ino"), old.get("dev"))
        if current == recorded:
            try:
                path.unlink()
                result.removed += 1
            except OSError as exc:
                result.errors.append(f"could not remove stale link {path.name}: {exc}")
        else:
            # Replaced under us (delete+recreate = new inode) → real bytes now.
            result.preserved += 1

    # ── 2. Create the projection ───────────────────────────────────────
    manifest: List[dict] = []
    taken: set = set()
    bracket_dirs: set = set()
    for entry in entries:
        name = link_name(entry)
        if entry.bracket_group_id:
            sub = bracket_dir_name(entry.bracket_group_id)
            rel = f"{sub}/{name}"
            dest_dir = root / sub
        else:
            rel = name
            dest_dir = root
        if rel in taken:
            result.errors.append(f"duplicate projection name skipped: {rel}")
            continue
        taken.add(rel)
        src = Path(entry.source_path)
        if not src.exists():
            result.skipped_missing += 1
            continue
        dest = dest_dir / name
        dest_dir.mkdir(parents=True, exist_ok=True)
        if entry.bracket_group_id:
            bracket_dirs.add(dest_dir)
        if dest.exists():
            try:
                if os.path.samefile(src, dest):
                    pass                       # already the right link — keep it
                else:
                    # A real (foreign) file owns this name — never overwrite.
                    result.preserved += 1
                    result.errors.append(
                        f"name occupied by a real file, link skipped: {rel}")
                    continue
            except OSError as exc:
                result.errors.append(f"cannot inspect {rel}: {exc}")
                continue
        else:
            try:
                os.link(src, dest)
            except OSError:
                # Cross-volume (or filesystem without hardlinks) → copy.
                try:
                    shutil.copy2(src, dest)
                    result.copied += 1
                except OSError as exc:
                    result.errors.append(f"could not project {rel}: {exc}")
                    continue
        ino = _stat_ino(dest)
        if ino is not None:
            manifest.append({"relpath": rel, "ino": ino[0], "dev": ino[1]})
        result.linked += 1

    result.bracket_dirs = len(bracket_dirs)

    # ── 3. Prune empty dirs + persist ownership ───────────────────────
    for child in root.iterdir():
        if child.is_dir():
            try:
                next(child.iterdir())
            except StopIteration:
                try:
                    child.rmdir()
                except OSError:  # noqa: PERF203 — best-effort prune
                    pass
            except OSError:
                pass
    _write_manifest(root, manifest)
    log.info(
        "picked-media rebuild: %d linked (%d copied), %d bracket dirs, "
        "%d removed, %d preserved, %d missing, %d errors",
        result.linked, result.copied, result.bracket_dirs,
        result.removed, result.preserved, result.skipped_missing,
        len(result.errors),
    )
    return result
