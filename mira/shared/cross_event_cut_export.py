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

from core import audio_library
from mira.shared.cut_export import (
    _clear_folder_contents, _dedup_filename, _fresh_folder, _place,
    write_audio_playlist,
)

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


def _kind_index_for_source_event(
    gateway, source_event_id: str,
) -> dict:
    """spec/112 — build a ``{relpath -> (kind, duration_ms)}`` map for
    one source event so the cross-event audio block can compute the
    show's projected duration without re-opening the event per member.

    Resolves each member's relpath (an ``export_relpath`` for
    ``kind='export'`` or an ``origin_relpath`` for ``kind='grab'``) to
    the source item's ``kind`` + ``duration_ms``. Returns ``{}`` when
    the source event can't be opened (relocated, deleted) — those
    members default to photo / 0 ms downstream, identical to how
    ``SessionFile`` degrades on a missing lineage join (mira.shared.
    cut_session.files_from_lineage)."""
    try:
        eg = gateway.open_event(source_event_id)
    except Exception:                                              # noqa: BLE001
        log.exception(
            "cross-event kind index: cannot open source event %s",
            source_event_id)
        return {}
    try:
        try:
            by_item = {it.id: it for it in eg.items()}
            origin_index = {
                (it.origin_relpath or ""): it
                for it in by_item.values() if it.origin_relpath
            }
            index: dict = {}
            for ln in eg.exported_files():
                src = by_item.get(getattr(ln, "source_item_id", None))
                if src is None:
                    continue
                key = str(ln.export_relpath).replace("\\", "/")
                index[key] = (src.kind, int(src.duration_ms or 0))
            # Grab-kind members land on the source item's
            # ``origin_relpath`` — register those directly so a Cut
            # that mixes export and grab members from the same event
            # resolves both in one pass.
            for relpath, it in origin_index.items():
                key = relpath.replace("\\", "/")
                index.setdefault(key, (it.kind, int(it.duration_ms or 0)))
            return index
        except Exception:                                          # noqa: BLE001
            log.exception(
                "cross-event kind index build failed for %s",
                source_event_id)
            return {}
    finally:
        try:
            eg.close()
        except Exception:                                          # noqa: BLE001
            pass


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
    overwrite_existing: bool = True,
    audio_root: Optional[str] = None,
    audio_tracks: Optional[list] = None,
    rng=None,
    opener_writer: Optional[callable] = None,
    separator_writer: Optional[callable] = None,
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

    spec/112 — ``audio_root`` (or pre-scanned ``audio_tracks``) drives
    the soundtrack playlist, written into ``<target>/audio/`` via the
    same :func:`mira.shared.cut_export.write_audio_playlist` helper the
    per-event exporter uses, so per-event and cross-event Cuts emit
    identical soundtrack layouts. The Cut's ``music_category`` gates
    the block; ``None`` leaves ``audio/`` absent (parity with the
    per-event behaviour for category-less Cuts).

    spec/148 — ``overwrite_existing`` mirrors the per-event flag:
    ``True`` (the default — preserves today's per-file-overwrite
    semantics so callers and tests that re-export to the same target
    keep working) materialises into ``target`` directly, clearing any
    prior bundle first so stale members can't linger; ``False`` runs
    ``target`` through :func:`mira.shared.cut_export._fresh_folder`
    so a re-export lands at ``<tag> (2)/`` and the old folder stays
    untouched.

    spec/111 — ``opener_writer`` / ``separator_writer`` are the same
    injected card renderers the per-event exporter accepts. The host
    builds them at the Cut's aspect (``cut.aspect`` → canvas WxH via
    :func:`core.cut_aspect.aspect_dimensions`). ``opener_writer`` (when
    provided) runs once as the show's first slide; ``separator_writer``
    runs per ``(event_uuid, day)`` boundary in chronological order so
    every per-event day grouping earns its own card. Either left
    ``None`` skips that slide tier — the cross-event surface defaults
    separators OFF (spec/81 §3.1), so neither writer is required.

    Raises :class:`CrossEventExportError` only when the export can't
    run at all (cut gone from mira.db, target unwritable). Per-member
    errors fall under ``missing``."""
    target = Path(target)
    if overwrite_existing:
        # spec/148 Overwrite — write into target; clear any prior
        # bundle so a smaller re-export can't leave orphan members
        # behind. The per-file unlink/relink loop below still handles
        # in-flight collisions inside this run.
        if target.exists():
            _clear_folder_contents(target)
    else:
        # spec/148 Keep both — never touch an existing bundle; the
        # re-export lands at ``<tag> (2)/`` so the user's prior PTE
        # project stays addressable by its current absolute paths.
        if target.exists():
            target = _fresh_folder(target)
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
    # spec/112 — running totals for the soundtrack duration math.
    # ``video_ms_total`` carries true clip lengths so a Cut packed with
    # videos gets a longer playlist than a same-count photo Cut.
    audio_photo_count = 0
    audio_video_ms = 0
    # spec/111 — opener + separator counters (mirror the per-event
    # ExportResult.separators field so the UI summary path is uniform).
    separators = 0

    # spec/111 — opener slide. Fires once at the show's head when the
    # caller wired a writer. The opener filename is sequence-prefixed
    # ``000_opener.jpg`` so plain filename sort keeps it first in the
    # output folder.
    if opener_writer is not None and resolved:
        opener_path = target / "000_opener.jpg"
        try:
            opener_writer(opener_path)
            separators += 1
        except Exception:                                          # noqa: BLE001
            log.exception("cross-event opener render failed for %s",
                          opener_path)
    # spec/105 §3 — build a per-source-event lineage→origin index ONCE
    # so the per-member loop is O(1). Done only when originals are
    # requested and there's at least one export-kind member from each
    # event; the dict for an unopenable source event is empty.
    origin_index: dict = {}
    if include_originals:
        for eid in {m.event_id for m in resolved if m.kind == "export"}:
            origin_index[eid] = _origin_index_for_source_event(
                gateway, eid)
    # spec/112 — per-source-event kind/duration index for the soundtrack
    # duration math. Only built when the Cut has a music_category (no
    # category → no audio block → no need to open source events for
    # kind/duration). The dict for an unopenable source event is empty;
    # those members count as photos (the SessionFile degradation rule).
    kind_index: dict = {}
    if getattr(cut, "music_category", None):
        for eid in {m.event_id for m in resolved}:
            kind_index[eid] = _kind_index_for_source_event(gateway, eid)
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

        # spec/112 — accumulate kind/duration for the playlist length.
        # Members whose source event is missing default to photo / 0 ms
        # (the same degradation rule as the per-event SessionFile
        # build). The lookup uses the same relpath the member was
        # resolved against.
        if kind_index:
            member_key = str(m.source_relpath).replace("\\", "/")
            kind_kind, duration_ms = kind_index.get(
                m.event_id, {}).get(member_key, ("photo", 0))
            if kind_kind == "video":
                audio_video_ms += duration_ms
            else:
                audio_photo_count += 1

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

    # spec/112 — soundtrack via the shared helper so per-event and
    # cross-event Cuts emit identical audio/ playlists. Cross-event
    # Cuts have no separators today (the cross-event flow doesn't
    # render them), so ``separator_count=0``.
    audio_files, audio_short = write_audio_playlist(
        dest=target,
        music_category=getattr(cut, "music_category", None),
        photo_count=audio_photo_count,
        separator_count=0,
        video_ms_total=audio_video_ms,
        photo_s=getattr(cut, "photo_s", 6.0),
        audio_root=audio_root,
        audio_tracks=audio_tracks,
        copy_mode=copy_mode,
        rng=rng,
    )

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
        # spec/112 — audio playlist counters, matching the per-event
        # ExportResult.audio_files / audio_short fields so the UI
        # summary path is uniform.
        "audio_files": audio_files,
        "audio_short": audio_short,
        # spec/111 — separator + opener slides rendered. 0 when no
        # writer was wired; non-zero when the host opted in.
        "separators": separators,
        # spec/148 — the actual folder written to. Identical to the
        # caller's ``target`` under Overwrite; ``<tag> (2)/`` etc. when
        # Keep-both disambiguated. UI reads this back for the
        # export-complete summary + Open-folder action.
        "folder": target,
    }


__all__ = ["export_cross_event_cut", "CrossEventExportError"]
