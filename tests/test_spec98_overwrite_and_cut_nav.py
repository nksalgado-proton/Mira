"""spec/98 — overwrite on name collision (Recipe / Collection / Cut)
+ Cut session day navigation.

Three Replace flows + one navigation flow, kept light: stub the host
seams (store / creator / gateway) and verify the spec-prescribed
outcome. Pre-existing surfaces still own their own end-to-end tests."""
from __future__ import annotations

import itertools
import json

import pytest
from PyQt6.QtWidgets import QDialog, QMessageBox

from mira.shared.recipe_store import (
    FLAVOUR_CUT,
    RecipeNameTakenError,
    RecipeStore,
)
from mira.user_store.repo import UserStore
from mira.ui.pages import new_recipe_dialog as nrd_mod
from mira.ui.pages.new_cut_dialog import (
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    JOIN_OR,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
    _SaveAsDcNameDialog,
    _SaveRecipeNameDialog,
)


NOW = "2026-06-22T12:00:00+00:00"


# ──────────────────────────── Recipe overwrite ─────────────────────────
#
# stub a RecipeStore whose ``create`` raises ``RecipeNameTakenError`` and
# whose ``by_name`` returns a known Recipe; verify the dialog runs the
# Replace path → ``update`` called + ``recipe_saved`` emitted.


def _ctx() -> NewRecipeContext:
    return NewRecipeContext(
        event_name="Spec98",
        available_pools=[OperandOption(
            name="#exported", count=10, kind="base", tag="exported")],
        available_styles=["macro"],
        selected_source=[(JOIN_OR, OperandOption(
            name="#exported", count=10, kind="base", tag="exported"))],
    )


class _RecipeStoreStub:
    """Just enough RecipeStore surface to drive the dialog's save loop."""

    def __init__(self, *, raise_on_create=True, by_name_result=None):
        from mira.user_store import models as um
        self._raise_on_create = raise_on_create
        self._by_name_result = by_name_result
        self.create_calls: list = []
        self.update_calls: list = []
        self.by_name_calls: list = []
        self._um = um

    def create(self, *, name, flavour, composition):
        self.create_calls.append((name, flavour, composition))
        if self._raise_on_create:
            raise RecipeNameTakenError(flavour, name)
        return self._um.Recipe(
            id="new", name=name, flavour=flavour,
            composition_json=json.dumps(composition),
            created_at=NOW, updated_at=NOW)

    def by_name(self, flavour, name):
        self.by_name_calls.append((flavour, name))
        return self._by_name_result

    def update(self, id, *, name=None, composition=None):
        self.update_calls.append({
            "id": id, "name": name, "composition": composition})
        return self._um.Recipe(
            id=id, name="short", flavour=FLAVOUR_CUT,
            composition_json=json.dumps(composition or {}),
            created_at=NOW, updated_at=NOW)

    def list(self, *args, **kwargs):                         # picker hook
        return []


def _existing_recipe() -> "any":
    from mira.user_store import models as um
    return um.Recipe(
        id="ex1", name="short", flavour=FLAVOUR_CUT,
        composition_json="{}", created_at=NOW, updated_at=NOW)


def _open_save_dialog(qapp, store):
    """Build NewCutDialog with the stub store + drive
    _on_save_recipe_clicked by patching the inner naming dialog exec."""
    dlg = NewCutDialog(
        flavour=FLAVOUR_CUT,
        show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=_ctx(),
        recipe_store=store,
    )
    dlg._name_edit.setText("short")
    return dlg


