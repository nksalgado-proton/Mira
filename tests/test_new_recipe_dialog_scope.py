"""spec/90 Phase 4b — Scope section + Event / Event Collection / date-range
operand tests.

* Scope row visibility follows ``show_scope``.
* Scope picker surfaces Events, Event Collections, and the
  ``+ Add date range…`` row; Source picker doesn't show any of them.
* Adding chips emits the composition['scope'] shape spec/90 §5.1 documents.
* Date-range picker's quick-selects compute the right start/end given a
  frozen wall clock.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from mira.ui.pages import new_recipe_dialog as nrd
from mira.ui.pages.new_recipe_dialog import (
    FLAVOUR_COLLECTION,
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    JOIN_OR,
    NewRecipeContext,
    NewRecipeDialog,
    OperandOption,
    PICKER_TARGET_SCOPE,
    PICKER_TARGET_SOURCE,
    _DateRangePickerPopover,
    _OperandPickerPopover,
)


def _events():
    return [
        OperandOption(name="[Alaska]", count=120, kind="event",
                      uuid="evt-alaska"),
        OperandOption(name="[Bali]", count=80, kind="event",
                      uuid="evt-bali"),
    ]


def _event_collections():
    return [
        OperandOption(name="#adventure_events", count=3,
                      kind="event_collection",
                      tag="adventure_events", id="ec-adv"),
    ]


def _collection_dialog(qapp, *, ctx=None, **over) -> NewRecipeDialog:
    if ctx is None:
        ctx = NewRecipeContext(
            available_pools=[
                OperandOption(name="#exported", count=12, kind="base"),
            ],
            available_events=_events(),
            available_event_collections=_event_collections(),
            available_styles=["macro"],
        )
    kw = dict(
        flavour=FLAVOUR_COLLECTION,
        show_scope=True,
        show_hardware=True,
        inventory_scope=INVENTORY_LIBRARY,
        ctx=ctx,
    )
    kw.update(over)
    return NewRecipeDialog(**kw)


def _cut_dialog(qapp, **over) -> NewRecipeDialog:
    ctx = NewRecipeContext(
        available_pools=[
            OperandOption(name="#exported", count=12, kind="base"),
        ],
        available_styles=["macro"],
    )
    kw = dict(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx,
    )
    kw.update(over)
    return NewRecipeDialog(**kw)


# --------------------------------------------------------------------------- #
# Scope row visibility
# --------------------------------------------------------------------------- #


def test_scope_section_hidden_when_show_scope_false(qapp):
    dlg = _cut_dialog(qapp)
    assert dlg.findChild(object, "ScopeSection") is None


def test_scope_section_visible_when_show_scope_true(qapp):
    dlg = _collection_dialog(qapp)
    assert dlg.findChild(object, "ScopeSection") is not None


def test_scope_row_renders_lead_and_add_button(qapp):
    dlg = _collection_dialog(qapp)
    # Lead label "Events:" + the + button render even with no chips.
    has_lead = False
    has_add = False
    for i in range(dlg._scope_row.count()):
        w = dlg._scope_row.itemAt(i).widget()
        if w is None:
            continue
        if w.objectName() == "PoolAddLabel":
            has_lead = True
        if isinstance(w, type(dlg._scope_box).__mro__[0]):
            pass
        if hasattr(w, "text") and w.text() == "+":
            has_add = True
    assert has_lead
    assert has_add


# --------------------------------------------------------------------------- #
# Picker — Scope target shows the three sections; Source target doesn't
# --------------------------------------------------------------------------- #


def _section_headers(picker: _OperandPickerPopover):
    """Return the labels of every section header (QLabel #Micro) in the
    picker's list layout, in order."""
    out = []
    for i in range(picker._list_layout.count()):
        w = picker._list_layout.itemAt(i).widget()
        if w is None:
            continue
        if w.objectName() == "Micro":
            out.append(w.text())
    return out


def test_scope_picker_shows_events_collections_and_date_ranges(qapp):
    picker = _OperandPickerPopover(
        target=PICKER_TARGET_SCOPE,
        events=_events(),
        event_collections=_event_collections(),
    )
    headers = _section_headers(picker)
    # spec/90 §3.4 order — Events · Event Collections · Date ranges.
    # Headers render uppercased (Micro QSS), so match by lowercased value.
    lowered = [h.lower() for h in headers]
    assert lowered == ["events", "event collections", "date ranges"]
    # Date-range row is the single "+ Add date range…" button.
    assert picker._date_range_row is not None
    assert "Add date range" in picker._date_range_row.text()


def test_source_picker_hides_events_collections_and_date_ranges(qapp):
    """Picking from the Source section MUST NOT surface scope operands —
    Source's universe is items, not events."""
    pools = [
        OperandOption(name="#exported", count=10, kind="base"),
        OperandOption(name="#long", count=200, kind="cut", tag="long"),
    ]
    picker = _OperandPickerPopover(
        pools, target=PICKER_TARGET_SOURCE,
        events=_events(),                       # passed but ignored
        event_collections=_event_collections(),  # passed but ignored
    )
    lowered = [h.lower() for h in _section_headers(picker)]
    assert "events" not in lowered
    assert "event collections" not in lowered
    assert "date ranges" not in lowered
    assert picker._date_range_row is None
    # Source-level Save as DC moved to the "Which items?" band header
    # (spec/90 §5.5); the popover no longer carries its own entry.
    assert picker._save_btn is None


def test_scope_picker_omits_save_as_dc_button(qapp):
    """Save as DC doesn't apply to Scope — Scope output is event sets,
    not item sets."""
    picker = _OperandPickerPopover(
        target=PICKER_TARGET_SCOPE,
        events=_events(),
        event_collections=_event_collections(),
    )
    assert picker._save_btn is None


def test_invalid_picker_target_raises(qapp):
    with pytest.raises(ValueError, match="picker target"):
        _OperandPickerPopover(target="elsewhere")


# --------------------------------------------------------------------------- #
# Chip flow — event, event_collection
# --------------------------------------------------------------------------- #


def test_picking_event_adds_event_chip(qapp):
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_events()[0])  # [Alaska]
    assert dlg._scope_chips == [(JOIN_OR, _events()[0])]


