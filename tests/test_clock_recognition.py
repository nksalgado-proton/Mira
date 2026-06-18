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
    # Construct a setup where two clusters end up the same size: one at
    # κ=0 and one at κ=-60. Place them in distinct time windows so the
    # cross-products don't blur the counts.
    cams_a = [_cam(f"a{i}", datetime(2025, 5, 12, 10 + i, 0, 0))
              for i in range(2)]
    phones_a = [_phone(f"pa{i}", datetime(2025, 5, 12, 10 + i, 0, 0), 0)
                for i in range(2)]
    # κ=-60 cluster, placed days later so its cross with set A is far away
    # (huge off → snaps to a wildly-off κ, still tightness 0, but lands in
    # its own bucket and doesn't pollute the 0 or -60 buckets).
    cams_b = [_cam(f"b{i}", datetime(2025, 6, 12, 10 + i, 0, 0))
              for i in range(2)]
    phones_b = [_phone(f"pb{i}", datetime(2025, 6, 12, 11 + i, 0, 0), 0)
                for i in range(2)]
    clusters = find_candidate_pairs(cams_a + cams_b, phones_a + phones_b)

    by_kappa = {c.snapped_kappa_minutes: c for c in clusters}
    assert 0 in by_kappa
    assert -60 in by_kappa
    # The κ=0 cluster has size 4 (cams_a × phones_a). The κ=-60 cluster
    # has size 4 too (cams_b × phones_b). Tie → κ=0 wins.
    assert by_kappa[0].size == by_kappa[-60].size
    assert clusters[0].snapped_kappa_minutes == 0


# ── Cross-zone normalization (spec/88 §2 step 1) ─────────────────────────


def test_cross_zone_normalization_preserves_cluster():
    """Camera at constant set-TZ = -180; phone moved through -180 on day 1
    and -120 on day 2. Without normalization the clusters would split; with
    κ = phone_tz − off they fall in one pile (the spec's whole point)."""
    inst_day1 = datetime(2025, 5, 12, 13, 0, 0)
    inst_day2 = datetime(2025, 5, 13, 14, 0, 0)
    # Camera (set to -180) records T - 3h.
    cams = [
        _cam("c1", inst_day1 - timedelta(hours=3)),
        _cam("c2", inst_day2 - timedelta(hours=3)),
    ]
    # Phone day 1 at -180 → records T - 3h, tz=-180.
    # Phone day 2 at -120 → records T - 2h, tz=-120.
    phones = [
        _phone("p1", inst_day1 - timedelta(hours=3), -180),
        _phone("p2", inst_day2 - timedelta(hours=2), -120),
    ]

    clusters = find_candidate_pairs(cams, phones)

    by_kappa = {c.snapped_kappa_minutes: c for c in clusters}
    assert -180 in by_kappa, (
        f"normalization should land both pairs at κ=-180; got {list(by_kappa)}"
    )
    top = by_kappa[-180]
    # The two diagonal pairs are simultaneous (tightness=0).
    diagonals = [
        (p.camera_item.path.name, p.phone_item.path.name)
        for p in top.pairs if p.tightness == timedelta(0)
    ]
    assert ("c1.rw2", "p1.jpg") in diagonals
    assert ("c2.rw2", "p2.jpg") in diagonals


