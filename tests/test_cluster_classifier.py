"""Tests for the unified cluster classifier (spec/52 Quick Sweep).

The classifier consumes a ``BucketScanResult`` (the existing scanner's
output shape) and returns one ``ClusterAssignment`` per photo path. The
tests hand-build small ``BucketScanResult`` fixtures so the classifier's
routing logic is isolated from the heavier scanner stack — same pattern
the existing peek/scan_source tests use.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from core.bucket_scanner import (
    BucketScanResult,
    BurstSequence,
    IndividualPhoto,
    SourceKind,
    VideoFile,
)
from core.bracket_detector import BracketSequence
from core.cluster_classifier import (
    CLUSTER_KINDS,
    KIND_BRACKET,
    KIND_BURST,
    KIND_NONE,
    KIND_REPEAT,
    ClusterAssignment,
    classify_clusters,
)
from core.repeat_detector import RepeatDetectorConfig
from core.vocabulary import BracketType


# ─── helpers ─────────────────────────────────────────────────────────────────


def _base_ts() -> datetime:
    return datetime(2026, 6, 9, 11, 0, 0)


def _individual(name: str, *, offset_s: float | None = 0.0) -> IndividualPhoto:
    ts = (_base_ts() + timedelta(seconds=offset_s)) if offset_s is not None else None
    return IndividualPhoto(
        path=Path(name),
        timestamp=ts,
        make="Apple",
        model="iPhone 13",
    )


def _bracket(name_prefix: str, count: int, bracket_type: BracketType) -> BracketSequence:
    return BracketSequence(
        sequence_id=f"seq-{name_prefix}",
        sequence_type=bracket_type,
        photos=[Path(f"{name_prefix}_{i}.RW2") for i in range(count)],
        confidence=0.99,
        detection_source="exif_tag",
        representative_timestamp=_base_ts(),
    )


def _burst(burst_id: str, count: int) -> BurstSequence:
    return BurstSequence(
        burst_id=burst_id,
        photos=[Path(f"burst_{burst_id}_{i}.jpg") for i in range(count)],
        detection_source="burst_uuid",
        representative_timestamp=_base_ts(),
    )


def _empty_result(source_kind: SourceKind = SourceKind.CAMERA) -> BucketScanResult:
    return BucketScanResult(source_kind=source_kind)


# ─── empty input ─────────────────────────────────────────────────────────────


def test_empty_scan_result_returns_empty_map():
    assert classify_clusters(_empty_result()) == {}


# ─── bracket members ─────────────────────────────────────────────────────────


def test_focus_bracket_members_all_get_kind_bracket():
    result = _empty_result()
    seq = _bracket("focusA", 5, BracketType.FOCUS)
    result.focus_brackets.append(seq)

    out = classify_clusters(result)
    assert len(out) == 5
    for path in seq.photos:
        assert out[path].kind == KIND_BRACKET
        assert out[path].group_id == "seq-focusA"


def test_exposure_bracket_members_all_get_kind_bracket():
    result = _empty_result()
    seq = _bracket("expA", 3, BracketType.EXPOSURE)
    result.exposure_brackets.append(seq)

    out = classify_clusters(result)
    assert len(out) == 3
    for path in seq.photos:
        assert out[path].kind == KIND_BRACKET
        assert out[path].group_id == "seq-expA"


def test_two_brackets_share_no_group_id():
    result = _empty_result()
    a = _bracket("A", 3, BracketType.FOCUS)
    b = _bracket("B", 3, BracketType.FOCUS)
    result.focus_brackets.extend([a, b])

    out = classify_clusters(result)
    a_ids = {out[p].group_id for p in a.photos}
    b_ids = {out[p].group_id for p in b.photos}
    assert a_ids == {"seq-A"}
    assert b_ids == {"seq-B"}
    assert a_ids.isdisjoint(b_ids)


# ─── burst members ───────────────────────────────────────────────────────────


def test_burst_members_get_kind_burst_with_burst_id():
    result = _empty_result()
    burst = _burst("buuid-1", 4)
    result.bursts.append(burst)

    out = classify_clusters(result)
    assert len(out) == 4
    for path in burst.photos:
        assert out[path].kind == KIND_BURST
        assert out[path].group_id == "buuid-1"


# ─── repeats (the new layer) ─────────────────────────────────────────────────


def test_two_individuals_within_5s_form_a_repeat():
    """The classic tap-twice case."""
    result = _empty_result()
    a = _individual("IMG_001.jpg", offset_s=0.0)
    b = _individual("IMG_002.jpg", offset_s=1.0)
    result.individuals.extend([a, b])

    out = classify_clusters(result)
    assert out[a.path].kind == KIND_REPEAT
    assert out[b.path].kind == KIND_REPEAT
    assert out[a.path].group_id == out[b.path].group_id
    assert out[a.path].group_id != ""


def test_individuals_outside_window_get_kind_none():
    """30 s apart — not a repeat, not a burst, just two standalone photos."""
    result = _empty_result()
    a = _individual("IMG_001.jpg", offset_s=0.0)
    b = _individual("IMG_002.jpg", offset_s=30.0)
    result.individuals.extend([a, b])

    out = classify_clusters(result)
    assert out[a.path] == ClusterAssignment(kind=KIND_NONE, group_id="")
    assert out[b.path] == ClusterAssignment(kind=KIND_NONE, group_id="")


def test_two_separate_repeat_runs_have_distinct_group_ids():
    result = _empty_result()
    # Run 1
    a = _individual("IMG_001.jpg", offset_s=0.0)
    b = _individual("IMG_002.jpg", offset_s=1.0)
    # Pause
    # Run 2
    c = _individual("IMG_003.jpg", offset_s=60.0)
    d = _individual("IMG_004.jpg", offset_s=61.0)
    result.individuals.extend([a, b, c, d])

    out = classify_clusters(result)
    assert out[a.path].kind == KIND_REPEAT
    assert out[c.path].kind == KIND_REPEAT
    assert out[a.path].group_id == out[b.path].group_id
    assert out[c.path].group_id == out[d.path].group_id
    assert out[a.path].group_id != out[c.path].group_id


def test_individual_without_timestamp_gets_kind_none():
    """No timestamp → can't participate in a repeat run → routes to 'none'."""
    result = _empty_result()
    a = _individual("IMG_NOTS.jpg", offset_s=None)
    result.individuals.append(a)

    out = classify_clusters(result)
    assert out[a.path] == ClusterAssignment(kind=KIND_NONE, group_id="")


