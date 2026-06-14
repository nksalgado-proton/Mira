"""Plan dialog Save / Load CSV codec — spec/52 §5.5.

Pure-Python encode + decode of the plan-dialog "Save plan to file…" /
"Load plan from file…" format. Two buttons in the Plan dialog footer
(gated by the ``feature.plan_save_load_csv`` feature flag) emit this format;
nothing else uses it.

Format
------

* **`;`-separated** (semicolon, not comma). Why semicolons: the CSV is meant
  to round-trip through Excel under any locale, and Excel ties its comma /
  semicolon parsing to the regional decimal separator. Semicolons survive a
  user opening the file in pt-BR / de-DE Excel without column-explosion.
* **UTF-8 with BOM** on write — Excel still needs the BOM to recognise
  non-ASCII text on Windows; on read we tolerate either presence/absence.
* **Header row required** with exactly these column names in this order:
  ``date;country;tz;location;description``.
* **Quoting**: standard CSV double-quote rules. A cell containing ``;``,
  ``"``, or a newline is wrapped in double-quotes; internal double-quotes
  are doubled. The stdlib :mod:`csv` module handles this for us.

Column semantics
----------------

* **date** — ISO 8601 date (``YYYY-MM-DD``). The round-trip key. Required.
* **country** — ISO 3166-1 alpha-2 code (``"CR"``, ``"PT"``). Empty allowed.
* **tz** — Offset in ``±HH:MM`` form (``"+02:00"``, ``"-03:30"``,
  ``"+00:00"`` for UTC). Empty allowed. The dialog's TZ picker writes
  minutes-east-of-UTC; the codec converts between minutes ↔ string.
* **location** — Free-text human-readable location. Empty allowed.
* **description** — Free-text day description. Empty allowed.

Round-trip behaviour
--------------------

* Encode: takes a list of :class:`PlanCsvRow` (in any order); writes the
  header + one row per entry, sorted by date.
* Decode: returns a list of :class:`PlanCsvRow`. Malformed rows (missing
  date, unparseable date, wrong column count) raise :class:`PlanCsvError`
  with the line number — the dialog surfaces this to the user.
* Importing into the dialog (the caller) uses the **non-destructive
  partial-overlap** rule from spec/52 §5.5: each loaded row matches the
  scan day with the same ``date`` and overwrites that day's four
  non-date fields. Loaded rows whose date isn't in the scan are skipped
  with a count returned to the UI. Scan days with no matching loaded row
  are left as-is.

Pure logic, no Qt.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, List, Sequence, TextIO, Tuple


# --------------------------------------------------------------------------- #
# Row + error types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlanCsvRow:
    """One row of the plan CSV. Empty optional fields are the empty string
    on read; the codec normalises ``None`` and ``""`` to ``""`` on write so
    Excel doesn't see ``"None"`` in a cell."""

    date: date
    country: str = ""
    tz_minutes: int | None = None
    location: str = ""
    description: str = ""


class PlanCsvError(ValueError):
    """Raised on a malformed CSV file (missing/extra column, bad date,
    bad TZ). Carries the 1-based line number where the problem was
    detected. Caught + surfaced by the Plan dialog as a user-readable
    "couldn't load that file" message — never crashes the app."""

    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line

    def __str__(self) -> str:  # pragma: no cover — trivial
        base = super().__str__()
        return f"line {self.line}: {base}" if self.line is not None else base


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: The column header row, in canonical order. Decode requires an exact
#: match (case-sensitive); encode always emits this verbatim.
HEADER: Tuple[str, ...] = ("date", "country", "tz", "location", "description")

#: Field separator — spec/52 §5.5.
DELIMITER = ";"

#: UTF-8 BOM bytes — Excel on Windows needs this to recognise non-ASCII
#: text in CSV imports.
_UTF8_BOM = "﻿"


# --------------------------------------------------------------------------- #
# TZ minutes ↔ ±HH:MM string
# --------------------------------------------------------------------------- #


def tz_minutes_to_string(minutes: int | None) -> str:
    """Encode a minutes-east-of-UTC integer as ``±HH:MM``. ``None`` → ``""``.

    Range check is loose (anything in ``±14:00`` is accepted, matching IANA
    ranges); out-of-band values still encode but won't round-trip cleanly
    through the decoder's stricter validation."""
    if minutes is None:
        return ""
    sign = "+" if minutes >= 0 else "-"
    mag = abs(int(minutes))
    return f"{sign}{mag // 60:02d}:{mag % 60:02d}"


