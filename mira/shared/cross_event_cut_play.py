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

    # spec/154 — event_uuid → name, for the separator card title (a
    # cross-event "day" boundary is labelled by its SOURCE EVENT, not a
    # "Day N"). Tolerant of a gateway that can't list events.
    names_by_uuid: dict = {}
    try:
        for e in library_gateway.list_events_for_scope():
            names_by_uuid[e.get("uuid")] = e.get("name") or None
    except Exception:                                              # noqa: BLE001
        log.debug("cross_event entries: list_events_for_scope failed",
                  exc_info=True)

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
    # spec/143 + spec/154 — the opener is the show's title slide and
    # carries the Cut's provenance summary; it is independent of the
    # separators toggle (mirrors the per-event opener).
    if files:
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
                    date=day_iso, location="", description="",
                    title=names_by_uuid.get(f.event_uuid)))
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
    """Minimal shape the separator-card renderer reads. ``title`` is the
    cross-event override (spec/154 — the SOURCE EVENT name, since a
    cross-event Cut has no single "Day N" timeline); ``date`` is the day
    of that event the boundary falls on. ``location`` / ``description``
    aren't carried cross-event today (they're per-event facts in
    event.db) — a future slice can join them if the user wants a richer
    card."""
    date: Optional[str]
    location: Optional[str]
    description: str
    title: Optional[str] = None


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


def _read_provenance(library_gateway,
                     keys: Sequence[Tuple[str, str]]) -> dict:
    """``{(event_uuid, relpath) → FrameProvenance}`` from the global_items
    projection, which already denormalises every overlay field (spec/154 —
    no need to open each source event)."""
    from core.cut_overlay import FrameProvenance
    if not keys:
        return {}
    where = " OR ".join(
        "(event_uuid = ? AND (export_relpath = ? OR origin_relpath = ?))"
        for _ in keys)
    params: list = []
    for event_uuid, relpath in keys:
        params.extend([event_uuid, relpath, relpath])
    sql = (
        "SELECT event_uuid, export_relpath, origin_relpath, capture_time, "
        "       country, day_city, day_sublocation, camera_id, lens_model, "
        "       flash_fired, iso, aperture_f, shutter_speed_s, "
        "       focal_length_mm "
        f"FROM global_items WHERE {where}"
    )
    rows = library_gateway.user_store.conn.execute(sql, params).fetchall()
    out: dict = {}
    for r in rows:
        prov = FrameProvenance(
            when=r["capture_time"],
            city=r["day_city"], sublocation=r["day_sublocation"],
            country=r["country"], camera=r["camera_id"],
            lens_model=r["lens_model"],
            flash_fired=(None if r["flash_fired"] is None
                         else bool(r["flash_fired"])),
            aperture_f=r["aperture_f"], shutter_speed_s=r["shutter_speed_s"],
            iso=r["iso"], focal_length_mm=r["focal_length_mm"])
        for rel_col in ("export_relpath", "origin_relpath"):
            relpath = r[rel_col]
            if relpath:
                out.setdefault((r["event_uuid"], relpath), prov)
    return out


#: English month abbreviations for :func:`compose_origin_label`. Pure logic
#: (no Qt → no ``tr()``); a future localisation slice can swap these for a
#: locale-aware formatter without changing the call sites.
_MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _format_origin_date(capture_time: Optional[str]) -> Optional[str]:
    """``'2025-09-28T11:26:41'`` → ``'28 Sep 2025'``. Reads the leading
    ISO ``YYYY-MM-DD``; returns ``None`` for a missing / unparseable value
    (charter §5.3 — tolerate, don't crash)."""
    if not capture_time:
        return None
    iso = str(capture_time).strip()[:10]
    try:
        year, month, day = (int(x) for x in iso.split("-"))
        label = _MONTH_ABBR[month - 1]
    except (ValueError, IndexError):
        return None
    return f"{day} {label} {year}"


def compose_origin_label(
    event_name: Optional[str], capture_time: Optional[str]
) -> Optional[str]:
    """spec/154 — the per-slide origin string: source event name + capture
    date, e.g. ``'Salta, Argentina · 28 Sep 2025'``. Either half may be
    missing (an unnamed event, a frame with no capture time); returns the
    available part, or ``None`` when both are absent.

    The one composer for the origin label — Play feeds it through
    :func:`cross_event_origin_resolver`, and the cross-event PTE generator
    (spec/154 slice B) reuses it to emit the same string as an editable
    ``:Text`` object."""
    parts = []
    if event_name:
        parts.append(str(event_name))
    date_text = _format_origin_date(capture_time)
    if date_text:
        parts.append(date_text)
    return " · ".join(parts) if parts else None


def cross_event_origin_resolver(
    library_gateway, cut_id: str
) -> Callable[[object], Optional[str]]:
    """spec/154 — the resolver the cross-event player hands
    :class:`CutPlayerDialog` for the per-slide **origin label** (top of
    slide): given a play payload, return ``'EventName · 28 Sep 2025'`` (or
    ``None``). Built once — one ``list_events_for_scope`` call for the
    event-name map — so the per-frame closure is O(1). Keys on
    ``event_uuid`` + the payload's ``capture_time``."""
    names_by_uuid: dict = {}
    try:
        for e in library_gateway.list_events_for_scope():
            names_by_uuid[e.get("uuid")] = e.get("name") or None
    except Exception:                                              # noqa: BLE001
        log.debug("cross_event origin: list_events_for_scope failed",
                  exc_info=True)

    def resolve(payload) -> Optional[str]:
        return compose_origin_label(
            names_by_uuid.get(getattr(payload, "event_uuid", "")),
            getattr(payload, "capture_time", None))
    return resolve


def cross_event_provenance_resolver(
    library_gateway, cut_id: str
) -> Callable[[object], object]:
    """spec/154 — the resolver the cross-event player hands
    :class:`CutPlayerDialog`: given a play payload, return its
    :class:`~core.cut_overlay.FrameProvenance` (or ``None``). Built once —
    one projection query for all members — so the per-frame closure is
    O(1). Keys on ``(event_uuid, relpath)`` because the same filename can
    live in two source events."""
    members = library_gateway.cross_event_cut_members(cut_id)
    keys: list = []
    for m in members:
        relpath = m.origin_relpath if m.kind == "grab" else m.export_relpath
        if relpath:
            keys.append((m.event_id, relpath))
    prov_by_key = _read_provenance(library_gateway, keys)

    def resolve(payload):
        relpath = (getattr(payload, "export_relpath", "")
                   or getattr(payload, "origin_relpath", ""))
        return prov_by_key.get(
            (getattr(payload, "event_uuid", ""), relpath))
    return resolve


__all__ = [
    "CrossEventPlayFile",
    "build_cross_event_entries",
    "compose_origin_label",
    "cross_event_origin_resolver",
    "cross_event_provenance_resolver",
    "make_resolve_path",
]
