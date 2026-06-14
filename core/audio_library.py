"""Audio library scanner.

Walks the audio library on disk and returns one ``AudioTrack`` per
audio file found. The library layout convention is:

    <library_root>/
        music/
            cinematic/        # mood = "cinematic", kind = MUSIC
            ambient/
            ...
        sfx/
            nature/           # mood = "nature", kind = SFX
            transitions/
            ...

The first folder under the library root tells us whether a track is
music or a sound effect. The second folder is the mood / category.
The user can add or rename mood folders freely — the scanner picks
them up by name. Any deeper nesting flattens into the same mood.

ID3 / Vorbis tags are read via ``mutagen`` so we can surface title,
artist, BPM and duration in the UI without re-reading files. Tags
that don't exist are returned as ``None`` rather than empty strings
so the UI can decide whether to render a fallback.

Qt-free and synchronous — the UI layer wraps it in a ``QThread``
when scanning a large library. The scanner doesn't validate that
the audio files are playable; mutagen's failure to read tags
returns a track with mostly-None metadata rather than skipping
the file (the user might still want to see the filename).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# Audio formats we'll surface in the UI. Every common slideshow-music
# format is here. Extending the list is the only change needed for
# new formats — mutagen handles the parsing in all of these.
AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga",
    ".wav", ".wma", ".opus",
})

# The two top-level kind folders. A track lives under one of these;
# anything outside is ignored so the user can drop unrelated folders
# (covers, project files) at the library root without polluting
# the scan.
_MUSIC_DIR = "music"
_SFX_DIR = "sfx"


class AudioKind(str, Enum):
    """Whether a track is music or a sound effect. Drives the kind
    filter in the UI and the default copy-target for the chapter
    integration (music goes to the chapter folder; sfx tends to go
    to a separate sfx folder)."""
    MUSIC = "music"
    SFX = "sfx"


@dataclass(frozen=True)
class AudioTrack:
    """One audio file in the library, with the metadata we need to
    filter / preview / use it.

    ``duration_seconds`` is a float because some formats (Opus,
    fractional-frame-count MP3) report sub-second resolution.
    Truncate to int in the UI as needed.

    ``mood`` is the immediate parent folder name — that's the user's
    own taxonomy. The Audio Library page groups by this string and
    populates the mood filter from the set of moods seen.

    ``title`` / ``artist`` / ``bpm`` come from tags when present.
    Falling back on ``path.stem`` for display is the UI's job, not
    the scanner's — keeping ``title`` truly None tells the renderer
    "use the fallback you want", which differs by context.
    """
    path: Path
    kind: AudioKind
    mood: str
    duration_seconds: float
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    bpm: Optional[int] = None
    file_size_bytes: int = 0


def scan_library(library_root: Path) -> list[AudioTrack]:
    """Walk ``library_root/{music,sfx}/<mood>/...`` and return one
    ``AudioTrack`` per audio file found.

    Returns an empty list when the root doesn't exist or contains
    no audio files — the UI shows a helpful onboarding message in
    that case rather than crashing.

    Tracks are returned sorted by (kind, mood, title or filename)
    so the UI list is stable across scans. Order matters because
    the user's eye follows the same row for the same track between
    refreshes — random ordering is jarring.
    """
    if not library_root.exists() or not library_root.is_dir():
        return []

    tracks: list[AudioTrack] = []

    # Detect the root layout. Three forms supported, picked in this
    # order to give the most informative scan when more is available:
    #
    # 1. Library root containing music/ and/or sfx/ subtrees — full
    #    layout, both kinds available with mood subfolders inside.
    # 2. The user pointed at the music/ folder directly (or sfx/),
    #    inferred from the parent folder name. Treat the immediate
    #    children as moods and infer kind from the folder name.
    # 3. None of the above — treat the immediate children as moods
    #    and default kind=MUSIC. The user pointed at an arbitrary
    #    folder that happens to be organized "by mood".
    has_music_dir = (library_root / _MUSIC_DIR).is_dir()
    has_sfx_dir = (library_root / _SFX_DIR).is_dir()

    if has_music_dir or has_sfx_dir:
        # Form 1.
        for kind_dir_name, kind in (
            (_MUSIC_DIR, AudioKind.MUSIC),
            (_SFX_DIR, AudioKind.SFX),
        ):
            kind_root = library_root / kind_dir_name
            if kind_root.is_dir():
                _scan_kind_root(kind_root, kind, tracks)
    else:
        # Form 2 / 3.
        inferred_kind = AudioKind.MUSIC
        if library_root.name.lower() == _SFX_DIR:
            inferred_kind = AudioKind.SFX
        _scan_kind_root(library_root, inferred_kind, tracks)

    tracks.sort(key=lambda t: (
        t.kind.value, t.mood.lower(),
        (t.title or t.path.stem).lower(),
    ))
    return tracks


def _scan_kind_root(
    kind_root: Path, kind: AudioKind, tracks: list[AudioTrack],
) -> None:
    """Walk one ``kind_root`` (a music/ or sfx/ folder) and append
    AudioTracks to ``tracks``. Each immediate child folder is a
    mood; recurse into nested subfolders so cinematic/orchestral/
    is still attributed to "cinematic"."""
    for mood_dir in sorted(kind_root.iterdir()):
        if not mood_dir.is_dir():
            continue
        mood = mood_dir.name
        for path in sorted(mood_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            track = _read_track(path, kind=kind, mood=mood)
            if track is not None:
                tracks.append(track)


def _read_track(
    path: Path, *, kind: AudioKind, mood: str,
) -> Optional[AudioTrack]:
    """Build an ``AudioTrack`` from one file on disk. Tag-read errors
    fall back to filename + duration=0 rather than skipping; the
    user may still want to use the file with a renamed display
    name. Returns ``None`` only on os errors that prevent stat()."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        log.warning("could not stat %s: %s", path, exc)
        return None

    duration = 0.0
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    bpm: Optional[int] = None
    try:
        # Lazy-import mutagen so the scanner module loads in test
        # environments that mock the package or run scanner-free.
        from mutagen import File as MutagenFile  # type: ignore[import]

        meta = MutagenFile(str(path))
        if meta is not None:
            if meta.info is not None:
                duration = float(meta.info.length or 0.0)
            # Tag access via .get() — different formats expose tags
            # under different keys. For ID3 it's TIT2/TPE1/TALB/TBPM;
            # for Vorbis it's "title"/"artist"/"album"/"bpm". Both
            # forms respond to mutagen's high-level dict access.
            title = _first_tag(meta, ["TIT2", "title", "Title", "\xa9nam"])
            artist = _first_tag(meta, ["TPE1", "artist", "Artist", "\xa9ART"])
            album = _first_tag(meta, ["TALB", "album", "Album", "\xa9alb"])
            # BPM frame names vary: TBPM is the standard ID3v2 frame,
            # but some software (notably ffmpeg with -metadata BPM=)
            # writes it as a user-defined TXXX:BPM. Check both.
            raw_bpm = _first_tag(
                meta,
                ["TBPM", "TXXX:BPM", "bpm", "BPM", "tmpo"],
            )
            if raw_bpm is not None:
                try:
                    # BPM tags are sometimes "120", sometimes "120.0",
                    # sometimes "120-130". Take the first integer chunk.
                    bpm = int(float(str(raw_bpm).split("-")[0].strip()))
                except (ValueError, AttributeError):
                    bpm = None
    except Exception as exc:  # noqa: BLE001 — mutagen raises various
        log.debug("tag read failed for %s: %s", path.name, exc)

    return AudioTrack(
        path=path,
        kind=kind,
        mood=mood,
        duration_seconds=duration,
        title=title or None,
        artist=artist or None,
        album=album or None,
        bpm=bpm,
        file_size_bytes=size,
    )


