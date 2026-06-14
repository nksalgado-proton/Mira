"""Tests for ``mira.ingest.backfill``'s sibling —
``mira.ingest.classify_pass`` (spec/58 slices 1–2).

The classifier + ExifTool stay out: the three ``*_fn`` hooks inject
fakes. What's pinned: candidate selection (the §3 stability guards),
RAW-first stem inheritance ("Use the raw"), the rules-version re-open,
confidence persistence, and the v1→v2 migration.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from mira.gateway.event_gateway import EventGateway
from mira.ingest.classify_pass import classify_event_items
from mira.store import models as m
from mira.store.repo import EventStore
from mira.store.schema import _migrate_v1_to_v2

NOW = "2026-06-10T22:00:00+00:00"
VER = "7.abc1234"


def _make_eg(tmp_path, items) -> EventGateway:
    store = EventStore.create(tmp_path / "event.db", event_id="evt-cp")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-cp", name="CP", created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="G9", is_phone=False))
    store.upsert(m.Camera(camera_id="iP15", is_phone=True))
    store.upsert(m.TripDay(day_number=3, date="2026-04-03"))
    for it in items:
        src = tmp_path / it.origin_relpath
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"x")
        store.upsert(it)
    return EventGateway(store, event_root=tmp_path, now=lambda: NOW)


def _item(iid, rel, *, kind="photo", camera="G9", day=3,
          classification=None, source=None, rules_version=None):
    return m.Item(
        id=iid, kind=kind, created_at=NOW, provenance="captured",
        origin_relpath=rel, sha256=f"s{iid}", byte_size=1,
        materialized_at=NOW, materialized_phase="ingest",
        camera_id=camera, day_number=day,
        capture_time_raw="2026-04-03T08:00:00",
        capture_time_corrected="2026-04-03T08:00:00",
        classification=classification, classification_source=source,
        classification_rules_version=rules_version,
        duration_ms=5000 if kind == "video" else None,
    )


def _fakes(value="macro", confidence=0.85, calls=None):
    def fake_exif_batch(paths):
        return [SimpleNamespace(path=p, raw={"Model": "X"}) for p in paths]

    def fake_classify(path, raw, *, source=None):
        if calls is not None:
            calls.append((Path(path).name, source))
        return SimpleNamespace(
            scenario=SimpleNamespace(value=value),
            confidence=confidence,
            needs_review=confidence < 0.60,
        )

    return dict(
        exif_batch_fn=fake_exif_batch,
        classify_fn=fake_classify,
        rules_version_fn=lambda source: VER,
    )


def test_raw_jpeg_pair_classifies_once_jpeg_inherits(tmp_path):
    calls = []
    eg = _make_eg(tmp_path, [
        _item("r1", "Original Media/_cameras/d3/G9/p1.rw2"),
        _item("j1", "Original Media/_cameras/d3/G9/p1.jpg"),
    ])
    try:
        rep = classify_event_items(eg, tmp_path, **_fakes(calls=calls))
        assert rep.classified == 1 and rep.inherited == 1
        assert len(calls) == 1 and calls[0][0] == "p1.rw2"   # the RAW
        for iid in ("r1", "j1"):
            it = eg.item(iid)
            assert it.classification == "macro"
            assert it.classification_source == "auto"
            assert it.classification_rules_version == VER
            assert it.classification_confidence == 0.85
            assert it.classification_needs_review == 0
    finally:
        eg.close()


def test_video_classifies_itself_even_sharing_a_stem(tmp_path):
    calls = []
    eg = _make_eg(tmp_path, [
        _item("r1", "Original Media/_cameras/d3/G9/p1.rw2"),
        _item("v1", "Original Media/_cameras/d3/G9/p1.mp4", kind="video"),
    ])
    try:
        rep = classify_event_items(eg, tmp_path, **_fakes(calls=calls))
        assert rep.classified == 2 and rep.inherited == 0
        assert {c[0] for c in calls} == {"p1.rw2", "p1.mp4"}
        assert eg.item("v1").classification == "macro"
    finally:
        eg.close()


def test_stability_guards_user_frozen_current(tmp_path):
    eg = _make_eg(tmp_path, [
        _item("u1", "Original Media/_cameras/d3/G9/a.jpg",
              classification="portrait", source="user"),
        _item("f1", "Original Media/_cameras/d3/G9/b.jpg"),
        _item("c1", "Original Media/_cameras/d3/G9/c.jpg",
              classification="landscape", source="auto", rules_version=VER),
    ])
    try:
        # f1 is FROZEN by Edit work (an adjustment row) — even though it
        # was never classified, writing one now would change its render
        # routing after the user already worked on it (spec/58 §3).
        eg.save_adjustment(m.Adjustment(item_id="f1", look="brighter"))
        rep = classify_event_items(eg, tmp_path, **_fakes())
        assert rep.wrote == 0
        assert rep.skipped_user == 1
        assert rep.skipped_frozen == 1
        assert rep.skipped_current == 1
        assert eg.item("u1").classification == "portrait"    # untouched
        assert eg.item("f1").classification is None          # still frozen
        assert eg.item("c1").classification == "landscape"
    finally:
        eg.close()


def test_rules_version_change_reopens_untouched_auto(tmp_path):
    eg = _make_eg(tmp_path, [
        _item("a1", "Original Media/_cameras/d3/G9/a.jpg",
              classification="landscape", source="auto",
              rules_version="6.old"),
    ])
    try:
        rep = classify_event_items(eg, tmp_path, **_fakes(value="wildlife"))
        assert rep.classified == 1
        it = eg.item("a1")
        assert it.classification == "wildlife"
        assert it.classification_rules_version == VER
    finally:
        eg.close()


def test_video_with_developed_segment_is_frozen(tmp_path):
    eg = _make_eg(tmp_path, [
        _item("v1", "Original Media/_cameras/d3/G9/clip.mp4", kind="video"),
    ])
    try:
        eg.ensure_video_segments("v1")
        seg = eg.segment_items("v1")[0]
        eg.save_video_adjustment(m.VideoAdjustment(item_id=seg.id, speed=2.0))
        rep = classify_event_items(eg, tmp_path, **_fakes())
        assert rep.skipped_frozen == 1 and rep.wrote == 0
        assert eg.item("v1").classification is None
    finally:
        eg.close()


def test_missing_file_counted_others_proceed(tmp_path):
    eg = _make_eg(tmp_path, [
        _item("a1", "Original Media/_cameras/d3/G9/a.jpg"),
        _item("b1", "Original Media/_cameras/d3/G9/b.jpg"),
    ])
    try:
        (tmp_path / "Original Media/_cameras/d3/G9/a.jpg").unlink()
        rep = classify_event_items(eg, tmp_path, **_fakes())
        assert rep.missing == 1 and rep.classified == 1
        assert eg.item("b1").classification == "macro"
        assert eg.item("a1").classification is None
    finally:
        eg.close()


def test_phone_camera_routes_phone_source(tmp_path):
    calls = []
    eg = _make_eg(tmp_path, [
        _item("p1", "Original Media/_phones/d3/iP15/x.heic", camera="iP15"),
    ])
    try:
        classify_event_items(eg, tmp_path, **_fakes(calls=calls))
        assert calls == [("x.heic", "phone")]
    finally:
        eg.close()


def test_low_confidence_sets_needs_review(tmp_path):
    eg = _make_eg(tmp_path, [
        _item("a1", "Original Media/_cameras/d3/G9/a.jpg"),
    ])
    try:
        classify_event_items(
            eg, tmp_path, **_fakes(value="general", confidence=0.30))
        it = eg.item("a1")
        assert it.classification_needs_review == 1
        assert it.classification_confidence == 0.30
    finally:
        eg.close()


def test_migration_v1_to_v2_adds_confidence_column(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE item (id TEXT PRIMARY KEY, classification TEXT)")
    _migrate_v1_to_v2(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(item)")}
    assert "classification_confidence" in cols
    conn.close()
