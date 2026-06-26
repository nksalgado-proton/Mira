"""PTE AV Studio project (`.pte`) generator (spec/107).

Turns a Cut's exported folder into a ready-to-open `.pte` slideshow project
for PTE AV Studio. Pure logic — Qt-free (charter invariant 8) — so the
generator runs in tests and from the export pipeline without dragging UI
in. The Tier-1 launch helpers (open folder / open in PTE) live in a
separate :mod:`mira.shared.pte_launch` module so this file stays focused.

## The model

A `.pte` file is text (UTF-8 with BOM, CRLF). It has `[Main]`, `[Tracks]`,
`[Slide N]`, `[Effects]`, and `[Times]` sections. Inside them, nested
``object Name:Type ... end`` blocks describe slides, music items, video
clips and overlays. The generator never invents that structure — it
clones it from a **skeleton template** (`assets/pte/skeleton.pte` by
default, or a captured `.mira/pte_skeleton.pte`) and substitutes paths /
durations / overlay text per Cut member.

Three prototypes ride in the skeleton:
  * **photo prototype** — the first `[Slide N]` block whose Container
    objects are `:Image`. Its style (zoom, fit-mode, shadow, font of the
    nested overlay Text) is the author's; the generator only swaps
    `ImageName=` / `Picture=` and re-mints the per-object `GUID`s.
  * **video prototype** — the first `[Slide N]` block whose Container
    objects are `:Video`. Same idea: swap `FileName=` / `Picture=` /
    `Duration=`, re-mint a shared `ClipGUID` and per-object `GUID`s, and
    emit a matching `[Tracks]` `VideoClip` linking back via `MasterID`.
  * **overlay Text prototype** — the single nested `:Text` object inside
    the photo prototype's `PlaceInto` image object (and likewise in the
    video prototype if the author put one there). The generator
    recognises it **structurally** (one `Text` per slide, nested under
    the inner image/video) — never by matching the placeholder string.

## GUID regeneration is load-bearing

`Image` and `Video` slides bind their bytes by **per-object GUID**, not by
the path string. Cloning a slide without re-minting its GUIDs leaves PTE
pointing at the original sample; every member's `GUID` line gets a fresh
UUID so the new `ImageName=` / `FileName=` takes effect. Audio items and
the dangling `Tracks` clips bind by **path string** — repathing alone is
enough (a fresh `GUID` per music item just avoids collisions if the user
later merges projects).

## Timing

`[Times]` is cumulative milliseconds, one entry per slide:
  * photo / separator slide → the Cut's per-slide seconds (+ transition);
  * video slide → the clip's `Duration` (+ transition).
The trailing `opt_slidescount=N` must equal the slide count. The `[Main]`
`DefDuration` carries the photo/separator-tier seconds so PTE shows a
sensible default for slides Mira didn't time explicitly.

## Output naming

The generator writes `slideshow.pte` into the supplied cut-export folder.
On collision, the default is to **disambiguate** (`slideshow (2).pte`)
so a re-export never clobbers a project the user has since edited in
PTE — callers that explicitly want to overwrite pass ``overwrite=True``.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: The output filename inside the cut-export folder (spec/107 §4 +
#: spec/121 §2 — the stem is parameterised so a per-Cut name lands
#: instead of ``slideshow.pte``).
DEFAULT_OUTPUT_STEM = "slideshow"
DEFAULT_OUTPUT_NAME = f"{DEFAULT_OUTPUT_STEM}.pte"
#: Where a captured (user-customised) skeleton lives, relative to the
#: library root. Spec/107 §2.
CAPTURED_SKELETON_RELPATH = Path(".mira") / "pte_skeleton.pte"
#: The bundled default skeleton (Nuitka data asset). Resolved at runtime
#: via :func:`bundled_skeleton_path` so tests can override it.
_BUNDLED_SKELETON_RELPATH = Path("assets") / "pte" / "skeleton.pte"

#: Default transition time between slides (ms). Matches the dissolve
#: effect baked into the bundled skeleton — kept as a knob so a
#: captured skeleton with a different effect time can override.
DEFAULT_TRANSITION_MS = 2000

#: Overlay modes (mirror :mod:`core.cut_overlay`).
OVERLAY_EMBEDDED = "embedded"
OVERLAY_BURN_IN = "burn_in"
OVERLAY_OFF = "off"

#: spec/153 — text-object style roles. Each maps to a row in
#: :data:`_TEXT_STYLE` (font / box-scale / position). Mira owns the look;
#: the user restyles in PTE afterward.
TEXT_PHOTO_CAPTION = "photo_caption"   # one centred line at the photo's bottom
TEXT_SEP_TITLE = "sep_title"           # "Day N" — large, upper-centre
TEXT_SEP_SUB = "sep_sub"               # date · location · description — smaller
TEXT_OPENER_TITLE = "opener_title"     # the show title — largest, centre
TEXT_OPENER_SUB = "opener_sub"         # the show's facts — smaller, below


# ── Data classes ───────────────────────────────────────────────────

@dataclass(frozen=True)
class PteText:
    """spec/153 — one separate PTE ``:Text`` object to layer over a slide.

    ``role`` selects the Mira-owned default style (:data:`_TEXT_STYLE`);
    ``text`` is the (single- or multi-line) content. The generator emits
    one ``object TextN:Text`` per entry, nested in the slide's foreground
    image, with a fresh GUID — so swapping the image beneath keeps the
    text (spec/153)."""

    text: str
    role: str = TEXT_PHOTO_CAPTION


@dataclass(frozen=True)
class PteMember:
    """One Cut member, resolved to its export-folder absolute path.

    ``kind`` is ``'photo'`` (.jpg / separator card) or ``'video'`` (.mp4).
    ``duration_ms`` carries the true clip length for videos and is
    ignored for photos. ``texts`` are the separate overlay text objects
    for this slide (spec/153) — empty = a clean slide. ``overlay_text`` is
    the legacy single-string field (spec/107 §3.4); when ``texts`` is empty
    it is bridged to one :data:`TEXT_PHOTO_CAPTION` object."""

    kind: str
    path: Path
    duration_ms: int = 0
    overlay_text: Optional[str] = None
    texts: Sequence["PteText"] = ()


@dataclass(frozen=True)
class PteAudioTrack:
    """One soundtrack item, with the on-disk Windows path + true duration
    in milliseconds (PTE wants integer ms)."""

    path: Path
    duration_ms: int


# ── GUID helpers ──────────────────────────────────────────────────

_GUID_RE = re.compile(r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                      r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}")


def fresh_guid() -> str:
    """A fresh PTE-shaped GUID: ``{UUID4-UPPERCASE}``."""
    return "{" + str(uuid.uuid4()).upper() + "}"


def _regenerate_guids(block: str) -> str:
    """Replace every PTE GUID in ``block`` with a fresh one. Used to
    re-mint a cloned slide so PTE doesn't think it's still the
    skeleton's prototype (spec/107 §0 — image/video bind by GUID, not
    by path)."""
    return _GUID_RE.sub(lambda _m: fresh_guid(), block)


# ── Path formatting ───────────────────────────────────────────────

def _windows_path(p: Path) -> str:
    """Format ``p`` as an absolute Windows path string. PTE happily
    accepts forward-or-backslash paths but writes backslashes in its
    own saves, so we match for diffability."""
    return str(Path(p).resolve()).replace("/", "\\")


# ── Section parsing ───────────────────────────────────────────────

#: Regex matching `[SectionName]` headers anchored to a line start.
_SECTION_HEADER_RE = re.compile(r"^\[(?P<name>[A-Za-z0-9_ ]+)\]\s*$",
                                re.MULTILINE)


@dataclass
class _Section:
    name: str        # 'Main' / 'Tracks' / 'Slide1' / 'Slide2' / ... / 'Effects' / 'Times'
    body: str        # everything BETWEEN this header and the next one
                     # (or end of file). Includes the trailing CRLF.


def _split_sections(text: str) -> List[_Section]:
    """Parse ``text`` into ordered sections. Anything before the first
    `[Section]` header is dropped (PTE files always open with `[Main]`,
    so there is nothing before it)."""
    headers = list(_SECTION_HEADER_RE.finditer(text))
    if not headers:
        raise ValueError("no [Section] headers in PTE text — invalid skeleton")
    sections: List[_Section] = []
    for i, m in enumerate(headers):
        name = m.group("name")
        body_start = m.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[body_start:body_end]
        # body_start is right after the header line's newline char(s);
        # leading whitespace before the body content is normal CRLF.
        sections.append(_Section(name=name, body=body))
    return sections


def _join_sections(sections: Sequence[_Section]) -> str:
    """The inverse of :func:`_split_sections`: emit ``[Name]<CRLF>body``
    for each section, in order. The body already carries its own line
    terminations."""
    out: List[str] = []
    for s in sections:
        out.append(f"[{s.name}]\r\n")
        out.append(s.body)
    return "".join(out)


# ── Skeleton model ────────────────────────────────────────────────

@dataclass
class Skeleton:
    """Parsed skeleton — the prototypes plus the rest of the file held
    verbatim so the generator can splice cloned slides in without
    touching the author's style."""

    raw: str                          # full skeleton text (no BOM)
    sections: List[_Section]
    photo_slide_idx: int              # index into ``sections`` of the photo prototype
    video_slide_idx: Optional[int]    # index, or None when the skeleton has no video proto
    music_item_template: str          # one ``object ItemN:TMusicItem ... end`` block, raw
    main_body: str                    # the [Main] section body, with the Music block stripped


