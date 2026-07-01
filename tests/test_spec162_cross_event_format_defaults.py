"""spec/162 §7.3 Round 3b — cross-event Format defaults.

Cross-event Cuts are search-result-first, not presentation-first: when
composing a new cross-event Cut the dialog seeds Section 2 with a
different set of defaults from the event-Cut Nelson designed for a
slideshow-play use.

Pins:
* Budget row hidden (checkbox off, ``_has_budget=False``).
* Transition 0 s.
* Music: no category.
* Overlays: When + Where + Source label on; Camera + Exposure off.
* Separators off.
* Aspect + per-photo cadence unchanged (still 16:9 / 3 s / etc.).

Event-scope + mode=edit keep the pre-existing seeding path so this
change is scope × mode gated.
"""
from __future__ import annotations

from dataclasses import replace as _replace
from types import SimpleNamespace

import pytest

from core import cut_overlay as _co
from mira.ui.pages.new_cut_dialog import (
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    MODE_EDIT,
    MODE_NEW,
    SCOPE_CROSS_EVENT,
    SCOPE_EVENT,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
)


def _ctx(**over) -> NewRecipeContext:
    ctx = NewRecipeContext(
        available_pools=[
            OperandOption(name="#exported", count=12, kind="base"),
        ],
        available_events=[
            OperandOption(name="[Alaska]", count=100, kind="event",
                          uuid="evt-a"),
        ],
        available_styles=["macro"],
        overlay_field_options=[
            (_co.FIELD_WHEN, "When"),
            (_co.FIELD_WHERE, "Where"),
            (_co.FIELD_HOW1, "Camera"),
            (_co.FIELD_HOW2, "Exposure"),
        ],
        music_categories=["ambient", "upbeat"],
    )
    for k, v in over.items():
        setattr(ctx, k, v)
    return ctx


def _cross_event_dialog(qapp, *, mode=MODE_NEW, ctx=None) -> NewCutDialog:
    if ctx is None:
        ctx = _ctx()
    return NewCutDialog(
        scope=SCOPE_CROSS_EVENT,
        mode=mode,
        show_scope=True,
        show_hardware=True,
        inventory_scope=INVENTORY_LIBRARY,
        ctx=ctx,
    )


def _event_dialog(qapp, *, mode=MODE_NEW, ctx=None) -> NewCutDialog:
    if ctx is None:
        ctx = _ctx()
    return NewCutDialog(
        scope=SCOPE_EVENT,
        mode=mode,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx,
    )


def test_cross_event_new_hides_budget(qapp):
    dlg = _cross_event_dialog(qapp)
    assert dlg._has_budget is False


def test_cross_event_new_transition_is_hard_cut(qapp):
    dlg = _cross_event_dialog(qapp)
    assert dlg._transition_ms == 0
    # The user-set flag flips so the draft emits the honest 0 rather
    # than reading as an untouched global-default fallthrough.
    assert dlg._transition_user_set is True


def test_cross_event_new_no_music_seed(qapp):
    dlg = _cross_event_dialog(qapp, ctx=_ctx(music_category="ambient"))
    # Even when the context carried a music category, a NEW cross-
    # event Cut lands on no-music.
    assert dlg._music_category is None


def test_cross_event_new_overlays_default_to_when_where_and_source_label(qapp):
    dlg = _cross_event_dialog(qapp)
    assert set(dlg._overlay_fields) == {_co.FIELD_WHEN, _co.FIELD_WHERE}
    assert dlg._source_label is True


def test_cross_event_new_separators_off(qapp):
    dlg = _cross_event_dialog(qapp)
    assert dlg._separators is False


def test_cross_event_new_summary_reads_search_defaults(qapp):
    dlg = _cross_event_dialog(qapp)
    text = dlg._format_summary_text()
    # spec/162 §7.3 — chip reads e.g. "Format · 16:9 · no budget · 2 overlays · search defaults"
    assert "Format" in text
    assert "no budget" in text
    assert "search defaults" in text
    # No explicit "no music" slot at cross-event scope.
    assert "no music" not in text


def test_event_scope_new_summary_reads_no_music_no_search_defaults(qapp):
    """Event-scope dialog keeps the presentation-first chip shape."""
    dlg = _event_dialog(qapp)
    text = dlg._format_summary_text()
    assert "Format" in text
    # Event scope default has_budget=True, so "no budget" doesn't show.
    assert "no budget" not in text
    # No "search defaults" hint at event scope.
    assert "search defaults" not in text


def test_cross_event_edit_preserves_prefill_budget(qapp):
    """Edit mode does NOT apply cross-event new defaults — the existing
    Cut's saved values win."""
    ctx = _ctx(has_budget=True, target_minutes=5, is_editing=True,
               separators=True, music_category="ambient")
    dlg = _cross_event_dialog(qapp, mode=MODE_EDIT, ctx=ctx)
    assert dlg._has_budget is True
    assert dlg._target_minutes == 5
    assert dlg._separators is True
    assert dlg._music_category == "ambient"


def test_event_scope_new_does_not_apply_cross_event_defaults(qapp):
    """Event-scope + new preserves the presentation-first defaults —
    budget on, separators mirror the context, overlays follow prefill."""
    ctx = _ctx(has_budget=True, separators=True)
    dlg = _event_dialog(qapp, ctx=ctx)
    assert dlg._has_budget is True
    # Event-scope defaults: no cross-event override applied.
    assert dlg._transition_ms == dlg._default_transition_ms
    assert dlg._separators is True
