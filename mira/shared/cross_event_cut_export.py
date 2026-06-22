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

from mira.shared.cut_export import _dedup_filename, _place

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
    otherwise. Returns ``'linked'`` or ``'copied'`` for the summary.

    spec/105 §5 — kept as a thin wrapper over the new shared
    :func:`mira.shared.cut_export._place` so the cross-event path
    and the per-event path share the same primitive."""
    linked = _place(src, dst, force_copy=False)
    return "linked" if linked else "copied"


def _open_event_root(gateway, event_id: str) -> Optional[Path]:
    """Resolve an ``event_id`` to its on-disk root via the umbrella
    Gateway's index. ``None`` when the event has been removed / relocated
    out of band."""
    entry = gateway.index.get(event_id)
    if entry is None:
        return None
    return gateway.index.resolve_root(entry, gateway.photos_base_path())


def _origin_index_for_source_event(
    gateway, source_event_id: str,
) -> dict:
    """spec/105 §3 — build a ``{export_relpath -> origin_relpath}`` map
    for one source event so each cross-event member can resolve in
    O(1) without reopening the event per member.

    Joins the event's lineage to its items: for every exported
    lineage row, look up the source item and read its
    ``origin_relpath``. Returns ``{}`` when the source event can't
    be opened (relocated, deleted) — the caller surfaces those
    members via ``missing_originals``."""
    try:
        eg = gateway.open_event(source_event_id)
    except Exception:                                              # noqa: BLE001
        log.exception(
            "cross-event originals: cannot open source event %s",
            source_event_id)
        return {}
    try:
        try:
            item_origin = {
                it.id: (getattr(it, "origin_relpath", None) or None)
                for it in eg.items()
            }
            index: dict = {}
            for ln in eg.exported_files():
                origin = item_origin.get(
                    getattr(ln, "source_item_id", None))
                if not origin:
                    continue
                key = str(ln.export_relpath).replace("\\", "/")
                index[key] = origin
            return index
        except Exception:                                          # noqa: BLE001
            log.exception(
                "cross-event originals: index build failed for %s",
                source_event_id)
            return {}
    finally:
        try:
            eg.close()
        except Exception:                                          # noqa: BLE001
            pass


def export_cross_event_cut(
    gateway,
    anchor_event_id: str,
    cut_id: str,
    *,
    target: Path,
    include_originals: bool = False,
    copy_mode: bool = False,
) -> dict:
    """Materialise a cross-event Cut at ``target`` (a writable directory).

    Reads cut_member rows from **mira.db** via
    :meth:`LibraryGateway.cross_event_cut_members`, resolves each member
    to its source event's bytes, and links / copies into ``target`` with
    a flattened filename. Returns a summary dict ``{member_count, linked,
    copied, missing, members_missing, originals_linked, originals_copied,
    missing_originals}`` for the UI to surface.

    ``anchor_event_id`` is kept for back-compat with the legacy
    signature and ignored — the cut + its members live in the library
    store now (spec/93 §3); per-member event_id routes to the right
    source event for each link/copy.

    spec/105 §3 — ``include_originals=True`` places each member's
    source ``origin_relpath`` (resolved per member against its source
    event's root) under ``<target>/Original Media/``. Members with no
    origin (no ``origin_relpath`` on the cut_member row) skip; missing
    bytes go into ``missing_originals``.

    spec/105 §5 — ``copy_mode=True`` forces ``shutil.copy2`` for media
    AND originals. Default ``False`` hardlinks per member with a
    cross-volume copy fallback. Grab-kind members ALWAYS copy (charter
    §3 — Original Media is byte-pristine; a link couples the output
    to those bytes' lifecycle), regardless of ``copy_mode``.

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
    originals_linked = 0
    originals_copied = 0
    missing_originals: list = []
    # spec/105 §3 — build a per-source-event lineage→origin index ONCE
    # so the per-member loop is O(1). Done only when originals are
    # requested and there's at least one export-kind member from each
    # event; the dict for an unopenable source event is empty.
    origin_index: dict = {}
    if include_originals:
        for eid in {m.event_id for m in resolved if m.kind == "export"}:
            origin_index[eid] = _origin_index_for_source_event(
                gateway, eid)
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
                # bytes' lifecycle. `copy_mode` is moot here.
                shutil.copy2(src, dst)
                copied += 1
            else:
                if _place(src, dst, force_copy=copy_mode):
                    linked += 1
                else:
                    copied += 1
        except OSError as exc:
            missing.append((m.member_id, f"link/copy failed: {exc}"))
            continue

        # spec/105 §3 — Original Media/ subdir, resolved against the
        # member's OWN source-event root (cross-event members span
        # multiple roots / volumes). Skipped for grab-kind members —
        # their source IS already the original byte-stream in
        # ``Original Media/<origin_relpath>``, so the flat output
        # IS the original; duplicating into the subdir would just
        # be the same bytes again.
        if include_originals and m.kind == "export":
            origin_rel = origin_index.get(m.event_id, {}).get(
                str(m.source_relpath).replace("\\", "/"))
            if not origin_rel:
                missing_originals.append((m.member_id, "no origin"))
                continue
            origin_src = m.source_root / origin_rel
            if not origin_src.is_file():
                missing_originals.append((m.member_id, origin_rel))
                continue
            originals_dir = target / "Original Media"
            originals_dir.mkdir(exist_ok=True)
            origin_dst = _dedup_filename(
                originals_dir, Path(origin_rel).name)
            try:
                if _place(origin_src, origin_dst,
                          force_copy=copy_mode):
                    originals_linked += 1
                else:
                    originals_copied += 1
            except OSError as exc:
                log.warning(
                    "cross-event original place failed (%s → %s): %s",
                    origin_src, origin_dst, exc)
                missing_originals.append((m.member_id, origin_rel))

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
        # spec/105 §3 — Original Media/ subdir counts; always present so
        # callers can show them unconditionally.
        "originals_linked": originals_linked,
        "originals_copied": originals_copied,
        "missing_originals": missing_originals,
    }


__all__ = ["export_cross_event_cut", "CrossEventExportError"]
