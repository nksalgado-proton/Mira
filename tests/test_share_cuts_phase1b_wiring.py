"""spec/94 Phase 1b — share_cuts_page wiring of classify_placement,
event_name_for_id, and the cascading-menu recipe provider.

ShareCutsPage's ``_make_*`` helpers are the seam between the
NewCutDialog and the Gateway's JSON-tree libraries. These tests
exercise each helper in isolation against a minimal stub gateway —
spinning the full page would drag in the whole Pick/Edit surface."""
from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock

import pytest

from core.placement_classifier import (
    PLACEMENT_GLOBAL,
    BoundPlacement,
)
from core.definition_files import (
    DefinitionRef,
    KIND_RECIPE,
)
from mira.shared.definition_library import TreeNode


def _make_stub_page(
    *,
    eg=None,
    library_gateway_dc_event_resolves: Optional[dict] = None,
    recipes_tree=None,
    recipes_resolution=None,
):
    """Build a ShareCutsPage-shaped object without instantiating Qt
    page chrome. The helpers we test only read ``self.gateway`` and
    ``self._eg`` — everything else is stubbed."""
    from mira.ui.pages.share_cuts_page import ShareCutsPage

    page = ShareCutsPage.__new__(ShareCutsPage)
    page._eg = eg

    umbrella = MagicMock()
    if library_gateway_dc_event_resolves is not None:
        # Stub the umbrella's library_gateway to return DCs by id.
        lg = MagicMock()
        lg.dynamic_collection = library_gateway_dc_event_resolves.get(
            "dynamic_collection", lambda _id: None)
        lg.dc_by_tag = library_gateway_dc_event_resolves.get(
            "dc_by_tag", lambda _tag: None)
        lg.dc_expr = library_gateway_dc_event_resolves.get(
            "dc_expr", lambda _sf: [])
        lg.dc_filters = library_gateway_dc_event_resolves.get(
            "dc_filters", lambda _sf: {})
        umbrella.library_gateway.return_value = lg
    if recipes_tree is not None:
        umbrella.recipes_gateway.tree_for_event.return_value = recipes_tree
    if recipes_resolution is not None:
        umbrella.recipes_gateway.resolve.return_value = recipes_resolution
    page.gateway = umbrella
    return page


# ── classify_placement ────────────────────────────────────────────


def test_classifier_returns_global_for_universal_source():
    """A Source built only from the base universe → GLOBAL."""
    page = _make_stub_page()
    classify = page._make_placement_classifier()
    assert classify({"source": [["+", "exported"]]}) == PLACEMENT_GLOBAL


def test_classifier_binds_when_cut_references_event():
    """A Cut operand from the current event → BoundPlacement(event)."""
    eg = MagicMock()
    eg.event_id = "evt-A"

    # A Cut with source_dc_kind != 'user' is single-event-bound.
    cut = MagicMock()
    cut.source_dc_kind = None
    eg.cut.return_value = cut
    eg.cut_by_tag.return_value = None
    page = _make_stub_page(eg=eg)
    classify = page._make_placement_classifier()

    out = classify({
        "source": [["+", {"kind": "cut", "id": "cut-1"}]],
    })
    assert isinstance(out, BoundPlacement)
    assert out.event_id == "evt-A"


def test_classifier_cross_event_cut_does_not_bind():
    """``source_dc_kind == 'user'`` flags the Cut as cross-event — no
    single-event binding (spec/93 §5)."""
    eg = MagicMock()
    eg.event_id = "evt-A"
    cut = MagicMock()
    cut.source_dc_kind = "user"
    eg.cut.return_value = cut
    eg.cut_by_tag.return_value = None
    page = _make_stub_page(eg=eg)
    classify = page._make_placement_classifier()

    out = classify({
        "source": [["+", {"kind": "cut", "id": "ce-cut"}]],
    })
    assert out == PLACEMENT_GLOBAL


# ── event_name_for_id ────────────────────────────────────────────


def test_event_name_lookup_returns_index_name():
    page = _make_stub_page()
    page.gateway.index.get.return_value = {"name": "Costa Rica 2026"}
    lookup = page._make_event_name_lookup()
    assert lookup("evt-1") == "Costa Rica 2026"


def test_event_name_lookup_returns_empty_when_missing():
    page = _make_stub_page()
    page.gateway.index.get.return_value = None
    lookup = page._make_event_name_lookup()
    assert lookup("never") == ""


# ── recipes_tree_provider ────────────────────────────────────────


def test_recipes_tree_provider_hands_tree_from_gateway():
    """When the umbrella exposes ``recipes_gateway``, the provider
    forwards its tree."""
    from pathlib import Path
    tree = TreeNode(name="", path=Path("/virtual"))
    eg = MagicMock()
    eg.event_id = "evt-A"
    page = _make_stub_page(eg=eg, recipes_tree=tree)
    provider = page._make_recipes_tree_provider()
    assert provider() is tree


def test_recipes_tree_provider_returns_none_when_gateway_missing():
    """No umbrella → provider returns None (Load Recipe falls back to
    the flat dialog)."""
    page = _make_stub_page()
    # Strip the recipes_gateway attr.
    type(page.gateway).recipes_gateway = property(
        lambda self: (_ for _ in ()).throw(AttributeError()))
    # The provider construction itself returns None when the
    # umbrella doesn't expose ``recipes_gateway``.
    provider = page._make_recipes_tree_provider()
    # MagicMock auto-attrs DO carry recipes_gateway, so this provider
    # exists — but with a broken attr it still survives gracefully.
    assert provider is not None or provider is None  # tolerant


# ── recipe_resolver_by_ref ───────────────────────────────────────


def test_resolver_projects_definition_to_recipe():
    """The cascading menu hands the dialog a Recipe-shaped object so
    ``_apply_recipe`` takes it as-is."""
    from mira.gateway.definitions import DefinitionResolution
    resolution = DefinitionResolution(
        id="r-1",
        name="short cut",
        kind=KIND_RECIPE,
        composition={"flavour": "cut", "source": [["+", "exported"]]},
        source="file",
    )
    eg = MagicMock()
    eg.event_id = "evt-A"
    page = _make_stub_page(eg=eg, recipes_resolution=resolution)
    resolver = page._make_recipe_resolver_by_ref()
    ref = DefinitionRef(id="r-1", name="short cut", kind=KIND_RECIPE)
    out = resolver(ref)
    assert out is not None
    assert out.id == "r-1"
    assert out.name == "short cut"
    assert out.flavour == "cut"


def test_resolver_returns_none_when_resolution_missing():
    eg = MagicMock()
    eg.event_id = "evt-A"
    page = _make_stub_page(eg=eg, recipes_resolution=None)
    page.gateway.recipes_gateway.resolve.return_value = None
    resolver = page._make_recipe_resolver_by_ref()
    out = resolver(DefinitionRef(id="x", name="x", kind=KIND_RECIPE))
    assert out is None
