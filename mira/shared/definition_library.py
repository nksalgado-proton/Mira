"""Cached file-tree library for spec/93 §4 Collections / Recipes.

Wraps one of the two folder trees under the user-chosen library root:

    <library_root>/Collections/     (kind="collection")
    <library_root>/Recipes/         (kind="recipe")

The library is the service-level seam between the on-disk JSON files
(:mod:`core.definition_files`) and the gateway/UI layer. It owns:

* A **cached tree-scan** — a :class:`TreeNode` mirroring the folder
  structure, leaves carrying :class:`core.definition_files.DefinitionRef`.
  The cache invalidates on writes through the library and on an
  explicit :meth:`refresh` (the wirer triggers ``refresh`` after the
  user has edited files in their OS file manager).
* **id-based lookup** (:meth:`by_id`) and a **name fallback**
  (:meth:`by_name`) — the spec/93 §4 resolution contract.
* **OS-rename adoption** — the next scan sees the new filename and
  the in-memory display name updates without any DB row to migrate.
  The id is unchanged, so every referrer (a nested Collection
  operand, a Cut's frozen source link) keeps resolving.
* **Atomic write-then-rename** for new + edited files (invariant #6).
* **Soft duplicate-display-name warning** — duplicates don't fail
  the save (the id is the load-bearing key); the library exposes
  the duplicates so the dialog can ask the user to disambiguate.

The library is wirable; lifecycle is owned by whoever assembles the
gateway. It expects to live under the spec/76 §A single-writer lock,
so the writes are not serialised internally — the lock guarantees
exclusive access library-wide.

No Qt imports, no gateway coupling — same discipline as
:mod:`mira.shared.recipe_store`.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from core.definition_files import (
    DefinitionFile,
    DefinitionParseError,
    DefinitionRef,
    KIND_COLLECTION,
    KIND_RECIPE,
    KINDS,
    display_name_from_path,
    file_path_for,
    new_definition_id,
    read_definition,
    to_ref,
    write_definition,
)

log = logging.getLogger(__name__)


@dataclass
class TreeNode:
    """One folder in the library tree.

    ``name`` is the folder name (empty for the root). ``folders`` are
    the immediate subfolders, ordered alphabetically; ``leaves`` are
    the definitions in this folder, ordered alphabetically by display
    name. The cascading-menu builder (Block 5) walks this structure to
    build nested ``QMenu`` instances.
    """
    name: str
    path: Path
    folders: List["TreeNode"] = field(default_factory=list)
    leaves: List[DefinitionRef] = field(default_factory=list)


class DefinitionLibrary:
    """File-system-backed library for one definition kind.

    Construct with ``DefinitionLibrary(library_root / "Collections",
    KIND_COLLECTION)`` or the analogous Recipes call.
    """

    def __init__(self, root: Path, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown definition kind: {kind!r}")
        self._root = root
        self._kind = kind
        self._tree: Optional[TreeNode] = None
        self._by_id: Dict[str, DefinitionFile] = {}

    # ── public state ──────────────────────────────────────────────

    @property
    def root(self) -> Path:
        """The folder this library wraps (e.g.
        ``<library_root>/Collections/``)."""
        return self._root

    @property
    def kind(self) -> str:
        """``"collection"`` or ``"recipe"``."""
        return self._kind

    # ── scan ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Rebuild the cache from disk.

        Walks the tree under :attr:`root`, reads every ``.json`` file
        as a :class:`DefinitionFile`, backfills missing ids (writing
        the new id back atomically so the next scan is stable), and
        rebuilds the :class:`TreeNode` index.

        Parse errors on individual files are logged + skipped; the
        rest of the tree still loads. The first run on an empty tree
        is a no-op.
        """
        self._by_id.clear()
        loaded: Dict[Path, DefinitionFile] = {}
        for path in self._walk():
            try:
                df = read_definition(path)
            except DefinitionParseError as exc:
                log.warning(
                    "definition_library: skipping %s: %s", path, exc.reason)
                continue
            # spec/93 §4 fallback: a hand-authored file may omit ``id`` —
            # backfill a UUID and write it back so subsequent reads are
            # id-anchored. The file's display name (filename) is
            # unchanged.
            if not df.id:
                df.id = new_definition_id()
                try:
                    write_definition(df)
                except OSError as exc:
                    log.warning(
                        "definition_library: id backfill failed for %s: %s",
                        path, exc)
                    # Cache the in-memory copy anyway so by_name still
                    # finds it — the next save will get an id written.
            # Defend against duplicate ids (two files claiming the
            # same UUID — rare but possible if the user duplicated a
            # JSON file in their file manager). Last one wins; warn.
            if df.id in self._by_id:
                log.warning(
                    "definition_library: duplicate id %s in %s and %s — "
                    "the later file wins. Edit the JSON to give one a "
                    "fresh id, or delete the duplicate.",
                    df.id, self._by_id[df.id].path, path,
                )
            self._by_id[df.id] = df
            loaded[path] = df
        self._tree = self._build_tree(loaded)

    def list_tree(self) -> TreeNode:
        """The folder tree, with leaves rendered as
        :class:`DefinitionRef`. Refreshes from disk on first call;
        the wirer can call :meth:`refresh` to force a re-scan after
        an external file edit."""
        if self._tree is None:
            self.refresh()
        # ``refresh`` always rebuilds the tree, so ``_tree`` is set
        # after the call. ``assert`` keeps the type checkers happy.
        assert self._tree is not None
        return self._tree

    def all_definitions(self) -> List[DefinitionFile]:
        """Every loaded definition, in arbitrary order. Useful for
        the classifier and the duplicate-name probe."""
        if self._tree is None:
            self.refresh()
        return list(self._by_id.values())

    # ── id / name lookup ──────────────────────────────────────────

    def by_id(self, definition_id: str) -> Optional[DefinitionFile]:
        """Lookup by stable id. ``None`` if the id is unknown."""
        if self._tree is None:
            self.refresh()
        return self._by_id.get(definition_id)

    def by_name(self, display_name: str) -> Optional[DefinitionFile]:
        """Lookup by display name (filename). Returns the first match
        when duplicates exist; the soft-uniqueness warning surfaces
        via :meth:`duplicate_display_names`."""
        if self._tree is None:
            self.refresh()
        for df in self._by_id.values():
            if df.name == display_name:
                return df
        return None

    def resolve(
        self,
        *,
        definition_id: str = "",
        display_name: str = "",
    ) -> Optional[DefinitionFile]:
        """Spec/93 §4 resolution: id first, name fallback. Used by
        the gateway when a referrer's ``id`` no longer matches a
        live file (the user deleted it and re-created one with the
        same display name — the name fallback finds the new file).
        """
        if definition_id:
            hit = self.by_id(definition_id)
            if hit is not None:
                return hit
        if display_name:
            return self.by_name(display_name)
        return None

    def duplicate_display_names(self) -> Dict[str, int]:
        """``{display_name: count}`` for names that resolve to more
        than one file. Drives the soft uniqueness warning (§4 / §8
        ``soft scan-on-save``). An empty dict means the namespace
        is clean."""
        if self._tree is None:
            self.refresh()
        counts: Dict[str, int] = {}
        for df in self._by_id.values():
            counts[df.name] = counts.get(df.name, 0) + 1
        return {k: v for k, v in counts.items() if v > 1}

    # ── CRUD ──────────────────────────────────────────────────────

    def save(
        self,
        df: DefinitionFile,
        *,
        subfolder: Path = Path("."),
    ) -> DefinitionFile:
        """Persist ``df`` under :attr:`root` / ``subfolder``.

        If ``df.path`` is None, it's computed from ``subfolder`` +
        ``df.name`` via :func:`file_path_for`. ``df.id`` is
        backfilled on demand (a new definition typically arrives
        with no id; we mint one).

        The write is atomic (write-then-rename, invariant #6). The
        cache is refreshed on success so subsequent lookups see the
        new state.
        """
        if df.kind != self._kind:
            raise ValueError(
                f"definition_library({self._kind}).save received kind "
                f"{df.kind!r}; mismatched library."
            )
        if not df.id:
            df.id = new_definition_id()
        if df.path is None:
            folder = (self._root / subfolder).resolve()
            df.path = file_path_for(folder, df.name)
        write_definition(df)
        self.refresh()
        return df

    def rename(self, definition_id: str, new_display_name: str) -> DefinitionFile:
        """Rename the file backing ``definition_id`` to
        ``new_display_name``. The id is unchanged, so every referrer
        keeps resolving.

        The in-file ``name`` hint is rewritten so hand-editors see
        the new name on opening the JSON.
        """
        df = self.by_id(definition_id)
        if df is None:
            raise KeyError(definition_id)
        assert df.path is not None  # always set after a scan
        new_path = file_path_for(df.path.parent, new_display_name)
        if new_path != df.path:
            # ``os.replace`` is atomic on the same volume and silently
            # overwrites — that mirrors the file-manager rename the
            # user could've done themselves.
            os.replace(str(df.path), str(new_path))
            df.path = new_path
            df.name = display_name_from_path(new_path)
            # Rewrite the JSON so the in-file ``name`` hint matches.
            write_definition(df)
        self.refresh()
        return df

    def delete(self, definition_id: str) -> bool:
        """Remove the file backing ``definition_id``. Returns
        ``True`` on success, ``False`` if the id didn't resolve
        (already deleted). The cache is refreshed."""
        df = self.by_id(definition_id)
        if df is None:
            return False
        assert df.path is not None
        try:
            os.remove(str(df.path))
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning(
                "definition_library: delete failed for %s: %s", df.path, exc)
            return False
        self.refresh()
        return True

    # ── helpers (internal) ────────────────────────────────────────

    def _walk(self) -> Iterable[Path]:
        """Yield every ``.json`` file under :attr:`root`, depth-first.
        Empty if the root doesn't exist (a fresh install without the
        scaffold)."""
        if not self._root.exists():
            return
        for path in sorted(self._root.rglob("*.json")):
            if path.is_file():
                yield path

    def _build_tree(self, loaded: Dict[Path, DefinitionFile]) -> TreeNode:
        """Project the flat path → file dict back into a tree mirroring
        the folder structure on disk. Folder names sort alphabetically;
        leaves sort by display name."""
        root_node = TreeNode(name="", path=self._root)
        # Map of relative-folder-path → TreeNode for accumulation.
        nodes: Dict[Path, TreeNode] = {Path("."): root_node}

        def _ensure_folder(rel: Path) -> TreeNode:
            if rel == Path(".") or str(rel) == "":
                return root_node
            if rel in nodes:
                return nodes[rel]
            parent = _ensure_folder(rel.parent)
            node = TreeNode(name=rel.name, path=self._root / rel)
            parent.folders.append(node)
            nodes[rel] = node
            return node

        for path, df in loaded.items():
            try:
                rel = path.parent.relative_to(self._root)
            except ValueError:
                # File outside root (shouldn't happen via _walk) —
                # skip rather than misplace.
                continue
            folder = _ensure_folder(rel)
            folder.leaves.append(to_ref(df))

        def _sort(node: TreeNode) -> None:
            node.folders.sort(key=lambda n: n.name.lower())
            node.leaves.sort(key=lambda r: r.name.lower())
            for sub in node.folders:
                _sort(sub)

        _sort(root_node)
        return root_node


__all__ = [
    "DefinitionLibrary",
    "TreeNode",
]