def _first_tag(meta, keys: list[str]) -> Optional[str]:
    """Return the first non-empty tag value for any of ``keys``, or
    None. mutagen tag values are usually lists (TIT2 wraps a single
    string in a one-element list); we collapse to the first entry."""
    for key in keys:
        try:
            value = meta.get(key) if hasattr(meta, "get") else None
        except Exception:  # noqa: BLE001 — defensive
            value = None
        if value is None:
            continue
        # mutagen tag containers are often list-like — pull the head.
        if isinstance(value, list):
            if not value:
                continue
            value = value[0]
        # ID3 frames have a .text list inside; squeeze that out too.
        if hasattr(value, "text"):
            inner = value.text
            if isinstance(inner, list) and inner:
                value = inner[0]
            else:
                value = inner
        text = str(value).strip()
        if text:
            return text
    return None


def moods_in_tracks(tracks: list[AudioTrack]) -> list[str]:
    """Distinct moods across ``tracks`` in stable display order
    (alphabetic). Used to populate the mood filter dropdown."""
    return sorted({t.mood for t in tracks}, key=str.lower)


def list_moods(library_root) -> list[str]:
    """Just the MUSIC mood/category names — no tag reads, no file walk.

    The New Cut dialog's Music combo (spec/61 §5.3) needs only the
    names, and a full :func:`scan_library` reads tags on every file —
    too heavy for opening a dialog. Honors the same root forms as the
    scanner: ``<root>/music/<mood>/`` when a music/ subtree exists,
    else the root's immediate subdirs are the moods (sfx/ excluded).
    Empty on unset/missing/unreadable — graceful absence."""
    if not library_root:
        return []
    root = Path(library_root)
    try:
        if not root.is_dir():
            return []
        music = root / _MUSIC_DIR
        scan_root = music if music.is_dir() else root
        return sorted(
            (d.name for d in scan_root.iterdir()
             if d.is_dir() and not d.name.startswith(".")
             and d.name.lower() != _SFX_DIR),
            key=str.lower)
    except OSError:
        return []