def _is_image_slide(section_body: str) -> bool:
    """A slide whose Container's first nested object is `:Image`."""
    return bool(re.search(r"^  object [^:\r\n]+:Image\b", section_body,
                          re.MULTILINE))


def _is_video_slide(section_body: str) -> bool:
    """A slide whose Container's first nested object is `:Video`."""
    return bool(re.search(r"^  object [^:\r\n]+:Video\b", section_body,
                          re.MULTILINE))


_MUSIC_ITEM_RE = re.compile(
    r"    object Item\d+:TMusicItem\r\n"
    r"(?:      [^\r\n]*\r\n)+?"
    r"    end\r\n",
)

_MUSIC_BLOCK_RE = re.compile(
    r"object Music:Music\r\n"
    r"(?:[\s\S]*?)\r\nend\r\n",
)


def parse_skeleton(text: str) -> Skeleton:
    """Parse a skeleton string into a :class:`Skeleton`. Strips the BOM
    if present. Raises :class:`ValueError` when the skeleton is missing
    a photo prototype."""
    if text.startswith("﻿"):
        text = text[1:]
    sections = _split_sections(text)
    photo_idx: Optional[int] = None
    video_idx: Optional[int] = None
    for i, s in enumerate(sections):
        if not s.name.startswith("Slide"):
            continue
        if photo_idx is None and _is_image_slide(s.body):
            photo_idx = i
        elif video_idx is None and _is_video_slide(s.body):
            video_idx = i
    if photo_idx is None:
        raise ValueError(
            "skeleton has no photo (:Image) slide prototype")

    main_body = sections[0].body
    music_match = _MUSIC_ITEM_RE.search(main_body)
    if not music_match:
        raise ValueError(
            "skeleton has no TMusicItem prototype in the Music block")
    music_item_template = music_match.group(0)

    return Skeleton(
        raw=text,
        sections=sections,
        photo_slide_idx=photo_idx,
        video_slide_idx=video_idx,
        music_item_template=music_item_template,
        main_body=main_body,
    )


