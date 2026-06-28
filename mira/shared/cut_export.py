"""Cut export — the handoff folder (spec/81 §4-§5, spec/61 §5.2).

Materializes a Cut to a target directory: **linked** media (NTFS hardlink,
copy fallback — never byte-duplicates by choice), named with a sequence prefix
so plain filename sort = chronological show order; **separator** images
rendered into the same sequence (when the Cut's ``separators`` flag is on);
an **``audio/``** subdir with the playlist; and **overlay** handling driven
by the Cut's selected fields (spec/81 §3.1, spec/153).

**The target is a PARAMETER (spec/81 §5).** Composition travels with the Cut;
the destination does NOT. ``export_cut`` takes ``target`` defaulting to
``<event_root>/Cuts/<tag>/`` (no absolute path is ever stored on the Cut —
charter invariant #2). Re-export reproduces identical bundle *content*; where
it lands is a re-confirmed default.

**Overlays cost no budget** (spec/81 §3.1) and change no membership. The
selected fields ride the generated ``.pte`` as separate ``:Text`` objects
(spec/153); here, members stay **hardlinks** and we additionally embed the
*where* IPTC tags (City / Sub-location / Country) into the linked JPEG via
the bundled ExifTool so the location travels with the file (the technical
EXIF is already there). A frame with no *where* data stays a pure link.
Burn-in is retired. In-app Play draws overlays live (the play path, not here).

Export is a SNAPSHOT: the Cut stays live; by default a name collision on
disk gets a ``(2)``-style disambiguator instead of touching the old folder
(spec/148 Keep both). Pass ``overwrite_existing=True`` (spec/148 Overwrite)
to materialise straight into the existing ``<tag>/`` — its contents are
cleared first so the bundle is a clean replacement and the generated
``.pte`` carries correct ``<tag>/`` paths from the start. Stamps
``last_exported_at`` when done.

No Qt imports here (charter invariant 8 posture for the data layer).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from core import audio_library, cut_budget, cut_overlay
from core import cut_names
from mira.shared.cut_session import SessionFile, files_from_lineage

log = logging.getLogger(__name__)

#: target -> None; renders the day's separator card into ``target``.
SeparatorWriter = Callable[[Path, Optional[int]], None]

#: relpath -> FrameProvenance; resolves a member's provenance facts for
#: overlays (injected by the gateway/caller — pure data, no Qt).
ProvenanceResolver = Callable[[str], "cut_overlay.FrameProvenance"]

#: (path, iptc_tags) -> bool; embeds the *where* IPTC tags into ``path`` in
#: place (True on success). Injected so this module stays Qt-free AND
#: subprocess-free in tests. The default writer uses the bundled ExifTool.
IptcWriter = Callable[[Path, Dict[str, str]], bool]

#: spec/105 §3 — source_item_id -> origin_relpath; resolves a Cut
#: member to the source item's pristine original under
#: ``<event_root>/Original Media/<origin_relpath>``. Injected so this
#: module stays Qt-free and gateway-agnostic; the default reads from
#: the supplied gateway via ``gateway.item(source_item_id)``.
OriginalResolver = Callable[[Optional[str]], Optional[str]]


@dataclass
class ExportResult:
    folder: Path
    linked: int = 0
    copied: int = 0                      # hardlink fallback copies
    iptc_written: int = 0                # where-IPTC writes (location → file)
    separators: int = 0
    audio_files: int = 0
    audio_short: bool = False            # library couldn't cover the show
    # spec/105 §3 — `Original Media/` subdir counts.
    originals_linked: int = 0
    originals_copied: int = 0
    missing: List[str] = field(default_factory=list)
    missing_originals: List[str] = field(default_factory=list)
    # spec/158 — "Only new files" re-export: members skipped because the
    # folder's manifest already records them as materialized here.
    skipped: int = 0


def _fresh_folder(base: Path) -> Path:
    """The snapshot folder: ``<target>``, or ``<target> (2)`` etc. when a
    previous export (or a renamed Cut's history) already owns it."""
    if not base.exists():
        return base
    n = 2
    while True:
        candidate = base.with_name(f"{base.name} ({n})")
        if not candidate.exists():
            return candidate
        n += 1


def _clear_folder_contents(folder: Path) -> None:
    """spec/148 Overwrite — empty ``folder`` in place so an Overwrite
    export lands on a clean slate without dropping the folder itself
    (Windows file handles, watchers, the FS volume identity all stay).
    Files are unlinked one by one; subdirectories (``audio/``,
    ``Original Media/`` from prior runs) are rmtree'd recursively."""
    for child in folder.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            try:
                child.unlink()
            except IsADirectoryError:                              # noqa: PERF203
                shutil.rmtree(child)


#: spec/158 (Nelson 2026-06-27) — sidecar manifest written into every
#: export folder so an "Only new files" re-export knows exactly which
#: Cut members were already materialized THERE. Tied to the folder (not
#: the Cut row): deleting the folder correctly means "everything is new
#: again," and exporting to a different folder starts fresh.
_MANIFEST_NAME = ".mira-cut-export.json"
_MANIFEST_VERSION = 1


def _read_export_manifest(folder: Path, *, cut_id: str) -> dict:
    """Read the sidecar export manifest. Returns ``{}`` when absent,
    unreadable, malformed, or written by a DIFFERENT Cut (a folder
    re-used for another Cut is not an additive base for this one)."""
    try:
        data = json.loads((folder / _MANIFEST_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or str(data.get("cut_id")) != str(cut_id):
        return {}
    return data


def _write_export_manifest(
    folder: Path, *, cut_id: str, members, max_seq: int,
) -> None:
    """Atomically write the sidecar manifest (charter invariant #6 —
    write-then-rename). Best-effort: a failure logs but never fails the
    export the user came for."""
    payload = {
        "version": _MANIFEST_VERSION,
        "cut_id": str(cut_id),
        "members": sorted(set(members)),
        "max_seq": int(max_seq),
    }
    tmp = folder / (_MANIFEST_NAME + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), "utf-8")
        os.replace(tmp, folder / _MANIFEST_NAME)
    except OSError:
        log.exception("could not write export manifest in %s", folder)
        try:
            tmp.unlink()
        except OSError:
            pass


def _link_or_copy(src: Path, dst: Path) -> bool:
    """Hardlink; copy when the volume/file refuses. True = linked."""
    try:
        os.link(src, dst)
        return True
    except OSError:
        shutil.copy2(src, dst)
        return False


def _place(src: Path, dst: Path, *, force_copy: bool) -> bool:
    """spec/105 §5 — one placement helper for media, originals AND
    audio. ``force_copy=True`` forces an independent ``shutil.copy2``
    (the export survives moving / deleting the source event);
    ``False`` hardlinks and falls back to copy on a cross-volume
    OSError. Returns ``True`` when the result was linked, ``False``
    when copied — symmetric with the legacy ``_link_or_copy``."""
    if force_copy:
        shutil.copy2(src, dst)
        return False
    try:
        os.link(src, dst)
        return True
    except OSError:
        shutil.copy2(src, dst)
        return False


def _same_volume(a: Path, b: Path) -> bool:
    """True when ``a`` and ``b`` resolve to the same filesystem volume.
    Used to pick the volume-aware target so hardlinks survive: a Cut
    home on the event's own volume can always link the member bytes.

    Falls back to ``True`` (assume same volume) when a path doesn't
    exist yet — the deepest existing ancestor's volume is read instead,
    so a freshly chosen but un-created folder still reads correctly.
    On Windows that's the drive letter; on POSIX the device id."""
    def _existing_ancestor(p: Path) -> Path:
        cur = Path(p)
        while cur != cur.parent and not cur.exists():
            cur = cur.parent
        return cur
    try:
        aa = _existing_ancestor(a)
        bb = _existing_ancestor(b)
    except OSError:
        return True
    if os.name == "nt":
        # Drive letter comparison — cheap and matches what the user
        # sees (D:\ vs C:\). UNC paths fall back to st_dev below.
        da = aa.drive.upper() if aa.drive else ""
        db = bb.drive.upper() if bb.drive else ""
        if da and db:
            return da == db
    try:
        return aa.stat().st_dev == bb.stat().st_dev
    except OSError:
        return True


def default_target(event_root: Path, tag: str) -> Path:
    """Legacy entry point — kept for back-compat with callers that
    haven't migrated to :func:`resolve_event_cut_target` yet (spec/105
    §2). Equivalent to the off-volume / external-event branch:
    ``<event_root>/Cuts/<tag>/``. The Cut never stores the result;
    it is recomputed each export and offered as the default."""
    return Path(event_root) / "Cuts" / tag


def resolve_event_cut_target(
    *,
    event_root: Path,
    event_name: str,
    cut_tag: str,
    library_root: Optional[Path] = None,
    cuts_export_root: Optional[Path] = None,
) -> Path:
    """spec/105 §2 — volume-aware default for a per-event Cut.

    The layout keeps hardlinks working wherever an event physically
    lives:

    * ``cuts_export_root`` set → honoured verbatim:
      ``<cuts_export_root>/<event slug>/<cut slug>/``. The dialog
      warns when this is off-volume from the event's media (§5
      copies, with the §6 notice).
    * ``cuts_export_root`` blank, event on the SAME volume as
      ``library_root`` → ``<library_root>/Cuts/<event slug>/<cut
      slug>/``. One discoverable home; links work.
    * ``cuts_export_root`` blank, event on a DIFFERENT volume from
      ``library_root`` (the external-event case under
      ``event_root_abs``) → ``<event_root>/Cuts/<cut slug>/`` — the
      event's own volume, so links still work.

    The Cut never stores the result — the caller still recomputes
    and offers it as a default the user can override (charter #2 +
    spec/81 §5)."""
    event_root = Path(event_root)
    event_slug = cut_names.slugify_event_name(event_name or "")
    cut_slug = cut_tag
    if cuts_export_root is not None and str(cuts_export_root).strip():
        return Path(cuts_export_root) / event_slug / cut_slug
    if library_root is not None and _same_volume(event_root, library_root):
        return Path(library_root) / "Cuts" / event_slug / cut_slug
    return event_root / "Cuts" / cut_slug


def resolve_cross_event_cut_target(
    *,
    cut_tag: str,
    library_root: Path,
    cuts_export_root: Optional[Path] = None,
) -> Path:
    """spec/105 §2 — default for a CROSS-event Cut.

    Cross-event Cuts span several event roots / possibly volumes, so
    some members copy regardless. The home is the library:
    ``<library_root>/Cuts/Cross-event/<cut slug>/`` (or under
    ``cuts_export_root`` when set).

    Source-volume hardlinks still apply per member where they can —
    same-volume members link, off-volume members copy. The dialog
    surfaces the mix as part of the §6 cross-volume notice."""
    if cuts_export_root is not None and str(cuts_export_root).strip():
        return Path(cuts_export_root) / "Cross-event" / cut_tag
    return Path(library_root) / "Cuts" / "Cross-event" / cut_tag


def _dedup_filename(parent: Path, name: str) -> Path:
    """Return ``parent/name`` when it's free, else ``parent/<stem>_2.<ext>``,
    ``_3.<ext>``, …  Same shape as the ingest `_2` dedup so the
    Originals folder reads predictably."""
    candidate = parent / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    n = 2
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _default_original_resolver(gateway) -> OriginalResolver:
    """Default `OriginalResolver` — `gateway.item(source_item_id).
    origin_relpath`. Returns `None` when the id is missing OR the
    gateway can't find the item (export still proceeds; the
    member's `Original Media/` entry just goes into
    ``result.missing_originals``)."""
    def resolve(source_item_id: Optional[str]) -> Optional[str]:
        if not source_item_id:
            return None
        try:
            item = gateway.item(source_item_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "original_resolver: gateway.item(%s) failed", source_item_id)
            return None
        if item is None:
            return None
        return getattr(item, "origin_relpath", None) or None
    return resolve


def write_audio_playlist(
    *,
    dest: Path,
    music_category: Optional[str],
    photo_count: int,
    separator_count: int,
    video_ms_total: int,
    photo_s: float,
    audio_root: Optional[str],
    audio_tracks: Optional[Sequence[audio_library.AudioTrack]] = None,
    copy_mode: bool = False,
    rng=None,
    # spec/152 §3 — transition + opener accounted for in the show
    # length so the playlist runs to the same total PTE shows. Defaults
    # are zero so callers that pre-date 152 are unaffected.
    transition_ms: int = 0,
    opener_count: int = 0,
) -> tuple[int, bool]:
    """spec/112 — build + place one Cut's soundtrack into ``dest/audio/``.

    Shared between :func:`export_cut` (per-event) and
    :func:`mira.shared.cross_event_cut_export.export_cross_event_cut`
    (cross-event) so the per-event and cross-event paths build
    soundtracks identically (spec/112: the cross-event exporter had no
    audio block at all — verified by spec/112 §1 — so the fix is to
    share this one).

    * No ``music_category`` → no audio dir; returns ``(0, False)``.
    * No matching tracks → returns ``(0, True)`` when the show has
      length (the empty playlist is "short" of the show), ``(0, False)``
      for a zero-length show.
    * Otherwise → write the playlist via :func:`_place`, mirroring the
      ``copy_mode`` switch (spec/105 §5).

    Returns ``(audio_files, audio_short)`` so the caller can stamp its
    own result counters."""
    if not music_category:
        return (0, False)
    show_s = cut_budget.ShowTotals(
        photo_count=photo_count,
        separator_count=separator_count,
        video_ms_total=video_ms_total,
        opener_count=opener_count,
    ).seconds(photo_s, transition_ms / 1000.0)
    if audio_tracks is not None:
        tracks = list(audio_tracks)
    elif audio_root:
        tracks = [
            t for t in audio_library.scan_library(Path(audio_root))
            if t.kind is audio_library.AudioKind.MUSIC
            and t.mood == music_category
        ]
    else:
        tracks = []
    playlist = audio_library.build_playlist(tracks, show_s, rng=rng)
    if not playlist:
        return (0, show_s > 0)
    audio_dir = dest / "audio"
    audio_dir.mkdir(exist_ok=True)
    audio_files = 0
    for i, t in enumerate(playlist, start=1):
        target_path = audio_dir / f"{i:02d}_{t.path.name}"
        try:
            _place(t.path, target_path, force_copy=copy_mode)
            audio_files += 1
        except OSError:
            log.exception("audio link failed for %s", t.path)
    covered = sum(t.duration_seconds for t in playlist)
    return (audio_files, covered < show_s)


def _exiftool_iptc_writer(path: Path, tags: Dict[str, str]) -> bool:
    """The default embedded-mode IPTC writer: stamp the *where* tags into a
    file IN PLACE via the bundled ExifTool (``-overwrite_original``, atomic).
    Returns True on success. Lazy-imports the tool seam so tests inject their
    own writer and this module stays import-light + Qt-free."""
    if not tags:
        return True
    try:
        import subprocess
        from core.exif_reader import _get_exiftool_path
        from core.proc import run as _run_hidden
        args = [str(_get_exiftool_path()), "-overwrite_original",
                "-charset", "filename=UTF8"]
        for tag, value in tags.items():
            args.append(f"-{tag}={value}")
        args.append(str(path))
        cp = _run_hidden(args, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60)
        if cp.returncode != 0:
            log.warning("IPTC write failed for %s: %s", path, cp.stderr.strip())
            return False
        return True
    except Exception:  # noqa: BLE001 — a failed overlay write never blocks export
        log.exception("IPTC write raised for %s", path)
        return False


def export_cut(
    gateway,
    cut,
    *,
    event_root: Path,
    target: Optional[Path] = None,
    separators_on: Optional[bool] = None,
    separator_writer: Optional[SeparatorWriter] = None,
    opener_writer: Optional[Callable[[Path], None]] = None,
    audio_root: Optional[str] = None,
    audio_tracks: Optional[Sequence[audio_library.AudioTrack]] = None,
    provenance_resolver: Optional[ProvenanceResolver] = None,
    iptc_writer: Optional[IptcWriter] = None,
    include_originals: bool = False,
    copy_mode: bool = False,
    overwrite_existing: bool = False,
    only_new: bool = False,
    original_resolver: Optional[OriginalResolver] = None,
    rng=None,
    # spec/152 §3 — global crossfade transition (ms) the audio
    # playlist + PTE generator both account for. Defaults to 0 so
    # pre-152 callers see the legacy hard-cut budget; UI callers
    # pass the Settings value (``default_transition_ms``).
    transition_ms: int = 0,
) -> ExportResult:
    """Materialize one Cut (spec/81 §4-§5, spec/105 §3-§5).

    ``target`` defaults to ``<event_root>/Cuts/<tag>/`` (the Cut stores
    no path) — callers wanting the spec/105 §2 volume-aware default
    pass it in via :func:`resolve_event_cut_target` (so the resolver
    can see ``library_root`` + ``cuts_export_root`` without coupling
    this module to settings). ``separators_on`` defaults to the Cut's
    own ``separators`` flag. Overlays follow the Cut's ``overlay_fields``:
    the fields ride the generated ``.pte`` as separate text (spec/153) and
    the *where* IPTC is embedded into the linked file (members stay links).
    Overlays cost no budget. ``audio_tracks`` overrides the library scan
    (tests + pre-scanned callers).

    spec/105 §3 — ``include_originals=True`` resolves each member's
    source item to its ``origin_relpath`` (via ``original_resolver``,
    default = the supplied gateway) and places it under
    ``<dest>/Original Media/`` with the same link/copy switch as the
    show files. Members with no source (opener / separators / audio)
    skip this stage. Missing source files land in
    ``result.missing_originals``, never a crash.

    spec/105 §5 — ``copy_mode=True`` forces ``shutil.copy2`` for media,
    originals AND audio (the show is then independent of the source
    event's lifecycle). Default ``False`` hardlinks with a copy
    fallback on cross-volume OSError.

    spec/148 — ``overwrite_existing=True`` materialises into ``base``
    even when it already exists (its prior contents are cleared first),
    so the new bundle's ``.pte`` carries correct ``<tag>/`` paths and
    folders stop accumulating ``(2)`` siblings. ``False`` (the default)
    keeps the snapshot behaviour: ``_fresh_folder`` disambiguates to
    ``<tag> (2)/``, the old bundle untouched. The UI confirms the
    destructive replace before passing ``True``; the data layer trusts
    the flag."""
    event_root = Path(event_root)
    files = files_from_lineage(gateway, gateway.cut_member_files(cut.id))
    if separators_on is None:
        separators_on = bool(getattr(cut, "separators", True))
    base = Path(target) if target is not None else default_target(event_root, cut.tag)
    # spec/158 — "Only new files" additive re-export. Land in ``base``
    # (the same folder) and read its sidecar manifest to learn which
    # members are already materialized here; we then write ONLY the
    # members not yet present, leaving existing files untouched. With no
    # manifest for THIS Cut (never exported / folder gone / re-used for
    # another Cut) every member is new, so it degenerates to a normal
    # full export into ``base``.
    prior: dict = {}
    # spec/158 (Nelson 2026-06-28 data-loss fix) — a multiset of the
    # show-files ACTUALLY on disk, keyed by name minus the ``NNN_``
    # sequence prefix. A manifest claim that a member is "already here"
    # is VERIFIED against this before we skip it, so we can never mark a
    # file copied that isn't really on disk (the Repeated-cluster bug:
    # the old loose ``endswith`` match skipped brand-new members against
    # a different same-suffix file and never copied them).
    disk_basenames: "Counter[str]" = Counter()
    max_existing_seq = 0

    def _strip_seq(name: str) -> str:
        head, sep, rest = name.partition("_")
        return rest if (sep and head.isdigit()) else name

    if only_new:
        dest = base
        if dest.exists():
            prior = _read_export_manifest(dest, cut_id=cut.id)
            max_existing_seq = int(prior.get("max_seq", 0))
            for p in dest.iterdir():
                if not p.is_file() or p.name.startswith("."):
                    continue
                disk_basenames[_strip_seq(p.name)] += 1
                head = p.name.split("_", 1)[0]
                if head.isdigit():
                    max_existing_seq = max(max_existing_seq, int(head))
    elif overwrite_existing:
        # spec/148 — write into base; clear any prior bundle so the
        # new export is a clean replacement (no leftover members from
        # a smaller prior version).
        dest = base
        if dest.exists():
            _clear_folder_contents(dest)
    else:
        dest = _fresh_folder(base)
    dest.mkdir(parents=True, exist_ok=True)
    # ``incremental`` is the additive sub-case of ``only_new``: a valid
    # prior manifest exists, so opener / separators / audio are NOT
    # re-emitted (they came from the first full export) and already-
    # materialized members are skipped. A fresh ``only_new`` (no
    # manifest) runs the full path below.
    # Incremental = additive re-export onto an existing bundle (there are
    # show files already on disk): skip the opener / separators / audio
    # (they're already here) and add only members not yet present. A
    # truly fresh ``only_new`` (empty folder) runs the full path below.
    incremental = only_new and bool(disk_basenames)
    result = ExportResult(folder=dest)
    # Members confirmed present in THIS folder after the run — seeds the
    # manifest write at the end. Built FRESH (not from ``already``) so a
    # stale/poisoned prior manifest can't carry a phantom entry forward:
    # an entry only lands here once we've either verified its file on
    # disk (skip path) or actually copied it (write path).
    present_members: set = set()

    overlay_fields = list(gateway.cut_overlay_fields(cut))
    iptc_writer = iptc_writer or _exiftool_iptc_writer
    resolve_original = (
        original_resolver if original_resolver is not None
        else _default_original_resolver(gateway))

    def _provenance(relpath: str) -> cut_overlay.FrameProvenance:
        if provenance_resolver is not None:
            return provenance_resolver(relpath)
        return cut_overlay.FrameProvenance()

    # Incremental runs continue numbering after the prior bundle's last
    # sequence (from the manifest AND a disk scan) so new files append
    # cleanly without renaming what PTE (or the user) already references.
    seq = max_existing_seq if incremental else 0
    last_day: object = object()
    totals_photos = totals_seps = 0
    totals_video_ms = 0
    # Opener (title slide) always rides when the Cut has at least one
    # file and the caller provided an ``opener_writer``. Decoupled from
    # ``separators_on`` (which now controls per-day cards only) so a
    # Cut with separators off still gets its initial header — the
    # user's "Separators OFF should keep the title card" report.
    if opener_writer is not None and files and not incremental:
        seq += 1
        target_path = dest / f"{seq:03d}_opener.jpg"
        try:
            opener_writer(target_path)
            result.separators += 1
            totals_seps += 1
        except Exception:  # noqa: BLE001 — a failed card never blocks
            log.exception("opener render failed for %s", target_path)
            seq -= 1
    for f in files:
        # spec/158 — Only-new-files skips a member ONLY when the manifest
        # records it AND its file is VERIFIED on disk (exact show-name,
        # consumed 1:1). Anything unverified — a new member, or a stale
        # manifest entry whose file is missing — falls through and is
        # copied. This can never mark a file "copied" that isn't really
        # there (the data-loss fix) and self-heals a poisoned manifest.
        if only_new:
            nm = _strip_seq(Path(f.export_relpath).name)
            if disk_basenames.get(nm, 0) > 0:
                # A file with this exact show-name is really on disk —
                # consume it 1:1 and skip the copy. Disk presence (not
                # the manifest) is the authority, so a poisoned manifest
                # can never make us skip a member whose file is absent.
                disk_basenames[nm] -= 1
                result.skipped += 1
                present_members.add(f.export_relpath)
                continue
            # Not on disk → copy it (new member, or self-heal a stale
            # manifest entry whose file went missing).
        # spec/158 — day-separator cards are part of the first full
        # bundle; an incremental add appends media only (re-run
        # Overwrite to refresh separators / opener / audio).
        if separators_on and not incremental and f.day_number != last_day:
            last_day = f.day_number
            if separator_writer is not None:
                seq += 1
                day_tag = "undated" if f.day_number is None else f"day{f.day_number}"
                target_path = dest / f"{seq:03d}_{day_tag}.jpg"
                try:
                    separator_writer(target_path, f.day_number)
                    result.separators += 1
                    totals_seps += 1
                except Exception:  # noqa: BLE001 — a failed card never
                    log.exception("separator render failed for %s", target_path)
                    seq -= 1
        src = event_root / f.export_relpath
        if not src.is_file():
            result.missing.append(f.export_relpath)
            continue
        seq += 1
        dst = dest / f"{seq:03d}_{Path(f.export_relpath).name}"
        # spec/153 — overlays ride the generated .pte as separate text
        # objects (burn-in retired). The member stays a link; we still
        # embed the *where* IPTC tags into the JPEG so the location
        # travels with the file (the technical EXIF is already there).
        if _place(src, dst, force_copy=copy_mode):
            result.linked += 1
        else:
            result.copied += 1
        # spec/158 — this member is now materialized in the folder;
        # record it so the manifest write below (and any later
        # "Only new files" run) knows it's here.
        present_members.add(f.export_relpath)
        if overlay_fields:
            prov = _provenance(f.export_relpath)
            if cut_overlay.needs_embedded_write(overlay_fields, prov):
                if iptc_writer(dst, cut_overlay.where_iptc_tags(prov)):
                    result.iptc_written += 1
        if f.kind == "video":
            totals_video_ms += f.duration_ms
        else:
            totals_photos += 1

        # spec/105 §3 — Original Media/ subdir (members with no
        # source_item_id — separators / opener — never reach this
        # branch because they don't loop here).
        if include_originals:
            origin_rel = resolve_original(f.source_item_id)
            if not origin_rel:
                continue
            origin_src = event_root / origin_rel
            if not origin_src.is_file():
                result.missing_originals.append(origin_rel)
                continue
            originals_dir = dest / "Original Media"
            originals_dir.mkdir(exist_ok=True)
            origin_dst = _dedup_filename(
                originals_dir, Path(origin_rel).name)
            try:
                if _place(origin_src, origin_dst, force_copy=copy_mode):
                    result.originals_linked += 1
                else:
                    result.originals_copied += 1
            except OSError:
                log.exception(
                    "original place failed for %s", origin_rel)
                result.missing_originals.append(origin_rel)

    # spec/112 — soundtrack via the shared helper so the per-event and
    # cross-event exporters build identical playlists. spec/158 — an
    # incremental "Only new files" run leaves the prior playlist alone
    # (its budget covered the first bundle); a full Overwrite refreshes
    # it for the new total.
    if not incremental:
        result.audio_files, result.audio_short = write_audio_playlist(
            dest=dest,
            music_category=cut.music_category,
            photo_count=totals_photos,
            separator_count=totals_seps,
            video_ms_total=totals_video_ms,
            photo_s=cut.photo_s,
            audio_root=audio_root,
            audio_tracks=audio_tracks,
            copy_mode=copy_mode,
            rng=rng,
            # spec/152 §3 — include the transition_ms + opener slot in
            # the show total so the playlist runs to the same wall time
            # PTE plays. The opener rides whenever an ``opener_writer``
            # was provided AND the Cut has files — decoupled from
            # ``separators_on`` (which now controls per-day cards only).
            transition_ms=transition_ms,
            opener_count=1 if (opener_writer is not None and files) else 0,
        )

    # spec/158 — refresh the sidecar manifest so a later "Only new
    # files" run knows exactly what this folder now holds. Written for
    # every mode: a full export records the whole bundle; an incremental
    # run records the prior set plus the members it just appended.
    _write_export_manifest(
        dest, cut_id=cut.id, members=present_members, max_seq=seq)

    gateway.mark_cut_exported(cut.id)
    log.info(
        "export_cut %s -> %s (%d linked, %d copied, %d iptc, "
        "%d separators, %d audio, %d originals_linked, %d originals_copied, "
        "%d skipped, %d missing, %d missing_originals)",
        cut.tag, dest, result.linked, result.copied,
        result.iptc_written, result.separators, result.audio_files,
        result.originals_linked, result.originals_copied,
        result.skipped, len(result.missing), len(result.missing_originals))
    return result
