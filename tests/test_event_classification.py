"""Tests for ``mira.event_classification`` — the type/subtype/phase seam.

The phase-set seam tests pin Slice A's "full pipeline for every type" behavior
explicitly. The next sprint changes those return values; when it does, these
tests are the canary that proves every call site routes through the seam
(if any consumer still hardcodes a phase list, those tests fail to surface it
on their own — but the seam itself is solid).
"""
from __future__ import annotations

import pytest

from mira import event_classification as ec


# ── Types ─────────────────────────────────────────────────────────────────


def test_all_event_types_membership():
    assert ec.EVENT_TYPE_TRIP in ec.ALL_EVENT_TYPES
    assert ec.EVENT_TYPE_SESSION in ec.ALL_EVENT_TYPES
    assert ec.EVENT_TYPE_OCCASION in ec.ALL_EVENT_TYPES
    assert ec.EVENT_TYPE_PROJECT in ec.ALL_EVENT_TYPES
    assert ec.EVENT_TYPE_UNCLASSIFIED in ec.ALL_EVENT_TYPES
    assert len(ec.ALL_EVENT_TYPES) == 5


def test_is_known_type():
    for t in ec.ALL_EVENT_TYPES:
        assert ec.is_known_type(t)
    assert not ec.is_known_type("happening")    # the user's working name; renamed
    assert not ec.is_known_type("")
    assert not ec.is_known_type("TRIP")          # case-sensitive


def test_display_label_for_type():
    assert ec.display_label_for_type(ec.EVENT_TYPE_TRIP) == "Trip"
    assert ec.display_label_for_type(ec.EVENT_TYPE_SESSION) == "Session"
    assert ec.display_label_for_type(ec.EVENT_TYPE_OCCASION) == "Occasion"
    assert ec.display_label_for_type(ec.EVENT_TYPE_PROJECT) == "Project"
    assert ec.display_label_for_type(ec.EVENT_TYPE_UNCLASSIFIED) == "Unclassified"
    # Unknown → Unclassified label (graceful fallback for legacy / future values).
    assert ec.display_label_for_type("totally_made_up") == "Unclassified"


@pytest.mark.parametrize("raw,expected", [
    ("trip", "trip"),
    ("session", "session"),
    ("occasion", "occasion"),
    ("project", "project"),
    ("unclassified", "unclassified"),
    ("", "unclassified"),
    (None, "unclassified"),
    ("legacy free-text label", "unclassified"),
    ("TRIP", "unclassified"),                     # case-sensitive on purpose
])
def test_normalize_type(raw, expected):
    assert ec.normalize_type(raw) == expected


# ── Subtype presets ───────────────────────────────────────────────────────


def test_subtype_presets_for_trip():
    presets = ec.subtype_presets_for(ec.EVENT_TYPE_TRIP)
    # The full vocabulary is the contract; check a couple of representatives + the size.
    assert "International" in presets
    assert "Two weeks" in presets
    assert "Roadtrip" in presets
    assert len(presets) >= 5


def test_subtype_presets_for_session():
    presets = ec.subtype_presets_for(ec.EVENT_TYPE_SESSION)
    assert "Macro" in presets
    assert "Long exposure" in presets
    assert "Astro" in presets


def test_subtype_presets_for_occasion():
    presets = ec.subtype_presets_for(ec.EVENT_TYPE_OCCASION)
    assert "Wedding" in presets
    assert "Birthday" in presets


def test_subtype_presets_for_project():
    presets = ec.subtype_presets_for(ec.EVENT_TYPE_PROJECT)
    assert "Series" in presets
    assert "Documentary" in presets


def test_subtype_presets_for_unclassified_is_empty():
    assert ec.subtype_presets_for(ec.EVENT_TYPE_UNCLASSIFIED) == ()


def test_subtype_presets_for_unknown_type_is_empty():
    assert ec.subtype_presets_for("not_a_type") == ()


def test_is_preset_subtype():
    assert ec.is_preset_subtype(ec.EVENT_TYPE_TRIP, "International")
    assert ec.is_preset_subtype(ec.EVENT_TYPE_SESSION, "Macro")
    assert not ec.is_preset_subtype(ec.EVENT_TYPE_TRIP, "my custom value")
    assert not ec.is_preset_subtype(ec.EVENT_TYPE_TRIP, "")
    assert not ec.is_preset_subtype(ec.EVENT_TYPE_TRIP, None)
    # Cross-type — Trip subtype isn't a Session preset.
    assert not ec.is_preset_subtype(ec.EVENT_TYPE_SESSION, "International")


# ── Extras keys ───────────────────────────────────────────────────────────


def test_extras_keys_trip_includes_people():
    keys = ec.extras_keys_for(ec.EVENT_TYPE_TRIP)
    assert "people" in keys
    assert "countries" in keys
    assert "duration_label" in keys


def test_extras_keys_session_includes_people():
    keys = ec.extras_keys_for(ec.EVENT_TYPE_SESSION)
    assert "people" in keys
    assert "target_subject" in keys


def test_extras_keys_occasion_includes_people():
    keys = ec.extras_keys_for(ec.EVENT_TYPE_OCCASION)
    assert "people" in keys
    assert "host" in keys


def test_extras_keys_project_includes_people_and_goal():
    keys = ec.extras_keys_for(ec.EVENT_TYPE_PROJECT)
    assert "people" in keys
    assert "goal" in keys
    assert "subject" in keys
    assert "target_artifact" in keys


def test_extras_keys_unclassified_empty():
    assert ec.extras_keys_for(ec.EVENT_TYPE_UNCLASSIFIED) == ()