# ── Capture (user-customised skeleton) ────────────────────────────

def capture_skeleton(source_text: str) -> str:
    """Given a user-saved 1-photo-1-video(+overlay) `.pte` project, return
    a content-void skeleton that the generator can consume (spec/107 §2).

    The capture KEEPS:
      * `[Main]` style options, `TheEffect` block, `[Effects]` group;
      * the photo prototype `[Slide N]` (one `:Image` block);
      * the video prototype `[Slide N]` (one `:Video` block) when present;
      * the nested overlay `Text` object (the *style*, not the words);
      * one music item template (the user's fade times / volume).

    The capture STRIPS:
      * real media paths in `ImageName=` / `FileName=` / `Picture=` →
        placeholder markers;
      * baked overlay `Text=` content → ``{overlay}`` (the generator
        recognises overlays structurally, not by string — this is purely
        cosmetic so a hand-read skeleton stays uncluttered);
      * dangling `[Tracks]` `VideoClip` rows (`StartSlideIdx ≥ slide_count`
        in the source) — they'd point at nothing after a re-generation;
      * music items beyond the first (we keep one as the template; the
        generator emits N per export).
    """
    text = source_text
    if text.startswith("﻿"):
        text = text[1:]
    skel = parse_skeleton(text)
    # Strip personal paths line-by-line.
    out_lines: List[str] = []
    in_dangling_clip = False
    for raw in text.split("\r\n"):
        ln = raw
        # Image / Video object name lines: `  object NAME:Image|Video` ↑ keep.
        if ln.startswith("    ImageName="):
            ln = "    ImageName={photo_path}"
        elif ln.startswith("    FileName=") and ln.lower().endswith(".mp4"):
            ln = "    FileName={video_path}"
        elif ln.startswith("    Duration=") and not in_dangling_clip:
            # Video slide block carries Duration=<ms>; replace with marker.
            if re.match(r"^    Duration=\d+$", ln):
                ln = "    Duration={video_duration}"
        elif ln.startswith("Picture=") and ln.lower().endswith(".jpg"):
            ln = "Picture={photo_path}"
        elif ln.startswith("Picture=") and ln.lower().endswith(".mp4"):
            ln = "Picture={video_path}"
        elif ln.startswith("      FileName=") and ln.lower().endswith(".mp3"):
            ln = "      FileName={audio_path}"
        elif ln.startswith("      Duration=") and re.match(
                r"^      Duration=\d+$", ln):
            ln = "      Duration={audio_duration}"
        elif ln.startswith("  FileName=") and ln.lower().endswith(".mp4"):
            # The [Tracks] dangling-clip FileName.
            ln = "  FileName={dangling_video_path}"
            in_dangling_clip = True
        elif ln.startswith("  Duration=") and re.match(
                r"^  Duration=\d+$", ln) and in_dangling_clip:
            ln = "  Duration={dangling_video_duration}"
        elif ln == "end" and in_dangling_clip:
            in_dangling_clip = False
        # Overlay Text= content → placeholder marker (cosmetic).
        if re.match(r"^\s{6,}Text=\".*\"$", ln):
            indent = ln[: len(ln) - len(ln.lstrip())]
            ln = f'{indent}Text="{{overlay}}"'
        elif ln.startswith("ImagesFolder="):
            ln = "ImagesFolder={images_folder}"
        elif ln.startswith("ProjectFilePath="):
            ln = "ProjectFilePath={project_file_path}"
        elif ln.startswith("projectname="):
            ln = "projectname=mira_skeleton"
        elif ln.startswith("opt_vidmp4fn="):
            ln = "opt_vidmp4fn="
        out_lines.append(ln)
    sanitized = "\r\n".join(out_lines)

    # Drop additional music items beyond the first.
    music_match = _MUSIC_BLOCK_RE.search(sanitized)
    if music_match:
        block = music_match.group(0)
        items = list(_MUSIC_ITEM_RE.finditer(block))
        if len(items) > 1:
            # Keep only the first; drop the rest in reverse order.
            new_block = block
            for it in reversed(items[1:]):
                new_block = new_block[: it.start()] + new_block[it.end():]
            sanitized = (sanitized[: music_match.start()] + new_block
                         + sanitized[music_match.end():])

    # Drop dangling [Tracks] VideoClip rows. "Dangling" = StartSlideIdx
    # is past the new last slide count; for a captured skeleton with
    # 1 photo + 1 video the only valid clip is the one that matches the
    # video slide. We approximate: KEEP exactly one VideoClip block if
    # the captured project has a video prototype, otherwise strip all.
    sanitized = _strip_extra_video_clips(
        sanitized, keep_first=skel.video_slide_idx is not None)
    return sanitized