def test_recipe_replace_calls_update_and_emits_saved(qapp, monkeypatch):
    """spec/98 §1 — Replace branch: ``by_name`` resolves the existing
    Recipe; ``update`` is called with the dialog's composition;
    ``recipe_saved`` fires with the updated row."""
    store = _RecipeStoreStub(by_name_result=_existing_recipe())
    dlg = _open_save_dialog(qapp, store)

    # The naming sub-dialog returns Accepted with "short" (the default
    # the main dialog seeds from _name_edit).
    monkeypatch.setattr(_SaveRecipeNameDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    # The confirm helper returns True (user clicked Replace).
    monkeypatch.setattr(nrd_mod, "confirm",
                        lambda *args, **kwargs: True)
    # _toast pops a QMessageBox(self).exec() — short-circuit it.
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    saved: list = []
    dlg.recipe_saved.connect(saved.append)

    dlg._on_save_recipe_clicked()

    assert len(store.create_calls) == 1                 # one create attempt
    assert len(store.by_name_calls) == 1                # lookup happened
    assert len(store.update_calls) == 1                 # then update
    assert store.update_calls[0]["id"] == "ex1"
    assert store.update_calls[0]["composition"] is not None
    assert len(saved) == 1                              # recipe_saved fired


def test_recipe_replace_cancel_does_not_update(qapp, monkeypatch):
    """spec/98 §1 — Cancel branch: no ``update`` call; the loop falls
    through to the legacy 'Pick another' inline message and the sub-
    dialog stays open for a retry."""
    store = _RecipeStoreStub(by_name_result=_existing_recipe())
    dlg = _open_save_dialog(qapp, store)

    exec_calls = {"n": 0}

    def _alt_exec(self):
        exec_calls["n"] += 1
        # First iter: Accepted (user picked the name); second iter:
        # Rejected (user closed the sub-dialog after the inline error).
        return (QDialog.DialogCode.Accepted if exec_calls["n"] == 1
                else QDialog.DialogCode.Rejected)
    monkeypatch.setattr(_SaveRecipeNameDialog, "exec", _alt_exec)
    monkeypatch.setattr(nrd_mod, "confirm",
                        lambda *args, **kwargs: False)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    saved: list = []
    dlg.recipe_saved.connect(saved.append)

    dlg._on_save_recipe_clicked()

    assert len(store.update_calls) == 0
    assert saved == []
    assert exec_calls["n"] >= 2                         # loop iterated


def test_recipe_replace_falls_back_when_by_name_misses(qapp, monkeypatch):
    """spec/98 §1 — defensive: ``by_name`` returns None (slug freed
    between create and lookup; never realistic but harmless). No
    confirm prompt, no update, inline 'Pick another' fallback."""
    store = _RecipeStoreStub(by_name_result=None)
    dlg = _open_save_dialog(qapp, store)

    exec_calls = {"n": 0}

    def _alt_exec(self):
        exec_calls["n"] += 1
        return (QDialog.DialogCode.Accepted if exec_calls["n"] == 1
                else QDialog.DialogCode.Rejected)
    monkeypatch.setattr(_SaveRecipeNameDialog, "exec", _alt_exec)

    confirm_calls: list = []
    monkeypatch.setattr(
        nrd_mod, "confirm",
        lambda *a, **kw: confirm_calls.append(True) or True)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    dlg._on_save_recipe_clicked()

    assert confirm_calls == []                          # NEVER prompted
    assert store.update_calls == []                     # no update


# ──────────────────────────── Collection overwrite ─────────────────────


def _open_dc_dialog(qapp, *, dc_creator, dc_replacer=None):
    """Build a Collection NewCutDialog with a stub dc_creator (+
    optional dc_replacer) and return it ready for
    ``_open_save_as_dc_dialog``."""
    ctx = NewRecipeContext(
        event_name="Spec98",
        available_pools=[OperandOption(
            name="#exported", count=10, kind="base", tag="exported")],
        available_styles=["macro"],
        selected_source=[(JOIN_OR, OperandOption(
            name="#exported", count=10, kind="base", tag="exported"))],
    )
    return NewCutDialog(
        flavour="collection",
        show_scope=True, show_hardware=False,
        inventory_scope=INVENTORY_LIBRARY, ctx=ctx,
        dc_creator=dc_creator, dc_replacer=dc_replacer,
    )


def test_collection_replace_routes_to_dc_replacer(qapp, monkeypatch):
    """spec/98 §1 — Replace branch on the DC save: the host's
    ``dc_replacer`` is the one called (not ``dc_creator``)."""
    creator_calls: list = []
    replacer_calls: list = []

    def _dc_creator(name, expr, filters):
        creator_calls.append((name, expr, filters))
        raise ValueError("taken")

    def _dc_replacer(name, expr, filters):
        replacer_calls.append((name, expr, filters))
        return OperandOption(
            name=f"#{name}", count=0, kind="dc", tag=name, id="dc-ex")

    dlg = _open_dc_dialog(qapp, dc_creator=_dc_creator,
                          dc_replacer=_dc_replacer)

    monkeypatch.setattr(_SaveAsDcNameDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(_SaveAsDcNameDialog, "dc_name",
                        lambda self: "my_collection")
    monkeypatch.setattr(nrd_mod, "confirm",
                        lambda *a, **kw: True)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    dlg._open_save_as_dc_dialog([["+", "exported"]], {"styles": ["macro"]})

    assert len(creator_calls) == 1
    assert len(replacer_calls) == 1
    assert replacer_calls[0][0] == "my_collection"


def test_collection_replace_cancel_does_not_call_replacer(qapp, monkeypatch):
    """spec/98 §1 — Cancel branch: no ``dc_replacer`` call; the loop
    falls through to the legacy 'pick another' inline path."""
    replacer_calls: list = []

    def _dc_creator(name, expr, filters):
        raise ValueError("taken")

    def _dc_replacer(name, expr, filters):
        replacer_calls.append(name)
        return None

    dlg = _open_dc_dialog(qapp, dc_creator=_dc_creator,
                          dc_replacer=_dc_replacer)

    exec_calls = {"n": 0}

    def _alt_exec(self):
        exec_calls["n"] += 1
        return (QDialog.DialogCode.Accepted if exec_calls["n"] == 1
                else QDialog.DialogCode.Rejected)
    monkeypatch.setattr(_SaveAsDcNameDialog, "exec", _alt_exec)
    monkeypatch.setattr(_SaveAsDcNameDialog, "dc_name",
                        lambda self: "my_collection")
    monkeypatch.setattr(nrd_mod, "confirm",
                        lambda *a, **kw: False)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    dlg._open_save_as_dc_dialog([["+", "exported"]], {"styles": ["macro"]})

    assert replacer_calls == []


# ──────────────────────────── Cut overwrite ────────────────────────────


def test_cut_replace_adopts_existing_id_and_takes_update_branch(
        qapp, tmp_path, monkeypatch):
    """spec/98 §1 — name-taken commit: Replace adopts ``cut_by_tag(slug).id``
    onto the session and re-runs ``session.commit``, which takes the
    update branch (update_cut_settings + set_cut_members) instead of
    creating a second cut."""
    # Real event store + gateway + session — drive the actual commit
    # path so the adoption is end-to-end.
    from mira.gateway.event_gateway import EventGateway
    from mira.shared.cut_session import CutSession
    from mira.store.repo import EventStore
    from mira.ui.shared.cut_session_page import CutSessionPage
    from tests.test_cut_session import _draft
    from tests.test_gateway_cuts import _doc, _now

    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    gw = EventGateway(
        store, now=_now, new_id=lambda: f"id-{next(counter)}")

    # Create a Cut with the same slug the session will commit under so
    # the second create attempt hits "taken".
    existing = gw.create_cut("Pássaros 2026", target_s=600, max_s=720)

    session = CutSession.from_draft(gw, _draft())
    page = CutSessionPage(gw, session, event_root=tmp_path)

    # The Replace confirm returns True.
    import mira.ui.shared.cut_session_page as page_mod
    monkeypatch.setattr(page_mod, "confirm",
                        lambda *a, **kw: True)
    # The _on_create's terminal QMessageBox.exec would block — but on
    # the success path it doesn't fire. Safety stub anyway.
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    finished: list = []
    page.finished.connect(finished.append)

    page._on_create()

    # The session adopted the existing cut's id mid-commit.
    assert session.cut_id == existing.id
    # No second cut got created — the only Cuts in the gateway are the
    # fixture's pre-existing ``short_version`` (from ``_doc()``) and the
    # ``Pássaros 2026`` we pre-created above (now updated in place via
    # the Replace branch).
    tags = sorted(c.tag for c in gw.cuts())
    assert tags == ["passaros_2026", "short_version"]
    # finished fired with the adopted (now-updated) cut.
    assert len(finished) == 1
    assert finished[0].id == existing.id

    page.deleteLater()
    gw.close()


# ──────────────────────────── Cut session navigation ───────────────────


def test_cut_session_lands_on_day_list_when_multi_day(qapp, tmp_path):
    """spec/98 §2 — match the other phase surfaces: open on the day
    list. The two-day fixture has both day 1 + day 2 visible, so the
    stack starts at index 0 (days panel)."""
    from mira.gateway.event_gateway import EventGateway
    from mira.shared.cut_session import CutSession
    from mira.store.repo import EventStore
    from mira.ui.shared.cut_session_page import CutSessionPage
    from tests.test_cut_session import _draft
    from tests.test_gateway_cuts import _doc, _now

    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    gw = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    session = CutSession.from_draft(gw, _draft())
    assert len(session.days()) >= 2                     # sanity

    page = CutSessionPage(gw, session, event_root=tmp_path)
    assert page._stack.currentIndex() == 0              # day list

    page.deleteLater()
    gw.close()


def test_cut_session_grid_chrome_has_visible_back_button(qapp, tmp_path):
    """spec/98 §2 — the day-grid chrome carries a visible Back control
    wired to ``_back_to_days``; clicking it returns to the day list."""
    from mira.gateway.event_gateway import EventGateway
    from mira.shared.cut_session import CutSession
    from mira.store.repo import EventStore
    from mira.ui.shared.cut_session_page import CutSessionPage
    from tests.test_cut_session import _draft
    from tests.test_gateway_cuts import _doc, _now

    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    gw = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    session = CutSession.from_draft(gw, _draft())

    page = CutSessionPage(gw, session, event_root=tmp_path)
    assert hasattr(page, "_back_to_days_btn")
    assert "Back" in page._back_to_days_btn.text()

    # Drill into a day so the stack moves to the grid (index 1).
    page._open_day(0)
    assert page._stack.currentIndex() == 1

    # Click the visible back button → days panel.
    page._back_to_days_btn.click()
    assert page._stack.currentIndex() == 0

    page.deleteLater()
    gw.close()


def test_cut_session_single_day_skips_list(qapp, tmp_path, monkeypatch):
    """spec/98 §2 exception — single-day session opens straight in the
    grid; there's no list worth showing."""
    from mira.gateway.event_gateway import EventGateway
    from mira.shared.cut_session import CutSession
    from mira.store.repo import EventStore
    from mira.ui.shared.cut_session_page import CutSessionPage
    from tests.test_cut_session import _draft
    from tests.test_gateway_cuts import _doc, _now

    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    gw = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    session = CutSession.from_draft(gw, _draft())
    # Collapse to a single-day session for this case. ``days()`` is the
    # method CutSessionPage reads at construction, so override it on
    # the instance.
    one_day = session.days()[:1]
    session.days = lambda: one_day                      # type: ignore[assignment]

    page = CutSessionPage(gw, session, event_root=tmp_path)
    assert len(page._groups) == 1
    assert page._stack.currentIndex() == 1              # straight to grid

    page.deleteLater()
    gw.close()
