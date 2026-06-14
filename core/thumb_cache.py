"""Per-event thumbnail cache for video derivatives + grid posters.

docs/24 Step 1 + spec/59 (the black-frame guarantee, 2026-06-11). At
clip/still create time — and lazily for Day-Grid video cells — a JPEG
thumbnail lands under ``<event_root>/.cache/thumbs/`` so later phases
can render heterogeneous grids without re-decoding the source video
on every paint.

The cache directory mirrors the source video's path relative to the
event root so two cameras producing the same filename in the same
event (``IMG_0001.MP4`` from a G9 AND a GoPro) can't collide — the
bucket-relative source path is the disambiguator.

The thumbnail layout::

    <event_root>/.cache/thumbs/
        00 - Captured/Day 1/G9/DSC_0042.MP4/c1.jpg
        00 - Captured/Day 1/G9/DSC_0042.MP4/s1.jpg
        00 - Captured/Day 1/GoPro/IMG_0001.MP4/c1.jpg   ← no collision

**The black-frame ladder (spec/59, supersedes the single 2026-05-28
fallback).** A thumb extracts at the caller's ``position_ms`` first;
if the result's mean luma is below :data:`_BLACK_LUMA_THRESHOLD` the
module walks a FORWARD ladder — the caller's fallback, then 3 s, then
10 % and 25 % of the probed duration — keeping the first frame that
clears the threshold, or the brightest candidate when nothing does
(a genuinely dark video stays dark — honest, never worse). The old
single fallback walked *backwards* to 0 ms, which on a fade-in opener
is even blacker — the very case it existed for.

**Cache self-heal.** A cached thumb that still reads black gets ONE
ladder re-run (legacy thumbs predate the ladder); a ``.vetted``
sidecar marks the result so genuinely dark videos aren't re-extracted
on every paint. Same drop-and-redo posture as the Edit frame-cache
heal.

Qt-free; pure I/O. Lives in ``core/`` because Select / Process
consumers are themselves Qt-free for the most part (the rendering
layer wraps the path in a ``QPixmap`` itself).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PIL import Image

from core.video_extract import extract_frame

log = logging.getLogger(__name__)


# Cache sub-tree under the event root. ``.cache`` is a derived-data
# convention (gitignored, safe to delete to regenerate). ``thumbs``
# scopes this module's outputs apart from any future cache kinds.
CACHE_SUBDIR = Path(".cache") / "thumbs"

# Mean-luma threshold below which a thumbnail is treated as "too
# dark to be useful" and the ladder keeps walking. 0-255 scale.
# 5 covers true black + heavy fade-ins without false-positive-ing
# legitimately dark shots (night photography, silhouettes — those
# typically read 15-30 on the same scale).
_BLACK_LUMA_THRESHOLD = 5.0

# The forward rungs after the caller's own positions: a fixed 3 s
# (clears typical fade-in openers without needing a probe), then two
# duration fractions for videos whose whole opening act is dark.
_LADDER_FIXED_MS = (3000,)
_LADDER_DURATION_FRACTIONS = (0.10, 0.25)

# The Day-Grid poster's cache key — shared by the Pick and Edit grids
# AND the player surfaces' load poster (spec/59 black-frame guarantee:
# the player shows this JPEG until the decoder's first real frame).
DAYGRID_ITEM_ID = "daygrid"


def thumb_path(
    event_root: Path,
    source_rel_path: Path,
    item_id: str,
) -> Path:
    """Build the cache path for one derivative without touching disk.

    ``source_rel_path`` is the source video's path **relative to**
    ``event_root`` — e.g. ``Path("00 - Captured/Day 1/G9/DSC_0042.MP4")``.
    The video's filename becomes a directory under the cache; the
    item id (``c1`` / ``s1``) is the leaf JPEG basename.

    Pure path math — no ``mkdir`` here so the function stays a cheap
    lookup. :func:`ensure_thumb` creates parents when it writes.
    """
    return Path(event_root) / CACHE_SUBDIR / source_rel_path / f"{item_id}.jpg"


def poster_path_if_cached(
    event_root: Path,
    source_rel_path: Path,
) -> Optional[Path]:
    """The Day-Grid poster JPEG for a video, **cache-only** — never
    extracts. Player surfaces call this at load time: the grid almost
    always populated it already; when it hasn't, the player honestly
    falls back to its no-poster behaviour rather than block on ffmpeg.
    """
    p = thumb_path(event_root, source_rel_path, DAYGRID_ITEM_ID)
    return p if p.exists() else None


def ensure_thumb(
    event_root: Path,
    source_video: Path,
    source_rel_path: Path,
    item_id: str,
    position_ms: int,
    *,
    fallback_position_ms: Optional[int] = None,
) -> Path:
    """Return the cache path for ``item_id``, extracting on miss.

    Extraction walks the black-frame ladder (module docstring): the
    caller's ``position_ms``, then ``fallback_position_ms`` (if any),
    then the forward rungs — stopping at the first frame whose mean
    luma clears :data:`_BLACK_LUMA_THRESHOLD`, else keeping the
    brightest candidate seen.

    Idempotent on a healthy hit. A cached thumb that reads black and
    was never ladder-vetted gets one re-run (self-heal for legacy
    caches); the ``.vetted`` sidecar prevents eternal re-extraction
    of genuinely dark videos.

    Raises:
        FileNotFoundError: ``source_video`` doesn't exist.
        RuntimeError: ffmpeg refused the source at every rung.
    """
    dest = thumb_path(event_root, source_rel_path, item_id)
    vetted = dest.with_suffix(".vetted")
    if dest.exists():
        # Self-heal a corrupt cached JPEG (Bug 3, 2026-06-13). A previous
        # extraction can leave a partial / unreadable file on disk —
        # interrupted process, full disk, antivirus quarantine, etc. —
        # and the Day Grid then renders forever-blank cells because PIL
        # raises here and the caller catches + returns None. Detect the
        # broken file, drop it (and its sibling .vetted), fall through
        # to the ladder. The luma-vetted fast path stays for healthy
        # thumbs; corrupt + vetted together still re-extract because
        # the read fails before the luma check.
        try:
            if vetted.exists():
                # Confirm the cached JPEG is actually readable; .vetted
                # only records "ladder ran", not "file is intact".
                _mean_luma(dest)
                return dest
            if _mean_luma(dest) >= _BLACK_LUMA_THRESHOLD:
                return dest
            log.info("cached thumb for %s reads black — ladder re-run",
                     source_rel_path)
        except Exception as exc:  # noqa: BLE001 — corrupt JPEG / PIL raise
            log.warning(
                "cached thumb for %s is unreadable (%s) — dropping + "
                "re-running the ladder", source_rel_path, exc)
            try:
                dest.unlink()
            except OSError:
                pass
            try:
                if vetted.exists():
                    vetted.unlink()
            except OSError:
                pass
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Stage 1: the cheap rungs — no probe, no extra subprocess for the
    # common case (a bright frame at the caller's position returns
    # after ONE ffmpeg call, exactly like the pre-ladder code).
    cheap: list[int] = [int(position_ms)]
    if fallback_position_ms is not None:
        cheap.append(int(fallback_position_ms))
    cheap.extend(_LADDER_FIXED_MS)

    state = _LadderState()
    if _walk_rungs(source_video, dest, cheap, state):
        _mark_vetted(vetted)
        return dest

    # Stage 2: the whole opening act is dark (or short clips where the
    # fixed rungs fell past EOF) — probe the duration ONCE and walk the
    # fraction rungs.
    duration_ms = _probed_duration_ms(source_video)
    if duration_ms > 0:
        fractions = [
            int(duration_ms * f) for f in _LADDER_DURATION_FRACTIONS]
        if _walk_rungs(source_video, dest,
                       [r for r in fractions if r < duration_ms],
                       state):
            _mark_vetted(vetted)
            return dest

    if state.best_pos is None:
        # Every rung failed — surface the last ffmpeg error.
        raise (state.last_error if state.last_error is not None
               else RuntimeError(
                   f"No frame could be extracted from {source_video.name}"))
    # Nothing cleared the threshold: keep the brightest candidate
    # (the genuinely-dark-video case — honest, never worse than any
    # single position). dest currently holds the LAST rung; re-extract
    # the best one only when they differ.
    log.info("no ladder rung cleared luma %.1f for %s — keeping the "
             "brightest (%.1f @ %dms)", _BLACK_LUMA_THRESHOLD,
             source_rel_path, state.best_luma, state.best_pos)
    if state.last_pos != state.best_pos:
        extract_frame(source_video, state.best_pos, dest)
    _mark_vetted(vetted)
    return dest


class _LadderState:
    """Brightest-so-far tracking shared across the two ladder stages."""

    def __init__(self) -> None:
        self.best_luma = -1.0
        self.best_pos: Optional[int] = None
        self.last_pos: Optional[int] = None
        self.last_error: Optional[Exception] = None
        self.tried: set = set()


def _walk_rungs(
    source_video: Path, dest: Path, rungs: list, state: _LadderState,
) -> bool:
    """Extract each rung into ``dest`` until one clears the luma
    threshold (→ True). Failed rungs (past-EOF etc.) are skipped;
    a missing source propagates immediately."""
    for pos in rungs:
        pos = max(0, int(pos))
        if pos in state.tried:
            continue
        state.tried.add(pos)
        try:
            extract_frame(source_video, pos, dest)
        except FileNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001 — a rung past EOF etc.
            state.last_error = exc
            continue
        state.last_pos = pos
        luma = _mean_luma(dest)
        if luma >= _BLACK_LUMA_THRESHOLD:
            return True
        if luma > state.best_luma:
            state.best_luma, state.best_pos = luma, pos
    return False


def _probed_duration_ms(source_video: Path) -> int:
    """Best-effort duration probe for the fraction rungs. 0 = unknown."""
    try:
        from core.video_extract import probe_video
        return int(probe_video(source_video).duration_ms or 0)
    except Exception:  # noqa: BLE001 — the ladder degrades to fixed rungs
        return 0


def _mark_vetted(vetted: Path) -> None:
    """Drop the ladder-ran sidecar (best-effort — a failed write only
    costs a future re-check, never the thumb)."""
    try:
        vetted.write_bytes(b"")
    except OSError:
        log.debug("could not write vetted marker %s", vetted)


def _mean_luma(jpeg_path: Path) -> float:
    """Mean grayscale value (0-255) of ``jpeg_path``.

    PIL's ``"L"`` mode applies Rec. 601 luma weights when downconverting
    from RGB, which is what we want for a black-detection check —
    cheap, well-defined, no numpy dependency. Reading a typical
    thumbnail JPEG is <5ms; small enough to run synchronously inside
    the same wait cursor that already wraps the create flow.
    """
    with Image.open(jpeg_path) as im:
        gray = im.convert("L")
        hist = gray.histogram()
    total = sum(hist)
    if total == 0:
        return 0.0
    return sum(i * c for i, c in enumerate(hist)) / total
