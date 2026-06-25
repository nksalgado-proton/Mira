"""spec/149 §2.A — standalone PTE generation against an existing
exported Cut folder.

Generate PTE writes ``<stem>.pte`` into a folder using whatever media
files are already there — no re-export. The .pte's baked absolute
paths reflect the folder's current location, so a renamed bundle is
self-healing after one click.

These tests pin:
  * Walking a folder with photos + videos + ``audio/`` produces a
    valid .pte (BOM + CRLF) whose paths match the folder.
  * Renaming the folder + re-running Generate produces .pte paths that
    match the renamed folder (no stale absolute paths leaking through).
  * The media bytes are NOT re-touched — only the .pte changes.
  * An empty folder (no media) returns ``None`` instead of writing a
    project with zero slides.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mira.shared.cut_pte_generation import generate_pte_for_folder
from mira.shared.pte_project import bundled_skeleton_path


def _seed_bundle(folder: Path, *, with_audio: bool = True) -> dict:
    """Seed ``folder`` with a typical exported Cut bundle: two photos,
    one video, plus an ``audio/`` track. Returns a dict mapping role
    to absolute path + original byte payload so tests can assert
    no-re-write."""
    folder.mkdir(parents=True, exist_ok=True)
    photo_a = folder / "001_a.jpg"
    photo_b = folder / "002_b.jpg"
    video = folder / "003_clip.mp4"
    photo_a.write_bytes(b"PHOTO-A-BYTES")
    photo_b.write_bytes(b"PHOTO-B-BYTES")
    video.write_bytes(b"VIDEO-BYTES")
    out = {
        "photo_a": (photo_a, b"PHOTO-A-BYTES"),
        "photo_b": (photo_b, b"PHOTO-B-BYTES"),
        "video": (video, b"VIDEO-BYTES"),
    }
    if with_audio:
        audio_dir = folder / "audio"
        audio_dir.mkdir(exist_ok=True)
        track = audio_dir / "01_song.mp3"
        track.write_bytes(b"MP3-BYTES")
        out["track"] = (track, b"MP3-BYTES")
    return out


def _read_pte(path: Path) -> str:
    """Read a written .pte as text (BOM stripped, CRLF preserved)."""
    raw = path.read_bytes()
    return raw.decode("utf-8-sig")


def test_generate_writes_pte_when_none_exists(tmp_path):
    """Folder with media + audio/ but no .pte → Generate PTE writes a
    valid project. BOM + CRLF (the PTE format) on the byte payload."""
    folder = tmp_path / "Cuts" / "iceland"
    _seed_bundle(folder)
    assert not list(folder.glob("*.pte"))

    out = generate_pte_for_folder(
        folder, aspect="16:9", photo_seconds=6.0,
        stem="iceland",
        bundled_fallback=bundled_skeleton_path())

    assert out == folder / "iceland.pte"
    assert out.is_file()
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")           # BOM
    assert b"\r\n" in raw                             # CRLF


def test_pte_paths_match_folder(tmp_path):
    """The .pte's ProjectFilePath / ImagesFolder / per-slide FileName
    point at the folder where the project was written — not at some
    captured-at-export-time absolute path."""
    folder = tmp_path / "deeply" / "nested" / "iceland"
    seeds = _seed_bundle(folder)

    out = generate_pte_for_folder(
        folder, aspect="16:9", photo_seconds=6.0,
        stem="iceland",
        bundled_fallback=bundled_skeleton_path())
    text = _read_pte(out)

    # Windows-shape (backslashes) for the on-disk paths.
    folder_win = str(folder).replace("/", "\\")
    assert f"ProjectFilePath={folder_win}\\iceland.pte" in text
    assert f"ImagesFolder={folder_win}\\" in text
    # Every photo / video is referenced at the folder it sits in.
    for role in ("photo_a", "photo_b", "video"):
        path = str(seeds[role][0]).replace("/", "\\")
        assert path in text, (
            f"expected {role} path {path} in .pte")


def test_renaming_folder_yields_correct_renamed_paths(tmp_path):
    """Rename the export folder, re-run Generate PTE → the new .pte
    carries the renamed paths (the original failure mode that spec/148
    + spec/149 together fix). The user's manual rename now self-heals."""
    src = tmp_path / "Cuts" / "iceland (2)"
    _seed_bundle(src)
    # Write the first .pte under the (2) name.
    first = generate_pte_for_folder(
        src, aspect="16:9", photo_seconds=6.0,
        stem="iceland",
        bundled_fallback=bundled_skeleton_path())
    assert first is not None
    # Stale paths bake the (2) name in.
    assert "iceland (2)" in _read_pte(first).replace("/", "\\")

    # User manually renames the folder to drop the (2).
    dst = tmp_path / "Cuts" / "iceland"
    shutil.move(src, dst)
    # Re-run Generate PTE on the renamed folder.
    second = generate_pte_for_folder(
        dst, aspect="16:9", photo_seconds=6.0,
        stem="iceland",
        bundled_fallback=bundled_skeleton_path())
    assert second == dst / "iceland.pte"
    text = _read_pte(second)
    # The renamed folder's path lands in the .pte; no (2) anywhere.
    assert "iceland (2)" not in text
    dst_win = str(dst).replace("/", "\\")
    assert f"ProjectFilePath={dst_win}\\iceland.pte" in text
    assert f"ImagesFolder={dst_win}\\" in text


