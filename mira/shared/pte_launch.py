"""PTE launch / reveal helpers (spec/107 Tier 1).

Two small Qt-free entry points the export-complete summary calls:

  * :func:`reveal_in_explorer` — open a folder in the OS file manager.
  * :func:`open_in_pte` — spawn PTE AV Studio with a generated `.pte`.

Both are non-blocking — they return as soon as the OS hand-off succeeds.
A failure raises :class:`OSError`; the caller decides whether to surface
the message via a Qt dialog or just log it.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def reveal_in_explorer(folder: Path) -> None:
    """Open ``folder`` in the OS file manager. Windows uses
    ``explorer.exe`` (graceful — no shell required); other platforms
    fall through to ``xdg-open`` / ``open``."""
    folder = Path(folder)
    if not folder.exists():
        raise OSError(f"folder does not exist: {folder}")
    if os.name == "nt":
        # ``explorer.exe`` returns 1 even on success (documented Windows
        # quirk) — DON'T check the return code. Spawn detached so the
        # explorer process stays alive after Mira closes.
        subprocess.Popen(["explorer", str(folder)],
                         creationflags=getattr(
                             subprocess, "DETACHED_PROCESS", 0))
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
        return
    subprocess.Popen(["xdg-open", str(folder)])


def open_in_pte(pte_executable: Path, project_file: Path) -> None:
    """Spawn PTE AV Studio with ``project_file`` loaded. Detaches the
    child so Mira can quit without taking PTE down.

    Raises :class:`OSError` when ``pte_executable`` doesn't exist or
    when ``project_file`` doesn't exist — the caller surfaces both."""
    pte_executable = Path(pte_executable)
    project_file = Path(project_file)
    if not pte_executable.is_file():
        raise OSError(f"PTE executable not found: {pte_executable}")
    if not project_file.is_file():
        raise OSError(f"project file not found: {project_file}")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
    log.info("opening PTE: %s %s", pte_executable, project_file)
    subprocess.Popen(
        [str(pte_executable), str(project_file)],
        creationflags=creationflags,
        close_fds=True,
    )


def pte_launch_available(pte_executable: Optional[str]) -> bool:
    """Cheap predicate for the UI: is the configured executable present?
    Empty / None / a stale path → False. The caller uses this to decide
    whether the "Open in PTE" button is enabled."""
    if not pte_executable:
        return False
    try:
        return Path(pte_executable).is_file()
    except OSError:
        return False


__all__ = [
    "reveal_in_explorer",
    "open_in_pte",
    "pte_launch_available",
]