# ─── videos always go to 'none' ──────────────────────────────────────────────


def test_videos_get_kind_none():
    result = _empty_result()
    v = VideoFile(path=Path("CLIP.mp4"), timestamp=_base_ts(), duration_s=12.5)
    result.videos.append(v)

    out = classify_clusters(result)
    assert out[v.path] == ClusterAssignment(kind=KIND_NONE, group_id="")


def test_motion_clips_get_kind_none():
    result = _empty_result(SourceKind.PHONE)
    m = VideoFile(path=Path("MOTION.mp4"), timestamp=_base_ts(), duration_s=2.0)
    result.motion_clips.append(m)

    out = classify_clusters(result)
    assert out[m.path] == ClusterAssignment(kind=KIND_NONE, group_id="")


# ─── precedence and mutual exclusion ─────────────────────────────────────────


def test_every_returned_assignment_has_a_valid_kind():
    result = _empty_result()
    result.focus_brackets.append(_bracket("F", 2, BracketType.FOCUS))
    result.exposure_brackets.append(_bracket("E", 2, BracketType.EXPOSURE))
    result.bursts.append(_burst("BURST", 3))
    a = _individual("IMG_01.jpg", offset_s=0.0)
    b = _individual("IMG_02.jpg", offset_s=2.0)         # forms a repeat with a
    c = _individual("IMG_03.jpg", offset_s=120.0)       # alone → 'none'
    result.individuals.extend([a, b, c])
    result.videos.append(VideoFile(path=Path("V.mp4"), timestamp=_base_ts(), duration_s=5.0))

    out = classify_clusters(result)
    for assignment in out.values():
        assert assignment.kind in CLUSTER_KINDS


def test_mixed_scene_yields_one_cluster_kind_per_path():
    """A path appears in exactly one of the scanner's mutually-exclusive
    buckets, so the classifier must emit exactly one assignment per
    path — count matches the scanner total."""
    result = _empty_result()
    result.focus_brackets.append(_bracket("F", 2, BracketType.FOCUS))
    result.bursts.append(_burst("B", 3))
    a = _individual("IMG_01.jpg", offset_s=0.0)
    b = _individual("IMG_02.jpg", offset_s=2.0)
    c = _individual("IMG_03.jpg", offset_s=120.0)
    result.individuals.extend([a, b, c])
    result.videos.append(VideoFile(path=Path("V.mp4"), timestamp=_base_ts(), duration_s=5.0))

    out = classify_clusters(result)
    # 2 bracket + 3 burst + 3 individuals + 1 video = 9 distinct paths
    assert len(out) == 9


