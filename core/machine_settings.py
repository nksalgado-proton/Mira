"""Per-install machine-local settings (spec/95 §C).

A tiny override file for the **handful** of settings that must NOT
roam between machines pointed at the same library. Today's only
key is ``display_quality`` (the spec/95 normal-view ceiling): a
desktop attached to a 4K monitor wants ``"high"`` while the laptop
sits on ``"balanced"``, with both pointing at one shared NAS
library. Putting the key in the roaming ``Settings`` would mean
last-writer-wins on the shared ``settings.rebuild.json``; this
module keeps it per-install instead.

**Where it lives.** Beside the library-root bootstrap pointer
(``core.library_root.bootstrap_pointer_path``):

* Windows: ``%LOCALAPPDATA%\\Mira\\machine.json``
* Other:   ``~/.config/mira/machine.json``

NEVER inside the library root; NEVER under ``MIRA_DATA_DIR``
(charter inv. 2 — paths route through settings/paths, not
hardcoded; the override env that retargets ``user_data_dir`` is
deliberately not consulted here so a test fixture can't accidentally
poison the production override).

**Robustness.** Missing file → defaults. Corrupt JSON → defaults
(the bad bytes are NOT preserved as ``.bak`` because the override is
disposable). Unknown enum value → default. The defaults are
documented at :data:`DEFAULT_DISPLAY_QUALITY`.

**No Qt** (charter inv. 8 — ``core/`` stays GUI-free).
"""
from __future__ import annotations

import json
import logging
import os
import platform
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

#: Filename of the machine-local override; sibling to
#: :data:`core.library_root.POINTER_FILENAME`. A reinstall wipes it
#: cleanly and the next read returns the default.
MACHINE_FILENAME = "machine.json"

#: Closed enum of accepted ``display_quality`` values (spec/95 §C —
#: the ``"native"`` / ``"unbounded"`` tier is explicitly forbidden by
#: the anti-lag invariant).
DISPLAY_QUALITY_VALUES = ("balanced", "high")

#: Default ``display_quality``: ``"balanced"`` (3840 px ceiling) — sharp
#: on a 4K monitor, cheap on a laptop (the laptop's target never
#: reaches the ceiling so the spec/95 §B settle-only original-decode
#: upgrade never fires).
DEFAULT_DISPLAY_QUALITY = "balanced"


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #


def machine_settings_path() -> Path:
    """Where the per-install override file lives. Same OS-local
    config dir as :func:`core.library_root.bootstrap_pointer_path`,
    so the two travel together (a reinstall that wipes the pointer
    also wipes the machine.json — and that's fine: both are
    disposable per-install state).

    Deliberately does NOT consult ``MIRA_DATA_DIR`` — the override
    env retargets the *library* root for tests, but the machine
    file must stay per-install even in tests so the production
    OS-local file is never touched. Tests that need to redirect
    this path monkeypatch :func:`machine_settings_path` directly.
    """
    if platform.system() == "Windows":
        base = Path.home() / "AppData" / "Local" / "Mira"
    else:
        base = Path.home() / ".config" / "mira"
    return base / MACHINE_FILENAME


# --------------------------------------------------------------------------- #
# Low-level read / write
# --------------------------------------------------------------------------- #


def _read_blob() -> Dict[str, Any]:
    """Read the machine.json envelope as a plain dict.

    Returns ``{}`` when the file is missing, unreadable, malformed,
    or not a JSON object — every recoverable failure mode maps to
    "no overrides recorded yet", which is what the readers expect.
    """
    p = machine_settings_path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.warning("machine_settings: %s unreadable (%s)", p, exc)
        return {}
    try:
        blob = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("machine_settings: %s malformed (%s)", p, exc)
        return {}
    if not isinstance(blob, dict):
        log.warning(
            "machine_settings: %s payload is not an object", p)
        return {}
    return blob


def _write_blob(blob: Dict[str, Any]) -> None:
    """Atomic write-then-rename (invariant #6) of the envelope.

    The parent dir is created on demand. ``fsync`` failure is
    tolerated (some filesystems / network mounts don't honour it)
    — ``os.replace`` is atomic at the rename level either way.
    """
    p = machine_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(blob, indent=2, ensure_ascii=False).encode("utf-8")
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    os.replace(str(tmp), str(p))


# --------------------------------------------------------------------------- #
# Display quality (spec/95)
# --------------------------------------------------------------------------- #


def read_display_quality() -> str:
    """Current machine-local ``display_quality`` (spec/95 §C). Always
    returns one of :data:`DISPLAY_QUALITY_VALUES` — missing file,
    corrupt JSON, or an unknown enum value all collapse to
    :data:`DEFAULT_DISPLAY_QUALITY`.
    """
    raw = _read_blob().get("display_quality")
    if isinstance(raw, str) and raw in DISPLAY_QUALITY_VALUES:
        return raw
    return DEFAULT_DISPLAY_QUALITY


def write_display_quality(value: str) -> None:
    """Persist a new ``display_quality`` (spec/95 §C). Validates the
    value against the closed enum so a typo can't disable the
    setting. Other keys in the envelope (if any future ones land)
    are preserved across the write.
    """
    if value not in DISPLAY_QUALITY_VALUES:
        raise ValueError(
            f"display_quality must be one of {DISPLAY_QUALITY_VALUES!r}, "
            f"got {value!r}"
        )
    blob = _read_blob()
    blob["display_quality"] = value
    _write_blob(blob)


__all__ = [
    "DEFAULT_DISPLAY_QUALITY",
    "DISPLAY_QUALITY_VALUES",
    "MACHINE_FILENAME",
    "machine_settings_path",
    "read_display_quality",
    "write_display_quality",
]
