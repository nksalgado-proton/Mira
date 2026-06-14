"""Bracket sequence detector.

Identifies groups of consecutive photos that form a focus bracket or an
exposure bracket. Runs during import (after EXIF reading, before refinement
rules) so that brackets get an intermediate scenario and are treated as a
unit by the culler rather than individually classified.

Two-pass algorithm (see v2_design.md §12):

    Pass 1 — temporal + contextual windowing:
        Group consecutive photos by proximity in time (< window_seconds)
        AND same lens AND same body AND same orientation.

    Pass 2 — type classification per window:
        First try explicit EXIF bracket tag (confidence 0.99).
        Then try inferred variation of parameters (confidence 0.85).
        Discard as bracket if neither path fits — the photos then go
        through the normal classifier individually.

Input shape: a list of BracketCandidate objects already built by the caller
from raw EXIF (similar to PhotoContext for the classifier — decouples the
detector from exiftool and makes tests trivial).

Output: a BracketDetectionResult with two lists:
    - sequences: the detected BracketSequence objects
    - orphans: photos that are not part of any sequence
"""

import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.logging_setup import log_activity
from core.settings import user_data_dir
from core.vocabulary import BracketType

log = logging.getLogger(__name__)

# Default thresholds — overridable via JSON config (bracket_detector.json)
DEFAULT_WINDOW_SECONDS = 2.0
DEFAULT_MIN_SEQUENCE_SIZE = 3
DEFAULT_MAX_SEQUENCE_SIZE = 100

# Confidence values emitted by the detector
CONFIDENCE_EXIF_TAG = 0.99
CONFIDENCE_INFERRED = 0.85


@dataclass
class BracketCandidate:
    """A single photo expressed in the shape the detector needs.

    The caller builds this from raw EXIF via brand profile + body profile.
    It contains just enough normalized data to decide whether photos belong
    together as a sequence.
    """
    path: Path
    timestamp: Optional[datetime]       # DateTimeOriginal
    lens_name: str                      # canonical lens name
    body_id: str                        # body profile id
    orientation: int                    # 1..8 (EXIF orientation tag)

    focal_length: float = 0.0
    aperture: float = 0.0
    shutter_speed: float = 0.0
    iso: int = 0
    focus_distance: Optional[float] = None
    exposure_compensation: Optional[float] = None

    # Explicit EXIF bracket signals (read by caller via brand profile)
    focus_bracket_tag_active: bool = False
    exposure_bracket_tag_active: bool = False
    # Explicit EXIF "I was in continuous burst mode" signal — Nikon
    # ``ShootingMode = Continuous``, Sony ``ReleaseMode = Continuous``,
    # Canon ``ContinuousDrive``, Panasonic ``DriveMode``. When True for
    # every frame in a window AND no frame has a bracket tag, the
    # detector refuses to *infer* a bracket from parameter variation:
    # a manual shutter / aperture tweak mid-burst should not promote a
    # plain burst to a bracket. Costa Rica field test 2026-04-30 —
    # Nikon D7100 burst with mid-sequence shutter change was being
    # mis-classified as exposure bracket.
    continuous_shooting_active: bool = False
    # Per-frame in-bracket / in-burst counter the camera writes. For
    # Panasonic this is ``SequenceNumber`` (1, 2, ... N within a single
    # bracket/burst, then reset to 1 at the start of the next). Used by
    # the windowing pass to SPLIT two consecutive brackets that fall
    # within the time-window of each other (Nelson 2026-06-06: two 80-
    # frame focus brackets shot back-to-back were being merged into a
    # 160-frame window, then arbitrarily cut at max_sequence_size=100).
    # ``None`` when the camera doesn't write this counter — windowing
    # then falls back to time + size cap as before.
    sequence_number: Optional[int] = None


@dataclass
class BracketSequence:
    """A detected sequence of photos that form a bracket."""
    sequence_id: str
    sequence_type: BracketType       # FOCUS or EXPOSURE
    photos: list[Path]               # ordered by timestamp
    confidence: float
    detection_source: str            # "exif_tag" or "inferred"
    # Representative timestamp for the whole sequence — populated from
    # the first frame's DateTimeOriginal at construction. Brackets are
    # temporally cohesive (within seconds) so a single timestamp is
    # enough for day-routing purposes in CullerSession.save(). Without
    # this, brackets fall back to the flat ``<event_root>/<scenario>/``
    # layout instead of ``<event_root>/<day>/<scenario>/`` — surfaced
    # 2026-04-29 in Costa Rica field test (Day 5 AEB created landscape/
    # outside the day folder).
    representative_timestamp: Optional[datetime] = None
    user_modified: bool = False
    accepted: Optional[bool] = None  # None = undecided by user yet

    @property
    def photo_count(self) -> int:
        return len(self.photos)


