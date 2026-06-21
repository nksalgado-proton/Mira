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
from typing import Dict, Iterable, List, Optional, Tuple

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
        # spec/94 Phase 1b — reconcile-on-scan. Stash a content hash
        # of the tree the LAST time we refreshed; the next public read
        # auto-refreshes if a file's been touched out-of-band (rename
        # / move / hand-edit in the OS file manager).
        self._scan_signature: Optional[int] = None

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

        Updates the scan signature so subsequent reads can short-
        circuit when nothing on disk has changed (spec/94 Phase 1b
        reconcile-on-scan).
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
        self._scan_signature = self._current_scan_signature()

    def list_tree(self) -> TreeNode:
        """The folder tree, with leaves rendered as
        :class:`DefinitionRef`. Refreshes from disk on first call AND
        whenever the on-disk tree's signature has changed since the
        last scan (spec/94 Phase 1b reconcile-on-scan: an OS rename or
        a hand-edit between reads is picked up automatically). The
        wirer can also call :meth:`refresh` explicitly."""
        self._maybe_reconcile()
        assert self._tree is not None
        return self._tree

    def all_definitions(self) -> List[DefinitionFile]:
        """Every loaded definition, in arbitrary order. Useful for
        the classifier and the duplicate-name probe."""
        self._maybe_reconcile()
        return list(self._by_id.values())

    # ── id / name lookup ──────────────────────────────────────────

    def by_id(self, definition_id: str) -> Optional[DefinitionFile]:
        """Lookup by stable id. ``None`` if the id is unknown."""
        self._maybe_reconcile()
        return self._by_id.get(definition_id)

    def by_name(self, display_name: str) -> Optional[DefinitionFile]:
        """Lookup by display name (filename). Returns the first match
        when duplicates exist; the soft-uniqueness warning surfaces
        via :meth:`duplicate_display_names`."""
        self._maybe_reconcile()
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
        self._maybe_reconcile()
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
            candidate = file_path_for(folder, df.name)
            df.path = self._disambiguate_path(candidate, owner_id=df.id)
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
        candidate = file_path_for(df.path.parent, new_display_name)
        new_path = self._disambiguate_path(candidate, owner_id=df.id)
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

    # ── reconcile-on-scan (spec/94 Phase 1b) ──────────────────────

    def _current_scan_signature(self) -> int:
        """A cheap hash of the tree's state on disk — derived from the
        sorted (path, size, mtime_ns) triples of every ``.json`` file
        under the root.

        Catches every out-of-band change a normal file operation can
        produce: an OS rename (path changes), a delete (file
        disappears), an add (new path appears), a hand-edit (size +
        mtime move).

        Doesn't catch a hand-edit that perfectly preserves size + mtime
        — vanishingly rare, and the caller can always call
        :meth:`refresh` explicitly for those.

        We hash the triples rather than storing them so the cached
        state stays a single int (cheap to compare on every read).
        """
        if not self._root.exists():
            return 0
        try:
            entries = []
            for path in sorted(self._root.rglob("*.json")):
                if not path.is_file():
                    continue
                st = path.stat()
                entries.append((str(path), st.st_size, st.st_mtime_ns))
        except OSError:
            return 0
        return hash(tuple(entries))

    def _maybe_reconcile(self) -> None:
        """Refresh from disk when the cached signature is stale.

        Called by every public read path. First call refreshes
        unconditionally (``_scan_signature`` is None); subsequent
        calls only refresh when the on-disk signature has moved.
        """
        if self._scan_signature is None:
            self.refresh()
            return
        if self._current_scan_signature() != self._scan_signature:
            self.refresh()

    # ── slug-collision disambiguation (spec/94 Phase 1b) ─────────

    def _disambiguate_path(self, candidate: Path, *, owner_id: str) -> Path:
        """If ``candidate`` is already in use by a DIFFERENT id (or
        a name that case-folds to the same string on a Windows-style
        filesystem), append a short id-fragment suffix so the two
        definitions live as distinct files.

        The check folds case so NTFS / APFS-default don't silently
        overwrite (``Best Wildlife.json`` vs ``best wildlife.json``
        are the same file on those filesystems). The id is the
        load-bearing key — the displayed name + the disambiguating
        suffix stay readable, no UUID hex strings in the visible
        slug.
        """
        folder = candidate.parent
        if not folder.exists():
            return candidate

        target_stem = candidate.stem
        target_stem_fold = target_stem.lower()

        # If a file with the same case-folded stem exists, decide
        # whether it's the owner's existing file (re-save) or a
        # genuine collision with a different id.
        collision: Optional[Path] = None
        for path in folder.iterdir():
            if not path.is_file() or path.suffix != candidate.suffix:
                continue
            if path.stem.lower() != target_stem_fold:
                continue
            # Found a same-stem neighbour. Check the id.
            try:
                existing = read_definition(path)
            except DefinitionParseError:
                # Corrupt file at the target — treat as a collision
                # so we don't overwrite it on save.
                collision = path
                break
            if existing.id == owner_id:
                # Same definition (rename target back, or repeated
                # save) — keep using the existing path verbatim so
                # case-only renames work out of the box.
                return path
            collision = path
            break

        if collision is None:
            return candidate

        # Genuine collision: another definition owns this slug.
        # Suffix with a short id-fragment of the SAVING definition
        # so the existing file keeps its bare slug.
        suffix_stem = f"{target_stem} ({owner_id[:6]})"
        new_path = folder / f"{suffix_stem}{candidate.suffix}"
        # Defensive: if even the suffixed slug collides (two
        # definitions whose ids share a 6-char prefix), grow to
        # 12 chars.
        if new_path.exists() and not _is_owner(new_path, owner_id):
            suffix_stem = f"{target_stem} ({owner_id[:12]})"
            new_path = folder / f"{suffix_stem}{candidate.suffix}"
        return new_path


def _is_owner(path: Path, owner_id: str) -> bool:
    """``True`` when ``path`` already holds a definition with id
    ``owner_id`` — used by the disambiguation helper to detect a
    re-save of an existing file."""
    try:
        df = read_definition(path)
    except DefinitionParseError:
        return False
    return df.id == owner_id


__all__ = [
    "DefinitionLibrary",
    "TreeNode",
]