def test_repeat_detector_config_passes_through():
    """A tighter window kills a 4-second doublet."""
    result = _empty_result()
    a = _individual("IMG_01.jpg", offset_s=0.0)
    b = _individual("IMG_02.jpg", offset_s=4.0)
    result.individuals.extend([a, b])

    tight_cfg = RepeatDetectorConfig(window_seconds=2.0)
    out = classify_clusters(result, repeat_config=tight_cfg)
    assert out[a.path].kind == KIND_NONE
    assert out[b.path].kind == KIND_NONE


# ─── live-photo-merged path: stills land in individuals, no double counting ──


# ─── phone-only repeats (Nelson 2026-06-09) ──────────────────────────────────


def _camera_individual(name: str, *, offset_s: float = 0.0) -> IndividualPhoto:
    """An individual with camera Make/Model — fails is_phone()."""
    return IndividualPhoto(
        path=Path(name),
        timestamp=_base_ts() + timedelta(seconds=offset_s),
        make="Panasonic",
        model="DC-G9M2",
    )


def test_camera_individuals_do_not_form_a_repeat_even_when_tight():
    """Two camera-EXIF individuals 1 s apart get kind='none' — the
    repeat layer is phone-only. Rapid camera fire belongs to the burst
    detector (continuous-mode), not the repeat detector."""
    result = _empty_result()
    a = _camera_individual("DSC_001.RW2", offset_s=0.0)
    b = _camera_individual("DSC_002.RW2", offset_s=1.0)
    result.individuals.extend([a, b])

    out = classify_clusters(result)
    assert out[a.path].kind == KIND_NONE
    assert out[b.path].kind == KIND_NONE


def test_mixed_phone_and_camera_only_phone_forms_repeat():
    """Phone + camera individuals all 1 s apart in the same scan.
    Only the phone pair forms a repeat; camera pair stays 'none'."""
    result = _empty_result()
    phone_a = _individual("HEIC_A.HEIC", offset_s=0.0)         # Apple / iPhone 13
    phone_b = _individual("HEIC_B.HEIC", offset_s=1.0)
    cam_a = _camera_individual("DSC_A.RW2", offset_s=0.5)
    cam_b = _camera_individual("DSC_B.RW2", offset_s=1.5)
    result.individuals.extend([phone_a, phone_b, cam_a, cam_b])

    out = classify_clusters(result)
    assert out[phone_a.path].kind == KIND_REPEAT
    assert out[phone_b.path].kind == KIND_REPEAT
    assert out[cam_a.path].kind == KIND_NONE
    assert out[cam_b.path].kind == KIND_NONE


def test_unknown_make_falls_through_to_none():
    """An individual with no Make/Model can't be identified as a phone —
    falls through to 'none' even with tight timestamps."""
    result = _empty_result()
    a = IndividualPhoto(
        path=Path("UNKNOWN_A.jpg"),
        timestamp=_base_ts(),
        make="", model="",
    )
    b = IndividualPhoto(
        path=Path("UNKNOWN_B.jpg"),
        timestamp=_base_ts() + timedelta(seconds=1.0),
        make="", model="",
    )
    result.individuals.extend([a, b])

    out = classify_clusters(result)
    assert out[a.path].kind == KIND_NONE
    assert out[b.path].kind == KIND_NONE


def test_live_photo_stills_routed_through_individuals():
    """The phone scanner merges Live Photo stills back into ``individuals``;
    the classifier doesn't need to know — they flow through the normal
    repeat/none routing like any other individual."""
    result = _empty_result(SourceKind.PHONE)
    a = _individual("HEIC_001.HEIC", offset_s=0.0)
    b = _individual("HEIC_002.HEIC", offset_s=2.0)
    result.individuals.extend([a, b])
    result.live_photo_pairs_merged = 2  # diagnostic counter only

    out = classify_clusters(result)
    assert out[a.path].kind == KIND_REPEAT
    assert out[b.path].kind == KIND_REPEAT


# ─── split_repeats_in_nodes — shared between Quick Sweep + PickPage ──────────


def _node(kind: str, bucket_id: str, files: tuple) -> "BucketNode":
    """Build a minimal BucketNode for the splitter tests."""
    from core.bucket_navigator_model import BucketNode, _default_state
    return BucketNode(
        kind=kind,
        bucket_id=bucket_id,
        title=f"{kind.title()} · {len(files)}",
        files=files,
        default_state=_default_state(kind),
        detection_source="",
        camera="iPhone 13",
    )


