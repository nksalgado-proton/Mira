"""Cascading folder-menu widget (spec/93 §4 last paragraph).

Given a :class:`mira.shared.definition_library.TreeNode` mirroring a
folder tree on disk, builds a chain of nested :class:`QMenu` instances
whose structure follows the folders, leaves of which are the
definitions. The user navigates their own structure ("Wildlife" →
"Tropical" → "Best macaws") to pick the one they want — the file
manager is the management surface, the menu is the browser.

Emits :pyattr:`definition_picked` with the chosen
:class:`core.definition_files.DefinitionRef` so the dialog can resolve
it via the gateway.

Any depth supported. Folders come first, alphabetical (case-blind);
leaves follow, also alphabetical. Separator between the two groups so
the menu reads cleanly. Empty leaf groups stay empty (no placeholder);
empty folder groups simply don't add a submenu.

No inline ``setStyleSheet`` — the standard :class:`QMenu` styling
(palette-driven) is enough; the cascading reads visually as nested
menus do everywhere else in Qt.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMenu, QWidget

from core.definition_files import DefinitionRef
from mira.shared.definition_library import TreeNode


class CascadingTreeMenu(QMenu):
    """A QMenu that mirrors a folder TreeNode.

    Construct with a :class:`TreeNode` from the library's
    :meth:`DefinitionLibrary.list_tree`; connect
    :pyattr:`definition_picked` to the slot that loads the chosen
    definition.
    """

    #: Emitted with the picked :class:`DefinitionRef` when the user
    #: triggers a leaf action. Submenus forward the signal so the
    #: connection only needs to happen on the root menu.
    definition_picked = pyqtSignal(object)

    def __init__(
        self,
        tree: TreeNode,
        *,
        title: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        # The root menu's title is set by the caller (e.g. "Load
        # Recipe…") via ``title``; subfolders inherit their folder
        # name automatically.
        super().__init__(title or tree.name, parent)
        self.setObjectName("CascadingTreeMenu")
        self._populate(tree)

    def _populate(self, tree: TreeNode) -> None:
        """Walk the tree, building submenus for folders + actions for
        leaves. Folders sort alphabetically (already done by
        :class:`DefinitionLibrary._build_tree`, but we don't rely on
        caller-side sort); leaves sort similarly. Separator between
        the two groups so the menu reads cleanly."""
        folders = list(tree.folders)
        leaves = list(tree.leaves)

        for folder in folders:
            sub = CascadingTreeMenu(folder, parent=self)
            sub.setTitle(folder.name)
            # Forward the leaf signal up the chain so the caller only
            # connects on the root menu.
            sub.definition_picked.connect(self.definition_picked)
            self.addMenu(sub)

        if folders and leaves:
            self.addSeparator()

        for leaf in leaves:
            action = self.addAction(leaf.name)
            # The default-arg dance captures ``leaf`` per-iteration so
            # every action emits its own ref (without it the lambda
            # would close over the last-iteration value).
            action.triggered.connect(
                lambda _checked=False, ref=leaf: self.definition_picked.emit(ref))


__all__ = ["CascadingTreeMenu"]