@dataclass
class BracketDetectionResult:
    """Full output of the detector for a batch of candidates."""
    sequences: list[BracketSequence] = field(default_factory=list)
    orphans: list[Path] = field(default_factory=list)


@dataclass
class DetectorConfig:
    """Runtime config loaded from JSON (or defaults)."""
    window_seconds: float = DEFAULT_WINDOW_SECONDS
    min_sequence_size: int = DEFAULT_MIN_SEQUENCE_SIZE
    max_sequence_size: int = DEFAULT_MAX_SEQUENCE_SIZE

    # Focus bracket inference tolerances
    require_monotonic_focus_distance: bool = True
    tolerate_aperture_jitter_stops: float = 0.0
    tolerate_shutter_jitter_stops: float = 0.0

    # Exposure bracket inference
    require_constant_aperture: bool = True
    require_constant_iso: bool = True
    min_exposure_range_stops: float = 1.0


# ---------------------------------------------------------------------------
# Pass 1 — windowing
# ---------------------------------------------------------------------------

def _same_context(a: BracketCandidate, b: BracketCandidate) -> bool:
    """Return True if two candidates share the same capture context.

    Both must have the same lens, same body, and same orientation.
    Missing data (e.g. empty lens name) disqualifies the pair — we
    refuse to infer a bracket across unknown context.
    """
    if not a.lens_name or not b.lens_name:
        return False
    if a.lens_name != b.lens_name:
        return False
    if a.body_id != b.body_id:
        return False
    if a.orientation != b.orientation:
        return False
    return True


def _time_delta_seconds(
    earlier: BracketCandidate, later: BracketCandidate
) -> Optional[float]:
    if earlier.timestamp is None or later.timestamp is None:
        return None
    return (later.timestamp - earlier.timestamp).total_seconds()


def _sequence_number_reset(
    earlier: BracketCandidate, later: BracketCandidate,
) -> bool:
    """True when the camera's in-bracket counter reset between two
    frames — a hard "a new bracket started here" signal that overrides
    the time-window grouping (Nelson 2026-06-06: two consecutive
    focus brackets fired in quick succession used to merge into one
    window, then get arbitrarily cut at ``max_sequence_size``).

    Only fires when BOTH frames carry a counter — if either is None
    the camera doesn't write this signal and we fall back to the
    time-window heuristics. The counter is "strictly less than" the
    previous, which catches the canonical 80→1 reset; an equal value
    is treated as no reset (some cameras occasionally repeat a value
    under burst-buffer pressure).
    """
    if earlier.sequence_number is None or later.sequence_number is None:
        return False
    return later.sequence_number < earlier.sequence_number


def _bracket_tag_kind(c: BracketCandidate) -> Optional[str]:
    """The bracket kind the camera explicitly declared on this frame, or
    ``None`` if neither bracket tag is active.

    Returns ``"focus"`` or ``"exposure"`` (matching ``BracketType.value``)
    so callers can compare tag states across adjacent frames without
    importing the enum. Most cameras flag at most one tag per frame; if
    both somehow fire, focus wins — only matters for diagnostics
    (windowing only cares about same-vs-different)."""
    if c.focus_bracket_tag_active:
        return "focus"
    if c.exposure_bracket_tag_active:
        return "exposure"
    return None


