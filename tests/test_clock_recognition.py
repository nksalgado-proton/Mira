"""Tests for core.clock_recognition — candidate-pair generation + clustering.

Pure-math; no Qt, no EXIF, no file I/O. Validates spec/88 §2's normalization
+ tolerance rules so the recognition UI can trust what it ranks.

The math reused (``snap_to_tz_offset`` / ``snap_disagreement``) is covered
in test_clock_calibration; here we only exercise the candidate finder.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.clock_recognition import (
    CandidateCluster,
    CandidatePair,
    TIGHTNESS_TOLERANCE,
    find_candidate_pairs,
)
from core.fresh_source import SourceItem


# ── Helpers ──────────────────────────────────────────────────────────────


def _cam(name: str, t: datetime) -> SourceItem:
    return SourceItem(
        path=Path(f"cam/{name}.rw2"),
        timestamp=t,
        camera_id="G9",
    )


def _phone(name: str, t: datetime, tz_minutes: int) -> SourceItem:
    return SourceItem(
        path=Path(f"phone/{name}.jpg"),
        timestamp=t,
        camera_id="iPhone",
        tz_offset_minutes=tz_minutes,
    )


def _kappas(cluster: CandidateCluster) -> list[int]:
    return [p.snapped_kappa_minutes for p in cluster.pairs]


# ── Single-zone cluster ──────────────────────────────────────────────────


def test_single_zone_cluster_converges_to_one_kappa():
    """Camera set to UTC-180 (São Paulo) on a UTC-180 trip; every truly
    simultaneous pair implies κ = -180 and lands in a single bucket."""
    # Real UTC instants. The camera (set to -180) writes T - 3h; the
    # phone (also at -180) writes T - 3h. Three simultaneous pairs.
    instants = [
        datetime(2025, 5, 12, 13, 0, 0),
        datetime(2025, 5, 12, 14, 30, 0),
        datetime(2025, 5, 12, 16, 0, 0),
    ]
    cams = [_cam(f"c{i}", t - timedelta(hours=3))
            for i, t in enumerate(instants)]
    phones = [_phone(f"p{i}", t - timedelta(hours=3), -180)
              for i, t in enumerate(instants)]

    clusters = find_candidate_pairs(cams, phones)

    assert clusters, "expected at least one cluster"
    top = clusters[0]
    assert top.snapped_kappa_minutes == -180
    # Only the diagonal — same-instant pairs — implies κ=-180; off-diagonal
    # pairs land in other κ buckets (different time differences ⇒ different
    # implied set-TZ). So the strongest cluster has exactly the 3 diagonal
    # pairs, each at tightness 0.
    assert top.size == 3
    assert all(p.tightness == timedelta(0) for p in top.pairs)


def test_zero_offset_cluster_is_normal():
    """Camera correctly set — κ = phone_tz (κ=0 if phone is UTC). The
    recognition UI's one-click 0-offset case must come out as a normal
    cluster (spec/88 §3 point 2)."""
    instants = [
        datetime(2025, 5, 12, 13, 0, 0),
        datetime(2025, 5, 12, 14, 0, 0),
    ]
    cams = [_cam(f"c{i}", t) for i, t in enumerate(instants)]
    phones = [_phone(f"p{i}", t, 0) for i, t in enumerate(instants)]

    clusters = find_candidate_pairs(cams, phones)

    assert clusters
    assert clusters[0].snapped_kappa_minutes == 0


def test_zero_cluster_wins_ties_against_other_kappas():
    """When two clusters tie on size + top-tightness, the one closer to
    κ=0 wins (the correctly-set-camera case is the common case)."""
    # Two κ=0 pairs (truly simultaneous) plus two κ=-15 pairs (cam shot a
    # few minutes after the phone on each occasion). All within the 15-min
    # raw-delta gate.
    cams = [
        _cam("c0", datetime(2025, 5, 12, 10, 0, 0)),
        _cam("c1", datetime(2025, 5, 12, 11, 0, 0)),
        _cam("c2", datetime(2025, 5, 12, 12, 0, 0)),
        _cam("c3", datetime(2025, 5, 12, 13, 0, 0)),
    ]
    phones = [
        _phone("p0", datetime(2025, 5, 12, 10, 0, 0), 0),         # κ=0
        _phone("p1", datetime(2025, 5, 12, 11, 0, 0), 0),         # κ=0
        _phone("p2", datetime(2025, 5, 12, 12, 14, 0), 0),        # κ=-15
        _phone("p3", datetime(2025, 5, 12, 13, 14, 0), 0),        # κ=-15
    ]

    clusters = find_candidate_pairs(cams, phones)

    by_kappa = {c.snapped_kappa_minutes: c for c in clusters}
    assert 0 in by_kappa
    assert -15 in by_kappa
    assert by_kappa[0].size == by_kappa[-15].size
    # Tie on size → κ=0 wins on |κ| closest to zero.
    assert clusters[0].snapped_kappa_minutes == 0


# ── Raw-delta gate (Nelson 2026-06-18 — the new hard rule) ───────────────


def test_pairs_more_than_a_few_minutes_apart_are_filtered_out():
    """Spec/88 update (Nelson 2026-06-18): pairs whose clocks read more
    than 15 min apart can't be "the same moment" — the scenes won't match
    visually and the user can't recognize them. The algorithm drops them
    before clustering."""
    cam_close = _cam("close", datetime(2025, 5, 12, 12, 0, 0))
    phone_close = _phone("p_close", datetime(2025, 5, 12, 12, 0, 0), 0)
    # Hours apart — even with κ math the scene would not match. Filtered.
    cam_far = _cam("far", datetime(2025, 5, 12, 12, 0, 0))
    phone_far = _phone("p_far", datetime(2025, 5, 12, 18, 0, 0), 0)

    clusters = find_candidate_pairs(
        [cam_close, cam_far], [phone_close, phone_far],
    )

    by_kappa = {c.snapped_kappa_minutes: c for c in clusters}
    assert 0 in by_kappa
    pair_paths = {
        (p.camera_item.path.name, p.phone_item.path.name)
        for p in by_kappa[0].pairs
    }
    assert ("close.rw2", "p_close.jpg") in pair_paths
    # The 6-hour-apart "same hour different time" pair never reaches a
    # bucket — the user can't recognize photos that far apart in clock.
    assert all(name != "p_far.jpg" for _, name in pair_paths)


def test_cross_tz_camera_produces_no_clusters():
    """Trade-off (Nelson 2026-06-18): a camera set to a different TZ than
    the phone means every truly-simultaneous pair is hours apart by clock.
    Those photos depict different scenes (different light, different
    location) and aren't recognizable as "the same moment" — even though
    they're mathematically simultaneous in UTC. The algorithm produces
    nothing; the caller falls back to the manual picker."""
    # Camera at UTC-3 (Brazil home), phone at UTC-9 (Alaska trip). All
    # truly-simultaneous pairs are 6h apart by clock.
    instants_utc = [
        datetime(2025, 5, 12, 16, 0, 0),
        datetime(2025, 5, 12, 17, 30, 0),
        datetime(2025, 5, 12, 19, 0, 0),
    ]
    cams = [_cam(f"c{i}", t - timedelta(hours=3))
            for i, t in enumerate(instants_utc)]
    phones = [_phone(f"p{i}", t - timedelta(hours=9), -540)
              for i, t in enumerate(instants_utc)]

    assert find_candidate_pairs(cams, phones) == []


# ── Tolerance gate (spec/88 §2 step 2) ──────────────────────────────────


def test_adjacent_15_min_offsets_do_not_blur():
    """A pair near a zone boundary snaps to exactly ONE adjacent multiple —
    never both. So distinct κ truths produce distinct clusters and
    adjacent 15-min zones stay separate (spec/88 §2)."""
    inst = datetime(2025, 5, 12, 12, 0, 0)
    cam = _cam("c", inst)
    # Phone shot 6m before — raw κ = +6 min, closer to 0 than to 15 → snap 0.
    p_below = _phone("p_below", inst - timedelta(minutes=6), 0)
    # Phone shot 9m before — raw κ = +9 min, closer to 15 than to 0 → snap 15.
    p_above = _phone("p_above", inst - timedelta(minutes=9), 0)

    clusters = find_candidate_pairs([cam], [p_below, p_above])
    by_kappa = {c.snapped_kappa_minutes: c for c in clusters}

    # Two distinct clusters — adjacent zones stay separate.
    assert set(by_kappa) == {0, 15}
    assert by_kappa[0].size == 1
    assert by_kappa[15].size == 1


def test_pair_exactly_at_zone_midpoint_is_rejected():
    """spec/88 §2: tolerance is tightness ``< ~7.5 min``. A pair whose raw
    κ sits at the exact midpoint between two 15-min multiples is
    ambiguous — snap can't disambiguate it — and it's dropped rather
    than routed arbitrarily."""
    inst = datetime(2025, 5, 12, 12, 0, 0)
    cam = _cam("c", inst)
    # Phone shot 7m30s before → raw κ = +7.5 min, exactly equidistant from
    # 0 and 15. Rejected.
    p_midpoint = _phone(
        "p", inst - timedelta(minutes=7, seconds=30), 0,
    )
    assert find_candidate_pairs([cam], [p_midpoint]) == []


def test_pair_just_inside_tolerance_is_accepted():
    """Just inside the boundary: tightness 7m29s < 7m30s — accepted, and
    snapped to the nearer multiple."""
    inst = datetime(2025, 5, 12, 12, 0, 0)
    cam = _cam("c", inst)
    # Phone shot 7m31s before → raw κ = +7m31s = 7.5166 min, closer to 15
    # than to 0 — tightness = 15 − 7.5166 ≈ 7m29s < tolerance → accepted.
    p_just_in = _phone(
        "p", inst - timedelta(minutes=7, seconds=31), 0,
    )
    clusters = find_candidate_pairs([cam], [p_just_in])
    assert clusters
    assert clusters[0].snapped_kappa_minutes == 15
    assert clusters[0].pairs[0].tightness < TIGHTNESS_TOLERANCE


# ── Ambiguous two-cluster surfacing (spec/88 §4) ─────────────────────────


def test_ambiguous_two_clusters_are_both_surfaced():
    """Two distinct clusters must BOTH be returned so the recognition UI
    can show ambiguity instead of silently picking the slightly-bigger
    pile (spec/88 §4 "ambiguity is surfaced, not hidden")."""
    # Group A: two simultaneous pairs at κ=0 (camera matches the phone).
    cams_a = [
        _cam("a0", datetime(2025, 5, 12, 9, 0, 0)),
        _cam("a1", datetime(2025, 5, 12, 9, 45, 0)),
    ]
    phones_a = [
        _phone("pa0", datetime(2025, 5, 12, 9, 0, 0), 0),
        _phone("pa1", datetime(2025, 5, 12, 9, 45, 0), 0),
    ]
    # Group B: two pairs offset by 10 min (κ=-10 → snaps to -15). Placed
    # far in time so cross-group pairs are filtered by the 15-min gate.
    cams_b = [
        _cam("b0", datetime(2025, 7, 12, 9, 0, 0)),
        _cam("b1", datetime(2025, 7, 12, 9, 45, 0)),
    ]
    phones_b = [
        _phone("pb0", datetime(2025, 7, 12, 9, 10, 0), 0),
        _phone("pb1", datetime(2025, 7, 12, 9, 55, 0), 0),
    ]

    clusters = find_candidate_pairs(cams_a + cams_b, phones_a + phones_b)
    by_kappa = {c.snapped_kappa_minutes: c for c in clusters}

    # Both clusters present.
    assert 0 in by_kappa
    assert -15 in by_kappa
    assert by_kappa[0].size >= 2
    assert by_kappa[-15].size >= 2


# ── No overlap → empty (spec/88 §5 first bullet) ─────────────────────────


def test_no_phone_items_returns_empty():
    """No phone → no normalization is possible → empty result. The caller
    falls back to the manual sync pair picker."""
    cams = [_cam("c0", datetime(2025, 5, 12, 12, 0, 0))]
    assert find_candidate_pairs(cams, []) == []


def test_no_camera_items_returns_empty():
    """No camera-side items to pair against → empty result."""
    phones = [_phone("p0", datetime(2025, 5, 12, 12, 0, 0), 0)]
    assert find_candidate_pairs([], phones) == []


def test_items_without_timestamps_are_skipped():
    """Quarantined items (no readable EXIF timestamp) can't contribute and
    are silently dropped — never crash the candidate finder."""
    cams = [
        _cam("c_good", datetime(2025, 5, 12, 12, 0, 0)),
        SourceItem(path=Path("cam/c_bad.rw2"), timestamp=None,
                   camera_id="G9"),
    ]
    phones = [
        _phone("p_good", datetime(2025, 5, 12, 12, 0, 0), 0),
        SourceItem(path=Path("phone/p_bad.jpg"), timestamp=None,
                   camera_id="iPhone", tz_offset_minutes=0),
    ]
    clusters = find_candidate_pairs(cams, phones)
    assert clusters
    # Only the one good pair is in any bucket.
    assert clusters[0].size == 1


def test_phone_without_tz_offset_is_skipped():
    """A phone item that for some reason lacks ``tz_offset_minutes`` can't
    be normalized — it's silently dropped UNLESS the caller supplied a
    fallback (see test_default_phone_tz_minutes_falls_back below)."""
    cam = _cam("c0", datetime(2025, 5, 12, 12, 0, 0))
    phone_no_tz = SourceItem(
        path=Path("phone/p_no_tz.jpg"),
        timestamp=datetime(2025, 5, 12, 12, 0, 0),
        camera_id="iPhone",
        tz_offset_minutes=None,
    )
    assert find_candidate_pairs([cam], [phone_no_tz]) == []


def test_default_phone_tz_minutes_falls_back_for_phones_without_exif_tz():
    """When phone items lack OffsetTimeOriginal (older iPhones / re-exported
    phones — Nelson 2026-06-18 iPhone 6s case where 0/21 items carried the
    tag), the caller can supply a trip-wide TZ default so the κ-labeling
    still works. The pair-time gate is unchanged (still 15-min)."""
    instants = [
        datetime(2025, 5, 12, 13, 0, 0),
        datetime(2025, 5, 12, 14, 0, 0),
    ]
    cams = [_cam(f"c{i}", t) for i, t in enumerate(instants)]
    phones = [
        SourceItem(
            path=Path(f"phone/p{i}.jpg"),
            timestamp=t,
            camera_id="iPhone 6s",
            tz_offset_minutes=None,        # no EXIF TZ
        )
        for i, t in enumerate(instants)
    ]
    # No fallback → no clusters (the failure mode the user hit).
    assert find_candidate_pairs(cams, phones) == []
    # Fallback supplied → cluster forms at κ = the supplied TZ
    # (-180 here, the trip TZ — camera was set to match).
    clusters = find_candidate_pairs(
        cams, phones, default_phone_tz_minutes=-180,
    )
    assert clusters
    assert clusters[0].snapped_kappa_minutes == -180


def test_default_phone_tz_minutes_yields_to_per_photo_tz_when_present():
    """The default only kicks in when ``tz_offset_minutes`` is None; phone
    items WITH a per-photo TZ keep using their own value."""
    inst = datetime(2025, 5, 12, 13, 0, 0)
    cam = _cam("c", inst)
    phone_with_tz = _phone("p", inst, -180)
    # Pass a different default; per-photo TZ wins, κ snaps -180.
    clusters = find_candidate_pairs(
        [cam], [phone_with_tz], default_phone_tz_minutes=0,
    )
    assert clusters
    assert clusters[0].snapped_kappa_minutes == -180


# ── Outlier robustness ───────────────────────────────────────────────────


def test_outlier_pair_does_not_destroy_a_clean_cluster():
    """A pair that lands wildly off the snap (tightness > tolerance) is
    discarded by the gate — it never reaches a bucket, so it can't
    overwhelm the clean κ cluster. The downstream build_calibration
    re-applies its own median-based rejection on whatever the user confirms."""
    instants = [
        datetime(2025, 5, 12, 10, 0, 0),
        datetime(2025, 5, 12, 11, 0, 0),
        datetime(2025, 5, 12, 12, 0, 0),
        datetime(2025, 5, 12, 13, 0, 0),
    ]
    cams = [_cam(f"c{i}", t) for i, t in enumerate(instants)]
    phones = [_phone(f"p{i}", t, 0) for i, t in enumerate(instants)]
    # An "outlier" pair: a stray phone shot 8m30s off any 15-min multiple.
    cams.append(_cam("c_outlier", datetime(2025, 5, 12, 20, 0, 0)))
    phones.append(_phone(
        "p_outlier", datetime(2025, 5, 12, 20, 8, 30), 0,
    ))

    clusters = find_candidate_pairs(cams, phones)

    # The outlier pair lands at κ=-8.5 min → snaps to 0 OR -15 with
    # tightness 8.5 → discarded by the gate; it is NOT in the κ=0 bucket.
    top = clusters[0]
    assert top.snapped_kappa_minutes == 0
    assert "c_outlier.rw2" not in {p.camera_item.path.name for p in top.pairs}


# ── Ranking inside a cluster (spec/88 §2 ranking) ────────────────────────


def test_ranking_prefers_tightness_then_spread():
    """Pairs of equal tightness are spread across the trip — the first
    ``cards`` returned are at least 30 min apart in camera time."""
    # All these pairs are simultaneous (tightness 0); they differ only in
    # camera time. Two are within 30 min of each other.
    cams = [
        _cam("c0", datetime(2025, 5, 12, 10, 0, 0)),
        _cam("c1", datetime(2025, 5, 12, 10, 5, 0)),  # within gap of c0
        _cam("c2", datetime(2025, 5, 12, 12, 0, 0)),
        _cam("c3", datetime(2025, 5, 12, 14, 0, 0)),
        _cam("c4", datetime(2025, 5, 12, 16, 0, 0)),
    ]
    phones = [
        _phone(f"p{i}", c.timestamp, 0) for i, c in enumerate(cams)
    ]

    clusters = find_candidate_pairs(
        cams, phones, cards_per_cluster=4,
    )
    top = clusters[0]
    # Take just the leading sample of 4 cards.
    leading = list(top.pairs[:4])
    cam_times = sorted(p.camera_item.timestamp for p in leading)
    # Each adjacent pair is at least 30 min apart.
    for a, b in zip(cam_times, cam_times[1:]):
        assert (b - a) >= timedelta(minutes=30), (
            "leading cards should be spread by ≥30 min"
        )


def test_ranking_underfills_gracefully_for_small_clusters():
    """If the spread filter would leave fewer than ``cards`` items (all shots
    inside the 30-min gap), top up from the rest in tightness order — never
    starve the UI when the data is genuinely small (a short trip with all
    shots in one hour)."""
    cams = [
        _cam(f"c{i}", datetime(2025, 5, 12, 10, i, 0))
        for i in range(5)
    ]
    phones = [
        _phone(f"p{i}", c.timestamp, 0) for i, c in enumerate(cams)
    ]
    clusters = find_candidate_pairs(
        cams, phones, cards_per_cluster=6,
    )
    top = clusters[0]
    # 5×5 cross-product all lands in κ=0 (every off is ≤ 4 min, snap=0).
    assert top.snapped_kappa_minutes == 0
    assert top.size == 25
    # The leading sample is the 6 cards the UI shows; the cluster carries
    # the full ranked list past that.
    assert len(top.pairs) == 25
    # Leading sample is tightness-ordered: the 5 diagonals (tightness 0)
    # come first, then off-by-1-min pairs (tightness 1) — never out of order.
    leading_tightness = [p.tightness for p in top.pairs[:6]]
    assert leading_tightness == sorted(leading_tightness)


# ── phone_tz_for override (multi-day TZ map case) ────────────────────────


def test_phone_tz_for_override_replaces_per_photo_default():
    """Callers with a per-day phone TZ map (rather than per-photo EXIF)
    can supply it via ``phone_tz_for`` — the candidate finder uses the
    override and ignores the SourceItem field. The pair itself must
    still pass the 15-min gate."""
    inst = datetime(2025, 5, 12, 13, 0, 0)
    cam = _cam("c", inst)
    # Phone item with NO per-photo tz; the override supplies it. Both
    # photos at the same instant so the pair passes the 15-min gate.
    phone = SourceItem(
        path=Path("phone/p.jpg"),
        timestamp=inst,
        camera_id="iPhone",
        tz_offset_minutes=None,
    )

    clusters = find_candidate_pairs(
        [cam], [phone], phone_tz_for=lambda _it: -180,
    )

    assert clusters
    assert clusters[0].snapped_kappa_minutes == -180


# ── CandidatePair → CalibrationPair conversion ───────────────────────────


def test_to_calibration_pair_carries_raw_timestamps():
    """The handoff to ``build_calibration`` uses raw EXIF timestamps so its
    own median rejection + cross-check sees real numbers (not pre-snapped
    ones). Within the 15-min gate the timestamps are necessarily close —
    use a few-minute delta."""
    cam_t = datetime(2025, 5, 12, 12, 0, 0)
    phone_t = datetime(2025, 5, 12, 12, 5, 0)        # off = +5min
    cam = _cam("c", cam_t)
    phone = _phone("p", phone_t, 0)
    [cluster] = find_candidate_pairs([cam], [phone])
    pair = cluster.pairs[0]
    cal_pair = pair.to_calibration_pair()
    assert cal_pair.camera_time == cam_t
    assert cal_pair.reference_time == phone_t
    assert cal_pair.offset == timedelta(minutes=5)
