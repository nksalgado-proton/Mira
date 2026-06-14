"""Tests for core.process_export_engine.

Covers the multi-bucket walk + collision handling + per-photo
override path. The render pipeline is mocked out for the tests that
don't care about pixels; tests that DO care write a real JPEG and
read it back.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.cull_export import CollisionPolicy, ExportFileType
from core.cull_state import STATE_DISCARDED as STATE_SKIPPED, STATE_KEPT as STATE_PICKED
from core.process_export_engine import (
    ProcessBucketInput,
    run_process_export,
)


# ── Helpers ────────────────────────────────────────────────────


def _write_jpeg(path: Path, *, size=(100, 100), color=(127, 127, 127)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=85)


def _make_bucket(
    src_dir: Path,
    kept_names: list[str],
    extra_names: list[str] | None = None,
    *,
    day_label: str = "Dia 1",
    style_label: str = "uncategorized",
) -> ProcessBucketInput:
    """Create real JPEGs under ``src_dir`` and a journal where only
    ``kept_names`` are KEPT."""
    all_names = list(kept_names) + list(extra_names or [])
    files = []
    for name in all_names:
        path = src_dir / name
        _write_jpeg(path)
        files.append(path)
    journal = {
        "marks": {n: STATE_PICKED for n in kept_names}
        | {n: STATE_SKIPPED for n in (extra_names or [])},
    }
    return ProcessBucketInput(
        files=tuple(files), journal=journal,
        day_label=day_label, style_label=style_label,
    )


# ── Multi-bucket walk ──────────────────────────────────────────


def test_run_process_export_writes_one_file_per_kept_photo(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket = _make_bucket(src, ["a.jpg", "b.jpg"], extra_names=["c.jpg"])

    result = run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
    )

    assert result.ok_count == 2
    # Files land under dest/<day>/<stem>.jpg (flat per-day — docs/25 §8)
    assert (dest / "Dia 1" / "a.jpg").exists()
    assert (dest / "Dia 1" / "b.jpg").exists()
    # The DISCARDED file is NOT exported.
    assert not (dest / "Dia 1" / "c.jpg").exists()


def test_run_process_export_walks_multiple_buckets_and_days(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket1 = _make_bucket(
        src / "day1" / "wildlife", ["w1.jpg"],
        day_label="Dia 1", style_label="wildlife")
    bucket2 = _make_bucket(
        src / "day2" / "macro", ["m1.jpg", "m2.jpg"],
        day_label="Dia 2", style_label="macro")

    result = run_process_export(
        [bucket1, bucket2], dest, file_type=ExportFileType.JPEG,
    )

    assert result.ok_count == 3
    assert (dest / "Dia 1" / "w1.jpg").exists()
    assert (dest / "Dia 2" / "m1.jpg").exists()
    assert (dest / "Dia 2" / "m2.jpg").exists()


# ── Collision policy ───────────────────────────────────────────


def test_run_process_export_unique_appends_suffix(tmp_path):
    """UNIQUE = leave the existing file alone, write under
    ``stem (2).jpg``."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket = _make_bucket(src, ["a.jpg"])
    # Pre-seed a collision: a file with the same final name already
    # exists at the destination.
    pre_existing = dest / "Dia 1" / "a.jpg"
    pre_existing.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), (255, 0, 0)).save(pre_existing, "JPEG")
    pre_bytes = pre_existing.read_bytes()

    result = run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
        collision=CollisionPolicy.UNIQUE,
    )

    # The pre-existing file is UNTOUCHED.
    assert pre_existing.read_bytes() == pre_bytes
    # And a new file landed under "a (2).jpg".
    new_file = dest / "Dia 1" / "a (2).jpg"
    assert new_file.exists()
    assert result.renamed and result.renamed[0][1] == new_file


def test_run_process_export_override_replaces_existing(tmp_path):
    """OVERRIDE = overwrite the existing file atomically."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket = _make_bucket(src, ["a.jpg"])
    pre_existing = dest / "Dia 1" / "a.jpg"
    pre_existing.parent.mkdir(parents=True, exist_ok=True)
    # Write a tiny red 10×10 file we can distinguish.
    Image.new("RGB", (10, 10), (255, 0, 0)).save(pre_existing, "JPEG")
    pre_bytes = pre_existing.read_bytes()

    result = run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
        collision=CollisionPolicy.OVERRIDE,
    )

    # The file STILL exists at the same path but the bytes are different.
    assert pre_existing.exists()
    assert pre_existing.read_bytes() != pre_bytes
    assert result.overwritten == [pre_existing]
    # No "a (2).jpg" was created.
    assert not (dest / "Dia 1" / "a (2).jpg").exists()


# ── ORIGINAL file type ─────────────────────────────────────────


def test_run_process_export_original_is_byte_copy(tmp_path):
    """ORIGINAL skips the render pipeline → output bytes equal
    source bytes."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket = _make_bucket(src, ["a.jpg"])
    src_bytes = (src / "a.jpg").read_bytes()

    result = run_process_export(
        [bucket], dest, file_type=ExportFileType.ORIGINAL,
    )

    assert result.ok_count == 1
    out = dest / "Dia 1" / "a.jpg"
    assert out.exists()
    assert out.read_bytes() == src_bytes