def _strip_extra_video_clips(text: str, *, keep_first: bool) -> str:
    """Remove `[Tracks]` `VideoClip` blocks beyond the first (or all when
    ``keep_first=False``). Used by capture (one clip max for the proto)
    and by the generator (any clip whose `StartSlideIdx ≥ N` is dead)."""
    sections = _split_sections(text)
    for i, s in enumerate(sections):
        if s.name != "Tracks":
            continue
        clips = list(re.finditer(
            r"object [^:\r\n]+:VideoClip\r\n(?:[\s\S]*?)\r\nend\r\n",
            s.body))
        if not clips:
            return text
        if keep_first:
            # Drop clips beyond the first.
            new_body = s.body
            for c in reversed(clips[1:]):
                new_body = new_body[: c.start()] + new_body[c.end():]
            sections[i] = _Section(name=s.name, body=new_body)
        else:
            new_body = s.body
            for c in reversed(clips):
                new_body = new_body[: c.start()] + new_body[c.end():]
            sections[i] = _Section(name=s.name, body=new_body)
        return _join_sections(sections)
    return text


# ── Bundled / captured skeleton resolution ────────────────────────

def bundled_skeleton_path() -> Path:
    """The path to the hand-authored shipped skeleton. Tests can
    monkeypatch this; the export pipeline reads it via
    :func:`load_skeleton`."""
    # Walk up from this module to the project root (the dir holding
    # ``assets/``). In a Nuitka-built binary the resource lives next to
    # the executable; in dev / tests it lives in the repo.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / _BUNDLED_SKELETON_RELPATH
        if candidate.is_file():
            return candidate
    # Last-ditch fallback — the relpath from the cwd. The caller will
    # get a missing-file error if this doesn't resolve; that's correct.
    return _BUNDLED_SKELETON_RELPATH


def load_skeleton(
    *,
    library_root: Optional[Path] = None,
    bundled_fallback: Optional[Path] = None,
) -> str:
    """Resolve the skeleton to load (captured > bundled) and return its
    text (BOM stripped). Raises :class:`FileNotFoundError` only when
    BOTH paths are missing — which means a broken install."""
    if library_root is not None:
        captured = Path(library_root) / CAPTURED_SKELETON_RELPATH
        if captured.is_file():
            return _read_pte(captured)
    bundled = bundled_fallback or bundled_skeleton_path()
    return _read_pte(bundled)


def _read_pte(path: Path) -> str:
    """Read a `.pte` text file, stripping the UTF-8 BOM and normalising
    line endings to CRLF in memory (the rest of the module assumes
    CRLF — preserving it makes round-trips byte-faithful)."""
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig")
    # Normalise: any bare LF → CRLF (we re-emit CRLF on write anyway).
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text


def write_pte(text: str, target: Path) -> None:
    """Write ``text`` to ``target`` as UTF-8 with BOM + CRLF — the
    encoding PTE writes (spec/107 §0). Atomic at the FS level: writes
    to ``<target>.tmp`` then renames so a half-written file never
    appears."""
    target = Path(target)
    # Force CRLF on every line ending; PTE chokes on mixed.
    body = text.replace("\r\n", "\n").replace("\n", "\r\n")
    payload = b"\xef\xbb\xbf" + body.encode("utf-8")
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)


def slideshow_target(folder: Path, *,
                     stem: str = DEFAULT_OUTPUT_STEM,
                     overwrite: bool = False) -> Path:
    """Pick the output path inside ``folder`` (spec/107 §4 + spec/121
    §2): default ``<stem>.pte``, or ``<stem> (2).pte`` etc. when one
    already exists and ``overwrite`` is ``False``. ``stem`` defaults
    to ``"slideshow"`` for back-compat; the share_cuts export pipeline
    passes the Cut's tag so a folder of exported Cuts gets distinct
    project filenames. Empty / whitespace-only stem falls back to the
    default so a Cut with a missing name still yields a
    ``slideshow.pte``."""
    folder = Path(folder)
    safe_stem = (stem or "").strip() or DEFAULT_OUTPUT_STEM
    base = folder / f"{safe_stem}.pte"
    if overwrite or not base.exists():
        return base
    n = 2
    while True:
        candidate = folder / f"{safe_stem} ({n}).pte"
        if not candidate.exists():
            return candidate
        n += 1


# ── Generation ────────────────────────────────────────────────────

def _replace_kv(body: str, key: str, value: str) -> str:
    """Replace ``key=…`` line in ``body`` with ``key=value``. The key
    must match at line start (no indent); used for `[Main]` overrides.

    The replacement is performed via a callable so backslashes in
    Windows paths can't be misinterpreted as regex backreferences.

    The pattern omits the ``$`` anchor and trusts ``[^\r\n]*`` to stop
    at the line break — Python's ``re.MULTILINE`` puts ``$`` right
    before ``\n``, which doesn't line up with the ``\r`` we leave in
    the character class, so anchoring with ``$`` silently fails on
    CRLF text (the bug that shipped the first revision)."""
    pattern = re.compile(rf"^{re.escape(key)}=[^\r\n]*", re.MULTILINE)
    replacement = f"{key}={value}"
    return pattern.sub(lambda _m: replacement, body, count=1)


#: Nested ``TMusicOptions`` block with ``IsRepeat=1``. PTE writes this
#: block inside ``object Music:Music`` when the user enables
#: "Repeat tracks" in Project Options → Music; absent otherwise. We
#: emit it unconditionally so a Mira-generated show whose playlist
#: total is shorter than the visual run-time (the audio-library
#: builder rounds DOWN and the spec/152 transition budget is
#: approximate) loops the soundtrack instead of running to silence.
#: Confirmed by diffing two otherwise-identical projects exported
#: from PTE AV Studio 11 with the option ON vs OFF.
_MUSIC_REPEAT_BLOCK = (
    "  object Options:TMusicOptions\r\n"
    "    IsRepeat=1\r\n"
    "  end\r\n"
)


