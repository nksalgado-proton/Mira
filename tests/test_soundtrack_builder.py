"""Tests for core.soundtrack_builder."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from core.audio_library import AudioKind, AudioTrack
from core.soundtrack_builder import (
    DEFAULT_TOLERANCE_SECONDS,
    SoundtrackPlan,
    suggest_soundtrack,
    usage_counts,
    write_soundtrack,
)


def _track(name: str, duration: float, mood: str = "cinematic") -> AudioTrack:
    return AudioTrack(
        path=Path(f"/lib/music/{mood}/{name}.mp3"),
        kind=AudioKind.MUSIC,
        mood=mood,
        duration_seconds=duration,
        title=name.replace("_", " "),
    )


def _seeded_rng() -> random.Random:
    """Fixed seed so tiebreak shuffles are deterministic across runs."""
    return random.Random(42)


# ── usage_counts ────────────────────────────────────────────────


def test_usage_counts_empty_when_root_missing(tmp_path):
    assert usage_counts(tmp_path / "Audio") == {}


def test_usage_counts_empty_when_root_empty(tmp_path):
    (tmp_path / "Audio").mkdir()
    assert usage_counts(tmp_path / "Audio") == {}


def test_usage_counts_counts_basenames_across_subfolders(tmp_path):
    """A track copied into two soundtrack folders shows up as
    count=2; one used once is count=1; never used = absent."""
    audio = tmp_path / "Audio"
    (audio / "Long-LaFortuna").mkdir(parents=True)
    (audio / "Short-Drake").mkdir(parents=True)
    (audio / "Long-LaFortuna" / "Aeronaut.mp3").touch()
    (audio / "Long-LaFortuna" / "Ascension.mp3").touch()
    (audio / "Short-Drake" / "Aeronaut.mp3").touch()  # repeated
    counts = usage_counts(audio)
    assert counts == {"Aeronaut.mp3": 2, "Ascension.mp3": 1}


def test_usage_counts_ignores_non_files(tmp_path):
    audio = tmp_path / "Audio"
    (audio / "set1" / "subdir").mkdir(parents=True)
    (audio / "set1" / "track.mp3").touch()
    counts = usage_counts(audio)
    assert counts == {"track.mp3": 1}


# ── suggest_soundtrack ──────────────────────────────────────────


def test_suggest_returns_empty_plan_for_no_candidates():
    plan = suggest_soundtrack([], target_seconds=120)
    assert plan.tracks == []
    assert plan.total_seconds == 0.0
    assert plan.target_seconds == 120
    assert not plan.fits_target


def test_suggest_picks_until_target_reached():
    """3 tracks of 120s each, target 240s ±30 — should pick 2."""
    candidates = [
        _track("a", 120),
        _track("b", 120),
        _track("c", 120),
    ]
    plan = suggest_soundtrack(
        candidates, target_seconds=240,
        tolerance_seconds=30, rng=_seeded_rng(),
    )
    assert len(plan.tracks) == 2
    assert plan.total_seconds == 240
    assert plan.fits_target


def test_suggest_skips_tracks_that_would_overshoot():
    """Target 200 ±20. Pool: [180, 100, 50]. Force a deterministic
    order via usage counts (a=0, b=1, c=2) so a is tried first.
    After 180 we're at the lower-bound (>= 180), so the picker stops.
    Validates that the lower-bound exit is hit and we don't keep
    pulling overshooters from the pool."""
    candidates = [_track("a", 180), _track("b", 100), _track("c", 50)]
    usage = {"a.mp3": 0, "b.mp3": 1, "c.mp3": 2}
    plan = suggest_soundtrack(
        candidates, target_seconds=200,
        tolerance_seconds=20,
        usage=usage,
        rng=_seeded_rng(),
    )
    assert plan.total_seconds == 180
    assert plan.fits_target  # at the lower edge of tolerance


def test_suggest_skips_overshooter_to_find_smaller():
    """Variant of the above: Pool [50, 200, 50] target 100 ±20.
    After picking the 50, the 200 would overshoot massively (250,
    way beyond 120). Picker should skip it and try the next 50,
    landing at 100 exact. Validates skip-not-stop behaviour."""
    candidates = [_track("a", 50), _track("b", 200), _track("c", 50)]
    usage = {"a.mp3": 0, "b.mp3": 1, "c.mp3": 2}
    plan = suggest_soundtrack(
        candidates, target_seconds=100,
        tolerance_seconds=20,
        usage=usage,
        rng=_seeded_rng(),
    )
    assert plan.total_seconds == 100
    assert {t.title for t in plan.tracks} == {"a", "c"}


def test_suggest_returns_partial_when_pool_too_small():
    """Library only has 60s of total material — target is 300 ±30.
    Should return what's available rather than fail."""
    candidates = [_track("a", 60)]
    plan = suggest_soundtrack(
        candidates, target_seconds=300,
        tolerance_seconds=30, rng=_seeded_rng(),
    )
    assert plan.tracks == candidates
    assert plan.total_seconds == 60
    assert not plan.fits_target


