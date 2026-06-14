"""Tests for core.bucket_navigator_model (Stage A.3a).

Pure. A fake ``scan_fn`` returns hand-built BucketScanResults so
day-grouping (EXIF date, undated-last), flatten order, stable
bucket ids, per-kind default state, and the bucket_stats wrapper
are deterministic without driving real camera EXIF.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.bracket_detector import BracketSequence
from core.bucket_scanner import (
    BucketScanResult,
    BurstSequence,
    IndividualPhoto,
    SourceKind,
    VideoFile,
)
from core.cull_stats import BADGE_UNTOUCHED
from core.exif_reader import PhotoExif
from core.bucket_navigator_model import (
    bucket_stats,
    build_days,
    scan_event_day_folders,
)


def _pe(name: str, ts):
    return PhotoExif(path=Path(name), timestamp=ts)


def _empty(_entries, _kind, _cfg):
    return BucketScanResult(source_kind=SourceKind.CAMERA)


def test_day_grouping_chronological_with_undated_last():
    def fake(entries, _k, _c):
        # one Individuals bucket = exactly this day's files
        r = BucketScanResult(source_kind=SourceKind.CAMERA)
        r.individuals = [
            IndividualPhoto(path=e.path, timestamp=None) for e in entries
        ]
        return r

    exifs = [
        _pe("d2a.rw2", datetime(2026, 4, 2, 9, 0)),
        _pe("d1a.rw2", datetime(2026, 4, 1, 8, 0)),
        _pe("no.rw2", None),                       # undated
        _pe("d1b.rw2", datetime(2026, 4, 1, 18, 0)),
    ]
    days = build_days(exifs, SourceKind.CAMERA, scan_fn=fake)
    assert [d.key for d in days] == ["2026-04-01", "2026-04-02", "undated"]
    assert days[-1].label == "Undated"
    # day 1 has both its files in the single individual bucket.
    d1 = days[0].buckets[0]
    assert d1.kind == "individual" and d1.count == 2
    assert {p.name for p in d1.files} == {"d1a.rw2", "d1b.rw2"}


def test_flatten_order_ids_defaults_and_titles():
    def fake(entries, _k, _c):
        r = BucketScanResult(source_kind=SourceKind.CAMERA)
        r.focus_brackets = [BracketSequence(
            sequence_id="FB1", sequence_type="focus",
            photos=[Path("f1.rw2"), Path("f2.rw2")],
            confidence=1.0, detection_source="exif")]
        r.bursts = [BurstSequence(
            burst_id="B1", photos=[Path("b1.rw2"), Path("b2.rw2")],
            detection_source="drive_mode")]
        r.individuals = [IndividualPhoto(path=Path("i1.rw2"),
                                         timestamp=None)]
        r.videos = [VideoFile(path=Path("v1.mp4"), timestamp=None,
                              duration_s=3.0)]
        return r

    days = build_days([_pe("x.rw2", datetime(2026, 4, 1, 9, 0))],
                      SourceKind.CAMERA, scan_fn=fake)
    b = days[0].buckets
    assert [n.kind for n in b] == [
        "focus_bracket", "burst", "individual", "video"]
    fb, burst, ind, vid = b
    assert fb.bucket_id == "2026-04-01|focus_bracket|FB1"
    assert burst.bucket_id == "2026-04-01|burst|B1"
    # Individuals with no timestamp land in the trailing
    # ``<day>|individual|notime|<content_key>`` bucket (Nelson
    # 2026-05-23 task #104 — single mega-Individuals bucket
    # replaced by time-localized sub-buckets + a no-timestamp
    # tail bucket).
    assert ind.bucket_id.startswith("2026-04-01|individual|notime|")
    # One bucket PER clip now (stem-keyed) — like brackets/bursts.
    assert vid.bucket_id == "2026-04-01|video|v1"
    assert fb.title == "Focus Bracket · 2" and fb.count == 2
    assert vid.title == "Video · v1.mp4" and vid.count == 1
    # EVERY kind defaults DISCARDED (Nelson 2026-05-18 — brackets
    # no longer the exception; Keep All replaces default-kept).
    assert (fb.default_state, burst.default_state,
            ind.default_state, vid.default_state) == \
        ("skipped",) * 4


def test_each_video_is_its_own_bucket(tmp_path):
    """Nelson 2026-05-18: a day's clips are NOT lumped — one bucket
    per clip (like each bracket/burst), stem-keyed, dup stems
    deduped, count==1 each."""
    def fake(entries, _k, _c):
        r = BucketScanResult(source_kind=SourceKind.CAMERA)
        r.videos = [
            VideoFile(path=Path("a/clip.mp4"), timestamp=None,
                      duration_s=3.0),
            VideoFile(path=Path("b/clip.mp4"), timestamp=None,
                      duration_s=4.0),                 # dup stem
            VideoFile(path=Path("P1418066.MP4"), timestamp=None,
                      duration_s=5.0),
        ]
        return r

    days = build_days([_pe("x.rw2", datetime(2026, 4, 1, 9, 0))],
                      SourceKind.CAMERA, scan_fn=fake)
    vids = [b for b in days[0].buckets if b.kind == "video"]
    assert len(vids) == 3
    assert [v.bucket_id for v in vids] == [
        "2026-04-01|video|clip",
        "2026-04-01|video|clip-1",                     # deduped
        "2026-04-01|video|P1418066",
    ]
    assert all(v.count == 1 for v in vids)
    assert vids[0].title == "Video · clip.mp4"


def test_empty_scan_yields_no_days():
    days = build_days([_pe("a.rw2", datetime(2026, 4, 1, 9, 0))],
                      SourceKind.CAMERA, scan_fn=_empty)
    assert days == []


def test_bucket_stats_uniform_discarded_default_fresh_bucket():
    def fake(entries, _k, _c):
        r = BucketScanResult(source_kind=SourceKind.CAMERA)
        r.focus_brackets = [BracketSequence(
            sequence_id="FB1", sequence_type="focus",
            photos=[Path("f1.rw2"), Path("f2.rw2"), Path("f3.rw2")],
            confidence=1.0, detection_source="exif")]
        r.individuals = [IndividualPhoto(path=Path("i1.rw2"),
                                         timestamp=None)]
        return r

    days = build_days([_pe("x.rw2", datetime(2026, 4, 1, 9, 0))],
                      SourceKind.CAMERA, scan_fn=fake)
    fb, ind = days[0].buckets
    # Fresh focus bracket, empty journal → DISCARDED default now
    # (Nelson 2026-05-18 — Keep All replaces the bracket=kept
    # exception), same as everything else.
    s_fb = bucket_stats(fb, {})
    assert (s_fb.kept, s_fb.discarded) == (0, 3)
    assert s_fb.badge == BADGE_UNTOUCHED          # not acted on yet
    # Fresh individuals → also DISCARDED.
    s_i = bucket_stats(ind, {})
    assert (s_i.kept, s_i.discarded) == (0, 1)
    assert s_i.badge == BADGE_UNTOUCHED
    # bucket_stats must not mutate the caller's journal.
    j: dict = {}
    bucket_stats(fb, j)
    assert j == {}


# ── Folder-derived days (Stage B.3a — Home consolidation) ────────


def _fake_individuals(entries, _kind, _cfg):
    r = BucketScanResult(source_kind=SourceKind.CAMERA)
    r.individuals = [
        IndividualPhoto(path=e.path, timestamp=None) for e in entries
    ]
    return r


def test_moment_clusters_become_view_buckets_and_are_lossless():
    """Defect B (frozen 2026-05-18): _flatten splits individuals
    into one MOMENT bucket per cluster + a residual Individuals
    bucket; all are VIEWS over the shared day journal so a
    re-cluster regroups without touching per-file marks."""
    from core.bucket_navigator_model import _flatten, bucket_stats
    from core.cull_state import set_state, STATE_KEPT as STATE_PICKED

    def scan(cluster_of):
        r = BucketScanResult(source_kind=SourceKind.CAMERA)
        r.individuals = [
            IndividualPhoto(path=Path(n), timestamp=None,
                            cluster_id=cluster_of.get(n))
            for n in ("a.rw2", "b.rw2", "c.rw2", "d.rw2")
        ]
        return r

    # First cluster window: {a,b}=M1, {c}=M2, d loose.
    nodes = _flatten("2026-04-20", scan(
        {"a.rw2": "M1", "b.rw2": "M1", "c.rw2": "M2"}))
    kinds = [n.kind for n in nodes]
    assert kinds == ["moment", "moment", "individual"]
    assert nodes[0].count == 2 and nodes[1].count == 1
    assert nodes[2].kind == "individual" and nodes[2].count == 1
    assert nodes[0].bucket_id.startswith("2026-04-20|moment|")
    assert all(n.default_state == "skipped" for n in nodes)

    # The shared day journal: a per-FILE mark on a.rw2.
    shared = {"marks": {}, "default_state": "skipped"}
    set_state(shared, "a.rw2", STATE_PICKED)
    m1 = next(n for n in nodes if "a.rw2" in
              {p.name for p in n.files})
    assert bucket_stats(m1, shared).kept == 1     # mark visible here

    # Re-cluster (window changed): a.rw2 now alone in a new moment.
    nodes2 = _flatten("2026-04-20", scan(
        {"a.rw2": "X9", "b.rw2": "X8", "c.rw2": "X8"}))
    m_a = next(n for n in nodes2 if "a.rw2" in
               {p.name for p in n.files})
    # Same shared journal, different grouping → the per-file mark
    # SURVIVED (lossless by construction).
    assert bucket_stats(m_a, shared).kept == 1


def test_scan_event_day_folders_uses_plan_not_exif(tmp_path):
    from datetime import date
    from core.models import TripDay
    from core.path_builder import day_folder_name

    d9 = TripDay(day_number=9, date=date(2026, 4, 20),
                 description="Manuel Antonio National Park")
    d10 = TripDay(day_number=10, date=date(2026, 4, 21),
                  description="Departure")          # no folder on disk
    f9 = day_folder_name(d9)
    day_dir = tmp_path / f9
    (day_dir / "macro").mkdir(parents=True)         # scenario subdir
    (day_dir / "a.rw2").write_bytes(b"x")
    (day_dir / "b.jpg").write_bytes(b"x")
    (day_dir / "macro" / "c.rw2").write_bytes(b"x")  # recursive
    (day_dir / "_cull.json").write_text("{}")        # ignored (not media)

    days = scan_event_day_folders(
        tmp_path, [d10, d9], SourceKind.CAMERA,
        scan_fn=_fake_individuals,
    )
    assert len(days) == 1                            # only d9 has a folder
    dn = days[0]
    assert dn.key == f9 and dn.label == f9           # label from the PLAN
    assert dn.label == "Dia 9 - 2026-04-20 - Manuel Antonio National Park"
    b = dn.buckets[0]
    assert b.kind == "individual"
    assert {p.name for p in b.files} == {"a.rw2", "b.jpg", "c.rw2"}
    # Scenario/style mix = the immediate sub-folder (only c.rw2 is
    # under one); a.rw2/b.jpg sit at the Dia root → no style.
    assert dn.style_mix == (("macro", 1),)


def test_list_event_day_folders_is_cheap_then_scan_day(tmp_path):
    """The lazy pair (frozen 2026-05-18): list_event_day_folders is
    folder-only — no EXIF, no scan_fn — and scan_day does the
    per-day work. Same plan-not-EXIF day derivation."""
    from datetime import date
    from core.models import TripDay
    from core.path_builder import day_folder_name
    from core.bucket_navigator_model import (
        list_event_day_folders, scan_day,
    )

    d9 = TripDay(day_number=9, date=date(2026, 4, 20),
                 description="Manuel Antonio National Park")
    d10 = TripDay(day_number=10, date=date(2026, 4, 21),
                  description="Departure")          # no folder
    f9 = day_folder_name(d9)
    day_dir = tmp_path / f9
    (day_dir / "macro").mkdir(parents=True)
    (day_dir / "a.rw2").write_bytes(b"x")
    (day_dir / "macro" / "c.rw2").write_bytes(b"x")
    (day_dir / "notes.txt").write_text("hi")          # not media

    # Cheap list: no scan_fn arg at all; days from the PLAN.
    folders = list_event_day_folders(tmp_path, [d10, d9])
    assert len(folders) == 1
    df = folders[0]
    assert df.key == f9 and df.label == f9
    assert {p.name for p in df.files} == {"a.rw2", "c.rw2"}
    assert df.filenames and set(df.filenames) == {"a.rw2", "c.rw2"}
    assert df.style_mix == (("macro", 1),)

    # The expensive half, on demand, injectable scan_fn. Now also
    # yields the classified style mix (inc.2b) alongside buckets.
    buckets, _mix = scan_day(df, SourceKind.CAMERA,
                             scan_fn=_fake_individuals)
    assert len(buckets) == 1 and buckets[0].kind == "individual"
    assert {p.name for p in buckets[0].files} == {"a.rw2", "c.rw2"}

    # No plan folders → empty list, never raises.
    assert list_event_day_folders(tmp_path, [d10]) == []


def test_flatten_carries_provenance(tmp_path):
    """Enriched rows (Nelson 2026-05-17): a bucket carries its
    detection_source (why it was grouped) and a camera string;
    individual/video have no detection source."""
    from core.bucket_navigator_model import _flatten

    res = BucketScanResult(source_kind=SourceKind.CAMERA)
    res.bursts = [BurstSequence(
        burst_id="B1", photos=[Path("p1.rw2"), Path("p2.rw2")],
        detection_source="sequence_number")]
    res.individuals = [IndividualPhoto(path=Path("i1.rw2"),
                                       timestamp=None)]
    nodes = _flatten("2026-04-20", res,
                     camera_for=lambda _p: "DC-G9M2")
    burst = next(n for n in nodes if n.kind == "burst")
    indiv = next(n for n in nodes if n.kind == "individual")
    assert burst.detection_source == "sequence_number"
    assert burst.camera == "DC-G9M2"
    assert indiv.detection_source == ""        # not a sequence
    assert indiv.camera == "DC-G9M2"
    # No camera_for → empty, never raises.
    plain = _flatten("d", res)
    assert next(n for n in plain if n.kind == "burst").camera == ""


def test_scan_event_day_folders_missing_or_empty(tmp_path):
    from datetime import date
    from core.models import TripDay
    from core.path_builder import day_folder_name

    d = TripDay(day_number=1, date=date(2026, 4, 1), description="X")
    # No folder at all → [].
    assert scan_event_day_folders(
        tmp_path, [d], SourceKind.CAMERA,
        scan_fn=_fake_individuals) == []
    # Folder exists but only non-media → skipped (no DayNode).
    (tmp_path / day_folder_name(d)).mkdir()
    (tmp_path / day_folder_name(d) / "notes.txt").write_text("hi")
    assert scan_event_day_folders(
        tmp_path, [d], SourceKind.CAMERA,
        scan_fn=_fake_individuals) == []


# ── inc.2b: classified style mix on the Day rows ─────────────────

from types import SimpleNamespace  # noqa: E402
from core.bucket_navigator_model import day_style_mix  # noqa: E402


def _fake_classify(mapping):
    def f(path, _exif, **_kw):
        return SimpleNamespace(
            scenario=SimpleNamespace(value=mapping[Path(path).name]))
    return f


def test_day_style_mix_counts_and_orders(monkeypatch):
    monkeypatch.setattr(
        "core.genre.classify_exif",
        _fake_classify({"a.rw2": "wildlife", "b.rw2": "wildlife",
                        "c.rw2": "landscape"}),
    )
    mix = day_style_mix(
        [_pe("a.rw2", None), _pe("b.rw2", None), _pe("c.rw2", None)])
    assert mix == (("wildlife", 2), ("landscape", 1))


def test_day_style_mix_classify_error_is_skipped(monkeypatch):
    def boom(path, _exif, **_kw):
        if Path(path).name == "bad.rw2":
            raise RuntimeError("classify blew up")
        return SimpleNamespace(
            scenario=SimpleNamespace(value="macro"))
    monkeypatch.setattr("core.genre.classify_exif", boom)
    mix = day_style_mix([_pe("bad.rw2", None), _pe("ok.rw2", None)])
    assert mix == (("macro", 1),)              # bad one skipped, no crash


def test_build_days_carries_classified_style_mix(monkeypatch):
    monkeypatch.setattr(
        "core.genre.classify_exif",
        _fake_classify({"d1a.rw2": "wildlife", "d1b.rw2": "landscape",
                        "d2a.rw2": "wildlife"}),
    )

    def fake(entries, _k, _c):
        r = BucketScanResult(source_kind=SourceKind.CAMERA)
        r.individuals = [
            IndividualPhoto(path=e.path, timestamp=None) for e in entries
        ]
        return r

    exifs = [
        _pe("d1a.rw2", datetime(2026, 4, 1, 8, 0)),
        _pe("d1b.rw2", datetime(2026, 4, 1, 9, 0)),
        _pe("d2a.rw2", datetime(2026, 4, 2, 8, 0)),
    ]
    days = build_days(exifs, SourceKind.CAMERA, scan_fn=fake)
    by_key = {d.key: d for d in days}
    assert by_key["2026-04-01"].style_mix == (
        ("wildlife", 1), ("landscape", 1))
    assert by_key["2026-04-02"].style_mix == (("wildlife", 1),)
