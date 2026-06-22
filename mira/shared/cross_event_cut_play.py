"""Cross-event Cut play adapter (spec/94 Phase 4a-iii).

The event-scope :func:`mira.shared.cut_session.show_entries` walks one
event's lineage. A cross-event Cut spans events (spec/93 §3), so this
module does the equivalent walk against:

* mira.db's ``cut_member`` rows (the membership, library-stored), and
* mira.db's ``global_items`` projection (the per-item facts —
  capture_time, kind, duration_ms — already maintained for the
  resolver).

The output is the same ``[(kind, payload)]`` shape
:class:`mira.ui.shared.cut_play.CutPlayerDialog` consumes, so the player
itself doesn't grow special-case logic.

Each file payload is a :class:`CrossEventPlayFile` carrying the source
event UUID alongside the relpath; the player's ``resolve_path``
callable composes that with the umbrella gateway's ``index.resolve_root``
to land on the right ``Exported Media/<relpath>``.

Pure logic — no Qt. The caller (the new
:class:`mira.ui.pages.library_page.LibraryPage`) wires the player and
provides the umbrella gateway.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrossEventPlayFile:
    """One playable cell of a cross-event Cut.

    Carries the source event's UUID so the player can route to the
    right ``Exported Media/`` (or ``Original Media/`` for grab-kind
    members) tree; otherwise field-shape-compatible with
    :class:`mira.shared.cut_session.SessionFile` so
    :class:`CutPlayerDialog` doesn't need a special case for it
    (kind / export_relpath / capture_time / duration_ms / day_number)."""

    event_uuid: str
    export_relpath: str = ""
    origin_relpath: str = ""
    member_kind: str = "export"           # 'export' | 'grab'
    kind: str = "photo"                   # 'photo' | 'video'
    capture_time: Optional[str] = None
    duration_ms: int = 0
    day_number: Optional[int] = None      # used only for separator generation


def build_cross_event_entries(
    *,
    library_gateway,
    cut_id: str,
    separators_on: bool = False,
) -> Tuple[List[Tuple[str, object]], dict]:
    """Walk a cross-event Cut's members (mira.db) + the projection's
    per-item facts; return the player-shaped entries list AND a day
    metadata map keyed by the separator's day token.

    Separators on cross-event Cuts default OFF (spec/81 §3.1 — no
    single timeline to orient). When the caller flips them on, every
    ``(event_uuid, day)`` boundary gets one separator (the per-event
    day grouping matches the rest of the cross-event surface).

    Returns:
        ``(entries, day_meta)``
        - ``entries`` is the ``[(kind, payload)]`` list
          :class:`CutPlayerDialog` consumes. ``kind`` is one of
          ``'opener'`` (when separators are on + members exist),
          ``'sep'`` (day boundary), ``'file'`` (a CrossEventPlayFile).
        - ``day_meta`` maps the ``sep`` payload (a ``(event_uuid, day)``
          tuple) to a small object exposing the fields
          :func:`mira.ui.shared.separator_card.render_separator_image`
          reads (``date``, ``location``, ``description``).
    """
    members = library_gateway.cross_event_cut_members(cut_id)
    if not members:
        return [], {}

    # Pull every projection row for these (event_uuid, relpath) pairs in
    # one query. The match is on (event_uuid, export_relpath) for
    # 'export' members and (event_uuid, origin_relpath) for 'grab' —
    # the projection row carries both columns, only one populated.
    keys: list = []
    for m in members:
        relpath = (
            m.origin_relpath if m.kind == "grab" else m.export_relpath)
        if relpath:
            keys.append((m.event_id, relpath))
    facts_by_key = _read_facts(library_gateway, keys)

    files: List[CrossEventPlayFile] = []
    for m in members:
        relpath = m.origin_relpath if m.kind == "grab" else m.export_relpath
        if not relpath:
            log.debug(
                "cross_event_cut_play: skipping member without relpath: "
                "cut=%s event=%s", cut_id, m.event_id)
            continue
        facts = facts_by_key.get((m.event_id, relpath))
        kind = (facts.get("kind") if facts else None) or "photo"
        capture_time = facts.get("capture_time") if facts else None
        duration_ms = int((facts.get("duration_ms") or 0) if facts else 0)
        files.append(CrossEventPlayFile(
            event_uuid=m.event_id,
            export_relpath=m.export_relpath or "",
            origin_relpath=m.origin_relpath or "",
            member_kind=m.kind,
            kind=kind,
            capture_time=capture_time,
            duration_ms=duration_ms if kind == "video" else 0,
        ))

    # Chronological order — capture_time first, ties broken by
    # (event_uuid, relpath) for determinism.
    files.sort(key=lambda f: (
        f.capture_time or "",
        f.event_uuid,
        f.export_relpath or f.origin_relpath,
    ))

    # Day separators (when ON): a "day" here is the ISO date of
    # capture_time; the per-event grouping is implicit via the
    # (event_uuid, date) tuple so the same calendar day in two events
    # earns two separators (spec/81 §3.1).
    entries: List[Tuple[str, object]] = []
    day_meta: dict = {}
    if separators_on and files:
        entries.append(("opener", None))
    last_token: object = object()
    for f in files:
        if separators_on:
            day_iso = (f.capture_time or "")[:10] or None
            token = (f.event_uuid, day_iso)
            if token != last_token and day_iso is not None:
                last_token = token
                entries.append(("sep", token))
                day_meta.setdefault(token, _SeparatorMeta(
                    date=day_iso, location="", description=""))
                # day_number on the file is the separator's token —
                # used by the player only for grouping in the scrubber.
                object.__setattr__(f, "day_number", token)
        entries.append(("file", f))
    return entries, day_meta


def make_resolve_path(*, gateway) -> Callable[[object], Path]:
    """Build the ``resolve_path`` callable :class:`CutPlayerDialog`
    accepts. Maps a :class:`CrossEventPlayFile` to its source event's
    bytes path:

    * ``'export'`` members → ``<event_root>/<export_relpath>``
    * ``'grab'`` members → ``<event_root>/<origin_relpath>``

    A missing source event (deleted / relocated out of band) yields a
    path that doesn't exist — :class:`CutPlayerDialog`'s "missing"
    fallback handles it without crashing."""
    def _resolve(payload) -> Path:
        eid = getattr(payload, "event_uuid", "")
        entry = gateway.index.get(eid) if eid else None
        if entry is None:
            return Path(getattr(payload, "export_relpath", "") or "")
        root = gateway.index.resolve_root(entry, gateway.photos_base_path())
        if root is None:
            return Path(getattr(payload, "export_relpath", "") or "")
        relpath = (
            payload.origin_relpath if payload.member_kind == "grab"
            else payload.export_relpath)
        return root / relpath
    return _resolve


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _SeparatorMeta:
    """Minimal shape the separator-card renderer reads. Cross-event
    Cuts don't carry the per-trip-day's location / description today
    (those are per-event facts in event.db); the date is enough for
    the v1 separator. A future slice can join the per-event day data
    if the user wants the richer card."""
    date: Optional[str]
    location: Optional[str]
    description: str


def _read_facts(library_gateway,
                keys: Sequence[Tuple[str, str]]) -> dict:
    """For each ``(event_uuid, relpath)`` pair, return the matching
    projection row's facts as a dict. Unknown pairs map to ``None``."""
    if not keys:
        return {}
    # Use OR clauses so the query runs in one round trip; param count
    # stays small (typical cuts ship < 1000 members) so a literal
    # join is fine.
    where = " OR ".join(
        "(event_uuid = ? AND (export_relpath = ? OR origin_relpath = ?))"
        for _ in keys
    )
    params: list = []
    for event_uuid, relpath in keys:
        params.extend([event_uuid, relpath, relpath])
    sql = (
        "SELECT event_uuid, export_relpath, origin_relpath, "
        "       kind, capture_time, duration_ms "
        f"FROM global_items WHERE {where}"
    )
    rows = library_gateway.user_store.conn.execute(sql, params).fetchall()
    out: dict = {}
    for r in rows:
        for rel_col in ("export_relpath", "origin_relpath"):
            relpath = r[rel_col]
            if not relpath:
                continue
            out.setdefault((r["event_uuid"], relpath), {
                "kind": r["kind"],
                "capture_time": r["capture_time"],
                "duration_ms": r["duration_ms"],
            })
    return out


__all__ = [
    "CrossEventPlayFile",
    "build_cross_event_entries",
    "make_resolve_path",
]