def test_suggest_prefers_unused_tracks():
    """Two equally-good tracks but one already used — the unused
    one wins. Verifies the usage_count-first sort key."""
    candidates = [
        _track("used_a", 120),
        _track("fresh_b", 120),
    ]
    usage = {"used_a.mp3": 5}  # heavily used in prior soundtracks
    plan = suggest_soundtrack(
        candidates, target_seconds=120,
        tolerance_seconds=10,
        usage=usage,
        rng=_seeded_rng(),
    )
    assert len(plan.tracks) == 1
    assert plan.tracks[0].title == "fresh b"


def test_suggest_falls_back_to_used_tracks_when_no_alternative():
    """All tracks already used — picker still picks the
    least-used to fill the soundtrack."""
    candidates = [
        _track("a", 120),
        _track("b", 120),
    ]
    usage = {"a.mp3": 3, "b.mp3": 1}
    plan = suggest_soundtrack(
        candidates, target_seconds=120,
        tolerance_seconds=10,
        usage=usage,
        rng=_seeded_rng(),
    )
    assert len(plan.tracks) == 1
    # b has lower usage so it should be picked first.
    assert plan.tracks[0].title == "b"


def test_suggest_ignores_zero_duration_tracks():
    """A track with unreadable duration (size 0 from EXIF parse)
    can't be packed — exclude it rather than treat it as 0s."""
    candidates = [
        _track("broken", 0),
        _track("good", 120),
    ]
    plan = suggest_soundtrack(
        candidates, target_seconds=120,
        tolerance_seconds=10, rng=_seeded_rng(),
    )
    assert len(plan.tracks) == 1
    assert plan.tracks[0].title == "good"


def test_suggest_default_tolerance_used_when_omitted():
    """Tolerance defaults to 30s — verify by leaving it off."""
    candidates = [_track("a", 60)]
    plan = suggest_soundtrack(candidates, target_seconds=85)
    # 60 vs target 85: |60-85| = 25 <= 30 → should fit
    assert plan.fits_target
    assert plan.tolerance_seconds == DEFAULT_TOLERANCE_SECONDS


# ── SoundtrackPlan props ────────────────────────────────────────


def test_plan_overshoot_seconds():
    plan = SoundtrackPlan(
        tracks=[], total_seconds=130, target_seconds=120,
        tolerance_seconds=30,
    )
    assert plan.overshoot_seconds == 10


def test_plan_undershoot_is_negative():
    plan = SoundtrackPlan(
        tracks=[], total_seconds=90, target_seconds=120,
        tolerance_seconds=30,
    )
    assert plan.overshoot_seconds == -30


# ── write_soundtrack ────────────────────────────────────────────


