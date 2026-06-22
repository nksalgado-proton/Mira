"""Cross-event Cut export — bytes flow (spec/81 Phase 2 + spec/94
Phase 4a-ii — repointed at mira.db).

Materialises a cross-event Cut as a directory of links / copies:

* **Export-kind members** — hardlink from the source event's
  ``Exported Media/<export_relpath>``. Hardlink fails cross-volume;
  falls back to a copy.
* **Grab-kind members** (spec/61 §6, §8) — copy from the source event's
  ``Original Media/<origin_relpath>``. Copies are never linked because
  Original Media is the byte-pristine tier (charter §3 + spec/57):
  taking a link would couple the Cut's output to the source event's
  pristine bytes, and the export must stay independent.

For each member the source event's root is resolved via the umbrella
:class:`mira.gateway.gateway.Gateway`. Members whose source event isn't
in the index (relocated, deleted) are reported under ``missing`` and
skipped — the export still produces a directory; the user sees the
warning. Atomic at the per-file level, idempotent at the directory
level (re-export overwrites identically-named target files).

spec/94 Phase 4a-ii: the cut + cut_member rows now live in **mira.db**
(spec/93 §3). The export pipeline reads them through the library
gateway in one query; no event.db is opened for membership lookups.
Per-member event_id still routes back to each source event's bytes.

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
    """Surface to the UI when export can't proceed (cut gone from the
    library, target unwritable, etc.)."""


@dataclass(frozen=True)
class _ResolvedMember:
    """One cut_member row resolved to its source-event root + bytes path.
    ``kind`` discriminates which copy/link strategy applies."""

    member_id: str
    kind: str                     # 'export' | 'grab'
    source_root: Path             # the source event's event_root
    source_relpath: str           # export_relpath OR origin_relpath
    event_id: str


def _safe_filename(rel: str) -> str:
    """Flatten a relpath to a single filename for the cross-event Cut
    output. Cross-event members come from multiple event roots, so a
    member from event A and a member from event B could share a relpath;
    the output folder needs distinct names. Replace path separators with
    ``_``."""
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

    Reads cut_member rows from **mira.db** via
    :meth:`LibraryGateway.cross_event_cut_members`, resolves each member
    to its source event's bytes, and links / copies into ``target`` with
    a flattened filename. Returns a summary dict ``{member_count, linked,
    copied, missing, members_missing}`` for the UI to surface.

    ``anchor_event_id`` is kept for back-compat with the legacy
    signature and ignored — the cut + its members live in the library
    store now (spec/93 §3); per-member event_id routes to the right
    source event for each link/copy.

    Raises :class:`CrossEventExportError` only when the export can't
    run at all (cut gone from mira.db, target unwritable). Per-member
    errors fall under ``missing``."""
    target = Path(target)
    if not target.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CrossEventExportError(
                f"could not create target {target}: {exc}") from exc

    lg = gateway.library_gateway()
    cut = lg.cross_event_cut(cut_id)
    if cut is None:
        raise CrossEventExportError(
            f"cross-event Cut {cut_id} is no longer in the library")
    rows = lg.cross_event_cut_members(cut_id)

    # Resolve each member's source-event root.
    resolved: list = []
    missing: list = []
    for r in rows:
        eid = r.event_id
        relpath = r.export_relpath or r.origin_relpath
        kind = r.kind
        if not relpath:
            missing.append((r.member_id, "no relpath"))
            continue
        source_root = _open_event_root(gateway, eid)
        if source_root is None:
            missing.append((r.member_id, f"source event {eid} gone"))
            continue
        resolved.append(_ResolvedMember(
            member_id=r.member_id, kind=kind,
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

    # Stamp the cut row's last_exported_at via the library gateway.
    try:
        lg.stamp_cross_event_cut_exported(cut_id)
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
