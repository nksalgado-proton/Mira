"""spec/61 slice 9 — the export engine (the handoff folder).

Real files on a real temp event tree: sequence naming = chronological
sort, hardlinks (copy fallback counted separately), separator slots in
sequence via the injected writer, snapshot collision folders, missing
sources skipped honestly, the audio playlist linked + the short-library
flag, and the last_exported_at stamp.
"""
from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest

from core import audio_library
from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_export import export_cut
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now

MEMBERS = ["Edited Media/e1.jpg", "Edited Media/e3a.jpg", "Edited Media/v1.mp4"]


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    for ln in ("e1.jpg", "e2.jpg", "e3a.jpg", "e3b.jpg", "v1.mp4"):
        p = tmp_path / "Edited Media" / ln
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"FILE:" + ln.encode())
    counter = itertools.count(1)
    g = EventGateway(store, event_root=tmp_path, now=_now,
                     new_id=lambda: f"id-{next(counter)}")
    g.set_cut_members("cut-s", MEMBERS)
    yield g
    g.close()


def _sep_writer(target: Path, day) -> None:
    target.write_bytes(b"SEP:" + str(day).encode())


def _names(folder: Path) -> list:
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def test_export_sequence_names_with_opener_and_separators(gw, tmp_path):
    cut = gw.cut("cut-s")
    result = export_cut(gw, cut, event_root=tmp_path,
                        separators_on=True, separator_writer=_sep_writer,
                        opener_writer=lambda t: t.write_bytes(b"OPENER"))
    assert result.folder == tmp_path / "Cuts" / "short_version"
    assert _names(result.folder) == [
        "001_opener.jpg", "002_day1.jpg", "003_e1.jpg",
        "004_day2.jpg", "005_e3a.jpg", "006_v1.mp4"]
    assert result.linked == 3 and result.copied == 0
    assert result.separators == 3 and result.missing == []
    # linked, not copied: same content, and the source still exists
    assert (result.folder / "003_e1.jpg").read_bytes() == b"FILE:e1.jpg"
    assert (result.folder / "001_opener.jpg").read_bytes() == b"OPENER"
    assert gw.cut("cut-s").last_exported_at == _now()


def test_export_without_separators(gw, tmp_path):
    cut = gw.cut("cut-s")
    result = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert _names(result.folder) == [
        "001_e1.jpg", "002_e3a.jpg", "003_v1.mp4"]
    assert result.separators == 0


def test_second_export_gets_fresh_snapshot_folder(gw, tmp_path):
    cut = gw.cut("cut-s")
    first = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    second = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert first.folder.name == "short_version"
    assert second.folder.name == "short_version (2)"
    assert _names(first.folder) == _names(second.folder)


def test_missing_source_skipped_and_reported(gw, tmp_path):
    (tmp_path / "Edited Media" / "e3a.jpg").unlink()
    cut = gw.cut("cut-s")
    result = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert result.missing == ["Edited Media/e3a.jpg"]
    assert _names(result.folder) == ["001_e1.jpg", "002_v1.mp4"]


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


def test_audio_playlist_linked_and_covering(gw, tmp_path):
    gw.update_cut_settings("cut-s", music_category="happy")
    cut = gw.cut("cut-s")
    # show = (2 photos + 2 separators) × 6 s + 30 s clip = 54 s
    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=True,
        separator_writer=_sep_writer,
        audio_tracks=_tracks(tmp_path, 40, 50), rng=random.Random(3))
    audio = result.folder / "audio"
    assert result.audio_files == 2 and not result.audio_short
    assert len(_names(audio)) == 2
    assert all(n[:3] in ("01_", "02_") for n in _names(audio))


def test_audio_short_library_flagged(gw, tmp_path):
    gw.update_cut_settings("cut-s", music_category="happy")
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=True,
        separator_writer=_sep_writer,
        audio_tracks=_tracks(tmp_path, 20), rng=random.Random(3))
    assert result.audio_files == 1 and result.audio_short


def test_no_music_category_no_audio_dir(gw, tmp_path):
    cut = gw.cut("cut-s")
    result = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert result.audio_files == 0
    assert not (result.folder / "audio").exists()
