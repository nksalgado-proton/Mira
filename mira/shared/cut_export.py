"""Cut export — the handoff folder (spec/81 §4-§5, spec/61 §5.2).

Materializes a Cut to a target directory: **linked** media (NTFS hardlink,
copy fallback — never byte-duplicates by choice), named with a sequence prefix
so plain filename sort = chronological show order; **separator** images
rendered into the same sequence (when the Cut's ``separators`` flag is on);
an **``audio/``** subdir with the playlist; and **overlay** handling per the
Cut's ``overlay_mode`` (spec/81 §3.1).

**The target is a PARAMETER (spec/81 §5).** Composition travels with the Cut;
the destination does NOT. ``export_cut`` takes ``target`` defaulting to
``<event_root>/Cuts/<tag>/`` (no absolute path is ever stored on the Cut —
charter invariant #2). Re-export reproduces identical bundle *content*; where
it lands is a re-confirmed default.

**Overlays cost no budget** (spec/81 §3.1) and change no membership:
  * ``embedded`` (default, link-pure) — the technical fields already live in
    the JPEG's EXIF; only *where* needs writing → IPTC City/Sub-location/
    Country via the bundled ExifTool, written into the linked file in place
    (members stay hardlinks). A frame with no *where* data stays a pure link.
  * ``burn_in`` (opt-in) — member *copies* with the chosen fields drawn into
    the pixels by an injected renderer (the UI layer owns pixels). These
    members are copies, not links.
In-app Play draws overlays live (the play path, not here).

Export is a SNAPSHOT: the Cut stays live; a name collision on disk gets a
``(2)``-style disambiguator instead of touching the old folder. Stamps
``last_exported_at`` when done.

No Qt imports here (charter invariant 8 posture for the data layer).
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from core import audio_library, cut_budget, cut_overlay
from mira.shared.cut_session import SessionFile, files_from_lineage

log = logging.getLogger(__name__)

#: target -> None; renders the day's separator card into ``target``.
SeparatorWriter = Callable[[Path, Optional[int]], None]

#: (src, dst, fields, provenance) -> None; renders a member COPY with the
#: chosen overlay fields drawn into the pixels (burn-in mode). Injected by the
#: caller — the UI layer owns pixels.
OverlayRenderer = Callable[[Path, Path, Sequence[str], "cut_overlay.FrameProvenance"], None]

#: relpath -> FrameProvenance; resolves a member's provenance facts for
#: overlays (injected by the gateway/caller — pure data, no Qt).
ProvenanceResolver = Callable[[str], "cut_overlay.FrameProvenance"]

#: (path, iptc_tags) -> bool; embeds the *where* IPTC tags into ``path`` in
#: place (True on success). Injected so this module stays Qt-free AND
#: subprocess-free in tests. The default writer uses the bundled ExifTool.
IptcWriter = Callable[[Path, Dict[str, str]], bool]


@dataclass
class ExportResult:
    folder: Path
    linked: int = 0
    copied: int = 0                      # hardlink fallback copies
    burned_in: int = 0                   # overlay burn-in copies
    iptc_written: int = 0                # embedded-mode where-IPTC writes
    separators: int = 0
    audio_files: int = 0
    audio_short: bool = False            # library couldn't cover the show
    missing: List[str] = field(default_factory=list)


def _fresh_folder(base: Path) -> Path:
    """The snapshot folder: ``<target>``, or ``<target> (2)`` etc. when a
    previous export (or a renamed Cut's history) already owns it."""
    if not base.exists():
        return base
    n = 2
    while True:
        candidate = base.with_name(f"{base.name} ({n})")
        if not candidate.exists():
            return candidate
        n += 1


def _link_or_copy(src: Path, dst: Path) -> bool:
    """Hardlink; copy when the volume/file refuses. True = linked."""
    try:
        os.link(src, dst)
        return True
    except OSError:
        shutil.copy2(src, dst)
        return False


def default_target(event_root: Path, tag: str) -> Path:
    """The defaulted (not frozen) target for an event Cut: ``<event_root>/
    Cuts/<tag>/`` (spec/81 §5). The Cut never stores this — it is recomputed
    each export and offered as the one-keystroke default."""
    return Path(event_root) / "Cuts" / tag


def _exiftool_iptc_writer(path: Path, tags: Dict[str, str]) -> bool:
    """The default embedded-mode IPTC writer: stamp the *where* tags into a
    file IN PLACE via the bundled ExifTool (``-overwrite_original``, atomic).
    Returns True on success. Lazy-imports the tool seam so tests inject their
    own writer and this module stays import-light + Qt-free."""
    if not tags:
        return True
    try:
        import subprocess
        from core.exif_reader import _get_exiftool_path
        from core.proc import run as _run_hidden
        args = [str(_get_exiftool_path()), "-overwrite_original",
                "-charset", "filename=UTF8"]
        for tag, value in tags.items():
            args.append(f"-{tag}={value}")
        args.append(str(path))
        cp = _run_hidden(args, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60)
        if cp.returncode != 0:
            log.warning("IPTC write failed for %s: %s", path, cp.stderr.strip())
            return False
        return True
    except Exception:  # noqa: BLE001 — a failed overlay write never blocks export
        log.exception("IPTC write raised for %s", path)
        return False


def export_cut(
    gateway,
    cut,
    *,
    event_root: Path,
    target: Optional[Path] = None,
    separators_on: Optional[bool] = None,
    separator_writer: Optional[SeparatorWriter] = None,
    opener_writer: Optional[Callable[[Path], None]] = None,
    audio_root: Optional[str] = None,
    audio_tracks: Optional[Sequence[audio_library.AudioTrack]] = None,
    provenance_resolver: Optional[ProvenanceResolver] = None,
    overlay_renderer: Optional[OverlayRenderer] = None,
    iptc_writer: Optional[IptcWriter] = None,
    rng=None,
) -> ExportResult:
    """Materialize one Cut (spec/81 §4-§5).

    ``target`` defaults to ``<event_root>/Cuts/<tag>/`` (the Cut stores no
    path). ``separators_on`` defaults to the Cut's own ``separators`` flag.
    Overlays follow the Cut's ``overlay_mode`` + ``overlay_fields``:
    ``embedded`` writes *where* IPTC into the linked file (members stay links);
    ``burn_in`` emits rendered copies via ``overlay_renderer``. Overlays cost
    no budget. ``audio_tracks`` overrides the library scan (tests + pre-scanned
    callers)."""
    event_root = Path(event_root)
    files = files_from_lineage(gateway, gateway.cut_member_files(cut.id))
    if separators_on is None:
        separators_on = bool(getattr(cut, "separators", True))
    base = Path(target) if target is not None else default_target(event_root, cut.tag)
    dest = _fresh_folder(base)
    dest.mkdir(parents=True, exist_ok=True)
    result = ExportResult(folder=dest)

    overlay_fields = list(gateway.cut_overlay_fields(cut))
    overlay_mode = cut.overlay_mode or "embedded"
    iptc_writer = iptc_writer or _exiftool_iptc_writer

    def _provenance(relpath: str) -> cut_overlay.FrameProvenance:
        if provenance_resolver is not None:
            return provenance_resolver(relpath)
        return cut_overlay.FrameProvenance()

    seq = 0
    last_day: object = object()
    totals_photos = totals_seps = 0
    totals_video_ms = 0
    if separators_on and opener_writer is not None and files:
        seq += 1
        target_path = dest / f"{seq:03d}_opener.jpg"
        try:
            opener_writer(target_path)
            result.separators += 1
            totals_seps += 1
        except Exception:  # noqa: BLE001 — a failed card never blocks
            log.exception("opener render failed for %s", target_path)
            seq -= 1
    for f in files:
        if separators_on and f.day_number != last_day:
            last_day = f.day_number
            if separator_writer is not None:
                seq += 1
                day_tag = "undated" if f.day_number is None else f"day{f.day_number}"
                target_path = dest / f"{seq:03d}_{day_tag}.jpg"
                try:
                    separator_writer(target_path, f.day_number)
                    result.separators += 1
                    totals_seps += 1
                except Exception:  # noqa: BLE001 — a failed card never
                    log.exception("separator render failed for %s", target_path)
                    seq -= 1
        src = event_root / f.export_relpath
        if not src.is_file():
            result.missing.append(f.export_relpath)
            continue
        seq += 1
        dst = dest / f"{seq:03d}_{Path(f.export_relpath).name}"
        if overlay_fields and overlay_mode == "burn_in" and overlay_renderer is not None:
            try:
                overlay_renderer(src, dst, overlay_fields, _provenance(f.export_relpath))
                result.burned_in += 1
                result.copied += 1
            except Exception:  # noqa: BLE001 — a failed burn-in falls back to a plain link
                log.exception("overlay burn-in failed for %s", f.export_relpath)
                if _link_or_copy(src, dst):
                    result.linked += 1
                else:
                    result.copied += 1
        else:
            if _link_or_copy(src, dst):
                result.linked += 1
            else:
                result.copied += 1
            if overlay_fields and overlay_mode == "embedded":
                prov = _provenance(f.export_relpath)
                if cut_overlay.needs_embedded_write(overlay_fields, prov):
                    if iptc_writer(dst, cut_overlay.where_iptc_tags(prov)):
                        result.iptc_written += 1
        if f.kind == "video":
            totals_video_ms += f.duration_ms
        else:
            totals_photos += 1

    if cut.music_category:
        show_s = cut_budget.ShowTotals(
            photo_count=totals_photos,
            separator_count=totals_seps,
            video_ms_total=totals_video_ms,
        ).seconds(cut.photo_s)
        tracks = list(audio_tracks) if audio_tracks is not None else [
            t for t in audio_library.scan_library(Path(audio_root))
            if t.kind is audio_library.AudioKind.MUSIC
            and t.mood == cut.music_category
        ] if audio_root else []
        playlist = audio_library.build_playlist(tracks, show_s, rng=rng)
        if playlist:
            audio_dir = dest / "audio"
            audio_dir.mkdir(exist_ok=True)
            for i, t in enumerate(playlist, start=1):
                target_path = audio_dir / f"{i:02d}_{t.path.name}"
                try:
                    _link_or_copy(t.path, target_path)
                    result.audio_files += 1
                except OSError:
                    log.exception("audio link failed for %s", t.path)
            covered = sum(t.duration_seconds for t in playlist)
            result.audio_short = covered < show_s
        else:
            result.audio_short = show_s > 0

    gateway.mark_cut_exported(cut.id)
    log.info(
        "export_cut %s -> %s (%d linked, %d copied, %d burned-in, %d iptc, "
        "%d separators, %d audio, %d missing)",
        cut.tag, dest, result.linked, result.copied, result.burned_in,
        result.iptc_written, result.separators, result.audio_files,
        len(result.missing))
    return result
