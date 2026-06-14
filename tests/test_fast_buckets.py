"""Tests for the Fast Culler bucket model (build_quick_sweep_buckets) — day-by-EXIF-date grouping
+ per-day clustering via the shared scanner. Pure (fake scanner + EXIF reader)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from core.bucket_scanner import (
    BucketScanResult,
    BurstSequence,
    IndividualPhoto,
    VideoFile,
)
from mira.picked.quick_sweep_buckets import build_quick_sweep_buckets

_D1 = datetime(2026, 4, 1, 8, 0, 0)
_D2 = datetime(2026, 4, 2, 9, 0, 0)
_CFG = object()   # non-None → _resolve_config returns it (no settings read)


def _items():
    def si(name, ts):
        return SimpleNamespace(path=Path(f"/card/{name}"), timestamp=ts, camera_id="G9")
    return [
        si("burst1.jpg", _D1), si("burst2.jpg", _D1), si("burst3.jpg", _D1),
        si("moment1.jpg", _D1), si("moment2.jpg", _D1),
        si("clip.mov", _D1),
        si("solo.jpg", _D2),
    ]


def _fake_read_exif(paths):
    return [SimpleNamespace(path=Path(p), raw={}) for p in paths]


def _fake_scan(entries, source_kind, config):
    """Route by filename: burst* → one burst, moment* → a moment cluster, *.mov → video,
    else individual (same shape as the cull-model test fake)."""
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
            representative_timestamp=datetime(2026, 4, 1, 8, 0, 0)))
    for i, p in enumerate(moment):
        # Phone make/model so the cluster_classifier's phone-only repeat
        # filter fires for the tight moment (Nelson 2026-06-09).
        res.individuals.append(IndividualPhoto(
            path=p, timestamp=datetime(2026, 4, 1, 8, 5, i),
            make="Apple", model="iPhone 13",
            cluster_id="c1", cluster_size=len(moment), cluster_position=i + 1))
    for p in solos:
        res.individuals.append(IndividualPhoto(
            path=p, timestamp=datetime(2026, 4, 2, 9, 0, 0)))
    for p in vids:
        res.videos.append(VideoFile(
            path=p, timestamp=datetime(2026, 4, 1, 8, 30, 0), duration_s=12.0))
    return res


def _build():
    return build_quick_sweep_buckets(
        _items(), read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG)


def test_groups_by_capture_date():
    buckets = _build()
    labels = [b.day_label for b in buckets]
    assert "2026-04-01" in labels and "2026-04-02" in labels
    # Day order: 04-01's buckets all precede 04-02's (day-major).
    first_d2 = next(i for i, b in enumerate(buckets) if b.day_label == "2026-04-02")
    assert all(b.day_label == "2026-04-01" for b in buckets[:first_d2])


def test_clusters_within_a_day():
    buckets = _build()
    d1 = [b for b in buckets if b.day_label == "2026-04-01"]
    kinds = {b.kind for b in d1}
    assert "burst" in kinds            # the 3 burst frames → one burst bucket
    assert "video" in kinds            # clip.mov → its own video bucket
    burst = next(b for b in d1 if b.kind == "burst")
    assert burst.count == 3
    video = next(b for b in d1 if b.kind == "video")
    assert video.count == 1


def test_second_day_has_the_solo():
    buckets = _build()
    d2 = [b for b in buckets if b.day_label == "2026-04-02"]
    assert sum(b.count for b in d2) == 1


def test_undated_items_group_under_undated_last():
    items = _items() + [SimpleNamespace(
        path=Path("/card/notime.jpg"), timestamp=None, camera_id="G9")]
    buckets = build_quick_sweep_buckets(
        items, read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG)
    assert buckets[-1].day_label == "Undated"     # undated sorts last


def test_progress_reports_per_day_and_finishes():
    calls = []
    build_quick_sweep_buckets(
        _items(), read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
        progress=lambda done, total, label, n: calls.append((done, total, label, n)))
    assert calls
    total = calls[-1][1]
    assert calls[-1][0] == total          # final completion tick
    assert total == 2                     # two capture dates


# --------------------------------------------------------------------------- #
# build_fast_days adapter (Nelson 2026-06-05) — wraps FastBucket → PickDay
# shapes the days panel + DayGridView consume.
# --------------------------------------------------------------------------- #


def _build_days(state_for=None):
    from mira.picked.quick_sweep_buckets import build_fast_days
    return build_fast_days(
        _items(), read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG,
        state_for=state_for,
    )


def test_build_fast_days_yields_one_culldays_per_capture_date():
    days = _build_days()
    assert len(days) == 2
    assert [d.day_number for d in days] == [1, 2]
    # Label carries the date.
    assert "2026-04-01" in days[0].label
    assert "2026-04-02" in days[1].label


def test_build_fast_days_buckets_match_quick_sweep_buckets():
    """Each PickDay's buckets are 1:1 with build_quick_sweep_buckets's output for that day."""
    fast = build_quick_sweep_buckets(
        _items(), read_exif=_fake_read_exif, scan_fn=_fake_scan, config=_CFG)
    days = _build_days()
    by_label = {d.label: d for d in days}
    for label_key in ("2026-04-01", "2026-04-02"):
        cd = next(d for d in days if label_key in d.label)
        expected = [fb for fb in fast if fb.day_label == label_key]
        assert len(cd.buckets) == len(expected)
        for cb, fb in zip(cd.buckets, expected):
            assert cb.kind == fb.kind
            assert tuple(ci.path for ci in cb.items) == fb.paths


def test_build_fast_days_default_state_is_kept():
    """The default state_for is "every path is Keep" — Fast Culler's contract."""
    from mira.picked.status import STATE_PICKED
    days = _build_days()
    # The day's status rollup counts every item as kept by default.
    day = days[0]
    total = sum(b.status.total for b in day.buckets)
    assert day.status.kept == total
    assert day.status.discarded == 0
    assert day.status.candidate == 0


