"""Tests for core.cull_export — the Stage-C export engine.

Pure / filesystem. Covers the courtesy-prefix rule, per-file
collision (Override vs Unique), atomic copy (no half-file, source
untouched), missing-source skip, and collision detection.
"""

from __future__ import annotations

from datetime import datetime

from core.cull_export import (
    CollisionPolicy,
    ExportItem,
    courtesy_filename,
    detect_collisions,
    export_items,
)


def _src(tmp_path, name, data=b"original-bytes"):
    p = tmp_path / "src"
    p.mkdir(exist_ok=True)
    f = p / name
    f.write_bytes(data)
    return f


def test_courtesy_filename():
    dt = datetime(2025, 10, 26, 7, 8, 9)
    assert courtesy_filename("P1010001.RW2", dt) == \
        "20251026_070809_P1010001.RW2"
    # No timestamp → unchanged (derived/no-EXIF artifact).
    assert courtesy_filename("frame.jpg", None) == "frame.jpg"
    # Idempotent — not double-prefixed.
    once = courtesy_filename("a.jpg", dt)
    assert courtesy_filename(once, dt) == once


def test_export_writes_into_day_style(tmp_path):
    s = _src(tmp_path, "a.jpg")
    dest = tmp_path / "02 Selected" / "Dia 1 - Kathmandu" / "wildlife"
    res = export_items(
        [ExportItem(s, dest, "a.jpg")],
        collision=CollisionPolicy.OVERRIDE,
    )
    out = dest / "a.jpg"
    assert out.is_file() and out.read_bytes() == b"original-bytes"
    assert res.written == [out] and res.ok_count == 1
    assert s.is_file()                       # source untouched


def test_collision_override_replaces(tmp_path):
    s = _src(tmp_path, "a.jpg", b"NEW")
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "a.jpg").write_bytes(b"OLD")
    res = export_items(
        [ExportItem(s, dest, "a.jpg")],
        collision=CollisionPolicy.OVERRIDE,
    )
    assert (dest / "a.jpg").read_bytes() == b"NEW"
    assert res.overwritten == [dest / "a.jpg"]
    assert res.written == []


def test_collision_unique_keeps_existing(tmp_path):
    s = _src(tmp_path, "a.jpg", b"NEW")
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "a.jpg").write_bytes(b"OLD")
    (dest / "a (2).jpg").write_bytes(b"OLD2")     # force " (3)"
    res = export_items(
        [ExportItem(s, dest, "a.jpg")],
        collision=CollisionPolicy.UNIQUE,
    )
    assert (dest / "a.jpg").read_bytes() == b"OLD"      # untouched
    assert (dest / "a (2).jpg").read_bytes() == b"OLD2"  # untouched
    assert (dest / "a (3).jpg").read_bytes() == b"NEW"   # written
    assert res.renamed == [(s, dest / "a (3).jpg")]


def test_missing_source_skipped_not_fatal(tmp_path):
    good = _src(tmp_path, "good.jpg")
    missing = tmp_path / "src" / "nope.jpg"
    dest = tmp_path / "d"
    res = export_items(
        [
            ExportItem(missing, dest, "nope.jpg"),
            ExportItem(good, dest, "good.jpg"),
        ],
        collision=CollisionPolicy.OVERRIDE,
    )
    # One bad item must not abort the rest.
    assert (dest / "good.jpg").is_file()
    assert res.written == [dest / "good.jpg"]
    assert len(res.skipped) == 1 and res.skipped[0][0] == missing


def test_no_half_file_temp_cleaned(tmp_path):
    s = _src(tmp_path, "a.jpg")
    dest = tmp_path / "d"
    export_items(
        [ExportItem(s, dest, "a.jpg")],
        collision=CollisionPolicy.OVERRIDE,
    )
    # No leftover .part- temp files in the destination.
    assert sorted(p.name for p in dest.iterdir()) == ["a.jpg"]


def test_detect_collisions(tmp_path):
    s = _src(tmp_path, "a.jpg")
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "a.jpg").write_bytes(b"x")
    items = [
        ExportItem(s, dest, "a.jpg"),         # collides
        ExportItem(s, dest, "b.jpg"),         # free
    ]
    coll = detect_collisions(items)
    assert [c.dest_name for c in coll] == ["a.jpg"]


# ── Model 3: corrected EXIF baked into the COPY (docs/18 §"Model 3") ──


def test_export_bakes_exif_only_for_items_with_datetime(
    tmp_path, monkeypatch,
):
    """A keeper whose camera clock was off carries exif_datetime →
    its COPY's DateTimeOriginal is batch-rewritten (source never
    touched); a pass-through keeper (None) is never retimed."""
    import core.exif_rewriter as exr

    ops_seen: list = []

    def _fake_batch(operations, *, preserve_original=True):
        ops_seen.extend(operations)
        return [exr.RewriteOutcome(path=p, new_time=t)
                for p, t in operations]

    monkeypatch.setattr(exr, "rewrite_capture_times_batch", _fake_batch)

    fixed = _src(tmp_path, "wrong_tz.rw2")
    passthru = _src(tmp_path, "phone.jpg")
    dest = tmp_path / "01 - Culled" / "Dia 1 - Kathmandu" / "wildlife"
    corrected = datetime(2025, 10, 26, 7, 8, 9)
    res = export_items(
        [
            ExportItem(fixed, dest, "wrong_tz.rw2",
                       exif_datetime=corrected),
            ExportItem(passthru, dest, "phone.jpg"),   # None
        ],
        collision=CollisionPolicy.UNIQUE,
    )
    assert res.ok_count == 2
    # Only the off-clock copy is retimed, to the corrected value.
    assert ops_seen == [(dest / "wrong_tz.rw2", corrected)]
    assert res.retimed == [dest / "wrong_tz.rw2"]
    # Source bytes untouched (the engine never rewrites the source).
    assert fixed.read_bytes() == b"original-bytes"


def test_export_retime_failure_keeps_file_records_error(
    tmp_path, monkeypatch,
):
    """A retime failure must NOT lose the exported file — the copy
    stays (camera time), the failure is an error, not a skip."""
    import core.exif_rewriter as exr

    monkeypatch.setattr(
        exr, "rewrite_capture_times_batch",
        lambda operations, *, preserve_original=True: [
            exr.RewriteOutcome(path=p, error="exiftool boom")
            for p, _t in operations
        ],
    )
    s = _src(tmp_path, "a.rw2")
    dest = tmp_path / "01 - Culled" / "Dia 1" / "general"
    res = export_items(
        [ExportItem(s, dest, "a.rw2",
                    exif_datetime=datetime(2025, 1, 1, 0, 0, 0))],
        collision=CollisionPolicy.UNIQUE,
    )
    assert (dest / "a.rw2").is_file()        # file kept
    assert res.retimed == []
    assert res.errors and "retime failed" in res.errors[0][1]


def test_export_no_datetime_never_calls_rewriter(tmp_path, monkeypatch):
    import core.exif_rewriter as exr
    called = []
    monkeypatch.setattr(
        exr, "rewrite_capture_times_batch",
        lambda *a, **k: called.append(1) or [])
    s = _src(tmp_path, "a.jpg")
    dest = tmp_path / "01 - Culled" / "Dia 1" / "general"
    export_items([ExportItem(s, dest, "a.jpg")],
                 collision=CollisionPolicy.UNIQUE)
    assert called == []                      # no bake path at all
