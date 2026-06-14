"""Diagnostic launcher — log every subprocess.Popen + os.spawn during MC startup.

Usage (from D:\\Projetos_Nelson\\Mira):
    python tools\\_diag_subprocess.py

Wraps subprocess.Popen and os.spawnv* so every child process is logged with
the command line + caller stack. The log lands at:
    %LOCALAPPDATA%\\Mira\\logs\\subprocess_diag.txt

Run, let the wizard appear, close it. Inspect the log to see what spawned
during startup. The two console flashes Nelson is observing should map to
two Popen entries here.
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

# Project root on sys.path so `import mira.*` resolves when this
# script is launched from anywhere.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _log_path() -> Path:
    base = Path(os.environ["LOCALAPPDATA"]) / "Mira" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "subprocess_diag.txt"


_LOG = _log_path()
_LOG.write_text("=== diag start ===\n", encoding="utf-8")


def _log(msg: str) -> None:
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


_original_popen_init = subprocess.Popen.__init__


def _patched_popen_init(self, args, *rest, **kwargs):
    cflags = kwargs.get("creationflags", 0)
    has_no_window = bool(cflags & getattr(subprocess, "CREATE_NO_WINDOW", 0))
    caller = "".join(traceback.format_stack(limit=6)[:-1])
    _log(f"--- Popen ---\nargs: {args!r}\ncreationflags: {cflags} (CREATE_NO_WINDOW={has_no_window})\ncaller:\n{caller}")
    return _original_popen_init(self, args, *rest, **kwargs)


subprocess.Popen.__init__ = _patched_popen_init

# os.spawn* family (rare but possible)
for name in ("spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe"):
    orig = getattr(os, name, None)
    if orig is None:
        continue

    def _wrap(orig_fn, fn_name):
        def wrapper(*args, **kwargs):
            _log(f"--- os.{fn_name} ---\nargs: {args!r}\nkwargs: {kwargs!r}")
            return orig_fn(*args, **kwargs)
        return wrapper

    setattr(os, name, _wrap(orig, name))

_log(f"=== patches installed; launching app ===\n")

# Now launch the real app
from mira.ui.app import main
raise SystemExit(main())
