"""Scenario bootstrap logic — the brains of Block B.

Given a list of photo paths dropped into a scenario drop zone, this
module batch-reads EXIF via the v1.x reader, extracts the canonical lens
name via the matching brand profile, and returns per-photo results that
the caller (HardwareTab) uses to update the HardwareEntry state.

The module is UI-independent: it takes paths and returns data. The
Hardware tab converts the results into HardwareEntry mutations and UI
updates.

Usage:
    from core.scenario_bootstrap import analyze_dropped_photos

    results = analyze_dropped_photos(
        paths=[Path("P001.RW2"), Path("P002.RW2"), ...],
        expected_body_id="panasonic_g9_ii",
    )
    for result in results:
        print(result.path.name, result.lens_canonical, result.warning)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.brand_profile import match_brand_profile_for_photo
from core.logging_setup import log_activity
from core.vocabulary import (
    FINAL_SCENARIOS,
    INTERMEDIATE_SCENARIOS,
    PhotoStyle,
    Scenario,
)

log = logging.getLogger(__name__)


@dataclass
class DroppedPhotoResult:
    """Analysis result for a single photo dropped into a scenario.

    Fields:
        path: the file path
        lens_canonical: canonical lens name via brand profile, or "" if the
            photo has no LensModel EXIF tag (common with vintage lenses or
            no-chip adapters)
        focal_length: mm from EXIF
        aperture: f-number
        focus_mode_raw: raw FocusMode string from EXIF
        photo_style: normalized PhotoStyle setting the photographer chose
            before shooting (Panasonic PhotoStyle, Sony CreativeLook, etc.).
            Strong intent signal when populated — PhotoStyle.UNKNOWN means
            either the camera doesn't expose it or the user had it off.
        body_id_detected: which body the photo came from (for mismatch warning)
        accepted: True if this photo should be counted toward the scenario;
            False if it failed to read or was rejected
        warning: human-readable warning to show in the UI (e.g. "no lens info",
            "wrong body"), or "" if clean
    """
    path: Path
    lens_canonical: str = ""
    focal_length: float = 0.0
    aperture: float = 0.0
    focus_mode_raw: str = ""
    photo_style: PhotoStyle = PhotoStyle.UNKNOWN
    body_id_detected: str = ""
    accepted: bool = True
    warning: str = ""


@dataclass
class BootstrapAnalysisResult:
    """Aggregated results of analyzing a batch of dropped photos."""
    photos: list[DroppedPhotoResult] = field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return sum(1 for p in self.photos if p.accepted)

    @property
    def rejected_count(self) -> int:
        return sum(1 for p in self.photos if not p.accepted)

    @property
    def lens_counts(self) -> dict[str, int]:
        """{canonical_lens_name: count} for accepted photos only.

        Photos with empty lens_canonical are grouped under "" and can be
        distinguished by checking the empty key in the returned dict.
        """
        result: dict[str, int] = {}
        for p in self.photos:
            if not p.accepted:
                continue
            result[p.lens_canonical] = result.get(p.lens_canonical, 0) + 1
        return result


def _parse_float_tolerant(value) -> float:
    """Best-effort float parser — handles '6.3', '1/2000', '400.0 mm', 6.3."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("mm", "").replace("f/", "").replace("F", "").strip()
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


def analyze_dropped_photos(
    paths: list[Path],
    *,
    expected_body_id: Optional[str] = None,
) -> BootstrapAnalysisResult:
    """Analyze a batch of photos just dropped into a scenario drop zone.

    Reads EXIF from all photos in one ExifTool call (much faster than one
    call per photo), canonicalizes each lens via its brand profile, and
    returns a per-photo result that the UI can use to update counts and
    show feedback.

    Args:
        paths: photo file paths to analyze
        expected_body_id: if provided, photos whose Model doesn't match this
            body get a "wrong body" warning so the user knows they dropped a
            photo from a different camera into the wrong hardware's
            scenario bootstrap. The photo is still accepted — the warning
            is advisory.

    Returns:
        BootstrapAnalysisResult with per-photo results and aggregate counts.

    Does not raise on per-photo errors — one bad photo does not abort the
    batch. Failed photos have ``accepted=False`` and a ``warning`` string.
    """
    if not paths:
        return BootstrapAnalysisResult()

    with log_activity(log, f"analyzing {len(paths)} dropped photos"):
        result = BootstrapAnalysisResult()

        # Filter out non-existent files up front (defensive)
        valid_paths: list[Path] = []
        for path in paths:
            if not path.exists():
                result.photos.append(DroppedPhotoResult(
                    path=path,
                    accepted=False,
                    warning="file does not exist",
                ))
                continue
            if not path.is_file():
                result.photos.append(DroppedPhotoResult(
                    path=path,
                    accepted=False,
                    warning="not a file",
                ))
                continue
            valid_paths.append(path)

        if not valid_paths:
            return result

        # One ExifTool subprocess call for the whole batch
        try:
            from core.exif_reader import read_exif_batch
            photos = read_exif_batch(valid_paths)
        except Exception as exc:  # noqa: BLE001
            log.error("ExifTool batch read failed: %s", exc, exc_info=True)
            for path in valid_paths:
                result.photos.append(DroppedPhotoResult(
                    path=path,
                    accepted=False,
                    warning=f"EXIF read failed: {exc}",
                ))
            return result

        # Build a path → raw exif dict lookup. ExifTool may return paths
        # with different slashes or casing — normalize via pathlib.
        exif_by_path: dict[str, dict] = {}
        for photo in photos:
            # photo.path is a Path from the v1.x reader
            key = str(Path(photo.raw.get("SourceFile", photo.path)).resolve())
            exif_by_path[key] = photo.raw

        for path in valid_paths:
            key = str(path.resolve())
            raw = exif_by_path.get(key)
            if raw is None:
                # Fallback: try matching by filename only (ExifTool sometimes
                # returns relative or forward-slash paths on Windows)
                for stored_key, stored_raw in exif_by_path.items():
                    if Path(stored_key).name == path.name:
                        raw = stored_raw
                        break

            if raw is None:
                result.photos.append(DroppedPhotoResult(
                    path=path,
                    accepted=False,
                    warning="ExifTool returned no data for this file",
                ))
                continue

            photo_result = _analyze_single(path, raw, expected_body_id)
            result.photos.append(photo_result)

        log.info(
            "Analyzed %d photos: %d accepted, %d rejected",
            len(paths), result.accepted_count, result.rejected_count,
        )
        return result


