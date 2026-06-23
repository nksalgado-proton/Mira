"""Tests for spec/114 — restore the overlay control in NewRecipeDialog.

Six contracts:

* The control sets ``overlay_mode`` + ``overlay_fields`` into
  ``composition()["presentation"]`` when the user opts in.
* Prefill (Edit-mode) pre-selects the saved mode + chip checks.
* Mode = Off keeps the saved chip picks AROUND (don't lose work) but the
  presentation block omits both fields — adapters read it as Off.
* End-to-end: ``EventGateway.create_cut(overlay_fields=…,
  overlay_mode=…)`` persists, ``EventGateway.cut_overlay_fields`` reads
  them back, and ``export_cut`` writes the matching overlay
  (embedded IPTC for one mode, burn-in pixels for the other).
* The cross-event path also persists overlay via
  ``LibraryGateway.create_cross_event_cut`` + the same dialog with
  ``inventory_scope == INVENTORY_LIBRARY``.
* The pre-spec/114 ``composition()`` schema stays exact when nothing
  overlay is set — the regression guard for unrelated dialog tests.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import cut_overlay
from mira.gateway.event_gateway import EventGateway
from mira.gateway.library_gateway import LibraryGateway
from mira.shared.cut_export import export_cut
from mira.store.repo import EventStore
from mira.ui.pages.new_recipe_dialog import (
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    NewRecipeContext,
    NewRecipeDialog,
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
            overlay_mode=None, overlay_fields=(), inventory=INVENTORY_EVENT):
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
        overlay_field_options=list(
            overlay_field_options if overlay_field_options is not None
            else _overlay_vocab()),
        overlay_mode=overlay_mode,
        overlay_fields=list(overlay_fields),
    )
    return NewRecipeDialog(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=inventory,
        ctx=ctx,
    )


# --------------------------------------------------------------------- #
# 1. The control surfaces overlay_mode + overlay_fields in composition()
# --------------------------------------------------------------------- #


def test_setting_mode_and_fields_emits_them_in_presentation(qapp):
    """spec/114 — the user picks Embedded + two fields; the
    composition's presentation block carries them under the canonical
    keys the export layer + adapter already read."""
    dlg = _dialog(qapp)
    try:
        # Off → no overlay keys at all.
        comp_off = dlg.composition()
        assert "overlay_mode" not in comp_off["presentation"]
        assert "overlay_fields" not in comp_off["presentation"]

        # Pick Embedded + the WHERE + HOW1 chips.
        dlg._overlay_mode_combo.setCurrentIndex(1)
        dlg._overlay_field_chips[cut_overlay.FIELD_WHERE].setChecked(True)
        dlg._overlay_field_chips[cut_overlay.FIELD_HOW1].setChecked(True)

        comp = dlg.composition()
        pres = comp["presentation"]
        assert pres["overlay_mode"] == "embedded"
        # Order follows the canonical OVERLAY_FIELDS tuple so the
        # round-trip is stable regardless of click order.
        assert pres["overlay_fields"] == [
            cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW1,
        ]
    finally:
        dlg.deleteLater()


def test_burn_in_mode_persists_through_composition(qapp):
    """The Burn-in option is its own enum value the export layer keys
    on. Pin both modes for clarity."""
    dlg = _dialog(qapp)
    try:
        dlg._overlay_mode_combo.setCurrentIndex(2)
        dlg._overlay_field_chips[cut_overlay.FIELD_HOW2].setChecked(True)
        comp = dlg.composition()
        assert comp["presentation"]["overlay_mode"] == "burn_in"
        assert comp["presentation"]["overlay_fields"] == [
            cut_overlay.FIELD_HOW2]
    finally:
        dlg.deleteLater()


def test_mode_off_drops_overlay_keys_even_with_chips_checked(qapp):
    """The chip checks survive a flip to Off (don't lose work on a
    fat-finger), but the composition omits the overlay block so the
    adapter reads it as Off — exactly today's behaviour."""
    dlg = _dialog(qapp)
    try:
        dlg._overlay_mode_combo.setCurrentIndex(1)
        dlg._overlay_field_chips[cut_overlay.FIELD_WHERE].setChecked(True)
        assert "overlay_mode" in dlg.composition()["presentation"]

        # Flip Off — the dialog state forgets the mode, the chips stay
        # checked so the user can flip back without re-picking.
        dlg._overlay_mode_combo.setCurrentIndex(0)
        chips_still_checked = [
            k for k, c in dlg._overlay_field_chips.items() if c.isChecked()
        ]
        assert chips_still_checked == [cut_overlay.FIELD_WHERE]
        # …but the composition omits both fields.
        pres = dlg.composition()["presentation"]
        assert "overlay_mode" not in pres
        assert "overlay_fields" not in pres
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 2. Prefill — opening Edit on an existing Cut pre-selects the choice
# --------------------------------------------------------------------- #


