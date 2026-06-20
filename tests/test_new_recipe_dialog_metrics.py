"""spec/90 Phase 4d — live metrics + resolver probe wiring.

* Metrics row reads the right values after a probe.
* Debounce timer fires :meth:`_run_probe` after a quiet period; rapid
  changes coalesce into a single probe call.
* :class:`RecipeResolutionError` shows the error banner with the
  missing-operand name; :class:`ValueError` shows the softer hint.
* Per-rule match labels update from ``rule_breakdown``.
* "N match" without overlap; "N match · M new" with overlap.
"""
from __future__ import annotations

import pytest

from PyQt6.QtTest import QTest

from core.recipe_resolver import (
    RecipeResolution,
    RecipeResolutionError,
    RuleMatchInfo,
)
from mira.ui.pages.new_recipe_dialog import (
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    JOIN_OR,
    NewRecipeContext,
    NewRecipeDialog,
    OperandOption,
    VERDICT_PICK,
    VERDICT_SKIP,
    _format_mm_ss,
)


def _pools():
    return [
        OperandOption(name="#exported", count=200, kind="base"),
        OperandOption(name="#long", count=386, kind="cut", tag="long",
                      id="cut-long"),
        OperandOption(name="#blurry", count=18, kind="cut", tag="blurry",
                      id="cut-blur"),
        OperandOption(name="#bests", count=17, kind="cut", tag="bests",
                      id="cut-bests"),
    ]


def _dialog(qapp, *, recipe_probe=None, **over) -> NewRecipeDialog:
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
    )
    kw = dict(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx,
        recipe_probe=recipe_probe,
    )
    kw.update(over)
    return NewRecipeDialog(**kw)


def _fake_resolution(pool_size: int = 0, picked: int = 0,
                     breakdown=()) -> RecipeResolution:
    """Stand-in for a real resolver run. Tests pass this back from
    ``recipe_probe`` to exercise the dialog's render path."""
    pool = [f"key-{i}" for i in range(pool_size)]
    seed = {f"key-{i}": (i < picked) for i in range(pool_size)}
    return RecipeResolution(pool=pool, seed=seed,
                            rule_breakdown=list(breakdown))


# --------------------------------------------------------------------------- #
# _format_mm_ss helper
# --------------------------------------------------------------------------- #


def test_format_mm_ss_basics():
    assert _format_mm_ss(0) == "0:00"
    assert _format_mm_ss(90) == "1:30"
    assert _format_mm_ss(300) == "5:00"
    assert _format_mm_ss(3661) == "61:01"


def test_format_mm_ss_clamps_negative_and_garbage():
    assert _format_mm_ss(-5) == "0:00"
    assert _format_mm_ss(float("nan")) == "0:00"
    assert _format_mm_ss("not a number") == "0:00"


# --------------------------------------------------------------------------- #
# Metrics row content
# --------------------------------------------------------------------------- #


def test_metrics_row_initially_shows_no_probe_hint(qapp):
    """No recipe_probe wired → friendly hint, no error."""
    dlg = _dialog(qapp)
    assert "no probe wired yet" in dlg._metrics_label.text()
    # Banner stays hidden in the "no probe" case — the metrics label
    # carries the hint.
    assert dlg._metrics_banner.isHidden()


def test_metrics_row_renders_pool_size_picked_runtime_target(qapp):
    """spec/90 §10 — N in pool · M initially picked · MM:SS of MM:SS target."""
    seen = []

    def probe(composition):
        seen.append(composition)
        return _fake_resolution(pool_size=386, picked=11)

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._add_source_chip(_pools()[1])  # #long → triggers a kick
    dlg._run_probe()                   # bypass the debounce wait
    text = dlg._metrics_label.text()
    assert "386" in text
    assert "11" in text
    # Default per_photo_seconds = 6.0 → 11 * 6.0 = 66s → 1:06.
    assert "1:06" in text
    # Default target = 10 min → 10:00.
    assert "10:00" in text
    assert "in pool" in text
    assert "initially picked" in text


def test_metrics_row_updates_when_per_photo_changes(qapp):
    """The spin widgets mutate the runtime state; the metrics line
    re-renders from the last resolution without firing another probe."""
    def probe(_composition):
        return _fake_resolution(pool_size=100, picked=10)

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._run_probe()
    # 10 * 6.0s = 60s → 1:00.
    assert "1:00" in dlg._metrics_label.text()

    # Set per-photo to 12 seconds → 10 * 12 = 120s → 2:00. The probe
    # doesn't re-run (pool/seed didn't change); only the line refreshes.
    dlg._on_per_photo_changed(12.0)
    assert "2:00" in dlg._metrics_label.text()