def _analyze_single(
    path: Path,
    raw: dict,
    expected_body_id: Optional[str],
) -> DroppedPhotoResult:
    """Analyze one photo's EXIF dict. Never raises."""
    result = DroppedPhotoResult(path=path)

    # Body mismatch warning — advisory, does not reject
    if expected_body_id:
        from core.body_profile import match_body_profile_for_photo
        body = match_body_profile_for_photo(raw)
        if body is not None:
            result.body_id_detected = body.body_id
            if body.body_id != expected_body_id:
                result.warning = (
                    f"photo from {body.display_name}, "
                    f"not the current hardware"
                )

    # Canonical lens via brand profile — uses the brand's multi-tag fallback
    # chain (LensModel → LensType → LensID). On Panasonic bodies with 2017
    # firmware, LensModel may be empty for third-party lenses but LensType
    # / LensID carry the decoded name from maker notes.
    brand = match_brand_profile_for_photo(raw)
    if brand is not None:
        result.lens_canonical = brand.canonicalize_lens(raw)
        # Photographer's style/creative-intent setting — a strong signal
        # when the user set it consciously before the shot. See v2_design
        # §11 for the refinement rules that will use it post-Costa Rica.
        result.photo_style = brand.translate_photo_style(raw)
    else:
        # Unknown brand — fall back to raw LensModel verbatim, leave
        # photo_style at its default UNKNOWN.
        for tag in ("LensModel", "LensType", "LensID"):
            value = str(raw.get(tag, "")).strip()
            if value:
                result.lens_canonical = value
                break

    # Lens-less warning (only set if we don't already have a more important warning)
    if not result.lens_canonical and not result.warning:
        result.warning = "no lens info in EXIF (counted toward scenario total only)"

    # Numeric fields for UI display
    result.focal_length = _parse_float_tolerant(raw.get("FocalLength"))
    result.aperture = _parse_float_tolerant(raw.get("FNumber"))
    result.focus_mode_raw = str(raw.get("FocusMode", "")).strip()

    return result


# ---------------------------------------------------------------------------
# Scenario enumeration for UI
# ---------------------------------------------------------------------------

# Order used by the Hardware tab's bootstrap grid. Final scenarios come
# first (most common), intermediates at the end (conditional).
BOOTSTRAP_SCENARIO_ORDER: tuple[Scenario, ...] = tuple(FINAL_SCENARIOS) + tuple(
    INTERMEDIATE_SCENARIOS
)


def describe_scenario(scenario: Scenario) -> str:
    """Short human-readable description for UI tooltips and drop zone labels."""
    descriptions = {
        Scenario.MACRO: "Close-ups, manual focus, small apertures",
        Scenario.WILDLIFE: "Long lens, fast AF, subject tracking",
        Scenario.PORTRAIT: "Face/eye focus, shallow depth of field",
        Scenario.SELFIE: "Front-camera or arm's-length self-portrait",
        Scenario.LANDSCAPE: "Wide angle, stopped down, no tracking",
        Scenario.NIGHT_LONG_EXPOSURE: "Long shutter, tripod, low light",
        Scenario.VIDEO: "Movie clips — phone, camera, or GoPro",
        Scenario.GENERAL: "Travel, street, anything uncategorized",
        Scenario.FOCUS_BRACKET: "Focus stacking sequences (drag the whole set)",
        Scenario.EXPOSURE_BRACKET: "HDR / exposure bracket sequences",
    }
    return descriptions.get(scenario, scenario.value)