def _format_music_block(audio_tracks: Sequence[PteAudioTrack],
                        item_template: str) -> str:
    """Render the ``object Music:Music`` block with one `TMusicItem` per
    track. ``item_template`` is the skeleton's prototype item — its
    FadeIn/FadeOut/Volume ride to every emitted item. The nested
    ``Options:TMusicOptions`` block with ``IsRepeat=1`` is emitted
    unconditionally (see :data:`_MUSIC_REPEAT_BLOCK`) so the soundtrack
    loops to the end of the visual show even when the audio total is
    a few seconds shorter than the slide total."""
    if not audio_tracks:
        # Empty Music block — no items. Match PTE's shape so the section
        # round-trips cleanly.
        return ("object Music:Music\r\n"
                + _MUSIC_REPEAT_BLOCK
                + "  object Track0:TMusicTrack\r\n"
                "  end\r\n"
                "end\r\n")
    items: List[str] = []
    for i, track in enumerate(audio_tracks):
        item = item_template
        # Re-name `Item0` to `ItemN`.
        item = re.sub(r"object Item\d+:TMusicItem",
                      f"object Item{i}:TMusicItem", item, count=1)
        # Regenerate GUID.
        item = _regenerate_guids(item)
        # Replace the FileName + Duration placeholders / values via a
        # callable so backslash-laden Windows paths don't get parsed as
        # backreferences in the replacement string.
        fname_repl = f"      FileName={_windows_path(track.path)}"
        item = re.sub(r"^      FileName=[^\r\n]*",
                      lambda _m: fname_repl,
                      item, flags=re.MULTILINE, count=1)
        dur_repl = f"      Duration={int(track.duration_ms)}"
        item = re.sub(r"^      Duration=[^\r\n]*",
                      lambda _m: dur_repl,
                      item, flags=re.MULTILINE, count=1)
        items.append(item)
    return ("object Music:Music\r\n"
            + _MUSIC_REPEAT_BLOCK
            + "  object Track0:TMusicTrack\r\n"
            + "".join(items)
            + "  end\r\n"
            "end\r\n")


#: spec/153 — Mira-owned default style per text role. ``scale`` is the
#: KeyPoint box scale (PTE sizes text by the box, not a font size — the
#: load-bearing trick Nelson demonstrated); ``pos`` is ``(x, y)`` in
#: Percent coords (0,0 = centre, +y = down). Tuned from the hand-authored
#: example; THIS table is the one place to retune the look.
_TEXT_STYLE: Dict[str, dict] = {
    TEXT_PHOTO_CAPTION: dict(
        font="Arial", scale=4.5, pos=(0.0, 78.0),
        align="Center", color="255,255,255"),
    TEXT_SEP_TITLE: dict(
        font="Segoe UI", scale=13.0, pos=(0.0, -16.0),
        align="Center", color="255,255,255"),
    TEXT_SEP_SUB: dict(
        font="Segoe UI", scale=5.0, pos=(0.0, 9.0),
        align="Center", color="255,255,255"),
    # The opener is the show title — a touch larger than a day separator.
    TEXT_OPENER_TITLE: dict(
        font="Segoe UI", scale=15.0, pos=(0.0, -14.0),
        align="Center", color="255,255,255"),
    TEXT_OPENER_SUB: dict(
        font="Segoe UI", scale=5.0, pos=(0.0, 12.0),
        align="Center", color="255,255,255"),
}


def _text_object(idx: int, text: str, role: str) -> str:
    """Emit one complete ``object TextN:Text`` block (4-space indent —
    nested in a slide's foreground image) with a fresh GUID and the
    Mira-owned style for ``role`` (spec/153). Quotes / backslashes in
    ``text`` are escaped for PTE's parser. ``ScaleX/ScaleY`` carry the
    size; ``Position`` places the box."""
    st = _TEXT_STYLE.get(role, _TEXT_STYLE[TEXT_PHOTO_CAPTION])
    safe = (text or "").replace("\\", "\\\\").replace('"', '\\"')
    px, py = st["pos"]
    return (
        f"    object Text{idx}:Text\r\n"
        f"      GUID={fresh_guid()}\r\n"
        f"      FitMode=PlaceInto\r\n"
        f"      PosMode=Percent\r\n"
        f"      CenterMode=Percent\r\n"
        f"      BlurMode=1\r\n"
        f"      ShadowEnable=1\r\n"
        f"      ShadowSize=16\r\n"
        f"      ShadowColor=0,0,0\r\n"
        f"      ShadowOpacity=59\r\n"
        f"      ShadowDistance=1.5\r\n"
        f"      ShadowAngle=45\r\n"
        f"      ShadowSpreadIndex=0\r\n"
        f'      Text="{safe}"\r\n'
        f"      TextColor={st['color']}\r\n"
        f"      TextColorHover={st['color']}\r\n"
        f"      TextColorClick={st['color']}\r\n"
        f"      FontName={st['font']}\r\n"
        f"      FontStyle=\r\n"
        f"      TextAlign={st['align']}\r\n"
        f"      LineSpacing=0\r\n"
        f"      Filtering=Default\r\n"
        f"      MipLodBias=-0.5\r\n"
        f"      NestedOpacity=1\r\n"
        f"      NestedColorisation=\r\n"
        f"      TranspForClick=\r\n"
        f"      HideMode=\r\n"
        f"      ChildInvis=\r\n"
        f"      Action=\r\n"
        f"      TextTransform=\r\n"
        f"      object KeyPoint1:KeyPoint\r\n"
        f"        Origin=SlideBegin\r\n"
        f"        Bokeh=50\r\n"
        f"        ScaleX={st['scale']}\r\n"
        f"        ScaleY={st['scale']}\r\n"
        f"        Opacity=100\r\n"
        f"        Position={px},{py}\r\n"
        f"        CenterPos=\r\n"
        f"        grps=127\r\n"
        f"      end\r\n"
        f"    end\r\n"
    )