def test_media_bytes_are_not_rewritten(tmp_path):
    """Generate PTE walks the folder and writes the .pte ONLY — every
    media file's bytes (and mtime, modulo FS coarseness) are
    untouched."""
    folder = tmp_path / "iceland"
    seeds = _seed_bundle(folder)
    # Record byte payloads + stat snapshots BEFORE the call.
    before = {
        role: (path.read_bytes(), path.stat().st_mtime_ns)
        for role, (path, _) in seeds.items()
    }

    out = generate_pte_for_folder(
        folder, aspect="16:9", photo_seconds=6.0,
        stem="iceland",
        bundled_fallback=bundled_skeleton_path())
    assert out is not None

    for role, (path, _) in seeds.items():
        cur_bytes = path.read_bytes()
        cur_mtime = path.stat().st_mtime_ns
        assert cur_bytes == before[role][0], (
            f"{role} bytes changed — media was re-written")
        assert cur_mtime == before[role][1], (
            f"{role} mtime changed — media was re-touched")


def test_empty_folder_returns_none(tmp_path):
    """A folder with no media members has nothing to wrap in a
    project — Generate PTE returns None instead of producing an empty
    .pte that would fail to load in PTE."""
    folder = tmp_path / "empty"
    folder.mkdir()
    out = generate_pte_for_folder(
        folder, aspect="16:9", photo_seconds=6.0,
        stem="x", bundled_fallback=bundled_skeleton_path())
    assert out is None
    assert not list(folder.glob("*.pte"))


def test_missing_folder_returns_none(tmp_path):
    """A folder that doesn't exist at all returns None — nothing to do."""
    folder = tmp_path / "does_not_exist"
    out = generate_pte_for_folder(
        folder, aspect="16:9", photo_seconds=6.0,
        stem="x", bundled_fallback=bundled_skeleton_path())
    assert out is None


def test_overwrite_replaces_existing_pte_at_canonical_name(tmp_path):
    """A folder that already has ``<stem>.pte`` — Generate PTE
    overwrites it (no ``<stem> (2).pte`` sibling). That's the standalone
    contract: the canonical filename, every time."""
    folder = tmp_path / "iceland"
    _seed_bundle(folder)
    (folder / "iceland.pte").write_bytes(b"STALE-PTE")
    out = generate_pte_for_folder(
        folder, aspect="16:9", photo_seconds=6.0,
        stem="iceland", bundled_fallback=bundled_skeleton_path())
    assert out == folder / "iceland.pte"
    assert (folder / "iceland.pte").read_bytes() != b"STALE-PTE"
    assert not (folder / "iceland (2).pte").exists()


def test_audio_dir_drives_music_block(tmp_path):
    """Audio tracks in ``folder/audio/`` show up in the PTE's Music
    block FileName lines (the path is whatever the on-disk track has
    — no re-copy, no re-link)."""
    folder = tmp_path / "iceland"
    seeds = _seed_bundle(folder)
    track_path = seeds["track"][0]

    out = generate_pte_for_folder(
        folder, aspect="16:9", photo_seconds=6.0,
        stem="iceland", bundled_fallback=bundled_skeleton_path())
    text = _read_pte(out)
    assert str(track_path).replace("/", "\\") in text


def test_no_audio_dir_still_writes_pte(tmp_path):
    """A folder with media but no audio/ subdir still writes a valid
    .pte. The Music block emits its skeleton-shape with no items."""
    folder = tmp_path / "silent"
    _seed_bundle(folder, with_audio=False)
    assert not (folder / "audio").exists()

    out = generate_pte_for_folder(
        folder, aspect="16:9", photo_seconds=6.0,
        stem="silent", bundled_fallback=bundled_skeleton_path())
    assert out is not None
    text = _read_pte(out)
    # Music block still rendered, just with no TMusicItem entries.
    assert "object Music:Music" in text
    assert "TMusicItem" not in text


def test_stem_falls_back_to_slideshow_when_empty(tmp_path):
    """Empty / whitespace-only stem → ``slideshow.pte`` (parity with
    the export-time slideshow_target fallback so a Cut with no name
    still produces a sane filename)."""
    folder = tmp_path / "x"
    _seed_bundle(folder)
    out = generate_pte_for_folder(
        folder, aspect="16:9", photo_seconds=6.0,
        stem="   ", bundled_fallback=bundled_skeleton_path())
    assert out == folder / "slideshow.pte"