def _window_candidates(
    candidates: list[BracketCandidate],
    config: DetectorConfig,
) -> list[list[BracketCandidate]]:
    """Group consecutive candidates into windows the detector can classify.

    Candidates with no timestamp are skipped from windowing — they become
    orphans (handled by the caller).

    **Two grouping strategies, picked per adjacent pair (Nelson
    2026-06-06):**

    * **Tag-driven** — when both adjacent frames carry the SAME explicit
      bracket tag (focus or exposure), the camera is telling us "these
      are bracket frames of the same kind". We trust it and IGNORE the
      time-window: a 30-second focus sweep is still one bracket. The
      only boundaries that close such a window are a context change
      (lens / body / orientation), the ``max_sequence_size`` safety cap,
      a ``sequence_number`` reset, or a tag-state flip.
    * **Inferred (fallback)** — when neither adjacent frame has a
      bracket tag (or the tags don't match), the only signal available
      is parameter variation across temporally-close frames. Apply the
      classical time-window: group only if the time gap is within
      ``window_seconds``. (Once windowed, ``_classify_window`` decides
      whether the parameter-variation pattern looks like a bracket.)

    A window always CLOSES on: context change, ``max_sequence_size``
    reached, ``sequence_number`` reset, OR a flip in the bracket tag
    state (focus→exposure, on→off, etc.). For inferred-path frames the
    time gap also closes.

    Returns only windows satisfying MIN <= len <= MAX. Smaller/larger
    windows are discarded or split respectively.
    """
    with_ts = [c for c in candidates if c.timestamp is not None]
    with_ts.sort(key=lambda c: c.timestamp)  # type: ignore[arg-type]

    windows: list[list[BracketCandidate]] = []
    if not with_ts:
        return windows

    current: list[BracketCandidate] = [with_ts[0]]

    for candidate in with_ts[1:]:
        previous = current[-1]
        prev_tag = _bracket_tag_kind(previous)
        cur_tag = _bracket_tag_kind(candidate)

        # Universal hard boundaries: context, size cap, counter reset.
        if (
            not _same_context(current[0], candidate)
            or len(current) >= config.max_sequence_size
            or _sequence_number_reset(previous, candidate)
        ):
            keep = current
            current = [candidate]
            if len(keep) >= config.min_sequence_size:
                windows.append(keep)
            continue

        # Tag-driven path: both frames declared the SAME bracket kind →
        # trust the camera, ignore the time gap.
        if prev_tag is not None and prev_tag == cur_tag:
            current.append(candidate)
            continue

        # Tag flip (focus↔exposure or on↔off) is always a hard boundary —
        # the camera changed mode mid-stream.
        if prev_tag != cur_tag:
            if len(current) >= config.min_sequence_size:
                windows.append(current)
            current = [candidate]
            continue

        # Inferred path: neither frame has a tag. Group on temporal
        # adjacency (the classical time-window heuristic).
        delta = _time_delta_seconds(previous, candidate)
        if delta is not None and delta <= config.window_seconds:
            current.append(candidate)
        else:
            if len(current) >= config.min_sequence_size:
                windows.append(current)
            current = [candidate]

    if len(current) >= config.min_sequence_size:
        windows.append(current)

    return windows


# ---------------------------------------------------------------------------
# Pass 2 — classification per window
# ---------------------------------------------------------------------------

def _all_have_focus_bracket_tag(window: list[BracketCandidate]) -> bool:
    return bool(window) and all(c.focus_bracket_tag_active for c in window)


def _any_has_exposure_bracket_tag(window: list[BracketCandidate]) -> bool:
    return any(c.exposure_bracket_tag_active for c in window)


def _is_pure_burst(window: list[BracketCandidate]) -> bool:
    """All frames flagged as continuous-burst AND none flagged as a
    bracket. The detector treats this as a hard veto on the inferred
    classification paths — shutter/aperture jitter inside a plain
    burst should not promote it to a bracket.
    """
    if not window:
        return False
    if not all(c.continuous_shooting_active for c in window):
        return False
    if any(c.focus_bracket_tag_active or c.exposure_bracket_tag_active for c in window):
        return False
    return True


def _is_constant(values: list[float], tolerance: float = 0.0) -> bool:
    """Return True if all values are within `tolerance` of each other."""
    if not values:
        return True
    lo, hi = min(values), max(values)
    return (hi - lo) <= tolerance


def _is_monotonic(values: list[Optional[float]]) -> bool:
    """Return True if the sequence is strictly increasing or strictly decreasing.

    None values disqualify monotonicity (we can't decide).
    """
    clean = [v for v in values if v is not None]
    if len(clean) != len(values) or len(clean) < 2:
        return False
    increasing = all(b > a for a, b in zip(clean, clean[1:]))
    decreasing = all(b < a for a, b in zip(clean, clean[1:]))
    return increasing or decreasing


def _varies(values: list[float], min_range: float = 0.0) -> bool:
    """Return True if max - min exceeds the given minimum range."""
    if len(values) < 2:
        return False
    return (max(values) - min(values)) > min_range


