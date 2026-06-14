"""Tests for core.print_export (F-003).

Engine-only — UI tests live in test_print_preview_dialog.py.

Covers:

  1. Direct copy when no collision exists.
  2. ``(2)`` / ``(3)`` / ... suffix when colliding.
  3. Re-print of an already-suffixed name continues the count
     rather than nesting ``(2) (2)``.
  4. The copy is atomic (the destination either has the full
     file or doesn't exist at all — never a partial).
  5. Missing source raises FileNotFoundError.
  6. Directory-as-source raises FileNotFoundError.
  7. ``copy2`` preserves mtime (smoke check — supports the
     "source-as-is" contract).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.print_export import (
    _split_existing_suffix,
    export_for_print,
    resolve_print_target,
)


# ── resolve_print_target (pure path math) ────────────────────────


def test_no_collision_returns_direct_path(tmp_path: Path):
    src = tmp_path / "src" / "IMG_0001.jpg"
    src.parent.mkdir()
    src.write_bytes(b"x")
    dst = tmp_path / "out"
    dst.mkdir()
    out = resolve_print_target(src, dst)
    assert out == dst / "IMG_0001.jpg"


def test_collision_appends_suffix_2(tmp_path: Path):
    src = tmp_path / "src" / "IMG_0001.jpg"
    src.parent.mkdir()
    src.write_bytes(b"x")
    dst = tmp_path / "out"
    dst.mkdir()
    (dst / "IMG_0001.jpg").write_bytes(b"existing")
    out = resolve_print_target(src, dst)
    assert out == dst / "IMG_0001 (2).jpg"


def test_collision_increments_to_3(tmp_path: Path):
    src = tmp_path / "src" / "IMG_0001.jpg"
    src.parent.mkdir()
    src.write_bytes(b"x")
    dst = tmp_path / "out"
    dst.mkdir()
    (dst / "IMG_0001.jpg").write_bytes(b"a")
    (dst / "IMG_0001 (2).jpg").write_bytes(b"b")
    out = resolve_print_target(src, dst)
    assert out == dst / "IMG_0001 (3).jpg"


def test_already_suffixed_source_continues_count(tmp_path: Path):
    """Re-print of ``IMG_0001 (2).jpg`` when that name is taken
    should produce ``IMG_0001 (3).jpg`` — NOT ``IMG_0001 (2) (2).jpg``.
    """
    src = tmp_path / "src" / "IMG_0001 (2).jpg"
    src.parent.mkdir()
    src.write_bytes(b"x")
    dst = tmp_path / "out"
    dst.mkdir()
    (dst / "IMG_0001 (2).jpg").write_bytes(b"existing")
    out = resolve_print_target(src, dst)
    assert out == dst / "IMG_0001 (3).jpg"


def test_split_existing_suffix_no_suffix():
    assert _split_existing_suffix("IMG_0001") == ("IMG_0001", 1)


def test_split_existing_suffix_with_suffix():
    assert _split_existing_suffix("IMG_0001 (5)") == ("IMG_0001", 5)


def test_split_existing_suffix_no_space_is_not_a_suffix():
    """``IMG(2)`` without the space is a legitimate user name, not
    one of our suffixes."""
    assert _split_existing_suffix("IMG(2)") == ("IMG(2)", 1)


# ── export_for_print (real I/O) ───────────────────────────────────


def test_export_copies_bytes(tmp_path: Path):
    src = tmp_path / "src" / "IMG_0001.jpg"
    src.parent.mkdir()
    payload = b"fake jpeg bytes" * 100
    src.write_bytes(payload)
    dst = tmp_path / "out"
    out = export_for_print(src, dst)
    assert out == dst / "IMG_0001.jpg"
    assert out.read_bytes() == payload


def test_export_creates_destination_dir(tmp_path: Path):
    src = tmp_path / "src" / "IMG_0001.jpg"
    src.parent.mkdir()
    src.write_bytes(b"x")
    # Destination dir does NOT exist yet.
    dst = tmp_path / "deep" / "newly-made"
    assert not dst.exists()
    export_for_print(src, dst)
    assert dst.is_dir()


def test_export_collision_writes_suffixed(tmp_path: Path):
    src = tmp_path / "src" / "IMG.jpg"
    src.parent.mkdir()
    src.write_bytes(b"new")
    dst = tmp_path / "out"
    dst.mkdir()
    (dst / "IMG.jpg").write_bytes(b"old")
    out = export_for_print(src, dst)
    assert out == dst / "IMG (2).jpg"
    assert (dst / "IMG.jpg").read_bytes() == b"old"
    assert out.read_bytes() == b"new"


def test_export_missing_source_raises(tmp_path: Path):
    dst = tmp_path / "out"
    with pytest.raises(FileNotFoundError):
        export_for_print(tmp_path / "nowhere.jpg", dst)


def test_export_directory_source_raises(tmp_path: Path):
    src = tmp_path / "src_dir"
    src.mkdir()
    dst = tmp_path / "out"
    with pytest.raises(FileNotFoundError):
        export_for_print(src, dst)


def test_export_leaves_no_partial_on_success(tmp_path: Path):
    """After a successful export, the temporary partial file must
    not remain in the destination directory."""
    src = tmp_path / "src" / "IMG.jpg"
    src.parent.mkdir()
    src.write_bytes(b"data")
    dst = tmp_path / "out"
    export_for_print(src, dst)
    leftovers = [
        p for p in dst.iterdir()
        if p.name.startswith(".") or ".partial-" in p.name
    ]
    assert not leftovers, f"Partial files left behind: {leftovers}"


def test_export_preserves_mtime(tmp_path: Path):
    """``shutil.copy2`` preserves mtime — important for the
    'source-as-is' contract (downstream tools should see the original
    capture time, not the print time)."""
    src = tmp_path / "src" / "IMG.jpg"
    src.parent.mkdir()
    src.write_bytes(b"data")
    # Stamp an old mtime that we can detect.
    old = time.time() - 86_400        # 1 day ago
    os.utime(src, (old, old))
    dst = tmp_path / "out"
    out = export_for_print(src, dst)
    assert abs(out.stat().st_mtime - old) < 2.0