def test_prefill_seeds_mode_and_chips_from_ctx(qapp):
    """spec/114 — the host prefills ``NewRecipeContext.overlay_mode`` +
    ``.overlay_fields`` from the existing Cut; the dialog opens on
    those exact values."""
    dlg = _dialog(
        qapp, overlay_mode="embedded",
        overlay_fields=[cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW2])
    try:
        assert dlg._overlay_mode == "embedded"
        assert dlg._overlay_mode_combo.currentData() == "embedded"
        checked = sorted(
            k for k, c in dlg._overlay_field_chips.items() if c.isChecked()
        )
        assert checked == sorted(
            [cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW2])
        # The composition emits the same shape on first call (no edit).
        pres = dlg.composition()["presentation"]
        assert pres["overlay_mode"] == "embedded"
        assert set(pres["overlay_fields"]) == {
            cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW2}
    finally:
        dlg.deleteLater()


def test_prefill_drops_unknown_overlay_mode(qapp):
    """A legacy / typo'd overlay_mode reads as Off — the dialog never
    surfaces an invalid value the export layer would mis-render."""
    dlg = _dialog(qapp, overlay_mode="not-a-mode")
    try:
        assert dlg._overlay_mode is None
        assert dlg._overlay_mode_combo.currentData() is None
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 3. The control is hidden when the host didn't supply the vocabulary
# --------------------------------------------------------------------- #


def test_empty_vocab_hides_the_control_entirely(qapp):
    """A host without the vocabulary gets the legacy zero-control state
    (overlays simply off). Pin this so a future surface that forgets
    to wire ``overlay_field_options`` doesn't crash on a missing combo."""
    dlg = _dialog(qapp, overlay_field_options=[])
    try:
        assert not dlg._overlay_field_chips
        # The mode combo only exists when the box is built; missing
        # vocabulary means missing widgets.
        assert not hasattr(dlg, "_overlay_mode_combo")
        # composition emits nothing overlay-related.
        pres = dlg.composition()["presentation"]
        assert "overlay_mode" not in pres
        assert "overlay_fields" not in pres
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 4. End-to-end — create_cut persists, export_cut writes the overlay
# --------------------------------------------------------------------- #


def _make_event_gw(tmp_path) -> EventGateway:
    """A minimal event.db + one Exported Media file the export can
    point at. Adapted from the cut_export fixture so the overlay
    end-to-end test stays self-contained."""
    from tests.test_gateway_cuts import _doc, _now
    store = EventStore.create(tmp_path / "event.db", event_id="evt-o")
    store.save_document(_doc())
    p = tmp_path / "Exported Media" / "e1.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FILE:e1.jpg")
    gw = EventGateway(store, event_root=tmp_path, now=_now)
    return gw


def test_create_cut_persists_overlay_mode_and_fields(tmp_path):
    """spec/114 — the dialog → create_cut path persists overlay. The
    fields round-trip via ``cut_overlay_fields`` (which parses the
    persisted JSON column)."""
    gw = _make_event_gw(tmp_path)
    try:
        cut = gw.create_cut(
            "show", overlay_mode="embedded",
            overlay_fields=[cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW1],
        )
        loaded = gw.cut(cut.id)
        assert loaded is not None
        assert loaded.overlay_mode == "embedded"
        assert list(gw.cut_overlay_fields(loaded)) == [
            cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW1]
    finally:
        gw.close()


def test_export_cut_writes_embedded_iptc_when_mode_is_embedded(tmp_path):
    """An ``embedded`` Cut with the *where* field hits the IPTC writer
    seam exactly once per shipped file. The pixels stay links so the
    rest of the show keeps the spec/57 link-pure invariant."""
    gw = _make_event_gw(tmp_path)
    try:
        cut = gw.create_cut(
            "show-embed", overlay_mode="embedded",
            overlay_fields=[cut_overlay.FIELD_WHERE],
        )
        gw.set_cut_members(cut.id, ["Exported Media/e1.jpg"])

        def _prov(_relpath):
            return cut_overlay.FrameProvenance(
                city="Tokyo", country="Japan")

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
        path, tags = iptc_calls[0]
        # The IPTC tag set matches the spec/32 §2c where mapping.
        assert tags[cut_overlay.IPTC_CITY] == "Tokyo"
        assert tags[cut_overlay.IPTC_COUNTRY] == "Japan"
    finally:
        gw.close()