def test_metrics_row_updates_when_target_changes(qapp):
    def probe(_composition):
        return _fake_resolution(pool_size=10, picked=5)

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._run_probe()
    # Default target 10 min → 10:00.
    assert "10:00" in dlg._metrics_label.text()

    dlg._on_target_changed(20)
    assert "20:00" in dlg._metrics_label.text()


# --------------------------------------------------------------------------- #
# Debounce timer
# --------------------------------------------------------------------------- #


def test_debounce_timer_coalesces_rapid_changes(qapp):
    """Adding three operands in rapid succession should result in ONE
    probe call after the quiet period (~200ms)."""
    calls = []

    def probe(composition):
        calls.append(composition)
        return _fake_resolution(pool_size=0, picked=0)

    dlg = _dialog(qapp, recipe_probe=probe)
    # The end-of-init kick already fired once; clear the trace so we
    # measure only the new rapid sequence.
    QTest.qWait(250)
    calls.clear()

    # Three rapid adds — each kicks the timer; the timer restarts every
    # time, so only ONE probe should fire after the last add settles.
    dlg._add_source_chip(_pools()[0])
    dlg._add_source_chip(_pools()[1])
    dlg._add_source_chip(_pools()[2])
    QTest.qWait(300)                          # > debounce interval

    assert len(calls) == 1


def test_debounce_timer_can_be_bypassed_by_run_probe(qapp):
    """Tests (and tightly-coupled code paths) can bypass the debounce
    by calling :meth:`_run_probe` directly."""
    calls = []

    def probe(_composition):
        calls.append(True)
        return _fake_resolution(pool_size=0, picked=0)

    dlg = _dialog(qapp, recipe_probe=probe)
    calls.clear()
    dlg._run_probe()
    assert calls == [True]


# --------------------------------------------------------------------------- #
# Error banner: RecipeResolutionError + ValueError
# --------------------------------------------------------------------------- #


def test_resolve_recipe_error_shows_error_banner(qapp):
    """spec/90 §1.4 — a deleted DC / Cut / Person / etc. surfaces as
    a RecipeResolutionError. The banner displays the operand label +
    kind so the user knows what to fix."""

    def probe(_composition):
        raise RecipeResolutionError("ghost_dc", kind="dc")

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._run_probe()
    assert (not dlg._metrics_banner.isHidden())
    text = dlg._metrics_banner.text()
    assert "ghost_dc" in text
    assert "dc" in text.lower()
    assert dlg._metrics_banner.property("severity") == "error"


def test_value_error_shows_soft_hint(qapp):
    """An author mistake (empty source, invalid otherwise) is a softer
    failure — surface it as a hint, not a red banner."""

    def probe(_composition):
        raise ValueError("recipe composition has no source expression")

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._run_probe()
    assert (not dlg._metrics_banner.isHidden())
    assert dlg._metrics_banner.property("severity") == "hint"
    assert "source" in dlg._metrics_banner.text().lower()


def test_successful_probe_clears_banner(qapp):
    """After a failure the banner shows; after a successful probe it
    hides."""
    state = {"fail": True}

    def probe(_composition):
        if state["fail"]:
            raise RecipeResolutionError("ghost", kind="cut")
        return _fake_resolution(pool_size=1, picked=1)

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._run_probe()
    assert (not dlg._metrics_banner.isHidden())

    state["fail"] = False
    dlg._run_probe()
    assert dlg._metrics_banner.isHidden()


def test_recipe_resolution_error_clears_rule_breakdown(qapp):
    """When the probe errors, per-rule labels reset to the placeholder
    so stale numbers don't linger."""

    def probe(_composition):
        raise RecipeResolutionError("ghost", kind="cut")

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].show_match_count(42, 12)     # paint a stale number
    dlg._run_probe()
    assert "—" in dlg._rule_rows[0]._match_label.text()


# --------------------------------------------------------------------------- #
# Per-rule match labels
# --------------------------------------------------------------------------- #


def test_rule_label_no_overlap_shows_n_match(qapp):
    """When predicate_match == new_match, the label is "N match"."""

    def probe(_composition):
        return _fake_resolution(
            pool_size=100, picked=42,
            breakdown=[RuleMatchInfo(
                rule_index=0, predicate_match=42, new_match=42)])

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].append_operand(_pools()[1])  # #long → non-empty
    dlg._run_probe()
    label = dlg._rule_rows[0]._match_label.text()
    assert "42" in label
    assert "match" in label
    assert "new" not in label