def test_build_fast_days_state_for_callable_drives_status():
    """A custom state_for shifts the day status counts."""
    from mira.picked.status import STATE_SKIPPED, STATE_PICKED
    seen = {"hits": 0}

    def state_for(p):
        seen["hits"] += 1
        return (STATE_SKIPPED if p.name.startswith("burst")
                else STATE_PICKED)
    days = _build_days(state_for=state_for)
    # state_for was queried at least once per item per day.
    assert seen["hits"] > 0
    d1 = days[0]
    # The 3 burst items count as discarded in d1's rollup.
    assert d1.status.discarded == 3


def test_fast_day_grid_cells_emits_cluster_cells():
    """spec/52 Quick Sweep slice B (Nelson 2026-06-09): real-cluster buckets
    (burst / focus / exposure / repeat) collapse to ONE cluster cell each;
    the user picks keepers in a sub-grid (slice C). Non-cluster kinds
    (video / loose moment / individual) still flatten to per-item cells.

    Reverses the prior 2026-06-05 design where Quick Sweep flattened every
    cluster to per-item cells."""
    from mira.picked.quick_sweep_buckets import fast_day_grid_cells
    from mira.picked.status import STATE_PICKED
    days = _build_days()
    cells = fast_day_grid_cells(days[0], lambda _p: STATE_PICKED)

    # Burst → one cluster cell (3 frames).
    burst_cells = [c for c in cells if c.cluster and c.cluster.kind == "burst"]
    assert len(burst_cells) == 1
    assert burst_cells[0].cluster.count == 3

    # The 2 moment photos in the fixture are 1 s apart — tight enough that
    # the repeat detector reclaims them into a single repeat cluster.
    repeat_cells = [c for c in cells if c.cluster and c.cluster.kind == "repeat"]
    assert len(repeat_cells) == 1
    assert repeat_cells[0].cluster.count == 2

    # Video stays a per-item flat cell.
    flat_cells = [c for c in cells if not c.is_cluster]
    assert len(flat_cells) == 1
    assert flat_cells[0].item_kind == "video"


