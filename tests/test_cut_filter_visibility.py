"""Tests for spec/113 — active-filter visibility in the Cut dialog.

Three contracts:

* **Stronger active visual**: a checked style chip exposes its checked
  state to QSS via ``isChecked()`` / the ``#PillToggle`` role, so the
  redesign.qss role can paint the unmistakable accent fill.
* **"Filters active" indicator**: the persistent notice appears iff any
  filter axis is non-default (style ∪ media-type ∪ hardware), reads
  "{n} filter(s) active — showing X of Y items", and quotes Y from a
  second resolver call with filters cleared.
* **One-click clear**: the Clear filters button empties styles, restores
  the media-type default, unchecks hardware chips, hides the indicator,
  and re-probes so the metrics line returns to the unfiltered count.

The dialog is built with stub probes — the test pins UI behaviour, not
the live resolver."""
from __future__ import annotations

import pytest

from core.recipe_resolver import RecipeResolution
from mira.ui.pages.new_recipe_dialog import (
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    NewRecipeContext,
    NewRecipeDialog,
    OperandOption,
)


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #


def _pools():
    return [
        OperandOption(name="#exported", count=200, kind="base"),
    ]


def _resolution(pool_size: int) -> RecipeResolution:
    """Cheap stand-in for a resolver run — the dialog only reads
    ``len(resolution.pool)`` for the indicator's X / Y."""
    pool = [f"key-{i}" for i in range(pool_size)]
    seed = {k: False for k in pool}
    return RecipeResolution(pool=pool, seed=seed)


def _make_dialog(qapp, *, show_hardware=True):
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro", "landscape"],
        available_cameras=["G9", "X100"],
        available_lenses=["35mm", "85mm"],
    )

    # Probe stub: pretend the source pool is 200 items unfiltered, half
    # that when any filter is active. The test inspects the indicator's
    # text + visibility, not the resolver itself.
    state = {"unfiltered": 200, "filtered": 80}

    def _probe(composition):
        filters = composition.get("filters") or {}
        active = bool(filters.get("styles")
                      or filters.get("camera_ids")
                      or filters.get("lens_models"))
        if (filters.get("media_type") or "both") != "both":
            active = True
        size = state["filtered"] if active else state["unfiltered"]
        return _resolution(size)

    dlg = NewRecipeDialog(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=show_hardware,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx,
        recipe_probe=_probe,
    )
    # A non-empty source so the probe's resolution isn't dropped as a
    # hint (the dialog short-circuits on an empty source expression).
    dlg._add_source_chip(_pools()[0])
    dlg._run_probe()
    return dlg, state


def _show(dlg) -> None:
    """Show the dialog so visibility predicates read True for visible
    widgets. ``setVisible`` propagates to ``isVisible`` only after the
    parent chain is shown."""
    dlg.show()
    dlg.adjustSize()


# --------------------------------------------------------------------- #
# 1. Stronger active visual — chip checked state drives the QSS role
# --------------------------------------------------------------------- #


def test_style_chip_checked_state_drives_qss_role(qapp):
    """spec/113 §2 — a checked style chip carries the ``isChecked``
    flag the ``#PillToggle:checked`` role keys off. Pinning this so a
    future refactor that swaps the chip widget can't break the visual
    cue (the QSS rule depends on ``QAbstractButton.isChecked()``)."""
    dlg, _ = _make_dialog(qapp)
    try:
        chip = dlg._style_chips["macro"]
        assert chip.isChecked() is False
        chip.setChecked(True)
        assert chip.isChecked() is True
        # The chip's object name must be the role the QSS keys off.
        # ``StyleChip`` overrides ``#PillToggle`` for nuance later; the
        # active-fill QSS lives on the underlying ``#PillToggle`` role.
        assert chip.objectName() in ("StyleChip", "PillToggle")
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 2. "Filters active" indicator — visibility + content
# --------------------------------------------------------------------- #


def test_indicator_hidden_when_no_filter_is_active(qapp):
    """spec/113 §3 — a clean Cut has no clutter. With every filter at
    its default the indicator must be hidden (no row, no label text)."""
    dlg, _ = _make_dialog(qapp)
    try:
        _show(dlg)
        assert dlg._active_filter_count() == 0
        assert dlg._filter_indicator_row.isHidden() is True
        assert dlg._filter_indicator_label.text() == ""
    finally:
        dlg.deleteLater()


def test_selecting_a_style_shows_indicator_with_x_below_y(qapp):
    """spec/113 §3 — clicking a style pill triggers the cue. The
    sentence carries the filter count, the current ``X`` (filtered pool
    size) and ``Y`` (unfiltered pool size). ``X < Y`` because the
    filter shrunk the pool."""
    dlg, state = _make_dialog(qapp)
    try:
        _show(dlg)
        dlg._style_chips["macro"].setChecked(True)
        dlg._run_probe()
        assert dlg._active_filter_count() == 1
        assert dlg._filter_indicator_row.isHidden() is False
        text = dlg._filter_indicator_label.text()
        assert "1 filter active" in text
        assert "80" in text
        assert "200" in text
        # The sentence pins the contract — the dialog must NOT silently
        # report X == Y while announcing a filter is active.
        assert "showing 80 of 200" in text
    finally:
        dlg.deleteLater()


