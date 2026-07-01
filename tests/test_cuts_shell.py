"""spec/61 slice 6 + spec/70 Phase 3 §5 — Surface 09 ShareCutsPage chassis
(Share landing) + audio-library feeds.

The chassis is driven with a duck-typed app gateway over the real
event.db fixture: rows render from live gateway reads, the New Cut
dialog kwargs wire the real probes, sessions mount/unmount in the
stack, and Back closes the per-event gateway.

History: pre-spec/70 these tests pointed at ``mira.ui.shared.cuts_shell``
when the chassis was a separate host wrapping the redesigned list. The
route swap (Phase 3 §5) folded the chassis into the redesigned page; the
class names map ``CutsShellPage`` → :class:`ShareCutsPage`. The test
file name stays so the test history is continuous.
"""
from __future__ import annotations

import itertools
import random

import pytest

from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy

from core import audio_library
from mira.gateway.event_gateway import EventGateway
from mira.settings.model import Settings
from mira.shared.cut_session import CutSession
from mira.store.repo import EventStore
from mira.ui.pages.share_cuts_page import (
    CutRow,
    CutSnapshot,
    ShareCutsPage,
    _ExportTargetDialog,
    _RenameCutDialog,
)

from tests.test_cut_session import _draft
from tests.test_gateway_cuts import _doc, _now


