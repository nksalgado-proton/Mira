"""Tests for core.folder_scanner."""

from pathlib import Path

import pytest

from core.folder_scanner import PHOTO_EXTENSIONS, scan_folder, walk_photo_paths
from core.import_pipeline import RawExifEntry


# ---------------------------------------------------------------------------
# walk_photo_paths — pure filesystem logic, no EXIF reading
# ---------------------------------------------------------------------------

def test_walk_empty_folder(tmp_path):
    result = walk_photo_paths(tmp_path)
    assert result == []


def test_walk_raises_for_missing_folder(tmp_path):
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        walk_photo_paths(missing)


def test_walk_raises_for_file_not_directory(tmp_path):
    f = tmp_path / "a_file.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        walk_photo_paths(f)


def test_walk_filters_by_extension(tmp_path):
    (tmp_path / "photo.RW2").write_bytes(b"fake")
    (tmp_path / "photo.jpg").write_bytes(b"fake")
    (tmp_path / "photo.arw").write_bytes(b"fake")
    (tmp_path / "notes.txt").write_text("not a photo")
    (tmp_path / "video.mp4").write_bytes(b"fake")
    (tmp_path / "readme.md").write_text("not a photo")

    result = walk_photo_paths(tmp_path)
    names = sorted(p.name for p in result)
    assert names == ["photo.RW2", "photo.arw", "photo.jpg"]


def test_walk_case_insensitive_extensions(tmp_path):
    (tmp_path / "upper.RW2").write_bytes(b"x")
    (tmp_path / "lower.rw2").write_bytes(b"x")
    (tmp_path / "mixed.Jpg").write_bytes(b"x")
    (tmp_path / "crazy.JPEG").write_bytes(b"x")

    result = walk_photo_paths(tmp_path)
    assert len(result) == 4


def test_walk_recursive_descends_subdirs(tmp_path):
    (tmp_path / "top.rw2").write_bytes(b"x")
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "inner.rw2").write_bytes(b"x")
    nested = subdir / "deeper"
    nested.mkdir()
    (nested / "deep.jpg").write_bytes(b"x")

    result = walk_photo_paths(tmp_path, recursive=True)
    names = sorted(p.name for p in result)
    assert names == ["deep.jpg", "inner.rw2", "top.rw2"]


def test_walk_non_recursive_stays_at_top(tmp_path):
    (tmp_path / "top.rw2").write_bytes(b"x")
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "inner.rw2").write_bytes(b"x")

    result = walk_photo_paths(tmp_path, recursive=False)
    assert len(result) == 1
    assert result[0].name == "top.rw2"


def test_walk_sorted_output(tmp_path):
    for name in ("zebra.rw2", "apple.rw2", "mango.rw2"):
        (tmp_path / name).write_bytes(b"x")
    result = walk_photo_paths(tmp_path)
    names = [p.name for p in result]
    assert names == sorted(names)


def test_walk_custom_extension_filter(tmp_path):
    (tmp_path / "a.rw2").write_bytes(b"x")
    (tmp_path / "b.jpg").write_bytes(b"x")
    (tmp_path / "c.arw").write_bytes(b"x")

    # Only accept Sony
    result = walk_photo_paths(tmp_path, extensions={".arw"})
    assert len(result) == 1
    assert result[0].name == "c.arw"


def test_walk_skips_directories_even_with_photo_extension(tmp_path):
    # Edge case: a directory named "something.rw2"
    dir_with_ext = tmp_path / "strange.rw2"
    dir_with_ext.mkdir()
    (tmp_path / "actual.rw2").write_bytes(b"x")

    result = walk_photo_paths(tmp_path)
    assert len(result) == 1
    assert result[0].name == "actual.rw2"


def test_photo_extensions_covers_common_raws():
    """Sanity: the default extension set covers all cameras we target."""
    expected = {".rw2", ".arw", ".raf", ".cr2", ".cr3", ".nef", ".dng", ".jpg", ".jpeg"}
    assert expected.issubset(PHOTO_EXTENSIONS)