# ── Progress callback ──────────────────────────────────────────


def test_progress_callback_called_per_kept_file(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket = _make_bucket(src, ["a.jpg", "b.jpg", "c.jpg"])
    seen: list[tuple[int, int, str]] = []

    def progress(done: int, total: int, name: str) -> bool:
        seen.append((done, total, name))
        return True

    run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG, progress=progress,
    )
    assert [s[:2] for s in seen] == [(1, 3), (2, 3), (3, 3)]


def test_progress_callback_can_cancel_mid_run(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket = _make_bucket(src, ["a.jpg", "b.jpg", "c.jpg"])
    calls: list[str] = []

    def progress(done: int, total: int, name: str) -> bool:
        calls.append(name)
        return done < 2                                  # stop after #1

    result = run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG, progress=progress,
    )
    # First file wrote (and reported); cancellation happens BEFORE the
    # second file's render — only one file lands on disk.
    assert result.ok_count == 1
    assert len(calls) == 2


# ── Error tolerance ────────────────────────────────────────────


def test_missing_source_recorded_as_skipped(tmp_path):
    """A path the journal marks Kept but isn't on disk is skipped, not
    raised."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir(parents=True, exist_ok=True)
    bucket = ProcessBucketInput(
        files=(src / "ghost.jpg",),
        journal={"marks": {"ghost.jpg": STATE_PICKED}},
        day_label="Dia 1",
        style_label="uncategorized",
    )
    result = run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
    )
    assert result.ok_count == 0
    assert len(result.skipped) == 1


# ── Per-photo crop override ────────────────────────────────────


def test_per_photo_crop_override_shrinks_output_size(tmp_path):
    """Per-photo crop_norm rect applies before encode → output JPEG's
    pixel dimensions are smaller than the source."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir(parents=True, exist_ok=True)
    # 200×200 source so the crop produces an obvious size difference.
    _write_jpeg(src / "big.jpg", size=(200, 200))
    bucket = ProcessBucketInput(
        files=(src / "big.jpg",),
        journal={"marks": {"big.jpg": STATE_PICKED}},
        day_label="Dia 1",
        style_label="uncategorized",
    )

    run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
        crop_by_filename={"big.jpg": (0.25, 0.25, 0.5, 0.5)},
        aspect_label="1:1",
    )

    out = dest / "Dia 1" / "big.jpg"
    with Image.open(out) as img:
        assert img.size == (100, 100)


def test_aspect_only_centred_default_when_no_per_photo_override(tmp_path):
    """With aspect_label set but no per-photo crop, the engine
    auto-computes the centred max-area rect → output dimensions
    match the chosen ratio."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir(parents=True, exist_ok=True)
    _write_jpeg(src / "big.jpg", size=(200, 200))    # square source
    bucket = ProcessBucketInput(
        files=(src / "big.jpg",),
        journal={"marks": {"big.jpg": STATE_PICKED}},
        day_label="Dia 1",
        style_label="uncategorized",
    )

    run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
        aspect_label="16:9",
    )

    out = dest / "Dia 1" / "big.jpg"
    with Image.open(out) as img:
        w, h = img.size
        # 16:9 on a square → top/bottom slabs cropped: h = w * 9/16.
        assert w == 200
        # Allow ±1 px for rounding.
        assert abs(h - int(round(200 * 9 / 16))) <= 1


def test_crop_angle_by_filename_blacks_some_corner_pixels(tmp_path):
    """Task #117 — per-photo ``crop_angle`` rotates with
    ``expand=False`` before the (no-op here) crop. The visible
    proof: a uniformly-red source comes out with at least one black
    pixel in a corner after a 10° tilt (rotated content moved away
    from that corner)."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir(parents=True, exist_ok=True)
    _write_jpeg(src / "tilt.jpg", size=(80, 60), color=(220, 30, 30))
    bucket = ProcessBucketInput(
        files=(src / "tilt.jpg",),
        journal={"marks": {"tilt.jpg": STATE_PICKED}},
        day_label="Dia 1",
        style_label="uncategorized",
    )

    # Box Rotation (docs/25 §4): a FULL-FRAME box rotated 10° about its
    # centre pulls outside-image (black) pixels into a corner.
    run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
        crop_by_filename={"tilt.jpg": (0.0, 0.0, 1.0, 1.0)},
        crop_angle_by_filename={"tilt.jpg": 10.0},
        auto_on=False,
    )

    out = dest / "Dia 1" / "tilt.jpg"
    with Image.open(out) as img:
        # Top-left corner: rotated box exposed outside-image → near-black
        # (allow some JPEG compression slop).
        r, g, b = img.convert("RGB").getpixel((0, 0))
        assert r < 40 and g < 40 and b < 40, (
            f"box rotation should black top-left corner; got {(r, g, b)}"
        )


