"""Window-suppressed subprocess launches for bundled console tools.

LOAD-BEARING INVARIANT (frozen 2026-05-18, Nelson eyeball — the
"console flashes everywhere + it's slow" report): the app ships
console executables (ExifTool, ffmpeg/ffprobe, Helicon) and is
launched as a GUI process (``pythonw`` in dev, a windowed Nuitka
build in production). On Windows a GUI parent has **no console**, so
every child console process is given its **own** flashing console
window — once per EXIF batch / ffprobe / extract. That is the
flicker the user saw *and* a real per-call cost (a window create +
destroy on top of the process spawn).

Every bundled-exe spawn MUST go through :func:`run` (or pass
:func:`no_window_kwargs` to ``Popen``) so the child gets
``CREATE_NO_WINDOW``. Never call ``subprocess.run`` directly for a
bundled tool. Cross-platform safe — a no-op off Windows.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

# subprocess.CREATE_NO_WINDOW exists only on Windows. Resolve once.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def no_window_kwargs(existing: int = 0) -> dict[str, int]:
    """``creationflags`` kwargs that suppress a child console window.

    Returns ``{}`` off Windows (nothing to suppress) so callers can
    splat it unconditionally: ``Popen(cmd, **no_window_kwargs())``.
    ``existing`` lets a caller OR-in flags it already needs.
    """
    if sys.platform != "win32":
        return {}
    return {"creationflags": existing | _CREATE_NO_WINDOW}


def run(*args: Any, **kwargs: Any) -> "subprocess.CompletedProcess":
    """``subprocess.run`` that never flashes a console window.

    Drop-in for ``subprocess.run`` at every bundled-exe call site.
    Injects ``CREATE_NO_WINDOW`` on Windows unless the caller already
    set ``creationflags`` (then it is OR-ed in, not clobbered). All
    other args/kwargs pass straight through.
    """
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            int(kwargs.get("creationflags", 0)) | _CREATE_NO_WINDOW
        )
    return subprocess.run(*args, **kwargs)


_SUPPRESSION_INSTALLED = False


def install_window_suppression() -> None:
    """Monkey-patch ``subprocess.Popen`` to set ``CREATE_NO_WINDOW`` on
    every child process — project code AND third-party libraries alike.

    MC is a GUI app launched via ``pythonw`` (dev) or a windowed Nuitka
    build (production); it has no console of its own. Any child process
    spawned WITHOUT ``CREATE_NO_WINDOW`` gets its own fresh console
    window that flashes briefly. :func:`run` and :func:`no_window_kwargs`
    cover the project's own ``proc``-routed call sites, but transitive
    deps spawn children without going through ``proc`` — e.g.
    ``imageio_ffmpeg.get_ffmpeg_exe()`` runs ``ffmpeg -version`` at
    import time via plain ``subprocess.check_call``.

    Installing this once at app startup catches every spawn from then
    on. Safe because MC never interacts via console; the only thing
    suppressed is a pop-up console window that would have been empty.
    Idempotent (re-calls are no-ops). No-op off Windows.

    Call once at the top of :func:`mira.ui.app.main`, BEFORE
    any deferred imports that might do module-load-time probes.
    """
    global _SUPPRESSION_INSTALLED
    if _SUPPRESSION_INSTALLED or sys.platform != "win32":
        return
    _SUPPRESSION_INSTALLED = True

    _original_popen_init = subprocess.Popen.__init__

    def _patched_popen_init(self, args, *rest, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["creationflags"] = (
            int(kwargs.get("creationflags", 0)) | _CREATE_NO_WINDOW
        )
        return _original_popen_init(self, args, *rest, **kwargs)

    subprocess.Popen.__init__ = _patched_popen_init  # type: ignore[method-assign]
