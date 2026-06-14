"""spec/54 + Nelson Look Strength slider — Commit 3 render integration:

- core.process_export_engine._render_one threads strength from the
  Look CHOICE into compute_look_params, so engine pixels match the
  AdjustmentSurface preview by construction.
- core.process_decisions.get_process_look surfaces strength to the
  engine through the journal seam; legacy entries (no strength key)
  default to 1.0.
- The spec/60 manifest carries strength on the wire (PhotoUnit.look
  dict), so a batched job from the worker process produces a
  visually different output for strength != 1.0 than for the
  default — proving the field really threads through the worker.

The preview-vs-export pin uses the same numpy math both sides run,
so the assertion is a direct numerical equality (not a tolerance).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.export_manifest import ExportManifest, PhotoUnit
from core.photo_render import Params, apply_params
from core.photo_decoder import decode_image
from core.photo_auto import compute_auto_params, look_params_from_natural
from core.process_export_engine import _render_one
from core.process_decisions import get_process_look


# ── engine seam: _render_one reads look["strength"] ─────────────────


def _make_jpeg(path: Path) -> Path:
    """A noisy-ish 64×48 source: AUTO/Look math computes something
    non-degenerate on it."""
    rng = np.random.default_rng(1)
    arr = rng.integers(40, 215, (48, 64, 3), dtype=np.uint8)
    Image.fromarray(arr).save(str(path), "JPEG", quality=95)
    return path


def test_render_one_threads_strength_into_look_compile(tmp_path):
    """At strength=0.5 the engine produces exactly the same pixels
    as compute_auto_params(...).scaled(0.5) does in-process. That's
    the preview/export parity guarantee Nelson asked for."""
    src = _make_jpeg(tmp_path / "noisy.jpg")
    # Engine path with strength=0.5 on Natural.
    rendered, used = _render_one(
        src,
        auto_on=False, cached_params=None,
        look_choice={"look": "natural", "style": None, "strength": 0.5},
        crop_norm=None, crop_angle=0.0, rotation=0,
        aspect_label="Original", style=None,
    )
    # Hand-computed expectation: decode, AUTO, .scaled(0.5), apply.
    img = decode_image(src)
    expected_params = compute_auto_params(img).scaled(0.5)
    expected = apply_params(img, expected_params)
    assert np.array_equal(rendered, expected)
    assert used == expected_params


def test_render_one_strength_default_is_one(tmp_path):
    """No strength key in look_choice → engine treats it as 1.0
    (legacy semantics, by-construction migration safety)."""
    src = _make_jpeg(tmp_path / "noisy.jpg")
    rendered_default, _ = _render_one(
        src,
        auto_on=False, cached_params=None,
        look_choice={"look": "natural", "style": None},
        crop_norm=None, crop_angle=0.0, rotation=0,
        aspect_label="Original", style=None,
    )
    rendered_one, _ = _render_one(
        src,
        auto_on=False, cached_params=None,
        look_choice={"look": "natural", "style": None, "strength": 1.0},
        crop_norm=None, crop_angle=0.0, rotation=0,
        aspect_label="Original", style=None,
    )
    assert np.array_equal(rendered_default, rendered_one)


def test_render_one_strength_zero_returns_decoded_pixels(tmp_path):
    """At strength=0.0 the Look engine returns identity Params, so
    the engine applies no tone change — output = decoded source."""
    src = _make_jpeg(tmp_path / "noisy.jpg")
    rendered, used = _render_one(
        src,
        auto_on=False, cached_params=None,
        look_choice={"look": "natural", "style": None, "strength": 0.0},
        crop_norm=None, crop_angle=0.0, rotation=0,
        aspect_label="Original", style=None,
    )
    assert used.is_identity is True
    assert np.array_equal(rendered, decode_image(src))


def test_render_one_strength_doubles_effect(tmp_path):
    """strength=2.0 doubles every tone field — the engine produces
    different pixels than strength=1.0 (the "louder" Look)."""
    src = _make_jpeg(tmp_path / "noisy.jpg")
    full, used_full = _render_one(
        src,
        auto_on=False, cached_params=None,
        look_choice={"look": "natural", "style": None, "strength": 1.0},
        crop_norm=None, crop_angle=0.0, rotation=0,
        aspect_label="Original", style=None,
    )
    boost, used_boost = _render_one(
        src,
        auto_on=False, cached_params=None,
        look_choice={"look": "natural", "style": None, "strength": 2.0},
        crop_norm=None, crop_angle=0.0, rotation=0,
        aspect_label="Original", style=None,
    )
    for f in used_full.__dataclass_fields__:
        if abs(getattr(used_full, f)) > 1e-6:
            assert abs(getattr(used_boost, f) - 2 * getattr(used_full, f)) < 1e-4
    assert not np.array_equal(full, boost)


# ── journal seam: get_process_look surfaces strength ────────────────


def test_get_process_look_extracts_strength_from_journal():
    journal = {"process_decisions": {
        "a.jpg": {"look": "natural", "strength": 1.5}}}
    choice = get_process_look(journal, "a.jpg")
    assert choice == {
        "look": "natural", "style": None, "creative_filter": None,
        "strength": 1.5}


def test_get_process_look_legacy_entries_default_strength_one():
    """A journal entry written before the slider landed has no
    strength key — the helper must default to 1.0 so existing
    legacy exports render identically."""
    journal = {"process_decisions": {
        "a.jpg": {"look": "natural"}}}
    choice = get_process_look(journal, "a.jpg")
    assert choice["strength"] == 1.0


def test_get_process_look_clamps_wild_strength():
    """A hand-edited journal with a wild strength value clamps to
    [0, 2] at the read seam — never crashes the engine."""
    journal_high = {"process_decisions": {"a": {"look": "natural", "strength": 9.9}}}
    journal_neg = {"process_decisions": {"a": {"look": "natural", "strength": -1.0}}}
    journal_bad = {"process_decisions": {"a": {"look": "natural", "strength": "not a number"}}}
    assert get_process_look(journal_high, "a")["strength"] == 2.0
    assert get_process_look(journal_neg, "a")["strength"] == 0.0
    assert get_process_look(journal_bad, "a")["strength"] == 1.0


# ── spec/60 manifest wire carries strength ──────────────────────────


def test_manifest_carries_strength_through_round_trip():
    """A PhotoUnit's look dict round-trips through the JSON wire —
    the worker reads the same shape the app writes."""
    unit = PhotoUnit(
        unit_id="x", source="/s.jpg", dest_dir="/d",
        look={"look": "natural", "strength": 0.7})
    m = ExportManifest(units=(unit,))
    loaded = ExportManifest.from_json(m.to_json())
    assert loaded.units[0].look == {"look": "natural", "strength": 0.7}


def test_worker_inline_renders_with_strength(tmp_path):
    """The inline runner (the §4 fallback the worker shares) reads
    the look dict's strength and produces different bytes from the
    default — proves the wire field actually drives the render."""
    src = _make_jpeg(tmp_path / "noisy.jpg")
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    from core.render_worker import run_manifest_inline

    base_unit = dict(
        source=str(src), auto_on=False, jpeg_quality=95,
    )
    m_one = ExportManifest(units=(PhotoUnit(
        unit_id="u", dest_dir=str(out_a),
        look={"look": "natural", "strength": 1.0}, **base_unit),))
    m_half = ExportManifest(units=(PhotoUnit(
        unit_id="u", dest_dir=str(out_b),
        look={"look": "natural", "strength": 0.0}, **base_unit),))

    msgs_one = run_manifest_inline(m_one)
    msgs_half = run_manifest_inline(m_half)
    assert all(m_["status"] == "ok" for m_ in msgs_one + msgs_half)

    # At strength=0.0 the file is the source pixels through a JPEG
    # re-encode; at strength=1.0 the Look bakes in.
    arr_full = np.asarray(Image.open(msgs_one[0]["final_path"]))
    arr_zero = np.asarray(Image.open(msgs_half[0]["final_path"]))
    assert not np.array_equal(arr_full, arr_zero), (
        "strength field did not thread to the worker render path")
    # The strength=0.0 output is closer to the decoded source than
    # the strength=1.0 output (Natural lifts shadows etc.). A
    # weaker pin but enough to confirm direction.
    src_arr = decode_image(src)
    d_zero = np.mean(np.abs(
        arr_zero.astype(int) - src_arr.astype(int)))
    d_full = np.mean(np.abs(
        arr_full.astype(int) - src_arr.astype(int)))
    assert d_zero < d_full
