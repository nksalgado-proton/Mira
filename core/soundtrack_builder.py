"""Soundtrack builder — pick a list of tracks that fits a chapter.

Drives the "Build Soundtrack" workflow. Inputs are a filtered set
of library tracks, a target duration, and an event-scoped audio
folder where prior soundtracks live (so we can de-prioritize
already-used tracks). Output is a list of tracks whose total
duration lands inside the user's tolerance window, with the
fewest possible repeats from earlier soundtracks for the same
event.

Algorithm:

1. Score each candidate track by ``usage_count`` — how many times
   it appears across the event's existing ``Audio/<name>/``
   subfolders. Tracks never used score 0; once-used score 1; etc.
2. Sort by ``(usage_count, random)`` — least used first, random
   tiebreak so two consecutive runs don't produce the same list
   when the user wants variety.
3. Greedy pick: walk the sorted list adding tracks until the
   running total enters ``[target - tolerance, target + tolerance]``.
   If the next track would overshoot beyond tolerance, skip it
   and keep walking. If we reach the end of the list with the
   total still under target - tolerance, return what we have
   (the library may not have enough material for the request).

The greedy choice is deliberately simple — perfect-fit subset sum
over 200 tracks is overkill for a slideshow soundtrack. The user
can always re-roll by clicking Suggest again or change the
tolerance.

Qt-free and synchronous.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from core.audio_library import AudioTrack

log = logging.getLogger(__name__)


# Default tolerance window in seconds. The picker is allowed to
# land within ±this many seconds of the target. 30s is generous
# enough to find a fit for most mood pools but tight enough that
# the resulting soundtrack feels deliberate.
DEFAULT_TOLERANCE_SECONDS = 30.0

# Top-level Audio folder under <event_root>/04 Curated/. Existing
# subfolders here are scanned for usage counts.
AUDIO_DIR_NAME = "Audio"


@dataclass(frozen=True)
class SoundtrackPlan:
    """A proposed soundtrack: which tracks, total duration, and
    how it relates to the user's target."""
    tracks: list[AudioTrack]
    total_seconds: float
    target_seconds: float
    tolerance_seconds: float

    @property
    def fits_target(self) -> bool:
        """True when the total lands inside the tolerance window."""
        return abs(self.total_seconds - self.target_seconds) <= self.tolerance_seconds

    @property
    def overshoot_seconds(self) -> float:
        """Positive when over target, negative when under. Useful
        for the UI to render a `+0:42` or `-0:18` indicator."""
        return self.total_seconds - self.target_seconds


def usage_counts(
    audio_root: Path,
) -> dict[str, int]:
    """Walk ``<event_root>/04 Curated/Audio/`` and count, per
    track filename, how many existing soundtracks include it.

    Filenames (not full paths) are the key because hard-links
    preserve the basename, so a track copied into N soundtracks
    appears as the same filename N times. Returns an empty dict
    when the audio root doesn't exist yet (first soundtrack for
    the event).
    """
    counts: dict[str, int] = {}
    if not audio_root.exists() or not audio_root.is_dir():
        return counts
    for subdir in audio_root.iterdir():
        if not subdir.is_dir():
            continue
        for child in subdir.iterdir():
            if child.is_file():
                name = child.name
                counts[name] = counts.get(name, 0) + 1
    return counts


def suggest_soundtrack(
    candidates: Iterable[AudioTrack],
    *,
    target_seconds: float,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    usage: Optional[dict[str, int]] = None,
    rng: Optional[random.Random] = None,
) -> SoundtrackPlan:
    """Greedy pick from ``candidates`` to fit ``target_seconds``.

    ``usage`` is the result of ``usage_counts(audio_root)`` — passed
    in rather than computed here so tests can inject deterministic
    counts and the UI can cache it across consecutive Suggest
    clicks (fast: it's already a dict).

    ``rng`` is a ``random.Random`` instance for the tiebreak shuffle;
    tests can pass a seeded one for stable output, the UI passes
    ``None`` (a fresh, time-seeded instance) so consecutive Suggest
    clicks return varied selections.

    Returns a SoundtrackPlan even when the library can't reach the
    target — let the UI render the partial fit with an indicator,
    which is more useful than an empty plan.
    """
    rng = rng or random.Random()
    usage = usage or {}

    # Score by usage count — lower is better. Random tiebreak so
    # tracks with the same usage count cycle between runs.
    scored = [
        (usage.get(t.path.name, 0), rng.random(), t)
        for t in candidates
        if t.duration_seconds > 0  # ignore tracks with unreadable
                                    # duration; can't pack what we
                                    # can't measure
    ]
    scored.sort(key=lambda x: (x[0], x[1]))

    picked: list[AudioTrack] = []
    total = 0.0
    upper_bound = target_seconds + tolerance_seconds

    # Greedy fill: walk the scored list and add any track whose
    # duration still fits under the upper bound. We DON'T break
    # early once the running total reaches the lower bound — that
    # was the original behaviour and it caused ugly outcomes when
    # the random-shuffled order put a few long tracks first
    # (e.g. four 12-minute pieces stop a 47-min picker after just
    # four tracks). Continuing to fill keeps the picker stuffing
    # smaller tracks where they fit until the pool runs out, which
    # is what the user actually wants for a Long-chapter
    # soundtrack.
    for _, _, track in scored:
        if total + track.duration_seconds > upper_bound:
            continue
        picked.append(track)
        total += track.duration_seconds

    return SoundtrackPlan(
        tracks=picked,
        total_seconds=total,
        target_seconds=target_seconds,
        tolerance_seconds=tolerance_seconds,
    )


def write_soundtrack(
    plan: SoundtrackPlan,
    audio_root: Path,
    name: str,
    *,
    use_hardlinks: bool = True,
) -> Path:
    """Materialize a SoundtrackPlan as a folder under
    ``<audio_root>/<name>/`` containing the chosen tracks.

    Hard-links by default when source + dest share a drive (zero
    extra disk space, identical files). Falls back to copy on
    cross-volume or platform issues. Returns the destination
    folder path.

    Raises:
        ValueError: if ``name`` is empty or contains path separators
        FileExistsError: if the destination folder already exists
            and isn't empty (caller decides whether to overwrite)
    """
    if not name or any(sep in name for sep in ("/", "\\", ":")):
        raise ValueError(
            f"Invalid soundtrack name: {name!r} (no slashes / colons)"
        )
    dest_dir = audio_root / name
    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise FileExistsError(
            f"Soundtrack folder already has content: {dest_dir}"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)

    import shutil

    for track in plan.tracks:
        src = track.path
        dest = dest_dir / src.name
        wrote = False
        if use_hardlinks:
            try:
                same_drive = (
                    str(src.drive).lower() == str(dest.drive).lower()
                )
                if same_drive:
                    dest.hardlink_to(src)
                    wrote = True
            except OSError as exc:
                log.debug(
                    "hardlink failed for %s -> %s, falling back to copy: %s",
                    src.name, dest, exc,
                )
        if not wrote:
            shutil.copy2(src, dest)

    log.info(
        "wrote soundtrack %s with %d tracks (%.1fs)",
        dest_dir, len(plan.tracks), plan.total_seconds,
    )
    return dest_dir
