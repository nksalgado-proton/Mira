"""Tests for the headless Cull model (spec/11 §5) — logic-only, no Qt.

Two tiers:
* ``status.py`` — the honest four-way projection (kept/candidate/discarded/untouched)
  + the day rollup, with the badge ladder (no heuristic).
* ``model.py`` — ``build_pick_days`` over a real ``EventGateway`` with an injected
  ``read_exif`` + ``scan_fn`` (no disk, no real EXIF): durable day axis, bucket→item_id
  mapping, content-stable ``bucket_key``, status wired from ``phase_state`` + soft-state.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.bucket_scanner import (
    BucketScannerConfig,
    BucketScanResult,
    BurstSequence,
    IndividualPhoto,
    SourceKind,
    VideoFile,
)
from mira.picked import (
    BADGE_BROWSED,
    BADGE_DONE,
    BADGE_IN_PROGRESS,
    BADGE_UNTOUCHED,
    BucketStatus,
    build_pick_days,
    pick_days,
    project_status,
    rollup_status,
)
from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store import schema
from mira.store.repo import EventStore

FIXED_NOW = "2026-06-01T12:00:00+00:00"


# --------------------------------------------------------------------------- #
# status.py
# --------------------------------------------------------------------------- #


def _ps(item_id, state):
    return m.PhaseState(item_id=item_id, phase="pick", state=state)


def test_project_status_four_way_honest():
    states = {
        "a": _ps("a", "picked"),
        "b": _ps("b", "candidate"),
        "c": _ps("c", "skipped"),
        # "d" has no row → untouched (distinct from explicit discard)
    }
    st = project_status(["a", "b", "c", "d"], states, bucket=None)
    assert (st.kept, st.candidate, st.discarded, st.untouched) == (1, 1, 1, 1)
    assert st.total == 4
    assert st.has_explicit_marks is True
    assert st.badge == BADGE_IN_PROGRESS


def test_project_status_untouched_is_not_discarded():
    """The bug the rebuild fixes: a fresh bucket reads untouched, not 100% discarded."""
    st = project_status(["a", "b", "c"], {}, bucket=None)
    assert st.untouched == 3
    assert st.discarded == 0
    assert st.badge == BADGE_UNTOUCHED


def test_project_status_badge_ladder():
    marks = {"a": _ps("a", "picked")}
    reviewed = m.Bucket(bucket_key="k", phase="pick", reviewed=True)
    browsed = m.Bucket(bucket_key="k", phase="pick", browsed=True)
    # reviewed wins even over marks
    assert project_status(["a"], marks, reviewed).badge == BADGE_DONE
    # a mark → in_progress
    assert project_status(["a"], marks, None).badge == BADGE_IN_PROGRESS
    # opened, no mark → browsed
    assert project_status(["a"], {}, browsed).badge == BADGE_BROWSED
    # nothing → untouched
    assert project_status(["a"], {}, None).badge == BADGE_UNTOUCHED


def test_project_status_unknown_state_folds_to_untouched():
    st = project_status(["a"], {"a": _ps("a", "bogus")}, None)
    assert st.untouched == 1 and st.discarded == 0


def test_rollup_done_only_when_all_done():
    done = BucketStatus(1, 1, 0, 0, 0, True, False, BADGE_DONE)
    prog = BucketStatus(2, 1, 0, 1, 0, False, False, BADGE_IN_PROGRESS)
    assert rollup_status([done, done]).badge == BADGE_DONE
    assert rollup_status([done, prog]).badge == BADGE_IN_PROGRESS
    r = rollup_status([done, prog])
    assert (r.total, r.kept, r.discarded) == (3, 2, 1)


def test_rollup_ignores_empty_buckets():
    empty = BucketStatus(0, 0, 0, 0, 0, False, False, BADGE_UNTOUCHED)
    browsed = BucketStatus(1, 0, 0, 0, 1, False, True, BADGE_BROWSED)
    assert rollup_status([empty, browsed]).badge == BADGE_BROWSED


# --------------------------------------------------------------------------- #
# model.py — build_pick_days
# --------------------------------------------------------------------------- #

# Captured items: 3 burst + 2 moment + 1 video on day 1; one solo on day 2.
# origin_relpath names are routed by the fake scan_fn below.
_ITEMS = [
    ("i1", "photo", "d1/burst1.jpg", 1),
    ("i2", "photo", "d1/burst2.jpg", 1),
    ("i3", "photo", "d1/burst3.jpg", 1),
    ("i4", "photo", "d1/moment1.jpg", 1),
    ("i5", "photo", "d1/moment2.jpg", 1),
    ("i6", "video", "d1/clip.mov", 1),
    ("i7", "photo", "d2/solo.jpg", 2),
]


def _build_event_doc():
    items = []
    for idx, (iid, kind, rel, day) in enumerate(_ITEMS):
        items.append(
            m.Item(
                id=iid, kind=kind, origin_relpath=rel, sha256=f"sha{idx}",
                byte_size=100 + idx,
                materialized_at=FIXED_NOW, materialized_phase="ingest",
                camera_id="G9M2",
                capture_time_raw=f"2026-04-01T08:0{idx}:00",
                capture_time_corrected=f"2026-04-01T08:0{idx}:00",
                created_at=FIXED_NOW, day_number=day, provenance="captured",
            )
        )
    phase_states = [
        _ps("i1", "picked"),
        _ps("i2", "picked"),
        _ps("i3", "skipped"),
        _ps("i4", "candidate"),
        # i5 untouched (no row)
        _ps("i6", "picked"),
        # i7 untouched
    ]
    event = m.Event(uuid="evt", name="Test", created_at=FIXED_NOW, updated_at=FIXED_NOW)
    trip_days = [
        m.TripDay(day_number=1, date="2026-04-01", description="Arrival"),
        m.TripDay(day_number=2, date="2026-04-02", description="Hike"),
    ]
    cameras = [m.Camera(camera_id="G9M2")]
    return m.EventDocument(
        event=event, items=items, phase_states=phase_states, trip_days=trip_days,
        cameras=cameras,
    )


def _open_gateway(tmp_path):
    db = tmp_path / "event.db"
    store = EventStore.create(db, event_id="evt")
    store.save_document(_build_event_doc())
    store.close()
    gw = EventGateway.open(db, event_root=tmp_path, now=lambda: FIXED_NOW)
    return gw


def _fake_read_exif(paths):
    # The model only reads ``.path`` + ``.raw``; timestamps come from scan_fn.
    return [SimpleNamespace(path=Path(p), raw={}, timestamp=None) for p in paths]


def _fake_scan(entries, source_kind, config):
    """Route paths to buckets by filename so _flatten yields a predictable tree:
    burst* → one burst, moment* → one moment cluster, *.mov → video, else individual."""
    by = {Path(e.path).name: Path(e.path) for e in entries}
    res = BucketScanResult(source_kind=source_kind)
    burst = sorted(p for n, p in by.items() if n.startswith("burst"))
    moment = sorted(p for n, p in by.items() if n.startswith("moment"))
    vids = sorted(p for n, p in by.items() if p.suffix.lower() == ".mov")
    solos = sorted(
        p for n, p in by.items()
        if not n.startswith(("burst", "moment")) and p.suffix.lower() != ".mov"
    )
    if burst:
        res.bursts.append(BurstSequence(
            burst_id="b1", photos=list(burst), detection_source="sequence_number",
            representative_timestamp=datetime(2026, 4, 1, 8, 0, 0),
        ))
    for i, p in enumerate(moment):
        res.individuals.append(IndividualPhoto(
            path=p, timestamp=datetime(2026, 4, 1, 8, 5, i),
            cluster_id="c1", cluster_size=len(moment), cluster_position=i + 1,
        ))
    for p in solos:
        res.individuals.append(IndividualPhoto(
            path=p, timestamp=datetime(2026, 4, 2, 9, 0, 0)))
    for p in vids:
        res.videos.append(VideoFile(
            path=p, timestamp=datetime(2026, 4, 1, 8, 30, 0), duration_s=12.0))
    return res


def _run(tmp_path):
    gw = _open_gateway(tmp_path)
    days = build_pick_days(
        gw, phase="pick", source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan,
    )
    return gw, days


def test_build_days_durable_day_axis(tmp_path):
    _gw, days = _run(tmp_path)
    assert [d.day_number for d in days] == [1, 2]
    assert days[0].label.startswith("Day 1")
    assert "Arrival" in days[0].label


def test_build_day1_buckets_and_membership(tmp_path):
    _gw, days = _run(tmp_path)
    day1 = days[0]
    kinds = [b.kind for b in day1.buckets]
    # chronological by anchor: burst (08:00) → moment (08:05) → video (08:30)
    assert kinds == ["burst", "moment", "video"]
    burst, moment, video = day1.buckets
    assert burst.item_ids == ("i1", "i2", "i3")
    assert set(moment.item_ids) == {"i4", "i5"}
    assert video.item_ids == ("i6",)
    # content-stable bucket_key for the burst (day|kind|id)
    assert burst.bucket_key == "1|burst|b1"


def test_build_honest_status_per_bucket(tmp_path):
    _gw, days = _run(tmp_path)
    burst, moment, video = days[0].buckets
    # burst: i1,i2 kept · i3 discarded · none untouched
    assert (burst.status.kept, burst.status.discarded, burst.status.untouched) == (2, 1, 0)
    assert burst.status.badge == BADGE_IN_PROGRESS
    # moment: i4 candidate · i5 untouched — the honest split (no fold, no badge-gating)
    assert (moment.status.candidate, moment.status.untouched) == (1, 1)
    assert moment.status.badge == BADGE_IN_PROGRESS
    # video: i6 kept
    assert video.status.kept == 1
    # day 2 solo: fully untouched
    assert days[1].buckets[0].status.badge == BADGE_UNTOUCHED
    assert days[1].buckets[0].status.untouched == 1


def test_bucket_soft_state_drives_badge(tmp_path):
    gw = _open_gateway(tmp_path)
    # Declare the burst bucket reviewed → its badge becomes DONE on the next build.
    gw.set_bucket_reviewed("1|burst|b1", "pick", True)
    days = build_pick_days(
        gw, source_kind=SourceKind.CAMERA,
        read_exif=_fake_read_exif, scan_fn=_fake_scan,
    )
    burst = days[0].buckets[0]
    assert burst.status.reviewed is True
    assert burst.status.badge == BADGE_DONE
    # Day rollup is not all-done (other buckets aren't) → in_progress.
    assert days[0].status.badge == BADGE_IN_PROGRESS


def test_day_rollup_counts(tmp_path):
    _gw, days = _run(tmp_path)
    r = days[0].status
    # day 1 totals across the 3 buckets: 6 items, 3 kept (i1,i2,i6), 1 candidate (i4),
    # 1 discarded (i3), 1 untouched (i5)
    assert r.total == 6
    assert (r.kept, r.candidate, r.discarded, r.untouched) == (3, 1, 1, 1)


# --------------------------------------------------------------------------- #
# pick_days — the cache-backed path (spec/11 §4, D5-revised)
# --------------------------------------------------------------------------- #

_CFG = BucketScannerConfig(cluster_window_seconds=300.0)


def _structure(days):
    """(day_number, [(bucket_key, item_ids)]) — the cached structure to compare."""
    return [
        (d.day_number, [(b.bucket_key, b.item_ids) for b in d.buckets])
        for d in days
    ]


def _counting_scan():
    calls = []

    def scan(entries, source_kind, config):
        calls.append(1)
        return _fake_scan(entries, source_kind, config)

    return scan, calls


def _boom_scan(*a, **k):
    raise AssertionError("scan_fn must not run on a cache hit")


def test_select_days_caches_and_avoids_rescan(tmp_path):
    gw = _open_gateway(tmp_path)
    scan, calls = _counting_scan()
    first = pick_days(gw, read_exif=_fake_read_exif, scan_fn=scan, config=_CFG)
    assert calls  # computed at least once (one scan per day)
    # Second build is a pure cache hit — the scanner must not be called at all.
    second = pick_days(gw, read_exif=_fake_read_exif, scan_fn=_boom_scan, config=_CFG)
    assert _structure(first) == _structure(second)


def test_select_days_reports_progress(tmp_path):
    """pick_days drives the optional progress callback per day + a final completion tick
    (M2.V — feeds the Cull-entry progress dialog)."""
    gw = _open_gateway(tmp_path)
    calls: list = []
    pick_days(
        gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
        progress=lambda done, total, day, n: calls.append((done, total, day, n)),
    )
    assert calls, "progress must be reported"
    totals = {c[1] for c in calls}
    assert len(totals) == 1                       # total day count is constant
    total = totals.pop()
    assert calls[-1] == (total, total, None, 0)   # final completion tick
    assert any(c[0] < total for c in calls[:-1])  # at least one per-day report


def test_select_days_matches_uncached(tmp_path):
    gw = _open_gateway(tmp_path)
    cached = pick_days(gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG)
    uncached = build_pick_days(gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG)
    assert _structure(cached) == _structure(uncached)


def test_select_days_camera_filter(tmp_path):
    """camera_id scopes the tree to one camera (per-camera in-event cull, Nelson 2026-06-01).
    The fixture's items are all camera 'G9M2'."""
    gw = _open_gateway(tmp_path)
    all_days = pick_days(gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG)
    g9 = pick_days(gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
                   camera_id="G9M2")
    assert _structure(g9) == _structure(all_days)        # all items are that camera
    none = pick_days(gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
                     camera_id="no-such-camera")
    assert none == []


