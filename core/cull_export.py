"""Cull export engine — Stage C core (frozen docs/18 §"Export" +
§"Export reach + invocation", Nelson 2026-05-18).

The cull's single file-write point. Given a **manifest** of copy
operations (built by a separate resolver from the journal + the
day/style mapping — kept that way so this engine is pure and
exhaustively testable), copy each source into its destination with:

- **atomic write-then-rename** — copy to a temp file *in the
  destination dir* (same volume) then ``os.replace`` onto the final
  name, so a crash or an *Override* never leaves a half-file
  (CLAUDE.md invariant 7; the codebase's ``tmp + os.replace``
  idiom);
- **per-file collision policy** (Nelson's call): ``OVERRIDE``
  replaces the destination file; ``UNIQUE`` writes under
  ``stem (2).ext``, ``stem (3).ext`` … leaving the existing file
  untouched;
- **never touches the source** — non-destructive, always.

Filename is a *convenience, never the lineage anchor* (frozen
§Lineage): :func:`courtesy_filename` adds a ``DateTimeOriginal``
date-time prefix so date-naive importers (PTE) order
chronologically; a tool that strips it is **not** a correctness
failure. Qt-free.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)


class CollisionPolicy(Enum):
    """What to do when the destination file already exists."""

    OVERRIDE = "override"   # replace it (atomic; no half-file)
    UNIQUE = "unique"       # keep it; write under " (2)", " (3)"…


class ExportFileType(Enum):
    """Output form (frozen docs/18 §Export). **Original** = copy the
    RAW/HEIC/… as-is — the **Cull default** (and all the Nepal test
    needs). JPEG/TIFF (Process defaults) are a transform; the engine
    copies for ORIGINAL today and the JPEG/TIFF encode is a later
    increment — the dialog still offers the choice per the frozen
    spec so the contract is stable."""

    ORIGINAL = "original"
    JPEG = "jpeg"
    TIFF = "tiff"


@dataclass(frozen=True)
class ExportItem:
    """One copy operation. ``dest_dir`` is the absolute target
    directory (``…/01 - Culled/Dia N - desc/<Style>`` or, for a
    bracket, ``…/<Style>/<bracket_id>``); ``dest_name`` is the final
    filename (courtesy prefix already applied by the caller).

    ``exif_datetime`` (Model 3, docs/18 §"Model 3") — when set, the
    one-time clock correction is **materialised into the COPY's**
    ``DateTimeOriginal`` after it is written (source never touched).
    ``None`` = pass-through (Home / phone / no shift): the copy keeps
    the camera's recorded time, no EXIF write at all."""

    src: Path
    dest_dir: Path
    dest_name: str
    exif_datetime: Optional[datetime] = None


@dataclass
class ExportResult:
    written: list[Path] = field(default_factory=list)        # new
    overwritten: list[Path] = field(default_factory=list)     # OVERRIDE
    renamed: list[tuple[Path, Path]] = field(default_factory=list)  # UNIQUE
    skipped: list[tuple[Path, str]] = field(default_factory=list)   # src bad
    errors: list[tuple[Path, str]] = field(default_factory=list)
    retimed: list[Path] = field(default_factory=list)  # Model-3 EXIF baked
    # Idempotency counter (Nelson 2026-05-28): the destination
    # already existed AND was a hardlink to the same source inode.
    # The export skipped it as a no-op — the desired end state was
    # already on disk. Counted as success in ok_count.
    already_present: list[Path] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return (
            len(self.written)
            + len(self.overwritten)
            + len(self.renamed)
            + len(self.already_present)
        )


def courtesy_filename(
    original_name: str,
    capture_dt: Optional[datetime],
) -> str:
    """``original_name`` prefixed with ``YYYYmmdd_HHMMSS_`` from the
    capture time — a *downstream-ordering courtesy* only (frozen
    §Lineage; never mtime). No timestamp (derived/no-EXIF artifact)
    → the name is returned unchanged. Idempotent: a name that
    already carries this exact prefix is not double-prefixed."""
    if capture_dt is None:
        return original_name
    prefix = capture_dt.strftime("%Y%m%d_%H%M%S_")
    if original_name.startswith(prefix):
        return original_name
    return prefix + original_name