_NESTED_TEXT_RE = re.compile(
    r"    object [^:\r\n]+:Text\r\n(?:[\s\S]*?)\r\n    end\r\n")


def _inject_texts(slide_body: str, texts: Sequence["PteText"]) -> str:
    """spec/153 — replace the skeleton's single nested ``:Text`` anchor
    with the member's generated text objects (one ``:Text`` per
    :class:`PteText`, styled by role). Empty ``texts`` → strip the anchor
    so the slide stays clean. The anchor marks WHERE text nests (inside
    the foreground image); its own style is irrelevant — Mira owns the
    look now."""
    blocks = "".join(
        _text_object(i + 1, t.text, t.role) for i, t in enumerate(texts))
    m = _NESTED_TEXT_RE.search(slide_body)
    if m:
        return slide_body[: m.start()] + blocks + slide_body[m.end():]
    return slide_body


def _strip_nested_text(slide_body: str) -> str:
    """Remove the single nested `:Text` object (the overlay anchor) from a
    slide body — the no-overlay path (spec/153: a member with no texts)."""
    return _NESTED_TEXT_RE.sub("", slide_body, count=1)


def _populate_nested_text(slide_body: str, overlay_text: str) -> str:
    """Set the nested `:Text` object's `Text="…"` line to ``overlay_text``
    and re-mint its GUID. The style (Scale / Position / font / shadow)
    is inherited verbatim from the skeleton (spec/107 §3.4)."""
    text_re = re.compile(
        r"(    object [^:\r\n]+:Text\r\n(?:[\s\S]*?)\r\n    end\r\n)")
    m = text_re.search(slide_body)
    if not m:
        return slide_body
    block = m.group(1)
    # Re-mint per-object GUID.
    block = _regenerate_guids(block)
    # Swap the Text="…" line. Escape any embedded quotes in
    # ``overlay_text`` to keep PTE's parser happy. The replacement
    # goes through a callable so backslashes in the value can't be
    # parsed as regex backreferences.
    safe = overlay_text.replace("\\", "\\\\").replace('"', '\\"')
    text_repl = f'      Text="{safe}"'
    block = re.sub(r'^      Text="[^"]*"',
                   lambda _m: text_repl, block,
                   flags=re.MULTILINE, count=1)
    return slide_body[: m.start()] + block + slide_body[m.end():]


def _set_slide_image_paths(slide_body: str, image_path: str) -> str:
    """Rewrite every `ImageName=` line + the slide-level `Picture=` line
    to ``image_path``. Photo slides use this; the per-object `GUID`s
    are re-minted by the caller (spec/107 §0). Replacements go through
    callables so backslashes in Windows paths can't be parsed as
    regex backreferences."""
    name_repl = f"    ImageName={image_path}"
    slide_body = re.sub(r"^    ImageName=[^\r\n]*",
                        lambda _m: name_repl,
                        slide_body, flags=re.MULTILINE)
    pic_repl = f"Picture={image_path}"
    slide_body = re.sub(r"^Picture=[^\r\n]*",
                        lambda _m: pic_repl,
                        slide_body, flags=re.MULTILINE, count=1)
    return slide_body


def _set_slide_video_paths(slide_body: str, video_path: str,
                           duration_ms: int) -> str:
    """Rewrite every `FileName=` + the slide-level `Picture=` line to
    ``video_path``, and every `Duration=` line to ``duration_ms``."""
    name_repl = f"    FileName={video_path}"
    slide_body = re.sub(r"^    FileName=[^\r\n]*",
                        lambda _m: name_repl,
                        slide_body, flags=re.MULTILINE)
    dur_repl = f"    Duration={int(duration_ms)}"
    slide_body = re.sub(r"^    Duration=[^\r\n]*",
                        lambda _m: dur_repl,
                        slide_body, flags=re.MULTILINE)
    pic_repl = f"Picture={video_path}"
    slide_body = re.sub(r"^Picture=[^\r\n]*",
                        lambda _m: pic_repl,
                        slide_body, flags=re.MULTILINE, count=1)
    return slide_body


def _set_video_clip_guid(slide_body: str, clip_guid: str) -> Tuple[str, str]:
    """Replace every `ClipGUID=` line in a video slide body with
    ``clip_guid``, and return ``(updated_body, cover_object_guid)``.
    The cover object is the FIRST `:Video` object in the slide body
    (its `GUID` is the one a `[Tracks]` `VideoClip` references as its
    `MasterID`)."""
    clip_repl = f"    ClipGUID={clip_guid}"
    body = re.sub(r"^    ClipGUID=\{[^}]+\}",
                  lambda _m: clip_repl,
                  slide_body, flags=re.MULTILINE)
    # Find the FIRST :Video object's GUID line.
    m = re.search(r"^  object [^:\r\n]+:Video\r\n    GUID=(\{[^}]+\})",
                  body, re.MULTILINE)
    if not m:
        raise ValueError("video slide has no GUID on its cover object")
    return body, m.group(1)


def _build_video_clip(*, clip_guid: str, master_id: str, video_path: str,
                      duration_ms: int, start_slide_idx: int,
                      caption: str) -> str:
    """Emit one `[Tracks]` `VideoClip` block for a video member. The
    caller is responsible for `StartSlideIdx` being **0-based** (spec/107
    §0)."""
    return (
        f"object VidClip_{start_slide_idx + 1}:VideoClip\r\n"
        f"  FileName={video_path}\r\n"
        f"  Duration={int(duration_ms)}\r\n"
        f"  StartVideoTime=0\r\n"
        f"  StartPTETime=0\r\n"
        f"  LoadFromDisk=0\r\n"
        f"  Mute=0\r\n"
        f"  ClipCaption={caption}\r\n"
        f"  ClipGUID={clip_guid}\r\n"
        f"  NotStoreImageFile=1\r\n"
        f"  StartSlideIdx={start_slide_idx}\r\n"
        f"  MasterID={master_id}\r\n"
        f"end\r\n"
    )