def tz_string_to_minutes(text: str) -> int | None:
    """Decode ``±HH:MM`` / ``±HHMM`` / ``Z`` to minutes-east-of-UTC.

    Empty / whitespace-only → ``None``. Invalid forms raise
    :class:`ValueError` (the codec wraps this into a :class:`PlanCsvError`
    with the source line number).
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    if stripped.upper() == "Z":
        return 0
    sign = 1
    body = stripped
    if body[0] in "+-":
        sign = -1 if body[0] == "-" else 1
        body = body[1:]
    if ":" in body:
        h_str, m_str = body.split(":", 1)
    else:
        # Allow `+0200` legacy/short form too.
        if len(body) in (3, 4) and body.isdigit():
            h_str, m_str = body[:-2], body[-2:]
        else:
            raise ValueError(f"unparseable TZ {text!r}")
    if not (h_str.isdigit() and m_str.isdigit()):
        raise ValueError(f"unparseable TZ {text!r}")
    hours = int(h_str)
    minutes = int(m_str)
    if not (0 <= hours <= 14 and 0 <= minutes < 60):
        raise ValueError(f"TZ out of range {text!r}")
    return sign * (hours * 60 + minutes)


# --------------------------------------------------------------------------- #
# Encode
# --------------------------------------------------------------------------- #


def encode(rows: Iterable[PlanCsvRow]) -> str:
    """Encode rows to the full CSV text (with BOM + header). Rows are sorted
    by date so the file order is stable regardless of input order."""
    sorted_rows = sorted(rows, key=lambda r: r.date.isoformat())
    buf = io.StringIO()
    buf.write(_UTF8_BOM)
    writer = csv.writer(
        buf, delimiter=DELIMITER,
        quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n",
    )
    writer.writerow(HEADER)
    for row in sorted_rows:
        writer.writerow([
            row.date.isoformat(),
            row.country or "",
            tz_minutes_to_string(row.tz_minutes),
            row.location or "",
            row.description or "",
        ])
    return buf.getvalue()


def save_to_path(rows: Iterable[PlanCsvRow], path: Path) -> None:
    """Write the encoded rows to ``path`` (UTF-8 with BOM, CRLF line
    endings). Overwrites any existing file at the path — the Plan dialog
    surfaces an overwrite confirmation upstream."""
    Path(path).write_text(encode(rows), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Decode
# --------------------------------------------------------------------------- #


def _strip_bom(text: str) -> str:
    return text[1:] if text.startswith(_UTF8_BOM) else text


def decode(text: str) -> List[PlanCsvRow]:
    """Decode the CSV text to a list of :class:`PlanCsvRow`.

    Header row must match :data:`HEADER` exactly. Tolerant of:
    BOM presence/absence, blank trailing lines, ``\\n`` or ``\\r\\n`` line
    endings, surrounding whitespace in cells.

    Raises :class:`PlanCsvError` on:
    * empty file / missing header
    * wrong column count in a data row
    * unparseable date
    * unparseable TZ
    """
    text = _strip_bom(text)
    reader = csv.reader(io.StringIO(text), delimiter=DELIMITER)
    rows = list(reader)
    if not rows:
        raise PlanCsvError("file is empty")

    # Header validation.
    header = tuple(cell.strip() for cell in rows[0])
    if header != HEADER:
        raise PlanCsvError(
            f"header mismatch (expected {list(HEADER)}, got {list(header)})",
            line=1,
        )

    out: List[PlanCsvRow] = []
    for idx, raw in enumerate(rows[1:], start=2):           # 1-based + skip header
        # Skip fully blank lines (a trailing newline produces one).
        if all((cell or "").strip() == "" for cell in raw):
            continue
        if len(raw) != len(HEADER):
            raise PlanCsvError(
                f"wrong column count ({len(raw)}; expected {len(HEADER)})",
                line=idx,
            )
        date_str = raw[0].strip()
        country = raw[1].strip()
        tz_str = raw[2].strip()
        location = raw[3].strip()
        description = raw[4].strip()
        if not date_str:
            raise PlanCsvError("date is required", line=idx)
        try:
            day = date.fromisoformat(date_str)
        except ValueError as exc:
            raise PlanCsvError(
                f"unparseable date {date_str!r} ({exc})", line=idx
            ) from None
        try:
            tz_minutes = tz_string_to_minutes(tz_str)
        except ValueError as exc:
            raise PlanCsvError(str(exc), line=idx) from None
        out.append(PlanCsvRow(
            date=day, country=country, tz_minutes=tz_minutes,
            location=location, description=description,
        ))
    return out


def load_from_path(path: Path) -> List[PlanCsvRow]:
    """Read + decode ``path``. Raises :class:`PlanCsvError` on parse failure;
    re-raises :class:`OSError` on read failure (the dialog surfaces both as
    "couldn't load that file")."""
    return decode(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Apply — the non-destructive partial-overlap merge (spec/52 §5.5)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ApplyOutcome:
    """Result of :func:`apply_to_scan_days`. ``applied_dates`` lists the
    dates whose scan-day rows were updated; ``unmatched_dates`` lists the
    CSV dates that had no matching scan day (these are reported back to the
    user in a notice per spec/52 §5.5)."""

    applied_dates: Tuple[date, ...]
    unmatched_dates: Tuple[date, ...]


def apply_to_scan_days(
    loaded: Sequence[PlanCsvRow],
    scan_dates: Iterable[date],
) -> ApplyOutcome:
    """Compute the apply outcome — which loaded rows hit scan days, which
    don't. **Does not mutate** anything; the dialog uses the returned dates
    to drive its row updates + the "N unmatched" notice.

    Per spec/52 §5.5:
    * CSV rows whose date isn't in the scan are **ignored with a notice**.
    * Scan days with no matching CSV row are **left as-is** (partial loads
      are non-destructive).
    """
    scan_set = set(scan_dates)
    applied: list[date] = []
    unmatched: list[date] = []
    for row in loaded:
        if row.date in scan_set:
            applied.append(row.date)
        else:
            unmatched.append(row.date)
    return ApplyOutcome(
        applied_dates=tuple(applied),
        unmatched_dates=tuple(unmatched),
    )