def _classify_window_as_focus_bracket(
    window: list[BracketCandidate],
    config: DetectorConfig,
) -> bool:
    """Test whether a window looks like a focus bracket (inferred path).

    Requires:
      - Focus distance varies monotonically (strictly inc or dec)
      - Aperture, shutter, ISO all constant (within tolerances)
    """
    if not config.require_monotonic_focus_distance:
        return False

    focus_distances = [c.focus_distance for c in window]
    if not _is_monotonic(focus_distances):
        return False

    apertures = [c.aperture for c in window if c.aperture > 0]
    shutters = [c.shutter_speed for c in window if c.shutter_speed > 0]
    isos = [float(c.iso) for c in window if c.iso > 0]

    if not _is_constant(apertures, tolerance=config.tolerate_aperture_jitter_stops):
        return False
    if not _is_constant(shutters, tolerance=config.tolerate_shutter_jitter_stops):
        return False
    if not _is_constant(isos, tolerance=0.0):
        return False

    return True


def _classify_window_as_exposure_bracket(
    window: list[BracketCandidate],
    config: DetectorConfig,
) -> bool:
    """Test whether a window looks like an exposure bracket (inferred path).

    Requires:
      - Shutter speed varies OR exposure compensation varies
      - Aperture constant, ISO constant
      - Focus distance NOT varying monotonically (or no focus_distance data)
        (otherwise it's a focus bracket, not exposure)
    """
    apertures = [c.aperture for c in window if c.aperture > 0]
    isos = [float(c.iso) for c in window if c.iso > 0]

    if config.require_constant_aperture and not _is_constant(apertures):
        return False
    if config.require_constant_iso and not _is_constant(isos):
        return False

    shutters = [c.shutter_speed for c in window if c.shutter_speed > 0]
    ev_comps = [
        float(c.exposure_compensation) for c in window
        if c.exposure_compensation is not None
    ]

    # A real exposure bracket varies *exposure* by a meaningful
    # margin, gated by ``min_exposure_range_stops`` (default 1.0).
    # Both legs measured in STOPS: shutter via log2(max/min) — the
    # threshold is in stops but shutter values are in seconds, so
    # comparing seconds to a stops threshold was unit-wrong, and the
    # ``* 0.0`` had been neutering it entirely. Result: sub-stop
    # auto-metering jitter in a handheld run was promoted to a false
    # AEB (Nelson eyeball 2026-05-17 — real G9 Dia 9: f/6.3 ISO 3200,
    # 1/125->1/100 across a 3-4 frame run = 0.32 stops, no AEB tag).
    # EV-comp is already in stops -> direct span. Genuine inferred
    # AEB (>= the threshold) and the explicit-tag path (conf 0.99,
    # handled earlier) are unaffected.
    need = config.min_exposure_range_stops
    shutter_stops = (
        math.log2(max(shutters) / min(shutters))
        if len(shutters) >= 2 and min(shutters) > 0 else 0.0
    )
    ev_stops = (
        (max(ev_comps) - min(ev_comps)) if len(ev_comps) >= 2 else 0.0
    )
    if shutter_stops < need and ev_stops < need:
        return False

    # If focus distance also varies monotonically, prefer focus bracket path
    focus_distances = [c.focus_distance for c in window]
    if _is_monotonic(focus_distances):
        return False

    return True


def _make_sequence(
    window: list[BracketCandidate],
    sequence_type: BracketType,
    confidence: float,
    detection_source: str,
) -> BracketSequence:
    representative_timestamp = next(
        (c.timestamp for c in window if c.timestamp is not None),
        None,
    )
    return BracketSequence(
        sequence_id=str(uuid.uuid4()),
        sequence_type=sequence_type,
        photos=[c.path for c in window],
        confidence=confidence,
        detection_source=detection_source,
        representative_timestamp=representative_timestamp,
    )


def _classify_window(
    window: list[BracketCandidate],
    config: DetectorConfig,
) -> Optional[BracketSequence]:
    """Attempt to classify a window as a bracket sequence.

    Returns a BracketSequence if classified, or None if the window is
    ambiguous (the caller then treats the photos as orphans).
    """
    # Confidence path 1: explicit EXIF tag
    if _all_have_focus_bracket_tag(window):
        return _make_sequence(
            window, BracketType.FOCUS, CONFIDENCE_EXIF_TAG, "exif_tag"
        )
    if _any_has_exposure_bracket_tag(window):
        return _make_sequence(
            window, BracketType.EXPOSURE, CONFIDENCE_EXIF_TAG, "exif_tag"
        )

    # Hard veto on the inferred paths if the camera explicitly
    # reported "this was a continuous burst" for every frame and
    # no frame had a bracket tag. Without this, a manual shutter
    # tweak mid-burst (Nelson's Nikon D7100 portrait sequence,
    # Costa Rica 2017) was being inferred as an exposure bracket.
    if _is_pure_burst(window):
        return None

    # Confidence path 2: inferred from parameter variation
    if _classify_window_as_focus_bracket(window, config):
        return _make_sequence(
            window, BracketType.FOCUS, CONFIDENCE_INFERRED, "inferred_focus"
        )
    if _classify_window_as_exposure_bracket(window, config):
        return _make_sequence(
            window, BracketType.EXPOSURE, CONFIDENCE_INFERRED, "inferred_exposure"
        )

    # Ambiguous — not classified as a bracket
    return None


