"""Tests for spec/89 §1.4 / §2.1 origin labels + scan chip wording."""
from __future__ import annotations

from dataclasses import dataclass

from core.export_provenance import (
    CAPTURE_ONE,
    EXTERNAL,
    HELICON,
    LRC,
    MIRA,
    cell_origin_label,
    lineage_origin_label,
    parse_third_party_label,
    scan_chip_text,
    stack_output_origin_label,
)


@dataclass
class _Row:
    """Minimal stand-in for :class:`mira.store.models.Lineage` with the
    fields the helpers actually read."""

    export_relpath: str
    provenance: str = "mira_render"


# ── parse_third_party_label ──────────────────────────────────────────────


def test_parse_third_party_label_handles_known_editors():
    assert parse_third_party_label("D03_G9_p1-Lightroom-edit.jpg") == LRC
    assert parse_third_party_label("D03_G9_p1-LRC.jpg") == LRC
    assert parse_third_party_label("focus-stack-Helicon.dng") == HELICON
    assert parse_third_party_label("D03_G9_p1-CaptureOne.jpg") == CAPTURE_ONE
    assert parse_third_party_label("D03_G9_p1-Capture-One.tif") == CAPTURE_ONE


def test_parse_third_party_label_defaults_to_external():
    assert parse_third_party_label("D03_G9_p1-edit.jpg") == EXTERNAL
    assert parse_third_party_label("") == EXTERNAL


# ── lineage_origin_label ─────────────────────────────────────────────────


def test_lineage_origin_label_mira_render_reads_as_mira():
    assert lineage_origin_label(
        "mira_render", "Exported Media/Dia 1/p1.jpg") == MIRA


def test_lineage_origin_label_third_party_picks_editor():
    assert lineage_origin_label(
        "third_party", "Exported Media/D03_G9_p1-Lightroom.jpg") == LRC
    assert lineage_origin_label(
        "third_party", "Exported Media/D03_G9_p1-Helicon.tif") == HELICON


def test_lineage_origin_label_falls_back_to_mira_for_unknown_provenance():
    """Pre-Model-B rows whose ``provenance`` column was backfilled to
    ``mira_render`` still read as Mira even if the caller passes
    ``None``."""
    assert lineage_origin_label(
        None, "Exported Media/Dia 1/p1.jpg") == MIRA


# ── cell_origin_label ────────────────────────────────────────────────────


def test_cell_origin_label_single_row():
    rows = [_Row("Exported Media/p1.jpg", "mira_render")]
    assert cell_origin_label(rows) == MIRA
    rows = [_Row("Exported Media/p1-LRC.jpg", "third_party")]
    assert cell_origin_label(rows) == LRC


def test_cell_origin_label_no_rows_returns_none():
    assert cell_origin_label([]) is None


def test_cell_origin_label_multi_row_returns_none():
    """spec/89 Block 1 D1.C — 2+ versions become a cluster; the cluster
    cover gets a count chip, not a wordmark."""
    rows = [
        _Row("Exported Media/p1.jpg", "mira_render"),
        _Row("Exported Media/p1-LRC.jpg", "third_party"),
    ]
    assert cell_origin_label(rows) is None


# ── scan_chip_text ───────────────────────────────────────────────────────


@dataclass
class _Report:
    associated: list


def test_scan_chip_text_says_up_to_date_when_nothing_changed():
    assert scan_chip_text(_Report([])) == "External edits: up to date"
    assert scan_chip_text(None) == "External edits: up to date"


def test_scan_chip_text_per_source_breakdown_on_change():
    """spec/89 §2.2 D5c.B — the chip splits the count by editor so the
    user sees at a glance which app's exports landed."""
    rep = _Report([
        "Exported Media/p1-LRC.jpg",
        "Exported Media/p2-Lightroom.jpg",
        "Exported Media/p3-Helicon.tif",
    ])
    text = scan_chip_text(rep)
    assert text.startswith("3 new external edits")
    assert "2 LRC" in text
    assert "1 Helicon" in text


def test_scan_chip_text_singular_form_for_one_change():
    rep = _Report(["Exported Media/p1-Helicon.dng"])
    assert scan_chip_text(rep) == "1 new external edit · 1 Helicon"


@dataclass
class _ReportFull:
    """Report stand-in with both ``associated`` and ``unmatched`` so the
    chip can report mismatches the user needs to fix."""

    associated: list
    unmatched: list


def test_scan_chip_text_surfaces_unmatched_count_when_nothing_linked():
    """spec/89 §2.2 (Nelson eyeball 2026-06-19): the scanner found 31
    LRC exports but matched none — the chip used to read "up to date"
    because the old check only looked at ``associated``. Now it tells
    the user the count + the most common cause."""
    rep = _ReportFull(associated=[], unmatched=[f"Edited Media/LRC/f{i}.jpg" for i in range(31)])
    text = scan_chip_text(rep)
    assert "31 files in Edited Media/" in text
    assert "didn't match any source" in text


def test_scan_chip_text_partial_match_lists_both():
    rep = _ReportFull(
        associated=["Exported Media/p1-LRC.jpg"],
        unmatched=["Edited Media/LRC/orphan.jpg"],
    )
    text = scan_chip_text(rep)
    assert "1 new external edit" in text
    assert "1 LRC" in text
    assert "1 unmatched" in text


# ── stack_output_origin_label ────────────────────────────────────────────


def test_stack_output_origin_label_mira_producer_renders_mira():
    """spec/109 §5 — a Mira-fused stack output reads as ``Mira`` so the
    consolidation badge distinguishes the in-app Mertens lane from a
    third-party stacker return."""
    assert stack_output_origin_label("mira") == MIRA


def test_stack_output_origin_label_external_producer_renders_ext():
    """spec/108 §3 — every external origin flattens to ``ext`` (no
    per-tool wordmarks). The default producer value also reads ``ext``
    so legacy rows badge consistently."""
    assert stack_output_origin_label("external") == EXTERNAL
    assert stack_output_origin_label(None) == EXTERNAL
    assert stack_output_origin_label("") == EXTERNAL