class _FakeAppGateway:
    """Duck-type of the app Gateway: settings + open_event."""

    def __init__(self, eg, settings: Settings) -> None:
        self._eg = eg
        self.settings = settings

    def open_event(self, event_id: str):
        return self._eg


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    # Materialise the on-disk bytes for every Exported Media/ lineage row so
    # the rescan prune (filesystem is the source of truth for the exported
    # tier) keeps them on Share entry instead of reconciling the pool to empty.
    for (rel,) in store.conn.execute(
            "SELECT export_relpath FROM lineage "
            "WHERE export_relpath LIKE 'Exported Media/%'").fetchall():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\xff\xd8\xff\xd9")
    counter = itertools.count(1)
    g = EventGateway(store, event_root=tmp_path, now=_now,
                     new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


def _shell(gw, **settings_over) -> ShareCutsPage:
    settings = Settings(**settings_over)
    shell = ShareCutsPage(_FakeAppGateway(gw, settings))
    assert shell.open_event("evt-c")
    return shell


# --------------------------------------------------------------------------- #
# The list — Surface 09 redesign: probes the ShareCutsPage snapshots
# --------------------------------------------------------------------------- #


def test_open_event_builds_exported_plus_cut_snapshots(qapp, gw, tmp_path):
    shell = _shell(gw)
    # The shell feeds the redesigned list page via setForPreview; assert
    # the snapshots carry the right shape.
    pool = shell.list_page._pool          # noqa: SLF001 — test introspection
    cuts = shell.list_page._cuts          # noqa: SLF001
    assert pool.exported_count == 5
    assert len(cuts) == 1
    cut = cuts[0]
    assert cut.name == "short_version"
    assert cut.item_count == 1
    # 1 photo + 1 separator × 6 s = 12 s
    assert cut.duration_seconds == 12
    # `created_at` is None for never-exported in the fake gateway
    assert cut.exported_date == ""


def test_no_user_cuts_yields_empty_cuts_list(qapp, gw, tmp_path):
    shell = _shell(gw)
    gw.delete_cut("cut-s")
    shell.refresh()
    assert shell.list_page._cuts == []     # noqa: SLF001
    assert shell.list_page._pool.exported_count == 5   # noqa: SLF001


def test_separators_setting_off_changes_duration(qapp, gw, tmp_path):
    shell = _shell(gw, use_separators=False)
    cut = shell.list_page._cuts[0]         # noqa: SLF001
    # 1 photo only, no separator card -> 6 s
    assert cut.duration_seconds == 6


def test_cut_row_is_fixed_height_and_list_scrolls(qapp, gw):
    """spec/61 §3 + Nelson 2026-06-15: rows are a fixed height so the
    list scrolls when it overflows — without this the rows balloon to
    fill the viewport and there is no scrolling. The cuts layout sits
    inside a ``QScrollArea`` and is the cuts-list's load-bearing seam."""
    row = CutRow(CutSnapshot(cut_id="c1", name="anything", item_count=3))
    # Fixed height = setFixedHeight collapses min/max to the same value;
    # vertical size policy refuses to grow beyond sizeHint.
    assert row.minimumHeight() == CutRow.ROW_HEIGHT
    assert row.maximumHeight() == CutRow.ROW_HEIGHT
    assert row.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed

    shell = _shell(gw)
    list_view = shell.list_page                # noqa: SLF001
    # The cuts area is wrapped in a QScrollArea; without it overflow
    # would have nowhere to go.
    scrolls = list_view.findChildren(QScrollArea)
    assert any(
        s.widget() is not None
        and s.widget().layout() is list_view._cuts_layout   # noqa: SLF001
        for s in scrolls
    )


# --------------------------------------------------------------------------- #
# Dialog kwargs — the real probes
# --------------------------------------------------------------------------- #


def test_make_new_recipe_dialog_wires_recipe_store(qapp, gw, tmp_path):
    """spec/90 Phase 4e — the page's dialog factory builds the
    :class:`NewCutDialog` in its Cut-face configuration and wires
    every probe + the user-store-backed :class:`RecipeStore`. Without
    this the Save / Load Recipe buttons would stay disabled in
    production."""
    from mira.user_store.repo import UserStore
    us = UserStore.create(tmp_path / "mira.db", app_version="t",
                          created_at="2026-06-20T12:00:00+00:00")
    # Stand-in app gateway that exposes the user_store the page needs.
    class _G:
        def __init__(self, eg, settings, us):
            self._eg = eg
            self.settings = settings
            self.user_store = us
        def open_event(self, _id): return self._eg
    g = _G(gw, Settings(), us)
    shell = ShareCutsPage(g)
    assert shell.open_event("evt-c")
    kwargs = shell._dialog_kwargs()
    dlg = shell._make_new_recipe_dialog(kwargs)
    # Load enables on store wiring alone (no compositional preconditions
    # — the user is loading from outside this dialog's state).
    assert dlg._load_btn.isEnabled() is True
    # Save as Recipe also needs a non-empty Name + Source (spec/90 §5.5).
    # The page seeds Source with #exported; typing a name flips the
    # band button enabled.
    dlg._name_edit.setText("clean_exports")
    assert dlg._save_recipe_btn.isEnabled() is True
    # The Cut face hides Scope + hardware.
    from mira.ui.pages.new_cut_dialog import FLAVOUR_CUT
    assert dlg._flavour == FLAVOUR_CUT
    assert dlg._show_scope is False
    assert dlg._show_hardware is False
    dlg.deleteLater()
    us.close()


def test_dialog_kwargs_wire_gateway_feeds(qapp, gw, tmp_path):
    shell = _shell(gw)
    kw = shell._dialog_kwargs()
    assert kw["exported_count"] == 5
    assert kw["existing_cuts"] == [("short_version", 1)]
    assert kw["style_options"] == ["macro", "wildlife"]
    assert kw["event_label"] == "Cuts fixture"
    assert kw["pool_probe"]([("+", "exported")]) == 5
    totals = kw["totals_probe"]([("+", "exported")], [], "both")
    assert totals.photo_count == 4 and totals.video_count == 1
    # no audio path set → the pointer-to-Settings hint
    assert "Settings" in kw["music_hint"]


def test_settings_repo_is_loaded_not_attribute_read(qapp, gw, tmp_path):
    """Nelson eyeball 2026-06-12 finding #1: gateway.settings is the
    REPO — attribute reads silently defaulted (audio path always empty).
    The shell must load() it; a set path now reaches the dialog."""
    class _RepoDuck:
        def __init__(self, settings): self._s = settings
        def load(self): return self._s

    lib = tmp_path / "lib"
    (lib / "happy").mkdir(parents=True)
    (lib / "calm").mkdir()
    settings = Settings(audio_library_path=str(lib))
    shell = ShareCutsPage(_FakeAppGateway(gw, None))
    shell.gateway.settings = _RepoDuck(settings)
    assert shell.open_event("evt-c")
    kw = shell._dialog_kwargs()
    assert kw["music_categories"] == ["calm", "happy"]
    assert kw["music_hint"] is None
    # path set but NO category subfolders → the diagnostic hint
    empty = tmp_path / "empty_lib"
    empty.mkdir()
    shell.gateway.settings = _RepoDuck(Settings(audio_library_path=str(empty)))
    kw2 = shell._dialog_kwargs()
    assert kw2["music_categories"] == []
    assert str(empty) in kw2["music_hint"]


# --------------------------------------------------------------------------- #
# Session mount / unmount
# --------------------------------------------------------------------------- #


def test_session_flow_commits_and_returns_to_list(qapp, gw, tmp_path):
    shell = _shell(gw)
    session = CutSession.from_draft(gw, _draft())
    shell._start_session(session)
    assert shell._stack.currentWidget() is shell._session_page
    shell._session_page._session.set_state("Exported Media/e2.jpg", True)
    shell._session_page._on_create()
    assert shell._session_page is None
    assert shell._stack.currentWidget() is shell.list_page
    assert any(c.name == "passaros_2026" for c in shell.list_page._cuts)   # noqa: SLF001


def test_adjust_goes_dialog_first_then_session(qapp, gw, tmp_path):
    """Adjust (round 3) routes through the EDIT dialog — stubbed here
    (tests must never exec a real modal) — then the session seeds from
    membership and carries the dialog's draft."""
    shell = _shell(gw)
    seen = {}

    def fake_dialog(prefill, kwargs):
        seen["prefill"] = prefill
        seen["existing"] = kwargs["existing_cuts"]
        from tests.test_cut_session import _draft
        return _draft(name="short_version", tag="short_version",
                      target_s=240, card_style="single")

    shell._exec_edit_dialog = fake_dialog
    shell._on_adjust_cut("cut-s")
    # the dialog saw the cut's recipe, with itself excluded from "taken"
    assert seen["prefill"].name == "short_version"
    assert seen["existing"] == []
    page = shell._session_page
    assert page is not None and page._session.cut_id == "cut-s"
    assert page._session.is_picked("Exported Media/e1.jpg")
    assert page._session.target_s == 240            # the dialog's new value
    page._on_cancel()                               # no decisions → no confirm
    assert shell._session_page is None
    assert shell._stack.currentWidget() is shell.list_page


def test_adjust_prefill_with_real_budget_keeps_has_budget_true(qapp, gw,
                                                               tmp_path):
    """spec/90 §5.1 Bug 2 — Adjusting a Cut whose target_s/max_s carry
    real values opens the dialog with has_budget=True and spinners
    showing the right numbers."""
    from types import SimpleNamespace
    from mira.ui.pages.new_cut_dialog import (
        FLAVOUR_CUT,
        INVENTORY_EVENT,
        NewRecipeContext,
    )
    shell = _shell(gw)
    prefill = SimpleNamespace(
        name="short_version",
        pool_expr_json='[["+", "exported"]]',
        style_filter_json='[]',
        type_filter="both",
        default_state="skipped",
        target_s=300, max_s=600,
        photo_s=5.0,
        music_category=None, card_style="black",
    )
    ctx = shell._build_recipe_context(
        shell._dialog_kwargs(), prefill=prefill)
    assert ctx.has_budget is True
    assert ctx.target_minutes == 5             # 300 s / 60
    assert ctx.max_minutes == 10               # 600 s / 60
    assert ctx.per_photo_seconds == 5.0


def test_adjust_prefill_marks_context_as_editing(qapp, gw, tmp_path):
    """spec/90 Phase 4e edit note (Nelson 2026-06-20): the Adjust
    prefill flags ``ctx.is_editing=True`` so the dialog's Start gate
    relaxes — the user can save a metadata change (clear budget,
    rename, etc.) on a Cut whose source resolves to an empty pool."""
    from types import SimpleNamespace
    shell = _shell(gw)
    prefill = SimpleNamespace(
        name="short_version",
        pool_expr_json='[["+", "exported"]]',
        style_filter_json='[]',
        type_filter="both",
        default_state="skipped",
        target_s=300, max_s=600,
        photo_s=5.0,
        music_category=None, card_style="black",
    )
    ctx = shell._build_recipe_context(
        shell._dialog_kwargs(), prefill=prefill)
    assert ctx.is_editing is True


def test_new_cut_context_is_not_editing(qapp, gw, tmp_path):
    """No prefill → not Adjust → ctx.is_editing stays False."""
    shell = _shell(gw)
    ctx = shell._build_recipe_context(shell._dialog_kwargs(), prefill=None)
    assert ctx.is_editing is False


def test_adjust_prefill_with_null_budget_flips_has_budget_false(qapp, gw,
                                                                tmp_path):
    """spec/90 §5.1 Bug 2 — Adjusting a Cut with target_s=max_s=None
    opens the dialog with has_budget=False (checkbox unchecked, spinners
    greyed at the UI layer)."""
    from types import SimpleNamespace
    shell = _shell(gw)
    prefill = SimpleNamespace(
        name="no_budget",
        pool_expr_json='[["+", "exported"]]',
        style_filter_json='[]',
        type_filter="both",
        default_state="skipped",
        target_s=None, max_s=None,
        photo_s=6.0,
        music_category=None, card_style="black",
    )
    ctx = shell._build_recipe_context(
        shell._dialog_kwargs(), prefill=prefill)
    assert ctx.has_budget is False


def test_start_round_trips_budget_through_adapter(qapp, gw, tmp_path):
    """spec/90 §5.1 Bug 1 + Bug 2 end-to-end — clicking Start on a Cut
    with a budget produces a CutDraft whose target_s reflects the
    dialog's spinner value (via the presentation block)."""
    from types import SimpleNamespace
    from mira.shared.recipe_draft_adapter import recipe_to_cut_draft
    from mira.user_store import models as um
    import json as _json
    shell = _shell(gw)
    prefill = SimpleNamespace(
        name="short_version",
        pool_expr_json='[["+", "exported"]]',
        style_filter_json='[]',
        type_filter="both",
        default_state="skipped",
        target_s=300, max_s=600,
        photo_s=5.0,
        music_category=None, card_style="black",
    )
    dlg = shell._make_new_recipe_dialog(
        shell._dialog_kwargs(), prefill=prefill)
    comp = dlg.composition()
    assert comp["presentation"]["target_s"] == 300
    assert comp["presentation"]["max_s"] == 600
    recipe = um.Recipe(id="", name="short_version", flavour="cut",
                       composition_json=_json.dumps(comp),
                       created_at="", updated_at="")
    draft = recipe_to_cut_draft(recipe)
    assert draft.target_s == 300
    assert draft.max_s == 600
    assert draft.photo_s == 5.0
    dlg.deleteLater()


def test_start_round_trips_null_budget_through_adapter(qapp, gw, tmp_path):
    """A no-budget composition round-trips to draft.target_s=None
    (cut_session_page renders "no limit" from the resulting CutDraft)."""
    from types import SimpleNamespace
    from mira.shared.recipe_draft_adapter import recipe_to_cut_draft
    from mira.user_store import models as um
    import json as _json
    shell = _shell(gw)
    prefill = SimpleNamespace(
        name="no_budget_cut",
        pool_expr_json='[["+", "exported"]]',
        style_filter_json='[]',
        type_filter="both",
        default_state="skipped",
        target_s=None, max_s=None,
        photo_s=6.0,
        music_category=None, card_style="black",
    )
    dlg = shell._make_new_recipe_dialog(
        shell._dialog_kwargs(), prefill=prefill)
    comp = dlg.composition()
    assert comp["presentation"]["target_s"] is None
    assert comp["presentation"]["max_s"] is None
    recipe = um.Recipe(id="", name="x", flavour="cut",
                       composition_json=_json.dumps(comp),
                       created_at="", updated_at="")
    draft = recipe_to_cut_draft(recipe)
    assert draft.target_s is None
    assert draft.max_s is None
    dlg.deleteLater()


def test_back_emits_closed(qapp, gw, tmp_path):
    shell = _shell(gw)
    got = []
    shell.closed.connect(lambda: got.append(True))
    shell._on_back()
    assert got == [True]


def test_save_as_dc_creates_a_dc_and_refreshes(qapp, gw, tmp_path):
    """Spec/81 §2 polish (C.7): the New Cut dialog's "Save as DC…" button
    fires the host's dc_saver with the current cut_info; the host calls
    gateway.create_dc and refreshes the page so the DC appears in the
    DCs tab."""
    shell = _shell(gw)
    # Confirm there are no user DCs yet (only the empty list).
    assert shell.list_page._dcs == []                       # noqa: SLF001
    # Simulate the dialog's save by calling _save_dc directly with a
    # cut_info-shaped dict. Pool = +#exported -short_version, styles =
    # ["macro"], photos only.
    shell._save_dc("Best macros", {
        "pool": {"#exported": 1, "#short_version": -1},
        "styles": ["macro"],
        "include_photos": True,
        "include_videos": False,
    })
    # The gateway has the new DC.
    tags = [dc.tag for dc in gw.dynamic_collections()]
    assert "best_macros" in tags
    # The page's DC tab snapshot now carries the new DC.
    assert any(d.name == "best_macros" for d in shell.list_page._dcs)


def test_make_new_recipe_dialog_wires_dc_creator(qapp, gw, tmp_path):
    """spec/90 §5 — the page's dialog factory wires a dc_creator closure
    that calls the per-event gateway's ``create_dc`` and returns an
    :class:`OperandOption` ready to drop into the operand inventory.
    Calling it directly mirrors what the dialog's naming sub-dialog does
    on OK; the DC lands in the gateway and the page's DC inventory both."""
    from mira.user_store.repo import UserStore
    us = UserStore.create(tmp_path / "mira.db", app_version="t",
                          created_at="2026-06-20T12:00:00+00:00")

    class _G:
        def __init__(self, eg, settings, us):
            self._eg = eg
            self.settings = settings
            self.user_store = us

        def open_event(self, _id): return self._eg

    g = _G(gw, Settings(), us)
    shell = ShareCutsPage(g)
    assert shell.open_event("evt-c")
    kwargs = shell._dialog_kwargs()
    dlg = shell._make_new_recipe_dialog(kwargs)
    assert dlg._dc_creator is not None
    # Drive the closure the dialog's sub-dialog would call on OK.
    operand = dlg._dc_creator(
        "Clean exports",
        [["+", "exported"]],
        {"styles": [], "media_type": "both"},
    )
    # The DC is live in the gateway.
    tags = [dc.tag for dc in gw.dynamic_collections()]
    assert "clean_exports" in tags
    # The returned OperandOption carries the new DC's identity.
    assert operand.kind == "dc"
    assert operand.tag == "clean_exports"
    assert operand.id
    # The page refreshed; the DCs tab snapshot picks the new DC up.
    assert any(d.name == "clean_exports" for d in shell.list_page._dcs)
    dlg.deleteLater()
    us.close()


def test_dialog_kwargs_offers_existing_dcs(qapp, gw, tmp_path):
    """Spec/81 §2: the New Cut dialog's add row offers DCs as operands
    so a DC can be composed out of other DCs (all-time-best =
    best-macro + best-wildlife). Host passes existing_dcs alongside
    existing_cuts; each carries (id, tag, live_count) so the chip count
    reflects the live resolution."""
    shell = _shell(gw)
    # Save two DCs through the host seam so the gateway has rows.
    shell._save_dc("Best macros", {
        "pool": {"#exported": 1}, "styles": ["macro"],
        "include_photos": True, "include_videos": False,
    })
    shell._save_dc("Best wildlife", {
        "pool": {"#exported": 1}, "styles": [],
        "include_photos": True, "include_videos": True,
    })
    kwargs = shell._dialog_kwargs()                      # noqa: SLF001
    dcs = kwargs["existing_dcs"]
    tags = [tag for _id, tag, _n in dcs]
    assert "best_macros" in tags and "best_wildlife" in tags
    # Each DC carries an id (the resolver prefers id over tag — spec/81 §5).
    for dc_id, _tag, _n in dcs:
        assert dc_id and isinstance(dc_id, str)


def test_save_dc_composed_of_other_dcs_resolves_end_to_end(qapp, gw, tmp_path):
    """Spec/81 §2: composing a DC out of other DCs via the dialog's
    pool_expr produces a recipe the gateway resolves correctly. The
    enabler for all-time-best = best-macro + best-wildlife."""
    shell = _shell(gw)
    # First seed two source DCs.
    shell._save_dc("Best macros", {
        "pool": {"#exported": 1}, "styles": ["macro"],
        "include_photos": True, "include_videos": False,
    })
    shell._save_dc("Best wildlife", {
        "pool": {"#exported": 1}, "styles": [],
        "include_photos": True, "include_videos": True,
    })
    dc_macro = next(dc for dc in gw.dynamic_collections()
                    if dc.tag == "best_macros")
    dc_wild = next(dc for dc in gw.dynamic_collections()
                   if dc.tag == "best_wildlife")
    # Now compose the umbrella DC. The dialog would ship pool_expr with
    # typed-ref operands — same shape here.
    shell._save_dc("All-time best", {
        "pool_expr": [
            ["+", {"kind": "dc", "id": dc_macro.id, "tag": dc_macro.tag}],
            ["+", {"kind": "dc", "id": dc_wild.id, "tag": dc_wild.tag}],
        ],
        "pool": {},                          # ignored when pool_expr present
        "styles": [],
        "include_photos": True, "include_videos": True,
    })
    umbrella = next(dc for dc in gw.dynamic_collections()
                    if dc.tag == "all_time_best")
    # The umbrella DC's resolution is the union of its operand DCs — and
    # NON-empty so we know the resolver actually walked the nested DC
    # operands (the pre-spec/81 bare-string bug returned an empty set).
    members = gw.resolve_dc(gw.dc_expr(umbrella), gw.dc_filters(umbrella))
    assert len(members) > 0


def test_back_button_works_after_creating_cut(qapp, gw, tmp_path):
    """Regression (Nelson 2026-06-16, updated 2026-06-21): after a
    session commit returns to the Cuts list, the title-bar Back must
    still fire ``closed``. Back moved out of the list-page header and
    into the shared title bar (Nelson 2026-06-21 surface
    standardisation); the chassis routes the title-bar click via
    ``on_titlebar_back`` which emits the current sub-page's
    ``back_requested`` (the list page wires that to ``_on_back`` ->
    ``closed``)."""
    shell = _shell(gw)
    closed = []
    shell.closed.connect(lambda: closed.append(True))
    # Create a Cut via the session.
    session = CutSession.from_draft(gw, _draft())
    shell._start_session(session)
    shell._session_page._session.set_state("Exported Media/e2.jpg", True)
    shell._session_page._on_create()
    assert shell._stack.currentWidget() is shell.list_page
    # The session page is fully released — re-entering New Cut on a
    # stale handle would crash.
    assert shell._session_page is None                  # noqa: SLF001
    # The shell opted in to the title-bar Back (uses_titlebar_back) and
    # the chassis exposes ``on_titlebar_back`` as the routing seam.
    assert getattr(shell, "uses_titlebar_back", False) is True
    # Drive the title-bar Back path — same path MainWindow takes on a
    # user click of the shared back button.
    shell.on_titlebar_back()
    assert closed == [True]


def test_back_button_works_after_cancelled_session(qapp, gw, tmp_path):
    """Same invariant as the create flow, but via the cancel exit
    (KI-1, Nelson 2026-06-16; title-bar route per 2026-06-21)."""
    shell = _shell(gw)
    closed = []
    shell.closed.connect(lambda: closed.append(True))
    session = CutSession.from_draft(gw, _draft())
    shell._start_session(session)
    # Drive the cancel exit at the page level — _on_cancel is what the
    # session-page back button triggers.
    shell._session_page.cancelled.emit()
    assert shell._stack.currentWidget() is shell.list_page
    assert shell._session_page is None                  # noqa: SLF001
    shell.on_titlebar_back()
    assert closed == [True]


# --------------------------------------------------------------------------- #
# Rename dialog (the form grammar travels)
# --------------------------------------------------------------------------- #


def test_rename_dialog_preview_and_gating(qapp):
    dlg = _RenameCutDialog("short_version", ["short_version", "family"])
    dlg._edit.setText("Exported")
    assert "reserved" in dlg._preview.text() and not dlg._ok.isEnabled()
    dlg._edit.setText("Family")
    assert "taken" in dlg._preview.text() and not dlg._ok.isEnabled()
    dlg._edit.setText("Nova Versão")
    assert "#nova_versao" in dlg._preview.text() and dlg._ok.isEnabled()
    assert dlg.new_name() == "Nova Versão"


# --------------------------------------------------------------------------- #
# Export target dialog — spec/81 §5 "defaulted, not frozen"
# --------------------------------------------------------------------------- #


def test_export_target_dialog_defaults_to_event_cuts_folder(qapp, tmp_path):
    """Spec/81 §5: the default is ``<event_root>/Cuts/<tag>/`` —
    pre-filled and selectable in one click."""
    from pathlib import Path
    default = Path(tmp_path) / "Cuts" / "short_version"
    dlg = _ExportTargetDialog(
        default_path=default, tag_display="#short_version")
    assert dlg._edit.text() == str(default)              # noqa: SLF001
    assert dlg.target() == default
    assert dlg._ok.isEnabled()                            # noqa: SLF001


def test_export_target_dialog_empty_path_disables_ok(qapp, tmp_path):
    """Empty text disables Export — the dialog refuses to ship without
    a target."""
    dlg = _ExportTargetDialog(
        default_path=tmp_path / "Cuts" / "x", tag_display="#x")
    dlg._edit.setText("")                                # noqa: SLF001
    assert not dlg._ok.isEnabled()                       # noqa: SLF001


def test_export_target_dialog_accepts_creatable_path(qapp, tmp_path):
    """A path whose parent exists (but the leaf doesn't) is fine — the
    export will mkdir the rest. The OK button stays enabled."""
    from pathlib import Path
    new = Path(tmp_path) / "FreshDest" / "subcut"
    assert not new.exists() and new.parent.parent.exists()
    dlg = _ExportTargetDialog(
        default_path=tmp_path / "Cuts" / "x", tag_display="#x")
    dlg._edit.setText(str(new))                          # noqa: SLF001
    assert dlg._ok.isEnabled()                           # noqa: SLF001
    assert "will write to" in dlg._status.text()         # noqa: SLF001


def test_export_target_dialog_rejects_nonexistent_drive(qapp, tmp_path):
    """If no part of the path resolves to an existing parent (e.g. an
    unmounted drive letter on Windows), Export is disabled."""
    dlg = _ExportTargetDialog(
        default_path=tmp_path / "Cuts" / "x", tag_display="#x")
    # A drive letter that is virtually certain not to exist.
    dlg._edit.setText("Z:\\definitely\\not\\here")       # noqa: SLF001
    assert not dlg._ok.isEnabled()                       # noqa: SLF001


def test_on_export_cut_skips_when_target_dialog_cancelled(qapp, gw, tmp_path):
    """If the user cancels the target picker, ``_on_export_cut`` returns
    without touching the gateway — no folder is created."""
    shell = _shell(gw)
    cut = next(iter(gw.cuts()))
    cuts_root = tmp_path / "Cuts"
    assert not cuts_root.exists()
    # Stub the modal seam to simulate Cancel.
    shell._exec_target_dialog = lambda default, c: None  # noqa: SLF001
    shell._on_export_cut(cut.id)                         # noqa: SLF001
    assert not cuts_root.exists()


def test_on_export_cut_uses_picked_target(qapp, gw, tmp_path):
    """When the user picks a target (or accepts the default), the
    export writes there. The folder shows up after the call.

    spec/105 §6 — the seam now returns an :class:`ExportChoices`
    (target + options) instead of a bare ``Path``; the test mirrors
    the new contract."""
    from pathlib import Path
    from mira.ui.pages.share_cuts_page import ExportChoices
    shell = _shell(gw)
    cut = next(iter(gw.cuts()))
    custom = tmp_path / "Elsewhere" / "my_export"
    shell._exec_target_dialog = lambda default, c: ExportChoices(  # noqa: SLF001
        target=custom)
    # Stub the QMessageBox so the summary popup doesn't park on the desktop.
    from PyQt6.QtWidgets import QMessageBox
    QMessageBox.exec = lambda self: None
    shell._on_export_cut(cut.id)                          # noqa: SLF001
    assert custom.exists()


# --------------------------------------------------------------------------- #
# Audio library feeds (spec/61 §5.3)
# --------------------------------------------------------------------------- #


def test_list_moods_flat_and_nested_forms(tmp_path):
    assert audio_library.list_moods(None) == []
    assert audio_library.list_moods(tmp_path / "missing") == []
    flat = tmp_path / "flat"
    for mood in ("happy", "calm"):
        (flat / mood).mkdir(parents=True)
    (flat / "sfx").mkdir()                         # excluded by name
    assert audio_library.list_moods(flat) == ["calm", "happy"]
    nested = tmp_path / "nested"
    for mood in ("samba", "80s"):
        (nested / "music" / mood).mkdir(parents=True)
    (nested / "sfx" / "nature").mkdir(parents=True)
    assert audio_library.list_moods(nested) == ["80s", "samba"]


def _track(name: str, secs: float) -> audio_library.AudioTrack:
    from pathlib import Path
    return audio_library.AudioTrack(
        path=Path(name), kind=audio_library.AudioKind.MUSIC,
        mood="happy", duration_seconds=secs)


def test_build_playlist_covers_and_includes_crossing_file():
    tracks = [_track("a.mp3", 60), _track("b.mp3", 90), _track("c.mp3", 120)]
    out = audio_library.build_playlist(tracks, 100, rng=random.Random(1))
    total = sum(t.duration_seconds for t in tracks[:0] or out)
    assert total >= 100                            # always "a bit more"
    # the file that crossed the threshold is INCLUDED (trim room in PTE)
    assert sum(t.duration_seconds for t in out[:-1]) < 100


def test_build_playlist_short_library_returns_all():
    tracks = [_track("a.mp3", 30), _track("b.mp3", 40)]
    out = audio_library.build_playlist(tracks, 600, rng=random.Random(7))
    assert len(out) == 2
    assert audio_library.build_playlist([], 600) == []
    assert audio_library.build_playlist(tracks, 0) == []
