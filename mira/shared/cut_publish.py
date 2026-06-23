"""Cut publishing — materialise a Cut to the library publish slot
+ manifest (spec/76 §B.3).

Publishing differs from exporting in three ways:

1. **Fixed target.** Export writes to a user-picked folder, with a
   ``(2)``-style disambiguator for re-runs. Publish writes to a
   stable slot under ``<library_publish_root>`` so the TV media
   server reads from a known path. Re-publish **overwrites** the
   previous slot (the slot is the "live" handoff, not a snapshot
   history).

2. **Manifest sidecar.** Each publish drops a ``manifest.json``
   beside the files — an ordered list of frames + audio with
   titles, day separators, and per-photo durations. A media server
   that understands the manifest gets a richer presentation; one
   that doesn't (DLNA / folder slideshow) still plays the files in
   sequence-prefixed order.

3. **Scope-aware layout.** Event-scope Cuts land under
   ``<publish_root>/Events/<event_uuid>/<cut_tag>/``; cross-event
   Cuts under ``<publish_root>/Cross-event/<cut_tag>/``. The split
   keeps the cross-event slot from colliding with an event Cut of
   the same name.

The actual byte movement is delegated to the existing export
pipelines (:mod:`cut_export` for event-scope,
:mod:`cross_event_cut_export` for cross-event); this module is the
publish-flavoured wrapper.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from mira.shared import cross_event_cut_export, cut_export

log = logging.getLogger(__name__)

#: Manifest schema version — bump on any breaking shape change. v1
#: lands the minimum spec/76 §B.3 surface; richer fields (transition
#: hints, PTE music handoff, slideshow themes) belong in v2+ per §C.
MANIFEST_SCHEMA_VERSION = 1

#: Sidecar filename written into every publish folder.
MANIFEST_FILENAME = "manifest.json"

#: Subfolder under the publish root for event-scope Cuts.
EVENT_SCOPE_DIRNAME = "Events"

#: Subfolder under the publish root for cross-event Cuts.
CROSS_EVENT_SCOPE_DIRNAME = "Cross-event"

#: File extensions classified as video — durations come from the file
#: itself, not the cut's photo_s. Anything not in this set is treated
#: as a photo and gets photo_s as its display duration.
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}


class CutPublishError(RuntimeError):
    """Surface to the UI when a publish can't proceed (publish root
    unwritable, source bytes missing, etc.)."""


@dataclass(frozen=True)
class PublishResult:
    """Outcome of a publish call."""
    target: Path                 # the publish folder (overwritten)
    manifest_path: Path
    summary: Dict[str, Any]      # underlying export's summary dict


# --------------------------------------------------------------------------- #
# Publish root resolution
# --------------------------------------------------------------------------- #


def publish_root(library_root_path: Path, override: str = "") -> Path:
    """Resolve where published Cuts land.

    ``override`` is :attr:`Settings.library_publish_root` (the user
    override). Empty falls back to ``<library_root>/Published/`` so
    the publish slot rides with the library when it relocates.
    Charter invariant #2 — no hardcoded path; the root comes from
    settings / paths.
    """
    raw = (override or "").strip()
    if raw:
        return Path(raw)
    return Path(library_root_path) / "Published"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Manifest writing
# --------------------------------------------------------------------------- #


def _classify_frame(filename: str, photo_s: float) -> Dict[str, Any]:
    """Build one ``frames[]`` entry from a published filename.

    The cut_export pipeline names everything ``NNN_<tag>`` — opener,
    day separators, and per-photo files all follow that. Sequence
    comes from ``NNN``; the rest of the name discriminates:

    * ``NNN_opener.<ext>`` → separator (the show opener)
    * ``NNN_dayN.<ext>``   → separator (day N break)
    * ``NNN_undated.<ext>`` → separator (undated bucket)
    * anything else → frame; photo or video by extension

    Videos skip the ``duration_s`` field so a media server uses the
    file's native duration; photos carry the cut's ``photo_s``.
    """
    seq_str, _, rest = filename.partition("_")
    try:
        seq = int(seq_str)
    except ValueError:
        seq = 0
    entry: Dict[str, Any] = {"seq": seq, "file": filename}
    stem = Path(rest).stem if rest else ""
    ext = Path(filename).suffix.lower()
    if stem == "opener":
        entry["kind"] = "separator"
        entry["title"] = "opener"
    elif stem == "undated":
        entry["kind"] = "separator"
        entry["title"] = "Undated"
    elif stem.startswith("day"):
        entry["kind"] = "separator"
        entry["title"] = stem.replace("day", "Day ")
        try:
            entry["day_number"] = int(stem[3:])
        except ValueError:
            pass
    else:
        entry["kind"] = "frame"
        if ext in _VIDEO_EXTS:
            entry["media"] = "video"
        else:
            entry["media"] = "photo"
            entry["duration_s"] = float(photo_s)
    return entry


def _scan_audio(target: Path) -> List[Dict[str, Any]]:
    """List ``audio/*`` files in publish order (sequence-prefixed)."""
    audio_dir = target / "audio"
    if not audio_dir.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(audio_dir.iterdir(), key=lambda q: q.name):
        if not p.is_file():
            continue
        seq_str, _, _ = p.name.partition("_")
        try:
            seq = int(seq_str)
        except ValueError:
            seq = 0
        out.append({
            "seq": seq, "file": f"audio/{p.name}",
        })
    return out


def _scan_frames(target: Path, photo_s: float) -> List[Dict[str, Any]]:
    """Walk ``target`` and produce the manifest ``frames`` list.

    Skips ``audio/`` (its own bucket) and ``manifest.json`` (the
    sidecar itself). Sort by sequence prefix so the manifest is
    show-order regardless of filesystem enumeration."""
    entries: List[Dict[str, Any]] = []
    for p in sorted(target.iterdir(), key=lambda q: q.name):
        if not p.is_file():
            continue
        if p.name == MANIFEST_FILENAME:
            continue
        entries.append(_classify_frame(p.name, photo_s))
    entries.sort(key=lambda e: e.get("seq", 0))
    return entries


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Atomic write-then-rename for the manifest (invariant #6)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    os.replace(str(tmp), str(path))


def _build_manifest(
    *,
    kind: str,
    cut: Any,
    target: Path,
    library_root_path: Path,
    event_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """Produce the manifest dict for the published folder.

    ``kind`` is ``"event_cut"`` or ``"cross_event_cut"`` —
    discriminates the source block. ``event_uuid`` is required for
    event_cut, ignored for cross_event_cut (members carry their own
    event id per row — too varied for the manifest top level).
    """
    photo_s = float(getattr(cut, "photo_s", 6.0) or 6.0)
    manifest: Dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": kind,
        "cut_id": getattr(cut, "id", ""),
        "tag": getattr(cut, "tag", ""),
        "published_at": _now_utc_iso(),
        "source": {
            "event_id": event_uuid,
            "library_root": str(library_root_path),
        },
        "frames": _scan_frames(target, photo_s),
        "audio": _scan_audio(target),
    }
    return manifest


# --------------------------------------------------------------------------- #
# Event-scope publish
# --------------------------------------------------------------------------- #


def _prepare_publish_target(target: Path) -> None:
    """Clear ``target`` and recreate it so the publish slot is a clean
    overwrite. Idempotent — the slot is meant to be the "live"
    handoff, not a snapshot history."""
    if target.exists():
        if not target.is_dir():
            raise CutPublishError(
                f"publish target {target} exists and is not a directory")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def publish_cut(
    gateway,
    cut,
    *,
    event_root: Path,
    event_uuid: str,
    library_root_path: Path,
    settings: Any,
    audio_root: Optional[str] = None,
    audio_tracks: Optional[Sequence] = None,
    separator_writer=None,
    opener_writer=None,
    provenance_resolver=None,
    overlay_renderer=None,
    iptc_writer=None,
) -> PublishResult:
    """Publish an event-scope Cut (spec/76 §B.3).

    Lands under
    ``<publish_root>/Events/<event_uuid>/<cut_tag>/`` with a
    ``manifest.json`` sidecar; the previous slot for the same Cut
    is overwritten. Delegates the byte movement to
    :func:`mira.shared.cut_export.export_cut`.
    """
    override = getattr(settings, "library_publish_root", "") or ""
    root = publish_root(Path(library_root_path), override)
    target = root / EVENT_SCOPE_DIRNAME / event_uuid / cut.tag
    _prepare_publish_target(target)
    result = cut_export.export_cut(
        gateway, cut,
        event_root=Path(event_root),
        target=target,
        audio_root=audio_root,
        audio_tracks=audio_tracks,
        separator_writer=separator_writer,
        opener_writer=opener_writer,
        provenance_resolver=provenance_resolver,
        overlay_renderer=overlay_renderer,
        iptc_writer=iptc_writer,
    )
    manifest = _build_manifest(
        kind="event_cut", cut=cut, target=target,
        library_root_path=library_root_path, event_uuid=event_uuid,
    )
    manifest_path = target / MANIFEST_FILENAME
    _atomic_write_json(manifest_path, manifest)
    summary = {
        "linked": result.linked, "copied": result.copied,
        "burned_in": result.burned_in, "iptc_written": result.iptc_written,
        "separators": result.separators, "audio_files": result.audio_files,
        "audio_short": result.audio_short,
        "missing": list(result.missing),
    }
    log.info(
        "publish_cut %s -> %s (manifest %d frames, %d audio)",
        cut.tag, target, len(manifest["frames"]), len(manifest["audio"]),
    )
    return PublishResult(
        target=target, manifest_path=manifest_path, summary=summary,
    )


# --------------------------------------------------------------------------- #
# Cross-event publish
# --------------------------------------------------------------------------- #


def publish_cross_event_cut(
    gateway,
    cut_id: str,
    *,
    library_root_path: Path,
    settings: Any,
) -> PublishResult:
    """Publish a cross-event Cut (spec/76 §B.3).

    Lands under ``<publish_root>/Cross-event/<cut_tag>/`` with a
    ``manifest.json`` sidecar; the previous slot is overwritten.
    Delegates byte movement to
    :func:`mira.shared.cross_event_cut_export.export_cross_event_cut`.
    """
    lg = gateway.library_gateway()
    cut = lg.cross_event_cut(cut_id)
    if cut is None:
        raise CutPublishError(
            f"cross-event Cut {cut_id} is no longer in the library")
    override = getattr(settings, "library_publish_root", "") or ""
    root = publish_root(Path(library_root_path), override)
    target = root / CROSS_EVENT_SCOPE_DIRNAME / cut.tag
    _prepare_publish_target(target)
    # spec/112 — pass the audio library root so the published
    # cross-event Cut carries the same ``audio/`` playlist a per-event
    # publish does.
    audio_root = getattr(settings, "audio_library_path", "") or ""
    summary = cross_event_cut_export.export_cross_event_cut(
        gateway, "", cut_id, target=target,
        audio_root=audio_root or None,
    )
    manifest = _build_manifest(
        kind="cross_event_cut", cut=cut, target=target,
        library_root_path=library_root_path, event_uuid=None,
    )
    manifest_path = target / MANIFEST_FILENAME
    _atomic_write_json(manifest_path, manifest)
    log.info(
        "publish_cross_event_cut %s -> %s (manifest %d frames)",
        cut.tag, target, len(manifest["frames"]),
    )
    return PublishResult(
        target=target, manifest_path=manifest_path, summary=summary,
    )


__all__ = [
    "CROSS_EVENT_SCOPE_DIRNAME",
    "CutPublishError",
    "EVENT_SCOPE_DIRNAME",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "PublishResult",
    "publish_cross_event_cut",
    "publish_cut",
    "publish_root",
]