#: spec/140 §2 — floor for any video member whose probed duration
#: came back as 0 (corrupt mp4 / missing ffmpeg). PTE refuses to play
#: a zero-length clip, so we substitute a sane minimum and log; the
#: result is a slide that plays for half a second instead of one that
#: silently dies during the show.
_MIN_VIDEO_DURATION_MS = 500


def _safe_video_duration_ms(probed_ms: int, *, path: object) -> int:
    """Coerce a probed ms value into something PTE can actually play.

    Negative / zero / missing → :data:`_MIN_VIDEO_DURATION_MS` with a
    warning logged once per call. Anything ≥ the floor passes through
    unchanged. Pure: the caller is what threads the file through to
    the three Duration sites."""
    n = int(probed_ms or 0)
    if n >= _MIN_VIDEO_DURATION_MS:
        return n
    log.warning(
        "pte: video %s had unusable duration %d ms — substituting "
        "%d ms so PTE plays the clip instead of skipping a "
        "Duration=0 entry", path, n, _MIN_VIDEO_DURATION_MS,
    )
    return _MIN_VIDEO_DURATION_MS


def _format_times_section(slide_durations_ms: Sequence[int]) -> str:
    """Render the `[Times]` body — cumulative `opt_synchpos<N>=` lines +
    the trailing `opt_slidescount=N`. Each entry is the running total
    AFTER its slide ends, in milliseconds."""
    lines: List[str] = []
    running = 0
    for i, d in enumerate(slide_durations_ms, start=1):
        running += int(d)
        lines.append(f"opt_synchpos{i}={running}")
    lines.append(f"opt_slidescount={len(slide_durations_ms)}")
    return "\r\n".join(lines) + "\r\n"


def generate(
    skeleton_text: str,
    members: Sequence[PteMember],
    audio_tracks: Sequence[PteAudioTrack],
    *,
    aspect: str,
    photo_seconds: float,
    project_path: Path,
    images_folder: Path,
    overlay_mode: str = OVERLAY_EMBEDDED,
    transition_ms: int = DEFAULT_TRANSITION_MS,
) -> str:
    """Build a complete `.pte` text from the skeleton and the Cut's
    members. The return is plain text (no BOM); :func:`write_pte` adds
    the BOM + ensures CRLF.

    ``aspect`` is a canonical ``"16:9"`` / ``"4:3"`` / ``"3:2"`` /
    ``"1:1"`` (spec/111). ``photo_seconds`` is the Cut's per-slide
    seconds — photo / separator slides get this, video slides get the
    clip's own ``duration_ms``. ``project_path`` is the absolute path
    where the file will be written (PTE wants it in `ProjectFilePath=`);
    ``images_folder`` is the folder containing the media files (the
    member paths typically live under it).

    ``overlay_mode``:
      * ``embedded`` — populate the nested `:Text` from each member's
        ``overlay_text`` (or strip the Text when the member has none —
        a member with no provenance to show shouldn't carry empty text);
      * ``burn_in`` — strip every nested `:Text` (burn-in already drew
        the words into pixels at export);
      * ``off`` — strip every nested `:Text` (the user wants no
        overlay anywhere)."""
    from core.cut_aspect import aspect_spec

    skel = parse_skeleton(skeleton_text)
    proto_photo_body = skel.sections[skel.photo_slide_idx].body
    proto_video_body = (skel.sections[skel.video_slide_idx].body
                        if skel.video_slide_idx is not None else None)

    spec = aspect_spec(aspect)
    project_path_s = _windows_path(project_path)
    images_folder_s = _windows_path(images_folder).rstrip("\\") + "\\"

    # ── [Main] overrides ─────────────────────────────────────────
    main_body = skel.main_body
    main_body = _replace_kv(main_body, "opt_scr_width", str(spec.width))
    main_body = _replace_kv(main_body, "opt_scr_height", str(spec.height))
    main_body = _replace_kv(main_body, "AspectRatio", spec.pte_aspect)
    main_body = _replace_kv(main_body, "DefDuration",
                            str(int(round(photo_seconds * 1000))))
    main_body = _replace_kv(main_body, "ProjectFilePath", project_path_s)
    main_body = _replace_kv(main_body, "ImagesFolder", images_folder_s)

    # Music block — swap the prototype item with N items. Substitute
    # via a callable so backslash-laden Windows paths in the new block
    # can't be parsed as regex backreferences.
    music_block = _format_music_block(audio_tracks, skel.music_item_template)
    main_body = _MUSIC_BLOCK_RE.sub(lambda _m: music_block,
                                    main_body, count=1)

    # ── slide bodies ─────────────────────────────────────────────
    slide_bodies: List[str] = []
    slide_durations_ms: List[int] = []
    video_clips: List[str] = []
    photo_ms = int(round(photo_seconds * 1000)) + int(transition_ms)
    for i, m in enumerate(members):
        path_s = _windows_path(m.path)
        if m.kind == "video":
            if proto_video_body is None:
                raise ValueError(
                    "skeleton has no video prototype but the Cut contains "
                    "a video member — please capture a skeleton from a "
                    "1-photo + 1-video PTE project")
            body = proto_video_body
            body = _regenerate_guids(body)
            clip_guid = fresh_guid()
            body, cover_guid = _set_video_clip_guid(body, clip_guid)
            # spec/140 §2 — coerce ``m.duration_ms`` to a usable
            # minimum so the same non-zero value lands in BOTH
            # :Video slide objects, the [Tracks] VideoClip Duration,
            # AND the [Times] cumulative. PTE won't play any clip
            # whose Duration is 0.
            clip_ms = _safe_video_duration_ms(
                int(m.duration_ms), path=m.path)
            body = _set_slide_video_paths(body, path_s, clip_ms)
            # spec/150 §1 — the [Times] slot for a video slide is the
            # clip's own length, with NO transition padding added.
            # Padding would hold the frozen last frame for transition_ms
            # before the next dissolve. Letting the incoming slide's
            # dissolve overlap the clip's tail keeps motion running and
            # also realigns with core/cut_budget.py and spec/61 ("clips
            # at their TRUE length").
            slide_durations_ms.append(clip_ms)
            video_clips.append(_build_video_clip(
                clip_guid=clip_guid, master_id=cover_guid,
                video_path=path_s, duration_ms=clip_ms,
                start_slide_idx=i, caption=Path(m.path).stem))
        else:
            body = proto_photo_body
            body = _regenerate_guids(body)
            body = _set_slide_image_paths(body, path_s)
            slide_durations_ms.append(photo_ms)
        # Overlay text objects (spec/153) — Mira-styled separate ``:Text``
        # per member. Empty ``texts`` → a clean slide. The legacy single
        # ``overlay_text`` is bridged to one photo-caption object for any
        # caller that hasn't migrated to ``texts`` yet.
        texts = list(m.texts)
        if not texts and m.overlay_text:
            texts = [PteText(m.overlay_text, TEXT_PHOTO_CAPTION)]
        body = _inject_texts(body, texts)
        slide_bodies.append(body)

    # ── [Tracks] body — fresh clips for emitted videos ───────────
    if video_clips:
        tracks_body = "\r\n" + "".join(video_clips)
    else:
        tracks_body = "\r\n"

    # ── [Effects] body — pass through verbatim if present ────────
    effects_body: Optional[str] = None
    for s in skel.sections:
        if s.name == "Effects":
            effects_body = s.body
            break
    if effects_body is None:
        effects_body = (
            "object global:group\r\n"
            "end\r\n"
            "object local:group\r\n"
            "end\r\n"
            "\r\n"
        )

    # ── [Times] body ─────────────────────────────────────────────
    times_body = _format_times_section(slide_durations_ms)

    # ── Reassemble ───────────────────────────────────────────────
    out_sections: List[_Section] = []
    out_sections.append(_Section(name="Main", body=main_body))
    out_sections.append(_Section(name="Tracks", body=tracks_body))
    out_sections.append(_Section(name="Effects", body=effects_body))
    for i, body in enumerate(slide_bodies, start=1):
        out_sections.append(_Section(name=f"Slide{i}", body=body))
    out_sections.append(_Section(name="Times", body=times_body))
    return _join_sections(out_sections)


