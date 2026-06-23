"""Cross-event Cut export — audio playlist parity (spec/112).

The per-event :func:`mira.shared.cut_export.export_cut` builds an
``audio/`` playlist sized to the show; the cross-event exporter did
not (verified by spec/112 §1: zero audio references in the cross-event
file before this fix). This file pins the parity:

* A cross-event Cut with a ``music_category`` exports a non-empty
  ``audio/`` subdir alongside the placed members.
* Without a category the subdir is absent (same behaviour as the
  per-event path).
* ``copy_mode`` flows through to the audio placement so the show is
  independent of the audio library when the user asks for it.
"""
from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from core import audio_library
from mira.shared.cross_event_cut_export import export_cross_event_cut
from mira.store.repo import EventStore


NOW = "2026-06-22T00:00:00+00:00"


# --------------------------------------------------------------------- #
# Fixtures — small umbrella + a source event with one exported file +
# a cut + a member.
# --------------------------------------------------------------------- #


def _make_umbrella(tmp_path):
    from mira.gateway.gateway import Gateway
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo

    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    photos_base = tmp_path / "photos"
    photos_base.mkdir()
    gw = Gateway(
        settings=settings, index=index,
        user_store_path=tmp_path / "mira.db",
        now=lambda: NOW, installation_profile="XMC")
    _ = gw.user_store
    settings.update(photos_base_path=str(photos_base))
    return gw, photos_base


def _build_event_with_file(photos_base: Path, *, eid: str, name: str,
                           relpath: str, content: bytes) -> Path:
    """Build a minimal event_root with ONE file under
    ``Exported Media/<relpath>``. Source event.db gets just the event
    row — the audio block degrades gracefully when items() is empty
    (it counts the member as a photo / 0 ms), which is fine: the test
    only needs the show to be long enough to want at least one track."""
    root = photos_base / name
    root.mkdir(exist_ok=True)
    store = EventStore.create(
        root / "event.db", event_id=eid, app_version="test",
        created_at=NOW)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, name, NOW, NOW))
    store.close()
    full = root / "Exported Media" / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)
    return root


def _register(gw, photos_base, root: Path, *, eid: str, name: str) -> None:
    from mira.gateway.index import make_entry
    gw.index.upsert(make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root, photos_base_path=photos_base))


def _seed_cross_event_cut(
    gw, cut_id: str, *, music_category=None, photo_s: float = 6.0,
) -> None:
    """Insert a cut row with the requested music_category + photo_s, then
    one member pointing at the source event."""
    lg = gw.library_gateway()
    with lg.user_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, photo_s, "
            "                 music_category, created_at, updated_at) "
            "VALUES (?, ?, 'user', ?, ?, ?, ?)",
            (cut_id, "test_cut", photo_s, music_category, NOW, NOW))


def _tracks(tmp_path: Path, *secs: float, mood: str = "happy"):
    """Stand-in :class:`audio_library.AudioTrack` list — real files on
    disk (so the link/copy step can act on them) with explicit
    ``duration_seconds`` so ``build_playlist`` is deterministic."""
    out = []
    for i, s in enumerate(secs):
        p = tmp_path / "audio_lib" / f"song{i}.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MP3-" + str(i).encode() * 64)
        out.append(audio_library.AudioTrack(
            path=p, kind=audio_library.AudioKind.MUSIC,
            mood=mood, duration_seconds=float(s)))
    return out


def _setup_one_member_cut(tmp_path, *, music_category):
    """Common setup: an umbrella + a source event with one Exported
    Media file + a cross-event Cut with one member pointing at it.

    Returns ``(gw, cut_id, target)``."""
    gw, photos_base = _make_umbrella(tmp_path)
    src = _build_event_with_file(
        photos_base, eid="src", name="Source",
        relpath="Day01/p1.jpg", content=b"the bytes")
    _register(gw, photos_base, src, eid="src", name="Source")
    cut_id = "cut-audio"
    _seed_cross_event_cut(gw, cut_id, music_category=music_category)
    gw.library_gateway().set_cross_event_cut_members(cut_id, [
        {"kind": "export",
         "export_relpath": "Exported Media/Day01/p1.jpg",
         "event_id": "src"},
    ])
    return gw, cut_id, tmp_path / "out"


# --------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------- #


