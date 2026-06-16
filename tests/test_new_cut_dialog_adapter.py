"""Adapter + redesigned dialog wiring tests.

Pins the live-probe + template wiring spec/65 §3.13 calls for:
* match count reflects style + media-type filter changes (totals_probe
  is called per change, the label updates from its return),
* Load / Save template round-trip through the adapter into a
  :class:`CutDraft`-shaped payload.

Driven without ``exec()`` like the legacy dialog tests: the adapter is
built with injected probes / templates / saver, the dialog is realised
via ``_build()`` (the same path ``exec()`` takes — sans the modal
event loop), widgets are poked, the saver / draft is inspected.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton

from core import cut_budget
from mira.shared.cut_draft import CutDraft
from mira.ui.pages.new_cut_dialog import _TemplateNameDialog
from mira.ui.shared.new_cut_dialog_adapter import NewCutDialog


def _totals(photos=80, videos=5, seps=8, video_ms=150_000):
    return cut_budget.ShowTotals(
        photo_count=photos, video_count=videos,
        separator_count=seps, video_ms_total=video_ms)


def _adapter(**over):
    kw = dict(
        existing_cuts=[("short_version", 60), ("family", 45)],
        exported_count=231,
        style_options=["macro", "wildlife"],
        music_categories=["happy", "calm"],
        pool_probe=lambda expr: 100,
        totals_probe=lambda expr, styles, tf: _totals(),
        event_label="Costa Rica 2026",
    )
    kw.update(over)
    return NewCutDialog(**kw)


# ─────────────────────────────────────────────────────────────────────────────
# Live counts — the match label tracks the totals probe across filter changes
# ─────────────────────────────────────────────────────────────────────────────


def test_pool_size_reads_live_from_pool_probe(qapp):
    """The pool summary surfaces ``pool_probe``'s number, not the
    multiplied-out static count (fabricated numbers killed)."""
    seen: list[list] = []

    def probe(expr):
        seen.append(list(expr))
        return 42

    adapter = _adapter(pool_probe=probe)
    adapter._build()
    dlg = adapter._dlg
    # First paint already called the probe with the default ``#exported``
    # term. The summary picks up the probe's answer, not the 231 declared
    # for ``#exported`` in the ctx.
    assert seen, "pool_probe should be called on first paint"
    assert "42" in dlg._pool_summary.text()
    assert "231" not in dlg._pool_summary.text()


def test_match_count_reflects_filter_change(qapp):
    """Toggling a style chip / media checkbox re-runs ``totals_probe``
    AND the label reads the new total. Without the live re-bind the
    label held a fabricated, fixed number."""
    calls: list[tuple] = []

    def totals(expr, styles, tf):
        calls.append((list(styles), tf))
        # Style filter ON → 17 matches; OFF → 80 photos.
        if "macro" in styles:
            return _totals(photos=17, videos=0)
        if tf == "photo":
            return _totals(photos=70, videos=0)
        return _totals(photos=80, videos=5)

    adapter = _adapter(totals_probe=totals)
    adapter._build()
    dlg = adapter._dlg
    assert calls[-1] == ([], "both")
    assert "85 of" in dlg._match_label.text()        # 80 + 5

    # Style chip toggle → probe re-fires with that style and the label
    # picks up the new count.
    dlg._style_chips["macro"].setChecked(True)
    assert calls[-1] == (["macro"], "both")
    assert "17 of" in dlg._match_label.text()

    # Media-type toggle → tf goes from "both" to "photo".
    dlg._style_chips["macro"].setChecked(False)
    dlg._videos_cb.setChecked(False)
    assert calls[-1] == ([], "photo")
    assert "70 of" in dlg._match_label.text()


def test_pin_choice_drives_start_as(qapp):
    """Spec/81 §1 retired the live/pinned distinction (a Cut is always
    frozen). The 3-way choice in the dialog is now Pin choice — keep_all
    / weed_out / pick_in — and cut_info derives the legacy ``start_as``
    from it. keep_all & weed_out start all-in; pick_in starts all-out.
    ``live`` is no longer emitted."""
    adapter = _adapter()
    adapter._build()
    dlg = adapter._dlg

    def _pick(mode: str) -> None:
        for b in dlg._build_mode_group.buttons():
            if b.property("_key") == mode:
                b.setChecked(True)
                break

    # Default for a fresh Cut = keep_all → all-in.
    info = dlg.cut_info()
    assert info["build_mode"] == "keep_all"
    assert info["start_as"] == "all_picked"
    assert "live" not in info       # spec/81: live concept retired

    _pick("weed_out")
    info = dlg.cut_info()
    assert info["build_mode"] == "weed_out"
    assert info["start_as"] == "all_picked"   # starts full

    _pick("pick_in")
    info = dlg.cut_info()
    assert info["build_mode"] == "pick_in"
    assert info["start_as"] == "all_skipped"  # starts empty
    # The dialog no longer carries a live/pinned consequence label.
    assert not hasattr(dlg, "_mode_hint")


def test_match_count_no_media_type_reads_empty(qapp):
    """Both checkboxes off → no honest match count; the dialog says so
    instead of showing a stale number."""
    adapter = _adapter()
    adapter._build()
    dlg = adapter._dlg
    dlg._photos_cb.setChecked(False)
    dlg._videos_cb.setChecked(False)
    assert "photos, videos, or both" in dlg._match_label.text()


# ─────────────────────────────────────────────────────────────────────────────
# Templates — Load / Save round-trip through the adapter
# ─────────────────────────────────────────────────────────────────────────────


def _template(**over):
    kw = dict(
        name="best_macro_shots",
        pool_expr_json=json.dumps([["+", "exported"], ["-", "short_version"]]),
        style_filter_json=json.dumps(["macro"]),
        type_filter="photo", default_state="picked",
        target_s=300, max_s=420, photo_s=4.0,
        music_category="calm", card_style="single",
    )
    kw.update(over)
    return SimpleNamespace(**kw)


def test_save_button_disabled_without_host_saver(qapp):
    adapter = _adapter()
    adapter._build()
    assert not adapter._dlg._save_tpl_btn.isEnabled()


def test_load_button_disabled_without_templates(qapp):
    adapter = _adapter()
    adapter._build()
    assert not adapter._dlg._load_btn.isEnabled()


def test_save_template_forwards_a_cutdraft_to_the_host(qapp):
    """The host's ``template_saver`` signature is ``(name, CutDraft)`` —
    the dialog speaks its own ``cut_info()`` dict; the adapter translates
    BEFORE the saver fires so the host store stays unchanged. Spec/81:
    CutDraft now carries ``expr`` / ``styles`` / ``media_type`` / ``pin_mode``;
    the base operand ``"exported"`` stays bare, a tag becomes a typed
    ``{"kind":"cut",...}`` ref."""
    saved: list[tuple[str, CutDraft]] = []
    adapter = _adapter(
        template_saver=lambda name, draft: saved.append((name, draft)))
    adapter._build()
    dlg = adapter._dlg
    dlg._name_edit.setText("Family 2026")
    dlg._style_chips["macro"].setChecked(True)
    dlg._videos_cb.setChecked(False)
    # Skip the name dialog — call the underlying saver path directly.
    dlg._template_saver("Family 2026", dlg.cut_info())
    assert len(saved) == 1
    name, draft = saved[0]
    assert name == "Family 2026"
    assert isinstance(draft, CutDraft)
    assert draft.tag == "family_2026"
    assert draft.styles == ("macro",)
    assert draft.media_type == "photo"
    # The pool composition stays intact through the translation: the
    # default +#exported term comes through as a bare base token.
    assert ("+", "exported") in draft.expr


def test_load_template_repopulates_every_field(qapp):
    """Picking a template from the Load menu populates name, pool,
    styles, type, default-state, target/max/per-photo, music, cards."""
    adapter = _adapter(templates=[_template()])
    adapter._build()
    dlg = adapter._dlg
    assert dlg._load_btn.isEnabled()
    dlg._apply_template(_template())
    assert dlg._name_edit.text() == "best_macro_shots"
    # Pool: + exported, - short_version → counts dict carries both
    assert dlg._pool_counts == {"#exported": 1, "#short_version": -1}
    assert dlg._style_chips["macro"].isChecked()
    assert not dlg._style_chips["wildlife"].isChecked()
    assert dlg._photos_cb.isChecked() and not dlg._videos_cb.isChecked()
    # default_state="picked" → weed_out pin choice (spec/81 §4: start
    # all-in, frozen on commit), which derives start_as=all_picked in
    # cut_info. ``live`` is no longer emitted (spec/81 §1).
    sel_mode = next(
        b for b in dlg._build_mode_group.buttons() if b.isChecked())
    assert sel_mode.property("_key") == "weed_out"
    assert dlg.cut_info()["start_as"] == "all_picked"
    assert "live" not in dlg.cut_info()
    # times: 300 s → 5 min, 420 s → 7 min, photo_s 4.0
    info = dlg.cut_info()
    assert info["target_minutes"] == 5
    assert info["max_minutes"] == 7
    assert info["per_photo_seconds"] == 4.0
    assert info["music"] == "calm"
    sel_slide = next(b for b in dlg._slide_group.buttons() if b.isChecked())
    assert sel_slide.property("_key") == "one_random"   # "single" card


def test_save_then_load_round_trips_through_adapter(qapp):
    """End-to-end: configure the dialog → save a template → reopen with
    that template → load it → the recipe fields all come back. The
    template name lands as the cut name on Load (spec/61 §2: the recipe
    is replayable — the saved name is the cut name unless the user
    edits it)."""
    saved_templates: list = []

    def saver(name: str, draft: CutDraft) -> None:
        # Mirror the host store: persist a JSON-encoded template the
        # adapter / dialog can read back the same way the user_store
        # does. Spec/81: the new CutDraft carries ``expr`` / ``styles`` /
        # ``media_type`` / ``pin_mode``; legacy template-column key names
        # (pool_expr_json / style_filter_json / type_filter / default_state)
        # are kept since they are the user-store schema, not CutDraft fields.
        from mira.shared.cut_draft import PIN_KEEP_ALL, PIN_WEED_OUT
        default_state = ("picked" if draft.pin_mode in (PIN_KEEP_ALL, PIN_WEED_OUT)
                         else "skipped")
        saved_templates.append(SimpleNamespace(
            name=name,
            pool_expr_json=json.dumps([list(t) for t in draft.expr]),
            style_filter_json=json.dumps(list(draft.styles)),
            type_filter=draft.media_type,
            default_state=default_state,
            target_s=draft.target_s, max_s=draft.max_s,
            photo_s=draft.photo_s,
            music_category=draft.music_category,
            card_style=draft.card_style,
        ))

    # Configure the dialog with non-default values + save the template.
    adapter1 = _adapter(template_saver=saver)
    adapter1._build()
    dlg1 = adapter1._dlg
    dlg1._name_edit.setText("family_best")
    dlg1._style_chips["wildlife"].setChecked(True)
    dlg1._videos_cb.setChecked(False)
    for b in dlg1._build_mode_group.buttons():
        if b.property("_key") == "weed_out":
            b.setChecked(True)
    for b in dlg1._slide_group.buttons():
        if b.property("_key") == "per_day":
            b.setChecked(True)
    info_before = dlg1.cut_info()
    dlg1._template_saver(info_before["name"], info_before)
    assert len(saved_templates) == 1

    # Reopen — empty dialog with the saved template available; apply it.
    adapter2 = _adapter(templates=saved_templates)
    adapter2._build()
    dlg2 = adapter2._dlg
    dlg2._apply_template(saved_templates[0])
    info_after = dlg2.cut_info()
    # Every user-visible field round-trips.
    assert info_after["name"] == info_before["name"]
    assert info_after["styles"] == info_before["styles"]
    assert info_after["include_photos"] == info_before["include_photos"]
    assert info_after["include_videos"] == info_before["include_videos"]
    assert info_after["start_as"] == info_before["start_as"]
    assert info_after["slide_cards"] == info_before["slide_cards"]
    assert info_after["target_minutes"] == info_before["target_minutes"]
    assert info_after["max_minutes"] == info_before["max_minutes"]
    assert info_after["per_photo_seconds"] == info_before["per_photo_seconds"]
    assert info_after["music"] == info_before["music"]


def test_template_name_dialog_gates_on_text(qapp):
    """The save-as-template name modal won't enable Save while the
    field is blank — same gating the legacy dialog had."""
    dlg = _TemplateNameDialog(default="")
    assert not dlg._ok.isEnabled()
    dlg._edit.setText("  My recipe  ")
    assert dlg._ok.isEnabled()
    assert dlg.template_name() == "My recipe"


# ─────────────────────────────────────────────────────────────────────────────
# QSS — no inline setStyleSheet residue in the dialog body
# ─────────────────────────────────────────────────────────────────────────────


def test_no_inline_stylesheets_on_pool_and_match_widgets(qapp):
    """Charter invariant: visual treatment lives in QSS, not in widget
    code. The pool summary, match label, formula tokens, and pool
    chips all carry their look via QSS roles — no inline sheets."""
    adapter = _adapter()
    adapter._build()
    dlg = adapter._dlg
    # Pool widgets — names below were assigned in the redesign cleanup.
    assert dlg._pool_summary.styleSheet() == ""
    assert dlg._pool_summary.objectName() == "PoolSummary"
    assert dlg._match_label.styleSheet() == ""
    assert dlg._match_label.objectName() == "MatchCount"
    # Pool chip buttons — the +/- steppers ride the QSS role too.
    for btn in dlg.findChildren(QPushButton):
        if btn.objectName() == "PoolStepperBtn":
            assert btn.styleSheet() == ""


# ─────────────────────────────────────────────────────────────────────────────
# Spec/81 §2 — DCs alongside Cuts in the add row
# ─────────────────────────────────────────────────────────────────────────────


def test_available_pools_lists_base_then_dcs_then_cuts(qapp):
    """Spec/81 §2 — the add row offers base, then DCs (event recipes),
    then Cuts (frozen). Order is stable so the user sees the same
    layout on every reopen."""
    adapter = _adapter(
        existing_dcs=[
            ("dc1", "best_macro", 12),
            ("dc2", "best_wildlife", 9),
        ],
        existing_cuts=[("short_version", 60)],
        exported_count=231,
    )
    pools = adapter._ctx.available_pools                # noqa: SLF001
    assert [p.name for p in pools] == [
        "#exported", "#best_macro", "#best_wildlife", "#short_version",
    ]
    assert [p.kind for p in pools] == ["base", "dc", "dc", "cut"]
    assert pools[1].id == "dc1" and pools[2].id == "dc2"


def test_tag_collision_disambiguates_display(qapp):
    """Tag collisions across the DC/Cut namespaces (spec/81 §2 puts them
    in separate ones, so legal but rare) are surfaced with a ``— DC`` /
    ``— Cut`` suffix so the user can tell the chips apart."""
    adapter = _adapter(
        existing_dcs=[("dc1", "best", 5)],
        existing_cuts=[("best", 7)],
        exported_count=0,
    )
    names = [p.name for p in adapter._ctx.available_pools]   # noqa: SLF001
    assert names == ["#exported", "#best — DC", "#best — Cut"]


def test_pool_expr_emits_typed_refs_for_dc_and_cut(qapp):
    """``cut_info()['pool_expr']`` is the typed-ref operand encoding
    (spec/81 §2) — the base universe stays bare, DC + Cut chips carry
    kind + id + tag. Fixes the pre-spec/81 silent-empty bug where a
    bare 'short_version' looked like an unknown base token and the
    resolver returned an empty set."""
    adapter = _adapter(
        existing_dcs=[("dc-best", "best_macro", 12)],
        existing_cuts=[("short_version", 60)],
        exported_count=231,
    )
    adapter._build()                                        # noqa: SLF001
    dlg = adapter._dlg
    # Compose: +#exported + #best_macro - #short_version.
    dlg._pool_counts = {
        "#exported": 1, "#best_macro": 1, "#short_version": -1,
    }
    dlg._refresh_selected_chips()                           # noqa: SLF001
    info = dlg.cut_info()
    assert info["pool_expr"] == [
        ["+", "exported"],
        ["+", {"kind": "dc", "id": "dc-best", "tag": "best_macro"}],
        ["-", {"kind": "cut", "id": None, "tag": "short_version"}],
    ]


def test_pool_expr_falls_back_to_legacy_when_pool_option_missing(qapp):
    """A chip whose name doesn't match any :class:`PoolOption` (legacy
    template prefill that adds a chip the adapter never knew about)
    falls back to the pre-spec/81 'assume Cut' rule for stability."""
    adapter = _adapter()
    adapter._build()                                        # noqa: SLF001
    dlg = adapter._dlg
    dlg._pool_counts = {"#exported": 1, "#mystery": -1}
    dlg._refresh_selected_chips()                           # noqa: SLF001
    info = dlg.cut_info()
    # 'mystery' is treated as a Cut operand (legacy fallback).
    assert info["pool_expr"] == [
        ["+", "exported"],
        ["-", {"kind": "cut", "id": None, "tag": "mystery"}],
    ]


def test_draft_from_info_prefers_pool_expr_over_pool(qapp):
    """The adapter's ``draft()`` (the public seam :class:`CutSession`
    consumes) prefers ``pool_expr`` when present so the typed-ref
    operands survive into the :class:`CutDraft`."""
    adapter = _adapter(
        existing_dcs=[("dc-best", "best_macro", 12)],
        existing_cuts=[("short_version", 60)],
        exported_count=231,
    )
    adapter._build()                                        # noqa: SLF001
    dlg = adapter._dlg
    dlg._name_edit.setText("All-time best")
    dlg._pool_counts = {"#exported": 1, "#best_macro": 1}
    dlg._refresh_selected_chips()                           # noqa: SLF001
    draft = adapter.draft()
    assert isinstance(draft, CutDraft)
    assert draft.expr == (
        ("+", "exported"),
        ("+", {"kind": "dc", "id": "dc-best", "tag": "best_macro"}),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ∩ (intersect) — spec/81 §2 third operator
# ─────────────────────────────────────────────────────────────────────────────


def test_intersect_toggle_emits_amp_operator_in_expr(qapp):
    """Clicking ∩ on a chip puts the operand into the formula with the
    ``&`` operator (display ``∩``). Order: signed operands first, then
    intersects — so the formula reads "union/diff first, then narrow"."""
    adapter = _adapter(
        existing_dcs=[("dc-w", "wildlife", 50)],
        existing_cuts=[],
        exported_count=231,
    )
    adapter._build()                                        # noqa: SLF001
    dlg = adapter._dlg
    # User clicks ∩ on the wildlife DC.
    dlg._toggle_intersect("#wildlife")                      # noqa: SLF001
    info = dlg.cut_info()
    # The expr now has: + #exported (default), ∩ #wildlife.
    assert info["pool_expr"] == [
        ["+", "exported"],
        ["&", {"kind": "dc", "id": "dc-w", "tag": "wildlife"}],
    ]


def test_intersect_clears_signed_count_and_vice_versa(qapp):
    """Mutually exclusive: clicking ∩ on a chip that already has a
    signed multiplier replaces the multiplier; clicking +/− on a chip
    in the intersect set removes it from intersect."""
    adapter = _adapter(
        existing_cuts=[("family", 12)],
        exported_count=231,
    )
    adapter._build()                                        # noqa: SLF001
    dlg = adapter._dlg
    dlg._pool_counts = {"#family": 2}
    # ∩ replaces +×2
    dlg._toggle_intersect("#family")                        # noqa: SLF001
    assert "#family" not in dlg._pool_counts                # noqa: SLF001
    assert "#family" in dlg._pool_intersect                 # noqa: SLF001
    # + replaces ∩
    dlg._step_pool("#family", +1)                           # noqa: SLF001
    assert "#family" not in dlg._pool_intersect             # noqa: SLF001
    assert dlg._pool_counts["#family"] == 1                 # noqa: SLF001


def test_intersect_toggles_off_on_second_click(qapp):
    """A second ∩ click removes the operand from intersect (no
    contribution to the formula)."""
    adapter = _adapter(existing_cuts=[("family", 12)], exported_count=231)
    adapter._build()                                        # noqa: SLF001
    dlg = adapter._dlg
    dlg._toggle_intersect("#family")                        # noqa: SLF001
    assert "#family" in dlg._pool_intersect                 # noqa: SLF001
    dlg._toggle_intersect("#family")                        # noqa: SLF001
    assert "#family" not in dlg._pool_intersect             # noqa: SLF001
    info = dlg.cut_info()
    # Only the default #exported remains.
    assert info["pool_expr"] == [["+", "exported"]]


def test_intersect_prefill_round_trips_through_template(qapp):
    """A saved expr with ``∩`` operands prefills the intersect set on
    edit/reopen so the formula display + emit are stable across save
    → load."""
    from types import SimpleNamespace
    import json
    prefill = SimpleNamespace(
        name="Wildlife-best",
        pool_expr_json=json.dumps([
            ["+", "exported"],
            ["&", {"kind": "dc", "id": "dc-w", "tag": "wildlife"}],
        ]),
    )
    adapter = _adapter(
        existing_dcs=[("dc-w", "wildlife", 50)],
        prefill=prefill,
    )
    adapter._build()                                        # noqa: SLF001
    dlg = adapter._dlg
    assert "#wildlife" in dlg._pool_intersect               # noqa: SLF001
    # And the round trip preserves the operator.
    info = dlg.cut_info()
    assert ["&", {"kind": "dc", "id": "dc-w", "tag": "wildlife"}] \
        in info["pool_expr"]
