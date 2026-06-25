"""Record export → source lineage rows after an Edit export run.

A ``lineage`` row records *which source ``Item`` produced which exported file
on disk*, scoped to the Edit phase. Share/Curate then walks back from each
processed file to its original Item (for source-bytes archival, EXIF readout,
classification-aware genre filters, etc.).

Edit's naming is deterministic
(:mod:`core.process_export_engine` §dest_name): ``<dest_dir>/<src.stem>.jpg``
(or ``.tif``). So we can map an output path back to its source by stem,
without needing the engine to report (src, dest) pairs for the
non-collision cases. The ``ExportResult.renamed`` rows carry the source
directly, and we use that explicitly.

This helper is the one place lineage gets written for the Edit phase. The
three Edit export entry points (host batch · single photo · video clip)
all call into here so the convention can never drift.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from core.photo_thumb_cache import queue_export_thumb
from mira.gateway.event_gateway import EventGateway
from mira.store import models as m

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _recipe_payload(
    recipe: Optional[dict], resolved: Optional[dict],
) -> Optional[str]:
    """Serialise the spec/54 §8 snapshot: the CHOICE the user made
    (style / look / creative_filter / crop / rotation) plus the
    resolved Params the render actually used, plus any active
    calibration trims (spec/54 §4.1 — a version must remember the
    knob positions it was rendered under). Archival, append-only —
    never re-read for rendering."""
    if recipe is None and resolved is None:
        return None
    payload = dict(recipe or {})
    if resolved is not None:
        payload["resolved_params"] = resolved
    try:
        from core.photo_auto import active_tone_scaling
        trims = active_tone_scaling()
        if trims:
            payload["tone_scaling"] = dict(trims)
    except Exception:                                  # noqa: BLE001
        pass
    return json.dumps(payload)


def record_edit_export_lineage(
    eg: EventGateway,
    event_root: Path,
    *,
    items_with_sources: Iterable[tuple[str, Path]],
    result,
    recipe_by_item: Optional[dict[str, dict]] = None,
    resolved_by_stem: Optional[dict[str, dict]] = None,
) -> int:
    """Walk every successful output path in ``result`` and record a
    ``lineage`` row linking it back to its source ``Item.id``.

    ``items_with_sources`` is an iterable of ``(item_id, source_path)``
    pairs — the input set the export was asked to write. The helper builds
    a stem-keyed lookup and matches each output's stem back to a source.

    ``ExportResult.renamed`` rows are ``(src, dest)`` tuples — we use the
    src directly. Every other bucket (``written`` / ``overwritten`` /
    ``already_present``) carries dest paths only — matched by stem.
    Renamed outputs are exactly the versions-as-exports case (spec/54
    §8): a re-export under the UNIQUE policy lands as ``stem (2).jpg``
    with its own lineage row — Share groups them by source item.

    ``recipe_by_item`` carries each item's CHOICE; ``resolved_by_stem``
    the engine's params_sink (keyed by source filename — matched here
    by stem). Together they become the ``recipe_json`` snapshot;
    ``exported_at`` stamps the run.

    Returns the number of rows written. Errors against a single row are
    logged + skipped — one bad write must not block lineage for the rest.
    """
    stem_to_item: dict[str, str] = {}
    for item_id, src in items_with_sources:
        stem_to_item[Path(src).stem] = item_id
    resolved_lookup: dict[str, dict] = {}
    for name, params in (resolved_by_stem or {}).items():
        resolved_lookup[Path(name).stem] = params

    pairs: list[tuple[Optional[str], str, Path]] = []
    for p in (getattr(result, "written", None) or []):
        pairs.append((stem_to_item.get(Path(p).stem), Path(p).stem, Path(p)))
    for p in (getattr(result, "overwritten", None) or []):
        pairs.append((stem_to_item.get(Path(p).stem), Path(p).stem, Path(p)))
    for src, dest in (getattr(result, "renamed", None) or []):
        pairs.append(
            (stem_to_item.get(Path(src).stem), Path(src).stem, Path(dest)))
    for p in (getattr(result, "already_present", None) or []):
        pairs.append((stem_to_item.get(Path(p).stem), Path(p).stem, Path(p)))

    stamp = _utc_now_iso()
    n = 0
    for item_id, src_stem, dest in pairs:
        if item_id is None:
            log.debug(
                "edit-lineage: no source item for %s (stem=%s)",
                dest, dest.stem,
            )
            continue
        try:
            rel = dest.relative_to(event_root)
        except ValueError:
            # Custom destination outside the event tree (user picked a
            # folder elsewhere) — Share can't read those anyway. Skip.
            log.debug(
                "edit-lineage: dest %s is outside event_root %s — skipping",
                dest, event_root,
            )
            continue
        try:
            eg.record_lineage(m.Lineage(
                export_relpath=rel.as_posix(),       # DB convention: forward slashes
                phase="edit",
                source_kind="item",
                source_item_id=item_id,
                recipe_json=_recipe_payload(
                    (recipe_by_item or {}).get(item_id),
                    resolved_lookup.get(src_stem)),
                exported_at=stamp,
            ))
            n += 1
            # spec/63 slice 8 — queue the Cut-grid thumb for the new
            # export (background builder; never inline — a batch of
            # hundreds must not stall the foreground).
            queue_export_thumb(event_root, rel.as_posix())
        except Exception:                              # noqa: BLE001
            log.exception(
                "record_lineage failed for %s → %s", item_id, rel)
    return n


def record_single_lineage(
    eg: EventGateway,
    event_root: Path,
    *,
    item_id: str,
    dest_path: Path,
    recipe: Optional[dict] = None,
    resolved_params: Optional[dict] = None,
    duration_ms: Optional[int] = None,
) -> bool:
    """One-shot helper for video clip exports (no ``ExportResult`` —
    the video worker returns ``(ok, out_path, cancelled)``). Records
    a single lineage row (with the spec/54 §8 snapshot when the caller
    has it); returns True on success.

    spec/144 — when ``duration_ms`` is given (the render worker emits
    ``(out_ms - in_ms) / speed`` per clip), it lands on the lineage row
    so the budget, cut-play scrubber, and PTE generator all read the
    SEGMENT's real on-disk length instead of the source video's whole
    duration. Photos pass ``None`` (their length is ``photo_s``, not a
    clip property)."""
    try:
        rel = dest_path.relative_to(event_root)
    except ValueError:
        log.debug(
            "edit-lineage: dest %s is outside event_root %s — skipping",
            dest_path, event_root,
        )
        return False
    try:
        eg.record_lineage(m.Lineage(
            export_relpath=rel.as_posix(),           # DB convention: forward slashes
            phase="edit",
            source_kind="item",
            source_item_id=item_id,
            recipe_json=_recipe_payload(recipe, resolved_params),
            exported_at=_utc_now_iso(),
            duration_ms=(int(duration_ms)
                         if isinstance(duration_ms, (int, float))
                         and int(duration_ms) > 0 else None),
        ))
        # spec/63 slice 8 — clips no-op inside (non-image suffix);
        # photo singles routed here get their Cut-grid thumb queued.
        queue_export_thumb(event_root, rel.as_posix())
        return True
    except Exception:                                  # noqa: BLE001
        log.exception(
            "record_lineage failed for %s → %s", item_id, rel)
        return False