def test_picking_event_collection_adds_collection_chip(qapp):
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_event_collections()[0])
    assert dlg._scope_chips[-1][1].name == "#adventure_events"
    assert dlg._scope_chips[-1][1].kind == "event_collection"


def test_remove_scope_chip(qapp):
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_events()[0])
    dlg._add_scope_chip(_events()[1])
    dlg._remove_scope_chip(0)
    assert len(dlg._scope_chips) == 1
    assert dlg._scope_chips[0][1].uuid == "evt-bali"


def test_two_scope_chips_render_join_chevron_between_them(qapp):
    """spec/90 §3.2 — Phase 4c hooks the join word to
    :class:`_JoinChevron` so the user can swap one-click between
    ``or`` / ``and`` / ``but not in``. ``join_word()`` returns the
    bare word (without the chevron glyph)."""
    from mira.ui.pages.new_recipe_dialog import _JoinChevron
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_events()[0])
    dlg._add_scope_chip(_events()[1])
    chevrons = []
    for i in range(dlg._scope_row.count()):
        w = dlg._scope_row.itemAt(i).widget()
        if isinstance(w, _JoinChevron):
            chevrons.append(w)
    assert len(chevrons) == 1
    assert chevrons[0].join_word() == JOIN_OR


# --------------------------------------------------------------------------- #
# Scope expression encoding
# --------------------------------------------------------------------------- #


def test_scope_expression_empty_when_no_chips(qapp):
    dlg = _collection_dialog(qapp)
    assert dlg.scope_expression() == []


def test_scope_expression_event_operand(qapp):
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_events()[0])
    assert dlg.scope_expression() == [
        ["+", {"kind": "event", "uuid": "evt-alaska"}]
    ]


def test_scope_expression_event_collection_operand(qapp):
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_event_collections()[0])
    assert dlg.scope_expression() == [
        ["+", {"kind": "event_collection",
               "tag": "adventure_events", "id": "ec-adv"}]
    ]


def test_scope_expression_date_range_operand(qapp):
    dlg = _collection_dialog(qapp)
    dlg._add_date_range_chip("2018-01-01", "2020-12-31")
    assert dlg.scope_expression() == [
        ["+", {"kind": "date_range",
               "start": "2018-01-01", "end": "2020-12-31"}]
    ]


def test_scope_expression_mixed_operands(qapp):
    """Phase 4b joins default to ``or``; the encoding is union for every
    join. (The dropdown to swap to ``and`` / ``but not in`` lands in 4c.)"""
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_events()[0])
    dlg._add_scope_chip(_event_collections()[0])
    dlg._add_date_range_chip("2018-01-01", "2020-12-31")
    assert dlg.scope_expression() == [
        ["+", {"kind": "event", "uuid": "evt-alaska"}],
        ["+", {"kind": "event_collection",
               "tag": "adventure_events", "id": "ec-adv"}],
        ["+", {"kind": "date_range",
               "start": "2018-01-01", "end": "2020-12-31"}],
    ]


def test_scope_expression_empty_when_show_scope_false(qapp):
    """A Cut-face dialog should never emit a non-empty scope, even if
    something is staged on the internal list (defensive)."""
    dlg = _cut_dialog(qapp)
    # Internal scope list is empty by construction on the Cut face.
    assert dlg._scope_chips == []
    assert dlg.scope_expression() == []


