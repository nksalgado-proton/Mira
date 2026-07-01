"""Tests for the Cut overlay control (spec/114, simplified by spec/153).

spec/153 retired the Off / Embedded / Burn-in mode combo: the overlay
control is now **just the four field flags** (When / Where / Camera /
Exposure). Overlays are on when ≥1 flag is checked, off when none; the
generated ``.pte`` always carries the selected fields as separate ``:Text``
objects (``overlay_mode`` is fixed at ``"embedded"`` internally).

Contracts pinned here:

* checking ≥1 field surfaces ``overlay_mode="embedded"`` + ``overlay_fields``
  in ``composition()["presentation"]``; no fields → both keys omitted (the
  legacy Off shape the recipe adapter tolerates);
* prefill (Edit-mode) pre-checks the saved field chips;
* end-to-end: ``create_cut`` persists, ``cut_overlay_fields`` reads back,
  ``export_cut`` embeds the *where* IPTC while members stay links;
* the cross-event path persists the same way;
* the chips are real QCheckBoxes in canonical field order.
"""
from __future__ import annotations

from pathlib import Path

from core import cut_overlay
from mira.gateway.event_gateway import EventGateway
from mira.gateway.library_gateway import LibraryGateway
from mira.shared.cut_export import export_cut
from mira.store.repo import EventStore
from mira.ui.pages.new_cut_dialog import (
    SCOPE_EVENT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
)
from mira.user_store.repo import UserStore


NOW = "2026-06-22T00:00:00+00:00"


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #


def _pools():
    return [OperandOption(name="#exported", count=200, kind="base")]


def _overlay_vocab():
    return [
        (cut_overlay.FIELD_WHEN, "When"),
        (cut_overlay.FIELD_WHERE, "Where"),
        (cut_overlay.FIELD_HOW1, "Camera"),
        (cut_overlay.FIELD_HOW2, "Exposure"),
    ]


def _dialog(qapp, *, overlay_field_options=None,
            overlay_fields=(), inventory=INVENTORY_EVENT,
            source_label=False):
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
        overlay_field_options=list(
            overlay_field_options if overlay_field_options is not None
            else _overlay_vocab()),
        overlay_fields=list(overlay_fields),
        source_label=source_label,
    )
    return NewCutDialog(
        scope=SCOPE_EVENT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=inventory,
        ctx=ctx,
    )


# --------------------------------------------------------------------- #
# 1. Field flags drive the overlay block in composition()
# --------------------------------------------------------------------- #


def test_checking_fields_emits_embedded_mode_and_fields(qapp):
    """spec/153 — checking ≥1 field surfaces ``overlay_mode="embedded"``
    + the canonical-ordered field list under the keys the export layer +
    adapter already read."""
    dlg = _dialog(qapp)
    try:
        # No fields → no overlay keys (the legacy Off shape).
        comp_off = dlg.composition()
        assert "overlay_mode" not in comp_off["presentation"]
        assert "overlay_fields" not in comp_off["presentation"]

        dlg._overlay_field_chips[cut_overlay.FIELD_WHERE].setChecked(True)
        dlg._overlay_field_chips[cut_overlay.FIELD_HOW1].setChecked(True)

        pres = dlg.composition()["presentation"]
        assert pres["overlay_mode"] == "embedded"
        # Order follows the canonical OVERLAY_FIELDS tuple regardless of
        # click order.
        assert pres["overlay_fields"] == [
            cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW1,
        ]
    finally:
        dlg.deleteLater()


def test_no_fields_omits_overlay_keys(qapp):
    """Unchecking back to zero fields drops both overlay keys so the
    adapter reads it as off — no smuggling stale picks through."""
    dlg = _dialog(qapp)
    try:
        chip = dlg._overlay_field_chips[cut_overlay.FIELD_WHERE]
        chip.setChecked(True)
        assert "overlay_mode" in dlg.composition()["presentation"]
        chip.setChecked(False)
        pres = dlg.composition()["presentation"]
        assert "overlay_mode" not in pres
        assert "overlay_fields" not in pres
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 2. Prefill — opening Edit on an existing Cut pre-checks the chips
# --------------------------------------------------------------------- #


