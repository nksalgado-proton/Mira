"""The spec/56 slice-4 walker — picked segments → ClipUnits."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.edit_export_walker import build_clip_units


@dataclass
class _VideoItem:
    id: str
    origin_relpath: str


@dataclass
class _Marker:
    at_ms: int


@dataclass
class _VAdj:
    look: str = "natural"
    style: Optional[str] = None
    creative_filter: Optional[str] = None
    include_audio: bool = True
    audio_volume: float = 1.0
    audio_fade_ms: int = 0
    speed: float = 1.0
    stabilise: float = 0.0


@dataclass
class _SegRow:
    item_id: str
    video_item_id: str
    seg_index: int


class _FakeGateway:
    def __init__(self, videos, markers, duration, adjustments):
        self.videos = videos
        self.markers = markers
        self.duration = duration
        self.adjustments = adjustments

    def item(self, item_id):
        return self.videos.get(item_id)

    def video_markers(self, video_item_id):
        return self.markers.get(video_item_id, [])

    def video_adjustment(self, item_id):
        return self.adjustments.get(item_id)

    # The walker calls item(video).duration_ms — embed it on the video
    # mock at fixture time.


def _make_video(event_root: Path, name: str, duration_ms: int = 60_000):
    # The walker only checks .is_file(); a tiny stub file is fine.
    p = event_root / "Original Media" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"stub")
    return p


def test_picked_segments_become_clip_units(tmp_path):
    event_root = tmp_path
    p = _make_video(event_root, "VID01.MP4")
    video = _VideoItem(id="v1", origin_relpath="Original Media/VID01.MP4")
    video.duration_ms = 60_000
    video.fps = 24.0
    eg = _FakeGateway(
        videos={"v1": video},
        markers={"v1": [_Marker(20_000), _Marker(40_000)]},
        duration={"v1": 60_000},
        adjustments={"seg-0": _VAdj(look="punch", style="wildlife"),
                     "seg-2": _VAdj()},
    )
    seg_rows = [
        _SegRow(item_id="seg-0", video_item_id="v1", seg_index=0),
        _SegRow(item_id="seg-2", video_item_id="v1", seg_index=2),
    ]

    # Override-shim stub matching the workshop's _OverrideShim duck.
    class _Shim:
        def __init__(self, adj, _params):
            self.params = None
            self.crop_norm = None
            self.box_angle = 0.0
            self.trim_start_delta_ms = 0
            self.trim_end_delta_ms = 0
            self.include_audio = bool(adj.include_audio) if adj else True
            self.audio_volume = adj.audio_volume if adj else 1.0
            self.audio_fade_ms = adj.audio_fade_ms if adj else 0
            self.speed = adj.speed if adj else 1.0
            self.stabilise = adj.stabilise if adj else 0.0
            self.filter_recipe = None
            self.filter_amount = 1.0

    units = build_clip_units(
        eg, seg_rows,
        event_root=event_root,
        dest_dir_for_video=lambda v: str(event_root / "Edited Media" / "Dia 1"),
        override_shim=_Shim,
    )

    assert len(units) == 2
    a, b = units
    assert a.unit_id == "seg-0"
    assert a.source == str(p)
    assert a.base_name == "VID01_clip1"
    # Segment 0 = [0, 20_000); markers at 20k and 40k.
    assert a.plan["in_ms"] == 0
    assert a.plan["out_ms"] == 20_000
    assert a.style == "wildlife"

    assert b.unit_id == "seg-2"
    assert b.base_name == "VID01_clip3"
    assert b.plan["in_ms"] == 40_000
    assert b.plan["out_ms"] == 60_000


def test_missing_source_skipped(tmp_path):
    event_root = tmp_path
    video = _VideoItem(id="v1", origin_relpath="Original Media/GONE.MP4")
    video.duration_ms = 30_000
    video.fps = 24.0
    eg = _FakeGateway(
        videos={"v1": video}, markers={}, duration={"v1": 30_000},
        adjustments={})
    rows = [_SegRow(item_id="seg-0", video_item_id="v1", seg_index=0)]
    out = build_clip_units(
        eg, rows, event_root=event_root,
        dest_dir_for_video=lambda v: str(event_root))
    assert out == []


def test_bad_seg_index_skipped(tmp_path):
    event_root = tmp_path
    _make_video(event_root, "VID.MP4")
    video = _VideoItem(id="v1", origin_relpath="Original Media/VID.MP4")
    video.duration_ms = 10_000
    video.fps = 24.0
    eg = _FakeGateway(
        videos={"v1": video}, markers={"v1": []},  # one segment total
        duration={"v1": 10_000}, adjustments={})
    rows = [_SegRow(item_id="seg-5", video_item_id="v1", seg_index=5)]
    out = build_clip_units(
        eg, rows, event_root=event_root,
        dest_dir_for_video=lambda v: str(event_root))
    assert out == []


def test_missing_video_item_skipped(tmp_path):
    eg = _FakeGateway(videos={}, markers={}, duration={}, adjustments={})
    rows = [_SegRow(item_id="seg", video_item_id="vXX", seg_index=0)]
    out = build_clip_units(
        eg, rows, event_root=tmp_path,
        dest_dir_for_video=lambda v: str(tmp_path))
    assert out == []


def test_plan_dict_round_trips_through_render_worker(tmp_path):
    # The walker's plan dict must hydrate cleanly into ExportPlan in
    # the worker — otherwise a real clip would crash at render time.
    event_root = tmp_path
    _make_video(event_root, "VID.MP4")
    video = _VideoItem(id="v1", origin_relpath="Original Media/VID.MP4")
    video.duration_ms = 30_000
    video.fps = 24.0
    eg = _FakeGateway(
        videos={"v1": video}, markers={"v1": []},
        duration={"v1": 30_000}, adjustments={})
    rows = [_SegRow(item_id="seg-0", video_item_id="v1", seg_index=0)]
    units = build_clip_units(
        eg, rows, event_root=event_root,
        dest_dir_for_video=lambda v: str(event_root))
    assert len(units) == 1
    plan_d = units[0].plan

    from core.photo_render import Params
    from core.video_export import ExportPlan
    plan = ExportPlan(
        in_ms=int(plan_d["in_ms"]),
        out_ms=int(plan_d["out_ms"]),
        params=Params(**plan_d["params"]),
        crop_norm=(tuple(plan_d["crop_norm"])
                   if plan_d["crop_norm"] else None),
        box_angle=float(plan_d["box_angle"]),
        include_audio=bool(plan_d["include_audio"]),
        audio_volume=float(plan_d["audio_volume"]),
        audio_fade_ms=int(plan_d["audio_fade_ms"]),
        speed=float(plan_d["speed"]),
        stabilise=float(plan_d["stabilise"]),
        src_fps=float(plan_d["src_fps"]),
        filter_recipe=plan_d["filter_recipe"],
        filter_amount=float(plan_d["filter_amount"]),
    )
    assert plan.duration_ms == 30_000
    assert plan.params.is_identity is True