def generate_into_folder(
    folder: Path,
    members: Sequence[PteMember],
    audio_tracks: Sequence[PteAudioTrack],
    *,
    aspect: str,
    photo_seconds: float,
    library_root: Optional[Path] = None,
    overlay_mode: str = OVERLAY_EMBEDDED,
    transition_ms: int = DEFAULT_TRANSITION_MS,
    overwrite: bool = False,
    bundled_fallback: Optional[Path] = None,
    stem: str = DEFAULT_OUTPUT_STEM,
) -> Path:
    """The convenience wrapper the export pipelines call: resolve the
    skeleton, pick the output path (disambiguated unless ``overwrite``),
    and write the `.pte` into ``folder``. Returns the path written.

    The chosen output naming follows spec/107 §4 — a re-export NEVER
    silently clobbers a project the user has since edited in PTE unless
    they explicitly confirm via ``overwrite=True``. ``stem`` (spec/121
    §2) parameterises the filename: callers pass the Cut's tag so each
    Cut's project takes its own name instead of the generic
    ``slideshow.pte``."""
    folder = Path(folder)
    skeleton_text = load_skeleton(library_root=library_root,
                                  bundled_fallback=bundled_fallback)
    target = slideshow_target(folder, stem=stem, overwrite=overwrite)
    text = generate(
        skeleton_text, members, audio_tracks,
        aspect=aspect, photo_seconds=photo_seconds,
        project_path=target, images_folder=folder,
        overlay_mode=overlay_mode, transition_ms=transition_ms,
    )
    write_pte(text, target)
    log.info("generated PTE project at %s (%d slides, %d audio tracks)",
             target, len(members), len(audio_tracks))
    return target


__all__ = [
    "DEFAULT_OUTPUT_NAME",
    "DEFAULT_OUTPUT_STEM",
    "DEFAULT_TRANSITION_MS",
    "OVERLAY_EMBEDDED", "OVERLAY_BURN_IN", "OVERLAY_OFF",
    "TEXT_PHOTO_CAPTION", "TEXT_SEP_TITLE", "TEXT_SEP_SUB",
    "TEXT_OPENER_TITLE", "TEXT_OPENER_SUB",
    "PteMember", "PteText", "PteAudioTrack",
    "Skeleton",
    "parse_skeleton",
    "capture_skeleton",
    "load_skeleton",
    "bundled_skeleton_path",
    "write_pte",
    "slideshow_target",
    "generate",
    "generate_into_folder",
    "fresh_guid",
]
