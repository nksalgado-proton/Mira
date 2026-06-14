"""Phone detection from EXIF Make/Model — spec/52 §9.

The single source of truth for "is this photo from a phone?". Drives:

* **Autofill** (spec/52 §3.1) — when a day has phone photos, the system pulls
  country / TZ / location from the phone EXIF.
* **TZ-calibration trigger** (spec/52 §8.2) — calibration is offered only when
  camera (non-phone) photos are present on a day.
* **Ingest bucketing** (`mira/ingest/`) — phone photos land in the
  `_phones` subdir of `00 - Captured`; cameras in `_cameras`.

The maintained list lives as data at ``assets/phone_makers.json`` — explicit
Make / model_patterns pairs, case-insensitive matching. Adding a new phone
maker means appending an entry to that file, no code change.

**Why Make/Model and not "has GPS":** modern cameras (Sony A7R V, some Nikon
Z bodies) include GPS too; GPS-presence alone would mis-classify them. EXIF
Make/Model is the cleanest, most direct signal (spec/52 §9).

**Compatibility note:** :mod:`core.source_index` and :mod:`mira.ingest.plan`
carry the legacy :func:`looks_like_phone(camera_id)` helpers that pre-date this
module — they work off the camera-id string (an EXIF Model) and use a small
hardcoded substring list. They still work for the legacy code paths; new
event-creation surfaces consume the data-driven API in this module instead.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data model for the loaded list
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PhoneMaker:
    """One entry in the maintained list. ``make`` matches case-insensitively
    against EXIF ``Make``. ``model_patterns`` are substrings checked
    case-insensitively against EXIF ``Model``; the wildcard ``"*"`` matches
    any model. Match fires when both Make AND at least one Model pattern hit."""

    make: str
    model_patterns: tuple[str, ...] = field(default_factory=tuple)

    def matches(self, make: Optional[str], model: Optional[str]) -> bool:
        if not make or make.casefold() != self.make.casefold():
            return False
        if "*" in self.model_patterns:
            return True
        # No model pattern → can't fire (Make alone is never enough for a list
        # entry that scoped itself to specific models).
        if not self.model_patterns:
            return False
        if not model:
            return False
        haystack = model.casefold()
        return any(p.casefold() in haystack for p in self.model_patterns)


# --------------------------------------------------------------------------- #
# Loading the maintained list
# --------------------------------------------------------------------------- #


def _default_makers_path() -> Path:
    """The bundled list under ``assets/phone_makers.json``. Resolved off the
    repo root via this module's own location so a Nuitka-packed build picks up
    the bundled assets folder identically to a source run."""
    return Path(__file__).resolve().parent.parent / "assets" / "phone_makers.json"


@lru_cache(maxsize=1)
def _load_default_makers() -> tuple[PhoneMaker, ...]:
    """Read + cache the bundled list. Caches a single in-process copy; tests
    that want a custom list pass it through :func:`is_phone` /
    :func:`is_phone_camera_id` explicitly instead of mutating this cache."""
    return tuple(load_phone_makers_from(_default_makers_path()))


def load_phone_makers_from(path: Path) -> List[PhoneMaker]:
    """Parse one ``phone_makers.json`` file into a list of :class:`PhoneMaker`.
    Tolerant on missing keys (unknown fields ignored) but raises on malformed
    JSON — the bundled file is part of the build and must parse cleanly."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out: List[PhoneMaker] = []
    for entry in raw.get("phone_makers", []):
        if not isinstance(entry, dict):
            continue
        make = entry.get("make")
        if not isinstance(make, str) or not make.strip():
            continue
        patterns = entry.get("model_patterns") or []
        if not isinstance(patterns, list):
            continue
        out.append(PhoneMaker(
            make=make.strip(),
            model_patterns=tuple(str(p) for p in patterns if isinstance(p, str)),
        ))
    return out


def reload_default_makers() -> None:
    """Drop the cached list so the next :func:`is_phone` call re-reads the
    bundled file. Test seam — production code never needs this."""
    _load_default_makers.cache_clear()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def is_phone(
    make: Optional[str],
    model: Optional[str],
    *,
    makers: Optional[List[PhoneMaker]] = None,
) -> bool:
    """The canonical EXIF-Make/Model phone check (spec/52 §9).

    Pass ``makers=`` to inject a custom list (tests). Default uses the bundled
    ``assets/phone_makers.json``."""
    rules = makers if makers is not None else _load_default_makers()
    return any(rule.matches(make, model) for rule in rules)


def is_phone_camera_id(
    camera_id: Optional[str],
    *,
    makers: Optional[List[PhoneMaker]] = None,
) -> bool:
    """Compatibility shim for callers that only carry the merged
    ``"Make Model"`` camera_id string (the existing ingest plan / source_index
    convention).

    Matches a phone-maker entry when:

    * the camera_id starts (case-insensitive) with the entry's ``make``, OR
    * the camera_id contains any of the entry's model_patterns.

    Note this is intentionally looser than :func:`is_phone` — without the
    structured Make/Model pair we can't enforce the "Make AND Model" rule
    that disambiguates Sony Xperia from a Sony camera. Production callers
    that have separated Make/Model fields should call :func:`is_phone`
    instead."""
    if not camera_id:
        return False
    rules = makers if makers is not None else _load_default_makers()
    needle = camera_id.casefold()
    for rule in rules:
        if needle.startswith(rule.make.casefold() + " ") or needle == rule.make.casefold():
            # Pure make match against the camera_id prefix — only fires for
            # phone-only makers (model_patterns = '*').
            if "*" in rule.model_patterns:
                return True
            # Make matches but the rule scopes to specific models; require a
            # pattern hit too.
            if any(p.casefold() in needle for p in rule.model_patterns if p != "*"):
                return True
            continue
        # Make doesn't prefix — try direct pattern match (legacy compatibility
        # path; covers cases where camera_id is the bare Model string).
        if any(p.casefold() in needle for p in rule.model_patterns if p != "*"):
            return True
    return False
