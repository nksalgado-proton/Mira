"""Import pipeline — the glue that connects Phase A modules into one call.

Given a list of (path, raw_exif_dict) entries, this module:
  1. Matches each photo to a brand profile and body profile (by Make/Model).
  2. Canonicalizes the lens name via the brand profile.
  3. Looks up the lens in the user's lens registry (or creates a stub).
  4. Builds PhotoContext (for the classifier) and BracketCandidate (for the
     bracket detector) in parallel.
  5. Runs the bracket detector to identify sequences (camera source only).
  6. Classifies the remaining orphan photos via refinement rules.
  7. Returns an ImportResult combining sequences + classified orphans + errors.

The caller (UI layer) is responsible for reading raw EXIF from the photo
files via ExifTool — this module takes pre-read dicts. Keeps it testable
without subprocess and decoupled from any specific ExifTool wrapper.

Typical usage:

    from core.import_pipeline import classify_imported_batch, RawExifEntry

    entries = [
        RawExifEntry(path=Path("P001.RW2"), exif={"Make": "Panasonic", ...}),
        ...
    ]
    result = classify_imported_batch(entries, source="camera")
    for seq in result.sequences:
        print(f"{seq.sequence_type.value}: {seq.photo_count} photos")
    for path, classification in result.classified:
        print(f"{path.name}: {classification.scenario.value}")
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from core.body_profile import (
    BodyProfile,
    build_stub_body_profile,
    match_body_profile_for_photo,
)
from core.bracket_detector import (
    BracketCandidate,
    BracketSequence,
    DetectorConfig,
    detect_brackets,
    load_detector_config,
)
from core.brand_profile import (
    BrandProfile,
    match_brand_profile_for_photo,
)
from core.classifier_v2 import (
    ClassificationResult,
    PhotoContext,
    RuleSet,
    classify,
)
from core.scenario_loader import (
    load_camera_rules_with_user_scenarios,
    load_phone_rules_with_user_scenarios,
)
from core.lens_registry import (
    LensEntry,
    LensRegistry,
    create_stub_lens_entry,
    load_lens_registry,
)
from core.logging_setup import log_activity
from core.vocabulary import (
    AfAreaMode,
    BracketType,
    DriveMode,
    FocusMode,
    PhotoStyle,
    ShootingMode,
    SubjectDetection,
)

log = logging.getLogger(__name__)

Source = Literal["camera", "phone"]


# ---------------------------------------------------------------------------
# Input / output data types
# ---------------------------------------------------------------------------

@dataclass
class RawExifEntry:
    """A single photo's EXIF data as read by the caller (already a dict).

    The caller uses ExifTool (or any other tool) to populate the dict.
    This module does not care about the reader — only the shape.
    """
    path: Path
    exif: dict[str, Any]


@dataclass
class ImportResult:
    """Output of the import pipeline for a batch of photos."""
    sequences: list[BracketSequence] = field(default_factory=list)
    classified: list[tuple[Path, ClassificationResult]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        in_sequences = sum(s.photo_count for s in self.sequences)
        return in_sequences + len(self.classified)

    @property
    def total_failed(self) -> int:
        return len(self.errors)


# ---------------------------------------------------------------------------
# EXIF value parsing helpers
# ---------------------------------------------------------------------------

def _parse_float(value: Any) -> float:
    """Parse a float tolerant to EXIF quirks: '6.3', '1/2000', '0.005', 1.8."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    if "/" in s:
        try:
            n, d = s.split("/", 1)
            return float(n) / float(d)
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(str(value).strip().split()[0])
    except (ValueError, IndexError):
        return 0


