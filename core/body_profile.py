"""Body (camera/phone) capability profile loader.

A body profile describes what a specific camera body can do:
sensor size, crop factor, which features it supports (IBIS, focus bracketing,
subject detection, phase-detect AF), and ISO baseline characteristics.

Body profiles are consumed by the refinement rules engine to:
  - Compute focal_35mm from focal_length via crop_factor
  - Skip rules that require capabilities the body doesn't support
  - Determine whether an ISO value is "high" relative to this body
  - Provide metadata for UI display

Discovery, override, and JSON shape mirror brand_profile.py:
  1. User override:  %APPDATA%/Mira/body_profiles/{body_id}.json
  2. Built-in:       assets/body_profiles/{body_id}.json
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from core.logging_setup import log_activity
from core.settings import user_data_dir
from core.vocabulary import SubjectDetection

log = logging.getLogger(__name__)


BodyKind = Literal["camera", "phone"]


@dataclass
class Sensor:
    size: str = "unknown"          # e.g. "four_thirds", "aps_c", "full_frame"
    crop_factor: float = 1.0
    megapixels: int = 0


@dataclass
class Capabilities:
    ibis: bool = False
    high_res_mode: bool = False
    pixel_shift: bool = False
    focus_bracket: bool = False
    exposure_bracket: bool = False
    pre_burst: bool = False
    phase_detect_af: bool = False

    def has(self, name: str) -> bool:
        """Check a capability by string name (for refinement rules
        that reference capabilities dynamically)."""
        return bool(getattr(self, name, False))


@dataclass
class SubjectDetectionCapability:
    supported: bool = False
    types: list[SubjectDetection] = field(default_factory=list)
    notes: str = ""

    def supports(self, subject: SubjectDetection) -> bool:
        return self.supported and subject in self.types


@dataclass
class IsoBaseline:
    native_min: int = 100
    native_max: int = 12800
    high_iso_threshold: int = 3200

    def classify(self, iso: int) -> Literal["low", "normal", "high"]:
        """Classify an ISO value relative to this body's baseline."""
        if iso <= 0:
            return "normal"
        if iso >= self.high_iso_threshold:
            return "high"
        if iso <= self.native_min * 2:
            return "low"
        return "normal"


@dataclass
class DriveMaxFps:
    mechanical: int = 0
    electronic: int = 0
    burst_high_threshold_fps: int = 0


@dataclass
class BodyProfile:
    body_id: str
    display_name: str
    brand_id: str
    kind: BodyKind = "camera"
    exiftool_model_match: list[str] = field(default_factory=list)
    year_released: Optional[int] = None
    mount: str = ""

    sensor: Sensor = field(default_factory=Sensor)
    capabilities: Capabilities = field(default_factory=Capabilities)
    subject_detection: SubjectDetectionCapability = field(
        default_factory=SubjectDetectionCapability
    )
    iso_baseline: IsoBaseline = field(default_factory=IsoBaseline)
    drive_max_fps: DriveMaxFps = field(default_factory=DriveMaxFps)

    @property
    def crop_factor(self) -> float:
        """Convenience alias for sensor.crop_factor.

        Exposed at the top level so refinement rules can write
        ``body.crop_factor`` instead of ``body.sensor.crop_factor``.
        """
        return self.sensor.crop_factor

    def matches_model(self, exif_model: str) -> bool:
        """Exact (case-insensitive) match against any pattern in
        exiftool_model_match.

        We use exact match rather than substring because model strings are
        short identifiers and substring matching causes collisions — e.g.
        "DC-G9" is a substring of "DC-G9M2", so a substring-based G9 profile
        would incorrectly match G9II photos. If a body ships with multiple
        model strings across firmware versions or regional variants, list
        them all explicitly in exiftool_model_match.
        """
        if not exif_model:
            return False
        model_lower = exif_model.strip().lower()
        return any(
            m.strip().lower() == model_lower
            for m in self.exiftool_model_match
        )

    def focal_35mm(self, focal_length_mm: float) -> float:
        """Convert physical focal length to 35mm equivalent."""
        return focal_length_mm * self.sensor.crop_factor

    def iso_relative(self, iso: int) -> str:
        return self.iso_baseline.classify(iso)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_sensor(data: dict[str, Any]) -> Sensor:
    return Sensor(
        size=data.get("size", "unknown"),
        crop_factor=float(data.get("crop_factor", 1.0)),
        megapixels=int(data.get("megapixels", 0)),
    )


def _parse_capabilities(data: dict[str, Any]) -> Capabilities:
    return Capabilities(
        ibis=bool(data.get("ibis", False)),
        high_res_mode=bool(data.get("high_res_mode", False)),
        pixel_shift=bool(data.get("pixel_shift", False)),
        focus_bracket=bool(data.get("focus_bracket", False)),
        exposure_bracket=bool(data.get("exposure_bracket", False)),
        pre_burst=bool(data.get("pre_burst", False)),
        phase_detect_af=bool(data.get("phase_detect_af", False)),
    )


def _parse_subject_detection(data: dict[str, Any]) -> SubjectDetectionCapability:
    types_raw = data.get("types", [])
    types: list[SubjectDetection] = []
    for t in types_raw:
        try:
            types.append(SubjectDetection(t))
        except ValueError:
            # Unknown value in JSON — skip silently rather than crashing.
            # Validation warnings are reported elsewhere (bootstrap review).
            pass
    return SubjectDetectionCapability(
        supported=bool(data.get("supported", False)),
        types=types,
        notes=data.get("notes", ""),
    )