def test_write_creates_folder_and_copies_tracks(tmp_path):
    """The src files need to exist on disk for hard-link / copy to
    succeed — set up a small library + write."""
    library = tmp_path / "lib"
    library.mkdir()
    src1 = library / "alpha.mp3"
    src1.write_bytes(b"fakeaudio1")
    src2 = library / "beta.mp3"
    src2.write_bytes(b"fakeaudio2")

    track1 = AudioTrack(src1, AudioKind.MUSIC, "cinematic", 60.0, title="Alpha")
    track2 = AudioTrack(src2, AudioKind.MUSIC, "cinematic", 60.0, title="Beta")
    plan = SoundtrackPlan(
        tracks=[track1, track2], total_seconds=120,
        target_seconds=120, tolerance_seconds=30,
    )

    audio_root = tmp_path / "Audio"
    dest = write_soundtrack(plan, audio_root, "Long-Test")

    assert dest == audio_root / "Long-Test"
    assert dest.is_dir()
    assert (dest / "alpha.mp3").is_file()
    assert (dest / "beta.mp3").is_file()
    # File contents identical (hard-link or copy).
    assert (dest / "alpha.mp3").read_bytes() == b"fakeaudio1"


def test_write_rejects_invalid_name(tmp_path):
    plan = SoundtrackPlan([], 0, 0, 30)
    with pytest.raises(ValueError):
        write_soundtrack(plan, tmp_path / "Audio", "")
    with pytest.raises(ValueError):
        write_soundtrack(plan, tmp_path / "Audio", "bad/name")
    with pytest.raises(ValueError):
        write_soundtrack(plan, tmp_path / "Audio", "bad\\name")


def test_write_refuses_to_overwrite_non_empty_folder(tmp_path):
    audio_root = tmp_path / "Audio"
    dest = audio_root / "existing"
    dest.mkdir(parents=True)
    (dest / "old.mp3").touch()

    plan = SoundtrackPlan([], 0, 0, 30)
    with pytest.raises(FileExistsError):
        write_soundtrack(plan, audio_root, "existing")


def test_write_allows_existing_empty_folder(tmp_path):
    """Empty folder is fine — happens when the user creates the
    target folder manually before running the builder."""
    audio_root = tmp_path / "Audio"
    dest = audio_root / "empty"
    dest.mkdir(parents=True)
    plan = SoundtrackPlan([], 0, 0, 30)
    # Should not raise.
    write_soundtrack(plan, audio_root, "empty")


# ── Integration: full picker + writer flow ──────────────────────


def test_repeat_call_avoids_previously_used_tracks(tmp_path):
    """Run the picker, write the result, run again — second run
    should pick a different track because the first one shows up
    in usage counts."""
    library = tmp_path / "lib"
    library.mkdir()
    src1 = library / "a.mp3"
    src1.write_bytes(b"x")
    src2 = library / "b.mp3"
    src2.write_bytes(b"y")
    candidates = [
        AudioTrack(src1, AudioKind.MUSIC, "cinematic", 60.0, title="a"),
        AudioTrack(src2, AudioKind.MUSIC, "cinematic", 60.0, title="b"),
    ]
    audio_root = tmp_path / "Audio"

    # First run — pick + write.
    plan1 = suggest_soundtrack(
        candidates, target_seconds=60,
        usage=usage_counts(audio_root),
        rng=_seeded_rng(),
    )
    assert len(plan1.tracks) == 1
    first_pick = plan1.tracks[0]
    write_soundtrack(plan1, audio_root, "First")

    # Second run — usage_counts should now include first_pick.
    plan2 = suggest_soundtrack(
        candidates, target_seconds=60,
        usage=usage_counts(audio_root),
        rng=_seeded_rng(),
    )
    assert len(plan2.tracks) == 1
    assert plan2.tracks[0].path != first_pick.path, (
        "second soundtrack should pick the unused track"
    )