def test_fast_day_grid_cells_uses_default_keep():
    """A cell with no explicit state shows KEPT colour (default-Keep contract)."""
    from mira.picked.quick_sweep_buckets import fast_day_grid_cells
    from mira.picked.status import CellColor, STATE_PICKED
    days = _build_days()
    cells = fast_day_grid_cells(days[0], lambda _p: STATE_PICKED)
    assert all(c.color is CellColor.KEPT for c in cells)


# --------------------------------------------------------------------------- #
# spec/52 Quick Sweep slice B — cluster cells + repeat detection
# (Nelson 2026-06-09).
# --------------------------------------------------------------------------- #


def _loose_moment_scan(entries, source_kind, config):
    """Variant of ``_fake_scan`` where the two ``moment*`` files are 60 s
    apart — well outside the repeat detector's 5 s window. The scanner's
    moment annotation still binds them (≥ 3 photos in 5-min window
    would, but a 2-photo group keeps cluster_id=None per the real scanner;
    we set cluster_id="c1" here to keep the fixture comparable). The
    classifier therefore does NOT flag these as repeats."""
    from core.bucket_scanner import BucketScanResult
    by = {Path(e.path).name: Path(e.path) for e in entries}
    res = BucketScanResult(source_kind=source_kind)
    moment = sorted(p for n, p in by.items() if n.startswith("moment"))
    for i, p in enumerate(moment):
        res.individuals.append(IndividualPhoto(
            path=p,
            timestamp=datetime(2026, 4, 1, 8, 5 + i, 0),     # 60 s apart
            cluster_id="c1",
            cluster_size=len(moment),
            cluster_position=i + 1,
        ))
    return res


def test_loose_moment_stays_a_moment_not_a_repeat():
    """A scanner-moment whose member gaps are > 5 s is NOT a repeat. The
    classifier passes; the moment bucket survives intact."""
    items = [
        SimpleNamespace(path=Path("/card/moment1.jpg"), timestamp=_D1, camera_id="G9"),
        SimpleNamespace(path=Path("/card/moment2.jpg"), timestamp=_D1, camera_id="G9"),
    ]
    buckets = build_quick_sweep_buckets(
        items, read_exif=_fake_read_exif, scan_fn=_loose_moment_scan, config=_CFG)
    kinds = [b.kind for b in buckets]
    assert "moment" in kinds, kinds
    assert "repeat" not in kinds, kinds


def test_tight_doublet_becomes_a_repeat_bucket():
    """Two individuals 1 s apart with no scanner cluster_id → the classifier
    detects a repeat doublet → one FastBucket with kind='repeat'."""
    def tight_doublet_scan(entries, source_kind, config):
        from core.bucket_scanner import BucketScanResult
        by = {Path(e.path).name: Path(e.path) for e in entries}
        res = BucketScanResult(source_kind=source_kind)
        for i, name in enumerate(("doublet1.jpg", "doublet2.jpg")):
            p = by.get(name)
            if p is None:
                continue
            # Phone make/model — the classifier's phone-only repeat
            # filter (Nelson 2026-06-09) gates non-phone individuals out.
            res.individuals.append(IndividualPhoto(
                path=p, timestamp=datetime(2026, 4, 1, 9, 0, i),
                make="Apple", model="iPhone 13"))
        return res

    items = [
        SimpleNamespace(path=Path("/card/doublet1.jpg"), timestamp=_D1, camera_id="Apple iPhone 13"),
        SimpleNamespace(path=Path("/card/doublet2.jpg"), timestamp=_D1, camera_id="Apple iPhone 13"),
    ]
    buckets = build_quick_sweep_buckets(
        items, read_exif=_fake_read_exif, scan_fn=tight_doublet_scan, config=_CFG)
    repeats = [b for b in buckets if b.kind == "repeat"]
    assert len(repeats) == 1
    assert repeats[0].count == 2
    assert "Repeat · 2" in repeats[0].title


