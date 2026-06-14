"""Tests for core.thumb_cache.

docs/24 Step 1. The cache mirrors the source video's path under
``<event_root>/.cache/thumbs/<source_rel>/<item_id>.jpg`` so two
cameras producing the same filename in the same event can't collide.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import thumb_cache
from core.video_extract import _make_test_video


@pytest.fixture
def red_video(tmp_path: Path) -> Path:
    """1-second 320x240 red — bright enough that the black-frame
    guard won't trigger."""
    return _make_test_video(tmp_path / "src" / "DSC_0042.MP4")


@pytest.fixture
def black_video(tmp_path: Path) -> Path:
    """1-second 320x240 black — used to verify the black-frame
    fallback fires. ``color=black`` produces mean luma 0 across the
    whole clip."""
    return _make_test_video(
        tmp_path / "src" / "DARK.MP4", color="black",
    )


def test_thumb_path_layout(tmp_path: Path):
    """The path must include the source-relative directory so two
    cameras with the same filename can't collide."""
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/DSC_0042.MP4")
    p = thumb_cache.thumb_path(event, rel, "c1")
    assert p == (
        event / ".cache" / "thumbs"
        / "00 - Captured" / "Day 1" / "G9" / "DSC_0042.MP4"
        / "c1.jpg"
    )


def test_thumb_path_no_collision_across_cameras(tmp_path: Path):
    """Same stem from two cameras → different cache paths."""
    event = tmp_path / "event"
    g9 = thumb_cache.thumb_path(
        event, Path("00 - Captured/Day 1/G9/IMG_0001.MP4"), "c1",
    )
    gopro = thumb_cache.thumb_path(
        event, Path("00 - Captured/Day 1/GoPro/IMG_0001.MP4"), "c1",
    )
    assert g9 != gopro


def test_ensure_thumb_creates_on_miss(
    tmp_path: Path, red_video: Path,
):
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/DSC_0042.MP4")
    out = thumb_cache.ensure_thumb(
        event, red_video, rel, "c1", position_ms=200,
    )
    assert out.exists()
    assert out.stat().st_size > 0
    # Parent dirs were created by ensure_thumb, not by the caller.
    assert out.parent.is_dir()


def test_ensure_thumb_idempotent_on_hit(
    tmp_path: Path, red_video: Path,
):
    """A second call with the same item_id must not rewrite the
    JPEG — the cache is a hit, and the mtime should not advance."""
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/DSC_0042.MP4")
    first = thumb_cache.ensure_thumb(
        event, red_video, rel, "c1", position_ms=200,
    )
    mtime_before = first.stat().st_mtime_ns
    second = thumb_cache.ensure_thumb(
        event, red_video, rel, "c1", position_ms=200,
    )
    assert second == first
    assert second.stat().st_mtime_ns == mtime_before


def test_ensure_thumb_missing_source_raises(tmp_path: Path):
    """ffmpeg can't extract from a nonexistent file — surface that
    cleanly rather than silently writing an empty JPEG."""
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/MISSING.MP4")
    with pytest.raises(FileNotFoundError):
        thumb_cache.ensure_thumb(
            event, tmp_path / "nope.mp4", rel, "c1", position_ms=0,
        )


def test_ensure_thumb_keeps_dark_frame_without_fallback(
    tmp_path: Path, black_video: Path,
):
    """No fallback supplied → even a pitch-black frame stays put.
    The guard only kicks in when the caller opts in by passing a
    fallback position."""
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/DARK.MP4")
    out = thumb_cache.ensure_thumb(
        event, black_video, rel, "c1", position_ms=0,
    )
    assert out.exists()
    assert thumb_cache._mean_luma(out) < thumb_cache._BLACK_LUMA_THRESHOLD


def test_ensure_thumb_falls_back_for_dark_frame(
    tmp_path: Path, black_video: Path,
):
    """When the primary extract is below the luma threshold AND a
    fallback is given, the module re-extracts at the fallback. In
    this synthetic case both positions are equally black so the
    final file is still dark — but ensure_thumb must have tried
    twice (we can't observe the retry directly, but the function
    must complete without error and return the same path)."""
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/DARK.MP4")
    out = thumb_cache.ensure_thumb(
        event, black_video, rel, "c1",
        position_ms=0, fallback_position_ms=500,
    )
    assert out.exists()


def test_mean_luma_red_above_threshold(
    tmp_path: Path, red_video: Path,
):
    """A bright-red frame must read well above the black threshold —
    sanity check that the threshold isn't gating normal content."""
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/DSC_0042.MP4")
    out = thumb_cache.ensure_thumb(
        event, red_video, rel, "c1", position_ms=200,
    )
    assert thumb_cache._mean_luma(out) > thumb_cache._BLACK_LUMA_THRESHOLD