def test_select_days_invalidates_on_config_change(tmp_path):
    gw = _open_gateway(tmp_path)
    pick_days(gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG)
    # Changing the moment-gap setting must force a recompute (fingerprint differs).
    cfg2 = BucketScannerConfig(cluster_window_seconds=120.0)
    scan, calls = _counting_scan()
    pick_days(gw, read_exif=_fake_read_exif, scan_fn=scan, config=cfg2)
    assert calls, "config change should have triggered a recompute"


def test_select_days_soft_state_survives_recompute(tmp_path):
    gw = _open_gateway(tmp_path)
    pick_days(gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG)
    # Declare the burst bucket reviewed (durable soft-state, the bucket table).
    gw.set_bucket_reviewed("1|burst|b1", "pick", True)
    # Force a recompute via a config change — the cache tables are rewritten, but the
    # content-stable bucket_key means the burst survives and keeps its reviewed flag.
    cfg2 = BucketScannerConfig(cluster_window_seconds=120.0)
    days = pick_days(gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=cfg2)
    burst = next(b for b in days[0].buckets if b.bucket_key == "1|burst|b1")
    assert burst.status.reviewed is True
    assert burst.status.badge == BADGE_DONE


def test_select_days_status_is_live_on_cache_hit(tmp_path):
    """Marks change constantly; the cache stores only structure, so a mark made after
    caching shows on the next (cache-hit) build."""
    gw = _open_gateway(tmp_path)
    pick_days(gw, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG)
    # i5 was untouched; mark it kept. No clustering input changed → cache hit.
    gw.set_phase_state("i5", "pick", "picked")
    days = pick_days(gw, read_exif=_fake_read_exif, scan_fn=_boom_scan, config=_CFG)
    moment = next(b for b in days[0].buckets if b.kind == "moment")
    assert moment.status.kept == 1 and moment.status.untouched == 0


# --------------------------------------------------------------------------- #
# schema — greenfield v1 (the bucket-cache tables are part of the fresh DDL)
# --------------------------------------------------------------------------- #


def _table_names(conn):
    return {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_schema_version_matches_migrations():
    # Each MIGRATIONS entry upgrades version N -> N+1, so version == len + 1.
    assert schema.SCHEMA_VERSION == len(schema.MIGRATIONS) + 1


def test_cache_tables_present_on_fresh_db(tmp_path):
    """Fresh DBs are created at SCHEMA_VERSION directly — the derived cache tables ship in
    the fresh DDL (migrations only patch older DBs). They are excluded from the JSON backup
    (``schema.CACHE_TABLES``) but always exist physically."""
    db = tmp_path / "event.db"
    store = EventStore.create(db, event_id="evt")
    try:
        assert schema.get_version(store.conn) == schema.SCHEMA_VERSION
        assert set(schema.CACHE_TABLES) <= _table_names(store.conn)
    finally:
        store.close()