def test_split_repeats_passes_through_non_splittable_kinds():
    """burst / *_bracket / video nodes — already in tighter clusters —
    pass through the splitter unchanged."""
    from core.cluster_classifier import split_repeats_in_nodes
    nodes = [
        _node("burst", "d|burst|b1", (Path("b_1.jpg"),)),
        _node("focus_bracket", "d|focus_bracket|f1", (Path("f_1.jpg"),)),
        _node("exposure_bracket", "d|exposure_bracket|e1", (Path("e_1.jpg"),)),
        _node("video", "d|video|v1", (Path("v_1.mp4"),)),
    ]
    out = split_repeats_in_nodes(nodes, assignments={})
    assert out == nodes


def test_split_repeats_splits_individuals_into_repeat_sub_nodes():
    """An individual node containing repeat-claimed paths splits into a
    residual individual + one ``repeat`` node per group_id."""
    from core.cluster_classifier import split_repeats_in_nodes
    p_solo = Path("solo.jpg")
    p_r1a, p_r1b = Path("r1a.jpg"), Path("r1b.jpg")
    p_r2a, p_r2b = Path("r2a.jpg"), Path("r2b.jpg")
    nodes = [_node(
        "individual", "d|individual|i1",
        (p_solo, p_r1a, p_r1b, p_r2a, p_r2b),
    )]
    assignments = {
        p_r1a: ClusterAssignment(kind=KIND_REPEAT, group_id="grp-1"),
        p_r1b: ClusterAssignment(kind=KIND_REPEAT, group_id="grp-1"),
        p_r2a: ClusterAssignment(kind=KIND_REPEAT, group_id="grp-2"),
        p_r2b: ClusterAssignment(kind=KIND_REPEAT, group_id="grp-2"),
    }
    out = split_repeats_in_nodes(nodes, assignments)
    kinds = [n.kind for n in out]
    assert kinds.count("individual") == 1
    assert kinds.count("repeat") == 2
    # Residual individual carries only the unclaimed path.
    residual = next(n for n in out if n.kind == "individual")
    assert residual.files == (p_solo,)
    # The two repeat nodes carry the right members.
    by_gid = {
        n.bucket_id.split("|")[-1]: tuple(n.files)
        for n in out if n.kind == "repeat"
    }
    assert by_gid == {
        "grp-1": (p_r1a, p_r1b),
        "grp-2": (p_r2a, p_r2b),
    }


def test_split_repeats_drops_residual_when_every_path_is_claimed():
    """All paths repeat-claimed → no residual individual emitted; only
    the repeat sub-node lands in the output."""
    from core.cluster_classifier import split_repeats_in_nodes
    a, b = Path("a.jpg"), Path("b.jpg")
    nodes = [_node("individual", "d|individual|i1", (a, b))]
    assignments = {
        a: ClusterAssignment(kind=KIND_REPEAT, group_id="grp-1"),
        b: ClusterAssignment(kind=KIND_REPEAT, group_id="grp-1"),
    }
    out = split_repeats_in_nodes(nodes, assignments)
    assert len(out) == 1
    assert out[0].kind == "repeat"
    assert out[0].files == (a, b)


def test_split_repeats_handles_moment_kind_same_as_individual():
    """``moment`` is the other splittable kind — same split logic applies."""
    from core.cluster_classifier import split_repeats_in_nodes
    a, b = Path("a.jpg"), Path("b.jpg")
    nodes = [_node("moment", "d|moment|m1", (a, b))]
    assignments = {
        a: ClusterAssignment(kind=KIND_REPEAT, group_id="grp-1"),
        b: ClusterAssignment(kind=KIND_REPEAT, group_id="grp-1"),
    }
    out = split_repeats_in_nodes(nodes, assignments)
    assert len(out) == 1
    assert out[0].kind == "repeat"


def test_split_repeats_preserves_node_order_for_non_split():
    """Ordering across multiple input nodes is preserved when none split."""
    from core.cluster_classifier import split_repeats_in_nodes
    n1 = _node("burst", "d|burst|b1", (Path("b1.jpg"),))
    n2 = _node("focus_bracket", "d|focus_bracket|f1", (Path("f1.jpg"),))
    n3 = _node("burst", "d|burst|b2", (Path("b2.jpg"),))
    out = split_repeats_in_nodes([n1, n2, n3], assignments={})
    assert out == [n1, n2, n3]