# ---------------------------------------------------------------------------
# scan_folder — walk_photo_paths + batch EXIF read
# ---------------------------------------------------------------------------

class _FakePhoto:
    """Minimal stand-in matching culler.exif_reader.PhotoExif shape."""
    def __init__(self, path, raw=None):
        self.path = path
        self.raw = raw or {}


def _mock_batch_reader(exif_by_path: dict[Path, dict]):
    """Factory for a fake read_exif_batch mirroring v1.x reader behavior.

    The real reader omits files that ExifTool fails to parse — it does NOT
    return an empty entry for them. We mirror that here: files not in
    ``exif_by_path`` are silently dropped from the return list.
    """
    def _reader(files):
        result = []
        for f in files:
            if f not in exif_by_path:
                continue
            raw = dict(exif_by_path[f])
            raw.setdefault("SourceFile", str(f))
            result.append(_FakePhoto(path=f, raw=raw))
        return result
    return _reader


def test_scan_returns_empty_for_empty_folder(tmp_path):
    result = scan_folder(tmp_path)
    assert result == []


def test_scan_produces_raw_exif_entries(tmp_path, monkeypatch):
    p1 = tmp_path / "a.rw2"
    p2 = tmp_path / "b.rw2"
    p1.write_bytes(b"x")
    p2.write_bytes(b"x")

    exif_map = {
        p1.resolve(): {"Make": "Panasonic", "Model": "DC-G9M2", "FocalLength": "90mm"},
        p2.resolve(): {"Make": "Panasonic", "Model": "DC-G9M2", "FocalLength": "400mm"},
    }
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _mock_batch_reader(exif_map),
    )

    entries = scan_folder(tmp_path)
    assert len(entries) == 2
    assert all(isinstance(e, RawExifEntry) for e in entries)
    assert entries[0].exif["Make"] == "Panasonic"
    assert entries[1].exif["Make"] == "Panasonic"


def test_scan_drops_files_with_no_exif_data(tmp_path, monkeypatch):
    p1 = tmp_path / "good.rw2"
    p2 = tmp_path / "bad.rw2"
    p1.write_bytes(b"x")
    p2.write_bytes(b"x")

    # Only p1 returns from the mock reader
    exif_map = {p1.resolve(): {"Make": "Panasonic", "Model": "DC-G9M2"}}
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _mock_batch_reader(exif_map),
    )

    entries = scan_folder(tmp_path)
    assert len(entries) == 1
    assert entries[0].path.name == "good.rw2"


def test_scan_handles_exiftool_crash_gracefully(tmp_path, monkeypatch):
    (tmp_path / "a.rw2").write_bytes(b"x")
    (tmp_path / "b.rw2").write_bytes(b"x")

    def crashing_reader(_files):
        raise RuntimeError("exiftool failed")

    monkeypatch.setattr("core.exif_reader.read_exif_batch", crashing_reader)

    # Should log warning and return empty rather than raising
    result = scan_folder(tmp_path)
    assert result == []


def test_scan_recursive_picks_up_subdirs(tmp_path, monkeypatch):
    (tmp_path / "top.rw2").write_bytes(b"x")
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "inner.rw2").write_bytes(b"x")

    all_paths = list(tmp_path.glob("**/*.rw2"))
    exif_map = {p.resolve(): {"Make": "Panasonic", "Model": "DC-G9"} for p in all_paths}
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _mock_batch_reader(exif_map),
    )

    entries = scan_folder(tmp_path, recursive=True)
    assert len(entries) == 2


def test_scan_non_recursive_only_top_level(tmp_path, monkeypatch):
    (tmp_path / "top.rw2").write_bytes(b"x")
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "inner.rw2").write_bytes(b"x")

    top_path = (tmp_path / "top.rw2").resolve()
    exif_map = {top_path: {"Make": "Panasonic", "Model": "DC-G9"}}
    monkeypatch.setattr(
        "core.exif_reader.read_exif_batch",
        _mock_batch_reader(exif_map),
    )

    entries = scan_folder(tmp_path, recursive=False)
    assert len(entries) == 1
    assert entries[0].path.name == "top.rw2"
