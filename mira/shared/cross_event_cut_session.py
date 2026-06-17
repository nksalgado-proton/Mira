"""The cross-event pin session — DC → Cut, library-wide (spec/81 Phase 2).

Cross-event sibling of :mod:`mira.shared.cut_session`. The event-scope session
sources candidates from one ``event.db``'s lineage; this one sources them from
``mira.db``'s ``global_items`` projection, so the Pick/Skip ledger spans every
event whose item the DC resolved to. Same vocabulary, same decision discipline,
same "nothing persists until Create Cut" rule.

Where it differs from event scope:

* **Candidate identity is ``(event_uuid, item_id)``**, not a bare export
  relpath. Two events can ship files with the same relative path; only the
  composite key disambiguates. The session exposes packed keys for the ledger
  (``"<event_uuid>::<item_id>"`` via
  :func:`mira.gateway.cross_event_resolver.pack_key`) so a ledger dict stays
  hashable.
* **Each candidate carries its source event.** :class:`CrossEventSessionFile`
  adds ``event_uuid`` to the event-scope shape so the export pipeline knows
  which event's ``Exported Media/`` to link from.
* **Commit writes to an anchor event's ``event.db``** (schema v8 — spec/81
  Phase 2). The cross-event Cut LIVES in one event.db (the anchor), with
  ``source_dc_kind = 'user'`` and ``source_dc_id`` pointing at the cross-event
  ``saved_filter`` row in ``mira.db``. Each ``cut_member`` row gets a
  non-NULL ``event_id`` (the source event's UUID) — the resolver routes the
  export relpath to the right event's lineage on the way out.

For Item 4 this session works only over the ``#exported`` rung (members must
have a known export relpath). The other ladder rungs (``#collected`` /
``#picked`` / ``#edited``) need grab-originals to materialise bytes before
they can become Cut members — that lands in Item 6 (spec/61 §8 + §6).
Sessions that include un-exported items today drop those candidates with a
log warning; the spec/32 §1 acceptance queries (Nelson's "5-star macro of
insects" etc.) all run over ``#exported`` so the practical first-cut surface
is covered.

No Qt (charter invariant 8); the cross-event filter UI (Item 5) drives this.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from core import cut_budget
from mira.gateway.cross_event_resolver import pack_key, unpack_key
from mira.shared.cut_draft import PIN_KEEP_ALL, PIN_PICK_IN, PIN_WEED_OUT
from mira.user_store import models as um

log = logging.getLogger(__name__)


#: A DC expression: ordered ``(op, operand)`` pairs (spec/81 §2).
Expr = Tuple[Tuple[str, Any], ...]


@dataclass(frozen=True)
class CrossEventSessionFile:
    """One cell of the cross-event session — a candidate item resolved to
    the facts the surfaces need plus the source event identity.

    ``member_kind`` is the grab-originals discriminator (spec/81 Phase 2
    Item 6, spec/61 §6 + §8): ``'export'`` = the item has shipped, the
    cut_member rows link from its lineage in the source event; ``'grab'``
    = the item is still ``#collected`` / ``#picked`` / ``#edited`` (no
    lineage row yet), so the export pipeline copies the ORIGINAL bytes
    from the source event's ``Original Media/<origin_relpath>``.

    For 'export' members: ``export_relpath`` is the source event's lineage
    relpath, ``origin_relpath`` is empty/unused.
    For 'grab' members: ``origin_relpath`` is the source event's
    ``Original Media/<...>`` path, ``export_relpath`` is empty/unused.
    """

    event_uuid: str
    item_id: str
    export_relpath: str = ""
    origin_relpath: str = ""
    member_kind: str = "export"               # 'export' | 'grab'
    kind: str = "photo"                       # 'photo' | 'video'
    capture_time: Optional[str] = None
    duration_ms: int = 0
    day_bucket: Optional[str] = None          # ``"<event_uuid>::<ISO date>"``
                                              # — per-event day separators
                                              # (spec/81 §3.1)

    @property
    def key(self) -> str:
        return pack_key(self.event_uuid, self.item_id)


def session_files_from_global_items(
    rows: Sequence[um.GlobalItem],
    keys: Sequence[str],
    *,
    allow_grab: bool = True,
) -> List[CrossEventSessionFile]:
    """Build session cells from the LibraryGateway's resolved keys + the
    projection rows. Order matches ``keys`` (the resolver's chronological
    order).

    Rows with ``export_relpath`` become ``'export'`` members. Rows without
    ``export_relpath`` but WITH ``origin_relpath`` become ``'grab'`` members
    (spec/81 Phase 2 Item 6, spec/61 §6 + §8) — the export pipeline will
    pull the original bytes from the source event when ``allow_grab=True``
    (the default). Set ``allow_grab=False`` to skip grabs (pre-Item-6
    behaviour); rows with neither relpath are always dropped (orphan
    projection — log + skip)."""
    by_key = {pack_key(r.event_uuid, r.item_id): r for r in rows}
    files: List[CrossEventSessionFile] = []
    for k in keys:
        r = by_key.get(k)
        if r is None:
            continue                                          # not in projection
        day_iso: Optional[str] = (r.capture_time or "")[:10] or None
        common = dict(
            event_uuid=r.event_uuid,
            item_id=r.item_id,
            kind=(r.kind or "photo"),
            capture_time=r.capture_time,
            duration_ms=int(r.duration_ms or 0) if (r.kind == "video") else 0,
            day_bucket=(f"{r.event_uuid}::{day_iso}" if day_iso else None),
        )
        if r.export_relpath:
            files.append(CrossEventSessionFile(
                **common,
                export_relpath=r.export_relpath,
                member_kind="export",
            ))
            continue
        if allow_grab and r.origin_relpath:
            files.append(CrossEventSessionFile(
                **common,
                origin_relpath=r.origin_relpath,
                member_kind="grab",
            ))
            continue
        log.debug(
            "cross_event_cut_session: skipping %s — no export_relpath, "
            "no origin_relpath (or grabs disabled)", k)
    return files


@dataclass
class CrossEventCutSession:
    """The in-memory cross-event pin session: draft fields + ordered files +
    decisions. Same shape as :class:`mira.shared.cut_session.CutSession`,
    differs in candidate identity (composite keys) + commit target (an anchor
    event's ``event.db``).

    ``anchor_event_id`` is the event whose ``event.db`` will hold the cut +
    cut_member rows. The dialog chooses it (the event with the most resolved
    members is a reasonable default; the user can override). Cross-event Cuts
    set ``source_dc_kind = 'user'`` in event.db; ``source_dc_id`` is the
    ``saved_filter`` id."""

    name: str
    expr: Expr
    filters: Mapping[str, Any]
    pin_mode: str                              # keep-all | weed-out | pick-in
    target_s: Optional[int]
    max_s: Optional[int]
    photo_s: float
    music_category: Optional[str]
    files: Tuple[CrossEventSessionFile, ...]
    anchor_event_id: Optional[str] = None      # event.db that holds the Cut row
    source_dc_id: Optional[str] = None         # saved_filter id (cross-event)
    separators_on: bool = False                # cross-event default (spec/81 §3.1)
    overlay_fields: Tuple[str, ...] = ()
    overlay_mode: Optional[str] = None
    card_style: str = "black"
    cut_id: Optional[str] = None
    _picked: dict = field(default_factory=dict, repr=False)
    _undo: list = field(default_factory=list, repr=False)

    @property
    def keep_all(self) -> bool:
        return self.pin_mode == PIN_KEEP_ALL

    def __post_init__(self) -> None:
        if not self._picked:
            start = self.pin_mode in (PIN_KEEP_ALL, PIN_WEED_OUT)
            self._picked = {f.key: start for f in self.files}

    # ── the ledger ─────────────────────────────────────────────────────── #

    def is_picked(self, key: str) -> bool:
        return bool(self._picked.get(key, False))

    def set_state(self, key: str, picked: bool) -> None:
        """Pick (True) / Skip (False) one cross-event candidate by packed key."""
        if key not in self._picked:
            return
        prev = self._picked[key]
        if prev == picked:
            return
        self._undo.append((key, prev))
        self._picked[key] = picked

    def toggle(self, key: str) -> bool:
        new = not self.is_picked(key)
        self.set_state(key, new)
        return new

    def undo(self) -> Optional[str]:
        if not self._undo:
            return None
        key, prev = self._undo.pop()
        self._picked[key] = prev
        return key

    # ── reads the surfaces render from ─────────────────────────────────── #

    def picked_files(self) -> List[CrossEventSessionFile]:
        return [f for f in self.files if self.is_picked(f.key)]

    def picked_count(self) -> int:
        return sum(1 for f in self.files if self.is_picked(f.key))

    def picked_members(self) -> List[dict]:
        """The commit-ready member dicts :meth:`EventGateway.set_cut_members`
        expects (spec/81 Phase 2, schema v9). One per picked file, carrying
        the source ``event_id`` + the right relpath for the member's kind
        ('export' → ``export_relpath``; 'grab' → ``origin_relpath``)."""
        out: List[dict] = []
        for f in self.picked_files():
            if f.member_kind == "grab":
                out.append({
                    "event_id": f.event_uuid,
                    "kind": "grab",
                    "origin_relpath": f.origin_relpath,
                })
            else:
                out.append({
                    "event_id": f.event_uuid,
                    "kind": "export",
                    "export_relpath": f.export_relpath,
                })
        return out

    def days(self) -> List[Tuple[Optional[str], List[CrossEventSessionFile]]]:
        """Files grouped by ``day_bucket`` (per-event ISO date) in show
        order — the cross-event flat grid's day strips. Separators across the
        day boundary remain per-event (spec/81 §3.1: cross-event default OFF;
        when ON, one separator per ``(event, day)``)."""
        groups: List[Tuple[Optional[str], List[CrossEventSessionFile]]] = []
        for f in self.files:
            if groups and groups[-1][0] == f.day_bucket:
                groups[-1][1].append(f)
            else:
                groups.append((f.day_bucket, [f]))
        return groups

    def totals(self) -> cut_budget.ShowTotals:
        """Budget composition of the CURRENT picks. Day separators count per
        ``(event, day)`` — same calendar day in two events earns two cards
        (spec/81 §3.1)."""
        photos = videos = video_ms = 0
        day_buckets: set = set()
        for f in self.picked_files():
            if f.kind == "video":
                videos += 1
                video_ms += f.duration_ms
            else:
                photos += 1
            if f.day_bucket is not None:
                day_buckets.add(f.day_bucket)
        return cut_budget.ShowTotals(
            photo_count=photos,
            video_count=videos,
            separator_count=len(day_buckets) if self.separators_on else 0,
            video_ms_total=video_ms,
        )

    def show_seconds(self) -> float:
        return self.totals().seconds(self.photo_s)

    def zone(self) -> str:
        return cut_budget.zone(self.show_seconds(), self.target_s, self.max_s)

    # ── the one persistence moment ─────────────────────────────────────── #

    def commit(self, anchor_gateway) -> Any:
        """Create the cross-event Cut: writes the cut row + the replace-all
        membership in the anchor event's ``event.db``. ``anchor_gateway`` is
        an :class:`mira.gateway.event_gateway.EventGateway` open on the
        anchor event. The Cut's ``source_dc_kind`` is set to ``'user'`` and
        ``source_dc_id`` to the cross-event DC's ``saved_filter`` id; each
        ``cut_member`` row carries the source event's UUID so the export
        pipeline routes the relpath through the right ``event.db``'s lineage.

        Fresh session → ``create_cut`` (the gateway re-validates the name +
        cycle-safe namespace within the anchor event). Re-entered session →
        ``update_cut_settings`` + ``rename_cut`` + ``set_cut_members``.
        Returns the cut row from the anchor's gateway."""
        members = self.picked_members()
        expr_list = [list(t) for t in self.expr]
        if self.cut_id is None:
            cut = anchor_gateway.create_cut(
                self.name,
                source_dc_id=self.source_dc_id,
                source_dc_kind="user",
                expr_snapshot=expr_list,
                target_s=self.target_s, max_s=self.max_s,
                photo_s=self.photo_s,
                default_state=(
                    "picked" if self.pin_mode in (PIN_KEEP_ALL, PIN_WEED_OUT)
                    else "skipped"),
                music_category=self.music_category,
                separators=self.separators_on,
                overlay_fields=list(self.overlay_fields),
                overlay_mode=self.overlay_mode,
                card_style=self.card_style,
            )
        else:
            current = anchor_gateway.cut(self.cut_id)
            from core import cut_names as _names
            if current is not None and \
                    _names.slugify(self.name) != current.tag:
                anchor_gateway.rename_cut(self.cut_id, self.name)
            anchor_gateway.update_cut_settings(
                self.cut_id,
                source_dc_id=self.source_dc_id,
                source_dc_kind="user",
                expr_snapshot_json=json.dumps(expr_list),
                target_s=self.target_s, max_s=self.max_s,
                photo_s=self.photo_s,
                default_state=(
                    "picked" if self.pin_mode in (PIN_KEEP_ALL, PIN_WEED_OUT)
                    else "skipped"),
                music_category=self.music_category,
                separators=self.separators_on,
                overlay_fields_json=json.dumps(list(self.overlay_fields)),
                overlay_mode=self.overlay_mode,
                card_style=self.card_style,
            )
            cut = anchor_gateway.cut(self.cut_id)
        anchor_gateway.set_cut_members(cut.id, members)
        return cut

    # ── constructors ───────────────────────────────────────────────────── #

    @classmethod
    def from_draft(
        cls,
        library_gateway,
        draft,
        *,
        separators_on: Optional[bool] = None,
        anchor_event_id: Optional[str] = None,
    ) -> "CrossEventCutSession":
        """A fresh cross-event pin session from the New Cut dialog's draft.
        Resolves the draft's DC formula against ``global_items`` via
        ``library_gateway``, builds session cells. ``separators_on`` defaults
        to OFF for cross-event (spec/81 §3.1 — no single timeline to orient).
        """
        expr, filters = cls._draft_expr_filters(library_gateway, draft)
        keys = library_gateway.resolve_dc_keys(expr, filters)
        # Pull the matching projection rows so we have export_relpath etc.
        rows = _pull_rows_for_keys(library_gateway, keys)
        files = session_files_from_global_items(rows, keys)
        seps = (getattr(draft, "separators", False)
                if separators_on is None else separators_on)
        return cls(
            name=draft.name,
            expr=tuple(tuple(t) for t in expr),
            filters=dict(filters),
            pin_mode=getattr(draft, "pin_mode", PIN_WEED_OUT),
            source_dc_id=getattr(draft, "source_dc_id", None),
            target_s=draft.target_s, max_s=draft.max_s,
            photo_s=draft.photo_s,
            music_category=draft.music_category,
            files=tuple(files),
            anchor_event_id=anchor_event_id,
            separators_on=seps,
            overlay_fields=tuple(getattr(draft, "overlay_fields", ()) or ()),
            overlay_mode=getattr(draft, "overlay_mode", None),
            card_style=getattr(draft, "card_style", "black"),
        )

    @staticmethod
    def _draft_expr_filters(library_gateway, draft):
        """Resolve a draft to (expr, filters): inline overrides win; else the
        saved cross-event DC's stored formula."""
        expr = tuple(tuple(t) for t in (getattr(draft, "expr", ()) or ()))
        filters = dict(getattr(draft, "filters", {}) or {})
        dc_id = getattr(draft, "source_dc_id", None)
        if not expr and dc_id:
            dc = library_gateway.dynamic_collection(dc_id)
            if dc is not None:
                expr = tuple(tuple(t) for t in library_gateway.dc_expr(dc))
                if not filters:
                    filters = library_gateway.dc_filters(dc)
        return expr, filters


def _pull_rows_for_keys(library_gateway,
                        keys: Sequence[str]) -> List[um.GlobalItem]:
    """Fetch the ``GlobalItem`` rows backing a resolver's key set. One query
    via the packed-key match against ``event_uuid || '::' || item_id`` (the
    same shape the cross-event resolver uses to round-trip)."""
    if not keys:
        return []
    placeholders = ",".join(["?"] * len(keys))
    sql = (
        "SELECT * FROM global_items "
        f"WHERE (event_uuid || '::' || item_id) IN ({placeholders})"
    )
    return library_gateway.user_store.query_raw(um.GlobalItem, sql, tuple(keys))


def pick_anchor_event(files: Sequence[CrossEventSessionFile]) -> Optional[str]:
    """A reasonable default anchor: the event contributing the most files
    (ties broken by event_uuid ascending). Returns ``None`` for an empty
    session — the dialog displays an "anchor event" picker only when
    multiple events are in play; a single-event resolved set just uses
    that one event."""
    counts: dict = {}
    for f in files:
        counts[f.event_uuid] = counts.get(f.event_uuid, 0) + 1
    if not counts:
        return None
    best = max(counts.items(), key=lambda kv: (kv[1], -ord(kv[0][0]) if kv[0] else 0))
    # Above lambda's secondary tie-break is brittle; explicit alpha sort:
    top = max(counts.values())
    candidates = sorted([uuid for uuid, n in counts.items() if n == top])
    return candidates[0]


__all__ = [
    "CrossEventCutSession",
    "CrossEventSessionFile",
    "pick_anchor_event",
    "session_files_from_global_items",
]
