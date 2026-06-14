"""Filename → capture timestamp recovery (task #120, Nelson 2026-05-23).

Files that lack readable EXIF land in ``00 - Captured/_no_timestamp/``
during ingest because ``mtime`` is unreliable (it usually carries the
copy date, not the capture date). But often the *filename itself*
carries the capture timestamp — common Android conventions
(``IMG_20250503_173143.jpg``), WhatsApp / messenger renames
(``IMG-20250503-WA0001.jpg``), Google Drive exports
(``2025-05-03 17.31.43.jpg``), and double-stamped files where some
tool prefixed an mtime in front of the original
(``2025-05-03_15-38-15__2025-04-10_20.32.46.jpg``).

This module is the **parser** half: pure regex over the filename,
returns an :class:`Optional[datetime]`. The recovery orchestrator
(see :mod:`core.quarantine_recovery`) consumes that to write EXIF +
re-route the file out of quarantine.

Defensive against false positives:

* Only YEAR ranges 1995-2099 accepted as a date-component candidate
  (a sub-string like ``200000`` accidentally matching is rejected).
* Month / day / hour / minute / second checked for valid ranges.
* Last timestamp wins when multiple are present — the convention in
  double-stamped filenames is "<prefix>__<original>", and the
  ORIGINAL is what we want in EXIF.

Qt-free. No filesystem access. Pure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


# Year range we accept. Pre-1995 photos exist but the false-positive
# risk from arbitrary 4-digit substrings is too high — and the
# user's photo collection is virtually certainly post-1995. Tightening
# the window is the only reason this parser is reliable on names like
# ``DSCN0001.JPG`` (which contains the substring "0001" that could
# look date-like under a looser pattern).
_MIN_YEAR = 1995
_MAX_YEAR = 2099


# Regex patterns. Each pattern captures (year, month, day,
# [hour], [minute], [second]) as named groups. Ordered by specificity:
# patterns with time fire FIRST so date-only ones don't snag a
# substring of a date+time match.

# Format A — separated date + time:
#   2025-05-03_17-31-43   2025-05-03 17.31.43   2025-05-03T17:31:43
#   2025/05/03 17:31:43   2025_05_03_17_31_43
# Separator between date and time: " ", "_", "-", "T" (ISO 8601).
# Separator within time: "-", ".", ":", "_".
_RE_FULL_SEPARATED = re.compile(
    r"(?P<y>\d{4})[-_/](?P<m>\d{2})[-_/](?P<d>\d{2})"
    r"[ T_-]"
    r"(?P<H>\d{2})[-:._](?P<M>\d{2})[-:._](?P<S>\d{2})"
)

# Format B — compact date + time:
#   IMG_20250503_173143.jpg   20250503T173143   20250503_173143
# Separator between date and time: "_", "T", "-", " ".
_RE_FULL_COMPACT = re.compile(
    r"(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})"
    r"[-_ T]"
    r"(?P<H>\d{2})(?P<M>\d{2})(?P<S>\d{2})"
)

# Format C — date only, separated. Time defaults to 12:00:00.
#   2025-05-03   2025_05_03   2025/05/03
_RE_DATE_ONLY_SEPARATED = re.compile(
    r"(?P<y>\d{4})[-_/](?P<m>\d{2})[-_/](?P<d>\d{2})"
)

# Format D — date only, compact (e.g. WhatsApp IMG-20250503-WA0001).
# Time defaults to 12:00:00. ``\b`` anchors avoid grabbing 8 digits
# out of the middle of an unrelated string.
_RE_DATE_ONLY_COMPACT = re.compile(
    r"(?:^|[^\d])"          # boundary that isn't a digit
    r"(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})"
    r"(?=[^\d]|$)"          # boundary that isn't a digit
)


@dataclass(frozen=True)
class ParsedTimestamp:
    """Result of parsing one filename. ``time_is_default`` flags
    timestamps that came from a date-only pattern (time set to noon)
    so callers can surface this in the UI — the user may want to
    keep the date and move on, but a "we made up the time"
    disclaimer is honest."""
    dt: datetime
    pattern: str
    time_is_default: bool = False


def parse_timestamp_from_filename(
    name: str,
) -> Optional[ParsedTimestamp]:
    """Parse ``name`` (a filename or stem; extension is ignored) and
    return the recovered timestamp, or ``None`` when no plausible
    pattern matches.

    When multiple timestamps appear in the same name (the common
    double-stamped convention ``<mtime>__<original>``), the LAST
    matching timestamp wins — which is the original capture in the
    double-stamped layout.

    Date-only patterns set ``time_is_default=True`` with the time
    defaulted to 12:00:00 (noon — neutral for day-bucket routing,
    far from midnight on either side).
    """
    if not name:
        return None
    # Strip extension(s) so we don't accidentally let ``.jpg``
    # collide with a time separator regex.
    stem = name.rsplit(".", 1)[0] if "." in name else name

    # Collect matches from EVERY full-time pattern + pick the one
    # at the latest stem position. Per the double-stamped convention
    # ``<mtime>__<original>``, the original always lands later in
    # the name, regardless of whether prefix and suffix use the
    # same format.
    full_candidates: list[tuple[int, datetime, str]] = []
    saw_full_pattern = False           # any FULL-shape match — valid or not
    for pattern_re, label in (
        (_RE_FULL_SEPARATED, "full_separated"),
        (_RE_FULL_COMPACT, "full_compact"),
    ):
        for match in pattern_re.finditer(stem):
            saw_full_pattern = True
            dt = _build_datetime(
                match.group("y"), match.group("m"), match.group("d"),
                match.group("H"), match.group("M"), match.group("S"),
            )
            if dt is not None:
                full_candidates.append((match.start(), dt, label))
    if full_candidates:
        # Latest position wins (the original capture in
        # double-stamped names).
        full_candidates.sort(key=lambda x: x[0])
        _pos, dt, label = full_candidates[-1]
        return ParsedTimestamp(
            dt=dt, pattern=label, time_is_default=False,
        )
    if saw_full_pattern:
        # A full-shape pattern matched but every component was bad
        # (e.g. hour=25). Don't fall back to date-only — the user's
        # intent was clearly a full timestamp; treat the whole name
        # as suspect rather than silently downgrading.
        return None

    # Date-only fallbacks. Same latest-position-wins rule.
    date_candidates: list[tuple[int, datetime, str]] = []
    for pattern_re, label in (
        (_RE_DATE_ONLY_SEPARATED, "date_only_separated"),
        (_RE_DATE_ONLY_COMPACT, "date_only_compact"),
    ):
        for match in pattern_re.finditer(stem):
            dt = _build_datetime(
                match.group("y"), match.group("m"), match.group("d"),
                "12", "00", "00",
            )
            if dt is not None:
                date_candidates.append((match.start(), dt, label))
    if date_candidates:
        date_candidates.sort(key=lambda x: x[0])
        _pos, dt, label = date_candidates[-1]
        return ParsedTimestamp(
            dt=dt, pattern=label, time_is_default=True,
        )

    return None


def _build_datetime(
    y: str, m: str, d: str,
    H: str, M: str, S: str,
) -> Optional[datetime]:
    """Validate ranges + build the datetime. ``None`` on any
    out-of-range component."""
    try:
        year = int(y)
        month = int(m)
        day = int(d)
        hour = int(H)
        minute = int(M)
        second = int(S)
    except (TypeError, ValueError):
        return None
    if not (_MIN_YEAR <= year <= _MAX_YEAR):
        return None
    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= 31):
        return None
    if not (0 <= hour <= 23):
        return None
    if not (0 <= minute <= 59):
        return None
    if not (0 <= second <= 59):
        return None
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        # Catches e.g. Feb 30, day=31 in a 30-day month, etc.
        return None


__all__ = [
    "ParsedTimestamp",
    "parse_timestamp_from_filename",
]