def _unique_target(dest_dir: Path, name: str) -> Path:
    """First free ``stem (n).ext`` in ``dest_dir`` (n starts at 2);
    ``name`` itself if free."""
    cand = dest_dir / name
    if not cand.exists():
        return cand
    p = Path(name)
    stem, suffix = p.stem, p.suffix
    n = 2
    while True:
        cand = dest_dir / f"{stem} ({n}){suffix}"
        if not cand.exists():
            return cand
        n += 1


def _same_inode(a: Path, b: Path) -> bool:
    """True iff ``a`` and ``b`` refer to the same on-disk file (same
    device + inode). Works for NTFS hardlinks on Windows via
    ``os.path.samefile`` (stat-based; not a content compare). Used
    by the export engine's idempotency guard to detect re-runs that
    would otherwise create UNIQUE-collision duplicates of a
    previously-materialised hardlink.

    Defensive: any OSError (file disappeared mid-check, permission
    issue) → False, treat as "not the same" and fall through to the
    normal collision-policy branch."""
    try:
        return a.samefile(b)
    except OSError:
        return False


def _atomic_copy(src: Path, target: Path, *, allow_link: bool = False) -> None:
    """Materialize ``src`` at ``target`` atomically.

    Two strategies:

    * **Hardlink** (``allow_link=True``, Model 3 v2 Nelson 2026-05-22):
      try ``os.link`` first — zero disk cost, identical inode. Use
      this in pure-consolidation phases (Cull-Export, Select-Export)
      where the destination is byte-identical to ``src``. Hardlinks
      fail with ``OSError`` when:

      - The source and target are on different filesystems (e.g.,
        cross-volume on Windows; cross-device on POSIX). We fall back
        to copy in that case.
      - The filesystem doesn't support hardlinks (FAT32, exFAT for
        some configs). Same fallback.

      The fallback uses the same atomic temp-rename copy path as
      ``allow_link=False``; behavior is observationally identical, just
      slower + uses real disk.

    * **Copy** (``allow_link=False``, default): copy to a temp file in
      the target's own directory (same volume → ``os.replace`` is a
      rename, not a cross-device copy), then ``os.replace`` onto the
      final name. Metadata preserved via ``shutil.copy2``. Use this in
      phases that need a real second copy (Process-Export's
      transformations; legacy events where Select-Export retimes
      EXIF; any path that's about to modify the destination bytes).

    Temp files are cleaned up on failure either way. Source is never
    modified.
    """
    if allow_link:
        try:
            # os.link doesn't overwrite an existing target; remove first.
            if target.exists():
                target.unlink()
            os.link(str(src), str(target))
            return
        except OSError as exc:
            # Cross-volume / unsupported filesystem — fall through to copy.
            log.debug(
                "Hardlink failed for %s → %s (%s); falling back to copy",
                src, target, exc,
            )
    tmp = target.with_name(f".{target.name}.part-{os.getpid()}")
    try:
        shutil.copy2(str(src), str(tmp))
        os.replace(str(tmp), str(target))
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def detect_collisions(items: Iterable[ExportItem]) -> list[ExportItem]:
    """Items whose destination file already exists — the host
    surfaces the Override / Unique choice only when this is
    non-empty (frozen: pre-detect; per destination file)."""
    return [
        it for it in items
        if (Path(it.dest_dir) / it.dest_name).exists()
    ]


