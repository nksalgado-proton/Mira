"""spec/81 Phase 2 polish — :class:`NewCrossEventCutDialog` UI tests.

Drives the cross-event Cut config dialog. The session that drives the commit
(:class:`CrossEventCutSession`) is tested elsewhere; this file focuses on the
dialog's surface — the user's name + DC + anchor + budget choices map cleanly
to :class:`CrossEventCutInfo`, the cross-event defaults match spec/81 §3.1,
the gating rules hold.
"""
from __future__ import annotations

import pytest

from mira.shared.cut_draft import PIN_KEEP_ALL, PIN_PICK_IN, PIN_WEED_OUT
from mira.ui.pages.new_cross_event_cut_dialog import (
    CrossEventCutInfo,
    CrossEventCutInventories,
    NewCrossEventCutDialog,
)


_INVENTORIES = CrossEventCutInventories(
    dynamic_collections=(
        ("dc-best-macro", "#best_macro"),
        ("dc-nepal", "#nepal_2025"),
    ),
    events=(
        ("evt-A", "Costa Rica 2026"),
        ("evt-B", "Nepal 2025"),
    ),
    music_categories=("happy", "samba"),
)


def _open(qapp, **kw):
    return NewCrossEventCutDialog(inventories=_INVENTORIES, **kw)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_tag_preview_slugifies_live(qapp):
    d = _open(qapp)
    d._name.setText("Nepal Highlights")
    assert d._tag_preview.text() == "tag: #nepal_highlights"
    d.deleteLater()


def test_tag_preview_warns_on_reserved(qapp):
    d = _open(qapp)
    d._name.setText("Picked")
    assert "reserved" in d._tag_preview.text()
    d.deleteLater()


def test_tag_preview_warns_on_taken(qapp):
    d = _open(qapp, existing_tags=("nepal_highlights",))
    d._name.setText("Nepal Highlights")
    assert "in use" in d._tag_preview.text()
    d.deleteLater()


def test_accept_gated_on_empty_name(qapp):
    d = _open(qapp)
    fired = []
    d.saved.connect(lambda info: fired.append(info))
    d._on_accept()
    assert fired == []
    d.deleteLater()


def test_accept_gated_on_no_dc(qapp):
    d = NewCrossEventCutDialog(
        inventories=CrossEventCutInventories(
            dynamic_collections=(),                     # no DCs
            events=(("evt-A", "Costa Rica"),),
        ))
    d._name.setText("x")
    fired = []
    d.saved.connect(lambda info: fired.append(info))
    d._on_accept()
    assert fired == []
    d.deleteLater()


def test_accept_gated_on_no_anchor(qapp):
    d = NewCrossEventCutDialog(
        inventories=CrossEventCutInventories(
            dynamic_collections=(("dc-1", "#x"),),
            events=(),                                  # no events
        ))
    d._name.setText("x")
    fired = []
    d.saved.connect(lambda info: fired.append(info))
    d._on_accept()
    assert fired == []
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Defaults — spec/81 §3.1 cross-event
# --------------------------------------------------------------------------- #


def test_pin_mode_default_keep_all(qapp):
    """Cross-event Cuts default to keep-all (1:1 pin) — the Picker UI isn't
    built yet."""
    d = _open(qapp)
    d._name.setText("x")
    assert d.info().pin_mode == PIN_KEEP_ALL
    d.deleteLater()


def test_all_pin_modes_enabled_with_picker(qapp):
    """spec/81 Phase 2 polish completed: the cross-event Picker landed,
    so every pin mode is selectable. Picking weed-out / pick-in routes the
    commit through :class:`CrossEventPickerDialog`."""
    d = _open(qapp)
    for btn, _value in d._pin_buttons:
        assert btn.isEnabled()
    d.deleteLater()


def test_pin_mode_picks_weed_out_when_selected(qapp):
    """Selecting weed-out lands the right pin_mode in the info."""
    d = _open(qapp)
    d._name.setText("x")
    for btn, value in d._pin_buttons:
        if value == PIN_WEED_OUT:
            btn.setChecked(True)
    assert d.info().pin_mode == PIN_WEED_OUT
    d.deleteLater()


def test_separators_default_off(qapp):
    """spec/81 §3.1: cross-event Cuts default separators OFF (no single
    timeline to orient)."""
    d = _open(qapp)
    d._name.setText("x")
    assert d.info().separators is False
    d.deleteLater()


