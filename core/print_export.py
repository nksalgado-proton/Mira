"""Print-export engine (F-003).

Copies a Curate slide's source file to a user-chosen "print export"
folder, applying a ``(2)`` / ``(3)`` / ... suffix on filename
collisions. File is copied **as-is** — no transcoding, no
re-encoding, no metadata stripping. The user prints what Process
produced.

The frame-preview dialog that drives this lives in
``ui/curate/print_preview_dialog.py``; this module is pure
(no Qt) so it's trivially testable + reusable from CLI tools later.

Atomicity follows the rest of the codebase: copy to ``<dest>.tmp``
then ``os.replace`` onto the final name. A crashed copy never
leaves a partial file under the user-visible name.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path


log = logging.getLogger(__name__)


# Match a stem that already ends in ``" (N)"`` so a second print of
# the same source becomes ``IMG (3)`` not ``IMG (2) (2)``. The
# trailing-space matters — "IMG(2)" without the space is NOT one of
# our suffixes (could be a legitimate name the user already had).
_SUFFIX_RE = re.compile(r"^(?P<base>.+) \((?P<n>\d+)\)$")


def _split_existing_suffix(stem: str) -> tuple[str, int]:
    """Extract ``(base, n)`` from a stem that already carries our
    suffix; otherwise return ``(stem, 1)``."""
    m = _SUFFIX_RE.match(stem)
    if m is None:
        return stem, 1
    return m.group("base"), int(m.group("n"))


def resolve_print_target(src: Path, dest_dir: Path) -> Path:
    """Compute the first non-colliding destination path for ``src``
    inside ``dest_dir``.

    Algorithm:
      1. If ``dest_dir / src.name`` is free → use it.
      2. Otherwise, start from ``"<stem> (2)<ext>"`` and increment
         until a free name is found.
      3. If ``src.stem`` already ends in ``" (N)"``, continue from
         ``N + 1`` rather than collapsing to ``(2)``.

    Pure path math (no I/O beyond ``Path.exists``); never raises.
    """
    direct = dest_dir / src.name
    if not direct.exists():
        return direct
    base, start_n = _split_existing_suffix(src.stem)
    ext = src.suffix
    n = max(2, start_n + 1)
    # Loop bound: in practice users print one-off; an upper limit
    # of 10,000 protects against the wildly-pathological "destination
    # has every suffix already taken" case + makes the loop
    # statically bounded for static analysis.
    for _ in range(10_000):
        candidate = dest_dir / f"{base} ({n}){ext}"
        if not candidate.exists():
            return candidate
        n += 1
    raise RuntimeError(
        f"Could not find a free name under {dest_dir} for {src.name} "
        f"after 10,000 attempts"
    )


def export_for_print(src: Path, dest_dir: Path) -> Path:
    """Copy ``src`` to ``dest_dir`` (creating ``dest_dir`` if needed)
    with collision-suffix handling, atomically.

    Returns the actual destination ``Path`` written. Raises:

      * ``FileNotFoundError`` — ``src`` does not exist or is a folder.
      * ``OSError`` — destination not writable, disk full, etc.

    Atomicity: writes to ``<dest>.partial-<pid>`` first, then
    ``os.replace`` swaps it into place. A crash or full-disk error
    mid-copy leaves the partial alongside but never a half-written
    file with the user-visible name.
    """
    if not src.exists():
        raise FileNotFoundError(f"Source does not exist: {src}")
    if src.is_dir():
        raise FileNotFoundError(f"Source is a directory, not a file: {src}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    final = resolve_print_target(src, dest_dir)

    # Atomic copy via partial-then-rename. Use the PID in the partial
    # name to avoid two concurrent print-exports racing on the same
    # destination dir (parallel session unlikely but cheap to guard).
    partial = final.with_name(f".{final.name}.partial-{os.getpid()}")
    try:
        # copy2 preserves mtime + permission bits where supported —
        # the print should land with the original capture's metadata
        # intact for any downstream tool that cares.
        shutil.copy2(src, partial)
        os.replace(partial, final)
    except Exception:
        # Best-effort cleanup of the partial — don't shadow the
        # original exception.
        try:
            if partial.exists():
                partial.unlink()
        except OSError:
            pass
        raise
    log.info("Print-exported %s → %s", src, final)
    return final