def _parse_iso_baseline(data: dict[str, Any]) -> IsoBaseline:
    return IsoBaseline(
        native_min=int(data.get("native_min", 100)),
        native_max=int(data.get("native_max", 12800)),
        high_iso_threshold=int(data.get("high_iso_threshold", 3200)),
    )


def _parse_drive_max_fps(data: dict[str, Any]) -> DriveMaxFps:
    return DriveMaxFps(
        mechanical=int(data.get("mechanical", 0)),
        electronic=int(data.get("electronic", 0)),
        burst_high_threshold_fps=int(data.get("burst_high_threshold_fps", 0)),
    )


def parse_body_profile(data: dict[str, Any]) -> BodyProfile:
    """Build a BodyProfile from a parsed JSON dict."""
    kind = data.get("kind", "camera")
    if kind not in ("camera", "phone"):
        kind = "camera"
    return BodyProfile(
        body_id=data["body_id"],
        display_name=data.get("display_name", data["body_id"]),
        brand_id=data["brand_id"],
        kind=kind,
        exiftool_model_match=data.get("exiftool_model_match", []),
        year_released=data.get("year_released"),
        mount=data.get("mount", ""),
        sensor=_parse_sensor(data.get("sensor", {})),
        capabilities=_parse_capabilities(data.get("capabilities", {})),
        subject_detection=_parse_subject_detection(data.get("subject_detection", {})),
        iso_baseline=_parse_iso_baseline(data.get("iso_baseline", {})),
        drive_max_fps=_parse_drive_max_fps(data.get("drive_max_fps", {})),
    )


# ---------------------------------------------------------------------------
# Discovery and loading
# ---------------------------------------------------------------------------

def _builtin_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "body_profiles"


def _user_override_dir() -> Path:
    return user_data_dir() / "body_profiles"


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge override onto base, two levels deep.

    Body profiles have nested structures (capabilities, sensor, etc.) that
    users want to override partially. We deep-merge known nested dicts so
    users can override a single capability without rewriting the whole block.
    """
    merged = dict(base)
    for key, value in override.items():
        if (
            isinstance(value, dict)
            and key in merged
            and isinstance(merged[key], dict)
        ):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def load_body_profile(body_id: str) -> BodyProfile:
    """Load a body profile by id, applying user override over built-in."""
    with log_activity(log, f"loading body profile '{body_id}'"):
        builtin_path = _builtin_dir() / f"{body_id}.json"
        override_path = _user_override_dir() / f"{body_id}.json"

        base: dict[str, Any] = {}
        if builtin_path.exists():
            with builtin_path.open("r", encoding="utf-8") as f:
                base = json.load(f)
            log.debug("Loaded built-in body profile from %s", builtin_path)

        if override_path.exists():
            with override_path.open("r", encoding="utf-8") as f:
                override = json.load(f)
            base = _merge(base, override)
            log.info("Applied user override for body '%s' from %s",
                     body_id, override_path)

        if not base:
            raise FileNotFoundError(
                f"No body profile found for '{body_id}' "
                f"(looked in {builtin_path} and {override_path})"
            )

        profile = parse_body_profile(base)
        log.debug(
            "Parsed body profile '%s' (%s, crop=%.1fx, %d capabilities enabled)",
            profile.body_id,
            profile.kind,
            profile.sensor.crop_factor,
            sum(1 for v in profile.capabilities.__dict__.values() if v),
        )
        return profile


def list_available_body_profiles() -> list[str]:
    """List all body_ids available (built-in + user overrides), deduped."""
    ids: set[str] = set()
    for directory in (_builtin_dir(), _user_override_dir()):
        if directory.exists():
            for entry in directory.glob("*.json"):
                ids.add(entry.stem)
    return sorted(ids)


def match_body_profile_for_photo(exif: dict[str, Any]) -> Optional[BodyProfile]:
    """Find the body profile whose exiftool_model_match matches the photo's Model.

    Returns the first matching profile or None. If multiple profiles could
    match, the order is determined by alphabetical body_id — use distinctive
    model strings to avoid collisions.
    """
    exif_model = str(exif.get("Model", "")).strip()
    if not exif_model:
        return None
    for body_id in list_available_body_profiles():
        try:
            profile = load_body_profile(body_id)
        except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
            continue
        if profile.matches_model(exif_model):
            return profile
    return None


def build_stub_body_profile(
    exif: dict[str, Any],
    brand_id: str = "unknown",
) -> BodyProfile:
    """Build a conservative stub profile for an unknown body.

    Used during onboarding bootstrap when a user drops a photo from a camera
    the app has no built-in profile for. Defaults are intentionally
    conservative: all capabilities false, generic crop_factor, generic ISO.
    The stub will be refined as more bootstrap photos are analyzed.
    """
    make = str(exif.get("Make", "")).strip()
    model = str(exif.get("Model", "")).strip() or "Unknown Model"
    body_id = (model.lower().replace(" ", "_").replace("-", "_")) or "unknown_body"

    return BodyProfile(
        body_id=body_id,
        display_name=f"{make} {model}".strip() or model,
        brand_id=brand_id,
        kind="camera",
        exiftool_model_match=[model],
        sensor=Sensor(size="unknown", crop_factor=1.0),
        capabilities=Capabilities(),
        subject_detection=SubjectDetectionCapability(),
        iso_baseline=IsoBaseline(),
    )