def test_prefill_pre_checks_the_saved_fields(qapp):
    """spec/114/153 — the host prefills ``NewRecipeContext.overlay_fields``
    from the existing Cut; the dialog opens with those chips checked and
    re-emits them (mode fixed at embedded)."""
    dlg = _dialog(
        qapp,
        overlay_fields=[cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW2])
    try:
        assert dlg._overlay_mode == "embedded"
        checked = sorted(
            k for k, c in dlg._overlay_field_chips.items() if c.isChecked())
        assert checked == sorted(
            [cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW2])
        pres = dlg.composition()["presentation"]
        assert pres["overlay_mode"] == "embedded"
        assert set(pres["overlay_fields"]) == {
            cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW2}
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 3. The control is hidden when the host didn't supply the vocabulary
# --------------------------------------------------------------------- #


def test_empty_vocab_hides_the_control_entirely(qapp):
    """A host without the vocabulary gets no chips and emits nothing
    overlay-related — the legacy zero-control state."""
    dlg = _dialog(qapp, overlay_field_options=[])
    try:
        assert not dlg._overlay_field_chips
        pres = dlg.composition()["presentation"]
        assert "overlay_mode" not in pres
        assert "overlay_fields" not in pres
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 4. End-to-end — create_cut persists, export_cut embeds where-IPTC
# --------------------------------------------------------------------- #


def _make_event_gw(tmp_path) -> EventGateway:
    """A minimal event.db + one Exported Media file the export can
    point at."""
    from tests.test_gateway_cuts import _doc, _now
    store = EventStore.create(tmp_path / "event.db", event_id="evt-o")
    store.save_document(_doc())
    p = tmp_path / "Exported Media" / "e1.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FILE:e1.jpg")
    gw = EventGateway(store, event_root=tmp_path, now=_now)
    return gw


def test_create_cut_persists_overlay_fields(tmp_path):
    """The dialog → create_cut path persists overlay fields; they
    round-trip via ``cut_overlay_fields``."""
    gw = _make_event_gw(tmp_path)
    try:
        cut = gw.create_cut(
            "show", overlay_mode="embedded",
            overlay_fields=[cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW1],
        )
        loaded = gw.cut(cut.id)
        assert loaded is not None
        assert list(gw.cut_overlay_fields(loaded)) == [
            cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW1]
    finally:
        gw.close()


def test_export_cut_embeds_where_iptc_and_keeps_links(tmp_path):
    """A Cut with the *where* field hits the IPTC writer once per shipped
    file; members stay links (overlays ride the .pte, not the pixels)."""
    gw = _make_event_gw(tmp_path)
    try:
        cut = gw.create_cut(
            "show-embed", overlay_mode="embedded",
            overlay_fields=[cut_overlay.FIELD_WHERE],
        )
        gw.set_cut_members(cut.id, ["Exported Media/e1.jpg"])

        def _prov(_relpath):
            return cut_overlay.FrameProvenance(city="Tokyo", country="Japan")

        iptc_calls: list = []

        def _iptc(path: Path, tags: dict) -> bool:
            iptc_calls.append((Path(path), dict(tags)))
            return True

        result = export_cut(
            gw, gw.cut(cut.id), event_root=tmp_path,
            separators_on=False,
            provenance_resolver=_prov, iptc_writer=_iptc,
        )
        assert result.iptc_written == 1
        assert result.linked == 1 and result.copied == 0
        assert len(iptc_calls) == 1
        _path, tags = iptc_calls[0]
        assert tags[cut_overlay.IPTC_CITY] == "Tokyo"
        assert tags[cut_overlay.IPTC_COUNTRY] == "Japan"
    finally:
        gw.close()


# --------------------------------------------------------------------- #
# 5. Cross-event side — same dialog, library gateway persistence
# --------------------------------------------------------------------- #


def test_cross_event_create_cut_persists_overlay(tmp_path):
    """The cross-event path persists the overlay fields the same way."""
    us = UserStore.create(
        tmp_path / "mira.db", app_version="t", created_at=NOW)
    try:
        lg = LibraryGateway(us, now=lambda: NOW)
        cut = lg.create_cross_event_cut(
            "share",
            overlay_mode="embedded",
            overlay_fields=[cut_overlay.FIELD_HOW2],
        )
        loaded = lg.cross_event_cut(cut.id)
        assert loaded is not None
        import json as _json
        assert _json.loads(loaded.overlay_fields_json) == [
            cut_overlay.FIELD_HOW2]
    finally:
        us.close()


