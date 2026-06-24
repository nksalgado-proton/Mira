"""Resolver for the persistent post-export actions on a shipped Cut
(spec/117).

The Cut stores **no absolute path** (charter #2). Once it shipped, the
export folder is recomputed by re-running the same resolver the export
pipeline used (per-event vs. cross-event), then probed on disk:

  1. Re-resolve the default via :func:`mira.shared.cut_export.resolve_event_cut_target`
     (per-event) or :func:`resolve_cross_event_cut_target` (cross-event),
     fed ``library_root`` + ``cuts_export_root`` from settings. If that
     exact folder exists, use it.
  2. If it doesn't exist (disambiguated to ``… (2)``, moved, or deleted),
     fall back to the parent ``Cuts/…`` folder so the user can still
     find the bundle. **Open in PTE** is hidden in this case — no
     project to point at.
  3. The ``.pte`` is discovered by globbing the resolved folder
     (``*.pte``, preferring ``slideshow*.pte``). When none is found
     **Open in PTE** is hidden.

Pure Qt-free — the UI layer wires the buttons via these results."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mira.shared.cut_export import (
    resolve_cross_event_cut_target,
    resolve_event_cut_target,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExportedCutLocation:
    """The resolved state of an exported Cut's bundle on disk.

    * ``folder`` — the directory **Open folder** reveals. The exact
      export folder when it exists; otherwise its parent (so the user
      lands one click away from the bundle).
    * ``pte_file`` — the ``.pte`` to hand to PTE, or ``None`` when no
      project file is found in the resolved folder.
    * ``folder_exists`` — True iff ``folder`` IS the exact re-resolved
      target (step 1 hit); False means we fell back to the parent
      (step 2). The UI hides the "Open in PTE" affordance when False.
    """

    folder: Path
    pte_file: Optional[Path]
    folder_exists: bool

    @property
    def pte_available(self) -> bool:
        """True iff the bundle resolved AND a ``.pte`` was found in it.
        The UI couples Open-in-PTE to this (alongside its own
        ``use_pte`` + ``pte_launch_available`` gates)."""
        return self.folder_exists and self.pte_file is not None


def find_pte_in(folder: Path) -> Optional[Path]:
    """Glob ``folder`` for a ``.pte`` project file. Prefer
    ``slideshow.pte`` (the generator's default) over any
    ``slideshow (2).pte`` disambiguation — the user may have already
    edited the original in PTE and the re-export sits at a higher
    suffix. Fall back to any ``*.pte`` when no ``slideshow`` match.
    Returns ``None`` when ``folder`` doesn't exist or has no project."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    canonical = folder / "slideshow.pte"
    if canonical.is_file():
        return canonical
    # Then any other ``slideshow*.pte`` (disambiguated copies). Sort by
    # name so the user sees a stable choice across runs.
    preferred = sorted(folder.glob("slideshow*.pte"))
    if preferred:
        return preferred[0]
    others = sorted(folder.glob("*.pte"))
    return others[0] if others else None


def resolve_event_cut_location(
    *,
    cut,
    event_root: Path,
    event_name: str,
    library_root: Optional[Path],
    cuts_export_root: Optional[str],
) -> ExportedCutLocation:
    """Resolve the location for a per-event Cut. The exporter wrote it
    under :func:`resolve_event_cut_target` — we re-run that resolver
    with the same inputs, then probe disk for an exact / fallback."""
    target = resolve_event_cut_target(
        event_root=Path(event_root),
        event_name=event_name or "",
        cut_tag=cut.tag,
        library_root=library_root,
        cuts_export_root=cuts_export_root or None,
    )
    return _resolve_from_target(target)


def resolve_cross_event_cut_location(
    *,
    cut_tag: str,
    library_root: Path,
    cuts_export_root: Optional[str],
) -> ExportedCutLocation:
    """Resolve the location for a cross-event Cut. The exporter wrote
    it under :func:`resolve_cross_event_cut_target` — re-run the same
    resolver, then probe disk."""
    target = resolve_cross_event_cut_target(
        cut_tag=cut_tag,
        library_root=Path(library_root),
        cuts_export_root=cuts_export_root or None,
    )
    return _resolve_from_target(target)


def _resolve_from_target(target: Path) -> ExportedCutLocation:
    """Steps 1–3 against a resolved default target. When the target
    exists, look for the ``.pte`` and return both. When it doesn't,
    fall back to the parent + drop the project."""
    target = Path(target)
    if target.is_dir():
        return ExportedCutLocation(
            folder=target,
            pte_file=find_pte_in(target),
            folder_exists=True,
        )
    # Step 2 — fall back to the parent so the user can still find the
    # bundle (even if it landed at ``… (2)`` or was renamed). The
    # parent ALWAYS exists when the target's parent chain is alive;
    # walk up if it doesn't (e.g. the whole Cuts/ tree was deleted).
    parent = target.parent
    while not parent.is_dir() and parent != parent.parent:
        parent = parent.parent
    return ExportedCutLocation(
        folder=parent,
        pte_file=None,
        folder_exists=False,
    )


def is_exported(cut) -> bool:
    """True iff the Cut shows up as shipped (``last_exported_at`` set).
    A never-exported Cut hides both spec/117 actions."""
    return bool(getattr(cut, "last_exported_at", None))


__all__ = [
    "ExportedCutLocation",
    "find_pte_in",
    "is_exported",
    "resolve_event_cut_location",
    "resolve_cross_event_cut_location",
]
