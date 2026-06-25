"""spec/144 — clip members carry the segment's TRUE on-disk duration.

The pre-spec/144 ``files_from_lineage`` assigned
``SessionFile.duration_ms = src.duration_ms`` for video lineage rows —
i.e. the source video's WHOLE duration, not the marker-partition
**segment**. Every downstream consumer was wrong: the Cut budget
undercounted (25 min for a 1 h+ render), the cut-play scrubber lagged,
and the PTE generator wrote ``Duration=0``.

Two truth sources land on ``SessionFile.duration_ms`` now, in this order:

1. ``lineage.duration_ms`` — populated at render time by the worker
   (``(out_ms - in_ms) / speed``); the segment's exact on-disk length.
2. ``probe_video`` (ffmpeg) of the exported file — the fallback for
   legacy pre-migration lineage rows whose ``duration_ms`` is ``None``.

These tests pin both paths AND the no-fallback case (file missing,
no recorded duration), which reads as ``0`` so the cut-play scrubber
falls back to ``photo_ms`` and the advance still rides
``EndOfMedia``.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import files_from_lineage, session_files
from mira.store import models as m
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now


# --------------------------------------------------------------------- #
# Fixture
# --------------------------------------------------------------------- #


@pytest.fixture
def gw(tmp_path):
    """A real event.db gateway over :func:`_doc` plus an on-disk
    ``Exported Media/v1.mp4`` so the ffprobe fallback can resolve a
    file (the probe itself may or may not return a meaningful value
    depending on the test's bytes; tests that need a real probe write
    real video bytes)."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# --------------------------------------------------------------------- #
# 1. Persisted lineage duration wins
# --------------------------------------------------------------------- #


def test_files_from_lineage_uses_lineage_duration_for_video(gw):
    """spec/144 §A — when ``lineage.duration_ms`` is set, the session
    cell carries that value verbatim. The :func:`_doc` fixture pins
    the v1.mp4 lineage row to 30_000 ms — that lands on the session
    cell."""
    files = session_files(gw, [["+", "exported"]])
    by_rel = {f.export_relpath: f for f in files}
    v = by_rel["Exported Media/v1.mp4"]
    assert v.kind == "video"
    assert v.duration_ms == 30_000


def test_short_segment_overrides_long_source_duration(gw):
    """spec/144 — the regression: a clip is a SEGMENT of a source
    video. Even when ``item.duration_ms`` is huge (the whole source
    is 30 s), the lineage row's segment ``duration_ms`` MUST win.

    Simulate a clip-segment lineage row by overwriting v1's lineage
    ``duration_ms`` to 5_000 (a 5-second sub-segment); the cell
    surfaces 5_000, not 30_000."""
    gw.store.conn.execute(
        "UPDATE lineage SET duration_ms = ? "
        "WHERE export_relpath = ?",
        (5_000, "Exported Media/v1.mp4"))
    files = session_files(gw, [["+", "exported"]])
    by_rel = {f.export_relpath: f for f in files}
    v = by_rel["Exported Media/v1.mp4"]
    assert v.duration_ms == 5_000, (
        "the SEGMENT's recorded length must override the source "
        "video's whole duration — pre-fix this returned 30_000")


# --------------------------------------------------------------------- #
# 2. Probe fallback when lineage row predates the migration
# --------------------------------------------------------------------- #


def test_probe_fallback_when_lineage_duration_is_null(gw, monkeypatch):
    """spec/144 §A — legacy lineage rows have ``duration_ms = NULL``.
    For those, ``files_from_lineage`` ffprobes the exported file so
    the session cell carries the true on-disk length.

    Uses a stub ``probe_video`` so the test doesn't depend on a real
    encoder. The file's bytes don't matter — only the presence test."""
    # Drop the persisted duration to force the fallback path.
    gw.store.conn.execute(
        "UPDATE lineage SET duration_ms = NULL "
        "WHERE export_relpath = ?",
        ("Exported Media/v1.mp4",))
    # Create a stand-in file so the fallback's ``is_file`` check passes.
    video_path = Path(gw.event_root) / "Exported Media" / "v1.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"x" * 16)

    probe_calls: list = []

    def _fake_probe(path):
        probe_calls.append(Path(path))
        # Within tolerance of the spec — fixture caller pins ~5_200 ms.
        return type("M", (), {"duration_ms": 5_200})()

    from mira.shared import cut_session as _cs
    monkeypatch.setattr(
        "core.video_extract.probe_video", _fake_probe)
    # Drop any module-level cache (none today; defensive).
    files = session_files(gw, [["+", "exported"]])
    by_rel = {f.export_relpath: f for f in files}
    v = by_rel["Exported Media/v1.mp4"]
    assert v.duration_ms == 5_200, (
        "spec/144 — when the lineage row's duration_ms is NULL the "
        "probe MUST resolve the segment's true length")
    assert probe_calls == [video_path], (
        "the probe must run against the exported file (the segment), "
        "not against the source video item's origin_relpath")


def test_probe_failure_yields_zero(gw, monkeypatch):
    """A probe that raises must NOT propagate; the cell carries 0,
    which the cut-play scrubber reads as "use ``photo_ms``" so the
    show still advances on ``EndOfMedia``."""
    gw.store.conn.execute(
        "UPDATE lineage SET duration_ms = NULL "
        "WHERE export_relpath = ?",
        ("Exported Media/v1.mp4",))
    video_path = Path(gw.event_root) / "Exported Media" / "v1.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"\x00")                    # not a real video

    def _boom(_path):
        raise RuntimeError("codec error")

    monkeypatch.setattr(
        "core.video_extract.probe_video", _boom)
    files = session_files(gw, [["+", "exported"]])
    by_rel = {f.export_relpath: f for f in files}
    v = by_rel["Exported Media/v1.mp4"]
    assert v.duration_ms == 0


def test_missing_file_yields_zero(gw):
    """When the lineage row carries no duration AND the on-disk file
    is gone (the user moved/deleted the export), the cell returns 0
    instead of falling back to the source item's whole-video length."""
    gw.store.conn.execute(
        "UPDATE lineage SET duration_ms = NULL "
        "WHERE export_relpath = ?",
        ("Exported Media/v1.mp4",))
    # No on-disk file: ``_clip_segment_duration_ms`` short-circuits to 0.
    files = session_files(gw, [["+", "exported"]])
    by_rel = {f.export_relpath: f for f in files}
    v = by_rel["Exported Media/v1.mp4"]
    assert v.duration_ms == 0, (
        "spec/144 — the spec REJECTS falling back to "
        "``src.duration_ms`` when neither the lineage nor the probe "
        "can resolve a value. 0 honestly says 'unknown'.")


# --------------------------------------------------------------------- #
# 3. Photos stay untouched
# --------------------------------------------------------------------- #


def test_photo_duration_ms_unchanged_at_zero(gw):
    """Photos read 0 ms by construction — the duration field is a
    video concept. spec/144 must not alter that."""
    files = session_files(gw, [["+", "exported"]])
    photos = [f for f in files if f.kind == "photo"]
    assert photos
    assert all(p.duration_ms == 0 for p in photos)
