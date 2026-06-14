"""Cull export resolver — Stage C inc.2 (frozen docs/18 §Export +
the 2026-05-18 classification correction).

Turns *kept photos* into the :class:`~core.cull_export.ExportItem`
manifest the engine copies. Two pure pieces (no Qt, no journal I/O
— the caller injects EXIF so this is exhaustively testable):

- :func:`effective_style` — the **one shared** scenario resolver:
  user override ?? cached auto-classification ?? classify now.
  This is the exact recipe the canvas Genre readout / **Reclassify**
  button and ``cull_ingest.commit_from_session._genre_for`` use, so
  the photo lands under the **same** Style everywhere
  (classification is the spine of the culler — never bypassed).
- :func:`build_export_manifest` — kept items → ``ExportItem``s
  under ``<event_root>/02 Selected/<Dia N - desc>/<Style>`` (a
  **bracket** bucket adds a ``<bracket_id>/`` sub-folder — the
  frozen handoff unit for Helicon/HDR); destination filename gets
  the ``DateTimeOriginal`` courtesy prefix.

The journal-scope gathering (kept-set per scope, RAW↔JPG sibling
expansion, mapping each kept file to its Dia N) is the disk/wiring
layer (inc.3/inc.4); it feeds :class:`KeptItem`s into here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from core.classifier_v2 import Scenario
from core.cull_export import ExportItem, courtesy_filename
from core.genre import (
    classify_exif,
    get_genre_override,
    peek_auto_genre,
)
from core.path_builder import culled_dir, sanitize_folder_name

log = logging.getLogger(__name__)


def effective_style(
    journal: dict,
    filename: str,
    raw_exif: Optional[dict],
    *,
    source: str = "camera",
) -> str:
    """The scenario this photo IS filed under, as a folder-safe
    slug. Resolution order (identical to the canvas + the legacy
    commit, so Style agrees everywhere):

    1. user **override** (the Reclassify button) — sticky truth;
    2. the **cached** auto-classification (set when the photo was
       viewed/scanned — never recomputed here, Speed-is-King);
    3. **classify now** from ``raw_exif`` when given (the kept set
       is small + this runs at Export, off the cull hot path);
    4. ``GENERAL`` when there is nothing to classify with.

    ``journal`` is the photo's cull journal (per-bucket / per-day);
    ``raw_exif`` is its EXIF dict (caller reads it — keeps this
    pure/testable). Never raises (a bad photo must not abort a
    1000-file export)."""
    ov = get_genre_override(journal, filename)
    if ov:
        return sanitize_folder_name(ov)
    pk = peek_auto_genre(journal, filename)
    if pk is not None:
        return sanitize_folder_name(pk[0])
    if raw_exif:
        try:
            sc = classify_exif(
                Path(filename), raw_exif, source=source,
            ).scenario.value
            return sanitize_folder_name(sc)
        except Exception as exc:  # noqa: BLE001 — never break export
            log.warning("export classify failed for %s: %s",
                        filename, exc)
    return sanitize_folder_name(Scenario.GENERAL.value)


@dataclass(frozen=True)
class KeptItem:
    """One kept photo to export. ``day_label`` is the canonical
    ``Dia N - desc`` folder name (caller built it via
    ``path_builder.day_folder_name``); ``style`` is the slug from
    :func:`effective_style`; ``bracket_id`` is set ONLY for members
    of a focus/exposure-bracket bucket (→ a sub-folder; every other
    bucket is a cull-time abstraction and lands flat).

    Per-camera layout (Nelson 2026-05-20 v4):

    * ``bucket`` — the cull-context bucket
      (:data:`CAPTURED_CAMERAS_SUBDIR` / phones / other). When
      non-empty, the destination path inserts it before the day:
      ``<dest_root>/<bucket>/<day>/<camera_id>/<style>/``. This is
      the Cull-Export shape — keepers stay separated by source.
    * ``camera_id`` — from the file's EXIF Make/Model (or "" when
      unreadable). Appears in the destination path only when
      ``bucket`` is non-empty. Always carried so Select-Export can
      look up the right per-camera calibration for the EXIF rewrite.
    * Empty ``bucket`` = the **consolidated** layout
      ``<dest_root>/<day>/<style>/`` (Select-Export's destination;
      camera_id is not part of the path because consolidation IS
      the point of Select).
    """

    src: Path
    capture_dt: Optional[datetime]
    day_label: str
    style: str
    bracket_id: Optional[str] = None
    # The corrected time to bake into the exported COPY's EXIF
    # — set ONLY when this file's camera clock was off (a real
    # shift). None = pass-through, no EXIF write.
    #
    # Lives on EVERY KeptItem; the WRITE decision is the caller's:
    # Cull-Export passes ``bake_exif=False`` and the bake never
    # triggers; Select-Export passes ``bake_exif=True`` and rewrites
    # land on the consolidated copies (frozen 2026-05-20 v4 — Model
    # 3's bake-at-Cull stance superseded; cull keepers stay
    # byte-identical to source).
    exif_datetime: Optional[datetime] = None
    # Per-camera layout fields (Nelson 2026-05-20 v4).
    bucket: str = ""
    camera_id: str = ""


def build_export_manifest(
    items: Iterable[KeptItem],
    dest_root: Path,
) -> list[ExportItem]:
    """Kept items → the copy manifest under ``dest_root``.

    Two layout shapes selected per ``KeptItem.bucket``:

    * **Per-camera (Cull-Export, frozen 2026-05-20 v4):** when
      ``bucket`` is non-empty, the path becomes
      ``<dest_root>/<bucket>/<day>/<camera_id>/<style>/[<bracket>/]``.
      Cameras stay separated until Select consolidates.
    * **Consolidated (Select-Export):** when ``bucket`` is empty,
      the path stays the old shape ``<dest_root>/<day>/<style>/
      [<bracket>/]``. ``camera_id`` is not part of the path because
      Select IS the consolidation step.

    Destination filename carries the ``DateTimeOriginal`` courtesy
    prefix. Pure — no disk access, no classification."""
    root = Path(dest_root)
    out: list[ExportItem] = []
    for it in items:
        if it.bucket:
            # Per-camera Cull-Export layout.
            cam = sanitize_folder_name(it.camera_id) or "_unknown_camera"
            dest_dir = (
                root / sanitize_folder_name(it.bucket)
                / it.day_label
                / cam
                / sanitize_folder_name(it.style)
            )
        else:
            # Consolidated Select-Export layout.
            dest_dir = root / it.day_label / sanitize_folder_name(it.style)
        if it.bracket_id:
            dest_dir = dest_dir / sanitize_folder_name(it.bracket_id)
        out.append(ExportItem(
            src=Path(it.src),
            dest_dir=dest_dir,
            dest_name=courtesy_filename(
                Path(it.src).name, it.capture_dt),
            exif_datetime=it.exif_datetime,
        ))
    return out


def event_default_dest(event_root: Path) -> Path:
    """The Export dialog's **default** destination for an in-event
    **Cull** (During-trip cameras / Phone / Other):
    ``<event_root>/01 - Culled`` (FROZEN 2026-05-19 pipeline
    taxonomy — docs/18 §"Pipeline taxonomy & phase model"; Cull
    output is `01 - Culled`, the Select phase later consolidates →
    `02 - Selected`). The user may still pick anywhere — the
    manifest lays ``Day/Style`` under whatever they choose."""
    return culled_dir(Path(event_root))
