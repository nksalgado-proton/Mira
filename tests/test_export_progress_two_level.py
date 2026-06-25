"""spec/139 — engine emits two levels of progress: aggregate
``progress(done, total, name)`` AND ``on_file_fraction(unit_id, frac)``.

Photos snap to ``1.0`` per file (writes are near-instant); videos
stream fractions through the clip encoder. The aggregate ``done``
only ticks forward on file completion — fraction moves continuously
within the current file.

Tested at the :func:`core.render_worker.run_manifest_inline` seam
(the engine-level fallback that runs the same manifest both lanes
go through, mocked clip render included so this test runs without
ffmpeg).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.cull_export import ExportFileType
from core.export_manifest import ExportManifest, PhotoUnit, ClipUnit
from core import render_worker


# ── Photos: aggregate ticks + fraction snaps to 1.0 per file ────────


def _png_unit(path: Path, unit_id: str, dest: Path) -> PhotoUnit:
    """A minimal PhotoUnit with the spec/139 contract:
    ``run_manifest_inline`` writes the file via ``_render_unit``
    (mocked here so the test never decodes a real image)."""
    return PhotoUnit(
        unit_id=unit_id,
        source=str(path),
        dest_dir=str(dest),
        file_type=ExportFileType.JPEG.value,
        jpeg_quality=90,
        look=None, auto_on=False, style=None,
        crop_norm=None, crop_angle=0.0, rotation=0,
        aspect_label="Original",
    )


def test_inline_engine_emits_fraction_for_each_photo(tmp_path):
    """Two photo units → two ``on_file_fraction`` ticks at 1.0, with
    the aggregate ``done`` advancing on each one."""
    sources = []
    for i in range(2):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xd9")
        sources.append(p)
    dest = tmp_path / "out"
    units = tuple(
        _png_unit(p, f"u{i}", dest) for i, p in enumerate(sources))
    manifest = ExportManifest(units=units, clips=())

    aggregate: list[tuple[int, int, str]] = []
    fractions: list[tuple[str, float]] = []

    def _agg(done, total, name):
        aggregate.append((done, total, name))
        return True

    def _frac(unit_id, fraction):
        fractions.append((unit_id, fraction))

    # Stub _render_unit so this test doesn't depend on a real image
    # decoder — the spec/139 wiring (fraction emit) is unrelated to
    # the photo writer's success/failure path.
    with patch.object(
            render_worker, "_render_unit",
            side_effect=[
                {"type": "unit", "unit_id": "u0", "status": "ok"},
                {"type": "unit", "unit_id": "u1", "status": "ok"},
            ]):
        render_worker.run_manifest_inline(
            manifest, progress=_agg, on_file_fraction=_frac)

    # Aggregate advanced once per file.
    assert [done for done, _t, _n in aggregate] == [1, 2]
    assert all(total == 2 for _d, total, _n in aggregate)
    # File fraction: 1.0 per file, in order, keyed by unit_id.
    assert fractions == [("u0", 1.0), ("u1", 1.0)], (
        f"spec/139 §2: photos must snap to 1.0 per file; got {fractions}"
    )


def test_inline_engine_streams_video_fractions_then_snaps(tmp_path):
    """A mocked clip render emits intermediate fractions
    (0.25 → 0.5 → 1.0) — the engine surfaces every tick via
    ``on_file_fraction``. The aggregate advances only on the unit's
    completion."""
    dest = tmp_path / "out"
    src = tmp_path / "v.mp4"
    src.write_bytes(b"\x00" * 16)
    clip = ClipUnit(
        unit_id="clip-1",
        source=str(src),
        dest_dir=str(dest),
        base_name="clip",
        plan={"in_ms": 0, "out_ms": 1000, "src_fps": 30.0,
              "speed": 1.0, "params": {}},
        style=None,
    )
    manifest = ExportManifest(units=(), clips=(clip,))

    aggregate: list[tuple[int, int, str]] = []
    fractions: list[tuple[str, float]] = []

    def _agg(done, total, name):
        aggregate.append((done, total, name))
        return True

    def _frac(unit_id, fraction):
        fractions.append((unit_id, fraction))

    def _fake_clip(clip, _collision, _reserver, *, on_file_fraction=None):
        # Emulate the spec/139 §2 inner-loop emits before returning
        # the per-unit completion message.
        assert on_file_fraction is not None, (
            "spec/139 §2: ``_render_clip_unit`` must accept "
            "``on_file_fraction`` so the engine can surface the "
            "frame-progress ticks the encoder already computes"
        )
        on_file_fraction(clip.unit_id, 0.25)
        on_file_fraction(clip.unit_id, 0.5)
        on_file_fraction(clip.unit_id, 1.0)
        return {"type": "unit", "unit_id": clip.unit_id, "status": "ok"}

    with patch.object(render_worker, "_render_clip_unit", _fake_clip):
        render_worker.run_manifest_inline(
            manifest, progress=_agg, on_file_fraction=_frac)

    # Aggregate ticks once for the one clip (done=1, total=1).
    assert aggregate == [(1, 1, "v.mp4")]
    # Fractions advance through the encode (no snap-to-1.0 from the
    # engine here — the encoder reported 1.0 as its final tick).
    assert fractions == [
        ("clip-1", 0.25), ("clip-1", 0.5), ("clip-1", 1.0),
    ], (
        f"spec/139 §2: clip fractions must stream through; got "
        f"{fractions}"
    )


def test_aggregate_ticks_once_per_file_regardless_of_fraction_emits(
    tmp_path,
):
    """The aggregate ``progress(done, total, name)`` callback fires
    ONCE per file (the spec/139 "N of M" semantic). Many
    ``on_file_fraction`` emits within a clip's encode MUST NOT
    bump the aggregate count — the two streams are independent."""
    dest = tmp_path / "out"
    src = tmp_path / "v.mp4"
    src.write_bytes(b"\x00" * 16)
    clip = ClipUnit(
        unit_id="c", source=str(src), dest_dir=str(dest),
        base_name="c",
        plan={"in_ms": 0, "out_ms": 1000, "src_fps": 30.0,
              "speed": 1.0, "params": {}},
        style=None,
    )
    manifest = ExportManifest(units=(), clips=(clip,))

    aggregate_calls: list[tuple[int, int, str]] = []
    fractions: list[float] = []

    def _agg(done, total, name):
        aggregate_calls.append((done, total, name))
        return True

    def _frac(_uid, frac):
        fractions.append(frac)

    def _fake_clip(clip, *_a, on_file_fraction=None, **_kw):
        # 30 frame-progress emits — the kind of stream a long encode
        # would produce. The aggregate count MUST stay at 1 throughout.
        for i in range(1, 31):
            on_file_fraction(clip.unit_id, i / 30)
        return {"type": "unit", "unit_id": clip.unit_id, "status": "ok"}

    with patch.object(render_worker, "_render_clip_unit", _fake_clip):
        render_worker.run_manifest_inline(
            manifest, progress=_agg, on_file_fraction=_frac)

    # Aggregate callback fired exactly once for the single clip
    # — done=1, total=1 — regardless of the 30 fraction emits.
    assert aggregate_calls == [(1, 1, "v.mp4")], (
        f"spec/139 §2: aggregate must tick once per file (not per "
        f"fraction emit); got {aggregate_calls}"
    )
    # And we got every fraction emit through.
    assert len(fractions) == 30
    assert fractions[-1] == pytest.approx(1.0)
