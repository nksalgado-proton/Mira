"""spec/140 §2 — PTE generator writes a NON-ZERO video duration into
every duration site.

The bug: a Cut emitting videos as ``Duration=0`` everywhere meant PTE
refused to play the clips (they were clocked as 0-length entries
between 7 s photos). The fix is two-fold:

  * Caller probes the EXPORTED ``.mp4`` (not a name-match against
    source items, which never matched Mira-named segment exports) so
    the duration is the file's ground truth — covered by the
    ``share_cuts_page._cut_video_duration_ms`` integration leg in
    :mod:`tests.test_pte_overlay_wiring` (already exercising the same
    seam). Here we pin the GENERATOR contract: when the caller hands
    a non-zero ``duration_ms`` the same value lands in all three
    Duration sites, and when the caller hands 0 the generator
    substitutes a sane minimum rather than emitting ``Duration=0``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mira.shared.pte_project import (
    OVERLAY_OFF,
    PteMember,
    bundled_skeleton_path,
    generate,
    load_skeleton,
)
from mira.shared.pte_project import (
    _MIN_VIDEO_DURATION_MS,
    _safe_video_duration_ms,
)


_GUID = (r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
         r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}")


@pytest.fixture
def skel() -> str:
    return load_skeleton(bundled_fallback=bundled_skeleton_path())


def _section(text: str, name: str) -> str:
    m = re.search(
        rf"\[{re.escape(name)}\]\r\n([\s\S]*?)"
        r"(?=\[[A-Za-z0-9_ ]+\]\r\n|\Z)", text)
    assert m is not None, f"no [{name}] section"
    return m.group(1)


def _generate(skel: str, *members: PteMember) -> str:
    return generate(
        skel, list(members), audio_tracks=[],
        aspect="16:9", photo_seconds=6.0,
        project_path=Path("C:/cut/slideshow.pte"),
        images_folder=Path("C:/cut"),
        overlay_mode=OVERLAY_OFF,
        transition_ms=0,             # easier arithmetic on [Times]
    )


# ── The safety helper ──────────────────────────────────────────────


def test_safe_video_duration_passes_through_real_value():
    assert _safe_video_duration_ms(13267, path="x.mp4") == 13267


def test_safe_video_duration_floors_zero_to_min():
    """Probe failure → 0 → floor to the minimum (so the slide plays
    for the floor instead of being silently dropped by PTE)."""
    assert _safe_video_duration_ms(0, path="x.mp4") == _MIN_VIDEO_DURATION_MS
    assert _safe_video_duration_ms(-50, path="x.mp4") == _MIN_VIDEO_DURATION_MS
    assert _safe_video_duration_ms(None, path="x.mp4") == _MIN_VIDEO_DURATION_MS


# ── Generator: probed duration lands in ALL THREE sites ────────────


def test_video_duration_lands_in_video_objects(skel):
    """spec/140 §2 — both :Video slide objects carry the probed
    duration (``Duration=<ms>`` lines inside the Container)."""
    out = _generate(
        skel,
        PteMember(kind="photo", path=Path("C:/cut/001_p.jpg")),
        PteMember(kind="video", path=Path("C:/cut/002_clip.mp4"),
                  duration_ms=9876),
    )
    slide2 = _section(out, "Slide2")
    assert ":Video\r\n" in slide2
    # The format gives BOTH Video objects (Cover + PlaceInto) a
    # Duration line; both must read the probed value.
    dur_lines = re.findall(r"^    Duration=(\d+)\r?$", slide2, re.MULTILINE)
    assert dur_lines, "expected at least one Duration= line in :Video block"
    assert all(int(d) == 9876 for d in dur_lines), (
        f"spec/140 §2: every :Video object's Duration must be the "
        f"probed clip length (9876); got {dur_lines}"
    )


def test_video_duration_lands_in_tracks_clip(skel):
    """spec/140 §2 — the [Tracks] VideoClip carries the SAME duration."""
    out = _generate(
        skel,
        PteMember(kind="video", path=Path("C:/cut/001_clip.mp4"),
                  duration_ms=4321),
    )
    tracks = _section(out, "Tracks")
    assert "Duration=4321" in tracks, (
        "spec/140 §2: [Tracks] VideoClip must carry the probed duration"
    )


def test_video_duration_lands_in_times_cumulative(skel):
    """spec/140 §2 — the [Times] opt_synchpos for the video slide
    advances by the CLIP length, not the photo seconds. A 10 s clip
    after a 6 s photo lands at 16000, not 12000."""
    out = _generate(
        skel,
        PteMember(kind="photo", path=Path("C:/cut/001_p.jpg")),
        PteMember(kind="video", path=Path("C:/cut/002_clip.mp4"),
                  duration_ms=10000),
    )
    times = _section(out, "Times")
    # transition=0, photo_seconds=6.0 → photo ms = 6000.
    # synchpos1 = 6000 (after photo)
    # synchpos2 = 16000 (photo + 10000 clip), NOT 12000 (photo+photo).
    assert "opt_synchpos1=6000" in times
    assert "opt_synchpos2=16000" in times, (
        f"spec/140 §2: video slide must advance [Times] by the clip "
        f"length; got {times!r}"
    )


def test_all_three_sites_use_the_same_value(skel):
    """The slide :Video Duration, the [Tracks] VideoClip Duration,
    and the [Times] increment for the video slide are all the same
    probed ms. (No site drifts to a different value.)"""
    out = _generate(
        skel,
        PteMember(kind="video", path=Path("C:/cut/001_clip.mp4"),
                  duration_ms=8888),
    )
    slide1 = _section(out, "Slide1")
    tracks = _section(out, "Tracks")
    times = _section(out, "Times")
    # All :Video Duration lines.
    video_durs = {int(d) for d in re.findall(
        r"^    Duration=(\d+)\r?$", slide1, re.MULTILINE)}
    assert video_durs == {8888}
    # [Tracks] VideoClip Duration.
    assert "Duration=8888" in tracks
    # [Times] cumulative for the single video slide.
    assert "opt_synchpos1=8888" in times


# ── Probe failure (Duration=0 in) → defensive minimum out ──────────


def test_zero_probe_never_emits_duration_zero(skel):
    """spec/140 §2 — a Cut member with ``duration_ms=0`` (probe
    failed) must NEVER produce ``Duration=0`` in the .pte; the
    generator substitutes the minimum so PTE still plays the clip."""
    out = _generate(
        skel,
        PteMember(kind="video", path=Path("C:/cut/001_bad.mp4"),
                  duration_ms=0),
    )
    slide1 = _section(out, "Slide1")
    tracks = _section(out, "Tracks")
    times = _section(out, "Times")
    # Slide Duration: floor, not zero.
    durs = re.findall(r"^    Duration=(\d+)\r?$", slide1, re.MULTILINE)
    assert durs and all(int(d) == _MIN_VIDEO_DURATION_MS for d in durs), (
        f"spec/140 §2: zero probe must NOT emit Duration=0; got {durs}"
    )
    # Tracks Duration: floor.
    assert f"Duration={_MIN_VIDEO_DURATION_MS}" in tracks
    # Times cumulative: floor.
    assert f"opt_synchpos1={_MIN_VIDEO_DURATION_MS}" in times


def test_no_duration_zero_anywhere_in_video_pte(skel):
    """Pin the negative — anywhere ``Duration=0`` appears in a
    generator output with one or more video members is a bug."""
    out = _generate(
        skel,
        PteMember(kind="video", path=Path("C:/cut/001_clip.mp4"),
                  duration_ms=0),
        PteMember(kind="video", path=Path("C:/cut/002_clip.mp4"),
                  duration_ms=2500),
    )
    assert "Duration=0\r\n" not in out, (
        "spec/140 §2: no ``Duration=0`` should land in the .pte for "
        "any present mp4"
    )


# ── Photos-only regression: unchanged ──────────────────────────────


def test_photos_only_pte_is_unchanged(skel):
    """The fix is video-only; a photos-only Cut still emits the photo
    Duration ladder unchanged (every slide advances by photo_ms)."""
    out = _generate(
        skel,
        PteMember(kind="photo", path=Path("C:/cut/001.jpg")),
        PteMember(kind="photo", path=Path("C:/cut/002.jpg")),
        PteMember(kind="photo", path=Path("C:/cut/003.jpg")),
    )
    times = _section(out, "Times")
    # 6 s per photo, transition=0 → 6000 / 12000 / 18000.
    assert "opt_synchpos1=6000" in times
    assert "opt_synchpos2=12000" in times
    assert "opt_synchpos3=18000" in times
    tracks = _section(out, "Tracks")
    # No VideoClip — pure photo Cut.
    assert ":VideoClip" not in tracks
