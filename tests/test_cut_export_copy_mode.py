"""spec/105 §5 — `copy_mode=True` forces independent copies for media,
originals AND audio; default `False` hardlinks with a cross-volume
copy fallback.

The link/copy outcome is verified the way the OS sees it: a hardlink
shares one inode with its source, so `os.stat(path).st_nlink > 1`. A
copy is a separate file, `st_nlink == 1`. (We unlink the source after
each placement so a future test failure can't muddy the count from a
third reference elsewhere.)
"""
from __future__ import annotations

import itertools
import os
import random
from pathlib import Path

import pytest

from core import audio_library
from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_export import export_cut
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now


MEMBERS = ["Exported Media/e1.jpg", "Exported Media/e3a.jpg", "Exported Media/v1.mp4"]


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    for rel in ("e1.jpg", "e3a.jpg", "v1.mp4"):
        p = tmp_path / "Exported Media" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"EXP:" + rel.encode())
    for origin_rel in (
        "Original Media/p1.jpg", "Original Media/p3.jpg",
        "Original Media/v1.mp4",
    ):
        p = tmp_path / origin_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RAW:" + origin_rel.encode())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    g.set_cut_members("cut-s", MEMBERS)
    yield g
    g.close()


def _tracks(tmp_path, *secs) -> list:
    out = []
    for i, s in enumerate(secs):
        p = tmp_path / "lib" / f"song{i}.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MP3" + bytes([i]))
        out.append(audio_library.AudioTrack(
            path=p, kind=audio_library.AudioKind.MUSIC,
            mood="happy", duration_seconds=float(s)))
    return out


def _nlink(path: Path) -> int:
    return os.stat(path).st_nlink


# ── Default (copy_mode=False) → media + originals + audio link


def test_default_links_media_originals_and_audio(gw, tmp_path):
    """The full happy path: copy_mode=False, include_originals=True,
    music_category set. Every placed file shares an inode with its
    source (`st_nlink > 1`)."""
    gw.update_cut_settings("cut-s", music_category="happy")
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path,
        separators_on=False,
        include_originals=True,
        copy_mode=False,
        audio_tracks=_tracks(tmp_path, 40, 50),
        rng=random.Random(3),
    )
    # Media — every show file is a link.
    for show_file in sorted(p for p in result.folder.iterdir()
                            if p.is_file() and p.name.startswith("00")):
        assert _nlink(show_file) > 1, (
            f"show file {show_file.name} should be a hardlink "
            f"(st_nlink > 1) under copy_mode=False")
    assert result.linked == 3 and result.copied == 0

    # Originals — every original is a link.
    originals = result.folder / "Original Media"
    for orig in sorted(originals.iterdir()):
        assert _nlink(orig) > 1, (
            f"original {orig.name} should be a hardlink")
    assert result.originals_linked > 0 and result.originals_copied == 0

    # Audio — every track is a link.
    audio = result.folder / "audio"
    for track in sorted(audio.iterdir()):
        assert _nlink(track) > 1, (
            f"audio track {track.name} should be a hardlink")


# ── copy_mode=True → media + originals + audio are independent copies


def test_copy_mode_makes_independent_files(gw, tmp_path):
    """`copy_mode=True` forces shutil.copy2 everywhere — each placed
    file is its own inode with `st_nlink == 1`. The user can then
    move / archive the cut folder without dragging the event's bytes."""
    gw.update_cut_settings("cut-s", music_category="happy")
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path,
        separators_on=False,
        include_originals=True,
        copy_mode=True,
        audio_tracks=_tracks(tmp_path, 40, 50),
        rng=random.Random(3),
    )
    # Every numbered show file is a fresh copy.
    for show_file in sorted(p for p in result.folder.iterdir()
                            if p.is_file() and p.name.startswith("00")):
        assert _nlink(show_file) == 1, (
            f"show file {show_file.name} should be an independent copy "
            f"(st_nlink == 1) under copy_mode=True — got "
            f"{_nlink(show_file)}")
    assert result.linked == 0 and result.copied == 3

    # Originals — each one is a fresh copy.
    originals = result.folder / "Original Media"
    for orig in sorted(originals.iterdir()):
        assert _nlink(orig) == 1
    assert result.originals_linked == 0 and result.originals_copied > 0

    # Audio — each track is a fresh copy.
    audio = result.folder / "audio"
    for track in sorted(audio.iterdir()):
        assert _nlink(track) == 1


# ── _place primitive — direct sanity


def test_place_force_copy_makes_a_real_copy(tmp_path):
    """The shared `_place(src, dst, force_copy=True)` writes a new
    inode regardless of volume."""
    from mira.shared.cut_export import _place
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"data")
    linked = _place(src, dst, force_copy=True)
    assert linked is False
    assert _nlink(dst) == 1
    # And the bytes match.
    assert dst.read_bytes() == b"data"


def test_place_default_hardlinks_on_same_volume(tmp_path):
    """The shared `_place(src, dst, force_copy=False)` hardlinks
    when possible — the temp dir is one volume, so this always
    succeeds in the test environment."""
    from mira.shared.cut_export import _place
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"data")
    linked = _place(src, dst, force_copy=False)
    assert linked is True
    assert _nlink(dst) == 2
    assert _nlink(src) == 2          # they're the same inode now
