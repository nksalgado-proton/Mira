"""Tests for core.audio_library."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from core.audio_library import (
    AUDIO_EXTENSIONS,
    AudioKind,
    AudioTrack,
    filter_tracks,
    moods_in_tracks,
    scan_library,
)


# Generate real silent MP3 files for the scanner. Faking the file
# bytes would slip past mutagen's parser into the "tag read failed"
# branch, masking real bugs in the metadata extraction.
_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _make_silent_mp3(
    path: Path, *,
    duration_s: float = 1.0,
    title: str | None = None,
    artist: str | None = None,
    bpm: int | None = None,
) -> Path:
    """Synthesize a short silent MP3 with optional ID3 tags. Used by
    the scanner tests so we exercise mutagen's actual code paths
    rather than mocking around it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", f"{duration_s:.3f}",
        "-c:a", "libmp3lame", "-b:a", "64k",
    ]
    if title is not None:
        cmd += ["-metadata", f"title={title}"]
    if artist is not None:
        cmd += ["-metadata", f"artist={artist}"]
    if bpm is not None:
        cmd += ["-metadata", f"BPM={bpm}"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=15)
    return path


# ── scan_library ────────────────────────────────────────────────


def test_scan_returns_empty_for_missing_root(tmp_path):
    assert scan_library(tmp_path / "does_not_exist") == []


def test_scan_returns_empty_for_empty_root(tmp_path):
    assert scan_library(tmp_path) == []


def test_scan_ignores_non_audio_files(tmp_path):
    """README.txt at the library root shouldn't show up — the
    scanner only walks music/ and sfx/ subtrees."""
    (tmp_path / "README.txt").write_text("ignored")
    (tmp_path / "music" / "cinematic").mkdir(parents=True)
    (tmp_path / "music" / "cinematic" / "notes.txt").write_text("ignored")
    assert scan_library(tmp_path) == []


def test_scan_finds_one_music_track(tmp_path):
    _make_silent_mp3(
        tmp_path / "music" / "cinematic" / "test.mp3",
        duration_s=2.0,
        title="Test Track",
        artist="Test Artist",
    )
    tracks = scan_library(tmp_path)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.kind == AudioKind.MUSIC
    assert t.mood == "cinematic"
    assert t.title == "Test Track"
    assert t.artist == "Test Artist"
    assert t.duration_seconds == pytest.approx(2.0, abs=0.5)
    assert t.file_size_bytes > 0


def test_scan_separates_music_and_sfx(tmp_path):
    _make_silent_mp3(
        tmp_path / "music" / "cinematic" / "song.mp3",
        title="Song",
    )
    _make_silent_mp3(
        tmp_path / "sfx" / "nature" / "rain.mp3",
        title="Rain",
    )
    tracks = scan_library(tmp_path)
    by_kind = {t.kind: t for t in tracks}
    assert by_kind[AudioKind.MUSIC].mood == "cinematic"
    assert by_kind[AudioKind.SFX].mood == "nature"


def test_scan_picks_up_user_mood_folders(tmp_path):
    """Nelson can rename / add mood folders freely — the scanner
    surfaces whatever subfolder names exist under music/ and sfx/."""
    _make_silent_mp3(tmp_path / "music" / "etnico" / "x.mp3")
    _make_silent_mp3(tmp_path / "music" / "underwater" / "y.mp3")
    tracks = scan_library(tmp_path)
    moods = {t.mood for t in tracks}
    assert moods == {"etnico", "underwater"}


def test_scan_handles_nested_subfolders(tmp_path):
    """Deeper nesting still attributes to the immediate mood folder
    so the user can group orchestral / electronic under cinematic
    without losing them."""
    _make_silent_mp3(
        tmp_path / "music" / "cinematic" / "orchestral" / "epic.mp3",
        title="Epic",
    )
    tracks = scan_library(tmp_path)
    assert len(tracks) == 1
    assert tracks[0].mood == "cinematic"


def test_scan_handles_missing_tags_gracefully(tmp_path):
    """File with no ID3 tags should still be returned — title /
    artist come back as None and the UI falls back to the filename."""
    _make_silent_mp3(
        tmp_path / "music" / "ambient" / "untagged.mp3",
        title=None, artist=None,
    )
    tracks = scan_library(tmp_path)
    assert len(tracks) == 1
    assert tracks[0].title is None
    assert tracks[0].artist is None
    assert tracks[0].path.stem == "untagged"


def test_scan_reads_bpm_when_present(tmp_path):
    _make_silent_mp3(
        tmp_path / "music" / "upbeat" / "fast.mp3",
        bpm=128,
    )
    tracks = scan_library(tmp_path)
    assert tracks[0].bpm == 128


def test_scan_sort_order_is_stable(tmp_path):
    """Tracks come back sorted by (kind, mood, title) so the UI list
    doesn't shuffle between scans — Nelson's eye follows the same
    row for the same track."""
    _make_silent_mp3(
        tmp_path / "music" / "cinematic" / "z.mp3", title="Z Song",
    )
    _make_silent_mp3(
        tmp_path / "music" / "cinematic" / "a.mp3", title="A Song",
    )
    _make_silent_mp3(
        tmp_path / "sfx" / "nature" / "rain.mp3", title="Rain",
    )
    tracks = scan_library(tmp_path)
    titles_in_order = [t.title for t in tracks]
    assert titles_in_order == ["A Song", "Z Song", "Rain"]