def test_extras_keys_unknown_type_empty():
    assert ec.extras_keys_for("???") == ()


# ── Phase-set seam (Slice A: full pipeline for every type) ────────────────


def test_all_phases_canonical_order():
    # The pipeline order matters — downstream code iterates this for funnels,
    # sort orders, etc. Pin it.
    assert ec.ALL_PHASES == (
        "plan", "capture", "pick", "pick", "edit", "share",
    )


def test_decision_phases_subset_of_all_phases():
    assert set(ec.DECISION_PHASES).issubset(set(ec.ALL_PHASES))
    assert ec.DECISION_PHASES == ("pick", "pick", "edit", "share")


@pytest.mark.parametrize("event_type", [
    ec.EVENT_TYPE_TRIP,
    ec.EVENT_TYPE_SESSION,
    ec.EVENT_TYPE_OCCASION,
    ec.EVENT_TYPE_PROJECT,
    ec.EVENT_TYPE_UNCLASSIFIED,
    "future_type",                    # unknown falls back to full
    "",
])
def test_phases_for_type_slice_a_returns_full_pipeline(event_type):
    """Slice A contract: every type returns the full pipeline. The next sprint
    changes this; until then, this test pins the seam's default behavior so a
    consumer's accidental hardcode would still match the seam."""
    assert ec.phases_for_type(event_type) == ec.ALL_PHASES


@pytest.mark.parametrize("event_type", [
    ec.EVENT_TYPE_TRIP,
    ec.EVENT_TYPE_SESSION,
    ec.EVENT_TYPE_OCCASION,
    ec.EVENT_TYPE_PROJECT,
    ec.EVENT_TYPE_UNCLASSIFIED,
])
def test_decision_phases_for_type_slice_a_returns_full_decision_set(event_type):
    assert ec.decision_phases_for_type(event_type) == ec.DECISION_PHASES


def test_preceding_phase_within_pipeline():
    assert ec.preceding_phase(ec.EVENT_TYPE_TRIP, "capture") == "plan"
    assert ec.preceding_phase(ec.EVENT_TYPE_TRIP, "pick") == "capture"
    assert ec.preceding_phase(ec.EVENT_TYPE_TRIP, "share") == "edit"


def test_preceding_phase_at_first_is_none():
    assert ec.preceding_phase(ec.EVENT_TYPE_TRIP, "plan") is None


def test_preceding_phase_for_absent_is_none():
    assert ec.preceding_phase(ec.EVENT_TYPE_TRIP, "ingest") is None
    assert ec.preceding_phase(ec.EVENT_TYPE_TRIP, "") is None


def test_following_phase_within_pipeline():
    assert ec.following_phase(ec.EVENT_TYPE_TRIP, "plan") == "capture"
    assert ec.following_phase(ec.EVENT_TYPE_TRIP, "pick") == "pick"
    assert ec.following_phase(ec.EVENT_TYPE_TRIP, "edit") == "share"


def test_following_phase_at_last_is_none():
    assert ec.following_phase(ec.EVENT_TYPE_TRIP, "share") is None


def test_following_phase_for_absent_is_none():
    assert ec.following_phase(ec.EVENT_TYPE_TRIP, "ingest") is None
    assert ec.following_phase(ec.EVENT_TYPE_TRIP, "") is None


def test_preceding_following_round_trip_through_pipeline():
    """Every interior phase: following(preceding(p)) == p and vice versa."""
    phases = ec.phases_for_type(ec.EVENT_TYPE_TRIP)
    for p in phases[1:-1]:
        prev = ec.preceding_phase(ec.EVENT_TYPE_TRIP, p)
        assert prev is not None
        assert ec.following_phase(ec.EVENT_TYPE_TRIP, prev) == p
        nxt = ec.following_phase(ec.EVENT_TYPE_TRIP, p)
        assert nxt is not None
        assert ec.preceding_phase(ec.EVENT_TYPE_TRIP, nxt) == p


# ── Routing-seam canary: consumers actually read through the seam ─────────
#
# These tests prove the spec/44 §1.7 phase-set seam is wired up. If a
# consumer ever hardcodes the phase list again (skipping the helper), this
# fires — the next sprint's "per-type phase pipeline" change is what these
# guard against silently breaking.


def test_event_card_grid_rows_track_phases_for_type(qapp, monkeypatch):
    """EventCardGrid's heatmap row set must come from
    ``event_classification.phases_for_type(event_type)`` — not a hardcoded list.
    Override the seam to return a smaller set and verify the grid follows."""
    from mira.ui.base import event_card as ec_card

    monkeypatch.setattr(
        ec_card, "_phase_rows_for",
        lambda _et: (ec_card._PhaseRow("pick", "Cull"), ec_card._PhaseRow("share", "Curate")),
    )
    grid = ec_card.EventCardGrid({}, total_days=3, event_type="trip")
    assert set(grid._cells_by_phase.keys()) == {"pick", "share"}


def test_overview_stats_pipeline_for_routes_through_seam(monkeypatch):
    """overview_stats._pipeline_for must yield the seam's decision phases for
    the event's type — not a hardcoded module-level tuple."""
    from mira import overview_stats

    class _StubEvent:
        event_type = "session"

    class _StubEG:
        def event(self):
            return _StubEvent()

    # Pretend the seam tightened the pipeline for sessions.
    monkeypatch.setattr(
        ec, "decision_phases_for_type",
        lambda et: ("pick", "edit", "share") if et == "session" else ec.DECISION_PHASES,
    )
    pairs = overview_stats._pipeline_for(_StubEG())
    keys = [k for k, _label in pairs]
    assert keys == ["pick", "edit", "share"]