def test_cross_zone_without_normalization_would_split():
    """Sanity: prove the normalization matters. With cross-zone phone TZs,
    raw ``off`` clusters by day; normalized κ clusters by camera. Verified
    by constructing a contrived case and asserting the normalized result
    has a single bucket of size ≥ 2 (the two diagonals)."""
    # Same setup as the previous test — the raw offs are 0 (day 1) and
    # +1h (day 2), which would otherwise produce two κ-on-off clusters.
    inst_day1 = datetime(2025, 5, 12, 13, 0, 0)
    inst_day2 = datetime(2025, 5, 13, 14, 0, 0)
    cams = [
        _cam("c1", inst_day1 - timedelta(hours=3)),
        _cam("c2", inst_day2 - timedelta(hours=3)),
    ]
    phones = [
        _phone("p1", inst_day1 - timedelta(hours=3), -180),
        _phone("p2", inst_day2 - timedelta(hours=2), -120),
    ]

    clusters = find_candidate_pairs(cams, phones)

    # The two simultaneous pairs sit in the SAME normalized bucket — that
    # bucket has at least size 2, and both diagonals are in it.
    top = clusters[0]
    diagonal_names = {
        (p.camera_item.path.name, p.phone_item.path.name)
        for p in top.pairs if p.tightness == timedelta(0)
    }
    assert {("c1.rw2", "p1.jpg"), ("c2.rw2", "p2.jpg")}.issubset(
        diagonal_names
    )


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
    # Group A: two simultaneous pairs at κ=-180 (camera set to -180).
    cams_a = [
        _cam("a0", datetime(2025, 5, 12, 9, 0, 0)),
        _cam("a1", datetime(2025, 5, 12, 9, 45, 0)),
    ]
    phones_a = [
        _phone("pa0", datetime(2025, 5, 12, 12, 0, 0), 0),
        _phone("pa1", datetime(2025, 5, 12, 12, 45, 0), 0),
    ]
    # Group B: two simultaneous pairs at κ=-120 (a *different* camera, but
    # the proposer doesn't know that — they pollute the κ=-120 bucket).
    # Placed far enough in time that cross-group pairs land in unrelated
    # buckets.
    cams_b = [
        _cam("b0", datetime(2025, 7, 12, 9, 0, 0)),
        _cam("b1", datetime(2025, 7, 12, 9, 45, 0)),
    ]
    phones_b = [
        _phone("pb0", datetime(2025, 7, 12, 11, 0, 0), 0),
        _phone("pb1", datetime(2025, 7, 12, 11, 45, 0), 0),
    ]

    clusters = find_candidate_pairs(cams_a + cams_b, phones_a + phones_b)
    by_kappa = {c.snapped_kappa_minutes: c for c in clusters}

    # Both clusters present.
    assert -180 in by_kappa
    assert -120 in by_kappa
    # Both have at least the diagonal pairs.
    assert by_kappa[-180].size >= 2
    assert by_kappa[-120].size >= 2


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
    """Spec/88 §2: "phone_tz is constant and clustering on off directly is
    equivalent" — when phone items lack OffsetTimeOriginal (older iPhones
    / re-exported phones, Nelson 2026-06-18: iPhone 6s case where 0/21
    items carried the tag), the caller can supply a trip-wide default so
    clusters still form."""
    instants = [
        datetime(2025, 5, 12, 13, 0, 0),
        datetime(2025, 5, 12, 14, 0, 0),
    ]
    cams = [_cam(f"c{i}", t - timedelta(hours=3))
            for i, t in enumerate(instants)]
    phones = [
        SourceItem(
            path=Path(f"phone/p{i}.jpg"),
            timestamp=t - timedelta(hours=3),
            camera_id="iPhone 6s",
            tz_offset_minutes=None,        # no EXIF TZ
        )
        for i, t in enumerate(instants)
    ]
    # No fallback → no clusters (the failure mode the user hit).
    assert find_candidate_pairs(cams, phones) == []
    # Fallback supplied → clusters form, snapping to the camera's set TZ.
    clusters = find_candidate_pairs(
        cams, phones, default_phone_tz_minutes=-180,
    )
    assert clusters
    assert clusters[0].snapped_kappa_minutes == -180


def test_default_phone_tz_minutes_yields_to_per_photo_tz_when_present():
    """The default only kicks in when ``tz_offset_minutes`` is None; phone
    items WITH a per-photo TZ keep using their own value (modern
    iPhones with mixed travel days)."""
    inst = datetime(2025, 5, 12, 13, 0, 0)
    cam = _cam("c", inst - timedelta(hours=3))
    phone_with_tz = _phone("p", inst - timedelta(hours=3), -180)
    # Pass a different default; per-photo TZ wins, κ still snaps -180.
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
    override and ignores the SourceItem field."""
    inst = datetime(2025, 5, 12, 13, 0, 0)
    cam = _cam("c", inst - timedelta(hours=3))
    # Build a phone item with NO per-photo tz; the override supplies it.
    phone = SourceItem(
        path=Path("phone/p.jpg"),
        timestamp=inst - timedelta(hours=3),
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
    ones)."""
    cam_t = datetime(2025, 5, 12, 10, 0, 0)
    phone_t = datetime(2025, 5, 12, 13, 0, 0)  # off = +3h, κ = -180
    cam = _cam("c", cam_t)
    phone = _phone("p", phone_t, 0)
    [cluster] = find_candidate_pairs([cam], [phone])
    pair = cluster.pairs[0]
    cal_pair = pair.to_calibration_pair()
    assert cal_pair.camera_time == cam_t
    assert cal_pair.reference_time == phone_t
    assert cal_pair.offset == timedelta(hours=3)