def test_repeat_bucket_renders_as_cluster_cell():
    """End-to-end: a repeat FastBucket → CullBucket → cluster cell on the
    Day Grid, with the correct kind + count."""
    from mira.picked.quick_sweep_buckets import build_fast_days, fast_day_grid_cells
    from mira.picked.status import STATE_PICKED

    def tight_doublet_scan(entries, source_kind, config):
        from core.bucket_scanner import BucketScanResult
        by = {Path(e.path).name: Path(e.path) for e in entries}
        res = BucketScanResult(source_kind=source_kind)
        for i, name in enumerate(("a.jpg", "b.jpg")):
            p = by.get(name)
            if p is None:
                continue
            res.individuals.append(IndividualPhoto(
                path=p, timestamp=datetime(2026, 4, 1, 9, 0, i),
                make="Apple", model="iPhone 13"))
        return res

    items = [
        SimpleNamespace(path=Path("/card/a.jpg"), timestamp=_D1, camera_id="G9"),
        SimpleNamespace(path=Path("/card/b.jpg"), timestamp=_D1, camera_id="G9"),
    ]
    days = build_fast_days(
        items, read_exif=_fake_read_exif, scan_fn=tight_doublet_scan, config=_CFG)
    cells = fast_day_grid_cells(days[0], lambda _p: STATE_PICKED)
    cluster_cells = [c for c in cells if c.is_cluster]
    assert len(cluster_cells) == 1
    assert cluster_cells[0].cluster.kind == "repeat"
    assert cluster_cells[0].cluster.count == 2


def test_individual_outside_any_cluster_still_flattens():
    """A truly-isolated individual (no neighbours within 5 s) stays as a
    flat per-item cell — not a cluster cell."""
    from mira.picked.quick_sweep_buckets import build_fast_days, fast_day_grid_cells
    from mira.picked.status import STATE_PICKED

    def solo_scan(entries, source_kind, config):
        from core.bucket_scanner import BucketScanResult
        by = {Path(e.path).name: Path(e.path) for e in entries}
        res = BucketScanResult(source_kind=source_kind)
        for i, name in enumerate(("solo.jpg",)):
            p = by.get(name)
            if p is None:
                continue
            res.individuals.append(IndividualPhoto(
                path=p, timestamp=datetime(2026, 4, 1, 9, 0, 0)))
        return res

    items = [
        SimpleNamespace(path=Path("/card/solo.jpg"), timestamp=_D1, camera_id="G9"),
    ]
    days = build_fast_days(
        items, read_exif=_fake_read_exif, scan_fn=solo_scan, config=_CFG)
    cells = fast_day_grid_cells(days[0], lambda _p: STATE_PICKED)
    assert len(cells) == 1
    assert not cells[0].is_cluster
    assert cells[0].item_id is not None


def test_cluster_cell_color_reflects_member_uniform_state():
    """When every cluster member shares one state → the cluster cell takes
    that colour; no MIXED yellow."""
    from mira.picked.quick_sweep_buckets import fast_day_grid_cells
    from mira.picked.status import CellColor, STATE_SKIPPED
    days = _build_days()
    # Mark every photo Skip → uniform skip → cluster cells DISCARDED red.
    cells = fast_day_grid_cells(days[0], lambda _p: STATE_SKIPPED)
    cluster_cells = [c for c in cells if c.is_cluster]
    assert cluster_cells, "expected at least one cluster cell on day 1"
    for cc in cluster_cells:
        assert cc.color is CellColor.DISCARDED


def test_fast_day_grid_cells_compare_member_turns_cluster_mixed():
    """spec/52 slice B: a single Compare member inside a cluster turns the
    cluster cell border MIXED (yellow) — the spec/32 cluster_color rule
    that "any colour mix → MIXED". The Compare flag is still visible
    when the user expands the cluster sub-grid (slice C wires that)."""
    from mira.picked.quick_sweep_buckets import fast_day_grid_cells
    from mira.picked.status import CellColor, STATE_CANDIDATE, STATE_PICKED
    days = _build_days()

    def state_for(p):
        return STATE_CANDIDATE if p.name == "burst1.jpg" else STATE_PICKED
    cells = fast_day_grid_cells(days[0], state_for)
    burst_cell = next(c for c in cells if c.cluster and c.cluster.kind == "burst")
    assert burst_cell.color is CellColor.MIXED