def test_rule_label_with_overlap_shows_n_match_and_m_new(qapp):
    """When new_match < predicate_match, the label shows both counts."""

    def probe(_composition):
        return _fake_resolution(
            pool_size=100, picked=42,
            breakdown=[
                RuleMatchInfo(rule_index=0, predicate_match=30, new_match=30),
                RuleMatchInfo(rule_index=1, predicate_match=42, new_match=12),
            ])

    dlg = _dialog(qapp, recipe_probe=probe)
    # Two rules with non-empty predicates so both rows attach to a
    # breakdown entry.
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].append_operand(_pools()[1])
    dlg._on_add_rule_clicked()
    dlg._rule_rows[1].append_operand(_pools()[2])
    dlg._run_probe()
    rule2_label = dlg._rule_rows[1]._match_label.text()
    assert "42" in rule2_label
    assert "12" in rule2_label
    assert "new" in rule2_label


def test_rule_label_tooltip_explains_the_new_count(qapp):
    """spec/90 §1.3 — hovering the label explains the §1.3 nuance."""

    def probe(_composition):
        return _fake_resolution(
            pool_size=100, picked=42,
            breakdown=[RuleMatchInfo(
                rule_index=0, predicate_match=42, new_match=12)])

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].append_operand(_pools()[1])
    dlg._run_probe()
    tooltip = dlg._rule_rows[0]._match_label.toolTip()
    assert "42" in tooltip
    assert "12" in tooltip
    assert "earlier" in tooltip.lower() or "rule" in tooltip.lower()


def test_empty_predicate_rule_keeps_placeholder(qapp):
    """A rule with no predicate isn't emitted in rules_expression() and
    the resolver doesn't return a breakdown for it. The row keeps the
    placeholder label."""

    def probe(_composition):
        # No rules in the emitted composition → no breakdown.
        return _fake_resolution(pool_size=10, picked=0, breakdown=[])

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._on_add_rule_clicked()                      # empty predicate
    dlg._run_probe()
    label = dlg._rule_rows[0]._match_label.text()
    assert "—" in label


def test_mixed_rules_align_breakdown_to_non_empty_rows(qapp):
    """When some rows have empty predicates and others don't, only the
    non-empty ones map to a breakdown entry — the empties keep the
    placeholder."""

    def probe(_composition):
        return _fake_resolution(
            pool_size=100, picked=10,
            breakdown=[
                RuleMatchInfo(rule_index=0, predicate_match=10, new_match=10),
            ])

    dlg = _dialog(qapp, recipe_probe=probe)
    # Row 0: empty predicate.
    dlg._on_add_rule_clicked()
    # Row 1: predicate populated.
    dlg._on_add_rule_clicked()
    dlg._rule_rows[1].append_operand(_pools()[1])
    dlg._run_probe()
    assert "—" in dlg._rule_rows[0]._match_label.text()
    assert "10" in dlg._rule_rows[1]._match_label.text()


# --------------------------------------------------------------------------- #
# composition() seam: probe input matches the dialog's emit
# --------------------------------------------------------------------------- #


def test_probe_receives_the_full_composition(qapp):
    captured = {}

    def probe(composition):
        captured["c"] = composition
        return _fake_resolution(pool_size=1, picked=1)

    dlg = _dialog(qapp, recipe_probe=probe)
    dlg._add_source_chip(_pools()[1])
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].append_operand(_pools()[2])
    dlg._run_probe()

    c = captured["c"]
    assert "source" in c and c["source"]
    assert "rules" in c
    assert c["otherwise"] in (VERDICT_PICK, VERDICT_SKIP)
    assert "filters" in c


# --------------------------------------------------------------------------- #
# Bug 1 / Bug 2 — runtime spinners flow into composition, has_budget toggle
# --------------------------------------------------------------------------- #


def test_composition_emits_presentation_with_runtime_fields(qapp):
    """spec/90 §5.1 Bug 1 — ``composition()`` includes a ``presentation``
    dict carrying the runtime spinner values (target_s / max_s in
    seconds; photo_s as float). Without this the recipe_to_cut_draft
    adapter reads None and the picker session loses its budget."""
    dlg = _dialog(qapp)
    comp = dlg.composition()
    assert "presentation" in comp
    presentation = comp["presentation"]
    # Default ctx: target_minutes=10 → 600 s; max_minutes=12 → 720 s;
    # per_photo_seconds=6.0.
    assert presentation["target_s"] == 600
    assert presentation["max_s"] == 720
    assert presentation["photo_s"] == 6.0


def test_composition_spinner_changes_round_trip_into_presentation(qapp):
    """Mutating the spinners updates the emitted presentation dict —
    the seam tests use to confirm the round-trip."""
    dlg = _dialog(qapp)
    dlg._on_target_changed(5)
    dlg._on_max_changed(15)
    dlg._on_per_photo_changed(4.5)
    presentation = dlg.composition()["presentation"]
    assert presentation["target_s"] == 300
    assert presentation["max_s"] == 900
    assert presentation["photo_s"] == 4.5