def export_items(
    items: Iterable[ExportItem],
    *,
    collision: CollisionPolicy,
    allow_hardlinks: bool = False,
) -> ExportResult:
    """Execute the manifest. Source missing / not a file → skipped
    (never raises out of the loop — one bad item must not abort a
    1000-photo export); per-item OS errors are collected.

    Model 3 v2 (Nelson 2026-05-22): when ``allow_hardlinks=True``,
    items WITHOUT an ``exif_datetime`` retime use ``os.link`` (zero
    disk cost) instead of ``shutil.copy2``. Items WITH ``exif_datetime``
    still use copy + retime — the retime would back-propagate through
    a hardlink into the source, which we never want. Cross-volume /
    unsupported-filesystem cases auto-fall-back to copy per
    :func:`_atomic_copy`.

    Cull-Export passes ``allow_hardlinks=True`` always (it never
    retimes). Select-Export passes ``True`` for new (ingest-baked)
    events and ``False`` for legacy events that still need the
    Select-time retime.
    """
    result = ExportResult()
    # Model 3: (copy, corrected_dt) for keepers whose camera clock
    # was off — the EXIF is baked AFTER all copies, in one batch
    # (one exiftool pass for 1000+; never on the source).
    retime_ops: list[tuple[Path, datetime]] = []
    for it in items:
        src = Path(it.src)
        if not src.is_file():
            result.skipped.append((src, "source missing"))
            continue
        dest_dir = Path(it.dest_dir)
        # Hardlink-eligible: caller opted in AND this item carries no
        # post-copy EXIF rewrite (retime would corrupt the source via
        # the shared inode).
        link_this = allow_hardlinks and it.exif_datetime is None
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            final = dest_dir / it.dest_name
            existed = final.exists()
            # Idempotency guard (Nelson 2026-05-28, B-XXX). If the
            # destination already exists AND we're in hardlink-eligible
            # mode AND it shares the same inode as the source, then a
            # previous silent-sync already produced this file — we must
            # treat it as a no-op, NOT walk into the UNIQUE/OVERRIDE
            # branches and create a "(2)" duplicate. Repro: user kept
            # a photo at Cull → silent-sync hardlinked into 01-Culled,
            # ran Select → silent-sync hardlinked into 02-Selected;
            # went BACK to Cull, kept one more photo, returned to Select
            # → second sync re-found the same KEPT names, re-emitted
            # them as items, and the UNIQUE collision branch fired for
            # every already-present file. Every original file ended up
            # with an "(2)" twin in 02-Selected.
            if existed and link_this and _same_inode(src, final):
                result.already_present.append(final)
                continue
            # Stale hardlink (Nelson 2026-05-28, B-XXX). The
            # destination exists, we're in hardlink-eligible mode,
            # BUT the source and destination point at different
            # inodes. Repro: silent-sync materialised the hardlink in
            # a previous run; then the source was rewritten in place
            # (exiftool atomic rewrite during Adjust TZ creates a NEW
            # inode and renames it over the source path), so the
            # destination is now pointing at an orphan inode with
            # STALE content. UNIQUE collision would produce a "(2)"
            # twin of the corrected source; OVERRIDE would silently
            # do the wrong thing across collision-policy boundaries.
            # The correct semantic for silent-sync re-runs is
            # "replace the stale link with a fresh one to the canonical
            # source" — which IS OVERRIDE in this hardlink-eligible
            # case but routed BEFORE the policy branch so user-driven
            # exports (Process / Print / Standalone Cull) keep their
            # documented UNIQUE-collision behaviour.
            if existed and link_this:
                _atomic_copy(src, final, allow_link=link_this)
                result.overwritten.append(final)
                continue
            if existed and collision is CollisionPolicy.UNIQUE:
                final = _unique_target(dest_dir, it.dest_name)
                _atomic_copy(src, final, allow_link=link_this)
                result.renamed.append((src, final))
            elif existed:                       # OVERRIDE
                _atomic_copy(src, final, allow_link=link_this)
                result.overwritten.append(final)
            else:
                _atomic_copy(src, final, allow_link=link_this)
                result.written.append(final)
            if it.exif_datetime is not None:
                retime_ops.append((final, it.exif_datetime))
        except OSError as exc:
            log.warning("export failed for %s: %s", src, exc)
            result.errors.append((src, str(exc)))
    if retime_ops:
        # The copy is the deliverable; a retime failure must NOT lose
        # an exported file — it's recorded as an error but the file
        # stays (with the camera's original time). Source untouched.
        try:
            from core.exif_rewriter import rewrite_capture_times_batch
            outcomes = rewrite_capture_times_batch(retime_ops)
            for (dest, _dt), oc in zip(retime_ops, outcomes):
                if not getattr(oc, "error", ""):
                    result.retimed.append(dest)
                else:
                    result.errors.append(
                        (dest, f"retime failed: {oc.error}"))
        except Exception as exc:  # noqa: BLE001 — bake must not abort
            log.warning("EXIF retime batch failed: %s", exc)
            for dest, _dt in retime_ops:
                result.errors.append((dest, f"retime failed: {exc}"))
    return result
