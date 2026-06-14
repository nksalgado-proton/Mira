"""Cut export — the handoff folder (spec/61 §5.2).

Materializes ``<event_root>/Cuts/<tag>/``: **linked** media (NTFS
hardlink, copy fallback — never byte-duplicates by choice), named with
a sequence prefix so plain filename sort = chronological show order;
separator images rendered into the same sequence; an ``audio/`` subdir
with the playlist (linked, ``01_``-prefixed for play order, covering
the show plus a margin — spec/61 §5.3).

Export is a SNAPSHOT: the Cut stays live; a name collision on disk gets
a ``(2)``-style disambiguator instead of touching the old folder
(renaming a Cut never rewrites history — spec/61 §1.4/§5.2). The
separator images are rendered by an injected writer (they're QImages —
the UI layer owns pixels; this module owns files and order). Stamps
``last_exported_at`` when done.

No Qt imports here (charter invariant 8 posture for the data layer).
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from core import audio_library, cut_budget
from mira.shared.cut_session import SessionFile, files_from_lineage

log = logging.getLogger(__name__)

#: target -> None; renders the day's separator card into ``target``.
SeparatorWriter = Callable[[Path, Optional[int]], None]


@dataclass
class ExportResult:
    folder: Path
    linked: int = 0
    copied: int = 0                      # hardlink fallback copies
    separators: int = 0
    audio_files: int = 0
    audio_short: bool = False            # library couldn't cover the show
    missing: List[str] = field(default_factory=list)


def _fresh_folder(base: Path) -> Path:
    """The snapshot folder: ``Cuts/<tag>/``, or ``<tag> (2)`` etc. when
    a previous export (or a renamed Cut's history) already owns it."""
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


def export_cut(
    gateway,
    cut,
    *,
    event_root: Path,
    separators_on: bool,
    separator_writer: Optional[SeparatorWriter] = None,
    opener_writer: Optional[Callable[[Path], None]] = None,
    audio_root: Optional[str] = None,
    audio_tracks: Optional[Sequence[audio_library.AudioTrack]] = None,
    rng=None,
) -> ExportResult:
    """Materialize one Cut. ``audio_tracks`` overrides the library scan
    (tests + pre-scanned callers); otherwise the cut's music category is
    scanned from ``audio_root`` on demand."""
    event_root = Path(event_root)
    files = files_from_lineage(gateway, gateway.cut_member_files(cut.id))
    dest = _fresh_folder(event_root / "Cuts" / cut.tag)
    dest.mkdir(parents=True, exist_ok=True)
    result = ExportResult(folder=dest)

    seq = 0
    last_day: object = object()
    totals_photos = totals_seps = 0
    totals_video_ms = 0
    if separators_on and opener_writer is not None and files:
        # The opener — the show's title slide, first in sort order.
        seq += 1
        target = dest / f"{seq:03d}_opener.jpg"
        try:
            opener_writer(target)
            result.separators += 1
            totals_seps += 1
        except Exception:  # noqa: BLE001 — a failed card never blocks
            log.exception("opener render failed for %s", target)
            seq -= 1
    for f in files:
        if separators_on and f.day_number != last_day:
            last_day = f.day_number
            if separator_writer is not None:
                seq += 1
                day_tag = "undated" if f.day_number is None else f"day{f.day_number}"
                target = dest / f"{seq:03d}_{day_tag}.jpg"
                try:
                    separator_writer(target, f.day_number)
                    result.separators += 1
                    totals_seps += 1
                except Exception:  # noqa: BLE001 — a failed card never
                    log.exception("separator render failed for %s", target)
                    seq -= 1       # blocks the export; the slot is reused
        src = event_root / f.export_relpath
        if not src.is_file():
            result.missing.append(f.export_relpath)
            continue
        seq += 1
        dst = dest / f"{seq:03d}_{Path(f.export_relpath).name}"
        if _link_or_copy(src, dst):
            result.linked += 1
        else:
            result.copied += 1
        if f.kind == "video":
            totals_video_ms += f.duration_ms
        else:
            totals_photos += 1

    # ── audio (spec/61 §5.3) ─────────────────────────────────────────
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
                target = audio_dir / f"{i:02d}_{t.path.name}"
                try:
                    _link_or_copy(t.path, target)
                    result.audio_files += 1
                except OSError:
                    log.exception("audio link failed for %s", t.path)
            covered = sum(t.duration_seconds for t in playlist)
            result.audio_short = covered < show_s
        else:
            result.audio_short = show_s > 0

    gateway.mark_cut_exported(cut.id)
    log.info(
        "export_cut %s → %s (%d linked, %d copied, %d separators, "
        "%d audio, %d missing)",
        cut.tag, dest, result.linked, result.copied, result.separators,
        result.audio_files, len(result.missing))
    return result
