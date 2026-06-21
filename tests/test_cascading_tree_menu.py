"""Tests for ``mira.ui.base.cascading_tree_menu``.

The widget is a thin QMenu wrapper, so the tests assert structure
(submenu counts, leaf labels, signal emission) rather than visual
rendering. A ``qapp`` fixture spins a headless QApplication so QMenu
construction works without a display server.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.definition_files import KIND_COLLECTION, DefinitionRef
from mira.shared.definition_library import TreeNode
from mira.ui.base.cascading_tree_menu import CascadingTreeMenu


def _leaf(name: str) -> DefinitionRef:
    return DefinitionRef(id=name + "-id", name=name, kind=KIND_COLLECTION)


def _tree() -> TreeNode:
    """Build a small fixture tree.

        (root)
          ├── Wildlife/
          │     ├── Tropical/
          │     │     └── Best Macaws        (leaf)
          │     └── Best Wildlife            (leaf)
          └── Top Level                      (leaf)
    """
    root = Path("/virtual")
    tropical = TreeNode(
        name="Tropical", path=root / "Wildlife" / "Tropical",
        folders=[], leaves=[_leaf("Best Macaws")],
    )
    wildlife = TreeNode(
        name="Wildlife", path=root / "Wildlife",
        folders=[tropical], leaves=[_leaf("Best Wildlife")],
    )
    return TreeNode(
        name="", path=root, folders=[wildlife], leaves=[_leaf("Top Level")],
    )


def _submenu_titles(menu) -> list[str]:
    """Return the titles of menus immediately under ``menu``."""
    out = []
    for action in menu.actions():
        sub = action.menu()
        if sub is not None:
            out.append(sub.title())
    return out


def _leaf_labels(menu) -> list[str]:
    """Return the text of leaf actions immediately under ``menu``."""
    out = []
    for action in menu.actions():
        if action.menu() is None and not action.isSeparator():
            out.append(action.text())
    return out


def test_root_mirrors_top_level_folders_and_leaves(qapp):
    """The root menu carries the top-level folders (as submenus) and
    the root leaves (as actions)."""
    menu = CascadingTreeMenu(_tree())
    assert _submenu_titles(menu) == ["Wildlife"]
    assert _leaf_labels(menu) == ["Top Level"]


def test_submenus_recurse_to_any_depth(qapp):
    """Nested folders → nested QMenus, any depth."""
    menu = CascadingTreeMenu(_tree())
    wildlife = next(
        a.menu() for a in menu.actions() if a.menu() and a.menu().title() == "Wildlife")
    # Wildlife has Tropical/ submenu and a Best Wildlife leaf.
    assert _submenu_titles(wildlife) == ["Tropical"]
    assert _leaf_labels(wildlife) == ["Best Wildlife"]

    tropical = next(a.menu() for a in wildlife.actions() if a.menu())
    assert _submenu_titles(tropical) == []
    assert _leaf_labels(tropical) == ["Best Macaws"]


def test_separator_between_folders_and_leaves(qapp):
    """A folder/leaf mix at the same level gets a separator between
    the two groups so the menu reads cleanly."""
    tree = TreeNode(
        name="", path=Path("/v"),
        folders=[TreeNode(name="A", path=Path("/v/A"))],
        leaves=[_leaf("X")],
    )
    menu = CascadingTreeMenu(tree)
    # Expect: A submenu, separator, X leaf.
    actions = list(menu.actions())
    assert actions[0].menu() is not None
    assert actions[0].menu().title() == "A"
    assert actions[1].isSeparator()
    assert actions[2].text() == "X"


def test_folders_only_no_separator(qapp):
    """Folders without leaves → no spurious separator."""
    tree = TreeNode(
        name="", path=Path("/v"),
        folders=[TreeNode(name="A", path=Path("/v/A"))],
        leaves=[],
    )
    menu = CascadingTreeMenu(tree)
    assert not any(a.isSeparator() for a in menu.actions())


def test_leaves_only_no_separator(qapp):
    """Leaves without folders → no spurious separator."""
    tree = TreeNode(
        name="", path=Path("/v"),
        folders=[],
        leaves=[_leaf("A"), _leaf("B")],
    )
    menu = CascadingTreeMenu(tree)
    assert not any(a.isSeparator() for a in menu.actions())


def test_empty_tree_makes_empty_menu(qapp):
    """No folders, no leaves → an empty menu (the dialog can hide /
    grey out the picker if it likes)."""
    tree = TreeNode(name="", path=Path("/v"))
    menu = CascadingTreeMenu(tree)
    assert menu.actions() == []


def test_picking_a_leaf_emits_its_ref(qapp):
    """Triggering a leaf action emits ``definition_picked`` with that
    leaf's :class:`DefinitionRef`."""
    menu = CascadingTreeMenu(_tree())
    captured: list[DefinitionRef] = []
    menu.definition_picked.connect(captured.append)
    leaf_action = next(
        a for a in menu.actions()
        if a.menu() is None and not a.isSeparator() and a.text() == "Top Level")
    leaf_action.trigger()
    assert len(captured) == 1
    assert captured[0].name == "Top Level"


def test_submenu_picks_forward_to_root(qapp):
    """A leaf chosen in a SUBMENU emits through the root menu's
    signal too (Block 5's connect-once contract)."""
    menu = CascadingTreeMenu(_tree())
    captured: list[DefinitionRef] = []
    menu.definition_picked.connect(captured.append)
    wildlife = next(
        a.menu() for a in menu.actions() if a.menu() and a.menu().title() == "Wildlife")
    deep_leaf = next(
        a for a in wildlife.actions()
        if a.menu() is None and not a.isSeparator() and a.text() == "Best Wildlife")
    deep_leaf.trigger()
    assert len(captured) == 1
    assert captured[0].name == "Best Wildlife"


def test_root_title_is_settable(qapp):
    """The root menu's title can be overridden at construction (for
    a top-level "Load Recipe…" caption)."""
    menu = CascadingTreeMenu(_tree(), title="Load Recipe")
    assert menu.title() == "Load Recipe"
