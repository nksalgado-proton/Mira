"""Session-level singleton flags (spec/76 §B.1).

Process-wide state that's set once at startup and consulted by both
the data layer and every UI surface. ONE flag, many readers — the
brief calls out "do not scatter per-widget guards".

The read-only flag is raised when another machine (or another local
process) holds the library writer lock; this app then opens the
library **read-only**: decision verbs, Edit writes, Export, event-
header / plan / day-management saves are all no-ops, greyed with a
quiet "read-only" hint, and a banner names the editing machine.

Qt-free on purpose — :mod:`mira.gateway` imports this for its
defensive write-guard, and the data layer must not depend on the UI
(charter §2). The UI imports the same module for its banner +
control-disable surface.
"""
from __future__ import annotations

from typing import Optional

from core.library_lock import LockInfo


_read_only: bool = False
_holder: Optional[LockInfo] = None


def set_read_only(read_only: bool, holder: Optional[LockInfo] = None) -> None:
    """Set the process-wide read-only flag.

    Called once during app startup from :mod:`mira.ui.app` when the
    writer lock is held by someone else. The rest of the app reads,
    never writes.

    When ``read_only=False``, ``holder`` is cleared regardless of the
    value passed — a writeable session never carries a foreign
    holder.
    """
    global _read_only, _holder
    _read_only = bool(read_only)
    _holder = holder if _read_only else None


def is_read_only() -> bool:
    """True when the library is opened read-only (spec/76 §B.1)."""
    return _read_only


def read_only_holder() -> Optional[LockInfo]:
    """The writer that owns the lock when we're read-only — drives
    the banner text and the per-control hint. ``None`` when we hold
    the lock ourselves."""
    return _holder


def reset_for_tests() -> None:
    """Test helper — restore the default writeable state. Tests that
    flip the flag must call this in teardown or another test will
    see read-only mode."""
    set_read_only(False, None)


class ReadOnlyLibraryError(RuntimeError):
    """Raised by gateway mutators when the library is open read-only.

    The UI is meant to disable mutating controls upfront (the brief's
    "greyed with a quiet read-only hint"); this exception is the
    defensive net for paths that forget. Rolled back inside the
    enclosing gateway transaction so no partial write survives.
    """


__all__ = [
    "ReadOnlyLibraryError",
    "is_read_only",
    "read_only_holder",
    "reset_for_tests",
    "set_read_only",
]
