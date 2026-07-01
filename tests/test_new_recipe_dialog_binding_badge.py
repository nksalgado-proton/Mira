"""Block 6 — the binding badge inside NewCutDialog (spec/93 §7).

The badge is a read-only chip in the Recipe toolbar that reports the
placement spec/93 §5 computes. The dialog calls
``classify_placement(composition)`` on each probe and reflects the
result through the badge widget.

These tests drive the badge by feeding a stub classifier callback to
the dialog and triggering :meth:`_run_probe`. We assert badge text
+ migration-note visibility/text without needing the resolver to do
real work.
"""
from __future__ import annotations

import pytest

from core.placement_classifier import (
    BoundPlacement,
    PLACEMENT_CROSS_BOUND,
    PLACEMENT_GLOBAL,
)
from mira.ui.pages.new_cut_dialog import (
    SCOPE_EVENT,
    INVENTORY_EVENT,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
)


def _ctx() -> NewRecipeContext:
    return NewRecipeContext(
        event_name="Costa Rica 2026",
        available_pools=[
            OperandOption(name="#exported", count=12, kind="base"),
        ],
        available_styles=["macro"],
    )


def _dialog_with_classifier(qapp, classifier, *, event_name=None):
    return NewCutDialog(
        scope=SCOPE_EVENT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=_ctx(),
        recipe_probe=lambda comp: _stub_resolution(),
        classify_placement=classifier,
        event_name_for_id=event_name,
    )


def _stub_resolution():
    """A minimal RecipeResolution-shaped object — the dialog's metrics
    code reads ``pool`` + ``seed`` + ``rule_breakdown`` only."""
    from core.recipe_resolver import RecipeResolution
    return RecipeResolution(pool=[], seed={}, rule_breakdown=())


# ── default (no callback) ─────────────────────────────────────────


def test_badge_defaults_to_global_when_no_classifier(qapp):
    """No callback wired → badge stays "Global" (the safe default)."""
    dlg = _dialog_with_classifier(qapp, classifier=None)
    assert dlg._binding_badge.text() == "Global"      # noqa: SLF001


# ── classifier wired ──────────────────────────────────────────────


def test_badge_reflects_global_classifier_output(qapp):
    """Classifier returns GLOBAL → badge reads "Global"."""
    dlg = _dialog_with_classifier(qapp, classifier=lambda c: PLACEMENT_GLOBAL)
    dlg._run_probe()                                  # noqa: SLF001
    assert dlg._binding_badge.text() == "Global"      # noqa: SLF001


def test_badge_reflects_bound_classifier_output_with_event_name(qapp):
    """Classifier returns BoundPlacement; the event_name_for_id lookup
    resolves the human name."""
    placement = BoundPlacement(event_id="evt-CR-2026")
    dlg = _dialog_with_classifier(
        qapp, classifier=lambda c: placement,
        event_name=lambda eid: "Costa Rica" if eid == "evt-CR-2026" else "",
    )
    dlg._run_probe()                                  # noqa: SLF001
    assert dlg._binding_badge.text() == "Event: Costa Rica"  # noqa: SLF001


def test_badge_reflects_bound_with_id_stub_when_name_unknown(qapp):
    """No event_name_for_id callback → badge falls back to an
    id-prefix stub."""
    placement = BoundPlacement(event_id="abcdef1234")
    dlg = _dialog_with_classifier(qapp, classifier=lambda c: placement)
    dlg._run_probe()                                  # noqa: SLF001
    assert dlg._binding_badge.text() == "Event: abcdef12"  # noqa: SLF001


def test_badge_reflects_cross_bound(qapp):
    """Classifier returns CROSS_BOUND → badge reads "Cross-event"."""
    dlg = _dialog_with_classifier(
        qapp, classifier=lambda c: PLACEMENT_CROSS_BOUND)
    dlg._run_probe()                                  # noqa: SLF001
    assert dlg._binding_badge.text() == "Cross-event"  # noqa: SLF001


# ── migration note ────────────────────────────────────────────────


def test_migration_note_hidden_on_first_classify(qapp):
    """First successful classify never shows a migration note — there's
    nothing to compare against (no prior placement)."""
    dlg = _dialog_with_classifier(qapp, classifier=lambda c: PLACEMENT_GLOBAL)
    dlg._run_probe()                                  # noqa: SLF001
    assert dlg._migration_note.text() == ""   # noqa: SLF001


class _PlacementOracle:
    """Stateful classifier stub that walks a list and stays on the
    last entry once exhausted (so the construction-time probe doesn't
    StopIteration before the test runs its own probes)."""

    def __init__(self, *steps):
        self._steps = list(steps)
        self._i = 0

    def __call__(self, _comp):
        out = self._steps[min(self._i, len(self._steps) - 1)]
        self._i += 1
        return out


def test_migration_note_fires_on_global_to_bound_flip(qapp):
    """Two probes; the second flips placement → migration note shows
    with the spec/93 §7 'now specific to' copy."""
    oracle = _PlacementOracle(
        PLACEMENT_GLOBAL,
        BoundPlacement(event_id="evt-A"),
    )
    dlg = _dialog_with_classifier(
        qapp, classifier=oracle,
        event_name=lambda eid: "Alaska",
    )
    dlg._run_probe()                                  # noqa: SLF001 — Global
    dlg._run_probe()                                  # noqa: SLF001 — Bound
    assert bool(dlg._migration_note.text()) is True    # noqa: SLF001
    assert "Alaska" in dlg._migration_note.text()     # noqa: SLF001


def test_migration_note_fires_on_bound_to_global_flip(qapp):
    """The inverse direction also fires a note ('now reusable in any
    event.')."""
    oracle = _PlacementOracle(
        BoundPlacement(event_id="evt-A"),
        PLACEMENT_GLOBAL,
    )
    dlg = _dialog_with_classifier(
        qapp, classifier=oracle,
        event_name=lambda eid: "Alaska",
    )
    dlg._run_probe()                                  # noqa: SLF001
    dlg._run_probe()                                  # noqa: SLF001
    assert bool(dlg._migration_note.text()) is True    # noqa: SLF001
    assert "reusable" in dlg._migration_note.text()   # noqa: SLF001


def test_migration_note_hidden_when_no_flip(qapp):
    """Two consecutive probes with the same placement → no migration
    note (a "still global" toast would be noise)."""
    dlg = _dialog_with_classifier(qapp, classifier=lambda c: PLACEMENT_GLOBAL)
    dlg._run_probe()                                  # noqa: SLF001
    dlg._run_probe()                                  # noqa: SLF001
    assert dlg._migration_note.text() == ""   # noqa: SLF001


# ── failure modes ────────────────────────────────────────────────


def test_classifier_exception_keeps_badge_as_is(qapp):
    """A classifier that raises → badge stays at its last-good state;
    the exception is logged, not propagated. The badge must never be
    a crash surface."""
    def _explode(_comp):
        raise RuntimeError("oops")

    dlg = _dialog_with_classifier(qapp, classifier=_explode)
    # Default is "Global" — should stay there after a raising probe.
    dlg._run_probe()                                  # noqa: SLF001
    assert dlg._binding_badge.text() == "Global"      # noqa: SLF001
