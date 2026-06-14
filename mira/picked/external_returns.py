"""The external round trip's RETURN seams (spec/57 §3).

External tools work on the ``Picked Media/`` links projection and hand
results back in two ways, both discovered by :func:`scan_for_returns`
(run on entering Edit + from the Edit menu's scan action — no watchers,
spec/57 §3.3):

* **Stacker outputs** land at the projection ROOT (spec/57 §2.3). A
  foreign root file whose stem STARTS WITH a picked bracket member's
  link stem is adopted as that bracket's final master
  (:meth:`EventGateway.adopt_stack_output` — bytes move to
  ``Original Media/Merged/``, the item + ``stack_bracket`` rows are
  written, the master is picked-by-construction). The caller rebuilds
  the links afterwards so the master appears at the root seamlessly.
* **Editor returns** (LRC-class) land in subdirs of ``Edited Media/``.
  A file not yet in ``lineage`` whose stem starts with ANY item's link
  stem is associated back to that item as an external edited output —
  a ``lineage`` row (``recipe_json`` NULL = external), composing with
  versions-as-exports (spec/54 §8).

Files matching nothing are FLAGGED in the report — never silently
ignored (spec/57 §3.2). Known sidecar noise (``.xmp`` etc.) is skipped
silently. The report also carries the derived reminder facts
(spec/57 §3.4): picked focus/exposure brackets with no merged result.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.path_builder import edited_media_dir
from core.photo_thumb_cache import queue_export_thumb
from core.picked_media import PickedEntry, foreign_root_files, link_name
from mira.picked.edit_model import picked_media_entries
from mira.picked.status import STATE_SKIPPED
from mira.store import models as m

log = logging.getLogger(__name__)

#: Sidecar / auxiliary extensions external editors drop beside their
#: exports — ignored without flagging (they are not returns).
_SIDECAR_EXTS = frozenset({".xmp", ".tmp", ".ini", ".db", ".json", ".txt"})


@dataclass
class ReturnsReport:
    adopted: List[str] = field(default_factory=list)      # merged-master filenames
    associated: List[str] = field(default_factory=list)   # editor-return relpaths
    unmatched: List[str] = field(default_factory=list)    # flagged, never ignored
    unmerged_bracket_count: int = 0                       # the derived reminder fact
    errors: List[str] = field(default_factory=list)

    @property
    def nothing_happened(self) -> bool:
        return not (self.adopted or self.associated or self.unmatched
                    or self.errors)


def _link_stem(entry: PickedEntry) -> str:
    return Path(link_name(entry)).stem


def _all_item_stems(gateway, event_root: Path) -> Dict[str, str]:
    """``link-stem → item_id`` over EVERY byte-bearing root-level item —
    pick-state-independent, so an item re-skipped after an external edit
    still associates its return."""
    out: Dict[str, str] = {}
    for it in gateway.items():
        if not it.origin_relpath:
            continue
        stem = _link_stem(PickedEntry(
            source_path=event_root / it.origin_relpath,
            filename=Path(it.origin_relpath).name,
            day_number=it.day_number,
            camera_id=it.camera_id,
        ))
        out[stem] = it.id
    return out


def _match_stem(stem: str, stems: Dict[str, str]) -> Optional[str]:
    """The spec/57 §3.2 rule: ``stem`` starts with a known link stem.
    Prefixes are unique by construction; prefer the LONGEST match so a
    stem that happens to extend another item's stem resolves to the
    more specific source."""
    best: Optional[Tuple[int, str]] = None
    for known, value in stems.items():
        if stem.startswith(known) and (best is None or len(known) > best[0]):
            best = (len(known), value)
    return best[1] if best else None


def scan_for_returns(
    gateway, pick_default_state: str = STATE_SKIPPED,
) -> ReturnsReport:
    """Run both return legs + compute the reminder facts. Mutates the
    event (adoptions + lineage rows) but never deletes anything except
    a successfully-adopted source file; every unmatched file is
    reported and left exactly where the tool wrote it."""
    report = ReturnsReport()
    if gateway.event_root is None:
        report.errors.append("event root unresolvable — scan skipped")
        return report
    event_root = Path(gateway.event_root)

    entries = picked_media_entries(gateway, pick_default_state)

    # ── Leg A — stacker outputs at the projection root ─────────────────
    memberships = {}
    try:
        memberships = gateway.bracket_memberships("pick")
    except Exception:  # noqa: BLE001
        log.exception("bracket memberships unavailable; root scan still runs")
    # member link-stem → bracket_key, and bracket_key → (kind, member ids)
    member_stems: Dict[str, str] = {}
    brackets: Dict[str, Tuple[str, List[str]]] = {}
    for e in entries:
        if not e.bracket_group_id or not e.item_id:
            continue
        member_stems[_link_stem(e)] = e.bracket_group_id
        kind = memberships.get(e.item_id, (None, "focus_bracket"))[1]
        bucket = brackets.setdefault(e.bracket_group_id, (kind, []))
        bucket[1].append(e.item_id)

    for f in foreign_root_files(event_root):
        if f.suffix.lower() in _SIDECAR_EXTS or f.name.startswith("."):
            continue
        bracket_key = _match_stem(f.stem, member_stems)
        if bracket_key is None:
            report.unmatched.append(f.name)
            continue
        kind, member_ids = brackets[bracket_key]
        try:
            gateway.adopt_stack_output(
                f, bracket_key=bracket_key, bracket_kind=kind,
                member_item_ids=member_ids,
            )
            report.adopted.append(f.name)
        except Exception as exc:  # noqa: BLE001 — one bad file never stops the scan
            log.exception("stack adoption failed for %s", f.name)
            report.errors.append(f"{f.name}: {exc}")

    # ── Leg B — editor returns under Edited Media ──────────────────────
    known_exports = {l.export_relpath for l in gateway.lineage()}
    stems = _all_item_stems(gateway, event_root)
    edited_root = edited_media_dir(event_root)
    if edited_root.is_dir():
        for f in sorted(edited_root.rglob("*")):
            if not f.is_file() or f.name.startswith("."):
                continue
            if f.suffix.lower() in _SIDECAR_EXTS:
                continue
            rel = f.relative_to(event_root).as_posix()
            if rel in known_exports:
                continue
            source_id = _match_stem(f.stem, stems)
            if source_id is None:
                report.unmatched.append(rel)
                continue
            try:
                gateway.record_lineage(m.Lineage(
                    export_relpath=rel, phase="edit", source_kind="item",
                    source_item_id=source_id, recipe_json=None,
                ))
                report.associated.append(rel)
                # spec/63 slice 8 — Cut-grid thumb, background builder.
                queue_export_thumb(event_root, rel)
            except Exception as exc:  # noqa: BLE001
                log.exception("return association failed for %s", rel)
                report.errors.append(f"{rel}: {exc}")

    # ── Leg C — the derived reminder fact (spec/57 §3.4) ───────────────
    merged = {sb.bracket_id for sb in gateway.stacks() if sb.output_item_id}
    report.unmerged_bracket_count = sum(
        1 for key in brackets if key not in merged)

    log.info(
        "external-returns scan: %d adopted, %d associated, %d unmatched, "
        "%d unmerged bracket(s), %d error(s)",
        len(report.adopted), len(report.associated), len(report.unmatched),
        report.unmerged_bracket_count, len(report.errors),
    )
    return report
