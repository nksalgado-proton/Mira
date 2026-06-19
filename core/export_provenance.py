"""Origin labels for Export-surface cells (spec/89 §1.4 + §2.1).

The lineage row's ``provenance`` enum is binary — ``'mira_render'`` vs
``'third_party'`` — per spec/72 §1. The displayed badge wordmark (Mira /
LRC / Helicon / CO / ext) is filename-inferred at the badge layer, not
stored on the row. This module owns the inference + the canonical label
set so the badge widget + tests share one source of truth.

When an item carries MULTIPLE shipped rows (the spec/89 Block 1
versions case), the per-row labels are surfaced inside the cluster
sub-grid. At the flat-cell layer the helper returns one summary label
per item; see :func:`cell_origin_label` for the rules.
"""
from __future__ import annotations

from pathlib import PurePosixPath

#: The label catalog. The keys are the canonical short forms used in
#: the badge wordmark; future visual polish may replace the text with
#: app-specific icons (deferred — spec/89 §9).
MIRA = "Mira"
LRC = "LRC"
HELICON = "Helicon"
CAPTURE_ONE = "CO"
EXTERNAL = "ext"

#: Filename hints, lower-cased substrings to look for. Order matters
#: only when two hints could match the same filename — first wins.
_THIRD_PARTY_HINTS: tuple[tuple[str, str], ...] = (
    ("lightroom", LRC),
    ("lrclassic", LRC),
    ("lrc", LRC),
    ("helicon", HELICON),
    ("captureone", CAPTURE_ONE),
    ("capture-one", CAPTURE_ONE),
    ("capture_one", CAPTURE_ONE),
    ("captureone", CAPTURE_ONE),
)


def parse_third_party_label(filename: str) -> str:
    """Return the short label (``LRC`` / ``Helicon`` / ``CO`` / ``ext``)
    for a third-party return based on its filename. Defaults to the
    generic ``ext`` fallback so an unknown editor still shows a badge
    (no silent gap in the strip)."""
    if not filename:
        return EXTERNAL
    stem = PurePosixPath(filename).stem.lower().replace(" ", "")
    for hint, label in _THIRD_PARTY_HINTS:
        if hint in stem:
            return label
    return EXTERNAL


def lineage_origin_label(provenance: str | None, export_relpath: str) -> str:
    """Resolve one lineage row to its displayed origin label.

    ``provenance`` is the binary signal from ``lineage.provenance``;
    ``export_relpath`` is the file's location, used only when the row
    is third-party (the filename carries the editor hint). Unknown /
    ``None`` provenance defaults to ``Mira`` for backwards compatibility
    with pre-Model-B rows whose column the migration backfilled."""
    if provenance == "third_party":
        filename = PurePosixPath(export_relpath or "").name
        return parse_third_party_label(filename)
    return MIRA


def cell_origin_label(rows: list) -> str | None:
    """Summary label for a flat cell that may have one or more shipped
    lineage rows.

    The flat cell is the spec/89 Block 1 single-version case (one row;
    return the row's label) or the zero-version case (no rows; no
    badge). Multi-version items become a cluster (Block 1 D1.C / Slice
    5) — the cluster cover gets a count chip, not a wordmark; the
    individual wordmarks appear inside the sub-grid. So this helper
    returns ``None`` for both "no rows" and "ambiguous multi-row" cases
    — let the caller decide which legend to draw."""
    if not rows:
        return None
    if len(rows) > 1:
        return None
    row = rows[0]
    return lineage_origin_label(
        getattr(row, "provenance", None),
        getattr(row, "export_relpath", "") or "",
    )


def scan_chip_text(report) -> str:
    """spec/89 §2.2 (D5a.C + D5c.B) — the Export-surface scan chip's
    wording. The chip is a quiet status read; on no-change it says
    "up to date"; on change it lists the **per-source breakdown**
    so the user can see at a glance which external editor produced
    the new returns.

    ``report`` is a :class:`mira.picked.external_returns.ReturnsReport`
    or any object with an ``associated: list[str]`` of newly-linked
    relpaths. Passing ``None`` is safe — the chip reads as up-to-date.
    """
    associated = list(getattr(report, "associated", None) or [])
    if not associated:
        return "External edits: up to date"
    counts: dict[str, int] = {}
    for relpath in associated:
        label = lineage_origin_label("third_party", relpath)
        counts[label] = counts.get(label, 0) + 1
    n = len(associated)
    word = "new external edit" if n == 1 else "new external edits"
    # Order: Mira (shouldn't appear here but keep it stable), then by
    # count descending, then alphabetical for determinism.
    parts = sorted(
        counts.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    breakdown = " · ".join(f"{cnt} {label}" for label, cnt in parts)
    return f"{n} {word} · {breakdown}"


__all__ = [
    "MIRA", "LRC", "HELICON", "CAPTURE_ONE", "EXTERNAL",
    "parse_third_party_label", "lineage_origin_label",
    "cell_origin_label", "scan_chip_text",
]
