"""spec/66 — the Phases 2×2 'Pick' donut plots picked / captured (the
**keep rate**), not decided / captured. The change (Nelson 2026-06-22)
aligns the donut with the Days-List "Picked" bar; review completeness
(decided / captured) is still queryable via ``phase_decided_count`` but
no longer surfaces on this tile.

These tests pin the new contract at three seams:
  1. ``_ratio_snapshot('pick', …)`` packs picked / captured into the
     PhaseSnapshot.
  2. The phase tooltip dictionary carries the keep-rate wording.
  3. ``_format_delta`` for a Pick snapshot is empty — the captured −
     picked count is the *not-kept* set, not a completion gap, so the
     legacy "N to review" line would mislead.
"""
from __future__ import annotations

from mira.ui.pages.phases_page import (
    _PHASE_CAPTIONS,
    PhaseCard,
    PhaseSnapshot,
    PhasesPage,
)


# Minimal palette with only the keys ``_ratio_snapshot`` reads.
_PALETTE = {
    "track": "#222",
    "accent": "#7c6cff",   # Pick fill
    "amber": "#f59e0b",
    "green": "#34d399",
    "blue": "#3b82f6",
}


def test_pick_ratio_snapshot_uses_picked_over_captured():
    """Numerator = picked keepers, denominator = captured. Center pct
    + the ``N / Total`` sub both read picked / captured (spec/66 §1.1
    keep rate)."""
    snap = PhasesPage._ratio_snapshot(
        "pick", "Pick", numerator=6, denominator=10, palette=_PALETTE,
    )
    assert snap.key == "pick"
    assert snap.numerator == 6
    assert snap.denominator == 10
    assert snap.center_text == "60%"
    assert snap.center_sub == "6 / 10"
    # Two slices: filled (picked) + remainder (captured − picked).
    assert len(snap.slices) == 2
    assert snap.slices[0].value == 6   # picked
    assert snap.slices[1].value == 4   # remainder


def test_pick_ratio_snapshot_with_no_keepers_yet_is_idle():
    """Pre-keep-rate move this read 'Not started' too — pin the shape
    survives so an event with captures but zero picks still reads idle."""
    snap = PhasesPage._ratio_snapshot(
        "pick", "Pick", numerator=0, denominator=10, palette=_PALETTE,
    )
    assert snap.status == "idle"
    assert snap.state_word == "Not started"
    assert snap.numerator == 0
    assert snap.denominator == 10


def test_pick_tooltip_reads_keep_rate_wording():
    """The hover caption on the Pick tile (spec/66) — was 'Share of
    captures reviewed (picked or skipped).'; now 'Share of captures
    kept (picked).' (keep rate)."""
    assert _PHASE_CAPTIONS["pick"] == "Share of captures kept (picked)."
    # The sibling captions are untouched.
    assert "reviewed" not in _PHASE_CAPTIONS["pick"]
    assert "picks" in _PHASE_CAPTIONS["edit"]
    assert "picks" in _PHASE_CAPTIONS["export"]


def test_pick_format_delta_returns_empty():
    """spec/66 keep-rate move: captured − picked is the *not-kept* set,
    not a completion gap. The 'N to review' / 'All reviewed' line no
    longer fits and is suppressed (the donut alone carries the metric,
    same shape Collect already uses)."""
    snap_progress = PhaseSnapshot(
        key="pick", label="Pick", status="prog", slices=[],
        numerator=3, denominator=10,
    )
    assert PhaseCard._format_delta(snap_progress) == ""
    snap_done = PhaseSnapshot(
        key="pick", label="Pick", status="done", slices=[],
        numerator=10, denominator=10,
    )
    assert PhaseCard._format_delta(snap_done) == ""
    snap_empty = PhaseSnapshot(
        key="pick", label="Pick", status="idle", slices=[],
        numerator=0, denominator=0,
    )
    assert PhaseCard._format_delta(snap_empty) == ""


def test_edit_and_export_format_delta_still_have_completion_phrasing():
    """The Pick suppression is targeted — Edit and Export still print
    the legacy 'N to edit' / 'All exported' completion lines (those
    phases ARE completion metrics: developed / picked, exported /
    picked)."""
    edit_prog = PhaseSnapshot(
        key="edit", label="Edit", status="prog", slices=[],
        numerator=2, denominator=10,
    )
    assert PhaseCard._format_delta(edit_prog) == "8 to edit"
    edit_done = PhaseSnapshot(
        key="edit", label="Edit", status="done", slices=[],
        numerator=10, denominator=10,
    )
    assert PhaseCard._format_delta(edit_done) == "All edited"
    export_prog = PhaseSnapshot(
        key="export", label="Export", status="prog", slices=[],
        numerator=4, denominator=10,
    )
    assert PhaseCard._format_delta(export_prog) == "6 to export"
    export_done = PhaseSnapshot(
        key="export", label="Export", status="done", slices=[],
        numerator=10, denominator=10,
    )
    assert PhaseCard._format_delta(export_done) == "All exported"
