"""F-011 — Standalone-cull copy engine.

The sidebar standalone-cull surface (Nelson 2026-05-25 — Path B
freeze, modernised onto ``BucketCullShell`` + ``IngestCullerPage``)
ends with a copy action: walk every bucket's journal, find KEPT
files, copy them to ``<dest>/<style>/<original_name>`` (flat — no
day or bucket hierarchy; the user picked one source and one
destination, they get one destination back).

Cross-volume by definition (source and dest are user-picked
separate paths in the standalone flow) so **always copies, never
hardlinks** — the hardlink shortcut in-event Cull / Select uses
doesn't help here.

Collision policy: ``(N)`` suffix like print-export, monotonically
incrementing. A re-run that finds the same file already at the
destination produces ``IMG_1234 (1).jpg``, then ``(2)``, etc. The
user always sees what shipped; nothing is overwritten silently.

Pure Python — Qt-free, off-thread safe. The dialog wraps the call
in a ``QProgressDialog`` (see ``ui/culler/standalone_cull_page.py``).

Spec: ``docs/18-culler-spec.md`` §"Standalone Cull".
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from core.bucket_navigator_model import BucketNode
from core.cull_state import STATE_KEPT, get_state
from core.genre import effective_genre, peek_auto_genre
from core.ingest_session import load_ingest_journal

log = logging.getLogger(__name__)


# Files for which no auto-genre was ever cached (the user never
# browsed them in this session, or the journal is empty) AND no
# explicit override land in this folder. The standalone-cull
# surface doesn't force-classify on Apply — that's a Select-phase
# concern.
_UNCLASSIFIED_DIR = "uncategorized"

# ProgressFn(message, current, total) — same shape the offload
# pipeline + the LRC re-import engine use.
ProgressFn = Callable[[str, int, int], None]


@dataclass(frozen=True)
class CopyItem:
    """One file the engine will copy. ``style`` is the
    destination-folder name; ``rel_dest`` is the
    ``<style>/<filename>`` path relative to the dest root (helpful
    for tests + summary)."""

    source: Path
    style: str
    rel_dest: Path


@dataclass
class CopyResult:
    """Outcome of a copy run.

    ``ok`` — paths that landed at the destination, in the form
    ``(source, written_dest)`` so the caller can show ``a.jpg ->
    portrait/a (2).jpg`` if the user wants the long form.
    ``skipped`` — paths the engine elected NOT to copy (e.g. the
    source vanished mid-run). ``errors`` — paths that raised
    during copy; the message is the exception's ``str``.
    """

    ok: list[tuple[Path, Path]] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return len(self.ok)


def build_copy_items(
    buckets: Sequence[tuple[BucketNode, Path]],
) -> list[CopyItem]:
    """Walk the buckets, read each one's journal, return the list
    of ``CopyItem``s for every KEPT file.

    ``buckets`` is a sequence of ``(node, journal_root)`` pairs:
    ``node`` is the bucket's `BucketNode` (gives us the file list
    + kind); ``journal_root`` is the directory whose
    ``cull_session.json`` carries the K/D + override state for
    that bucket. The caller (the standalone-cull page) owns the
    mapping — for standalone the journal scope is one root per
    bucket-id.

    A KEPT file's style resolves via the journal's effective_genre
    (override `??` cached auto). If the cache is empty AND no
    override exists, the file lands in ``uncategorized/`` — same
    fallback the Select-phase Export uses (docs/18 §Lineage).
    """
    items: list[CopyItem] = []
    for node, journal_root in buckets:
        try:
            journal = load_ingest_journal(journal_root)
        except Exception:                              # noqa: BLE001
            # A missing journal means nothing was marked. Skip.
            log.warning(
                "F-011: missing/unreadable journal at %s, skipping",
                journal_root,
            )
            continue
        for path in node.files:
            if get_state(journal, path.name) != STATE_KEPT:
                continue
            style = _style_for(journal, path.name)
            rel = Path(style) / path.name
            items.append(CopyItem(
                source=path, style=style, rel_dest=rel,
            ))
    return items


def _style_for(journal: dict, filename: str) -> str:
    """Resolve the destination style folder for ``filename``.
    override-or-cached-auto-or-``uncategorized``. Never raises."""
    cached = peek_auto_genre(journal, filename)
    auto = cached[0] if cached is not None else ""
    style = effective_genre(journal, filename, auto)
    return style or _UNCLASSIFIED_DIR


def copy_kept(
    items: Sequence[CopyItem],
    dest_root: Path,
    *,
    progress: Optional[ProgressFn] = None,
) -> CopyResult:
    """Copy every ``CopyItem`` to its ``rel_dest`` under
    ``dest_root``. Collisions resolve via the ``(N)`` suffix
    progression (``IMG (1).jpg``, ``(2)``, …). Always copy via
    :func:`shutil.copy2` (preserves metadata + mtime); never
    hardlink (standalone-cull is cross-volume by definition).
    Errors are collected per-item rather than aborting the run —
    a single unreadable file shouldn't kill a 200-file batch.

    Returns a :class:`CopyResult` summarising what happened."""
    result = CopyResult()
    dest_root = Path(dest_root)
    total = len(items)
    for i, item in enumerate(items, 1):
        if progress is not None:
            progress(item.source.name, i, total)
        if not item.source.is_file():
            result.skipped.append(
                (item.source, "source file no longer exists"),
            )
            continue
        target_dir = dest_root / item.style
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.errors.append((item.source, str(exc)))
            continue
        target = _resolve_collision(target_dir / item.source.name)
        try:
            shutil.copy2(item.source, target)
        except (OSError, shutil.SameFileError) as exc:
            result.errors.append((item.source, str(exc)))
            continue
        result.ok.append((item.source, target))
    return result


_SUFFIX_RE = re.compile(r"^(.*?)(?: \((\d+)\))?$")


def _resolve_collision(desired: Path) -> Path:
    """Return a writable destination path. If ``desired`` is free,
    return it as-is. If it collides, walk ``IMG (1).ext``,
    ``IMG (2).ext``, … until a free name is found.

    Handles a continuation case the user will hit on a repeat copy:
    if ``IMG (2).jpg`` already exists, the next collision becomes
    ``IMG (3).jpg``, not ``IMG (2) (2).jpg``. Same rule
    print-export uses, so the user sees one consistent naming
    pattern across the app."""
    if not desired.exists():
        return desired
    stem = desired.stem
    ext = desired.suffix
    base, current = _split_suffix(stem)
    n = current + 1 if current is not None else 1
    while True:
        candidate = desired.with_name(f"{base} ({n}){ext}")
        if not candidate.exists():
            return candidate
        n += 1


def _split_suffix(stem: str) -> tuple[str, Optional[int]]:
    """``IMG (2)`` → ``("IMG", 2)``; ``IMG`` → ``("IMG", None)``."""
    m = _SUFFIX_RE.match(stem)
    if not m:
        return stem, None
    base, n = m.group(1), m.group(2)
    return base, int(n) if n is not None else None
