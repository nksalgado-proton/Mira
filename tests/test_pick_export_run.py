"""Tests for core.cull_export_run (Stage C inc.4c-2, pure)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.cull_export import CollisionPolicy
from core.cull_export_resolver import KeptItem
from core.cull_export_run import collision_count, run_export


def _src(tmp_path, name, data=b"x"):
    s = tmp_path / "src"
    s.mkdir(exist_ok=True)
    f = s / name
    f.write_bytes(data)
    return f


def test_run_export_lays_day_style_under_chosen_dest(tmp_path):
    f = _src(tmp_path, "P1.RW2")
    items = [KeptItem(f, datetime(2025, 10, 26, 7, 8, 9),
                      "Dia 1 - Kathmandu", "wildlife")]
    dest = tmp_path / "pick"
    res = run_export(items, dest, collision=CollisionPolicy.UNIQUE)
    out = dest / "Dia 1 - Kathmandu" / "wildlife" / \
        "20251026_070809_P1.RW2"
    assert out.is_file() and res.ok_count == 1
    assert "02 Selected" not in str(out)        # dest used directly
    assert f.is_file()                          # source untouched


def test_run_export_uses_hardlinks_by_default(tmp_path):
    """Model 3 v2 (Nelson 2026-05-22): the default
    ``allow_hardlinks=True`` makes Cull/Select export materialise
    inode-sharing hardlinks rather than full copies. On filesystems
    that support it (NTFS, ext4, APFS, btrfs, zfs — i.e. every
    realistic Mira target), the destination has the same
    inode as the source."""
    f = _src(tmp_path, "P1.RW2", b"some bytes")
    items = [KeptItem(f, datetime(2025, 10, 26, 7, 8, 9),
                      "Dia 1", "macro")]
    dest = tmp_path / "out"
    res = run_export(items, dest, collision=CollisionPolicy.UNIQUE)
    out = dest / "Dia 1" / "macro" / "20251026_070809_P1.RW2"
    assert out.is_file() and res.ok_count == 1
    # Same inode + link count >= 2 → hardlinked (same volume,
    # which tmp_path always is). On exotic filesystems this would
    # silently fall back to copy via _atomic_copy; that's not a
    # contract violation, just slower disk use.
    import os
    src_stat = os.stat(str(f))
    dst_stat = os.stat(str(out))
    if hasattr(src_stat, "st_ino") and src_stat.st_dev == dst_stat.st_dev:
        # Same volume — expect hardlink. (st_ino comparison is the
        # canonical hardlink test on POSIX; on Windows the inode-
        # equivalent file index works the same way via os.stat.)
        assert src_stat.st_ino == dst_stat.st_ino
        # Link count includes the source itself.
        assert src_stat.st_nlink >= 2


def test_run_export_falls_back_to_copy_when_link_disabled(tmp_path):
    """``allow_hardlinks=False`` always materialises a real copy —
    different inodes, link count 1. Used by Process-Export (which
    transforms files) and by legacy Select-Export (which retimes)."""
    f = _src(tmp_path, "P1.RW2", b"some bytes")
    items = [KeptItem(f, datetime(2025, 10, 26, 7, 8, 9),
                      "Dia 1", "macro")]
    dest = tmp_path / "out"
    res = run_export(
        items, dest, collision=CollisionPolicy.UNIQUE,
        allow_hardlinks=False,
    )
    out = dest / "Dia 1" / "macro" / "20251026_070809_P1.RW2"
    assert out.is_file() and res.ok_count == 1
    import os
    src_stat = os.stat(str(f))
    dst_stat = os.stat(str(out))
    assert src_stat.st_ino != dst_stat.st_ino


def test_collision_count_tracks_existing(tmp_path):
    f = _src(tmp_path, "a.jpg")
    items = [KeptItem(f, None, "Dia 1", "macro")]
    dest = tmp_path / "d"
    assert collision_count(items, dest) == 0
    run_export(items, dest, collision=CollisionPolicy.UNIQUE)
    # Now the same export would collide on that one file.
    assert collision_count(items, dest) == 1


def test_run_export_unique_vs_override(tmp_path):
    """Model 3 v2 (Nelson 2026-05-22): run_export defaults to
    ``allow_hardlinks=True`` — but this test specifically checks the
    copy-semantics behavior (independently mutable destination), so
    we opt out of hardlinks here. The OVERRIDE / UNIQUE collision
    policies are orthogonal to the materialisation strategy."""
    f = _src(tmp_path, "a.jpg", b"NEW")
    items = [KeptItem(f, None, "Dia 1", "macro")]
    dest = tmp_path / "d"
    run_export(
        items, dest, collision=CollisionPolicy.UNIQUE,
        allow_hardlinks=False,
    )
    base = dest / "Dia 1" / "macro"
    (base / "a.jpg").write_bytes(b"OLD")        # pre-existing again

    r_u = run_export(
        items, dest, collision=CollisionPolicy.UNIQUE,
        allow_hardlinks=False,
    )
    assert (base / "a.jpg").read_bytes() == b"OLD"      # kept
    assert r_u.renamed and r_u.renamed[0][1].name != "a.jpg"

    r_o = run_export(
        items, dest, collision=CollisionPolicy.OVERRIDE,
        allow_hardlinks=False,
    )
    assert (base / "a.jpg").read_bytes() == b"NEW"      # replaced
    assert r_o.overwritten