# --------------------------------------------------------------------------- #
# spec/59 — the black-frame ladder + cache self-heal
# --------------------------------------------------------------------------- #


def _make_fade_video(
    path: Path, *, duration_s: float, fade_start_s: float,
    fade_dur_s: float,
) -> Path:
    """Red video that opens BLACK and fades in — the canonical
    fade-in-opener case the ladder exists for."""
    from core.proc import run as _run_hidden
    from core.video_extract import _FFMPEG_EXE
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=red:s=320x240:d={duration_s}:r=30",
        "-vf", f"fade=t=in:st={fade_start_s}:d={fade_dur_s}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(path),
    ]
    _run_hidden(cmd, check=True, capture_output=True, timeout=30)
    return path


def test_ladder_walks_forward_past_a_fade_in(tmp_path: Path):
    """The old fallback walked BACKWARDS to 0 ms — blacker still on a
    fade-in. The ladder walks forward (3 s rung) and lands bright."""
    video = _make_fade_video(
        tmp_path / "src" / "FADE.MP4",
        duration_s=4.0, fade_start_s=1.5, fade_dur_s=2.0)
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/FADE.MP4")
    out = thumb_cache.ensure_thumb(
        event, video, rel, "daygrid",
        position_ms=1000, fallback_position_ms=0,
    )
    assert thumb_cache._mean_luma(out) >= thumb_cache._BLACK_LUMA_THRESHOLD
    assert out.with_suffix(".vetted").exists()


def test_ladder_duration_fractions_reach_a_long_dark_opener(tmp_path: Path):
    """When even 3 s is dark, the probed 10 %/25 % rungs keep walking."""
    video = _make_fade_video(
        tmp_path / "src" / "LONGFADE.MP4",
        duration_s=16.0, fade_start_s=3.5, fade_dur_s=0.5)
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/LONGFADE.MP4")
    out = thumb_cache.ensure_thumb(
        event, video, rel, "daygrid",
        position_ms=1000, fallback_position_ms=0,
    )
    # 25 % of 16 s = 4 s — past the fade; bright.
    assert thumb_cache._mean_luma(out) >= thumb_cache._BLACK_LUMA_THRESHOLD


def test_cached_black_thumb_self_heals_once(tmp_path: Path):
    """A legacy black thumb (cached before the ladder existed) gets ONE
    ladder re-run on the next hit — then the vetted sidecar holds."""
    from PIL import Image
    video = _make_fade_video(
        tmp_path / "src" / "FADE.MP4",
        duration_s=4.0, fade_start_s=1.5, fade_dur_s=2.0)
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/FADE.MP4")
    dest = thumb_cache.thumb_path(event, rel, "daygrid")
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (0, 0, 0)).save(dest, "JPEG")
    assert thumb_cache._mean_luma(dest) < thumb_cache._BLACK_LUMA_THRESHOLD

    out = thumb_cache.ensure_thumb(
        event, video, rel, "daygrid", position_ms=1000)
    assert out == dest
    assert thumb_cache._mean_luma(out) >= thumb_cache._BLACK_LUMA_THRESHOLD
    assert out.with_suffix(".vetted").exists()


def test_vetted_dark_thumb_is_never_reextracted(
    tmp_path: Path, black_video: Path,
):
    """A genuinely dark video stays dark (brightest candidate kept) and
    the sidecar stops the heal from re-running on every paint."""
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/DARK.MP4")
    out = thumb_cache.ensure_thumb(
        event, black_video, rel, "daygrid",
        position_ms=1000, fallback_position_ms=0,
    )
    assert thumb_cache._mean_luma(out) < thumb_cache._BLACK_LUMA_THRESHOLD
    assert out.with_suffix(".vetted").exists()
    mtime = out.stat().st_mtime_ns
    again = thumb_cache.ensure_thumb(
        event, black_video, rel, "daygrid",
        position_ms=1000, fallback_position_ms=0,
    )
    assert again.stat().st_mtime_ns == mtime


def test_poster_path_if_cached_is_cache_only(
    tmp_path: Path, red_video: Path,
):
    """The player-surface lookup NEVER extracts — None on a cold cache,
    the JPEG after the grid populated it."""
    event = tmp_path / "event"
    rel = Path("00 - Captured/Day 1/G9/DSC_0042.MP4")
    assert thumb_cache.poster_path_if_cached(event, rel) is None
    thumb_cache.ensure_thumb(
        event, red_video, rel, thumb_cache.DAYGRID_ITEM_ID,
        position_ms=200,
    )
    p = thumb_cache.poster_path_if_cached(event, rel)
    assert p is not None and p.exists()