def test_export_cut_burns_pixels_when_mode_is_burn_in(tmp_path):
    """A ``burn_in`` Cut hits the overlay renderer (which emits a
    COPY, not a link). Pin the copy count + the renderer seam so the
    spec/114 → spec/107 wiring stays honest."""
    gw = _make_event_gw(tmp_path)
    try:
        cut = gw.create_cut(
            "show-burn", overlay_mode="burn_in",
            overlay_fields=[cut_overlay.FIELD_WHERE],
        )
        gw.set_cut_members(cut.id, ["Exported Media/e1.jpg"])

        def _prov(_relpath):
            return cut_overlay.FrameProvenance(city="Tokyo")

        renderer_calls: list = []

        def _renderer(src, dst, fields, prov):
            renderer_calls.append(
                (Path(src), Path(dst), tuple(fields), prov))
            dst.write_bytes(b"BURNED")

        result = export_cut(
            gw, gw.cut(cut.id), event_root=tmp_path,
            separators_on=False,
            provenance_resolver=_prov, overlay_renderer=_renderer,
        )
        assert result.burned_in == 1
        assert result.linked == 0 and result.copied == 1
        assert len(renderer_calls) == 1
        _src, _dst, fields, _prov = renderer_calls[0]
        assert fields == (cut_overlay.FIELD_WHERE,)
    finally:
        gw.close()


# --------------------------------------------------------------------- #
# 5. Cross-event side — same dialog, library gateway persistence
# --------------------------------------------------------------------- #


def test_cross_event_create_cut_persists_overlay(tmp_path):
    """spec/114 §4 — the same dialog (with ``INVENTORY_LIBRARY``)
    drives ``LibraryGateway.create_cross_event_cut``; the new column
    + the existing ``overlay_*`` kwargs persist the overlay. Pin both
    sides so a future divergence stays caught."""
    us = UserStore.create(
        tmp_path / "mira.db", app_version="t", created_at=NOW)
    try:
        lg = LibraryGateway(us, now=lambda: NOW)
        cut = lg.create_cross_event_cut(
            "share",
            overlay_mode="burn_in",
            overlay_fields=[cut_overlay.FIELD_HOW2],
        )
        loaded = lg.cross_event_cut(cut.id)
        assert loaded is not None
        assert loaded.overlay_mode == "burn_in"
        # The library gateway reads its fields from JSON column;
        # consume the same list to stay symmetrical with the per-event
        # gateway's ``cut_overlay_fields``.
        import json as _json
        assert _json.loads(loaded.overlay_fields_json) == [
            cut_overlay.FIELD_HOW2]
    finally:
        us.close()


def test_cross_event_dialog_emits_overlay_in_composition(qapp):
    """The cross-event surface uses the same NewRecipeDialog under
    ``INVENTORY_LIBRARY``. The contract is identical: pick mode +
    fields, composition carries them."""
    dlg = _dialog(qapp, inventory=INVENTORY_LIBRARY)
    try:
        dlg._overlay_mode_combo.setCurrentIndex(1)
        dlg._overlay_field_chips[cut_overlay.FIELD_WHERE].setChecked(True)
        pres = dlg.composition()["presentation"]
        assert pres["overlay_mode"] == "embedded"
        assert pres["overlay_fields"] == [cut_overlay.FIELD_WHERE]
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 6. Regression — the pre-spec/114 composition schema is unchanged when
# the user never touches overlay (the dialog's other tests rely on this)
# --------------------------------------------------------------------- #


def test_composition_schema_unchanged_when_overlay_untouched(qapp):
    """Pin the legacy presentation block exactly so the other dialog
    tests (and external consumers like the recipe adapter) keep reading
    the same keys. spec/114 only adds two keys; both stay absent when
    the user keeps overlay Off."""
    dlg = _dialog(qapp)
    try:
        pres = dlg.composition()["presentation"]
        # Required keys (the pre-spec/114 surface).
        assert "photo_s" in pres
        assert "music_category" in pres
        assert "aspect" in pres
        # Overlay keys absent in the Off / default state.
        assert "overlay_mode" not in pres
        assert "overlay_fields" not in pres
    finally:
        dlg.deleteLater()
