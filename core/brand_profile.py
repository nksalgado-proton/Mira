"""Brand profile loader and EXIF translator.

Brand profiles are JSON files that describe how to translate raw EXIF tag
values into the normalized vocabulary (see core/vocabulary.py). One profile
per camera brand. This is the only place that knows about brand-specific
strings like "AF-C" vs "AFC" vs "Continuous AF".

Usage:
    profile = load_brand_profile("panasonic")
    focus_mode = profile.translate_focus_mode("AFC")  # -> FocusMode.CONTINUOUS

Profile discovery:
    1. User override:  %APPDATA%/Mira/brand_profiles/{brand}.json
    2. Built-in:       assets/brand_profiles/{brand}.json
    User overrides are merged over built-in defaults (shallow merge per top-level key).

Matching by photo:
    profile = match_brand_profile_for_photo(exif_make="Panasonic")
    returns the first profile whose exiftool_make_match list contains a
    substring of the photo's Make tag.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.logging_setup import log_activity
from core.settings import user_data_dir
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


@dataclass
class TagMapping:
    """Maps raw EXIF string values to a normalized enum value.

    Attributes:
        exif_tag: primary ExifTool tag name to read
        exif_tag_alternatives: fallback tag names if primary is empty
        mapping: {normalized_value: [list of raw substrings that match]}
        default: normalized value when nothing matches
    """
    exif_tag: str
    exif_tag_alternatives: list[str] = field(default_factory=list)
    mapping: dict[str, list[str]] = field(default_factory=dict)
    default: str = "unknown"

    def translate(self, exif_values: dict[str, Any]) -> str:
        """Translate raw EXIF values to a normalized value.

        Looks up the primary tag, then alternatives in order, until one has
        a non-empty value. Then walks the mapping dict; the first normalized
        key whose substring list matches the raw value (case-insensitive)
        wins. Returns default if nothing matches.
        """
        raw_value = self._read_first_populated(exif_values)
        if raw_value is None:
            return self.default
        raw_lower = str(raw_value).strip().lower()
        if not raw_lower:
            return self.default
        for normalized, substrings in self.mapping.items():
            for substring in substrings:
                if substring.strip().lower() in raw_lower:
                    return normalized
        return self.default

    def _read_first_populated(self, exif: dict[str, Any]) -> Optional[Any]:
        for tag in [self.exif_tag, *self.exif_tag_alternatives]:
            if tag in exif and exif[tag] not in (None, "", []):
                return exif[tag]
        return None


@dataclass
class BracketRule:
    """Describes how to detect a specific bracket type from EXIF."""
    exif_tag: str
    is_active_when: str = ""          # e.g. "value > 0"
    active_values: list[str] = field(default_factory=list)

    def is_active(self, exif_values: dict[str, Any]) -> bool:
        raw = exif_values.get(self.exif_tag)
        if raw in (None, "", []):
            return False
        if self.active_values:
            raw_str = str(raw).strip().lower()
            return any(v.strip().lower() in raw_str for v in self.active_values)
        if self.is_active_when:
            # Only "value > 0" supported for now; extend if needed
            if self.is_active_when.strip() == "value > 0":
                try:
                    return float(str(raw).strip()) > 0
                except (ValueError, TypeError):
                    return False
        return False


_VALID_BOUNDS = ("lower", "upper", "midpoint")


@dataclass
class FocusPositionRule:
    """How a brand encodes "where in the focus range is the lens
    pointing" — normalized to [0, 1] where 0 = at minimum focus
    distance (macro range) and 1 = at infinity.

    The EXIF 2.x ``SubjectDistance`` / ``SubjectDistanceRange`` tags
    would be the universal answer, but Panasonic (the QA-reach brand)
    doesn't write them — it writes ``FocusStepNear`` / ``FocusStepCount``
    (motor steps), so the brand-profile layer absorbs the difference
    and exposes a normalized scalar that rules can query uniformly.

    Supported kinds (extend as you encounter new brands):

      - ``step_ratio``: ``near_tag / count_tag`` (Panasonic).
        ``FocusStepNear=0`` → 0.0 (closest), ``FocusStepNear=count``
        → 1.0 (infinity).
      - ``meters_scaled``: read ``meters_tag``, clamp to ``[0, max_meters]``,
        divide by ``max_meters``. Approximate but useful when a brand
        writes ``SubjectDistance`` in meters (some Sony, some Canon).
        < 0.5m → ~0.05; > max_meters → 1.0.
      - ``meters_range``: read ``meters_tag`` as a string like
        ``"0.12 - 0.16 m"`` (Apple's ``FocusDistanceRange`` in iPhone
        MakerNotes). Uses the lower bound (closest focal plane) /
        ``max_meters``. Front-camera selfies don't write the field;
        rule returns None and the higher-priority phone_selfie rule
        handles them.
      - ``subject_distance_range``: map EXIF 2.x ``SubjectDistanceRange``
        enum (Unknown / Macro / Close / Distant) to representative
        scalars (None / 0.0 / 0.5 / 0.9). Coarse but the official
        signal when cameras bother to write it.

    When the configured tags are missing or unparseable, ``compute()``
    returns ``None`` — the rule layer must handle this (rules with
    ``focus_position_normalized`` predicates simply won't fire).
    """
    kind: str
    near_tag: str = ""
    count_tag: str = ""
    meters_tag: str = ""
    max_meters: float = 5.0
    range_tag: str = ""
    # Which side of a range tag to consume — only used by ``meters_range``.
    # "lower" = closest plane in focus (default; loose macro filter).
    # "upper" = farthest plane in focus (Apple's FocusDistanceRange:
    #           catches genuine tight-DOF macros without false-positives
    #           on hyperfocal landscapes whose lower bound is close).
    # "midpoint" = (lower + upper) / 2.
    bound: str = "lower"

    def compute(self, exif: dict[str, Any]) -> Optional[float]:
        if self.kind == "step_ratio":
            return self._step_ratio(exif)
        if self.kind == "meters_scaled":
            return self._meters_scaled(exif)
        if self.kind == "meters_range":
            return self._meters_range(exif)
        if self.kind == "subject_distance_range":
            return self._subject_distance_range(exif)
        log.warning(
            "FocusPositionRule kind %r not recognized — returning None",
            self.kind,
        )
        return None

    def _step_ratio(self, exif: dict[str, Any]) -> Optional[float]:
        near = _coerce_number(exif.get(self.near_tag))
        count = _coerce_number(exif.get(self.count_tag))
        if near is None or count is None or count <= 0:
            return None
        ratio = near / count
        # Clamp into [0, 1] — some firmware writes step values slightly
        # outside the nominal range (e.g. focus motor calibration jitter).
        return max(0.0, min(1.0, ratio))

    def _meters_scaled(self, exif: dict[str, Any]) -> Optional[float]:
        meters = _coerce_number(exif.get(self.meters_tag))
        if meters is None or meters < 0 or self.max_meters <= 0:
            return None
        return min(1.0, meters / self.max_meters)

    def _meters_range(self, exif: dict[str, Any]) -> Optional[float]:
        """Parse a string like ``"0.12 - 0.16 m"`` (Apple
        FocusDistanceRange). Picks the lower / upper / midpoint per
        the rule's ``bound`` config and normalizes over ``max_meters``.
        Returns None when the field is absent or unparseable.

        For Apple specifically, ``bound="upper"`` is the right macro
        discriminator: tight-DOF macros (0.12-0.16m) keep upper close;
        hyperfocal landscapes (0.23-1.90m) have far upper despite the
        close lower bound. Verified on Costa Rica iPhone samples
        2026-05-13.
        """
        raw = exif.get(self.meters_tag)
        if raw is None or raw == "":
            return None
        s = str(raw).strip().lower()
        for suffix in (" m", "m"):
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                break
        if "-" not in s:
            value = _coerce_number(s)
        else:
            lo_str, _, hi_str = s.partition("-")
            lower = _coerce_number(lo_str)
            upper = _coerce_number(hi_str)
            if self.bound == "upper":
                value = upper if upper is not None else lower
            elif self.bound == "midpoint":
                if lower is not None and upper is not None:
                    value = (lower + upper) / 2
                else:
                    value = upper if upper is not None else lower
            else:   # "lower" default
                value = lower
        if value is None or value < 0 or self.max_meters <= 0:
            return None
        return min(1.0, value / self.max_meters)

    def _subject_distance_range(self, exif: dict[str, Any]) -> Optional[float]:
        value = exif.get(self.range_tag)
        if value is None or value == "":
            return None
        s = str(value).strip().lower()
        if "unknown" in s:
            return None
        if "macro" in s:
            return 0.0
        if "close" in s:
            return 0.5
        if "distant" in s:
            return 0.9
        return None


def _coerce_number(value: Any) -> Optional[float]:
    """Parse a numeric EXIF field, returning ``None`` for blank /
    non-numeric / units-only inputs. Strips ' m', ' s' suffixes some
    brands append."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    for suffix in (" m", "m", " s"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class BurstSequenceRule:
    """Reads the per-frame in-burst sequence index from EXIF.

    When a camera shoots in continuous-drive mode it usually writes a
    frame counter that resets to 1 at the start of each new burst
    (Panasonic's ``SequenceNumber`` is the canonical example — confirmed
    on the G9 MkII in the Costa Rica field test). The bucket scanner
    uses this signal to find burst boundaries deterministically: a run
    of monotonically increasing values is one burst; a reset (or a None)
    closes it and starts the next.

    Brand profiles that don't declare a sequence tag (or are loaded for
    cameras that don't emit one) fall back to time-gap clustering inside
    the bucket scanner.
    """
    exif_tag: str

    def read(self, exif: dict[str, Any]) -> Optional[int]:
        """Return the integer sequence index, or None when absent/invalid.

        Zero / blank / non-numeric values map to None (not a burst frame).
        """
        if not self.exif_tag:
            return None
        raw = exif.get(self.exif_tag)
        if raw in (None, "", []):
            return None
        try:
            n = int(float(str(raw).strip()))
        except (ValueError, TypeError):
            return None
        return n if n > 0 else None


@dataclass
class LensAlias:
    canonical: str
    matches: list[str]


@dataclass
class LensNormalization:
    """Configuration for reading and canonicalizing lens names from EXIF.

    Attributes:
        lens_model_tag: primary EXIF tag to read (e.g. "LensModel")
        lens_model_tag_alternatives: fallback tag names tried in order if
            the primary tag is empty. Important for cameras that write the
            lens info under a different tag when a third-party lens is
            mounted (e.g. Panasonic G9 2017 firmware leaves LensModel empty
            for Olympus lenses but populates LensType/LensID).
        lens_id_tag: optional numeric lens ID tag (e.g. "LensID" for Sony);
            not used directly by canonicalize but available for future
            lookup-table-based matching.
        aliases: canonical-name aliasing rules applied to the raw value.
    """
    lens_model_tag: str = "LensModel"
    lens_model_tag_alternatives: list[str] = field(default_factory=list)
    lens_id_tag: Optional[str] = None
    aliases: list[LensAlias] = field(default_factory=list)

    def read_raw_lens(self, exif: dict[str, Any]) -> str:
        """Walk lens_model_tag + alternatives and return the first non-empty value."""
        for tag in [self.lens_model_tag, *self.lens_model_tag_alternatives]:
            if not tag:
                continue
            value = str(exif.get(tag, "")).strip()
            if value:
                return value
        return ""

    def canonicalize(self, lens_model: str) -> str:
        """Return the canonical name if the raw lens string matches an alias,
        otherwise return the raw value stripped. Case-insensitive substring match.
        """
        if not lens_model:
            return ""
        lens_lower = lens_model.strip().lower()
        for alias in self.aliases:
            for m in alias.matches:
                if m.strip().lower() in lens_lower:
                    return alias.canonical
        return lens_model.strip()


@dataclass(frozen=True)
class AfPoint:
    """Where the camera focused, in **normalized image coordinates**
    (0..1, origin top-left). ``cx, cy`` = box centre; ``w, h`` = box
    size as a fraction of the frame. Brand-agnostic by construction:
    the culler's AF overlay + pan-seed (docs/18 E7) consume this and
    never touch a maker-note tag."""
    cx: float
    cy: float
    w: float
    h: float


@dataclass
class AfPointRule:
    """How a brand encodes the AF point/box in its maker notes,
    normalized to an :class:`AfPoint` (docs/18 §"AF-point from EXIF").

    The pixel rectangle is brand-specific (Lumix vs Sony write it
    completely differently), so — exactly like ``FocusPositionRule``
    — this rule absorbs the difference and the rest of the app reads
    one normalized shape. Per the project invariant: brand-specific
    EXIF interpretation lives here / in the JSON profile; rules stay
    brand-agnostic.

    Supported kinds (extend as new bodies are characterised):

      - ``normalized_xy``: a single ``xy_tag`` holding ``"cx cy"``
        already in 0..1 (Panasonic ``AFPointPosition``). The box
        size isn't written → ``default_box`` fraction is used.
      - ``pixel_xy_image_size``: ``x_tag`` / ``y_tag`` in pixels +
        ``image_w_tag`` / ``image_h_tag`` → normalize by the frame.
      - ``sony_focus_location``: one ``location_tag`` with four
        numbers ``"imgW imgH afX afY"`` (Sony ``FocusLocation``).
        Optional ``frame_size_tag`` ``"w h"`` (Sony
        ``FocusFrameSize``, pixels) for the real box size; else
        ``default_box``.
      - ``mwg_face_regions``: the standard Metadata-Working-Group
        region schema (XMP-mwg-rs) — ``RegionType`` +
        ``RegionArea{X,Y,W,H}`` (X/Y = region *centre*, normalized;
        already 0..1). iPhone HEIC writes face regions here; also
        Lightroom and others. Brand-agnostic *schema*, declared per
        brand. Picks a ``Focus`` region (literally the AF point in
        MWG) if present, else the **largest** ``Face``/``Pet`` (the
        subject the camera locked). No tag config — fixed schema.

    Missing / unparseable tags → ``compute()`` returns ``None`` (the
    overlay simply doesn't draw and the pan-seed tier is skipped —
    graceful degradation per docs/18: AF is a suggestion, never a
    lock).
    """
    kind: str
    xy_tag: str = ""
    x_tag: str = ""
    y_tag: str = ""
    image_w_tag: str = ""
    image_h_tag: str = ""
    location_tag: str = ""
    frame_size_tag: str = ""
    # Box side as a fraction of the frame when the brand doesn't
    # write a size. ~8% is a reasonable AF-area footprint.
    default_box: float = 0.08

    def compute(self, exif: dict[str, Any]) -> Optional[AfPoint]:
        try:
            if self.kind == "normalized_xy":
                return self._normalized_xy(exif)
            if self.kind == "pixel_xy_image_size":
                return self._pixel_xy(exif)
            if self.kind == "sony_focus_location":
                return self._sony_focus_location(exif)
            if self.kind == "mwg_face_regions":
                return self._mwg_face_regions(exif)
        except (ValueError, TypeError, ZeroDivisionError):
            return None
        log.warning(
            "AfPointRule kind %r not recognized — returning None", self.kind,
        )
        return None

    @staticmethod
    def _nums(raw: Any) -> list[float]:
        """Parse a maker-note value into floats. Accepts ``"a b c"``,
        ``"a, b"``, lists/tuples. Empty on anything unparseable."""
        if raw in (None, "", []):
            return []
        if isinstance(raw, (list, tuple)):
            seq = raw
        else:
            seq = str(raw).replace(",", " ").split()
        out: list[float] = []
        for tok in seq:
            try:
                out.append(float(str(tok).strip()))
            except (ValueError, TypeError):
                return []
        return out

    def _mk(self, cx: float, cy: float, w: float, h: float) -> AfPoint:
        c = lambda v: 0.0 if v < 0.0 else 1.0 if v > 1.0 else v  # noqa: E731
        return AfPoint(c(cx), c(cy), c(w), c(h))

    def _normalized_xy(self, exif: dict[str, Any]) -> Optional[AfPoint]:
        n = self._nums(exif.get(self.xy_tag))
        if len(n) < 2:
            return None
        return self._mk(n[0], n[1], self.default_box, self.default_box)

    def _pixel_xy(self, exif: dict[str, Any]) -> Optional[AfPoint]:
        xs = self._nums(exif.get(self.x_tag))
        ys = self._nums(exif.get(self.y_tag))
        iw = self._nums(exif.get(self.image_w_tag))
        ih = self._nums(exif.get(self.image_h_tag))
        if not (xs and ys and iw and ih) or iw[0] <= 0 or ih[0] <= 0:
            return None
        return self._mk(
            xs[0] / iw[0], ys[0] / ih[0], self.default_box, self.default_box,
        )

    def _sony_focus_location(
        self, exif: dict[str, Any],
    ) -> Optional[AfPoint]:
        loc = self._nums(exif.get(self.location_tag))
        if len(loc) < 4 or loc[0] <= 0 or loc[1] <= 0:
            return None
        img_w, img_h, af_x, af_y = loc[0], loc[1], loc[2], loc[3]
        w = h = self.default_box
        if self.frame_size_tag:
            fs = self._nums(exif.get(self.frame_size_tag))
            if len(fs) >= 2 and img_w > 0 and img_h > 0:
                w, h = fs[0] / img_w, fs[1] / img_h
        return self._mk(af_x / img_w, af_y / img_h, w, h)

    @staticmethod
    def _strs(raw: Any) -> list[str]:
        """Coerce a maybe-list maker-note value to a list of strs
        (``RegionType`` is a list when several regions exist, a
        scalar when one)."""
        if raw in (None, "", []):
            return []
        if isinstance(raw, (list, tuple)):
            return [str(x) for x in raw]
        return [str(raw)]

    # exiftool's human-readable Orientation strings → EXIF code 1-8.
    _ORIENT_NAMES = {
        "horizontal (normal)": 1,
        "mirror horizontal": 2,
        "rotate 180": 3,
        "mirror vertical": 4,
        "mirror horizontal and rotate 270 cw": 5,
        "rotate 90 cw": 6,
        "mirror horizontal and rotate 90 cw": 7,
        "rotate 270 cw": 8,
    }

    @classmethod
    def _orientation_code(cls, raw: Any) -> int:
        """EXIF Orientation as 1-8. Accepts exiftool's words or an
        int; anything unknown/absent → 1 (identity, safe)."""
        if raw in (None, ""):
            return 1
        if isinstance(raw, (int, float)):
            n = int(raw)
            return n if 1 <= n <= 8 else 1
        return cls._ORIENT_NAMES.get(str(raw).strip().lower(), 1)

    @staticmethod
    def _orient_norm(
        cx: float, cy: float, w: float, h: float, code: int,
    ) -> tuple[float, float, float, float]:
        """Map a normalized centre+size from the STORED (sensor)
        frame to the displayed EXIF-upright frame. MWG regions are
        relative to the un-rotated buffer; the canvas shows the
        exif-transposed image (HEIC via ImageOps.exif_transpose), so
        without this the box lands wrong on every rotated phone shot
        (Nelson 2026-05-16: 'Rotate 90/180' photos misplaced)."""
        if code == 2:                       # mirror horizontal
            return 1.0 - cx, cy, w, h
        if code == 3:                       # rotate 180
            return 1.0 - cx, 1.0 - cy, w, h
        if code == 4:                       # mirror vertical
            return cx, 1.0 - cy, w, h
        if code == 5:                       # transpose
            return cy, cx, h, w
        if code == 6:                       # rotate 90 CW
            return 1.0 - cy, cx, h, w
        if code == 7:                       # transverse
            return 1.0 - cy, 1.0 - cx, h, w
        if code == 8:                       # rotate 270 CW
            return cy, 1.0 - cx, h, w
        return cx, cy, w, h                  # 1 (or unknown): identity

    def _mwg_face_regions(self, exif: dict[str, Any]) -> Optional[AfPoint]:
        """MWG region schema → one AfPoint. ``RegionAreaX/Y`` are the
        region *centre* (normalized), ``W/H`` the size. Parallel
        arrays indexed by region. Prefer a ``Focus`` region (the
        literal AF point per MWG); else the largest ``Face``/``Pet``
        (the subject the camera locked onto). The result is rotated
        into the displayed (EXIF-upright) frame via ``Orientation``
        — MWG coords are in the un-rotated sensor buffer."""
        xs = self._nums(exif.get("RegionAreaX"))
        ys = self._nums(exif.get("RegionAreaY"))
        if not xs or not ys:
            return None
        ws = self._nums(exif.get("RegionAreaW"))
        hs = self._nums(exif.get("RegionAreaH"))
        types = self._strs(exif.get("RegionType"))
        best: Optional[tuple] = None     # ((prio, area), i, w, h)
        for i in range(min(len(xs), len(ys))):
            t = (types[i] if i < len(types) else "").strip().lower()
            w = ws[i] if i < len(ws) and ws[i] > 0 else self.default_box
            h = hs[i] if i < len(hs) and hs[i] > 0 else self.default_box
            prio = 2 if t == "focus" else 1 if t in ("face", "pet") else 0
            key = (prio, max(w, 0.0) * max(h, 0.0))
            if best is None or key > best[0]:
                best = (key, i, w, h)
        if best is None:
            return None
        _, i, w, h = best
        code = self._orientation_code(exif.get("Orientation"))
        cx, cy, w, h = self._orient_norm(xs[i], ys[i], w, h, code)
        return self._mk(cx, cy, w, h)


@dataclass
class BrandProfile:
    brand_id: str
    display_name: str
    version: int
    exiftool_make_match: list[str]

    focus_mode: TagMapping
    af_area_mode: TagMapping
    subject_detection: TagMapping
    drive_mode: TagMapping

    # Optional — newer addition. Older profiles may not declare it; parsed
    # as an empty TagMapping defaulting to "unknown" when missing.
    photo_style: TagMapping = field(
        default_factory=lambda: TagMapping(exif_tag="", default="unknown")
    )
    # Optional — same pattern as photo_style. The exposure/mode-dial
    # signal (Panasonic ShootingMode, Sony's mode field, EXIF
    # ExposureProgram fallback). Brand-specific raw → normalized
    # ShootingMode translation happens inside the brand profile;
    # classifier rules consume the normalized value only.
    shooting_mode: TagMapping = field(
        default_factory=lambda: TagMapping(exif_tag="", default="unknown")
    )

    focus_bracket: Optional[BracketRule] = None
    exposure_bracket: Optional[BracketRule] = None

    # Per-frame burst sequence index reader. None means the profile
    # doesn't declare one — the bucket scanner falls back to time-gap
    # clustering for that brand.
    burst_sequence: Optional[BurstSequenceRule] = None

    # Brand-aware normalized focus position reader. None means the
    # brand profile doesn't declare a focus-position signal; the
    # corresponding PhotoContext field stays None and rules that
    # query it won't fire. See FocusPositionRule for supported kinds.
    focus_position: Optional[FocusPositionRule] = None

    # Brand-aware AF-point reader (docs/18 E7). None → the profile
    # doesn't declare it; the culler's AF overlay won't draw and the
    # AF pan-seed tier is skipped (box-zoom still works on the
    # manual-override / global-default chain — AF is an accelerator,
    # not a prerequisite).
    af_point: Optional[AfPointRule] = None

    lens_normalization: LensNormalization = field(default_factory=LensNormalization)

    # F-019 (2026-05-25): per-model "how to set the clock/TZ correctly on
    # this camera" instructions, surfaced by the pre-ingest plan-confirm
    # dialog whenever the engine detects a TZ mismatch (or just as a
    # one-shot reminder when nothing is wrong). Keys: the EXIF Model
    # string (matches ``camera_id_for`` — Model alone, brand-aware). A
    # special ``"_default"`` key holds the brand-wide fallback used when
    # the user's specific Model isn't listed. Values are short ordered
    # step lists rendered as a bulleted block. Empty dict = profile
    # didn't declare it; the dialog hides the tip block in that case.
    tz_setting_instructions: dict[str, list[str]] = field(default_factory=dict)

    def matches_make(self, exif_make: str) -> bool:
        if not exif_make:
            return False
        make_lower = exif_make.strip().lower()
        return any(m.strip().lower() in make_lower for m in self.exiftool_make_match)

    def translate_focus_mode(self, exif: dict[str, Any]) -> FocusMode:
        return FocusMode(self.focus_mode.translate(exif))

    def translate_af_area_mode(self, exif: dict[str, Any]) -> AfAreaMode:
        return AfAreaMode(self.af_area_mode.translate(exif))

    def translate_subject_detection(self, exif: dict[str, Any]) -> SubjectDetection:
        return SubjectDetection(self.subject_detection.translate(exif))

    def translate_drive_mode(self, exif: dict[str, Any]) -> DriveMode:
        return DriveMode(self.drive_mode.translate(exif))

    def translate_photo_style(self, exif: dict[str, Any]) -> PhotoStyle:
        """Translate the photographer's style/creative-intent setting.

        Panasonic uses PhotoStyle, Sony uses CreativeStyle / CreativeLook,
        others vary. Each brand profile declares its own tag and value
        mappings via the photo_style TagMapping. Returns PhotoStyle.UNKNOWN
        when the tag is missing, empty, set to "Off", or unmapped.
        """
        # If the brand profile didn't declare a photo_style tag at all,
        # skip the translate — the TagMapping's empty exif_tag would read
        # nothing meaningful.
        if not self.photo_style.exif_tag:
            return PhotoStyle.UNKNOWN
        try:
            return PhotoStyle(self.photo_style.translate(exif))
        except ValueError:
            return PhotoStyle.UNKNOWN

    def translate_shooting_mode(self, exif: dict[str, Any]) -> ShootingMode:
        """Translate the camera's exposure/mode-dial setting.

        Panasonic uses MakerNotes ShootingMode ("Intelligent Auto" /
        "P" / "A" / "S" / "M" / "C1" etc.); Sony writes similar in its
        own MakerNotes; standard EXIF ``ExposureProgram`` is a brand-
        agnostic fallback (value 0 = unknown, 2 = program, 8 = scene,
        etc.). Each brand profile declares its own tag + mappings via
        the ``shooting_mode`` :class:`TagMapping`. Returns
        :attr:`ShootingMode.UNKNOWN` when the profile didn't declare a
        tag (phones don't have a mode dial), when the raw value is
        empty, or when nothing matches.

        Companion to :meth:`translate_photo_style` — same shape, same
        defensive behaviour when the optional block is omitted from
        the JSON.
        """
        if not self.shooting_mode.exif_tag:
            return ShootingMode.UNKNOWN
        try:
            return ShootingMode(self.shooting_mode.translate(exif))
        except ValueError:
            return ShootingMode.UNKNOWN

    def tip_for_model(self, model: str) -> list[str]:
        """F-019: ordered steps the pre-ingest dialog renders to help the
        user set this camera's clock/TZ correctly going forward.

        Resolution order: (1) exact ``model`` match in
        :attr:`tz_setting_instructions`; (2) brand-wide ``"_default"``
        fallback; (3) empty list (the dialog hides the tip block).
        ``model`` is matched verbatim against the dict keys — the engine
        passes the EXIF ``Model`` field through ``camera_id_for`` first
        so the key shape is consistent (e.g. ``"DC-G9M2"``, not
        ``"Panasonic DC-G9M2"``).
        """
        if not self.tz_setting_instructions:
            return []
        if model and model in self.tz_setting_instructions:
            return list(self.tz_setting_instructions[model])
        if "_default" in self.tz_setting_instructions:
            return list(self.tz_setting_instructions["_default"])
        return []

    def detect_bracket(self, exif: dict[str, Any]) -> BracketType:
        if self.focus_bracket and self.focus_bracket.is_active(exif):
            return BracketType.FOCUS
        if self.exposure_bracket and self.exposure_bracket.is_active(exif):
            return BracketType.EXPOSURE
        return BracketType.NONE

    def is_continuous_shooting(self, exif: dict[str, Any]) -> bool:
        """True when the camera was in continuous-drive (burst) mode.

        Brand-aware reading of the drive-mode tag (Panasonic's
        ``BurstMode``, Sony's ``DriveMode``, etc.) via the same TagMapping
        the classifier uses. Callers should OR this with a brand-agnostic
        substring fallback so unknown brands still get a best-effort
        detection.
        """
        return self.translate_drive_mode(exif) in (
            DriveMode.BURST_LOW, DriveMode.BURST_HIGH,
        )

    def focus_position_normalized(self, exif: dict[str, Any]) -> Optional[float]:
        """Where in the focus range is the lens pointing — [0, 1].

        0 = at minimum focus distance (macro range);
        1 = at infinity;
        None = the brand doesn't write enough info to tell.

        The brand profile encapsulates the EXIF-tag math (Panasonic
        uses motor steps, Sony might use meters, etc.) so rules query
        a single normalized concept and stay brand-agnostic.
        """
        if self.focus_position is None:
            return None
        return self.focus_position.compute(exif)

    def read_af_point(self, exif: dict[str, Any]) -> Optional[AfPoint]:
        """The AF point/box as a normalized :class:`AfPoint`, or
        ``None`` when the brand doesn't declare an af_point rule or
        the maker-note tags are absent/unparseable for this frame.

        The brand profile encapsulates the maker-note math (Lumix
        ``AFPointPosition`` normalized pair, Sony ``FocusLocation``
        pixel 4-tuple, …) so the culler reads one brand-agnostic
        shape (docs/18 §"AF-point from EXIF")."""
        if self.af_point is None:
            return None
        return self.af_point.compute(exif)

    def is_close_focus(self, exif: dict[str, Any], threshold: float = 0.2) -> bool:
        """Convenience: True when ``focus_position_normalized < threshold``.
        Returns False when the brand can't read focus position (rather
        than raising) so callers can use it in boolean contexts."""
        pos = self.focus_position_normalized(exif)
        return pos is not None and pos < threshold

    def detect_burst(self, exif: dict[str, Any]) -> Optional[int]:
        """Return the in-burst sequence index for this frame, or None.

        None means: either the camera isn't writing the brand's burst-
        sequence tag (so this frame isn't part of a burst as far as the
        camera's own metadata is concerned), OR the brand profile doesn't
        declare a burst-sequence tag at all. In both cases the bucket
        scanner falls back to time-gap clustering of frames whose
        ``continuous_shooting_active`` is True.

        Non-None values come back as positive integers that reset to 1
        at the start of each new burst — the scanner groups runs of
        strictly-increasing values into one BurstSequence, closing at
        a reset (or a None).
        """
        if self.burst_sequence is None:
            return None
        return self.burst_sequence.read(exif)

    def canonicalize_lens(self, exif: dict[str, Any]) -> str:
        """Return the canonical lens name for this photo, or "" if no lens info.

        Reads the primary lens tag declared in the brand profile, falling
        back to alternative tags (e.g. LensType, LensID) if the primary is
        empty. This handles cameras like the Panasonic G9 (2017 firmware)
        that leave LensModel empty for third-party lenses but populate
        LensType via maker notes.
        """
        raw = self.lens_normalization.read_raw_lens(exif)
        return self.lens_normalization.canonicalize(raw)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _parse_tag_mapping(data: dict[str, Any]) -> TagMapping:
    return TagMapping(
        exif_tag=data.get("exif_tag", ""),
        exif_tag_alternatives=data.get("exif_tag_alternatives", []),
        mapping=data.get("mapping", {}),
        default=data.get("default", "unknown"),
    )


def _parse_bracket_rule(data: Optional[dict[str, Any]]) -> Optional[BracketRule]:
    if not data:
        return None
    return BracketRule(
        exif_tag=data.get("exif_tag", ""),
        is_active_when=data.get("is_active_when", ""),
        active_values=data.get("active_values", []),
    )


def _parse_burst_sequence_rule(
    data: Optional[dict[str, Any]],
) -> Optional[BurstSequenceRule]:
    """Parse a ``burst_detection.sequence_tag`` block.

    Returns None when the brand profile omits the block — the scanner
    then falls back to time-gap clustering for that brand.
    """
    if not data:
        return None
    tag = data.get("sequence_tag", "")
    if not tag:
        return None
    return BurstSequenceRule(exif_tag=str(tag))


def _parse_focus_position_rule(
    data: Optional[dict[str, Any]],
) -> Optional[FocusPositionRule]:
    """Parse a ``focus_position`` block.

    Returns None when the brand profile omits the block or declares a
    ``kind`` without the corresponding tag fields — the rule simply
    won't fire for that brand.
    """
    if not data:
        return None
    kind = data.get("kind", "")
    if kind == "step_ratio":
        near = data.get("near_tag", "")
        count = data.get("count_tag", "")
        if not near or not count:
            return None
        return FocusPositionRule(
            kind="step_ratio", near_tag=str(near), count_tag=str(count),
        )
    if kind == "meters_scaled":
        meters = data.get("meters_tag", "")
        if not meters:
            return None
        return FocusPositionRule(
            kind="meters_scaled",
            meters_tag=str(meters),
            max_meters=float(data.get("max_meters", 5.0)),
        )
    if kind == "meters_range":
        meters = data.get("meters_tag", "")
        if not meters:
            return None
        bound = str(data.get("bound", "lower"))
        if bound not in _VALID_BOUNDS:
            log.warning(
                "FocusPositionRule meters_range bound=%r not in %r — "
                "falling back to 'lower'", bound, _VALID_BOUNDS,
            )
            bound = "lower"
        return FocusPositionRule(
            kind="meters_range",
            meters_tag=str(meters),
            max_meters=float(data.get("max_meters", 5.0)),
            bound=bound,
        )
    if kind == "subject_distance_range":
        range_tag = data.get("range_tag", "SubjectDistanceRange")
        return FocusPositionRule(
            kind="subject_distance_range", range_tag=str(range_tag),
        )
    return None


def _parse_af_point_rule(
    data: Optional[dict[str, Any]],
) -> Optional[AfPointRule]:
    """Parse an ``af_point`` block. None when omitted or a ``kind``
    is declared without its required tags (the rule simply won't
    fire — graceful, per docs/18)."""
    if not data:
        return None
    kind = data.get("kind", "")
    box = float(data.get("default_box", 0.08))
    if kind == "normalized_xy":
        xy = data.get("xy_tag", "")
        if not xy:
            return None
        return AfPointRule(
            kind="normalized_xy", xy_tag=str(xy), default_box=box,
        )
    if kind == "pixel_xy_image_size":
        xt = data.get("x_tag", "")
        yt = data.get("y_tag", "")
        iw = data.get("image_w_tag", "")
        ih = data.get("image_h_tag", "")
        if not (xt and yt and iw and ih):
            return None
        return AfPointRule(
            kind="pixel_xy_image_size",
            x_tag=str(xt), y_tag=str(yt),
            image_w_tag=str(iw), image_h_tag=str(ih),
            default_box=box,
        )
    if kind == "sony_focus_location":
        loc = data.get("location_tag", "")
        if not loc:
            return None
        return AfPointRule(
            kind="sony_focus_location",
            location_tag=str(loc),
            frame_size_tag=str(data.get("frame_size_tag", "")),
            default_box=box,
        )
    if kind == "mwg_face_regions":
        # Fixed standard schema — no per-brand tag config needed.
        return AfPointRule(kind="mwg_face_regions", default_box=box)
    return None


def _parse_lens_normalization(data: Optional[dict[str, Any]]) -> LensNormalization:
    if not data:
        return LensNormalization()
    aliases = [
        LensAlias(canonical=a["canonical"], matches=a.get("matches", []))
        for a in data.get("aliases", [])
    ]
    return LensNormalization(
        lens_model_tag=data.get("lens_model_tag", "LensModel"),
        lens_model_tag_alternatives=list(data.get("lens_model_tag_alternatives", [])),
        lens_id_tag=data.get("lens_id_tag"),
        aliases=aliases,
    )


def parse_brand_profile(data: dict[str, Any]) -> BrandProfile:
    """Build a BrandProfile from a parsed JSON dict."""
    # photo_style is optional — profiles that don't declare it get an empty
    # TagMapping that always returns "unknown"
    if "photo_style" in data:
        photo_style = _parse_tag_mapping(data["photo_style"])
    else:
        photo_style = TagMapping(exif_tag="", default="unknown")

    # shooting_mode follows the same pattern. Phones don't have a
    # mode-dial concept so their profiles omit this block.
    if "shooting_mode" in data:
        shooting_mode = _parse_tag_mapping(data["shooting_mode"])
    else:
        shooting_mode = TagMapping(exif_tag="", default="unknown")

    return BrandProfile(
        brand_id=data["brand_id"],
        display_name=data.get("display_name", data["brand_id"]),
        version=int(data.get("version", 1)),
        exiftool_make_match=data.get("exiftool_make_match", []),
        focus_mode=_parse_tag_mapping(data.get("focus_mode", {})),
        af_area_mode=_parse_tag_mapping(data.get("af_area_mode", {})),
        subject_detection=_parse_tag_mapping(data.get("subject_detection", {})),
        drive_mode=_parse_tag_mapping(data.get("drive_mode", {})),
        photo_style=photo_style,
        shooting_mode=shooting_mode,
        focus_bracket=_parse_bracket_rule(
            data.get("bracket_detection", {}).get("focus_bracket")
        ),
        exposure_bracket=_parse_bracket_rule(
            data.get("bracket_detection", {}).get("exposure_bracket")
        ),
        burst_sequence=_parse_burst_sequence_rule(
            data.get("burst_detection")
        ),
        focus_position=_parse_focus_position_rule(
            data.get("focus_position")
        ),
        af_point=_parse_af_point_rule(data.get("af_point")),
        lens_normalization=_parse_lens_normalization(data.get("lens_normalization")),
        tz_setting_instructions=_parse_tz_setting_instructions(
            data.get("tz_setting_instructions")),
    )


def _parse_tz_setting_instructions(
    raw: Optional[dict[str, Any]],
) -> dict[str, list[str]]:
    """F-019: parse the optional ``tz_setting_instructions`` block.
    Defensively coerce each value to a list of strings so a malformed
    profile never raises here — the worst case is an empty dict that
    the dialog interprets as "no tip available."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, list):
            continue
        steps = [str(s) for s in value if isinstance(s, (str, int, float))]
        if steps:
            out[key] = steps
    return out


def _builtin_dir() -> Path:
    """Path to bundled brand profiles directory."""
    return Path(__file__).resolve().parent.parent / "assets" / "brand_profiles"


def _user_override_dir() -> Path:
    """Path to user override directory under the app's user data dir."""
    return user_data_dir() / "brand_profiles"


def _shallow_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge override onto base, one level deep.

    Top-level keys in override replace those in base. For known nested
    structures (tag mapping blocks), we merge the mapping dict deeply so
    users can add one new mapping entry without rewriting the whole block.
    """
    merged = dict(base)
    for key, value in override.items():
        if (
            isinstance(value, dict)
            and key in merged
            and isinstance(merged[key], dict)
            and "mapping" in value
            and "mapping" in merged[key]
        ):
            merged_block = dict(merged[key])
            merged_mapping = dict(merged_block["mapping"])
            for norm_val, substrings in value["mapping"].items():
                existing = merged_mapping.get(norm_val, [])
                merged_mapping[norm_val] = list({*existing, *substrings})
            merged_block["mapping"] = merged_mapping
            for other_key, other_val in value.items():
                if other_key != "mapping":
                    merged_block[other_key] = other_val
            merged[key] = merged_block
        else:
            merged[key] = value
    return merged


def load_brand_profile(brand_id: str) -> BrandProfile:
    """Load a brand profile by id, applying user override over built-in.

    Raises:
        FileNotFoundError: if neither built-in nor override exists
        KeyError/ValueError: if the JSON is structurally invalid
    """
    with log_activity(log, f"loading brand profile '{brand_id}'"):
        builtin_path = _builtin_dir() / f"{brand_id}.json"
        override_path = _user_override_dir() / f"{brand_id}.json"

        base: dict[str, Any] = {}
        if builtin_path.exists():
            with builtin_path.open("r", encoding="utf-8") as f:
                base = json.load(f)
            log.debug("Loaded built-in profile from %s", builtin_path)

        if override_path.exists():
            with override_path.open("r", encoding="utf-8") as f:
                override = json.load(f)
            base = _shallow_merge(base, override)
            log.info("Applied user override for brand '%s' from %s",
                     brand_id, override_path)

        if not base:
            raise FileNotFoundError(
                f"No brand profile found for '{brand_id}' "
                f"(looked in {builtin_path} and {override_path})"
            )

        profile = parse_brand_profile(base)
        log.debug(
            "Parsed brand profile '%s' (version %d, %d make patterns)",
            profile.brand_id, profile.version, len(profile.exiftool_make_match),
        )
        return profile


def list_available_brand_profiles() -> list[str]:
    """List all brand_ids available (built-in + user overrides), deduped."""
    ids: set[str] = set()
    for directory in (_builtin_dir(), _user_override_dir()):
        if directory.exists():
            for entry in directory.glob("*.json"):
                ids.add(entry.stem)
    return sorted(ids)


def match_brand_profile_for_photo(exif: dict[str, Any]) -> Optional[BrandProfile]:
    """Find the brand profile whose exiftool_make_match matches the photo's Make.

    Returns the first matching profile, or None if nothing matches. The search
    order is the sorted order of available profile ids, which is deterministic
    but arbitrary — profiles should use distinctive Make substrings to avoid
    accidental collisions.
    """
    exif_make = str(exif.get("Make", "")).strip()
    if not exif_make:
        return None
    for brand_id in list_available_brand_profiles():
        try:
            profile = load_brand_profile(brand_id)
        except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
            continue
        if profile.matches_make(exif_make):
            return profile
    return None
