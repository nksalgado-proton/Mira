"""spec/61 slice 4 — the New Cut dialog.

Driven without ``exec()`` like the ExportDialog tests: construct with
injected data + probes, poke widgets, read :meth:`draft`. Pins the house
form grammar (titled FormFieldGroups, every interactive control hinted),
the live tag preview (transform + error states), the pool-builder
behavior, the filter→probe plumbing, the budget hint, and the
Start-button gating.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QComboBox, QGroupBox, QPushButton, QRadioButton

from core import cut_budget
from mira.ui.shared.new_cut_dialog import CutDraft, NewCutDialog


def _totals(photos=80, videos=5, seps=8, video_ms=150_000):
    return cut_budget.ShowTotals(
        photo_count=photos, video_count=videos,
        separator_count=seps, video_ms_total=video_ms)


def _dlg(**over):
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


# --------------------------------------------------------------------------- #
# Defaults + draft round-trip
# --------------------------------------------------------------------------- #


def test_default_draft_shape(qapp):
    dlg = _dlg()
    dlg._name_edit.setText("Best Macro Shots")
    d = dlg.draft()
    assert isinstance(d, CutDraft)
    assert d.tag == "best_macro_shots"
    assert d.pool_expr == (("+", "exported"),)     # the universe, pre-placed
    assert d.style_filter == ()                     # kickoff: default All
    assert d.type_filter == "both"
    assert d.default_state == "skipped"
    assert d.target_s == 600 and d.max_s == 720     # 10 / 12 min defaults
    assert d.photo_s == 6.0
    assert d.music_category is None                 # "(no music)" first entry


# --------------------------------------------------------------------------- #
# Live tag preview — the transform shown as the user types
# --------------------------------------------------------------------------- #


def test_tag_preview_states(qapp):
    dlg = _dlg()
    assert "type a name" in dlg._tag_preview.text()
    dlg._name_edit.setText("Pássaros do Pantanal")
    assert "#passaros_do_pantanal" in dlg._tag_preview.text()
    assert "available" in dlg._tag_preview.text()
    dlg._name_edit.setText("Short Version")         # case-blind collision
    assert "taken" in dlg._tag_preview.text()
    dlg._name_edit.setText("Exported")              # built-in live query
    assert "reserved" in dlg._tag_preview.text()


def test_start_gates_on_name_and_matches(qapp):
    dlg = _dlg()
    assert not dlg._start.isEnabled()               # no name yet
    dlg._name_edit.setText("ok name")
    assert dlg._start.isEnabled()
    dlg._name_edit.setText("short version")         # taken
    assert not dlg._start.isEnabled()
    # empty pool result also gates Start
    dlg2 = _dlg(totals_probe=lambda e, s, t: cut_budget.ShowTotals())
    dlg2._name_edit.setText("ok name")
    assert not dlg2._start.isEnabled()


# --------------------------------------------------------------------------- #
# Pool builder
# --------------------------------------------------------------------------- #


def test_pool_terms_append_and_remove(qapp):
    seen = []
    dlg = _dlg(pool_probe=lambda expr: seen.append(list(expr)) or 42)
    dlg._append_term("-", "short_version")
    dlg._append_term("+", "family")
    assert dlg._expr == [("+", "exported"), ("-", "short_version"), ("+", "family")]
    assert "42" in dlg._pool_count.text()
    assert seen[-1] == dlg._expr                    # probe sees the live expr
    dlg._remove_term(1)
    assert dlg._expr == [("+", "exported"), ("+", "family")]
    dlg._name_edit.setText("ok")
    assert dlg.draft().pool_expr == (("+", "exported"), ("+", "family"))


def test_add_row_offers_exported_plus_existing_cuts(qapp):
    dlg = _dlg()
    assert dlg._available_terms() == [
        ("exported", 231), ("short_version", 60), ("family", 45)]


# --------------------------------------------------------------------------- #
# Filters → probe plumbing
# --------------------------------------------------------------------------- #


def test_style_chips_and_type_boxes_reach_the_probe(qapp):
    calls = []
    dlg = _dlg(totals_probe=lambda e, styles, tf: calls.append((list(styles), tf)) or _totals())
    dlg._style_chips["macro"].setChecked(True)
    dlg._cb_videos.setChecked(False)
    assert calls[-1] == (["macro"], "photo")
    dlg._name_edit.setText("ok")
    d = dlg.draft()
    assert d.style_filter == ("macro",) and d.type_filter == "photo"


def test_unchecking_both_types_blocks_start(qapp):
    dlg = _dlg()
    dlg._name_edit.setText("ok")
    dlg._cb_photos.setChecked(False)
    dlg._cb_videos.setChecked(False)
    assert not dlg._start.isEnabled()
    assert "photos, videos, or both" in dlg._match_count.text()


# --------------------------------------------------------------------------- #
# Budget hint
# --------------------------------------------------------------------------- #


def test_budget_hint_photo_only_keep_rate(qapp):
    dlg = _dlg(totals_probe=lambda e, s, t: _totals(
        photos=500, videos=0, seps=8, video_ms=0))
    # 10 min at 6 s = 100 slots − 8 separators = 92; 500 photos → 1 in 5
    text = dlg._budget_hint.text()
    assert "92" in text and "1 in 5" in text and "8 day separators" in text


def test_budget_hint_mixed_pool_shows_separators_only(qapp):
    dlg = _dlg()                                    # videos in the pool
    text = dlg._budget_hint.text()
    assert "8 day separators" in text
    assert "keep" not in text


def test_separators_off_drops_them_from_the_hint(qapp):
    dlg = _dlg(separators_on=False, totals_probe=lambda e, s, t: _totals(
        photos=500, videos=0, seps=8, video_ms=0))
    text = dlg._budget_hint.text()
    assert "separators" not in text
    assert "100" in text                            # full 100 slots without them


# --------------------------------------------------------------------------- #
# Music + templates affordances
# --------------------------------------------------------------------------- #


def test_music_combo_lists_categories_with_none_first(qapp):
    dlg = _dlg()
    combo: QComboBox = dlg._music_combo
    assert combo.count() == 3 and combo.currentData() is None
    combo.setCurrentIndex(1)
    dlg._name_edit.setText("ok")
    assert dlg.draft().music_category == "happy"


def test_music_disabled_without_library(qapp):
    dlg = _dlg(music_categories=[])
    assert not dlg._music_combo.isEnabled()


def _template(**over):
    from types import SimpleNamespace
    kw = dict(
        name="best_macro_shots",
        pool_expr_json='[["+", "exported"], ["-", "short_version"]]',
        style_filter_json='["macro"]',
        type_filter="photo", default_state="picked",
        target_s=300, max_s=420, photo_s=4.0, music_category="calm")
    kw.update(over)
    return SimpleNamespace(**kw)


def test_load_template_disabled_until_templates_exist(qapp):
    dlg = _dlg(templates=[])
    assert not dlg._load_btn.isEnabled() and dlg._load_btn.toolTip()
    dlg2 = _dlg(templates=[_template()])
    assert dlg2._load_btn.isEnabled()


def test_apply_template_prefills_every_field(qapp):
    dlg = _dlg(templates=[_template()])
    dlg._apply_template(_template())
    assert dlg._name_edit.text() == "best_macro_shots"
    assert dlg._expr == [("+", "exported"), ("-", "short_version")]
    assert dlg._style_chips["macro"].isChecked()
    assert not dlg._style_chips["wildlife"].isChecked()
    assert dlg._cb_photos.isChecked() and not dlg._cb_videos.isChecked()
    assert dlg._rb_picked.isChecked()
    assert dlg._target_spin.value() == 5 and dlg._max_spin.value() == 7
    assert dlg._photo_spin.value() == 4.0
    assert dlg._music_combo.currentData() == "calm"
    # the prefilled name collides with nothing in this fixture → valid draft
    d = dlg.draft()
    assert d.tag == "best_macro_shots" and d.music_category == "calm"


def test_save_template_button_gated_on_saver(qapp):
    dlg = _dlg()
    assert not dlg._save_tpl_btn.isEnabled()
    saved = []
    dlg2 = _dlg(template_saver=lambda name, draft: saved.append((name, draft)))
    assert dlg2._save_tpl_btn.isEnabled()
    dlg2._name_edit.setText("Família")
    dlg2._template_saver("Família", dlg2.draft())
    assert saved and saved[0][0] == "Família"
    assert saved[0][1].tag == "familia"


def test_template_name_dialog_gates_on_text(qapp):
    from mira.ui.shared.new_cut_dialog import _TemplateNameDialog
    dlg = _TemplateNameDialog(default="")
    assert not dlg._ok.isEnabled()
    dlg._edit.setText("  My recipe  ")
    assert dlg._ok.isEnabled()
    assert dlg.template_name() == "My recipe"


# --------------------------------------------------------------------------- #
# Form grammar — the house rules
# --------------------------------------------------------------------------- #


def test_form_grammar_titled_groups_and_hints(qapp):
    """Nelson 2026-06-12 eyeball ruling: NEVER label-beside-input — every
    input lives in its own titled FormFieldGroup (Time split into Target
    time / Max time / Per photo; Filters split into Style / Media type)."""
    dlg = _dlg()
    groups = dlg.findChildren(QGroupBox)
    assert len(groups) == 10
    assert all(g.objectName() == "FormFieldGroup" for g in groups)
    assert {g.title() for g in groups} == {
        "Name", "Pool", "Style", "Media type", "Slide cards", "Start as",
        "Target time", "Max time", "Per photo", "Music"}
    for rb in dlg.findChildren(QRadioButton):
        assert rb.toolTip(), rb.text()
    for cb in dlg.findChildren(QCheckBox):
        assert cb.toolTip(), cb.text()
    for btn in dlg.findChildren(QPushButton):
        assert btn.toolTip(), (btn.objectName(), btn.text())
    assert dlg._name_edit.toolTip() and dlg._target_spin.toolTip()
    assert dlg._max_spin.toolTip() and dlg._photo_spin.toolTip()
    assert dlg._music_combo.toolTip()


def test_accept_snapshots_draft(qapp):
    dlg = _dlg()
    dlg._name_edit.setText("Família 2026")
    dlg._on_accept()
    assert dlg._snapshot is not None
    assert dlg.draft().tag == "familia_2026"


def test_card_style_radios_default_black_and_travel(qapp):
    dlg = _dlg()
    dlg._name_edit.setText("ok")
    assert dlg.draft().card_style == "black"
    dlg._card_radios["multi"].setChecked(True)
    assert dlg.draft().card_style == "multi"
    for rb in dlg._card_radios.values():
        assert rb.toolTip()


def test_prefill_edit_mode(qapp):
    """The dialog-first Adjust flow (Nelson round 3): every field —
    including the card style — pre-fills from the cut's recipe."""
    from types import SimpleNamespace
    prefill = SimpleNamespace(
        name="short_version",
        pool_expr_json='[["+", "exported"]]',
        style_filter_json='["wildlife"]',
        type_filter="both", default_state="picked",
        target_s=120, max_s=180, photo_s=5.0,
        music_category="calm", card_style="single")
    dlg = _dlg(heading_text="Edit Cut", prefill=prefill,
               existing_cuts=[("family", 45)])   # self excluded by caller
    assert dlg.windowTitle() == "Edit Cut"
    assert dlg._name_edit.text() == "short_version"
    assert "available" in dlg._tag_preview.text()
    assert dlg._card_radios["single"].isChecked()
    d = dlg.draft()
    assert d.tag == "short_version" and d.card_style == "single"
    assert d.target_s == 120 and d.music_category == "calm"
