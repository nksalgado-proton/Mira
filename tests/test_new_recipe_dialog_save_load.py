"""spec/90 Phase 4e — :class:`NewCutDialog` Save / Load wiring.

* Save flow: the footer "Save as Recipe…" button opens the name dialog;
  on OK it calls :meth:`RecipeStore.create` with the current composition
  and the main dialog stays open.
* Name conflict: :class:`RecipeNameTakenError` keeps the naming dialog
  open with an inline error.
* Load flow: the header "Load Recipe…" button opens the picker; the
  picker lists Recipes of the dialog's flavour by default; selecting one
  re-populates the dialog state and kicks the probe.
* Cross-flavour banner: loading a Collection Recipe into the Cut dialog
  shows the spec/90 §5.5 banner above the metrics line.
"""
from __future__ import annotations

import json

import pytest

from mira.shared.recipe_store import (

    RecipeStore,
)
from mira.ui.pages.new_cut_dialog import (
    SCOPE_CROSS_EVENT,
    SCOPE_EVENT,
    INVENTORY_EVENT,
    JOIN_OR,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
    VERDICT_PICK,
    VERDICT_SKIP,
    _LoadRecipeDialog,
    _SaveRecipeNameDialog,
)
from mira.user_store.repo import UserStore


NOW = "2026-06-20T12:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def store(tmp_path) -> RecipeStore:
    """A fresh user-store backed RecipeStore."""
    us = UserStore.create(tmp_path / "mira.db", app_version="t", created_at=NOW)
    return RecipeStore(us)


def _ctx() -> NewRecipeContext:
    return NewRecipeContext(
        event_name="Costa Rica 2026",
        available_pools=[
            OperandOption(name="#exported", count=42, kind="base",
                          tag="exported"),
            OperandOption(name="#bests", count=8, kind="cut",
                          tag="bests", id="cut-b"),
        ],
        available_styles=["macro", "wildlife"],
        selected_source=[(JOIN_OR, OperandOption(
            name="#exported", count=42, kind="base", tag="exported"))],
    )


def _dialog(
    qapp,
    store: RecipeStore,
    *,
    scope: str = SCOPE_EVENT,
    show_hardware: bool = False,
    show_scope: bool = False,
    ctx: NewRecipeContext = None,
) -> NewCutDialog:
    return NewCutDialog(
        scope=scope,
        show_scope=show_scope,
        show_hardware=show_hardware,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx or _ctx(),
        recipe_store=store,
    )


# --------------------------------------------------------------------------- #
# Save flow
# --------------------------------------------------------------------------- #


def test_save_flow_writes_through_recipe_store(qapp, store):
    """A successful save writes one Recipe through the store with the
    dialog's current composition and flavour."""
    dlg = _dialog(qapp, store)
    dlg._name_edit.setText("short")
    dlg._otherwise = VERDICT_PICK
    composition = dlg.composition()
    assert composition["source"]                    # non-empty
    recipe = store.create(
        name="short", scope=SCOPE_EVENT, composition=composition)
    assert recipe.name == "short"
    assert recipe.flavour == "cut"
    assert json.loads(recipe.composition_json)["source"]


def test_save_button_enabled_when_store_wired_and_name_set(qapp, store):
    """spec/90 §5.5 — Save as Recipe (now on the "What to do?" band
    header) enables when a store is wired AND Source is non-empty AND
    Name is non-empty. The default ctx fills Source; typing a name flips
    the button enabled."""
    dlg = _dialog(qapp, store)
    # Initial: Source is non-empty (ctx seeds #exported) but Name is
    # blank, so the band button stays disabled.
    assert dlg._save_recipe_btn.isEnabled() is False
    dlg._name_edit.setText("short")
    assert dlg._save_recipe_btn.isEnabled() is True


def test_save_button_disabled_when_no_store(qapp):
    """No store wired (smokes / unit tests) → button stays disabled
    regardless of Source / Name."""
    dlg = NewCutDialog(
        scope=SCOPE_EVENT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=_ctx(),
    )
    dlg._name_edit.setText("anything")
    assert dlg._save_recipe_btn.isEnabled() is False


def test_save_button_disabled_when_source_empty(qapp, store):
    """spec/90 §5.5 + §1.1 — a Recipe with no Source is meaningless;
    even with a wired store and a typed Name, the button stays disabled
    until the user composes a Source."""
    empty_ctx = NewRecipeContext(
        event_name="Costa Rica 2026",
        available_pools=[OperandOption(
            name="#exported", count=42, kind="base", tag="exported")],
        available_styles=["macro"],
        # selected_source is left empty.
    )
    dlg = _dialog(qapp, store, ctx=empty_ctx)
    dlg._name_edit.setText("short")
    assert not dlg._source_chips
    assert dlg._save_recipe_btn.isEnabled() is False


def test_save_name_conflict_shows_inline_error(qapp, store):
    """A :class:`RecipeNameTakenError` keeps the name dialog open with
    an inline error message (the user retries without retyping)."""
    store.create(name="short", scope=SCOPE_EVENT, composition={})
    dlg = _SaveRecipeNameDialog(default="short", scope=SCOPE_EVENT)
    dlg.show_error("A Cut Recipe named 'short' already exists. Pick another.")
    # ``isHidden`` is the right check here — ``isVisible`` returns False
    # unless the widget tree is on-screen (it isn't in tests).
    assert not dlg._error.isHidden()
    assert "already exists" in dlg._error.text()
    # Typing clears the error so the user isn't told their in-progress
    # name is taken.
    dlg._edit.setText("short_v2")
    assert dlg._error.isHidden()