def build_playlist(
    tracks: list[AudioTrack],
    target_seconds: float,
    *,
    rng=None,
) -> list[AudioTrack]:
    """The spec/51 §6 algorithm, unchanged by spec/61: shuffle, sum
    durations until the show is covered, INCLUDE the crossing file
    ("always a bit more" — trim room belongs to PTE). A library
    shorter than the show returns everything it has; the caller
    surfaces the small notice. Deliberately not clever."""
    if target_seconds <= 0 or not tracks:
        return []
    import random as _random
    order = list(tracks)
    (rng or _random).shuffle(order)
    out: list[AudioTrack] = []
    total = 0.0
    for t in order:
        if total >= target_seconds:
            break
        out.append(t)
        total += max(float(t.duration_seconds or 0.0), 0.0)
    return out


def filter_tracks(
    tracks: list[AudioTrack],
    *,
    kind: Optional[AudioKind] = None,
    mood: Optional[str] = None,
    min_duration: Optional[float] = None,
    max_duration: Optional[float] = None,
    text: Optional[str] = None,
) -> list[AudioTrack]:
    """Apply filter UI state to a track list. ``None`` means "no
    constraint" for each parameter. Empty / whitespace-only ``text``
    is treated as no constraint.

    Text search is a case-insensitive substring match across title,
    artist, album and filename. The user typing "ocean" should
    match a track titled "Deep Ocean" as well as a file named
    "ocean_drone_60s.mp3" with empty tags."""
    needle = (text or "").strip().lower() or None
    out: list[AudioTrack] = []
    for t in tracks:
        if kind is not None and t.kind != kind:
            continue
        if mood is not None and t.mood != mood:
            continue
        if (
            min_duration is not None
            and t.duration_seconds < min_duration
        ):
            continue
        if (
            max_duration is not None
            and t.duration_seconds > max_duration
        ):
            continue
        if needle is not None:
            haystack = " ".join(filter(None, [
                t.title, t.artist, t.album, t.path.name,
            ])).lower()
            if needle not in haystack:
                continue
        out.append(t)
    return out
