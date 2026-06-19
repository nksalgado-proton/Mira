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
