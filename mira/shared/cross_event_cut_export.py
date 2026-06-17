"""Cross-event Cut export — bytes flow (spec/81 Phase 2 polish — Item 4 + 6).

The cross-event analogue of :mod:`mira.shared.cut_export`. Materialises a
cross-event Cut as a directory of links / copies:

* **Export-kind members** (the legacy lineage path) — hardlink from the
  source event's ``Exported Media/<export_relpath>``. Hardlink fails
  cross-volume; falls back to a copy.
* **Grab-kind members** (spec/61 §6, §8 — Item 6) — copy from the source
  event's ``Original Media/<origin_relpath>``. Copies are never linked
  because Original Media is the byte-pristine tier (charter §3 + spec/57):
  taking a link would couple the Cut's output to the source event's
  pristine bytes, and the export must stay independent.

For each member the source event's root is resolved via the umbrella
:class:`mira.gateway.gateway.Gateway`. Members whose source event isn't in
the index (relocated, deleted) are reported under ``missing`` and skipped —
the export still produces a directory; the user sees the warning. Atomic at
the per-file level (each link/copy operation), idempotent at the
directory level (re-export overwrites identically-named target files).

No Qt; the events page wires it from the cross-event Cuts list action.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class CrossEventExportError(RuntimeError):
    """Surface to the UI when export can't proceed (anchor event gone,
    target unwritable, etc.)."""


@dataclass(frozen=True)
class _ResolvedMember:
    """One cut_member row resolved to its source-event root + bytes path.
    ``kind`` discriminates which copy/link strategy applies."""

    member_id: str
    kind: str                     # 'export' | 'grab'
    source_root: Path             # the source event's event_root
    source_relpath: str           # export_relpath OR origin_relpath
    event_id: Optional[str]


def _safe_filename(rel: str) -> str:
    """Flatten a relpath to a single filename for the cross-event Cut
    output. Cross-event members come from multiple event roots, so a member
    from event A and a member from event B could share a relpath; the output
    folder needs distinct names. Replace path separators with ``_``."""
    rel = rel.replace("\\", "/")
    return rel.replace("/", "_")


def _hardlink_or_copy(src: Path, dst: Path) -> str:
    """Hardlink ``src`` to ``dst`` when possible (same volume), copy
    otherwise. Returns ``'linked'`` or ``'copied'`` for the summary."""
    try:
        os.link(src, dst)
        return "linked"
    except OSError as exc:
        log.debug("hardlink failed (%s) — falling back to copy", exc)
        shutil.copy2(src, dst)
        return "copied"


def _open_event_root(gateway, event_id: str) -> Optional[Path]:
    """Resolve an ``event_id`` to its on-disk root via the umbrella
    Gateway's index. ``None`` when the event has been removed / relocated
    out of band."""
    entry = gateway.index.get(event_id)
    if entry is None:
        return None
    return gateway.index.resolve_root(entry, gateway.photos_base_path())


def export_cross_event_cut(
    gateway,
    anchor_event_id: str,
    cut_id: str,
    *,
    target: Path,
) -> dict:
    """Materialise a cross-event Cut at ``target`` (a writable directory).

    Opens the anchor event, reads cut_member rows, resolves each member to
    its source event's bytes, and links / copies into ``target`` with a
    flattened filename. Returns a summary dict ``{member_count, linked,
    copied, missing, members_missing: [...]}`` for the UI to surface.

    Raises :class:`CrossEventExportError` only when the export can't run at
    all (anchor event gone, target unwritable). Per-member errors fall
    under ``missing``."""
    target = Path(target)
    if not target.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CrossEventExportError(
                f"could not create target {target}: {exc}") from exc

    anchor_root = _open_event_root(gateway, anchor_event_id)
    if anchor_root is None or not (anchor_root / "event.db").exists():
        raise CrossEventExportError(
            f"anchor event {anchor_event_id} is unresolvable")

    from mira.store.repo import EventStore
    members: list = []
    try:
        anchor_store = EventStore.open(anchor_root / "event.db")
    except Exception as exc:
        raise CrossEventExportError(
            f"could not open anchor event.db: {exc}") from exc
    try:
        rows = anchor_store.conn.execute(
            "SELECT member_id, kind, export_relpath, origin_relpath, "
            "event_id FROM cut_member WHERE cut_id = ?",
            (cut_id,),
        ).fetchall()
    finally:
        anchor_store.close()

    # Resolve each member's source-event root.
    resolved: list = []
    missing: list = []
    for r in rows:
        eid = r["event_id"] or anchor_event_id
        relpath = r["export_relpath"] or r["origin_relpath"]
        kind = r["kind"]
        if not relpath:
            missing.append((r["member_id"], "no relpath"))
            continue
        source_root = _open_event_root(gateway, eid)
        if source_root is None:
            missing.append((r["member_id"], f"source event {eid} gone"))
            continue
        resolved.append(_ResolvedMember(
            member_id=r["member_id"], kind=kind,
            source_root=source_root, source_relpath=relpath,
            event_id=eid,
        ))

    # Link / copy.
    linked = 0
    copied = 0
    for m in resolved:
        src = (m.source_root / m.source_relpath)
        if not src.is_file():
            missing.append((m.member_id, f"source bytes missing at {src}"))
            continue
        out_name = _safe_filename(m.source_relpath)
        dst = target / out_name
        # Idempotent re-export: remove the existing target file so link/
        # copy don't fail with EEXIST. shutil.copy2 would overwrite but
        # os.link raises on existing.
        if dst.exists():
            try:
                dst.unlink()
            except OSError:
                missing.append((m.member_id, f"could not unlink {dst}"))
                continue
        try:
            if m.kind == "grab":
                # Grabs always copy — Original Media is byte-pristine
                # (charter §3); a link would couple the export to those
                # bytes' lifecycle.
                shutil.copy2(src, dst)
                copied += 1
            else:
                action = _hardlink_or_copy(src, dst)
                if action == "linked":
                    linked += 1
                else:
                    copied += 1
        except OSError as exc:
            missing.append((m.member_id, f"link/copy failed: {exc}"))

    # Stamp the cut row's last_exported_at.
    try:
        anchor_store = EventStore.open(anchor_root / "event.db")
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            anchor_store.conn.execute(
                "UPDATE cut SET last_exported_at = ? WHERE id = ?",
                (now, cut_id))
            anchor_store.conn.commit()
        finally:
            anchor_store.close()
    except Exception as exc:                                # noqa: BLE001
        log.warning("could not stamp last_exported_at: %s", exc)

    return {
        "member_count": len(rows),
        "linked": linked,
        "copied": copied,
        "missing": len(missing),
        "members_missing": missing,
    }


__all__ = ["export_cross_event_cut", "CrossEventExportError"]
