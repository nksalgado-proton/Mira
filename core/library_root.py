"""Library root resolution + bootstrap pointer + validation
(spec/76 §B.4 + §B.2).

The library root is the user-chosen folder holding everything Mira
durably owns:

    <library_root>/
        .mira/                    machinery (hidden)
            mira.db
            settings.json
            events_index.json
            writer.lock
            logs/
        Collections/              spec/93 recipe library — Collections
        Recipes/                  spec/93 recipe library — Recipes
        <event folder>/...        each event with its own event.db + media

The single thing OUTSIDE the root is the **bootstrap pointer** — a tiny
JSON file holding ``{"library_root": "<path>"}``. The pointer answers
"where is the library?" before any library file is opened. Without it,
Mira can't know where the user kept their library — so the
**resolution order** is:

    1. ``MIRA_DATA_DIR`` env var (override for tests and custom installs)
    2. The pointer file
    3. ``None`` — first-run; the UI must ask the user to Create or Open

Reinstall recovery falls out of #2: a Windows reinstall wipes only the
pointer; the library on D: or the NAS is intact. Recovery is "install
Mira → Open existing library → browse to the root the user remembers".

Pure logic + filesystem. No Qt imports. The UI seam (first-run wizard)
calls into here; this module never reaches back.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


#: The hidden machinery folder inside the library root.
MIRA_DIRNAME = ".mira"

#: A small ``marker.json`` is written into ``.mira/`` on scaffold so
#: "Open existing library" can probe a candidate folder for the shape
#: before pointing the pointer at it.
MARKER_FILENAME = "marker.json"

#: Filename of the bootstrap pointer (lives OUTSIDE the library root).
POINTER_FILENAME = "config.json"


def bootstrap_pointer_path() -> Path:
    """Where the bootstrap pointer lives.

    Windows: ``%LOCALAPPDATA%\\Mira\\config.json``.
    Other:   ``~/.config/mira/config.json``.

    This is the ONE thing outside the library root. The pointer is
    disposable — a reinstall wipes it, and recovery re-creates it by
    asking the user to Open their existing library.
    """
    if platform.system() == "Windows":
        base = Path.home() / "AppData" / "Local" / "Mira"
    else:
        base = Path.home() / ".config" / "mira"
    return base / POINTER_FILENAME


def read_pointer() -> Optional[Path]:
    """Read the library root from the pointer file, or ``None`` if the
    file is missing / unreadable / malformed.

    Corrupt files are logged and treated as missing — the caller will
    re-prompt via first-run and rewrite the pointer.
    """
    p = bootstrap_pointer_path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("library_root: pointer at %s unreadable: %s", p, exc)
        return None
    try:
        blob = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("library_root: pointer at %s malformed: %s", p, exc)
        return None
    if not isinstance(blob, dict):
        log.warning("library_root: pointer at %s is not an object", p)
        return None
    raw = blob.get("library_root")
    if not isinstance(raw, str) or not raw:
        return None
    return Path(raw)


def write_pointer(root: Path) -> None:
    """Persist the library root to the pointer file, atomically.

    Atomic write-then-rename (invariant #6) so readers never see a
    half-written pointer. Parent directories are created on demand.
    """
    p = bootstrap_pointer_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        {"library_root": str(root)}, indent=2, ensure_ascii=False,
    ).encode("utf-8")
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    os.replace(str(tmp), str(p))


def clear_pointer() -> bool:
    """Remove the pointer file. Returns ``True`` if a file was removed,
    ``False`` if nothing was there. Used by tests + the "switch
    library" path."""
    p = bootstrap_pointer_path()
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("library_root: clear_pointer failed for %s: %s", p, exc)
        return False


def resolve_library_root() -> Optional[Path]:
    """Resolve the library root using the spec/76 §B.4 order:

        1. ``MIRA_DATA_DIR`` env override (tests + custom installs)
        2. The pointer file
        3. ``None`` (first-run)

    The ``MIRA_DATA_DIR`` name is preserved from
    :func:`mira.paths.user_data_dir` for backward compatibility — it
    overrides BOTH the legacy user-data dir AND the new library root,
    so the override stays a one-knob test seam.
    """
    override = os.environ.get("MIRA_DATA_DIR")
    if override:
        return Path(override)
    pointer = read_pointer()
    if pointer is not None:
        return pointer
    return None


# --------------------------------------------------------------------------- #
# Scaffold + marker
# --------------------------------------------------------------------------- #

MARKER_SCHEMA_VERSION = 1


def _set_windows_hidden(path: Path) -> None:
    """Best-effort: mark ``path`` with Windows hidden+system attributes.

    Non-fatal — failure (non-NT filesystem, permissions, etc.) just
    leaves the folder visible. The dot-prefix already conveys "hidden"
    convention on POSIX and is enough for Explorer to deprioritise.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        FILE_ATTRIBUTE_SYSTEM = 0x04
        attrs = FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
        result = ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs)
        if not result:
            err = ctypes.get_last_error()
            log.info(
                "library_root: SetFileAttributesW(%s) returned 0 (err=%d); "
                "leaving folder visible.", path, err,
            )
    except (AttributeError, OSError) as exc:
        log.info("library_root: hidden-attribute set skipped for %s: %s",
                 path, exc)


def scaffold_library(root: Path) -> None:
    """Create the library skeleton at ``root``.

    Creates ``<root>/``, ``<root>/.mira/``, ``<root>/Collections/``,
    ``<root>/Recipes/`` (idempotent), sets the Windows hidden+system
    attributes on ``.mira/``, and writes the marker file
    ``<root>/.mira/marker.json`` so "Open existing library" can probe
    a candidate folder for the shape.

    Idempotent — re-running on an existing scaffold is a no-op except
    for refreshing the marker.
    """
    root.mkdir(parents=True, exist_ok=True)
    mira_dir = root / MIRA_DIRNAME
    mira_dir.mkdir(parents=True, exist_ok=True)
    (root / "Collections").mkdir(parents=True, exist_ok=True)
    (root / "Recipes").mkdir(parents=True, exist_ok=True)
    _set_windows_hidden(mira_dir)

    marker = mira_dir / MARKER_FILENAME
    data = json.dumps(
        {"schema_version": MARKER_SCHEMA_VERSION, "kind": "mira_library"},
        indent=2, ensure_ascii=False,
    ).encode("utf-8")
    tmp = marker.with_suffix(marker.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    os.replace(str(tmp), str(marker))


def is_library_shape(candidate: Path) -> bool:
    """Return ``True`` when ``candidate`` looks like an existing Mira
    library — i.e. it has the ``.mira/`` machinery directory inside.

    Used by "Open existing library" to validate before writing the
    pointer; rejects empty folders and unrelated trees. The marker
    file is not required (an older or hand-built library may lack
    it); the ``.mira/`` directory IS, because the writer lock and the
    user store live there.
    """
    try:
        return (candidate / MIRA_DIRNAME).is_dir()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# One-shot migration: legacy user-data dir → <root>/.mira/
# --------------------------------------------------------------------------- #


def legacy_user_data_dir() -> Path:
    """The pre-spec/76 location of user data.

    Pre-relocation (``mira.paths.user_data_dir``) Mira writes settings,
    mira.db, events index, logs to:

    * Windows: ``%LOCALAPPDATA%\\Mira``
    * Other:   ``~/.mira``

    Returned verbatim so the migration step has a single, named
    source. Kept here (not imported from ``mira.paths``) so ``core/``
    stays free of ``mira/`` imports — one-way dep invariant.
    """
    override = os.environ.get("MIRA_DATA_DIR")
    if override:
        return Path(override)
    if platform.system() == "Windows":
        return Path.home() / "AppData" / "Local" / "Mira"
    return Path.home() / ".mira"


def migrate_legacy_data_dir(root: Path) -> bool:
    """One-shot move of the legacy user-data dir's contents into
    ``<root>/.mira/``.

    Mirrors :func:`mira.paths.migrate_legacy_user_data`: idempotent
    and non-destructive. Skips when:

    * ``MIRA_DATA_DIR`` is set (explicit override — caller knows what
      it's doing).
    * The legacy dir is the SAME path as ``<root>/.mira`` (the user
      pointed the library at the old AppData location — nothing to
      move).
    * The legacy dir doesn't exist.
    * ``<root>/.mira`` already holds content the migration would
      collide with (live install owns it). The marker file alone is
      treated as "still empty" so a freshly-scaffolded library
      doesn't block the migration.

    Returns ``True`` when at least one item was copied. The legacy
    directory is left in place so a rollback ``git switch`` can still
    launch the old binary against its original data.
    """
    if os.environ.get("MIRA_DATA_DIR"):
        return False
    legacy = legacy_user_data_dir()
    mira_dir = root / MIRA_DIRNAME
    try:
        if mira_dir.exists() and legacy.resolve() == mira_dir.resolve():
            return False
    except OSError:
        pass
    if not legacy.exists():
        return False
    mira_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in mira_dir.iterdir() if p.name != MARKER_FILENAME]
    if existing:
        return False
    copied = 0
    for item in legacy.iterdir():
        target = mira_dir / item.name
        if target.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
            copied += 1
            log.info("library_root: migrated %s -> %s", item, target)
        except OSError as exc:
            log.warning("library_root: migrate %s failed: %s", item, exc)
    if copied:
        log.info(
            "library_root: migrated %d item(s) from legacy user-data dir "
            "%s to %s; the legacy directory was left in place.",
            copied, legacy, mira_dir,
        )
    return copied > 0


# --------------------------------------------------------------------------- #
# Validation — spec/76 §B.2 (library on NAS or local disk)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of :func:`validate_root`. ``ok`` is False when one of
    the hard probes failed (path unreachable, unwritable, atomic
    rename fails). ``reasons`` carries fatal messages; ``warnings``
    carries non-fatal flags (e.g. mapped-drive letter — works today,
    but breaks the multi-PC story per spec/76)."""
    ok: bool
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _windows_is_mapped_network_drive(path: Path) -> bool:
    """``True`` when ``path`` resolves to a Windows mapped network
    drive (drive letter pointing at a remote share). Detected via
    ``GetDriveType(W:)``: DRIVE_REMOTE (4) = mapped network drive.

    Best-effort — failure returns ``False`` (no warning rather than
    a false alarm). UNC paths (``\\\\server\\share``) get a separate
    bookkeeping note in :func:`validate_root` — those are the
    PREFERRED multi-PC shape.
    """
    if os.name != "nt":
        return False
    try:
        drive, _ = os.path.splitdrive(str(path))
        if not drive or not drive.endswith(":"):
            return False
        import ctypes

        DRIVE_REMOTE = 4
        kernel32 = ctypes.windll.kernel32
        # GetDriveTypeW wants a NUL-terminated wide string ending
        # with a backslash (e.g. "Z:\\"). Splitting then padding
        # keeps us safe against odd inputs.
        root = drive + "\\"
        result = kernel32.GetDriveTypeW(root)
        return int(result) == DRIVE_REMOTE
    except (OSError, AttributeError) as exc:
        log.debug("library_root: mapped-drive probe failed for %s: %s",
                  path, exc)
        return False


def _is_unc_path(path: Path) -> bool:
    """``True`` for UNC paths (``\\\\server\\share\\…``) — the
    multi-PC-friendly shape on Windows. POSIX never matches."""
    raw = str(path)
    return raw.startswith("\\\\") or raw.startswith("//")


def _probe_writable_atomic_rename(root: Path) -> Optional[str]:
    """Round-trip a small file via the project's atomic
    write-then-rename pattern (invariant #6). Returns ``None`` on
    success or a human-readable error reason. Used by
    :func:`validate_root` as the deciding test for "library bytes
    can land here safely."""
    probe_dir = root / MIRA_DIRNAME
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"could not create {probe_dir}: {exc}"
    target = probe_dir / ".validate-probe"
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = b'{"probe": "library-root-validate"}\n'
    try:
        with open(tmp, "wb") as f:
            f.write(payload)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        os.replace(str(tmp), str(target))
    except OSError as exc:
        # Clean up the tmp if the replace didn't get there.
        try:
            tmp.unlink()
        except OSError:
            pass
        return f"write-then-rename failed in {probe_dir}: {exc}"
    try:
        actual = target.read_bytes()
        if actual != payload:
            return (
                f"probe round-trip mismatch in {probe_dir} — the share "
                "may be caching writes aggressively")
    except OSError as exc:
        return f"probe re-read failed in {probe_dir}: {exc}"
    finally:
        try:
            target.unlink()
        except OSError:
            pass
    return None


def validate_root(path: Path) -> ValidationResult:
    """Probe ``path`` for spec/76 §B.2 library suitability.

    Used by the first-run dialog (Create + Open doors) BEFORE writing
    the bootstrap pointer, and by any future "switch library" entry.
    Hard probes — failure means the path cannot host a library:

    * the path exists OR its parent does and is creatable;
    * a write-then-rename round-trip succeeds inside ``<path>/.mira/``
      (the same primitive every lock + settings write uses);
    * the round-trip's bytes survive a re-read (catches over-eager
      client-side caching on misbehaving SMB clients).

    Soft warnings — work today but flagged to the user:

    * mapped network drive on Windows (``Z:\\`` pointing at a share):
      drive letters can re-assign per PC, breaking the multi-PC
      portability spec/76 promises. UNC (``\\\\server\\share``) is
      strongly preferred.

    UNC paths get a positive note in ``warnings`` so the dialog can
    surface "good — multi-PC ready" rather than nothing.
    """
    reasons: List[str] = []
    warnings: List[str] = []

    p = Path(path)
    if not p.exists():
        parent = p.parent
        if not parent.exists():
            reasons.append(
                f"{p} does not exist and neither does its parent "
                f"{parent}")
            return ValidationResult(ok=False, reasons=reasons,
                                    warnings=warnings)
        # The parent exists — we'll create p as part of the probe.

    # Hard probe: writable + atomic rename + round-trip.
    err = _probe_writable_atomic_rename(p)
    if err is not None:
        reasons.append(err)
        return ValidationResult(ok=False, reasons=reasons,
                                warnings=warnings)

    # Soft warnings.
    if _windows_is_mapped_network_drive(p):
        warnings.append(
            f"{p} is on a mapped network drive — drive letters can "
            "re-assign per PC, which breaks the multi-PC library "
            "story. Prefer a UNC path (\\\\server\\share\\…).")
    elif _is_unc_path(p):
        warnings.append(
            f"{p} is a UNC path — good. The library will resolve "
            "the same way from every PC on the network.")

    return ValidationResult(ok=True, reasons=reasons, warnings=warnings)


__all__ = [
    "MIRA_DIRNAME",
    "MARKER_FILENAME",
    "POINTER_FILENAME",
    "ValidationResult",
    "bootstrap_pointer_path",
    "clear_pointer",
    "is_library_shape",
    "legacy_user_data_dir",
    "migrate_legacy_data_dir",
    "read_pointer",
    "resolve_library_root",
    "scaffold_library",
    "validate_root",
    "write_pointer",
]