def test_scope_chip_render_shows_compact_date_range_label(qapp):
    """The chip name format ``[YYYY-MM-DD — YYYY-MM-DD]`` is what the
    user sees in the Scope sentence (spec/90 §3.1)."""
    dlg = _collection_dialog(qapp)
    dlg._add_date_range_chip("2018-01-01", "2020-12-31")
    operand = dlg._scope_chips[-1][1]
    assert operand.name == "[2018-01-01 — 2020-12-31]"


# --------------------------------------------------------------------------- #
# Date-range picker — quick-selects, OK/Cancel, swap on inverted dates
# --------------------------------------------------------------------------- #


@pytest.fixture
def frozen_today(monkeypatch):
    """Freeze the date-range picker's wall clock at 2026-06-20."""
    fixed = date(2026, 6, 20)
    monkeypatch.setattr(nrd, "_today", lambda: fixed)
    return fixed


def test_date_range_picker_default_dates_span_last_year(qapp, frozen_today):
    picker = _DateRangePickerPopover()
    start = picker._start_edit.date()
    end = picker._end_edit.date()
    assert (end.year(), end.month(), end.day()) == (2026, 6, 20)
    assert (start.year(), start.month(), start.day()) == (2025, 6, 20)


def test_date_range_picker_quick_select_last_12_months(qapp, frozen_today):
    picker = _DateRangePickerPopover()
    last_12 = picker._quick_buttons[0]   # First preset.
    last_12.click()
    s = picker._start_edit.date()
    e = picker._end_edit.date()
    assert (s.year(), s.month(), s.day()) == (2025, 6, 20)
    assert (e.year(), e.month(), e.day()) == (2026, 6, 20)


def test_date_range_picker_quick_select_last_3_years(qapp, frozen_today):
    picker = _DateRangePickerPopover()
    picker._quick_buttons[1].click()    # Last 3 years
    s = picker._start_edit.date()
    e = picker._end_edit.date()
    assert (s.year(), s.month(), s.day()) == (2023, 6, 20)
    assert (e.year(), e.month(), e.day()) == (2026, 6, 20)


def test_date_range_picker_quick_select_last_5_years(qapp, frozen_today):
    picker = _DateRangePickerPopover()
    picker._quick_buttons[2].click()    # Last 5 years
    s = picker._start_edit.date()
    e = picker._end_edit.date()
    assert (s.year(), s.month(), s.day()) == (2021, 6, 20)
    assert (e.year(), e.month(), e.day()) == (2026, 6, 20)


def test_date_range_picker_quick_select_all_time(qapp, frozen_today):
    picker = _DateRangePickerPopover()
    picker._quick_buttons[3].click()    # All time
    s = picker._start_edit.date()
    e = picker._end_edit.date()
    # All-time start is the module-level _ALL_TIME_START = 1900-01-01.
    assert (s.year(), s.month(), s.day()) == (1900, 1, 1)
    assert (e.year(), e.month(), e.day()) == (2026, 6, 20)


def test_date_range_picker_emits_iso_range_on_ok(qapp, frozen_today):
    picker = _DateRangePickerPopover()
    picker._quick_buttons[1].click()    # Last 3 years
    seen = []
    picker.range_chosen.connect(
        lambda s, e: seen.append((s, e)))
    picker._on_ok()
    assert seen == [("2023-06-20", "2026-06-20")]


def test_date_range_picker_swaps_inverted_dates(qapp, frozen_today):
    """A user who picks End before Start should still get a usable
    range — the picker swaps the order on confirm."""
    from PyQt6.QtCore import QDate
    picker = _DateRangePickerPopover()
    picker._start_edit.setDate(QDate(2020, 12, 31))
    picker._end_edit.setDate(QDate(2018, 1, 1))
    seen = []
    picker.range_chosen.connect(
        lambda s, e: seen.append((s, e)))
    picker._on_ok()
    assert seen == [("2018-01-01", "2020-12-31")]


# --------------------------------------------------------------------------- #
# Scope summary
# --------------------------------------------------------------------------- #


def test_scope_summary_counts_chip_event_counts(qapp):
    """Phase 4b doesn't have a real scope evaluator (the resolver takes
    pre-resolved uuids); the summary uses the chips' declared counts as
    a stand-in. Date-range chips don't contribute (they don't carry a
    count today)."""
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_events()[0])      # 120
    dlg._add_scope_chip(_events()[1])      # 80
    dlg._add_date_range_chip("2018-01-01", "2020-12-31")
    assert "200 events" in dlg._scope_summary.text()
