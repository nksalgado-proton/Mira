"""The spec/60 §4 encoder ladder — probe order + fallback all the way
to libx264. ``core.proc.run`` is mocked at the module surface so the
probes never actually shell ffmpeg out."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import encoder_ladder


def _ok(_args, **_kw):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _fail(_args, **_kw):
    return SimpleNamespace(returncode=1, stdout="", stderr="not supported")


def _selective(allow: set[str]):
    def _go(args, **_kw):
        codec = args[args.index("-c:v") + 1]
        return _ok(args) if codec in allow else _fail(args)
    return _go


@pytest.fixture(autouse=True)
def _reset():
    encoder_ladder._reset_cache_for_tests()
    yield
    encoder_ladder._reset_cache_for_tests()


def test_nvenc_wins_when_present(monkeypatch):
    monkeypatch.setattr(encoder_ladder, "_run_hidden", _ok)
    info = encoder_ladder.detect_encoder()
    assert info["name"] == "nvenc"
    assert "h264_nvenc" in info["args"]


def test_falls_through_to_qsv(monkeypatch):
    monkeypatch.setattr(encoder_ladder, "_run_hidden",
                        _selective({"h264_qsv", "h264_amf"}))
    info = encoder_ladder.detect_encoder()
    assert info["name"] == "qsv"
    assert "h264_qsv" in info["args"]


def test_falls_through_to_amf(monkeypatch):
    monkeypatch.setattr(encoder_ladder, "_run_hidden",
                        _selective({"h264_amf"}))
    info = encoder_ladder.detect_encoder()
    assert info["name"] == "amf"
    assert "h264_amf" in info["args"]


def test_libx264_floor_when_no_hardware(monkeypatch):
    monkeypatch.setattr(encoder_ladder, "_run_hidden", _fail)
    info = encoder_ladder.detect_encoder()
    assert info["name"] == "libx264"
    assert "libx264" in info["args"]
    # Floor encoder must always carry a yuv420p pin so every player
    # can play the result.
    assert "yuv420p" in info["args"]


def test_cache_probes_once(monkeypatch):
    calls = []

    def _spy(args, **_kw):
        calls.append(args)
        return _ok(args)

    monkeypatch.setattr(encoder_ladder, "_run_hidden", _spy)
    a = encoder_ladder.detect_encoder_args()
    b = encoder_ladder.detect_encoder_args()
    assert a == b
    # Three or four probes happen up to the first hit; the second call
    # adds zero. (NVENC wins on the first probe — len(calls) == 1.)
    n = len(calls)
    encoder_ladder.detect_encoder_args()
    assert len(calls) == n