def test_indicator_counts_style_media_and_hardware_axes(qapp):
    """spec/113 §3 — every axis contributes to ``{n} filter(s)``: each
    checked style + a non-default media type + each checked camera +
    each checked lens."""
    dlg, _ = _make_dialog(qapp, show_hardware=True)
    try:
        _show(dlg)
        # Two styles + media-type-not-default + one camera + one lens
        # = 5 active axes.
        dlg._style_chips["macro"].setChecked(True)
        dlg._style_chips["landscape"].setChecked(True)
        dlg._videos_cb.setChecked(False)              # media-type = photo
        dlg._camera_chips["G9"].setChecked(True)
        dlg._lens_chips["35mm"].setChecked(True)
        assert dlg._active_filter_count() == 5
        dlg._run_probe()
        assert dlg._filter_indicator_row.isHidden() is False
        text = dlg._filter_indicator_label.text()
        assert "5 filters active" in text
    finally:
        dlg.deleteLater()


def test_indicator_quotes_unfiltered_pool_via_second_probe(qapp):
    """spec/113 §3 — Y comes from a second resolver call with filters
    cleared, NOT a cached value. Verify by spying on the probe's
    composition argument."""
    seen: list = []

    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
    )

    def _probe(composition):
        seen.append({
            "filters": dict(composition.get("filters") or {}),
        })
        active = bool((composition.get("filters") or {}).get("styles"))
        return _resolution(50 if active else 200)

    dlg = NewRecipeDialog(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx,
        recipe_probe=_probe,
    )
    try:
        dlg._add_source_chip(_pools()[0])
        dlg._style_chips["macro"].setChecked(True)
        # Drain anything that fired during chip wiring so we count only
        # the explicit run_probe below.
        seen.clear()
        dlg._run_probe()
        # First call: the filtered composition (style chip checked).
        # Second call: the unfiltered probe issued by the indicator
        # refresh (filters cleared on the duplicate composition).
        assert len(seen) >= 2
        filtered_call, unfiltered_call = seen[0], seen[1]
        assert filtered_call["filters"].get("styles") == ["macro"]
        assert unfiltered_call["filters"].get("styles") == []
        assert unfiltered_call["filters"].get("media_type") == "both"
        # The label quotes the unfiltered count (200) as Y.
        text = dlg._filter_indicator_label.text()
        assert "200" in text
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 3. Clear filters — one-click recovery
# --------------------------------------------------------------------- #


def test_clear_filters_empties_styles_media_hardware_and_hides_indicator(qapp):
    """spec/113 §3 — [Clear filters] resets EVERY filter axis in one
    click, hides the indicator, and re-probes so the metrics line
    returns to the unfiltered pool size."""
    dlg, _ = _make_dialog(qapp, show_hardware=True)
    try:
        _show(dlg)
        dlg._style_chips["macro"].setChecked(True)
        dlg._videos_cb.setChecked(False)
        dlg._camera_chips["G9"].setChecked(True)
        dlg._lens_chips["35mm"].setChecked(True)
        dlg._run_probe()
        assert dlg._filter_indicator_row.isHidden() is False

        dlg._on_clear_filters()
        # Visibility flips immediately (no debounce wait).
        assert dlg._filter_indicator_row.isHidden() is True
        # Every axis is restored to its unfiltered default.
        assert dlg._selected_styles() == []
        assert dlg._media_type() == "both"
        assert dlg._selected_cameras() == []
        assert dlg._selected_lenses() == []
        assert dlg._active_filter_count() == 0
        # Re-running the probe must NOT bring the indicator back; the
        # composition is clean.
        dlg._run_probe()
        assert dlg._filter_indicator_row.isHidden() is True
        assert dlg._filter_indicator_label.text() == ""
    finally:
        dlg.deleteLater()


def test_clear_filters_button_is_wired_to_handler(qapp):
    """spec/113 §3 — the Clear button triggers the same code path the
    handler does. Clicking it from the UI must produce the same effect
    as calling the method directly so the indicator clears even when
    invoked from a real mouse press."""
    dlg, _ = _make_dialog(qapp, show_hardware=False)
    try:
        _show(dlg)
        dlg._style_chips["macro"].setChecked(True)
        dlg._run_probe()
        assert dlg._filter_indicator_row.isHidden() is False
        # ``QPushButton.click`` fires the connected handler.
        dlg._filter_clear_btn.click()
        assert dlg._selected_styles() == []
        assert dlg._filter_indicator_row.isHidden() is True
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 4. Cross-event Cuts honour the same contract (spec/113 §4)
# --------------------------------------------------------------------- #


def test_indicator_works_for_library_inventory_dialog(qapp):
    """Cross-event Cuts use the same dialog with ``inventory_scope ==
    INVENTORY_LIBRARY``. The indicator must behave identically — same
    chip wiring, same probe path, same visibility predicate. Pinning
    this so a future per-flavour split doesn't bypass spec/113."""
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
    )

    def _probe(composition):
        filters = composition.get("filters") or {}
        active = bool(filters.get("styles"))
        return _resolution(40 if active else 120)

    dlg = NewRecipeDialog(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_LIBRARY,
        ctx=ctx,
        recipe_probe=_probe,
    )
    try:
        dlg._add_source_chip(_pools()[0])
        dlg._run_probe()
        assert dlg._filter_indicator_row.isHidden() is True

        dlg._style_chips["macro"].setChecked(True)
        dlg._run_probe()
        assert dlg._filter_indicator_row.isHidden() is False
        text = dlg._filter_indicator_label.text()
        assert "40" in text and "120" in text
    finally:
        dlg.deleteLater()
