"""``EventDocument`` ⇄ ``event.json`` (spec/30 §2; backup + migration intermediate).

``event.json`` is the durable, human-readable backup format, the migration intermediate,
and the test-fixture shape — all the same serialisation. This module is pure (no SQLite):
it converts the flat :class:`EventDocument` to a nested JSON dict and back. The nesting
(each item's ``phase_state`` / ``adjustment`` / ``video_adjustment`` / ``video_segment``
/ ``video_snapshot``; each source video's ``video_markers``; members under each stack)
lives *here only*; the store and the dataclasses stay flat.

cut + cut_member (spec/61 — definitions + FILE-based membership referencing lineage)
and photo_person (M:N people links) serialize flat at the top level — none of them fit
the "nested under each item" pattern the legacy share_tag (1:1) used.

In the relational-core model a segment/snapshot is its **own item** (a child of its
source video via ``parent_item_id``), so it appears as a top-level entry in ``items``
carrying its 1:1 satellite (``video_segment`` / ``video_snapshot``) + its adjustment —
not nested inside the parent video. The source video itself carries its markers as a
``video_markers`` list (spec/56 — segments derive from marker order at read time).

**Restore and migration share :func:`from_json`** (charter §4 steps 2–5): a backup restore
and a migration extractor both produce this dict, which becomes an ``EventDocument``,
which the repo writes to a fresh ``event.db``. One reader.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, fields
from typing import Any, Dict, List

from mira.store import models as m
from mira.store.schema import SCHEMA_VERSION


def _without(d: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    """Copy ``d`` dropping the named (implied-by-nesting) keys."""
    return {k: v for k, v in d.items() if k not in keys}


# --------------------------------------------------------------------------- #
# EventDocument -> nested dict
# --------------------------------------------------------------------------- #


def to_json(doc: m.EventDocument) -> Dict[str, Any]:
    """Serialise an :class:`EventDocument` to the nested ``event.json`` dict."""
    # Index the per-item satellites by their owning item id.
    phase_by_item: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for ps in doc.phase_states:
        phase_by_item[ps.item_id][ps.phase] = _without(asdict(ps), "item_id", "phase")

    adj_by_item = {a.item_id: _without(asdict(a), "item_id") for a in doc.adjustments}
    vadj_by_item = {v.item_id: _without(asdict(v), "item_id") for v in doc.video_adjustments}
    seg_by_item = {s.item_id: _without(asdict(s), "item_id") for s in doc.video_segments}
    snap_by_item = {s.item_id: _without(asdict(s), "item_id") for s in doc.video_snapshots}
    markers_by_video: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for mk in doc.video_markers:
        markers_by_video[mk.video_item_id].append(_without(asdict(mk), "video_item_id"))

    items_json: List[Dict[str, Any]] = []
    for it in doc.items:
        idict = asdict(it)
        idict["phase_state"] = phase_by_item.get(it.id, {})
        idict["adjustment"] = adj_by_item.get(it.id)
        idict["video_adjustment"] = vadj_by_item.get(it.id)
        idict["video_segment"] = seg_by_item.get(it.id)
        idict["video_snapshot"] = snap_by_item.get(it.id)
        idict["video_markers"] = markers_by_video.get(it.id, [])
        items_json.append(idict)

    members_by_bracket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for sm in doc.stack_members:
        members_by_bracket[sm.bracket_id].append(_without(asdict(sm), "bracket_id"))
    stacks_json = []
    for br in doc.stacks:
        bdict = asdict(br)
        bdict["members"] = members_by_bracket.get(br.bracket_id, [])
        stacks_json.append(bdict)

    return {
        "schema_version": SCHEMA_VERSION,
        "event": asdict(doc.event),
        "trip_days": [asdict(x) for x in doc.trip_days],
        "cameras": [asdict(x) for x in doc.cameras],
        "camera_calibration_pairs": [asdict(x) for x in doc.camera_calibration_pairs],
        "camera_day_tz": [asdict(x) for x in doc.camera_day_tz],
        "items": items_json,
        "buckets": [asdict(x) for x in doc.buckets],
        "item_visits": [asdict(x) for x in doc.item_visits],
        "stacks": stacks_json,
        "cuts": [asdict(x) for x in doc.cuts],
        "cut_members": [asdict(x) for x in doc.cut_members],
        "photo_persons": [asdict(x) for x in doc.photo_persons],
        "lineage": [asdict(x) for x in doc.lineage],
    }


# --------------------------------------------------------------------------- #
# nested dict -> EventDocument
# --------------------------------------------------------------------------- #


def _build(cls, d: Dict[str, Any], **implied: Any):
    """Construct dataclass ``cls`` from ``d`` plus any implied-by-nesting keys.

    Tolerant of missing optional keys and ignores unknown keys, so an older or partial
    backup still loads (charter §5.3 — tolerate, don't crash).
    """
    valid = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in d.items() if k in valid}
    kwargs.update(implied)
    return cls(**kwargs)


def from_json(data: Dict[str, Any]) -> m.EventDocument:
    """Parse a nested ``event.json`` dict into a flat :class:`EventDocument`.

    Shared by restore and migration. Tolerant: missing top-level lists default to
    empty, missing optional fields fall back to dataclass defaults.
    """
    doc = m.EventDocument(event=_build(m.Event, data["event"]))

    doc.trip_days = [_build(m.TripDay, x) for x in data.get("trip_days", [])]
    doc.cameras = [_build(m.Camera, x) for x in data.get("cameras", [])]
    doc.camera_calibration_pairs = [
        _build(m.CameraCalibrationPair, x) for x in data.get("camera_calibration_pairs", [])
    ]
    doc.camera_day_tz = [
        _build(m.CameraDayTz, x) for x in data.get("camera_day_tz", [])
    ]
    doc.buckets = [_build(m.Bucket, x) for x in data.get("buckets", [])]
    doc.item_visits = [_build(m.ItemVisit, x) for x in data.get("item_visits", [])]
    doc.cuts = [_build(m.Cut, x) for x in data.get("cuts", [])]
    doc.cut_members = [_build(m.CutMember, x) for x in data.get("cut_members", [])]
    doc.photo_persons = [_build(m.PhotoPerson, x) for x in data.get("photo_persons", [])]
    doc.lineage = [_build(m.Lineage, x) for x in data.get("lineage", [])]

    for idict in data.get("items", []):
        doc.items.append(_build(m.Item, idict))
        item_id = idict["id"]

        for phase, ps in (idict.get("phase_state") or {}).items():
            doc.phase_states.append(_build(m.PhaseState, ps, item_id=item_id, phase=phase))

        adj = idict.get("adjustment")
        if adj is not None:
            doc.adjustments.append(_build(m.Adjustment, adj, item_id=item_id))

        vadj = idict.get("video_adjustment")
        if vadj is not None:
            doc.video_adjustments.append(_build(m.VideoAdjustment, vadj, item_id=item_id))

        seg = idict.get("video_segment")
        if seg is not None:
            doc.video_segments.append(_build(m.VideoSegment, seg, item_id=item_id))

        snap = idict.get("video_snapshot")
        if snap is not None:
            doc.video_snapshots.append(_build(m.VideoSnapshot, snap, item_id=item_id))

        for mk in idict.get("video_markers") or []:
            doc.video_markers.append(_build(m.VideoMarker, mk, video_item_id=item_id))

    for bdict in data.get("stacks", []):
        doc.stacks.append(_build(m.StackBracket, bdict))
        for sm in bdict.get("members", []):
            doc.stack_members.append(_build(m.StackMember, sm, bracket_id=bdict["bracket_id"]))

    return doc
