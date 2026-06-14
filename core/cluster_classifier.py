"""Unified cluster classifier — spec/52 Quick Sweep redesign.

Quick Sweep wants to lay out one cell per cluster (a bracket, a burst, a
repeat) plus one cell per truly-standalone photo. Today's ``BucketScanResult``
gives us bracket and burst membership directly; what's missing is the
**repeat** layer (the cell-phone "tap-twice" doublet pattern that doesn't
trip continuous-mode detection).

This module is the small, pure-logic helper that stitches the three signals
into one per-item answer:

    classify_clusters(scan_result) → Dict[Path, ClusterAssignment]

where every input photo path lands on exactly one of four kinds:

* ``'bracket'`` — member of a focus or exposure bracket (scanner-detected).
* ``'burst'``   — member of a burst (scanner-detected: BurstUUID for phones,
                  continuous-mode + sequence number / time gap for cameras).
* ``'repeat'``  — member of a repeat run (≥ 2 photos, every consecutive gap
                  within ``RepeatDetectorConfig.window_seconds`` — default 5 s).
* ``'none'``    — none of the above; a standalone item.

Bracket > burst > repeat > none precedence is already implicit in the
scanner's mutually-exclusive output (a photo is in exactly one bucket of
``focus_brackets / exposure_brackets / bursts / individuals``). We don't
re-litigate it here; we just route. The repeat pass runs ONLY over the
``individuals`` list — by construction, anything claimed by a bracket or
burst is already excluded.

**Phone-only repeats (Nelson 2026-06-09).** The repeat layer is restricted
to photos whose EXIF ``Make`` / ``Model`` identify a phone via
:func:`core.phone_detector.is_phone`. Rationale: the "tap-twice doublet"
pattern is fundamentally a phone behavior; rapid camera shutters are
covered by the burst detector (continuous-mode + sequence number).
Camera individuals fall through to ``kind='none'`` even when their
timestamps are tight.

Video items keep their existing scanner treatment — they're never repeats.
Cluster-aware Quick Sweep rendering (slice B) consumes this per-item map.

Pure-logic, no Qt, no I/O. The scanner is the input boundary; the slice-B
UI host is the output boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from core.bucket_scanner import BucketScanResult
from core.phone_detector import is_phone
from core.repeat_detector import (
    RepeatCandidate,
    RepeatDetectorConfig,
    detect_repeats,
)

if TYPE_CHECKING:
    from core.bucket_navigator_model import BucketNode


# The four mutually-exclusive cluster kinds slice B's renderer cares about.
KIND_BRACKET = "bracket"
KIND_BURST = "burst"
KIND_REPEAT = "repeat"
KIND_NONE = "none"

CLUSTER_KINDS = frozenset({KIND_BRACKET, KIND_BURST, KIND_REPEAT, KIND_NONE})


@dataclass(frozen=True)
class ClusterAssignment:
    """One photo's cluster membership.

    ``kind`` is one of ``CLUSTER_KINDS``. ``group_id`` is:

    * the bracket ``sequence_id`` (uuid) for ``kind='bracket'``;
    * the burst ``burst_id`` (uuid for camera bursts, BurstUUID for iPhone) for
      ``kind='burst'``;
    * the repeat ``repeat_id`` (uuid) for ``kind='repeat'``;
    * an empty string ``""`` for ``kind='none'``.

    Two items in the same cluster share the same ``(kind, group_id)``.
    """

    kind: str
    group_id: str


def classify_clusters(
    scan_result: BucketScanResult,
    *,
    repeat_config: Optional[RepeatDetectorConfig] = None,
) -> Dict[Path, ClusterAssignment]:
    """Return the cluster assignment for every photo path the scanner saw.

    Walks the four scanner outputs in spec/52 precedence order — brackets
    first, then bursts, then a repeat-detector pass over individuals,
    then everything left lands as ``'none'``. The result maps every
    distinct path the scanner emitted to exactly one assignment.

    Video items (``scan_result.videos`` + ``motion_clips``) are mapped to
    ``'none'`` here — Quick Sweep treats them as standalone cells, never
    as repeats. Live Photo pairs are already merged into ``individuals``
    by the scanner so they flow through the normal path.
    """
    out: Dict[Path, ClusterAssignment] = {}

    # 1. Brackets (focus + exposure) — scanner already grouped them.
    for seq in scan_result.focus_brackets:
        assignment = ClusterAssignment(kind=KIND_BRACKET, group_id=seq.sequence_id)
        for path in seq.photos:
            out[path] = assignment
    for seq in scan_result.exposure_brackets:
        assignment = ClusterAssignment(kind=KIND_BRACKET, group_id=seq.sequence_id)
        for path in seq.photos:
            out[path] = assignment

    # 2. Bursts (camera continuous-mode + iPhone BurstUUID).
    for burst in scan_result.bursts:
        assignment = ClusterAssignment(kind=KIND_BURST, group_id=burst.burst_id)
        for path in burst.photos:
            out[path] = assignment

    # 3. Repeats — phone-only (Nelson 2026-06-09). Filter individuals
    #    to those whose EXIF Make/Model match a phone maker; camera
    #    individuals fall through to ``kind='none'`` regardless of how
    #    tight their timestamps are (rapid camera shutters belong to
    #    the burst detector). The repeat detector itself stays general;
    #    the phone-only contract is encoded at the input boundary here.
    candidates = [
        RepeatCandidate(path=ind.path, timestamp=ind.timestamp)
        for ind in scan_result.individuals
        if is_phone(ind.make, ind.model)
    ]
    for seq in detect_repeats(candidates, repeat_config):
        assignment = ClusterAssignment(kind=KIND_REPEAT, group_id=seq.repeat_id)
        for path in seq.photos:
            out[path] = assignment

    # 4. Everything the scanner saw but no cluster claimed → 'none'.
    none = ClusterAssignment(kind=KIND_NONE, group_id="")
    for ind in scan_result.individuals:
        out.setdefault(ind.path, none)
    for video in scan_result.videos:
        out.setdefault(video.path, none)
    for clip in scan_result.motion_clips:
        out.setdefault(clip.path, none)

    return out


# --------------------------------------------------------------------------- #
# Repeat-split over flattened BucketNodes — shared between Quick Sweep + Picker
# --------------------------------------------------------------------------- #


# Scanner-emitted node kinds whose member paths can be re-grouped into
# ``repeat`` sub-nodes by :func:`split_repeats_in_nodes`. Cluster kinds
# (burst / *_bracket / video / video_moment) are off-limits — those frames
# already belong to a tighter cluster, per the spec/52 precedence:
# bracket > burst > repeat > none.
_REPEAT_SPLITTABLE_KINDS = frozenset({"individual", "moment"})


def split_repeats_in_nodes(
    nodes: List["BucketNode"],
    assignments: Dict[Path, ClusterAssignment],
) -> List["BucketNode"]:
    """Split individual / moment :class:`BucketNode`s into per-repeat-group
    sub-nodes (spec/52 Quick Sweep slice B — Nelson 2026-06-09).

    Walks ``nodes`` produced by :func:`core.bucket_navigator_model._flatten`;
    for each splittable kind (``individual`` / ``moment``), partitions its
    files by repeat membership (per ``assignments``). Each repeat group
    becomes a new ``BucketNode(kind="repeat", ...)``; the non-repeat
    residue keeps the original kind + title.

    Bracket / burst / video / video_moment nodes pass through unchanged —
    their members already belong to a tighter cluster.

    Pure-logic: no Qt, no gateway, no scanner re-run. Both Quick Sweep
    (:mod:`mira.picked.quick_sweep_buckets`) and the main Picker
    (:mod:`mira.picked.model`) call this so the two surfaces stay
    aligned on repeat-cluster semantics.
    """
    from core.bucket_navigator_model import BucketNode

    out: List[BucketNode] = []
    for node in nodes:
        if node.kind not in _REPEAT_SPLITTABLE_KINDS:
            out.append(node)
            continue
        repeats_by_id: Dict[str, list[Path]] = {}
        non_repeat: list[Path] = []
        for p in node.files:
            a = assignments.get(p)
            if a is not None and a.kind == KIND_REPEAT:
                repeats_by_id.setdefault(a.group_id, []).append(p)
            else:
                non_repeat.append(p)
        if non_repeat:
            out.append(replace(node, files=tuple(non_repeat)))
        for group_id, paths in repeats_by_id.items():
            # Stem the group_id in the title so two repeats of the same
            # size in one day still have distinct bucket_ids downstream.
            out.append(BucketNode(
                kind=KIND_REPEAT,
                bucket_id=f"{node.bucket_id}|repeat|{group_id}",
                title=f"Repeat · {len(paths)} · {group_id[:8]}",
                files=tuple(paths),
                default_state=node.default_state,
                detection_source="",
                camera=node.camera,
            ))
    return out