def test_crop_angle_default_zero_leaves_pixels_unchanged(tmp_path):
    """Sanity counterpart — without the ``crop_angle_by_filename``
    entry, a uniformly-red source comes out red end-to-end (no tilt
    short-circuited in ``_apply_crop_tilt_np``)."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir(parents=True, exist_ok=True)
    _write_jpeg(src / "flat.jpg", size=(60, 40), color=(220, 30, 30))
    bucket = ProcessBucketInput(
        files=(src / "flat.jpg",),
        journal={"marks": {"flat.jpg": STATE_PICKED}},
        day_label="Dia 1",
        style_label="uncategorized",
    )

    run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG, auto_on=False,
    )

    out = dest / "Dia 1" / "flat.jpg"
    with Image.open(out) as img:
        r, g, b = img.convert("RGB").getpixel((0, 0))
        # Still red (JPEG compression slop tolerated).
        assert r > 180 and g < 80 and b < 80, (
            f"no-tilt path should keep red corner; got {(r, g, b)}"
        )


def test_original_aspect_keeps_source_dimensions(tmp_path):
    """aspect_label="Original" and no per-photo crop = output keeps
    source dimensions."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir(parents=True, exist_ok=True)
    _write_jpeg(src / "img.jpg", size=(123, 77))
    bucket = ProcessBucketInput(
        files=(src / "img.jpg",),
        journal={"marks": {"img.jpg": STATE_PICKED}},
        day_label="Dia 1",
        style_label="uncategorized",
    )

    run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
        aspect_label="Original",
    )

    out = dest / "Dia 1" / "img.jpg"
    with Image.open(out) as img:
        assert img.size == (123, 77)


# ── Scope model: gate_kept=False (docs/25 §9) ──────────────────


def test_gate_kept_false_exports_all_files_regardless_of_marks(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    # 'c.jpg' is DISCARDED in the journal, but with gate_kept=False the
    # Process scope picker already chose the files — everything exports.
    bucket = _make_bucket(src, ["a.jpg"], extra_names=["c.jpg"])

    result = run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG, gate_kept=False,
    )

    assert result.ok_count == 2
    assert (dest / "Dia 1" / "a.jpg").exists()
    assert (dest / "Dia 1" / "c.jpg").exists()      # exported despite DISCARDED


def test_gate_kept_true_default_still_filters_kept(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket = _make_bucket(src, ["a.jpg"], extra_names=["c.jpg"])

    result = run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,   # gate_kept defaults True
    )

    assert result.ok_count == 1
    assert (dest / "Dia 1" / "a.jpg").exists()
    assert not (dest / "Dia 1" / "c.jpg").exists()


# ── Rotation (docs/25 §4) ──────────────────────────────────────


def test_rotation_by_filename_swaps_output_dimensions(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    # A landscape source (200×100) rotated 90° → portrait (100×200).
    path = src / "r.jpg"
    _write_jpeg(path, size=(200, 100))
    bucket = ProcessBucketInput(
        files=(path,),
        journal={"marks": {"r.jpg": STATE_PICKED}},
        day_label="Dia 1",
    )

    run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
        auto_on=False, rotation_by_filename={"r.jpg": 90},
    )

    out = dest / "Dia 1" / "r.jpg"
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == (100, 200)                # W,H swapped


# ── Journal-driven per-photo edits (docs/25) ───────────────────


def test_export_applies_saved_crop_from_journal(tmp_path):
    """Each photo's saved edits live in the bucket journal; export must
    honour them even when NOT passed via the override dicts (the day/
    event-scope case where only the current photo is a live override)."""
    from core.process_decisions import set_process_crop
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket = _make_bucket(src, ["a.jpg", "b.jpg"])     # 100×100 each
    # Save a crop for b ONLY in the journal (not in crop_by_filename).
    set_process_crop(bucket.journal, "b.jpg", (0.25, 0.25, 0.5, 0.5))

    run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
        auto_on=False, gate_kept=False)

    with Image.open(dest / "Dia 1" / "a.jpg") as ia:
        a_w = ia.size[0]
    with Image.open(dest / "Dia 1" / "b.jpg") as ib:
        b_w = ib.size[0]
    assert b_w < a_w        # b was cropped from its journal entry


def test_export_applies_saved_params_from_journal(tmp_path):
    """A saved tone edit in the journal is applied on export even with
    auto_on=False and no params override."""
    from core.process_decisions import set_process_params
    from core.photo_render import Params
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    bucket = _make_bucket(src, ["a.jpg"])
    set_process_params(bucket.journal, "a.jpg", Params(exposure=2.0))

    run_process_export(
        [bucket], dest, file_type=ExportFileType.JPEG,
        auto_on=False, gate_kept=False)

    with Image.open(dest / "Dia 1" / "a.jpg") as im:
        arr = np.asarray(im)
    # +2 EV on a mid-grey 127 source → much brighter than the original.
    assert arr.mean() > 200
