"""User-data location — the single source of truth for the new app.

Ported from legacy ``core/settings.py::user_data_dir`` so the ``mira/``
package is self-contained: when ``core/`` is archived (charter §4 step 8) nothing
here breaks. Resolution order:

  1. ``MIRA_DATA_DIR`` env var (tests, custom deployments)
  2. Windows: ``%LOCALAPPDATA%\\Mira``
  3. Other OS: ``~/.mira``

This is where every cross-event domain lives (settings, the events index, the
future user-knowledge / rules / tone-corpus stores). Per-event ``event.db`` files
live under their own ``event_root`` (spec/03), not here. No hardcoded user paths —
charter invariant.
"""
from __future__ import annotations

import logging
import os
import platform
from pathlib import Path

log = logging.getLogger(__name__)


def user_data_dir() -> Path:
    """Base directory for all cross-event Mira user data. Created on demand."""
    override = os.environ.get("MIRA_DATA_DIR")
    if override:
        base = Path(override)
    elif platform.system() == "Windows":
        base = Path.home() / "AppData" / "Local" / "Mira"
    else:
        base = Path.home() / ".mira"
    base.mkdir(parents=True, exist_ok=True)
    return base


def migrate_legacy_user_data() -> bool:
    """One-shot copy of legacy ``%LOCALAPPDATA%\\Miracraft\\`` contents into
    the new ``%LOCALAPPDATA%\\Mira\\`` location.

    Lets dev installs (and any future user who upgrades from the MiraCrafter-named
    build to the Mira-named one) carry their settings, events index, ``.history``
    journal, body/brand profile overrides, ``miracrafter.db`` / ``people/``, and
    log archive forward without manual copy.

    Idempotent and non-destructive:
    - skips on non-Windows (POSIX builds never wrote to ``Miracraft``).
    - skips if the new dir already has any content (the live install owns it).
    - leaves the legacy dir intact so ``git switch XMC`` can still launch the
      old binary against its original data.

    Returns ``True`` if a copy ran, ``False`` otherwise. Logs each item copied.
    Call once from ``mira.ui.app.main()`` early in startup; do NOT call from
    ``user_data_dir()`` (tests override that path via ``MIRA_DATA_DIR``).
    """
    if platform.system() != "Windows":
        return False
    if os.environ.get("MIRA_DATA_DIR"):
        return False  # explicit override — caller knows what they want
    new = Path.home() / "AppData" / "Local" / "Mira"
    old = Path.home() / "AppData" / "Local" / "Miracraft"
    if not old.exists():
        return False
    if new.exists() and any(new.iterdir()):
        return False
    import shutil
    new.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in old.iterdir():
        target = new / item.name
        if target.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
            copied += 1
            log.info("migrated %s -> %s", item, target)
        except OSError as exc:
            log.warning("failed to migrate %s: %s", item, exc)
    if copied:
        log.info(
            "migrated %d item(s) from legacy Miracraft user-data dir; the "
            "legacy directory was left in place as a safety net.", copied
        )
    return copied > 0