def test_save_name_dialog_defaults_to_current_name(qapp, store):
    """The naming dialog opens with the main dialog's Name field as
    the default."""
    dlg = _SaveRecipeNameDialog(default="trip_best", scope=SCOPE_EVENT)
    assert dlg.recipe_name() == "trip_best"


def test_save_name_dialog_gates_ok_on_text(qapp, store):
    """Empty input keeps OK disabled."""
    dlg = _SaveRecipeNameDialog(default="", scope=SCOPE_EVENT)
    assert not dlg._ok.isEnabled()
    dlg._edit.setText("  My Recipe  ")
    assert dlg._ok.isEnabled()
    assert dlg.recipe_name() == "My Recipe"


# --------------------------------------------------------------------------- #
# Load flow
# --------------------------------------------------------------------------- #


def test_load_button_enabled_when_store_wired(qapp, store):
    dlg = _dialog(qapp, store)
    assert dlg._load_btn.isEnabled() is True


def test_load_picker_lists_same_flavour_by_default(qapp, store):
    """The picker calls :meth:`RecipeStore.list` with the dialog's
    flavour; ``include_other=False`` filters out cross-flavour rows."""
    store.create(name="short", scope=SCOPE_EVENT, composition={})
    store.create(name="curated_macro", scope=SCOPE_CROSS_EVENT,
                 composition={})
    picker = _LoadRecipeDialog(
        recipes_for=lambda include_other: store.list(
            scope=SCOPE_EVENT, include_other=include_other),
        scope=SCOPE_EVENT,
    )
    rows = [picker._list.item(i).text() for i in range(picker._list.count())]
    assert any("short" in r for r in rows)
    assert not any("curated_macro" in r for r in rows)


@pytest.mark.skip(
    reason="spec/162 §6 — the Load Recipe picker now filters by the "
           "dialog's scope unconditionally; the legacy spec/90 §5.5 "
           "include_other cross-flavour toggle is orthogonal but no "
           "longer changes the pool at cross-scope. Test retires with "
           "the spec/90 include_other semantic.")
def test_load_picker_include_other_appends_cross_flavour(qapp, store):
    """Retired — see the skip reason above."""
    ...


def test_apply_recipe_populates_source_and_kicks_probe(qapp, store):
    """Loading a Recipe clears the current dialog state and seeds Source
    from the composition. ``_kick_probe`` runs (verified by checking
    the per-rule and Otherwise verdict updates)."""
    composition = {
        "source": [["+", "exported"], ["+", {"kind": "cut", "tag": "bests"}]],
        "rules": [{
            "predicate": [["+", {"kind": "cut", "tag": "bests"}]],
            "verdict": "pick",
        }],
        "otherwise": "skip",
        "filters": {"styles": ["macro"], "media_type": "photo"},
    }
    recipe = store.create(name="my_short", scope=SCOPE_EVENT,
                          composition=composition)
    dlg = _dialog(qapp, store)
    dlg._apply_recipe(recipe)
    # Source has both operands.
    names = [op.name for _join, op in dlg._source_chips]
    assert "#exported" in names
    assert any("bests" in n for n in names)
    # Rules + Otherwise.
    assert len(dlg._rules) == 1
    assert dlg._rules[0][1] == VERDICT_PICK
    assert dlg._otherwise == VERDICT_SKIP
    # Filters.
    assert dlg._style_chips["macro"].isChecked()
    assert dlg._photos_cb.isChecked()
    assert not dlg._videos_cb.isChecked()
    # Name field updated.
    assert dlg._name_edit.text() == "my_short"


def test_apply_collection_recipe_into_cut_dialog_shows_banner(qapp, store):
    """spec/90 §5.5 — a Collection Recipe loaded into the Cut dialog
    surfaces its hidden filters (Camera / Lens / Faces / Scope) as a
    banner above the metrics line."""
    composition = {
        "scope": [["+", {"kind": "event", "uuid": "evt-a"}]],
        "source": [["+", "exported"]],
        "filters": {"camera_ids": ["G9"], "person_ids": ["person-pedro"]},
        "otherwise": "skip",
    }
    recipe = store.create(name="curated_pedro",
                          scope=SCOPE_CROSS_EVENT,
                          composition=composition)
    dlg = _dialog(qapp, store, scope=SCOPE_EVENT)
    dlg._apply_recipe(recipe)
    assert dlg._cross_flavour_fields                # non-empty
    assert not dlg._metrics_banner.isHidden()
    text = dlg._metrics_banner.text()
    assert "Camera" in text
    assert "Faces" in text
    assert "Scope" in text


def test_apply_same_flavour_recipe_does_not_show_cross_flavour_banner(
        qapp, store):
    """A Cut Recipe loaded into the Cut dialog never carries hidden
    filters — no banner."""
    composition = {
        "source": [["+", "exported"]],
        "otherwise": "skip",
        "filters": {"styles": ["macro"], "media_type": "both"},
    }
    recipe = store.create(name="same", scope=SCOPE_EVENT,
                          composition=composition)
    dlg = _dialog(qapp, store, scope=SCOPE_EVENT)
    dlg._apply_recipe(recipe)
    assert dlg._cross_flavour_fields == []
