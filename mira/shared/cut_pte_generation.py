"""Standalone PTE generation against an already-exported Cut folder
(spec/149 §2.A).

The export pipelines (per-event :mod:`mira.ui.pages.share_cuts_page` +
cross-event :mod:`mira.ui.pages.library_page`) write ``slideshow.pte``
as a side effect of materialising a Cut. Spec/149 detaches generation
from materialisation so the user can:

* fix a broken ``.pte`` (renamed folder → stale absolute paths) without
  a full re-export;
* recover from ``use_pte=False`` at the time of the original export;
* recover from a deleted ``.pte``;
* auto-generate on Open-in-PTE when none is present.

This module is the data-layer seam both surfaces share. It scans the
folder's files into a member list (mirroring the export-time walk),
builds the audio list from ``audio/``, and writes the project via
:func:`mira.shared.pte_project.generate_into_folder`. Returns the
written path (or ``None`` when the folder has no media members).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


_PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png")
_VIDEO_SUFFIXES = (".mp4",)


def _probe_audio_ms(path: Path) -> int:
    """Best-effort audio duration probe in milliseconds. Uses
    :mod:`mutagen` when available; returns 0 on any failure (PTE
    silently coasts on a 0 here — the export-time helper has the same
    fallback)."""
    try:
        import mutagen
        audio = mutagen.File(path)
        if audio is None or audio.info is None:
            return 0
        return int(round(audio.info.length * 1000))
    except Exception:                                              # noqa: BLE001
        return 0


def _probe_video_ms(path: Path) -> int:
    """Best-effort video duration probe via :func:`core.video_extract.probe_video`
    (ffmpeg parse). Returns 0 on any failure (the PTE generator floors
    zero-duration clips to a safe minimum, so a missing probe just yields
    a short stand-in clip rather than crashing the export-folder fix-up
    flow)."""
    try:
        from core.video_extract import probe_video
        info = probe_video(path)
        return int(info.duration_ms or 0)
    except Exception:                                              # noqa: BLE001
        return 0


def generate_pte_for_folder(
    folder: Path,
    *,
    aspect: str = "16:9",
    photo_seconds: float = 6.0,
    overlay_mode: str = "embedded",
    stem: str = "slideshow",
    library_root: Optional[Path] = None,
    bundled_fallback: Optional[Path] = None,
) -> Optional[Path]:
    """spec/149 §2.A — write ``<stem>.pte`` into ``folder`` based on
    the files already there. Media is NOT re-materialised: this only
    reads the existing photos / videos and replaces (or creates) the
    project file. ``overwrite=True`` is forced so the project lands
    at ``<stem>.pte`` directly (no ``(2)`` disambiguation; the typical
    standalone caller wants the canonical filename).

    Returns the path written, or ``None`` when ``folder`` has no media
    members worth wrapping in a project.

    Photo overlay text is **not** populated here (the standalone call
    doesn't have the gateway's frame_provenance join). The export-time
    helper still drives populated overlays; standalone falls back to
    a clean slide. The ``.pte`` is structurally the same; only the
    Text= line is empty/stripped per the skeleton."""
    from mira.shared.pte_project import (
        PteAudioTrack, PteMember,
        generate_into_folder,
    )
    folder = Path(folder)
    if not folder.is_dir():
        log.warning("generate_pte_for_folder: folder missing: %s", folder)
        return None

    members: list = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file():
            continue
        suffix = entry.suffix.lower()
        if suffix in _PHOTO_SUFFIXES:
            members.append(PteMember(kind="photo", path=entry))
        elif suffix in _VIDEO_SUFFIXES:
            members.append(PteMember(
                kind="video", path=entry,
                duration_ms=_probe_video_ms(entry)))
    if not members:
        log.info(
            "generate_pte_for_folder: no media in %s, nothing to write",
            folder)
        return None

    tracks: list = []
    audio_dir = folder / "audio"
    if audio_dir.is_dir():
        for track in sorted(audio_dir.iterdir(),
                            key=lambda p: p.name.lower()):
            if not track.is_file():
                continue
            tracks.append(PteAudioTrack(
                path=track, duration_ms=_probe_audio_ms(track)))

    safe_stem = (stem or "").strip() or "slideshow"
    return generate_into_folder(
        folder, members, tracks,
        aspect=aspect or "16:9",
        photo_seconds=float(photo_seconds or 6.0),
        library_root=library_root,
        bundled_fallback=bundled_fallback,
        overlay_mode=overlay_mode or "embedded",
        stem=safe_stem,
        overwrite=True,
    )


__all__ = ["generate_pte_for_folder"]