def _parse_bool(value: Any) -> bool:
    """Truthy-ish interpretation of an EXIF flag."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if not s:
        return False
    if "did not fire" in s or "off" in s or "no flash" in s:
        return False
    return True


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse DateTimeOriginal-like strings into a datetime, or None on failure."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # ExifTool usually returns "2026:04:15 10:30:00" with optional fractions/tz
    s = s.split(".")[0]
    for prefix in ("+", "-"):
        # Strip trailing timezone offset like "+02:00"
        idx = s.rfind(prefix)
        if idx > 10:  # avoid chopping the date dashes
            s = s[:idx]
            break
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_focal_length(value: Any) -> float:
    """Focal length may come as '400.0 mm', '400', or 400.0."""
    if value is None:
        return 0.0
    s = str(value).replace("mm", "").strip()
    return _parse_float(s)


def _parse_aperture(value: Any) -> float:
    """FNumber may come as '6.3', 6.3, or 'f/6.3'."""
    if value is None:
        return 0.0
    s = str(value).replace("f/", "").replace("F", "").strip()
    return _parse_float(s)


def _parse_focus_distance(value: Any) -> Optional[float]:
    """Optional — None if unknown. Values like '2.5 m', 'inf', '0.5'."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s in ("inf", "infinity"):
        return None
    # Strip unit suffixes
    for suffix in (" m", "m"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Body profile resolution (with stub fallback)
# ---------------------------------------------------------------------------

# One log line per distinct unknown body per process — a batch of
# metadata-less files used to print a warning wall ("Unknown body ' '"
# once PER FILE — Nelson 2026-06-11).
_warned_bodies: set[tuple[str, str]] = set()


def _resolve_body_profile(exif: dict[str, Any]) -> BodyProfile:
    """Find or build a body profile for this photo's Make/Model.

    Returns a stub profile if no built-in or user override matches,
    so downstream code can always assume body is non-None.
    """
    profile = match_body_profile_for_photo(exif)
    if profile is not None:
        return profile
    make = str(exif.get("Make", "")).strip()
    model = str(exif.get("Model", "")).strip()
    key = (make, model)
    if key not in _warned_bodies:
        _warned_bodies.add(key)
        if not make and not model:
            # No camera metadata at all (exports, downloads, renamed
            # clips) — expected content, not unfamiliar hardware.
            log.info(
                "No camera metadata (blank Make/Model) — using the "
                "unknown-camera stub profile")
        else:
            # Stub with conservative defaults — a warning so the user
            # notices unfamiliar hardware the first time it appears.
            log.warning(
                "Unknown body '%s %s' — generating stub profile",
                make, model)
    return build_stub_body_profile(exif)


# ---------------------------------------------------------------------------
# PhotoContext and BracketCandidate builders
# ---------------------------------------------------------------------------

def _build_photo_context(
    entry: RawExifEntry,
    brand: Optional[BrandProfile],
    body: BodyProfile,
    lens: Optional[LensEntry],
    source: Source,
) -> PhotoContext:
    """Build a PhotoContext from raw EXIF + resolved profiles.

    If brand is None (unknown make), we fall back to defaults for all
    enum fields. The classifier will still work via the lens fallback path.
    """
    exif = entry.exif

    focal_length = _parse_focal_length(exif.get("FocalLength"))
    focal_35mm = body.focal_35mm(focal_length)
    aperture = _parse_aperture(exif.get("FNumber"))
    shutter_speed = _parse_float(exif.get("ExposureTime"))
    iso = _parse_int(exif.get("ISO"))

    if brand is not None:
        focus_mode = brand.translate_focus_mode(exif)
        af_area_mode = brand.translate_af_area_mode(exif)
        subject_detection = brand.translate_subject_detection(exif)
        drive_mode = brand.translate_drive_mode(exif)
        photo_style = brand.translate_photo_style(exif)
        shooting_mode = brand.translate_shooting_mode(exif)
        bracket_type = brand.detect_bracket(exif)
    else:
        focus_mode = FocusMode.UNKNOWN
        af_area_mode = AfAreaMode.UNKNOWN
        subject_detection = SubjectDetection.NONE
        drive_mode = DriveMode.UNKNOWN
        photo_style = PhotoStyle.UNKNOWN
        shooting_mode = ShootingMode.UNKNOWN
        bracket_type = BracketType.NONE

    focus_bracket_active = bracket_type == BracketType.FOCUS
    exposure_bracket_active = bracket_type == BracketType.EXPOSURE
    focus_distance = _parse_focus_distance(exif.get("FocusDistance"))
    # Brand-aware normalized focus position. Each brand profile knows
    # how to read its EXIF (Panasonic step ratio, Sony meters, etc.);
    # rules query this single [0,1] field and stay brand-agnostic.
    focus_position_normalized = (
        brand.focus_position_normalized(exif) if brand is not None else None
    )
    # Read lens name through the brand profile's fallback chain
    # (LensModel → LensType → LensID), not just LensModel. The
    # Panasonic G9 (2017 firmware) leaves LensModel empty for third-
    # party lenses and writes the data to LensType / LensID instead —
    # reading LensModel alone produced empty lens_model_raw, which
    # silently prevented t2_lens_name_macro (and any other rule that
    # depends on the lens name) from firing on G9 + Olympus 60mm
    # Macro shots.
    if brand is not None:
        lens_model_raw = brand.lens_normalization.read_raw_lens(exif)
    else:
        lens_model_raw = str(exif.get("LensModel", "") or "")
    faces_detected = _parse_int(exif.get("FacesDetected"))

    return PhotoContext(
        focal_length=focal_length,
        focal_35mm=focal_35mm,
        aperture=aperture,
        shutter_speed=shutter_speed,
        iso=iso,
        iso_relative_to_body=body.iso_relative(iso),
        focus_mode=focus_mode,
        af_area_mode=af_area_mode,
        subject_detection=subject_detection,
        faces_detected=faces_detected,
        drive_mode=drive_mode,
        photo_style=photo_style,
        shooting_mode=shooting_mode,
        flash_fired=_parse_bool(exif.get("Flash")),
        focus_distance=focus_distance,
        focus_position_normalized=focus_position_normalized,
        focus_bracket_active=focus_bracket_active,
        exposure_bracket_active=exposure_bracket_active,
        lens_model_raw=lens_model_raw,
        lens=lens,
        body=body,
        source=source,
    )


def _build_bracket_candidate(
    entry: RawExifEntry,
    brand: Optional[BrandProfile],
    body: BodyProfile,
    canonical_lens: str,
) -> BracketCandidate:
    """Build a BracketCandidate from raw EXIF + resolved profiles."""
    exif = entry.exif

    focus_bracket_tag = False
    exposure_bracket_tag = False
    if brand is not None:
        bracket_type = brand.detect_bracket(exif)
        if bracket_type == BracketType.FOCUS:
            focus_bracket_tag = True
        elif bracket_type == BracketType.EXPOSURE:
            exposure_bracket_tag = True
    else:
        # Universal Nikon/Sony/Canon ``ExposureBracketValue`` fallback —
        # non-zero on every frame of a real AEB. See bucket_scanner for
        # the same logic on the bucket-import path.
        ebv = exif.get("ExposureBracketValue")
        try:
            if ebv is not None and float(ebv) != 0.0:
                exposure_bracket_tag = True
        except (TypeError, ValueError):
            pass

    # Brand-aware continuous-shooting detection (Panasonic's BurstMode tag
    # isn't covered by the substring fallback below — without this branch,
    # real Panasonic bursts come through as single shots). OR'd with the
    # substring fallback for unprofiled brands.
    brand_says_continuous = brand.is_continuous_shooting(exif) if brand else False
    cs_value = (
        exif.get("ShootingMode")
        or exif.get("DriveMode")
        or exif.get("ContinuousDrive")
        or exif.get("ReleaseMode")
        or ""
    )
    cs_str = str(cs_value).lower()
    continuous_shooting_active = brand_says_continuous or (
        "continuous" in cs_str or "burst" in cs_str
    )

    return BracketCandidate(
        path=entry.path,
        timestamp=_parse_timestamp(exif.get("DateTimeOriginal")),
        lens_name=canonical_lens,
        body_id=body.body_id,
        orientation=_parse_int(exif.get("Orientation")) or 1,
        focal_length=_parse_focal_length(exif.get("FocalLength")),
        aperture=_parse_aperture(exif.get("FNumber")),
        shutter_speed=_parse_float(exif.get("ExposureTime")),
        iso=_parse_int(exif.get("ISO")),
        focus_distance=_parse_focus_distance(exif.get("FocusDistance")),
        exposure_compensation=(
            _parse_float(exif.get("ExposureCompensation"))
            if exif.get("ExposureCompensation") is not None
            else None
        ),
        focus_bracket_tag_active=focus_bracket_tag,
        exposure_bracket_tag_active=exposure_bracket_tag,
        continuous_shooting_active=continuous_shooting_active,
    )


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

def classify_folder(
    folder: Path,
    *,
    source: Source = "camera",
    recursive: bool = True,
    lens_registry: Optional[LensRegistry] = None,
    camera_rules: Optional[RuleSet] = None,
    phone_rules: Optional[RuleSet] = None,
    detector_config: Optional[DetectorConfig] = None,
) -> ImportResult:
    """Classify all photos in a folder end-to-end.

    Convenience entry point that combines folder scanning with the full
    classification pipeline in a single call. Used by the ad-hoc event
    creation flow and by the ``classify_folder.py`` CLI tool.

    Args:
        folder: directory containing photos to classify. Scanned recursively
            by default via ``core.folder_scanner.scan_folder``.
        source: "camera" or "phone" — drives bracket detection and rule set
        recursive: scan subdirectories if True
        lens_registry, camera_rules, phone_rules, detector_config:
            optional overrides, all auto-loaded from disk if None

    Returns:
        Full ImportResult with sequences, classified orphans, and per-file
        errors (never raises — per-photo failures collected in result.errors).

    Raises:
        FileNotFoundError / NotADirectoryError: if ``folder`` is invalid
            (these come from the scanner and are re-raised because they
            indicate a caller mistake, not a data problem)
    """
    # Import here to avoid a circular import — folder_scanner imports
    # RawExifEntry from this module.
    from core.folder_scanner import scan_folder

    entries = scan_folder(folder, recursive=recursive)
    return classify_imported_batch(
        entries,
        source=source,
        lens_registry=lens_registry,
        camera_rules=camera_rules,
        phone_rules=phone_rules,
        detector_config=detector_config,
    )


def classify_imported_batch(
    entries: list[RawExifEntry],
    *,
    source: Source = "camera",
    lens_registry: Optional[LensRegistry] = None,
    camera_rules: Optional[RuleSet] = None,
    phone_rules: Optional[RuleSet] = None,
    detector_config: Optional[DetectorConfig] = None,
) -> ImportResult:
    """Classify a batch of imported photos end-to-end.

    Args:
        entries: list of (path, raw_exif_dict) tuples from the caller
        source: "camera" (runs bracket detector + camera rules) or
                "phone" (skips bracket detector, uses phone rules)
        lens_registry: optional; loaded from disk if None
        camera_rules: optional; loaded from disk if None (only used for camera source)
        phone_rules: optional; loaded from disk if None (only used for phone source)
        detector_config: optional; loaded from disk if None

    Returns:
        ImportResult with sequences, classified photos, and any errors.

    Does not raise: per-photo failures are collected in result.errors.
    """
    with log_activity(log, f"importing batch of {len(entries)} photos (source={source})"):
        # Lazy-load defaults only when needed
        if lens_registry is None:
            lens_registry = load_lens_registry()
        if detector_config is None and source == "camera":
            detector_config = load_detector_config()

        # Default rule loaders merge wizard-generated user scenarios
        # into the built-in refinement rules — so the user's photo-
        # type preferences (from the wizard) actually influence
        # import-time classification. Callers can pass an explicit
        # camera_rules / phone_rules to bypass the merge (used by
        # unit tests that assert built-in-only behaviour).
        rules: Optional[RuleSet] = None
        if source == "camera":
            rules = camera_rules or load_camera_rules_with_user_scenarios()
        else:
            rules = phone_rules or load_phone_rules_with_user_scenarios()

        # Build per-photo context and candidates, tracking errors
        contexts_by_path: dict[Path, PhotoContext] = {}
        runtime_stub_lens_paths: set[Path] = set()
        candidates: list[BracketCandidate] = []
        errors: list[tuple[Path, str]] = []

        for entry in entries:
            try:
                brand = match_brand_profile_for_photo(entry.exif)
                body = _resolve_body_profile(entry.exif)

                if brand is not None:
                    canonical_lens = brand.canonicalize_lens(entry.exif)
                else:
                    canonical_lens = str(entry.exif.get("LensModel", "")).strip()

                lens_entry = lens_registry.match(canonical_lens) if canonical_lens else None
                if lens_entry is None and canonical_lens:
                    # Unknown lens at runtime — create a detected stub but do
                    # NOT persist it (that's the UI's job if the user confirms).
                    # Track that this was a runtime stub so we can tag the
                    # classification result for UI flagging.
                    lens_entry = create_stub_lens_entry(canonical_lens)
                    runtime_stub_lens_paths.add(entry.path)

                ctx = _build_photo_context(entry, brand, body, lens_entry, source)
                contexts_by_path[entry.path] = ctx

                if source == "camera":
                    candidate = _build_bracket_candidate(entry, brand, body, canonical_lens)
                    candidates.append(candidate)

            except Exception as exc:  # noqa: BLE001
                # One bad photo must not abort the whole batch
                log.warning(
                    "Failed to process '%s': %s: %s",
                    entry.path, type(exc).__name__, exc,
                )
                errors.append((entry.path, f"{type(exc).__name__}: {exc}"))

        # Bracket detection (camera only)
        if source == "camera":
            detection = detect_brackets(candidates, detector_config)
            sequences = detection.sequences
            paths_in_sequences = {p for s in sequences for p in s.photos}
        else:
            sequences = []
            paths_in_sequences = set()

        # Classify orphans (everything not in a sequence)
        classified: list[tuple[Path, ClassificationResult]] = []
        for path, ctx in contexts_by_path.items():
            if path in paths_in_sequences:
                continue
            try:
                result = classify(ctx, rules)
                # If this photo's lens was a runtime stub AND the classifier
                # fell back to the lens default (no rule matched), propagate
                # the unknown_lens signal so the UI can flag it for review.
                if (
                    path in runtime_stub_lens_paths
                    and result.rule_id is None
                    and result.tag is None
                ):
                    result = replace(result, tag="unknown_lens")
                classified.append((path, result))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Failed to classify '%s': %s: %s",
                    path, type(exc).__name__, exc,
                )
                errors.append((path, f"classify: {type(exc).__name__}: {exc}"))

        log.info(
            "Import batch done: %d sequences, %d classified photos, %d errors",
            len(sequences), len(classified), len(errors),
        )

        return ImportResult(
            sequences=sequences,
            classified=classified,
            errors=errors,
        )