def test_overlay_fields_default_all_four(qapp):
    """spec/81 §3.1: cross-event Cuts default overlays ON, all four fields
    (when / where / how¹ / how²)."""
    d = _open(qapp)
    d._name.setText("x")
    assert set(d.info().overlay_fields) == {"when", "where", "how1", "how2"}
    d.deleteLater()


def test_overlay_mode_default_none(qapp):
    """Default overlay mode = NULL → inherit the settings default."""
    d = _open(qapp)
    d._name.setText("x")
    assert d.info().overlay_mode is None
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Source DC + anchor picker
# --------------------------------------------------------------------------- #


def test_default_dc_pre_selected(qapp):
    """``default_dc_id`` constructor argument pre-selects the matching combo
    entry. The cross-event Dcs dialog uses this when the user clicks Pin →
    Cut on a specific DC row."""
    d = _open(qapp, default_dc_id="dc-nepal")
    d._name.setText("x")
    assert d.info().source_dc_id == "dc-nepal"
    d.deleteLater()


def test_default_anchor_event_pre_selected(qapp):
    """``default_anchor_event_id`` pre-selects the anchor combo. The host
    computes this from the DC's resolved keys (the event contributing the
    most members)."""
    d = _open(qapp, default_anchor_event_id="evt-B")
    d._name.setText("x")
    assert d.info().anchor_event_id == "evt-B"
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Budget + music
# --------------------------------------------------------------------------- #


def test_budget_minutes_default_to_none_when_disabled(qapp):
    """Target / max stay None until explicitly enabled."""
    d = _open(qapp)
    d._name.setText("x")
    info = d.info()
    assert info.target_s is None
    assert info.max_s is None
    d.deleteLater()


def test_budget_enable_emits_seconds(qapp):
    """Enabling the target stepper at 5 min → target_s=300."""
    d = _open(qapp)
    d._name.setText("x")
    d._target_enable.setChecked(True)
    d._target_spin.setValue(5)
    d._max_enable.setChecked(True)
    d._max_spin.setValue(10)
    info = d.info()
    assert info.target_s == 300
    assert info.max_s == 600
    d.deleteLater()


def test_photo_seconds_default_six(qapp):
    """Per-photo default = 6s (the event-scope default)."""
    d = _open(qapp)
    d._name.setText("x")
    assert d.info().photo_s == 6.0
    d.deleteLater()


def test_music_default_none(qapp):
    """The music combo's first entry is `(none)` — default selection."""
    d = _open(qapp)
    d._name.setText("x")
    assert d.info().music_category is None
    d.deleteLater()


def test_music_pick_lands_category(qapp):
    """Selecting a real category emits the category name."""
    d = _open(qapp)
    d._name.setText("x")
    # Find 'happy' in the combo.
    for i in range(d._music_combo.count()):
        if d._music_combo.itemText(i) == "happy":
            d._music_combo.setCurrentIndex(i)
            break
    assert d.info().music_category == "happy"
    d.deleteLater()


def test_no_audio_library_disables_combo(qapp):
    """Empty music_categories inventory → combo disabled, music_category
    stays None."""
    d = NewCrossEventCutDialog(
        inventories=CrossEventCutInventories(
            dynamic_collections=(("dc-1", "#x"),),
            events=(("evt-A", "Costa Rica"),),
            music_categories=(),
        ))
    d._name.setText("x")
    assert not d._music_combo.isEnabled()
    assert d.info().music_category is None
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Accept emits valid info
# --------------------------------------------------------------------------- #


def test_accept_emits_full_info_shape(qapp):
    d = _open(qapp, default_dc_id="dc-best-macro",
              default_anchor_event_id="evt-A")
    d._name.setText("Hero Pinned")
    d._separators.setChecked(True)
    d._target_enable.setChecked(True)
    d._target_spin.setValue(8)
    fired: list = []
    d.saved.connect(lambda info: fired.append(info))
    d._on_accept()
    assert len(fired) == 1
    info = fired[0]
    assert info.name == "Hero Pinned"
    assert info.source_dc_id == "dc-best-macro"
    assert info.anchor_event_id == "evt-A"
    assert info.pin_mode == PIN_KEEP_ALL
    assert info.target_s == 480
    assert info.separators is True
    d.deleteLater()