# ---------------------------------------------------------------------------
# Public detector entry point
# ---------------------------------------------------------------------------

def detect_brackets(
    candidates: list[BracketCandidate],
    config: Optional[DetectorConfig] = None,
) -> BracketDetectionResult:
    """Run the bracket detector over a list of candidates.

    Args:
        candidates: photos from a single import batch
        config: optional override; defaults to DetectorConfig()

    Returns:
        BracketDetectionResult with detected sequences and orphan paths.
    """
    if config is None:
        config = DetectorConfig()

    with log_activity(log, f"detecting brackets in {len(candidates)} candidates"):
        windows = _window_candidates(candidates, config)
        log.debug("Pass 1 produced %d candidate windows", len(windows))

        sequences: list[BracketSequence] = []
        paths_in_sequences: set[Path] = set()

        for window in windows:
            classified = _classify_window(window, config)
            if classified is not None:
                sequences.append(classified)
                paths_in_sequences.update(classified.photos)
                log.debug(
                    "Classified window of %d photos as %s (%s, conf=%.2f)",
                    len(window),
                    classified.sequence_type.value,
                    classified.detection_source,
                    classified.confidence,
                )
            else:
                log.debug(
                    "Window of %d photos rejected as bracket (ambiguous)",
                    len(window),
                )

        orphans = [c.path for c in candidates if c.path not in paths_in_sequences]

        log.info(
            "Detected %d bracket sequences covering %d photos; %d orphans",
            len(sequences), len(paths_in_sequences), len(orphans),
        )

        return BracketDetectionResult(sequences=sequences, orphans=orphans)


# ---------------------------------------------------------------------------
# Config loading from JSON
# ---------------------------------------------------------------------------

def _parse_config(data: dict[str, Any]) -> DetectorConfig:
    focus_cfg = data.get("focus_bracket", {})
    exposure_cfg = data.get("exposure_bracket", {})
    return DetectorConfig(
        window_seconds=float(data.get("window_seconds", DEFAULT_WINDOW_SECONDS)),
        min_sequence_size=int(data.get("min_sequence_size", DEFAULT_MIN_SEQUENCE_SIZE)),
        max_sequence_size=int(data.get("max_sequence_size", DEFAULT_MAX_SEQUENCE_SIZE)),
        require_monotonic_focus_distance=bool(
            focus_cfg.get("require_monotonic_focus_distance", True)
        ),
        tolerate_aperture_jitter_stops=float(
            focus_cfg.get("tolerate_aperture_jitter_stops", 0.0)
        ),
        tolerate_shutter_jitter_stops=float(
            focus_cfg.get("tolerate_shutter_jitter_stops", 0.0)
        ),
        require_constant_aperture=bool(
            exposure_cfg.get("require_constant_aperture", True)
        ),
        require_constant_iso=bool(
            exposure_cfg.get("require_constant_iso", True)
        ),
        min_exposure_range_stops=float(
            exposure_cfg.get("min_exposure_range_stops", 1.0)
        ),
    )


def _builtin_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "bracket_detector.json"


def _user_override_config_path() -> Path:
    return user_data_dir() / "bracket_detector.json"


def load_detector_config() -> DetectorConfig:
    """Load bracket detector config with user override falling back to built-in.

    Returns default DetectorConfig() if neither file exists (detector still
    works on default thresholds — no crash).
    """
    override_path = _user_override_config_path()
    builtin_path = _builtin_config_path()

    path_to_read: Optional[Path] = None
    if override_path.exists():
        path_to_read = override_path
    elif builtin_path.exists():
        path_to_read = builtin_path

    if path_to_read is None:
        log.debug("No bracket detector config file found, using defaults")
        return DetectorConfig()

    try:
        with path_to_read.open("r", encoding="utf-8") as f:
            data = json.load(f)
        config = _parse_config(data)
        log.debug("Loaded bracket detector config from %s", path_to_read)
        return config
    except (json.JSONDecodeError, OSError, ValueError, TypeError) as exc:
        log.warning(
            "Bracket detector config at %s is unreadable (%s) — using defaults",
            path_to_read, exc,
        )
        return DetectorConfig()