def test_cross_event_with_music_category_writes_audio_dir(tmp_path):
    """spec/112 §3 — a cross-event Cut with a ``music_category`` exports
    a non-empty ``audio/`` subdir sized to the show, identical to a
    per-event Cut. The audio counters surface on the returned summary
    so the UI can show them."""
    gw, cut_id, target = _setup_one_member_cut(
        tmp_path, music_category="happy")
    # 1 member counted as a photo × 6 s = 6 s show; tracks cover it.
    summary = export_cross_event_cut(
        gw, "anchor-ignored", cut_id, target=target,
        audio_tracks=_tracks(tmp_path, 8, 4), rng=random.Random(1))
    audio_dir = target / "audio"
    assert audio_dir.is_dir()
    audio_files = sorted(p.name for p in audio_dir.iterdir())
    assert len(audio_files) >= 1
    assert all(n.startswith("01_") or n.startswith("02_")
               for n in audio_files)
    assert summary["audio_files"] == len(audio_files)
    # The first track alone (8 s) is enough to cover a 6 s show, so the
    # playlist is NOT short.
    assert summary["audio_short"] is False
    gw.close()


def test_cross_event_without_music_category_has_no_audio_dir(tmp_path):
    """spec/112 §3 / acceptance — no ``music_category`` → no ``audio/``
    subdir (same as per-event Cuts; the category gates the block)."""
    gw, cut_id, target = _setup_one_member_cut(
        tmp_path, music_category=None)
    summary = export_cross_event_cut(
        gw, "anchor-ignored", cut_id, target=target,
        audio_tracks=_tracks(tmp_path, 8), rng=random.Random(1))
    assert not (target / "audio").exists()
    assert summary["audio_files"] == 0
    assert summary["audio_short"] is False
    gw.close()


def test_cross_event_copy_mode_copies_audio_instead_of_linking(tmp_path):
    """spec/112 §3 — ``copy_mode=True`` flows through to the audio
    placement so the playlist is byte-independent of the audio
    library. Verified by inode comparison: a hardlinked file shares
    its source's inode (``st_ino``); a copy does not."""
    gw, cut_id, target = _setup_one_member_cut(
        tmp_path, music_category="happy")
    tracks = _tracks(tmp_path, 8)
    src_track_path = tracks[0].path
    src_ino = src_track_path.stat().st_ino

    summary = export_cross_event_cut(
        gw, "anchor-ignored", cut_id, target=target,
        audio_tracks=tracks, rng=random.Random(1),
        copy_mode=True,
    )
    audio_files = list((target / "audio").iterdir())
    assert audio_files, "expected at least one audio file in copy mode"
    # copy_mode → distinct inode + independent bytes (modifying the
    # source must not affect the export).
    assert all(p.stat().st_ino != src_ino for p in audio_files)
    src_track_path.write_bytes(b"replaced")
    assert all(p.read_bytes() != b"replaced" for p in audio_files)
    assert summary["audio_files"] == len(audio_files)
    gw.close()


def test_cross_event_default_mode_hardlinks_audio_same_volume(tmp_path):
    """Default (``copy_mode=False``) on the same volume hardlinks the
    audio files — the per-event behaviour mirrors here so the user's
    audio library isn't byte-duplicated by default. Verified by inode
    equality."""
    gw, cut_id, target = _setup_one_member_cut(
        tmp_path, music_category="happy")
    tracks = _tracks(tmp_path, 8)
    src_track_path = tracks[0].path
    src_ino = src_track_path.stat().st_ino

    export_cross_event_cut(
        gw, "anchor-ignored", cut_id, target=target,
        audio_tracks=tracks, rng=random.Random(1),
    )
    audio_files = list((target / "audio").iterdir())
    # tmp_path lives on a single volume, so the hardlink must succeed.
    # Linked → distinct path but identical inode.
    assert audio_files
    assert all(p.stat().st_ino == src_ino for p in audio_files), (
        "default mode should hardlink, but inode differs")
    gw.close()


def test_cross_event_audio_short_when_playlist_undercovers_show(tmp_path):
    """spec/112 — the ``audio_short`` flag mirrors the per-event meaning:
    True iff the assembled playlist's total duration is below the
    show's projected length. Pin it so callers can surface a "library
    too sparse" hint."""
    gw, cut_id, target = _setup_one_member_cut(
        tmp_path, music_category="happy")
    # 1 photo × 6 s = 6 s show; 2 s track can't cover it → short.
    summary = export_cross_event_cut(
        gw, "anchor-ignored", cut_id, target=target,
        audio_tracks=_tracks(tmp_path, 2), rng=random.Random(1))
    assert summary["audio_files"] == 1
    assert summary["audio_short"] is True
    gw.close()