def test_composition_no_budget_emits_null_target_and_max(qapp):
    """spec/90 §5.1 Bug 2 — with ``has_budget=False`` the presentation
    block emits target_s + max_s as ``None`` (the picker session reads
    "no limit" from the resulting CutDraft). photo_s stays — it's
    slide-rate, not a budget."""
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
        has_budget=False,
    )
    dlg = NewRecipeDialog(
        flavour=FLAVOUR_CUT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    presentation = dlg.composition()["presentation"]
    assert presentation["target_s"] is None
    assert presentation["max_s"] is None
    assert presentation["photo_s"] == 6.0


def test_budget_checkbox_unchecked_disables_target_and_max_spinners(qapp):
    """The Target + Max spinners go disabled when the checkbox is
    unchecked. Per-photo stays enabled (it's slide-rate, not a budget)."""
    ctx = NewRecipeContext(
        available_pools=_pools(), has_budget=False)
    dlg = NewRecipeDialog(
        flavour=FLAVOUR_CUT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    assert dlg._budget_check.isChecked() is False
    assert dlg._target_spin.isEnabled() is False
    assert dlg._max_spin.isEnabled() is False
    assert dlg._per_photo_spin.isEnabled() is True


def test_budget_checkbox_toggle_flips_emitted_presentation(qapp):
    """Toggling the checkbox flips presentation.target_s / max_s
    between concrete int values and None — round-trips per toggle."""
    dlg = _dialog(qapp)
    # Default has_budget=True → numbers.
    assert dlg.composition()["presentation"]["target_s"] == 600
    dlg._on_budget_toggled(False)
    assert dlg.composition()["presentation"]["target_s"] is None
    assert dlg.composition()["presentation"]["max_s"] is None
    dlg._on_budget_toggled(True)
    assert dlg.composition()["presentation"]["target_s"] == 600
    assert dlg.composition()["presentation"]["max_s"] == 720


def test_metrics_line_drops_target_suffix_when_no_budget(qapp):
    """spec/90 §5.1 Bug 2 — with no budget, the metrics line drops the
    "of MM:SS target" suffix and tags the runtime as ``runtime``
    instead. The user sees the projected length without an implied
    limit."""
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
        has_budget=False,
    )
    dlg = NewRecipeDialog(
        flavour=FLAVOUR_CUT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
        recipe_probe=lambda _c: _fake_resolution(pool_size=386, picked=11),
    )
    dlg._run_probe()
    text = dlg._metrics_label.text()
    assert "386" in text
    assert "11" in text
    # 11 * 6.0 = 66s → 1:06.
    assert "1:06" in text
    # The "of N target" suffix is GONE; "runtime" appears instead.
    assert "target" not in text
    assert "runtime" in text


def test_metrics_line_keeps_target_suffix_when_budget_re_enabled(qapp):
    """Toggling the checkbox back on restores the "of MM:SS target"
    suffix without firing a fresh probe."""
    dlg = _dialog(qapp,
                  recipe_probe=lambda _c: _fake_resolution(pool_size=10,
                                                           picked=5))
    dlg._run_probe()
    assert "target" in dlg._metrics_label.text()
    dlg._on_budget_toggled(False)
    assert "target" not in dlg._metrics_label.text()
    assert "runtime" in dlg._metrics_label.text()
    dlg._on_budget_toggled(True)
    assert "target" in dlg._metrics_label.text()


def test_apply_composition_with_null_budget_unchecks_box(qapp):
    """Loading a Recipe whose presentation has target_s=max_s=None
    flips the checkbox off + greys the spinners."""
    dlg = _dialog(qapp)
    # Start with the default checked state, then load a no-budget comp.
    assert dlg._budget_check.isChecked() is True
    dlg._apply_composition({
        "source": [["+", "exported"]],
        "otherwise": "skip",
        "presentation": {"target_s": None, "max_s": None, "photo_s": 6.0},
    })
    assert dlg._budget_check.isChecked() is False
    assert dlg._target_spin.isEnabled() is False
    assert dlg._max_spin.isEnabled() is False
    assert dlg._has_budget is False


def test_apply_composition_with_budget_checks_box(qapp):
    """Loading a Recipe with a real budget flips the checkbox on +
    populates the spinners."""
    ctx = NewRecipeContext(available_pools=_pools(), has_budget=False)
    dlg = NewRecipeDialog(
        flavour=FLAVOUR_CUT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    assert dlg._budget_check.isChecked() is False
    dlg._apply_composition({
        "source": [["+", "exported"]],
        "otherwise": "skip",
        "presentation": {"target_s": 300, "max_s": 600, "photo_s": 4.0},
    })
    assert dlg._budget_check.isChecked() is True
    assert dlg._target_spin.isEnabled() is True
    assert dlg._max_spin.isEnabled() is True
    assert dlg._target_minutes == 5            # 300 s / 60
    assert dlg._max_minutes == 10              # 600 s / 60
    assert dlg._per_photo_seconds == 4.0
