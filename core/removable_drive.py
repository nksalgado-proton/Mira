"""Removable-drive detection — Stage D wipe gate (frozen 2026-05-19
scope expansion; docs/14 §"Destructive card offload").

Tiny pure utility: given a filesystem path, return whether it lives on
a removable device (SD card, USB stick, external HDD over USB). The
*only* consumer is the "Back up this card" surface's wipe-offer
decision — non-removable sources (a folder on the internal SSD, a
network share, a NAS path) NEVER receive the wipe offer, no matter
what. CLAUDE.md invariant #9 in code form.

Windows-only implementation (the app's target platform per
CLAUDE.md). The Windows ``GetDriveTypeW`` API returns one of:

    DRIVE_UNKNOWN     = 0
    DRIVE_NO_ROOT_DIR = 1
    DRIVE_REMOVABLE   = 2   ← only this counts as "removable"
    DRIVE_FIXED       = 3
    DRIVE_REMOTE      = 4
    DRIVE_CDROM       = 5
    DRIVE_RAMDISK     = 6

On any non-Windows platform (tests on Linux/macOS CI, dev laptops),
``is_removable`` returns ``False`` — the safer default, since the only
consumer is the wipe-offer gate and we'd rather over-protect than ever
silently wipe.

ctypes-based; no third-party deps.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


# Windows ``GetDriveType`` return code for SD cards / USB sticks /
# camera storage exposed as a removable volume. Exposed as a module
# constant so tests can patch around it without depending on a Windows
# host.
DRIVE_REMOVABLE = 2


def is_removable(path: Path | str) -> bool:
    """Return ``True`` when ``path`` lives on a removable Windows
    drive (SD card, USB stick, external USB HDD), ``False`` otherwise.

    Non-Windows hosts always return ``False`` — the wipe surface
    treats that as "no wipe offer", which is the safe behaviour for
    dev/test on Linux/macOS where we can't tell anyway.

    Paths that don't yet exist are resolved up the chain to find the
    nearest existing ancestor — handy when the UI is reasoning about
    a planned destination dir that's still on the same drive.
    """
    if sys.platform != "win32":
        return False
    drive = _resolve_drive_root(Path(path))
    if drive is None:
        return False
    return _get_drive_type(drive) == DRIVE_REMOVABLE


def _resolve_drive_root(path: Path) -> str | None:
    """Return the Windows drive-root string (``"E:\\"``) for ``path``,
    walking up to the nearest existing ancestor if needed. Returns
    ``None`` when no drive can be resolved (UNC path, malformed)."""
    try:
        candidate = path.resolve()
    except OSError:
        candidate = path
    # On Windows, ``Path.drive`` is "E:" for "E:\foo"; the API needs
    # the trailing backslash.
    drive = candidate.drive
    if not drive:
        # UNC paths (``\\server\share``) have empty .drive — treat as
        # non-removable (network share).
        return None
    return drive + "\\"


def _get_drive_type(drive_root: str) -> int:
    """Wrap the Win32 ``GetDriveTypeW`` call. Returns
    ``DRIVE_UNKNOWN`` (0) on any failure — fail-closed so the wipe
    surface doesn't get tricked by a transient API hiccup."""
    try:
        import ctypes
        return int(ctypes.windll.kernel32.GetDriveTypeW(drive_root))
    except Exception as exc:  # noqa: BLE001 — fail-closed by design
        log.warning("GetDriveTypeW failed for %s: %s", drive_root, exc)
        return 0
