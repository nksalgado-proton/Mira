"""``core.exif_reader.read_exif_batch`` shape pinning (Nelson 2026-06-09
fast-nav redesign — follow-up).

``pick_photo_surface._spawn_exif_prefetch`` relies on the batch
function reading N files in ONE exiftool subprocess so a 482-photo
day's EXIF warms in a single startup cost rather than 482 × startup.
These tests pin the contract: the batch returns one ``PhotoExif`` per
input (when EXIF is readable), keyed by ``SourceFile`` that matches
the input path via ``Path`` equality.

Skipped when the bundled ``bin/exiftool.exe`` is missing (e.g., test
worktree where the binary wasn't copied) — the bulk read is the
production code path under test, not a Python-level shim."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from core.exif_reader import _get_exiftool_path, read_exif_batch


pytestmark = pytest.mark.skipif(
    not _get_exiftool_path().exists(),
    reason="bundled exiftool binary not present in this checkout",
)


@pytest.fixture()
def three_jpegs(tmp_path: Path) -> list[Path]:
    paths = []
    for i, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
        path = tmp_path / f"photo_{i}.jpg"
        Image.new("RGB", (320, 240), color=color).save(path, "JPEG")
        paths.append(path)
    return paths


def test_batch_returns_one_photo_per_input(three_jpegs):
    results = read_exif_batch(three_jpegs)
    assert len(results) == 3
    for photo in results:
        assert photo is not None
        assert photo.path is not None


def test_batch_paths_match_inputs_via_path_equality(three_jpegs):
    """exiftool writes ``SourceFile`` with forward slashes; ``Path``
    normalises on Windows so equality matches the input ``Path``."""
    results = read_exif_batch(three_jpegs)
    result_paths = {Path(p.path) for p in results}
    for input_path in three_jpegs:
        assert Path(input_path) in result_paths


def test_batch_empty_input_is_empty_output(tmp_path):
    assert read_exif_batch([]) == []


def test_batch_single_file_matches_single_call_shape(tmp_path):
    """A 1-file batch returns the same ``PhotoExif`` shape as
    ``read_exif_single``; the prefetcher uses this when a bucket has
    only one item."""
    from core.exif_reader import read_exif_single
    path = tmp_path / "one.jpg"
    Image.new("RGB", (320, 240), color=(123, 45, 67)).save(path, "JPEG")
    batch = read_exif_batch([path])
    assert len(batch) == 1
    single = read_exif_single(path)
    assert single is not None
    assert Path(batch[0].path) == Path(single.path)