# ── Alternate root layouts ──────────────────────────────────────


def test_scan_accepts_music_folder_as_root(tmp_path):
    """User pointed at .../audio_library/music/ instead of
    .../audio_library/. Scanner should still find tracks and
    classify them as MUSIC (parent folder name = "music")."""
    music_root = tmp_path / "music"
    _make_silent_mp3(
        music_root / "cinematic" / "track.mp3", title="A Track",
    )
    tracks = scan_library(music_root)
    assert len(tracks) == 1
    assert tracks[0].kind == AudioKind.MUSIC
    assert tracks[0].mood == "cinematic"


def test_scan_accepts_sfx_folder_as_root(tmp_path):
    """Same as above but for sfx/ — kind comes back as SFX."""
    sfx_root = tmp_path / "sfx"
    _make_silent_mp3(sfx_root / "nature" / "rain.mp3", title="Rain")
    tracks = scan_library(sfx_root)
    assert len(tracks) == 1
    assert tracks[0].kind == AudioKind.SFX


def test_scan_accepts_arbitrary_folder_as_root(tmp_path):
    """User picked a folder that's neither music/ nor sfx/ but is
    organized by mood. Default to MUSIC since that's the common case
    for slideshow soundtrack libraries."""
    arbitrary = tmp_path / "my_tracks"
    _make_silent_mp3(arbitrary / "epic" / "x.mp3", title="X")
    tracks = scan_library(arbitrary)
    assert len(tracks) == 1
    assert tracks[0].kind == AudioKind.MUSIC
    assert tracks[0].mood == "epic"


# ── moods_in_tracks ─────────────────────────────────────────────


def test_moods_distinct_alphabetic():
    tracks = [
        AudioTrack(Path("a"), AudioKind.MUSIC, "ambient", 0),
        AudioTrack(Path("b"), AudioKind.MUSIC, "Cinematic", 0),
        AudioTrack(Path("c"), AudioKind.MUSIC, "ambient", 0),
        AudioTrack(Path("d"), AudioKind.SFX, "nature", 0),
    ]
    assert moods_in_tracks(tracks) == ["ambient", "Cinematic", "nature"]


# ── filter_tracks ───────────────────────────────────────────────


def _track(name: str, **kw) -> AudioTrack:
    defaults = dict(
        path=Path(f"/lib/music/cinematic/{name}.mp3"),
        kind=AudioKind.MUSIC,
        mood="cinematic",
        duration_seconds=60.0,
        title=name.replace("_", " "),
    )
    defaults.update(kw)
    return AudioTrack(**defaults)


def test_filter_no_constraints_returns_all():
    tracks = [_track("a"), _track("b")]
    assert filter_tracks(tracks) == tracks


def test_filter_by_kind():
    tracks = [
        _track("song", kind=AudioKind.MUSIC),
        _track("rain", kind=AudioKind.SFX, mood="nature"),
    ]
    music = filter_tracks(tracks, kind=AudioKind.MUSIC)
    assert len(music) == 1 and music[0].title == "song"


def test_filter_by_mood():
    tracks = [
        _track("a", mood="cinematic"),
        _track("b", mood="ambient"),
    ]
    out = filter_tracks(tracks, mood="ambient")
    assert len(out) == 1 and out[0].title == "b"


def test_filter_by_duration_range():
    tracks = [
        _track("short", duration_seconds=15.0),
        _track("medium", duration_seconds=60.0),
        _track("long", duration_seconds=180.0),
    ]
    out = filter_tracks(tracks, min_duration=30, max_duration=120)
    assert [t.title for t in out] == ["medium"]


def test_filter_text_searches_title_artist_filename():
    """Substring match across title/artist/album/filename — the
    user typing 'ocean' should hit a track titled 'Deep Ocean' or
    a filename like ocean_drone.mp3 even with empty tags."""
    tracks = [
        _track("ocean_drone", title="Deep Ocean"),
        _track("forest", title="Forest Walk"),
        _track("file_only", title=None,
               path=Path("/lib/music/x/ocean_thump.mp3")),
    ]
    out = filter_tracks(tracks, text="ocean")
    titles_or_paths = [(t.title, t.path.name) for t in out]
    assert ("Deep Ocean", "ocean_drone.mp3") in titles_or_paths
    assert (None, "ocean_thump.mp3") in titles_or_paths
    assert all("forest" not in (t.title or "").lower() for t in out)


def test_filter_text_is_case_insensitive():
    tracks = [_track("Cinematic_drone", title="Cinematic Drone")]
    assert filter_tracks(tracks, text="CINEMATIC")
    assert filter_tracks(tracks, text="cinematic")


def test_filter_blank_text_means_no_constraint():
    tracks = [_track("a"), _track("b")]
    assert filter_tracks(tracks, text="") == tracks
    assert filter_tracks(tracks, text="   ") == tracks


# ── AUDIO_EXTENSIONS sanity ─────────────────────────────────────


def test_audio_extensions_lowercase():
    """Comparing lowercased path suffixes — extensions must be
    lowercase or a Foo.MP3 file would be ignored."""
    for ext in AUDIO_EXTENSIONS:
        assert ext == ext.lower(), ext
        assert ext.startswith("."), ext