def test_cross_event_dialog_emits_overlay_in_composition(qapp):
    """The cross-event surface uses the same dialog under
    ``INVENTORY_LIBRARY``; checking a field carries it into the
    composition identically."""
    dlg = _dialog(qapp, inventory=INVENTORY_LIBRARY)
    try:
        dlg._overlay_field_chips[cut_overlay.FIELD_WHERE].setChecked(True)
        pres = dlg.composition()["presentation"]
        assert pres["overlay_mode"] == "embedded"
        assert pres["overlay_fields"] == [cut_overlay.FIELD_WHERE]
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 5b. spec/154 — the per-slide origin-label flag (cross-event only)
# --------------------------------------------------------------------- #


def test_source_label_control_cross_event_only(qapp):
    """The 'Source label per slide' checkbox renders only under the
    library (cross-event) inventory scope; the event-Cut face has no such
    control (a single-event Cut has no provenance to read out)."""
    dlg_event = _dialog(qapp, inventory=INVENTORY_EVENT)
    try:
        assert dlg_event._source_label_check is None
        assert "source_label" not in dlg_event.composition()["presentation"]
    finally:
        dlg_event.deleteLater()
    dlg_xe = _dialog(qapp, inventory=INVENTORY_LIBRARY)
    try:
        assert dlg_xe._source_label_check is not None
        from PyQt6.QtWidgets import QCheckBox
        assert isinstance(dlg_xe._source_label_check, QCheckBox)
        # Off by default → key omitted.
        assert "source_label" not in dlg_xe.composition()["presentation"]
        # Checking it emits the flag.
        dlg_xe._source_label_check.setChecked(True)
        assert dlg_xe.composition()["presentation"]["source_label"] is True
    finally:
        dlg_xe.deleteLater()


def test_source_label_prefill_checks_the_box(qapp):
    """Adjust prefill (``ctx.source_label=True``) opens with the box
    checked and re-emits the flag."""
    dlg = _dialog(qapp, inventory=INVENTORY_LIBRARY, source_label=True)
    try:
        assert dlg._source_label_check.isChecked()
        assert dlg.composition()["presentation"]["source_label"] is True
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 6. Regression — composition schema unchanged when overlay untouched
# --------------------------------------------------------------------- #


def test_composition_schema_unchanged_when_overlay_untouched(qapp):
    """The presentation block keeps its legacy keys and omits the overlay
    keys when no field is checked (external consumers rely on this)."""
    dlg = _dialog(qapp)
    try:
        pres = dlg.composition()["presentation"]
        assert "photo_s" in pres
        assert "music_category" in pres
        assert "aspect" in pres
        assert "overlay_mode" not in pres
        assert "overlay_fields" not in pres
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 7. spec/119 — overlay field selectors are real QCheckBoxes
# --------------------------------------------------------------------- #


def test_overlay_field_chips_are_qcheckboxes(qapp):
    """The overlay-field selectors carry an OS-native checkbox indicator
    (spec/119) and reuse the ``DaysTableCheck`` role."""
    from PyQt6.QtWidgets import QCheckBox
    dlg = _dialog(qapp)
    try:
        for key, chip in dlg._overlay_field_chips.items():
            assert isinstance(chip, QCheckBox), key
            assert chip.objectName() == "DaysTableCheck", key
    finally:
        dlg.deleteLater()


def test_overlay_fields_keep_canonical_order_regardless_of_click_order(qapp):
    """``_on_overlay_field_toggled`` rebuilds the list from
    :data:`core.cut_overlay.OVERLAY_FIELDS`, not click order."""
    dlg = _dialog(qapp)
    try:
        dlg._overlay_field_chips[cut_overlay.FIELD_HOW2].setChecked(True)
        dlg._overlay_field_chips[cut_overlay.FIELD_WHERE].setChecked(True)
        dlg._overlay_field_chips[cut_overlay.FIELD_WHEN].setChecked(True)
        pres = dlg.composition()["presentation"]
        assert pres["overlay_fields"] == [
            cut_overlay.FIELD_WHEN,
            cut_overlay.FIELD_WHERE,
            cut_overlay.FIELD_HOW2,
        ]
    finally:
        dlg.deleteLater()


def test_prefill_pre_checks_the_right_field_checkboxes(qapp):
    """Edit-mode prefill: the saved overlay_fields land checked on the
    matching QCheckBoxes."""
    dlg = _dialog(
        qapp,
        overlay_fields=[cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW1])
    try:
        for key, chip in dlg._overlay_field_chips.items():
            want = key in (cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW1)
            assert chip.isChecked() is want, key
    finally:
        dlg.deleteLater()
