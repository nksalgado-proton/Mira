"""Process-phase per-clip refinement overrides (F-029 Step 5b,
Nelson 2026-05-26).

Step 2's ``core/video_marks`` schema defined the editing
decisions made in Cull / Select — clips with stable ids,
start_ms, end_ms, state, label. Process is a **consumer** of
those definitions (Step 5a): it shows the cut the upstream
phase produced and lets the user refine each clip (Mute /
Rotate / Crop combo) before Export materialises the bytes.

This module owns the **refinement overrides** Process layers
on top, keyed by the stable upstream clip id so the user's
choices survive an upstream re-trim cleanly. The schema lives
under ``journal["video_overrides"]`` of the per-bucket Process
journal (``<event>/.process/<safe_bucket_id>/ingest_journal.json``):

    journal["video_overrides"] = {
        "c1": {
            "include_audio": true,
            "rotation_degrees": 90,
            "aspect_ratio_label": "16:9",
            "crop_norm": [0.1, 0.1, 0.8, 0.8]
        },
        "c3": {
            "include_audio": false
        },
        ...
    }

Every field is optional — a missing field inherits the
ClipRange default (``include_audio=True``, ``rotation_degrees=0``,
``aspect_ratio_label=ORIGINAL_LABEL``, ``crop_norm=None``). An
override entry with no fields at all is functionally a no-op but
not erased by the schema (``set_override`` only deletes entries
explicitly via ``remove_override`` / ``prune_overrides``).

The merge primitive :func:`apply_override` is what the Process
Export uses to assemble the final ClipRange for the encoder.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from core.aspect_ratio import ORIGINAL_LABEL
from core.photo_render import Params
from core.video_session import ClipRange

log = logging.getLogger(__name__)


OVERRIDES_KEY = "video_overrides"


@dataclass(frozen=True)
class VideoOverride:
    """Read-only view of one override entry. Construct via
    :func:`get_override` / :func:`list_overrides` — callers
    shouldn't build these directly.

    The first four fields (geometry + audio-presence) are the F-029
    Step 5b set merged into a :class:`~core.video_session.ClipRange` by
    :func:`apply_override`. The rest are the docs/26 §8 video-in-Process
    refinements — colour (the per-clip representative-frame look), the
    Box-Rotation angle + style + auto flag that produced it, the
    representative-frame position, and the video-only temporal tools
    (minimal trim deltas, audio volume/fade, speed, stabilisation). They
    are NOT part of ClipRange — the Phase-4 ffmpeg engine reads them
    directly off the override.
    """

    clip_id: str
    include_audio: Optional[bool] = None
    rotation_degrees: Optional[int] = None
    aspect_ratio_label: Optional[str] = None
    crop_norm: Optional[tuple[float, float, float, float]] = None
    # docs/26 §8 — per-clip colour/crop look (from the rep frame).
    params: Optional[Params] = None
    style: Optional[str] = None
    auto_on: Optional[bool] = None
    box_angle: Optional[float] = None
    rep_frame_ms: Optional[int] = None
    # docs/26 §3 — video-only temporal tools.
    trim_start_delta_ms: Optional[int] = None
    trim_end_delta_ms: Optional[int] = None
    audio_volume: Optional[float] = None              # 1.0 = unchanged
    audio_fade_ms: Optional[int] = None               # in+out fade duration
    speed: Optional[float] = None                     # 1.0 = normal
    stabilise: Optional[float] = None                 # 0 = off; strength

    @property
    def has_adjustment(self) -> bool:
        """True when the clip carries a colour/crop adjustment (a
        representative-frame edit was kept)."""
        return self.params is not None or self.rep_frame_ms is not None


# ── Schema parsing ────────────────────────────────────────────


def get_override(
    journal: dict, clip_id: str,
) -> Optional[VideoOverride]:
    """Return the override for ``clip_id`` or ``None``. Malformed
    entries (non-dict body, unparseable fields) return ``None``
    rather than raise — keeps the journal forward-compatible."""
    raw = journal.get(OVERRIDES_KEY) or {}
    if not isinstance(raw, dict):
        return None
    entry = raw.get(clip_id)
    if not isinstance(entry, dict):
        return None
    return _parse_override(clip_id, entry)


def list_overrides(journal: dict) -> list[VideoOverride]:
    """Parse every override in the journal, dropping malformed
    entries. Order is not specified."""
    raw = journal.get(OVERRIDES_KEY) or {}
    if not isinstance(raw, dict):
        return []
    out: list[VideoOverride] = []
    for clip_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        parsed = _parse_override(str(clip_id), entry)
        if parsed is not None:
            out.append(parsed)
    return out


def _parse_override(
    clip_id: str, entry: dict,
) -> Optional[VideoOverride]:
    if not clip_id:
        return None
    include_audio: Optional[bool] = None
    rotation_degrees: Optional[int] = None
    aspect_ratio_label: Optional[str] = None
    crop_norm: Optional[tuple[float, float, float, float]] = None

    raw_ia = entry.get("include_audio")
    if isinstance(raw_ia, bool):
        include_audio = raw_ia

    raw_rot = entry.get("rotation_degrees")
    if isinstance(raw_rot, (int, float)) and not isinstance(raw_rot, bool):
        rotation_degrees = int(raw_rot) % 360

    raw_label = entry.get("aspect_ratio_label")
    if isinstance(raw_label, str) and raw_label:
        aspect_ratio_label = raw_label

    raw_crop = entry.get("crop_norm")
    if isinstance(raw_crop, (list, tuple)) and len(raw_crop) == 4:
        try:
            crop_norm = tuple(float(v) for v in raw_crop)  # type: ignore[assignment]
        except (TypeError, ValueError):
            crop_norm = None

    # docs/26 §8 colour/crop look + temporal tools.
    params: Optional[Params] = None
    raw_params = entry.get("params")
    if isinstance(raw_params, dict):
        fields = {f.name for f in dataclasses.fields(Params)}
        kw = {k: float(v) for k, v in raw_params.items()
              if k in fields and isinstance(v, (int, float))
              and not isinstance(v, bool)}
        if kw:
            params = Params(**kw)

    style = entry.get("style") if isinstance(entry.get("style"), str) else None

    auto_on = entry.get("auto_on")
    auto_on = auto_on if isinstance(auto_on, bool) else None

    box_angle = _as_float(entry.get("box_angle"))
    rep_frame_ms = _as_int(entry.get("rep_frame_ms"))
    trim_start_delta_ms = _as_int(entry.get("trim_start_delta_ms"))
    trim_end_delta_ms = _as_int(entry.get("trim_end_delta_ms"))
    audio_volume = _as_float(entry.get("audio_volume"))
    audio_fade_ms = _as_int(entry.get("audio_fade_ms"))
    speed = _as_float(entry.get("speed"))
    stabilise = _as_float(entry.get("stabilise"))

    return VideoOverride(
        clip_id=clip_id,
        include_audio=include_audio,
        rotation_degrees=rotation_degrees,
        aspect_ratio_label=aspect_ratio_label,
        crop_norm=crop_norm,
        params=params,
        style=style,
        auto_on=auto_on,
        box_angle=box_angle,
        rep_frame_ms=rep_frame_ms,
        trim_start_delta_ms=trim_start_delta_ms,
        trim_end_delta_ms=trim_end_delta_ms,
        audio_volume=audio_volume,
        audio_fade_ms=audio_fade_ms,
        speed=speed,
        stabilise=stabilise,
    )


def _as_float(v) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _as_int(v) -> Optional[int]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    return None


# ── Mutators ─────────────────────────────────────────────────


def set_override(
    journal: dict, clip_id: str,
    *,
    include_audio: Optional[bool] = None,
    rotation_degrees: Optional[int] = None,
    aspect_ratio_label: Optional[str] = None,
    crop_norm: Optional[tuple[float, float, float, float]] = None,
    clear_crop_norm: bool = False,
    params: Optional[Params] = None,
    style: Optional[str] = None,
    auto_on: Optional[bool] = None,
    box_angle: Optional[float] = None,
    rep_frame_ms: Optional[int] = None,
    trim_start_delta_ms: Optional[int] = None,
    trim_end_delta_ms: Optional[int] = None,
    audio_volume: Optional[float] = None,
    audio_fade_ms: Optional[int] = None,
    speed: Optional[float] = None,
    stabilise: Optional[float] = None,
) -> VideoOverride:
    """Partial in-place update of ``clip_id``'s override entry.

    Pass an explicit value to set/replace a field; ``None`` leaves
    the existing value (or absence) alone. ``clear_crop_norm=True``
    resets ``crop_norm`` to ``None`` regardless of the ``crop_norm``
    kwarg — mirrors :meth:`core.video_session.VideoSession.update_clip`'s
    semantic so callers don't have to switch idioms across the two
    persistence layers.

    Returns the updated :class:`VideoOverride`. Mutates the journal
    dict in place — caller persists via
    :func:`core.ingest_session.save_ingest_journal` (the per-bucket
    journal's standard write path).
    """
    if not clip_id:
        raise ValueError("clip_id must be non-empty")
    overrides = journal.setdefault(OVERRIDES_KEY, {})
    if not isinstance(overrides, dict):
        overrides = {}
        journal[OVERRIDES_KEY] = overrides
    entry = overrides.setdefault(clip_id, {})
    if not isinstance(entry, dict):
        entry = {}
        overrides[clip_id] = entry

    if include_audio is not None:
        entry["include_audio"] = bool(include_audio)
    if rotation_degrees is not None:
        entry["rotation_degrees"] = int(rotation_degrees) % 360
    if aspect_ratio_label is not None:
        entry["aspect_ratio_label"] = str(aspect_ratio_label)
    if clear_crop_norm:
        entry["crop_norm"] = None
    elif crop_norm is not None:
        entry["crop_norm"] = [float(v) for v in crop_norm]

    # docs/26 §8 colour/crop look + temporal tools.
    if params is not None:
        entry["params"] = {
            k: float(v) for k, v in dataclasses.asdict(params).items()}
    if style is not None:
        entry["style"] = str(style)
    if auto_on is not None:
        entry["auto_on"] = bool(auto_on)
    if box_angle is not None:
        entry["box_angle"] = float(box_angle)
    if rep_frame_ms is not None:
        entry["rep_frame_ms"] = int(rep_frame_ms)
    if trim_start_delta_ms is not None:
        entry["trim_start_delta_ms"] = int(trim_start_delta_ms)
    if trim_end_delta_ms is not None:
        entry["trim_end_delta_ms"] = int(trim_end_delta_ms)
    if audio_volume is not None:
        entry["audio_volume"] = float(audio_volume)
    if audio_fade_ms is not None:
        entry["audio_fade_ms"] = int(audio_fade_ms)
    if speed is not None:
        entry["speed"] = float(speed)
    if stabilise is not None:
        entry["stabilise"] = float(stabilise)

    parsed = _parse_override(clip_id, entry)
    # Parse can return None only for an empty clip_id — guarded above.
    assert parsed is not None
    return parsed


def remove_override(journal: dict, clip_id: str) -> bool:
    """Drop ``clip_id``'s override. Returns ``True`` iff removed."""
    raw = journal.get(OVERRIDES_KEY)
    if not isinstance(raw, dict):
        return False
    if clip_id not in raw:
        return False
    del raw[clip_id]
    return True


def prune_overrides(
    journal: dict, valid_clip_ids: Iterable[str],
) -> int:
    """Drop every override whose ``clip_id`` is not in
    ``valid_clip_ids``. Called at Process seed-time to retire stale
    overrides for clips the upstream phase has since removed.
    Returns the count removed."""
    raw = journal.get(OVERRIDES_KEY)
    if not isinstance(raw, dict):
        return 0
    valid = set(valid_clip_ids)
    stale = [cid for cid in raw if cid not in valid]
    for cid in stale:
        del raw[cid]
    return len(stale)


# ── Merge with ClipRange ─────────────────────────────────────


def apply_override(
    clip: ClipRange, override: VideoOverride,
) -> None:
    """Apply ``override``'s non-None fields to ``clip`` in place.
    None fields are left alone — they inherit the existing
    ClipRange value (which is either upstream-seeded or the
    ClipRange dataclass default).

    Used by VideoCullPage's process-mode seed (replays overrides
    onto freshly seeded ClipRanges) AND by Process Export when it
    assembles the final encoder input."""
    if override.include_audio is not None:
        clip.include_audio = bool(override.include_audio)
    if override.rotation_degrees is not None:
        clip.rotation_degrees = int(override.rotation_degrees) % 360
    if override.aspect_ratio_label is not None:
        clip.aspect_ratio_label = str(override.aspect_ratio_label) or ORIGINAL_LABEL
    if override.crop_norm is not None:
        clip.crop_norm = tuple(float(v) for v in override.crop_norm)  # type: ignore[assignment]
