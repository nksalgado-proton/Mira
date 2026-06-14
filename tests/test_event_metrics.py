"""Tests for core.event_metrics — per-phase photo counts.

Synthesise an event tree with known photo counts per subdir, run
each extractor, and assert the dict / int matches."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.event_metrics import (
    captured_photos_per_camera,
    kept_in_cull_count,
    kept_in_process_count,
    kept_in_select_by_style,
    kept_in_select_count,
    total_captured_photos,
)


# ── Helpers ────────────────────────────────────────────────────


def _touch(path: Path, n: int = 1) -> None:
    """Create ``n`` empty .jpg files under ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (path / f"IMG_{i:04d}.jpg").write_bytes(b"")


# ── Captured ───────────────────────────────────────────────────


def test_captured_per_camera_empty_tree(tmp_path):
    """No event tree → empty dict, no exception."""
    assert captured_photos_per_camera(tmp_path) == {}
    assert total_captured_photos(tmp_path) == 0


def test_captured_per_camera_counts_across_buckets_and_days(tmp_path):
    """A camera id is collapsed across all buckets + days. Tests
    cover the three bucket subdirs (_cameras / _phones / _other)."""
    cap = tmp_path / "Original Media"
    # G9 contributes to two days via the cameras bucket.
    _touch(cap / "_cameras" / "Dia 1" / "DC-G9M2", n=5)
    _touch(cap / "_cameras" / "Dia 2" / "DC-G9M2", n=3)
    # Phone contributes via the phones bucket.
    _touch(cap / "_phones" / "Dia 1" / "iPhone 15", n=2)
    # An action cam via the other bucket.
    _touch(cap / "_other" / "Dia 2" / "HERO12", n=1)

    counts = captured_photos_per_camera(tmp_path)
    assert counts == {"DC-G9M2": 8, "iPhone 15": 2, "HERO12": 1}
    assert total_captured_photos(tmp_path) == 11


def test_captured_ignores_non_photo_files(tmp_path):
    """Journals, temp files, etc. don't inflate counts."""
    cam = tmp_path / "Original Media" / "_cameras" / "Dia 1" / "DC-G9M2"
    _touch(cam, n=3)
    (cam / "journal.json").write_text("{}")
    (cam / "IMG_temp.part").write_bytes(b"")
    assert captured_photos_per_camera(tmp_path) == {"DC-G9M2": 3}


def test_captured_ignores_empty_camera_dirs(tmp_path):
    """An empty camera directory means "this camera contributed 0
    photos". It must NOT appear in the dict."""
    cap = tmp_path / "Original Media" / "_cameras" / "Dia 1"
    (cap / "Empty Camera").mkdir(parents=True)
    _touch(cap / "Real Camera", n=2)
    counts = captured_photos_per_camera(tmp_path)
    assert "Empty Camera" not in counts
    assert counts == {"Real Camera": 2}


# ── Cull ───────────────────────────────────────────────────────


def test_kept_in_cull_count_walks_entire_culled_tree(tmp_path):
    """The cull layout is per-camera under each bucket. The total is
    a flat walk under ``01 - Culled/``."""
    culled = tmp_path / "01 - Culled"
    _touch(culled / "_cameras" / "Dia 1" / "DC-G9M2" / "wildlife", n=4)
    _touch(culled / "_cameras" / "Dia 2" / "DC-G9M2" / "landscape", n=3)
    _touch(culled / "_phones" / "Dia 1" / "iPhone 15" / "uncategorized", n=1)
    assert kept_in_cull_count(tmp_path) == 8


def test_kept_in_cull_count_zero_when_no_cull_yet(tmp_path):
    assert kept_in_cull_count(tmp_path) == 0


# ── Select ─────────────────────────────────────────────────────


def test_kept_in_select_by_style_counts_per_style(tmp_path):
    """The Select layout is ``02 - Selected/<day>/<style>/``. Styles
    are collapsed across days."""
    sel = tmp_path / "02 - Selected"
    _touch(sel / "Dia 1" / "wildlife", n=5)
    _touch(sel / "Dia 1" / "landscape", n=2)
    _touch(sel / "Dia 2" / "wildlife", n=3)
    _touch(sel / "Dia 2" / "macro", n=1)

    counts = kept_in_select_by_style(tmp_path)
    assert counts == {"wildlife": 8, "landscape": 2, "macro": 1}
    assert kept_in_select_count(tmp_path) == 11


def test_kept_in_select_empty(tmp_path):
    assert kept_in_select_by_style(tmp_path) == {}
    assert kept_in_select_count(tmp_path) == 0


# ── Process ────────────────────────────────────────────────────


def test_kept_in_process_count_walks_processed_tree(tmp_path):
    proc = tmp_path / "Edited Media"
    _touch(proc / "Dia 1" / "wildlife", n=3)
    _touch(proc / "Dia 2" / "landscape", n=2)
    assert kept_in_process_count(tmp_path) == 5


def test_kept_in_process_count_zero_when_no_process_yet(tmp_path):
    assert kept_in_process_count(tmp_path) == 0
