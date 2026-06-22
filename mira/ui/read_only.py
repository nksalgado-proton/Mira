"""Surface-level read-only helpers (spec/76 §B.1).

The shared "grey + tooltip" pattern every mutation control opts into
when :func:`mira.session.is_read_only` is True. The data layer's
defensive net (``EventGateway._touch`` + ``LibraryGateway.
_guard_read_only``) catches anything that slips past these helpers;
the UI is supposed to grey upfront so the user doesn't reach for a
control that's going to refuse.

The helpers accept either a :class:`QWidget` (buttons, menus, inputs)
or a :class:`QAction` (menubar / toolbar entries) — both expose
``setEnabled`` and ``setToolTip`` so the call site stays uniform.

Designed to be called ONCE at surface construction. For surfaces
that need to react to a mid-session lock loss (the spec/76 §A
heartbeat-failure path), call :func:`refresh_read_only_controls`
from the heartbeat handler with the widget tree — it walks every
registered control and re-applies the state.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional, Union

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QWidget

from mira.session import is_read_only, read_only_holder
from mira.ui.i18n import tr

log = logging.getLogger(__name__)

#: A control that can be disabled (QWidget OR QAction — both expose
#: ``setEnabled`` and ``setToolTip``). Typed wide so call sites don't
#: have to branch.
ReadOnlyTarget = Union[QWidget, QAction]


def read_only_hint() -> str:
    """The shared tooltip for any control greyed by read-only mode.

    Names the editing machine when the session has a holder
    (the common case — the §A.4 conflict dialog flow stamps one);
    falls back to a generic message when the holder is unknown
    (the §A heartbeat-loss path with a corrupt re-read)."""
    holder = read_only_holder()
    if holder is None:
        return tr("Read-only — the library is locked by another Mira.")
    return (tr("Read-only — the library is open for editing on "
               "{host}. Close that Mira to edit here.")
            .replace("{host}", holder.hostname))


def disable_if_read_only(
    target: ReadOnlyTarget, *, hint: Optional[str] = None,
) -> bool:
    """Disable ``target`` and apply the read-only tooltip when the
    session is open read-only. Returns ``True`` when the disable
    fired, ``False`` when the session is writeable (target untouched).

    Call this AT THE END of building any mutation control. The
    helper is idempotent — calling twice with the session writeable
    leaves the control enabled; calling twice with the session
    read-only is a no-op the second time.
    """
    if not is_read_only():
        return False
    target.setEnabled(False)
    target.setToolTip(hint or read_only_hint())
    return True


def refresh_read_only_controls(
    targets: Iterable[ReadOnlyTarget],
    *,
    hint: Optional[str] = None,
) -> int:
    """Re-apply the read-only state across a collection of controls.

    For surfaces that opt into the heartbeat-loss flow (spec/76 §A
    runtime drop-to-read-only): keep a list of every mutation
    control built, then call this on the list when the session flips.
    Returns the number of controls disabled.
    """
    if not is_read_only():
        # Re-enable previously disabled controls? Intentionally NO —
        # we never go back to writeable in the same session (the lock
        # was lost; the user must restart). Leave them alone.
        return 0
    n = 0
    for t in targets:
        try:
            t.setEnabled(False)
            t.setToolTip(hint or read_only_hint())
            n += 1
        except RuntimeError:
            # The C++ object underneath ``t`` may have been deleted
            # (page destroyed). Drop quietly — the next refresh will
            # see fewer entries.
            log.debug("read_only: skipped a deleted control")
    return n


__all__ = [
    "ReadOnlyTarget",
    "disable_if_read_only",
    "read_only_hint",
    "refresh_read_only_controls",
]
