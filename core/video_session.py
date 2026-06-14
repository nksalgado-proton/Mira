"""Video session — markers + clip / frame export state.

For the Process Videos workflow. Each ``VideoItem`` carries its own
markers (timestamps in ms where the user paused and pressed M) and
a list of clip definitions (start / end / optional crop). Frame
extracts are written immediately and tracked in
``extracted_frame_paths``; clip exports are written immediately
when the user explicitly asks (``Export Clip``).

Crash safety: every state mutation rewrites
``<event>/processed_videos/_video_session.json`` atomically, so
markers survive an Alt-F4. Already-exported clips and frames are on
disk; only the in-flight markers / clip ranges matter to the journal.

This module is Qt-free; the viewer drives it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Optional

from core.aspect_ratio import ORIGINAL_LABEL, get_aspect_ratio
from core.models import Event
from core.path_builder import day_folder_name, extracted_dir
from core.video_discovery import (
    PROCESSED_FOLDER_NAME,
    VideoItem,
)
from core.video_extract import (
    VideoMetadata,
    export_clip,
    extract_frame,
    probe_video,
)

log = logging.getLogger(__name__)


VIDEO_JOURNAL_SCHEMA_VERSION = 1
VIDEO_JOURNAL_FILENAME = "_video_session.json"


@dataclass
class ClipRange:
    """A user-defined clip: start and end timestamps inside a video,
    plus optional crop and audio toggle. The crop rect is normalized
    0-1 over the *post-rotation* video frame, same convention as
    ``ProcessDecision``.

    ``aspect_ratio_label`` defaults to Original (keep source ratio).
    Anything else triggers a centered max crop at export time —
    matches what Process Photos does when ``crop_norm is None``.

    ``include_audio`` toggles between keeping the AAC audio track
    (default) and stripping it entirely (``-an``). Stripping is
    handy for clips where the ambient audio is distracting or
    contains copyrighted music.

    ``rotation_degrees`` rotates the exported clip by a multiple of
    90°. ``0`` (default) keeps the source orientation; ``90`` /
    ``-90`` / ``180`` rotate clockwise / counter-clockwise / 180°.
    Implemented as an FFmpeg ``transpose`` chain at export time so
    the rotation is baked into the output MP4 — the live preview in
    the player still shows the source orientation, which is fine
    for the rotation use case (the user knows what they picked)."""
    start_ms: int
    end_ms: int
    aspect_ratio_label: str = ORIGINAL_LABEL
    crop_norm: Optional[tuple[float, float, float, float]] = None
    include_audio: bool = True
    rotation_degrees: int = 0


@dataclass
class VideoState:
    """Per-video session data — markers + clip ranges + extracted-frame
    timestamps. Tracked even after the user closes the viewer so they
    can resume."""
    markers_ms: list[int] = field(default_factory=list)
    clips: list[ClipRange] = field(default_factory=list)
    # Positions where extracted frames have been written. Used to
    # show a count in the UI without re-walking disk.
    extracted_frame_positions_ms: list[int] = field(default_factory=list)


@dataclass
class FrameExtractOutcome:
    """Result of ``extract_frame_at`` — what was written and where."""
    item: VideoItem
    position_ms: int
    output_path: Path


@dataclass
class ClipExportOutcome:
    """Result of ``export_clip_range`` — what was written and where."""
    item: VideoItem
    clip: ClipRange
    output_path: Path


@dataclass(frozen=True)
class VideoResumeStats:
    """A video clip's resume figure for the navigator (Nelson
    2026-05-18). ``has_entry`` = the user has a persisted session
    for this clip (⇒ opened at least once). ``duration_ms`` is the
    seeded end-marker (0 until first open — no probe). The bucket
    row paints kept_ms/duration_ms in the cull palette + a
    ``Clips: N · Stills: M`` line."""

    has_entry: bool
    duration_ms: int
    kept_ms: int
    clips: int
    stills: int


def video_resume_stats(
    journal_path: Path, video_path: Path,
) -> VideoResumeStats:
    """Pure, never-raises peek of the persisted VideoSession journal
    for one clip — NO VideoSession, NO player, NO ffprobe/decode
    (Speed-is-King; also sidesteps the heavy-4K decode on navigator
    entry). Missing / unreadable / schema-mismatch / no-entry → all
    zeros (⇒ untouched, all-discarded bar)."""
    try:
        if not journal_path.exists():
            return VideoResumeStats(False, 0, 0, 0, 0)
        data = json.loads(journal_path.read_text(encoding="utf-8"))
        if data.get("version") != VIDEO_JOURNAL_SCHEMA_VERSION:
            return VideoResumeStats(False, 0, 0, 0, 0)
        target, tname = str(video_path), Path(video_path).name
        entry = None
        for e in data.get("videos", []):
            ep = str(e.get("path", ""))
            if ep == target or Path(ep).name == tname:
                entry = e
                break
        if entry is None:
            return VideoResumeStats(False, 0, 0, 0, 0)
        markers = [int(m) for m in entry.get("markers_ms", [])]
        clips = [
            c for c in entry.get("clips", [])
            if "start_ms" in c and "end_ms" in c
        ]
        clip_ends = [int(c["end_ms"]) for c in clips]
        duration_ms = max(
            [m for m in markers] + clip_ends + [0]
        )
        kept_ms = sum(
            max(0, int(c["end_ms"]) - int(c["start_ms"])) for c in clips
        )
        if duration_ms > 0:
            kept_ms = min(kept_ms, duration_ms)
        stills = len(entry.get("extracted_frame_positions_ms", []))
        return VideoResumeStats(
            True, duration_ms, kept_ms, len(clips), stills,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return VideoResumeStats(False, 0, 0, 0, 0)


class VideoSession:
    """Owns the state for a list of videos in one (day, source_folder)
    bucket — or for a single standalone video opened via the Video
    Tool sidebar entry. Mirrors ``ProcessSession`` in shape so the
    viewer code can be the same kind of beast.

    Two construction modes:

    * **Event mode** (``event=...``) — the original Process Videos
      flow. ``event_root`` is the event's ``photos_base_path``.
      Frame snaps land in ``<event>/<Dia X>/extracted/`` and clip
      exports in ``<event>/Processed Media/<Dia X>/`` so they
      mix chronologically with the day's other media.
    * **Standalone mode** (``output_dir=...``) — single picked file.
      Both frame snaps and clip exports land flat under
      ``output_dir`` (typically ``<video_dir>/_extracted/``). The
      journal also lives there so resuming a crashed session is
      automatic when the user re-opens the same file.

    Exactly one of ``event`` / ``output_dir`` must be set.

    Lifecycle:
        1. Constructed from a list of ``VideoItem`` plus a mode hint.
           Existing journal restored.
        2. Viewer mutates state via ``add_marker`` / ``remove_marker``
           / ``add_clip`` / ``extract_frame_at`` / ``export_clip_range``.
        3. The two ``*_at`` and ``*_range`` methods write to disk
           immediately and update the journal — frame snaps and clip
           exports survive a crash because the file is the source of
           truth, not the journal.
        4. ``probe_metadata`` caches per-video duration / dimensions
           on first ask so the player can render its timeline without
           paying the FFmpeg startup cost twice.
    """

    def __init__(
        self,
        items: list[VideoItem],
        event: Optional[Event] = None,
        *,
        output_dir: Optional[Path] = None,
        journal_path: Optional[Path] = None,
    ):
        if (event is None) == (output_dir is None):
            raise ValueError(
                "VideoSession needs exactly one of `event` (event-mode) "
                "or `output_dir` (standalone-mode)."
            )
        self.items = list(items)
        self.event = event
        self.standalone_output_dir: Optional[Path] = (
            Path(output_dir) if output_dir is not None else None
        )

        if event is not None:
            if event.photos_base_path:
                self.event_root = Path(event.photos_base_path)
            else:
                self.event_root = Path.cwd()
            # Clip exports share the ``processed/`` folder with
            # Process Photos JPEGs — same per-day layout, mixed
            # media for a chronological readout downstream.
            self.processed_root = self.event_root / PROCESSED_FOLDER_NAME
        else:
            # Standalone: both frame snaps and clip exports go flat
            # under the picked output dir. ``event_root`` stays as
            # the same path so the rest of the class (journal write,
            # logs) has a sane non-None value to print.
            assert self.standalone_output_dir is not None
            self.event_root = self.standalone_output_dir
            self.processed_root = self.standalone_output_dir

        self.journal_path: Path = (
            Path(journal_path)
            if journal_path is not None
            else self.processed_root / VIDEO_JOURNAL_FILENAME
        )

        self._states: dict[Path, VideoState] = {}
        self._items_by_path: dict[Path, VideoItem] = {
            it.path: it for it in items
        }
        self._metadata_cache: dict[Path, VideoMetadata] = {}

        self.load_journal()

    @property
    def is_standalone(self) -> bool:
        """True when the session was constructed in standalone mode
        (no Event, single picked file, flat output layout)."""
        return self.standalone_output_dir is not None

    # ── Per-video state ───────────────────────────────────────────

    def get_state(self, item: VideoItem) -> VideoState:
        existing = self._states.get(item.path)
        if existing is not None:
            return existing
        new = VideoState()
        self._states[item.path] = new
        return new

    def add_marker(self, item: VideoItem, position_ms: int) -> None:
        """Insert a marker if no marker exists at that position. The
        list is kept sorted so the UI can render in order without
        having to sort on every paint."""
        state = self.get_state(item)
        if position_ms in state.markers_ms:
            return
        state.markers_ms.append(position_ms)
        state.markers_ms.sort()
        self.write_journal()

    def remove_marker(self, item: VideoItem, position_ms: int) -> bool:
        """Remove the marker at exactly ``position_ms``. Returns True
        on hit, False on miss — the viewer uses this to know whether
        a near-click landed on a marker or the empty timeline."""
        state = self.get_state(item)
        if position_ms not in state.markers_ms:
            return False
        state.markers_ms.remove(position_ms)
        self.write_journal()
        return True

    def add_clip(self, item: VideoItem, clip: ClipRange) -> None:
        """Stash a clip *range* on the item — does NOT export the
        clip. Use ``export_clip_range`` to actually render. The
        viewer keeps a list of pending clip ranges so the user can
        batch-export them."""
        state = self.get_state(item)
        state.clips.append(clip)
        self.write_journal()

    def remove_clip(self, item: VideoItem, index: int) -> bool:
        state = self.get_state(item)
        if not (0 <= index < len(state.clips)):
            return False
        del state.clips[index]
        self.write_journal()
        return True

    def update_clip(
        self,
        item: VideoItem,
        index: int,
        *,
        include_audio: Optional[bool] = None,
        rotation_degrees: Optional[int] = None,
        aspect_ratio_label: Optional[str] = None,
        crop_norm: Optional[tuple[float, float, float, float]] = None,
        clear_crop_norm: bool = False,
    ) -> Optional[ClipRange]:
        """Partial in-place edit of clip ``index`` for ``item`` (task
        #116 — Process Video mute / rotate toggles; extended for
        task #129 with aspect_ratio_label + crop_norm).

        Pass an explicit value to change a field; ``None`` leaves it
        alone. ``crop_norm`` here is "set or no-op" — to reset the
        crop to "auto-centred for the aspect_ratio_label" pass
        ``clear_crop_norm=True``. Returns the updated ClipRange, or
        ``None`` when the index is out of range. Persists via
        :meth:`write_journal` so the change survives an app restart.
        """
        state = self.get_state(item)
        if not (0 <= index < len(state.clips)):
            return None
        clip = state.clips[index]
        if include_audio is not None:
            clip.include_audio = bool(include_audio)
        if rotation_degrees is not None:
            clip.rotation_degrees = int(rotation_degrees) % 360
        if aspect_ratio_label is not None:
            clip.aspect_ratio_label = str(aspect_ratio_label)
        if clear_crop_norm:
            clip.crop_norm = None
        elif crop_norm is not None:
            clip.crop_norm = tuple(float(v) for v in crop_norm)  # type: ignore[assignment]
        self.write_journal()
        return clip

    # ── FFmpeg-backed operations ─────────────────────────────────

    def probe_metadata(self, item: VideoItem) -> VideoMetadata:
        """Cached probe — first call hits FFmpeg, subsequent ones
        return the cached metadata. The viewer asks for this once
        when opening a video to size the timeline."""
        cached = self._metadata_cache.get(item.path)
        if cached is not None:
            return cached
        meta = probe_video(item.path)
        self._metadata_cache[item.path] = meta
        return meta

    def extract_frame_at(
        self, item: VideoItem, position_ms: int,
        *,
        dest_dir: Optional[Path] = None,
        lineage_id: Optional[str] = None,
    ) -> FrameExtractOutcome:
        """Write a JPEG frame at exact ``position_ms``.

        Destination resolution (precedence):
          1. Explicit ``dest_dir`` argument — used verbatim. The
             Cull/Select shells use this to route frames to the
             per-phase tree (``01 - Culled/<bucket>/<day>/<cam>/<style>/``
             or ``02 - Selected/<day>/<style>/``) per Model 3 v2's
             unified destination model (task #114, Nelson 2026-05-23).
          2. Else event mode → ``extracted_dir(event_root, item.day)``
             (legacy default).
          3. Else standalone → flat ``standalone_output_dir``.

        Filename uses the wall-clock timestamp of the frame so the
        JPEG sorts chronologically when Process Photos picks it up.

        ``lineage_id`` (F-029 Step 6): the upstream snapshot id from
        :mod:`core.video_marks` (typically ``"s1"``, ``"s2"``, …).
        When supplied the filename becomes
        ``<HHMMSS>_<source_stem>_<lineage_id>.jpg`` so the materialised
        JPEG carries its provenance back to the journal entry — Process
        Export uses this to keep the lineage chain readable on disk.
        ``None`` (legacy path) keeps the original
        ``<HHMMSS>_<source_stem>_f<position_ms>.jpg`` shape.
        """
        wall_clock = item.timestamp + timedelta(milliseconds=position_ms)
        if dest_dir is not None:
            target_dir = Path(dest_dir)
        elif self.is_standalone:
            assert self.standalone_output_dir is not None
            target_dir = self.standalone_output_dir
        else:
            assert item.day is not None  # event-mode invariant
            target_dir = extracted_dir(self.event_root, item.day)
        target_dir.mkdir(parents=True, exist_ok=True)
        if lineage_id:
            filename = (
                f"{wall_clock.strftime('%H%M%S')}_{item.path.stem}_{lineage_id}.jpg"
            )
        else:
            filename = (
                f"{wall_clock.strftime('%H%M%S')}_{item.path.stem}_f{position_ms}.jpg"
            )
        dest = target_dir / filename
        extract_frame(item.path, position_ms, dest)

        # docs/24 Step 1 (corrected concept, 2026-05-28): bake
        # DateTimeOriginal into the extracted JPEG so it sorts with
        # neighbour photos when the Select scanner extension folds
        # snapshots into the photo pool. From Select onward a
        # snapshot IS a photo, indistinguishable from camera output;
        # the bake makes that promise hold at the EXIF layer too.
        #
        # The bake is best-effort — a failure here logs and continues;
        # the JPEG still exists with its (possibly missing) EXIF
        # metadata. Done via core.exif_rewriter.rewrite_capture_time
        # so we share the exiftool plumbing the ingest-bake path uses.
        try:
            from core.exif_rewriter import rewrite_capture_time
            rewrite_capture_time(
                dest, wall_clock, preserve_original=False,
            )
        except Exception as exc:                # noqa: BLE001
            log.warning(
                "snapshot EXIF bake failed for %s: %s",
                dest.name, exc,
            )

        state = self.get_state(item)
        if position_ms not in state.extracted_frame_positions_ms:
            state.extracted_frame_positions_ms.append(position_ms)
            state.extracted_frame_positions_ms.sort()
        self.write_journal()
        return FrameExtractOutcome(
            item=item, position_ms=position_ms, output_path=dest,
        )

    def export_clip_range(
        self,
        item: VideoItem,
        clip: ClipRange,
        *,
        crop_pixels: Optional[tuple[int, int, int, int]] = None,
        dest_dir: Optional[Path] = None,
        lineage_id: Optional[str] = None,
    ) -> ClipExportOutcome:
        """Render ``clip`` to disk.

        Destination resolution (precedence):
          1. Explicit ``dest_dir`` argument — used verbatim. The
             Cull/Select shells use this to route clips to the
             per-phase tree (``01 - Culled/<bucket>/<day>/<cam>/<style>/``
             or ``02 - Selected/<day>/<style>/``) per Model 3 v2's
             unified destination model (task #114, Nelson 2026-05-23).
          2. Else event mode → ``<processed_root>/<day_folder>/``
             (legacy default).
          3. Else standalone → flat ``standalone_output_dir``.

        The output filename uses the wall-clock start time of the
        clip so it sorts naturally alongside the day's other media
        (JPEGs from Process Photos and other clips) when downstream
        tools read the day folder chronologically.

        Crop is derived from ``clip.aspect_ratio_label`` (and
        ``clip.crop_norm`` when set) against the probed video
        dimensions — callers can still pass an explicit
        ``crop_pixels`` to override, but the typical UI flow leaves
        that None and lets the session compute the centered max
        crop for the chosen ratio.

        ``lineage_id`` (F-029 Step 6): the upstream clip id from
        :mod:`core.video_marks` (typically ``"c1"``, ``"c2"``, …).
        When supplied the filename becomes
        ``<HHMMSS>_<source_stem>_<lineage_id>.mp4`` so the
        materialised MP4 carries its provenance back to the journal
        entry. ``None`` (legacy path) keeps the original
        ``<HHMMSS>_<source_stem>.mp4`` shape.
        """
        if crop_pixels is None:
            crop_pixels = self._compute_crop_pixels(item, clip)
        wall_clock = item.timestamp + timedelta(milliseconds=clip.start_ms)
        if dest_dir is not None:
            target_dir = Path(dest_dir)
        elif self.is_standalone:
            assert self.standalone_output_dir is not None
            target_dir = self.standalone_output_dir
        else:
            assert item.day is not None  # event-mode invariant
            target_dir = self.processed_root / day_folder_name(item.day)
        target_dir.mkdir(parents=True, exist_ok=True)
        if lineage_id:
            filename = (
                f"{wall_clock.strftime('%H%M%S')}_{item.path.stem}_{lineage_id}.mp4"
            )
        else:
            filename = (
                f"{wall_clock.strftime('%H%M%S')}_{item.path.stem}.mp4"
            )
        dest = _resolve_unique(target_dir, filename)
        export_clip(
            item.path,
            start_ms=clip.start_ms,
            end_ms=clip.end_ms,
            output_path=dest,
            crop_pixels=crop_pixels,
            include_audio=clip.include_audio,
            rotation_degrees=clip.rotation_degrees,
        )
        return ClipExportOutcome(item=item, clip=clip, output_path=dest)

    def _compute_crop_pixels(
        self, item: VideoItem, clip: ClipRange,
    ) -> Optional[tuple[int, int, int, int]]:
        """Translate ``clip.aspect_ratio_label`` (+ optional
        normalized crop rect) into pixel coords for FFmpeg.

        Original ratio → ``None`` (no crop). For everything else, if
        the user hasn't dragged a custom rect we compute the maximal
        centered crop that matches the target ratio — same fallback
        behavior as Process Photos.

        Returns ``None`` when probe fails (rare) so the export
        proceeds uncropped instead of crashing."""
        ratio = get_aspect_ratio(clip.aspect_ratio_label)
        if ratio.is_original:
            return None
        try:
            meta = self.probe_metadata(item)
        except Exception as exc:  # noqa: BLE001 — defensive
            log.warning(
                "probe failed for %s: %s — exporting without crop",
                item.path.name, exc,
            )
            return None
        if meta.width <= 0 or meta.height <= 0:
            return None

        # FFmpeg auto-rotates the source frames before they reach the
        # ``-vf`` filter chain (per the source's displaymatrix). For
        # iPhone portrait videos that means the filter sees the swapped
        # dimensions, not the encoded ``meta.width`` / ``meta.height``.
        # Use ``display_width`` / ``display_height`` so the crop math
        # lines up with what FFmpeg actually feeds the filter — without
        # this, a 1920×1080 portrait MOV would compute ``crop=1920:1080``
        # and FFmpeg rejects it as "too big" because the post-rotation
        # frame is 1080×1920. Costa Rica re-test 2026-05-01 (IMG_5969).
        dw, dh = meta.display_width, meta.display_height

        if clip.crop_norm is not None:
            x_n, y_n, w_n, h_n = clip.crop_norm
            return (
                int(round(x_n * dw)),
                int(round(y_n * dh)),
                int(round(w_n * dw)),
                int(round(h_n * dh)),
            )

        # Centered max crop matching the target ratio.
        target = ratio.value
        src_ratio = dw / dh
        if target > src_ratio:
            # Target is wider → letterbox top/bottom (reduce height).
            crop_w = dw
            crop_h = int(round(dw / target))
        else:
            crop_w = int(round(dh * target))
            crop_h = dh
        # FFmpeg's ``crop`` filter requires even W and H for yuv420p.
        crop_w -= crop_w % 2
        crop_h -= crop_h % 2
        x = (dw - crop_w) // 2
        y = (dh - crop_h) // 2
        return (x, y, crop_w, crop_h)

    # ── Journal ───────────────────────────────────────────────────

    def write_journal(self) -> None:
        """Dump current state to disk with the full three-layer
        protection (B-009, 2026-05-25). Routes through
        ``core.atomic_journal.write_with_protection`` — same engine
        as cull + curate. No-op when nothing's been recorded AND
        there's no journal file yet, to avoid churning the disk on
        idle navigation."""
        if (
            not any(_state_has_data(s) for s in self._states.values())
            and not self.journal_path.exists()
        ):
            return
        data = {
            "version": VIDEO_JOURNAL_SCHEMA_VERSION,
            "event_root": str(self.event_root),
            "videos": [
                {
                    "path": str(path),
                    "markers_ms": list(state.markers_ms),
                    "extracted_frame_positions_ms": list(
                        state.extracted_frame_positions_ms,
                    ),
                    "clips": [
                        {
                            "start_ms": c.start_ms,
                            "end_ms": c.end_ms,
                            "aspect_ratio_label": c.aspect_ratio_label,
                            "crop_norm": (
                                list(c.crop_norm)
                                if c.crop_norm is not None
                                else None
                            ),
                            "include_audio": c.include_audio,
                            # Task #116 — per-clip rotation persists
                            # so an export run after a session restart
                            # honours the user's Rotate choice.
                            "rotation_degrees": c.rotation_degrees,
                        }
                        for c in state.clips
                    ],
                }
                for path, state in self._states.items()
                if _state_has_data(state)
            ],
        }
        from core.atomic_journal import write_with_protection
        try:
            write_with_protection(self.journal_path, data)
        except OSError as exc:
            log.warning("failed to write video journal: %s", exc)

    def load_journal(self) -> None:
        if not self.journal_path.exists():
            return
        try:
            data = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("ignoring unreadable video journal: %s", exc)
            return
        if data.get("version") != VIDEO_JOURNAL_SCHEMA_VERSION:
            log.info("video journal schema mismatch — discarding")
            return
        for entry in data.get("videos", []):
            try:
                p = Path(entry["path"])
            except (KeyError, TypeError):
                continue
            if p not in self._items_by_path:
                # Stale entry for a video that's been moved or
                # deleted — silently skip rather than crash.
                continue
            state = VideoState(
                markers_ms=sorted(int(m) for m in entry.get("markers_ms", [])),
                extracted_frame_positions_ms=sorted(
                    int(m) for m in entry.get(
                        "extracted_frame_positions_ms", [],
                    )
                ),
                clips=[
                    ClipRange(
                        start_ms=int(c["start_ms"]),
                        end_ms=int(c["end_ms"]),
                        aspect_ratio_label=c.get(
                            "aspect_ratio_label", ORIGINAL_LABEL,
                        ),
                        crop_norm=(
                            tuple(c["crop_norm"])
                            if c.get("crop_norm")
                            else None
                        ),
                        include_audio=bool(c.get("include_audio", True)),
                        # Task #116 — rotation persists. Old journals
                        # without the key default to 0 (no rotation).
                        rotation_degrees=int(
                            c.get("rotation_degrees", 0)) % 360,
                    )
                    for c in entry.get("clips", [])
                    if "start_ms" in c and "end_ms" in c
                ],
            )
            self._states[p] = state
        log.info(
            "restored %d video state(s) from journal", len(self._states),
        )

    def discard_journal(self) -> None:
        """Test helper. Production code never calls this — the
        journal lives forever; stale entries are filtered on
        ``load_journal`` instead."""
        try:
            self.journal_path.unlink()
        except FileNotFoundError:
            pass


def _state_has_data(state: VideoState) -> bool:
    return bool(
        state.markers_ms
        or state.clips
        or state.extracted_frame_positions_ms
    )


def _resolve_unique(directory: Path, filename: str) -> Path:
    """If ``directory / filename`` already exists, append (2), (3), …
    so re-exporting a clip with the same start time doesn't silently
    overwrite the previous one. Same convention as Process Photos
    save."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = directory / filename
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({n}){suffix}"
        n += 1
    return candidate
