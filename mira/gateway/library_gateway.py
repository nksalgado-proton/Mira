"""``LibraryGateway`` — the cross-event facade (spec/81 Phase 2).

Sibling to :class:`mira.gateway.event_gateway.EventGateway`. EventGateway is
**per-event** — opens one ``event.db`` and answers questions about its items,
its DCs, its Cuts. LibraryGateway is **cross-event** — opens the user-level
``mira.db`` and answers the same shape of questions library-wide. The model is
identical at both scopes (spec/81 §2 is scope-agnostic); the gateways differ
only in *which operands they admit* and *which store they read*.

The asymmetry table from spec/81 §2.1:

============  =====================  ==============================================
              Event scope            Cross-event
============  =====================  ==============================================
Origin        ``#exported`` only      full ladder ``collected/picked/edited/exported``
Filters       Style + media type      full spec/32 §2 catalogue
DC home       ``event.db``            user-level ``mira.db`` (``saved_filter``)
============  =====================  ==============================================

What this gateway owns:

* **DC CRUD** against ``saved_filter`` — create / update / rename / delete /
  list / lookup by tag. Same vocabulary as :class:`EventGateway`'s DC surface
  (``create_dc``, ``dc_by_tag``, …) so the UI seam is uniform — the dialog
  doesn't care which scope it's on.
* **Resolution** — :meth:`resolve_dc` / :meth:`dc_probe` / :meth:`dc_show_totals`
  drive :func:`mira.gateway.cross_event_resolver.resolve_cross_event` and
  report back ``(event_uuid, item_id)`` keys + budget composition.
* **Operand inventory** — :meth:`dc_operand_inventory` lists the operands the
  cross-event New Cut dialog offers: the four ladder rungs as base tokens, plus
  every existing ``saved_filter`` (typed ``dc`` ref). Cross-event Cuts join the
  inventory when Item 4 lands.
* **Facet inventories** — :meth:`available_classifications` / ``cameras`` /
  ``lenses`` / ``country_codes`` — the dialog's filter-chip vocabulary, pulled
  from the cross-event projection (DISTINCT over ``global_items``).
* **Sync triggers** — :meth:`sync_event` (the per-event close hook) and
  :meth:`reconcile_all` (the startup pass) delegate to
  :mod:`mira.gateway.global_items_sync` so the projection stays in lockstep
  with the per-event stores.

No lifecycle ownership of ``mira.db`` itself: the gateway wraps an *already
open* :class:`UserStore` (same as :class:`EventGateway` over :class:`EventStore`).
Opening + closing belong to whoever wires it into the app.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from core import collection_resolver, cut_budget, cut_names, recipe_resolver
from mira.gateway import cross_event_resolver as cev
from mira.gateway import global_items_sync as gis
from mira.store.repo import EventStore
from mira.user_store import models as um
from mira.user_store.repo import UserStore

log = logging.getLogger(__name__)


#: Sentinel for "argument not passed" — distinguishes None (= NULL the
#: column) from omission (= leave column untouched). Module-private so
#: callers always go through the keyword API.
_UNSET = object()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_mtime_iso(path: Any) -> str:
    """Format a JSON file's filesystem mtime as an ISO-8601 UTC string.

    Used as a fallback for ``created_at`` / ``updated_at`` when the
    file's JSON payload doesn't carry them — a hand-authored file may
    not, but the SavedFilter dataclass demands them, so we synthesise
    a stable value from the filesystem.
    """
    if path is None:
        return _utc_now_iso()
    try:
        import os
        return datetime.fromtimestamp(
            os.path.getmtime(str(path)), tz=timezone.utc,
        ).isoformat()
    except (OSError, ValueError):
        return _utc_now_iso()


def _new_uuid() -> str:
    return uuid.uuid4().hex


class LibraryGateway:
    """The cross-event query / mutator facade. Wraps an open
    :class:`UserStore` and is the **only** place cross-event surfaces touch
    ``mira.db``. UI surfaces hold one per session (cross-event work spans the
    whole app's lifetime, not a single event)."""

    def __init__(
        self,
        user_store: UserStore,
        *,
        now: Callable[[], str] = _utc_now_iso,
        new_id: Callable[[], str] = _new_uuid,
        collections_library: Any = None,
    ) -> None:
        self.user_store = user_store
        self._now = now
        self._new_id = new_id
        # spec/94 Phase 1b — when wired, every DC read/write goes
        # through the JSON tree (the single live source). When
        # ``None`` (e.g. a unit test constructing LibraryGateway
        # directly), the legacy ``saved_filter`` SQL path stays in
        # place so the gateway's unit tests keep exercising the same
        # surface they always did. The live app wires it via
        # :meth:`mira.gateway.gateway.Gateway.library_gateway`.
        self._collections_library = collections_library

    def __enter__(self) -> "LibraryGateway":
        return self

    def __exit__(self, *exc) -> None:                         # noqa: D401
        """Do NOT close the user_store — lifecycle is owned by the wirer."""
        pass

    # =================================================================== #
    # Read-only defensive net (spec/76 §B.1)
    # =================================================================== #

    def _guard_read_only(self) -> None:
        """Raise :class:`ReadOnlyLibraryError` when the session is open
        read-only. Mirrors :meth:`EventGateway._touch` — every mutator
        below calls this BEFORE opening a transaction so the read-only
        block is the same shape as the per-event guard. The UI surface
        is still expected to disable controls upfront
        (:func:`mira.session.is_read_only`); this catch is the
        defensive net for paths that slipped through."""
        from mira.session import ReadOnlyLibraryError, is_read_only
        if is_read_only():
            raise ReadOnlyLibraryError(
                "Library is open read-only — cross-event mutation "
                "refused. The writer lock is held by another machine.")

    @staticmethod
    def _skip_if_read_only() -> bool:
        """``True`` when a maintenance write (projection sync /
        reconcile / event-drop) should be SKIPPED rather than raise.
        These paths are not user mutations — they run on startup +
        event-close and the writer-half machine takes care of them.
        Returning early in read-only mode keeps us from writing to
        ``global_items`` from a session that doesn't own the lock."""
        from mira.session import is_read_only
        return is_read_only()

    # =================================================================== #
    # Dynamic Collections (cross-event — ``saved_filter`` rows)
    # =================================================================== #

    def dynamic_collections(self) -> List[um.SavedFilter]:
        """All cross-event DCs, oldest first. Mirrors
        :meth:`EventGateway.dynamic_collections`; readers don't care
        about the underlying storage shape.

        Phase 1b (spec/94): when the file-based library is wired
        (:attr:`_collections_library`), this enumerates the JSON tree
        and projects each :class:`DefinitionFile` to a
        :class:`SavedFilter` dataclass so callers see the same row
        shape. Falls back to the legacy SQL path when the library is
        absent (unit tests construct LibraryGateway directly)."""
        if self._collections_library is not None:
            rows = [
                self._df_to_saved_filter(df)
                for df in self._collections_library.all_definitions()
            ]
            rows.sort(key=lambda sf: (sf.created_at or "", sf.id))
            return rows
        return self.user_store.query_raw(
            um.SavedFilter,
            "SELECT * FROM saved_filter ORDER BY created_at, id")

    def dynamic_collection(self, dc_id: str) -> Optional[um.SavedFilter]:
        if self._collections_library is not None:
            df = self._collections_library.by_id(dc_id)
            return self._df_to_saved_filter(df) if df is not None else None
        return self.user_store.get(um.SavedFilter, dc_id)

    def dc_by_tag(self, tag: str) -> Optional[um.SavedFilter]:
        if self._collections_library is not None:
            df = self._collections_library.by_name(tag)
            return self._df_to_saved_filter(df) if df is not None else None
        rows = self.user_store.query_by(um.SavedFilter, tag=tag)
        return rows[0] if rows else None

    # ── DefinitionFile ↔ SavedFilter projection (Phase 1b) ────────

    @staticmethod
    def _df_to_saved_filter(df: Any) -> um.SavedFilter:
        """Project a :class:`DefinitionFile` (JSON-backed) onto the
        :class:`SavedFilter` dataclass callers expect.

        The JSON payload carries ``expr`` + ``filters`` + an optional
        ``description``. ``created_at`` / ``updated_at`` are stored in
        the JSON top-level when present (the migration writes them) and
        fall back to the file's filesystem mtime when not (a hand-
        authored file)."""
        payload = df.payload or {}
        expr = payload.get("expr") or []
        filters = payload.get("filters") or {}
        description = payload.get("description")
        created_at = payload.get("created_at") or _file_mtime_iso(df.path)
        updated_at = payload.get("updated_at") or created_at
        return um.SavedFilter(
            id=df.id,
            tag=df.name,
            description=description,
            created_at=created_at,
            updated_at=updated_at,
            expr_json=json.dumps(list(expr)),
            filters_json=json.dumps(dict(filters)),
        )

    @staticmethod
    def dc_expr(dc: um.SavedFilter) -> List[list]:
        """A DC's formula as ``[[op, operand], …]`` — typed-ref shape, same
        as event-scope (spec/81 §2)."""
        try:
            return list(json.loads(dc.expr_json or "[]"))
        except (ValueError, TypeError):
            return []

    @staticmethod
    def dc_filters(dc: um.SavedFilter) -> dict:
        """A DC's filter mapping (the spec/32 §2 catalogue). Tolerant readers
        — missing keys / malformed JSON fall back to ``{}``."""
        try:
            data = json.loads(dc.filters_json or "{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _check_dc_cycle(self, dc_id: str,
                        expr: Sequence[Sequence]) -> None:
        """Cheap, non-resolving cycle guard at the write seam (spec/81 §2).
        Walks DC→DC operand refs only; base tokens + cut refs are terminal.
        Raises ``ValueError("cycle")`` so the UI can ``tr()``-map the code."""
        by_id: Dict[str, list] = {}
        for d in self.dynamic_collections():
            if d.id == dc_id:
                continue
            by_id[d.id] = self.dc_expr(d)
        if collection_resolver.reaches(
                dc_id, [list(t) for t in expr],
                dc_expr_by_id=lambda i: by_id.get(i)):
            raise ValueError("cycle")

    def create_dc(
        self,
        name: str,
        *,
        expr: Sequence[Sequence] = (),
        filters: Optional[Mapping[str, Any]] = None,
        description: Optional[str] = None,
    ) -> um.SavedFilter:
        """Create a cross-event DC from a user-typed name (slugified +
        validated against the cross-event DC namespace — reserved against the
        ladder rungs + taken tags). Rejects self-referential operand graphs
        (cycle guard). ``filters`` is the spec/32 §2 catalogue as a dict —
        tolerant readers; unknown keys round-trip via ``filters_json``."""
        self._guard_read_only()
        slug = cut_names.slugify(name)
        err = cut_names.check_tag(slug, [d.tag for d in self.dynamic_collections()])
        if err:
            raise ValueError(err)
        dc_id = self._new_id()
        expr_list = [list(t) for t in expr]
        self._check_dc_cycle(dc_id, expr_list)
        now = self._now()
        if self._collections_library is not None:
            # spec/94 Phase 1b — single live source. Write the JSON file
            # and project back to SavedFilter for the caller.
            from core.definition_files import (
                DefinitionFile,
                KIND_COLLECTION,
            )
            df = DefinitionFile(
                id=dc_id,
                name=slug,
                kind=KIND_COLLECTION,
                payload={
                    "expr": expr_list,
                    "filters": dict(filters or {}),
                    "created_at": now,
                    "updated_at": now,
                    **({"description": description} if description else {}),
                },
            )
            self._collections_library.save(df)
            return self._df_to_saved_filter(df)
        sf = um.SavedFilter(
            id=dc_id, tag=slug,
            description=description,
            created_at=now, updated_at=now,
            expr_json=json.dumps(expr_list),
            filters_json=json.dumps(dict(filters or {})),
        )
        with self.user_store.transaction():
            self.user_store.upsert(sf)
        return sf

    def update_dc(
        self,
        dc_id: str,
        *,
        expr: Optional[Sequence[Sequence]] = None,
        filters: Optional[Mapping[str, Any]] = None,
        description: Optional[str] = None,
    ) -> None:
        """Edit a cross-event DC's formula / filters / description in place.
        The cycle guard runs against the NEW expr. ``filters`` REPLACES the
        whole mapping (event-scope's per-key merge is too narrow for the
        cross-event catalogue's open-ended key set; callers pass the full
        next state)."""
        self._guard_read_only()
        dc = self.dynamic_collection(dc_id)
        if dc is None:
            raise KeyError(dc_id)
        if expr is not None:
            self._check_dc_cycle(dc_id, [list(t) for t in expr])

        if self._collections_library is not None:
            df = self._collections_library.by_id(dc_id)
            if df is None:
                raise KeyError(dc_id)
            payload = dict(df.payload or {})
            if expr is not None:
                payload["expr"] = [list(t) for t in expr]
            if filters is not None:
                payload["filters"] = dict(filters)
            if description is not None:
                payload["description"] = description
            payload["updated_at"] = self._now()
            df.payload = payload
            from core.definition_files import write_definition
            write_definition(df)
            self._collections_library.refresh()
            return

        sets: Dict[str, str] = {}
        if expr is not None:
            expr_list = [list(t) for t in expr]
            sets["expr_json"] = json.dumps(expr_list)
        if filters is not None:
            sets["filters_json"] = json.dumps(dict(filters))
        if description is not None:
            sets["description"] = description
        if not sets:
            return
        cols = ", ".join(f"{k} = ?" for k in sets)
        with self.user_store.transaction() as conn:
            conn.execute(
                f"UPDATE saved_filter SET {cols}, updated_at = ? WHERE id = ?",
                (*sets.values(), self._now(), dc_id))

    def rename_dc(self, dc_id: str, new_name: str) -> um.SavedFilter:
        """Rename a cross-event DC (slug + validate against the cross-event
        namespace, excluding itself). The cycle guard does not need to
        re-run — renaming changes only the tag, not the operand graph."""
        self._guard_read_only()
        dc = self.dynamic_collection(dc_id)
        if dc is None:
            raise KeyError(dc_id)
        slug = cut_names.slugify(new_name)
        err = cut_names.check_tag(
            slug, [d.tag for d in self.dynamic_collections() if d.id != dc_id])
        if err:
            raise ValueError(err)
        if self._collections_library is not None:
            df = self._collections_library.rename(dc_id, slug)
            # The rename helper rewrites the in-file ``name`` hint;
            # also bump ``updated_at`` to match the SQL behavior.
            payload = dict(df.payload or {})
            payload["updated_at"] = self._now()
            df.payload = payload
            from core.definition_files import write_definition
            write_definition(df)
            self._collections_library.refresh()
            return self._df_to_saved_filter(df)
        with self.user_store.transaction() as conn:
            conn.execute(
                "UPDATE saved_filter SET tag = ?, updated_at = ? WHERE id = ?",
                (slug, self._now(), dc_id))
        return replace(dc, tag=slug)

    def delete_dc(self, dc_id: str) -> None:
        """Drop a cross-event DC. Cross-event Cuts that point at it (Item 4+)
        survive via the same opaque-id discipline event-scope already uses
        (the FK is dropped per the Phase-2 handover recommendation; freeze
        invariant — spec/81 §5)."""
        self._guard_read_only()
        if self._collections_library is not None:
            self._collections_library.delete(dc_id)
            return
        with self.user_store.transaction() as conn:
            conn.execute("DELETE FROM saved_filter WHERE id = ?", (dc_id,))

    # =================================================================== #
    # Resolution + probes
    # =================================================================== #

    def resolve_dc(
        self,
        expr: Sequence[Sequence],
        filters: Optional[Mapping] = None,
        *,
        scope: Optional[Iterable[str]] = None,
    ) -> List[Tuple[str, str]]:
        """Resolve a cross-event DC formula (spec/81 §2). Returns
        ``(event_uuid, item_id)`` tuples in chronological show order. The
        formula's own filters apply at the top; nested DCs' filters apply
        before they compose upward (the resolver handles both).

        spec/94 Phase 4a — ``scope`` is an optional pre-resolved set of
        event uuids to narrow the result to (the cross-event "power face"
        Scope sentence resolves to this via :meth:`resolve_scope` before
        the call). ``None`` means library-wide (the historical default).
        Empty iterable means "narrow to nothing" → empty result."""
        keys = self.resolve_dc_keys(expr, filters, scope=scope)
        return [cev.unpack_key(k) for k in keys]

    def resolve_dc_keys(
        self,
        expr: Sequence[Sequence],
        filters: Optional[Mapping] = None,
        *,
        scope: Optional[Iterable[str]] = None,
    ) -> List[str]:
        """The packed-key variant of :meth:`resolve_dc` — keep when the
        caller wants the resolver's native ``"event_uuid::item_id"`` strings
        (e.g. composing further set operations without re-packing). The flat
        grid + export pipelines use :meth:`resolve_dc` directly.

        spec/94 Phase 4a — ``scope`` narrows the resolved keys to the
        passed-in event uuids. Mirrors the contract of
        :meth:`resolve_recipe`'s ``scope`` parameter so the cross-event
        session path threads a single shape end-to-end."""
        keys = cev.resolve_cross_event(self.user_store, expr, filters)
        if scope is None:
            return keys
        scope_set = frozenset(scope)
        return [k for k in keys if cev.unpack_key(k)[0] in scope_set]

    def dc_probe(self, expr: Sequence[Sequence],
                 filters: Optional[Mapping] = None,
                 *,
                 scope: Optional[Iterable[str]] = None) -> int:
        """The dialog's live count for a draft DC formula (spec/81 §2).
        spec/94 Phase 4a — honours ``scope`` the same way as
        :meth:`resolve_dc_keys`."""
        return len(self.resolve_dc_keys(expr, filters, scope=scope))

    # ----- Scope resolution (spec/90 §3 / spec/94 Phase 4a) --------------- #

    def resolve_scope(
        self,
        scope_expr: Sequence[Sequence],
    ) -> Optional[FrozenSet[str]]:
        """Resolve a Scope expression (spec/90 §3.1, §3.2) — the
        chip-and-join-word sentence the Collection face composes — to the
        FROZEN SET of event uuids it covers.

        Returns ``None`` for an empty expression — the "library-wide"
        sentinel that :meth:`resolve_dc_keys` / :meth:`resolve_recipe`
        treat as "no narrowing" (don't filter). A non-empty expression
        that resolves to zero events returns ``frozenset()`` — the
        caller should narrow to nothing, not fall back to library-wide.

        Operand kinds accepted (spec/90 §3.1):

        * ``"event"`` (``{"uuid": …}``) — that one event.
        * ``"event_collection"`` (``{"id": …, "tag": …}``) — resolves the
          collection's saved ``expr_json`` via recursion (events expand,
          nested Event Collections expand). Missing → empty set
          (graceful shrink, same as DC operands).
        * ``"date_range"`` (``{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}``)
          — events whose cached ``[start_date_cached, end_date_cached]``
          overlaps the range. Half-open ends tolerated (only start, only
          end, or neither).

        Joins are evaluated left-to-right (spec/81 §2): ``+`` union,
        ``∩`` intersection, ``−`` set difference. The first chip's join
        is always treated as the seed (the empty-accumulator union
        case)."""
        if not scope_expr:
            return None
        accumulator: Optional[Set[str]] = None
        for pair in scope_expr:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            op, operand = pair[0], pair[1]
            if not isinstance(op, str):
                continue
            members = self._scope_operand_events(operand)
            if accumulator is None:
                accumulator = set(members)
            elif op == "+":
                accumulator |= members
            elif op in ("∩", "and"):
                accumulator &= members
            elif op in ("−", "-", "not"):
                accumulator -= members
            else:
                accumulator |= members
        return frozenset(accumulator or set())

    def _scope_operand_events(self, operand: Any) -> Set[str]:
        """Map one Scope operand to its set of event uuids. Tolerant of
        malformed shapes — anything we can't read becomes ``set()`` so
        the resolver shrinks gracefully (same rule the DC resolver
        applies to a deleted operand)."""
        if not isinstance(operand, Mapping):
            return set()
        kind = operand.get("kind")
        if kind == "event":
            uuid = operand.get("uuid") or operand.get("id")
            return {uuid} if uuid else set()
        if kind == "event_collection":
            ec = self._event_collection_by_ref(operand)
            if ec is None:
                return set()
            try:
                nested = json.loads(ec.expr_json or "[]")
            except (ValueError, TypeError):
                nested = []
            inner = self.resolve_scope(nested)
            return set(inner) if inner else set()
        if kind == "date_range":
            return self._events_in_date_range(
                operand.get("start"), operand.get("end"))
        return set()

    def _events_in_date_range(
        self,
        start_iso: Any,
        end_iso: Any,
    ) -> Set[str]:
        """Events whose cached date range overlaps ``[start_iso, end_iso]``.
        Either bound may be missing (open-ended on that side); undated
        events have NULL ``start_date_cached`` / ``end_date_cached`` and
        correctly stay out of the result (NULL comparisons are false)."""
        start = start_iso if isinstance(start_iso, str) and start_iso else None
        end = end_iso if isinstance(end_iso, str) and end_iso else None
        clauses: List[str] = []
        params: list = []
        if start is not None:
            clauses.append("(end_date_cached >= ? OR start_date_cached >= ?)")
            params.extend([start, start])
        if end is not None:
            clauses.append("(start_date_cached <= ? OR end_date_cached <= ?)")
            params.extend([end, end])
        sql = "SELECT event_uuid FROM event_index"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self.user_store.conn.execute(sql, params).fetchall()
        return {r["event_uuid"] for r in rows}

    # ----- Recipe resolution (spec/90 §7 Phase 2) ------------------------- #

    def _event_collection_by_ref(
        self, operand: Mapping[str, Any],
    ) -> Optional[um.EventCollection]:
        """Resolve a ``{"kind":"event_collection","id"|"tag":…}`` operand to
        the live row, or ``None`` when it's gone. Same lookup pattern as
        :meth:`dc_by_tag` so the strict-ref check can compose naturally."""
        ec_id = operand.get("id")
        if ec_id:
            ec = self.user_store.get(um.EventCollection, ec_id)
            if ec is not None:
                return ec
        tag = operand.get("tag")
        if tag:
            rows = self.user_store.query_by(um.EventCollection, tag=tag)
            return rows[0] if rows else None
        return None

    def _event_exists(self, uuid: str) -> bool:
        """An event uuid is "known" iff it has an ``event_index`` row. The
        cross-event surface uses this index (spec/53 §2.3) as the authoritative
        list of events the library knows about."""
        row = self.user_store.conn.execute(
            "SELECT 1 FROM event_index WHERE event_uuid = ? LIMIT 1",
            (uuid,),
        ).fetchone()
        return row is not None

    def _person_exists(self, person_id: str) -> bool:
        """A Person id is "known" iff it has a row in the user-level catalog
        (``person`` table, spec/53 §2.5). The face detections that resolve to
        items live per-event; this is only the existence gate (spec/90 §1.4
        strict-reference rule)."""
        row = self.user_store.conn.execute(
            "SELECT 1 FROM person WHERE id = ? LIMIT 1", (person_id,),
        ).fetchone()
        return row is not None

    def _check_recipe_operand(self, operand: Mapping[str, Any]) -> None:
        """Strict-reference guard for one named operand in a cross-event
        Recipe expression (spec/90 §1.4). Cross-event admits a wider operand
        alphabet than event-scope: ``dc`` (saved_filter) / ``cut`` (cross-event
        Cut, deferred to spec/81 Phase 2 Item 4) / ``event_collection`` (the
        Scope alphabet, spec/90 §5.3) / ``event`` (Scope) / ``person``
        (Filters + advanced rule predicates)."""
        kind = operand.get("kind")
        if kind == "dc":
            sf = None
            if operand.get("id"):
                sf = self.user_store.get(um.SavedFilter, operand["id"])
            if sf is None and operand.get("tag"):
                sf = self.dc_by_tag(operand["tag"])
            if sf is None:
                raise recipe_resolver.RecipeResolutionError(
                    operand.get("tag") or operand.get("id") or "",
                    kind="dc",
                )
        elif kind == "cut":
            # Cross-event Cuts are deferred (spec/81 Phase 2 Item 4); any
            # reference is "missing" until that surface lands. The strict
            # rule (spec/90 §1.4) raises here so a Recipe naming a
            # cross-event Cut surfaces the problem; the dialog UI in Phase 4
            # would gate the operand behind feature availability anyway.
            raise recipe_resolver.RecipeResolutionError(
                operand.get("tag") or operand.get("id") or "",
                kind="cut",
            )
        elif kind == recipe_resolver.EVENT_COLLECTION_KIND:
            ec = self._event_collection_by_ref(operand)
            if ec is None:
                raise recipe_resolver.RecipeResolutionError(
                    operand.get("tag") or operand.get("id") or "",
                    kind=recipe_resolver.EVENT_COLLECTION_KIND,
                )
        elif kind == recipe_resolver.EVENT_KIND:
            uuid = operand.get("uuid") or operand.get("id") or ""
            if not uuid or not self._event_exists(uuid):
                raise recipe_resolver.RecipeResolutionError(
                    uuid, kind=recipe_resolver.EVENT_KIND,
                )
        elif kind == recipe_resolver.PERSON_KIND:
            pid = operand.get("id") or ""
            if not pid or not self._person_exists(pid):
                raise recipe_resolver.RecipeResolutionError(
                    pid, kind=recipe_resolver.PERSON_KIND,
                )

    def _recipe_dc_expr_by_ref(
        self, operand: Mapping[str, Any],
    ) -> Optional[list]:
        """Live DC expression lookup for the strict walk's transitive
        recursion."""
        sf = None
        if operand.get("id"):
            sf = self.user_store.get(um.SavedFilter, operand["id"])
        if sf is None and operand.get("tag"):
            sf = self.dc_by_tag(operand["tag"])
        if sf is None:
            return None
        return self.dc_expr(sf)

    def _person_member_keys(self, person_id: str) -> Optional[Set[str]]:
        """Resolve a Person id to the set of cross-event packed keys where
        they appear (spec/90 §4.3). The face data is per-event; cross-event
        resolution would have to fan out across every event.db. For Phase 2
        we keep it simple: the existence gate runs against the user-level
        catalog (:meth:`_person_exists`), and detected-set resolution is
        deferred — the catalog-known Person resolves to the EMPTY set
        across events until the cross-event face sync ships.

        Returns ``None`` when the Person is unknown (strict-ref miss);
        empty set when known but no detections fanned out yet (lenient —
        the §4.3 face-substrate-empty rule)."""
        return set() if self._person_exists(person_id) else None

    def _operand_person_for_predicate(
        self, operand: Mapping[str, Any],
    ) -> Optional[Set[str]]:
        """``extra_operand`` adapter for Person chips inside rule predicates."""
        if operand.get("kind") != recipe_resolver.PERSON_KIND:
            return None
        members = self._person_member_keys(operand.get("id") or "")
        return set(members) if members is not None else set()

    def resolve_recipe(
        self,
        composition: Mapping[str, Any],
        *,
        scope: Optional[Sequence[str]] = None,
    ) -> recipe_resolver.RecipeResolution:
        """Evaluate a cross-event Recipe ``composition`` (spec/90 §7 Phase 2).

        Returns the ordered pool of cross-event packed keys
        (``"<event_uuid>::<item_id>"``) plus a per-key ``initially_picked``
        seed map.

        ``scope`` is the pre-resolved set of event uuids the Recipe reaches.
        For Phase 2 the parameter is accepted for API parity; the resolver
        narrows the source pool to those uuids when provided. ``None`` means
        "library-wide" — every event in scope (the default for a Collection
        Recipe with no explicit Scope sentence).

        Raises :class:`recipe_resolver.RecipeResolutionError` if any named
        operand (DC / Event Collection / Event / Person — cross-event Cuts
        are deferred) is missing. Vocabulary filters (Style / Media /
        Camera / Lens / EXIF / location) resolve leniently to empty pools."""
        scope_uuids: Optional[FrozenSet[str]] = (
            frozenset(scope) if scope is not None else None
        )

        def _resolve_pool(expr, filters):
            keys = cev.resolve_cross_event(self.user_store, expr, filters)
            if scope_uuids is None:
                return list(keys)
            return [k for k in keys if cev.unpack_key(k)[0] in scope_uuids]

        def _resolve_predicate_keys(predicate_expr):
            acc = cev.CrossEventAccessors(self.user_store)
            keys = collection_resolver.resolve(
                [list(t) for t in predicate_expr],
                {},
                base_universe=acc.base_universe,
                dc_by_ref=acc.dc_by_ref,
                cut_members=acc.cut_members,
                apply_filters=lambda ks, _f: list(ks),
                extra_operand=self._operand_person_for_predicate,
            )
            if scope_uuids is None:
                return set(keys)
            return {k for k in keys if cev.unpack_key(k)[0] in scope_uuids}

        return recipe_resolver.resolve_recipe(
            composition,
            resolve_pool=_resolve_pool,
            resolve_predicate_keys=_resolve_predicate_keys,
            person_members=self._person_member_keys,
            validate_named_operand=self._check_recipe_operand,
            dc_expr_by_ref=self._recipe_dc_expr_by_ref,
        )

    def dc_show_totals(
        self,
        expr: Sequence[Sequence],
        filters: Optional[Mapping] = None,
    ) -> cut_budget.ShowTotals:
        """Budget composition of a cross-event DRAFT DC formula (spec/81 §2 +
        spec/32 §3). Mirrors :meth:`EventGateway.dc_show_totals` but the
        ``separator_count`` semantics are scope-aware: cross-event Cuts
        default separators OFF (spec/81 §3.1), so the field reads the day
        count the formula resolved to and the dialog zeroes it when the
        per-Cut separators setting is off (same shape as event scope)."""
        keys = cev.resolve_cross_event(self.user_store, expr, filters)
        if not keys:
            return cut_budget.ShowTotals()
        # Resolve cross-event keys back to rows; one query joins on the
        # composite key. ``capture_time`` (ISO 'YYYY-MM-DDT…') drives the
        # day bucket — same surface as event-scope's ``trip_day`` join, just
        # day-derived from time instead of stored.
        placeholders = ",".join(["?"] * len(keys))
        sql = (
            "SELECT kind, duration_ms, capture_time, event_uuid "
            "FROM global_items "
            f"WHERE (event_uuid || '::' || item_id) IN ({placeholders})"
        )
        rows = self.user_store.conn.execute(sql, keys).fetchall()
        photos = 0
        videos = 0
        video_ms = 0
        days: set = set()
        for r in rows:
            kind = r["kind"] or "photo"
            if kind == "video":
                videos += 1
                video_ms += int(r["duration_ms"] or 0)
            else:
                photos += 1
            # Day bucket = (event_uuid, ISO date). Same-day across two events
            # counts as two separators — separators orient ONE event's
            # timeline (spec/81 §3.1), so the cross-event day count is
            # per-event by construction. Zero-time rows contribute no day.
            t = r["capture_time"]
            if t:
                days.add((r["event_uuid"], t[:10]))
        return cut_budget.ShowTotals(
            photo_count=photos,
            video_count=videos,
            separator_count=len(days),
            video_ms_total=video_ms,
        )

    # =================================================================== #
    # Operand + facet inventories (the dialog's vocabularies)
    # =================================================================== #

    def dc_operand_inventory(self) -> List[dict]:
        """The operands the cross-event New Cut dialog offers (spec/81 §2 +
        §2.1): the four ladder rungs as base tokens, then every existing
        cross-event DC as a typed ``dc`` ref. Cross-event Cuts (Item 4) will
        join here when their storage lands. Each entry mirrors
        :meth:`EventGateway.dc_operand_inventory`'s shape so the dialog
        consumes one API."""
        inv: List[dict] = []
        for token in (collection_resolver.BASE_COLLECTED,
                      collection_resolver.BASE_PICKED,
                      collection_resolver.BASE_EDITED,
                      collection_resolver.BASE_EXPORTED):
            inv.append({"kind": "base", "tag": token, "operand": token})
        for d in self.dynamic_collections():
            inv.append({"kind": "dc", "tag": d.tag,
                        "operand": {"kind": "dc", "id": d.id, "tag": d.tag}})
        return inv

    def list_events_for_scope(self) -> List[dict]:
        """Inventory of events for the Recipe dialog's Scope picker
        (spec/90 §1.1, §3.1). Returns one dict per known event with:

        * ``uuid`` — the stable event id used in the spec/90 operand
          encoding ``{"kind": "event", "uuid": …}``.
        * ``name`` — the cached event name (or ``"(unnamed)"`` fallback).
        * ``item_count`` — count of ``global_items`` rows in this event;
          the dialog renders this as the live count beside each chip.

        Ordered newest-first when start_date is available — the dialog's
        Scope picker reads "what did I shoot recently" most often, so the
        common case lands at the top. Events without a start_date trail.
        spec/53 §2.3 — ``event_index`` is the authoritative list of
        library-known events; counts ride the ``global_items`` projection
        the cross-event resolver already syncs to."""
        rows = self.user_store.conn.execute(
            "SELECT ei.event_uuid AS uuid, "
            "       ei.name_cached AS name, "
            "       ei.start_date_cached AS start_date, "
            "       COUNT(gi.item_id) AS item_count "
            "FROM event_index ei "
            "LEFT JOIN global_items gi ON gi.event_uuid = ei.event_uuid "
            "GROUP BY ei.event_uuid, ei.name_cached, ei.start_date_cached "
            "ORDER BY "
            "  CASE WHEN ei.start_date_cached IS NULL THEN 1 ELSE 0 END, "
            "  ei.start_date_cached DESC, ei.name_cached"
        ).fetchall()
        return [
            {
                "uuid": r["uuid"],
                "name": r["name"] or "(unnamed)",
                "item_count": int(r["item_count"] or 0),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Per-facet inventories (spec/83 §5)
    #
    # Each returns ``[(value, photo_count), …]`` ordered **most-used-first**
    # so the picker's main-list / occasional split (spec/83 §4) reads off the
    # tail directly, and the inline editor (spec/83 §3) puts the heavy
    # hitters first. NULLs are still excluded; the count is the row count
    # per distinct value in ``global_items``.
    #
    # ``camera_id`` is the per-event Make+Model business key (``"Pana+G9M2"``
    # etc.) — semi-readable; the picker shows it verbatim. If the user wants
    # a prettier label, the gear-wizard ([[gear-profile-wizard]]) is where
    # display names would attach. ``country_code`` is the ISO 3166-1 alpha-2
    # short string; the picker can map to a country name later (spec/83 open
    # question).
    # ------------------------------------------------------------------ #

    def available_classifications(self) -> List[Tuple[str, int]]:
        """``(classification, photo_count)`` across the projection,
        most-used-first. The cross-event dialog's Style vocabulary."""
        rows = self.user_store.conn.execute(
            "SELECT classification, COUNT(*) AS n FROM global_items "
            "WHERE classification IS NOT NULL "
            "GROUP BY classification ORDER BY n DESC, classification"
        ).fetchall()
        return [(r["classification"], int(r["n"])) for r in rows]

    def available_cameras(self) -> List[Tuple[str, int]]:
        """``(camera_id, photo_count)`` across the projection, most-used-first.
        Camera filter + gear-wizard vocabulary."""
        rows = self.user_store.conn.execute(
            "SELECT camera_id, COUNT(*) AS n FROM global_items "
            "WHERE camera_id IS NOT NULL "
            "GROUP BY camera_id ORDER BY n DESC, camera_id"
        ).fetchall()
        return [(r["camera_id"], int(r["n"])) for r in rows]

    def available_lenses(self) -> List[Tuple[str, int]]:
        """``(lens_model, photo_count)`` across the projection, most-used-first.
        Lens filter + gear-wizard vocabulary."""
        rows = self.user_store.conn.execute(
            "SELECT lens_model, COUNT(*) AS n FROM global_items "
            "WHERE lens_model IS NOT NULL "
            "GROUP BY lens_model ORDER BY n DESC, lens_model"
        ).fetchall()
        return [(r["lens_model"], int(r["n"])) for r in rows]

    def available_country_codes(self) -> List[Tuple[str, int]]:
        """``(country_code, photo_count)`` across the projection,
        most-used-first. ISO 3166-1 alpha-2 codes."""
        rows = self.user_store.conn.execute(
            "SELECT country_code, COUNT(*) AS n FROM global_items "
            "WHERE country_code IS NOT NULL "
            "GROUP BY country_code ORDER BY n DESC, country_code"
        ).fetchall()
        return [(r["country_code"], int(r["n"])) for r in rows]

    def available_cities(self) -> List[Tuple[str, int]]:
        """``(day_city, photo_count)`` across the projection, most-used-first."""
        rows = self.user_store.conn.execute(
            "SELECT day_city, COUNT(*) AS n FROM global_items "
            "WHERE day_city IS NOT NULL "
            "GROUP BY day_city ORDER BY n DESC, day_city"
        ).fetchall()
        return [(r["day_city"], int(r["n"])) for r in rows]

    def available_color_labels(self) -> List[Tuple[str, int]]:
        """``(color_label, photo_count)`` across the projection,
        most-used-first. LRC-compatible vocabulary."""
        rows = self.user_store.conn.execute(
            "SELECT color_label, COUNT(*) AS n FROM global_items "
            "WHERE color_label IS NOT NULL "
            "GROUP BY color_label ORDER BY n DESC, color_label"
        ).fetchall()
        return [(r["color_label"], int(r["n"])) for r in rows]

    # ------------------------------------------------------------------ #
    # Event-level inventories (spec/86 §3) — the qualifiers slice 1 pushed
    # into ``global_items``. Counts are over ITEM rows (not distinct events)
    # so heavy-event types lead the list the same way heavy-use cameras do.
    # Participants is JSON; ``json_each`` expands the array per row.
    # ------------------------------------------------------------------ #

    def available_event_types(self) -> List[Tuple[str, int]]:
        """``(event_type, photo_count)`` across the projection,
        most-used-first. Closed enum trip / session / occasion / project /
        unclassified (spec/52)."""
        rows = self.user_store.conn.execute(
            "SELECT event_type, COUNT(*) AS n FROM global_items "
            "WHERE event_type IS NOT NULL "
            "GROUP BY event_type ORDER BY n DESC, event_type"
        ).fetchall()
        return [(r["event_type"], int(r["n"])) for r in rows]

    def available_event_subtypes(self) -> List[Tuple[str, int]]:
        """``(event_subtype, photo_count)`` across the projection,
        most-used-first. Free-text — the spec/52 curated presets are a UI
        nudge, not a SQL constraint, so the inventory shows whatever
        actually landed."""
        rows = self.user_store.conn.execute(
            "SELECT event_subtype, COUNT(*) AS n FROM global_items "
            "WHERE event_subtype IS NOT NULL AND event_subtype <> '' "
            "GROUP BY event_subtype ORDER BY n DESC, event_subtype"
        ).fetchall()
        return [(r["event_subtype"], int(r["n"])) for r in rows]

    def available_experience_types(self) -> List[Tuple[str, int]]:
        """``(experience_type, photo_count)`` across the projection,
        most-used-first. spec/64 vocabulary (expedition_discovery /
        studio_craft / slow_down / urban_culture / milestones_traditions);
        events with no experience_type stay out of the list."""
        rows = self.user_store.conn.execute(
            "SELECT experience_type, COUNT(*) AS n FROM global_items "
            "WHERE experience_type IS NOT NULL "
            "GROUP BY experience_type ORDER BY n DESC, experience_type"
        ).fetchall()
        return [(r["experience_type"], int(r["n"])) for r in rows]

    def available_participants(self) -> List[Tuple[str, int]]:
        """``(participant_category, photo_count)`` across the projection,
        most-used-first. ``participants`` is a JSON array on every item
        row — ``json_each`` expands it; the count is how many items
        include each category. Empty arrays contribute nothing (json_each
        emits no rows for ``[]``)."""
        rows = self.user_store.conn.execute(
            "SELECT pe.value AS v, COUNT(*) AS n "
            "FROM global_items, json_each(global_items.participants) AS pe "
            "WHERE global_items.participants IS NOT NULL "
            "  AND json_valid(global_items.participants) "
            "  AND pe.value IS NOT NULL "
            "GROUP BY pe.value ORDER BY n DESC, pe.value"
        ).fetchall()
        return [(str(r["v"]), int(r["n"])) for r in rows]

    # Mapping from filters_json keys → the available_* method for that facet.
    # ``facet_inventory`` dispatches through this so the dialog can lazily
    # resolve any facet by its filter key (spec/83 §5).
    _FACET_INVENTORIES: Dict[str, str] = {
        "styles":            "available_classifications",
        "camera_ids":        "available_cameras",
        "lens_models":       "available_lenses",
        "country_codes":     "available_country_codes",
        "cities":            "available_cities",
        "color_labels":      "available_color_labels",
        # spec/86 — event-level qualifiers.
        "event_types":       "available_event_types",
        "event_subtypes":    "available_event_subtypes",
        "experience_types":  "available_experience_types",
        "participants":      "available_participants",
    }

    def facet_inventory(self, facet_key: str) -> List[Tuple[str, int]]:
        """The lazy seam (spec/83 §5): given a ``filters_json`` key, return
        ``(value, count)`` for the matching facet. The dialog calls this only
        when the user opens a filter — high-cardinality reads (cameras /
        lenses / cities / countries) never run at dialog open. Unknown keys
        return ``[]`` (forward-compat for the spec/32 tags / people roadmap)."""
        method_name = self._FACET_INVENTORIES.get(facet_key)
        if method_name is None:
            return []
        return getattr(self, method_name)()

    def event_uuids_in_projection(self) -> List[str]:
        """The set of events whose items live in the projection — useful
        for the cross-event-band entry point (spec/75 §2) and for the
        startup reconcile's "what do I know about" check."""
        rows = self.user_store.conn.execute(
            "SELECT DISTINCT event_uuid FROM global_items "
            "ORDER BY event_uuid"
        ).fetchall()
        return [r["event_uuid"] for r in rows]

    # =================================================================== #
    # Gear profile (spec/85) — the photographer's kit, user-level
    # =================================================================== #

    GEAR_KIND_CAMERA = "camera"
    GEAR_KIND_LENS = "lens"
    _GEAR_KINDS = (GEAR_KIND_CAMERA, GEAR_KIND_LENS)

    #: Confidence the spec/85 §5 user-gear-hint tier assigns. Above the
    #: generic unknown-lens fallback (UNKNOWN_LENS_FALLBACK_CONFIDENCE = 0.30
    #: in classifier_v2), below explicit user scenarios (typically ≥ 0.55).
    USER_GEAR_HINT_CONFIDENCE: float = 0.45

    @staticmethod
    def _validate_gear_kind(kind: str) -> None:
        if kind not in LibraryGateway._GEAR_KINDS:
            raise ValueError(
                f"gear_profile.kind must be one of "
                f"{LibraryGateway._GEAR_KINDS}, got {kind!r}")

    def get_gear_profile(self) -> List[um.GearProfile]:
        """All gear-profile rows the user has touched. Sorted by ``kind``
        then ``key`` so the wizard (spec/85 §3) renders deterministically."""
        return self.user_store.query_raw(
            um.GearProfile,
            "SELECT * FROM gear_profile ORDER BY kind, key")

    def gear_profile_for(self, kind: str,
                         key: str) -> Optional[um.GearProfile]:
        """Lookup one row by (kind, key). The classifier user-gear-hint
        tier (spec/85 §5, slice 7) and the picker's main / occasional
        split (spec/83 §4, slice 5) read through this."""
        self._validate_gear_kind(kind)
        return self.user_store.get(um.GearProfile, kind, key)

    def set_gear_active(self, kind: str, key: str,
                        is_active: bool) -> None:
        """Toggle the "I currently use this" flag (spec/85 §3). Upserts —
        a row arrives on first toggle and updates in place thereafter; the
        partial index on ``(kind, is_active) WHERE is_active = 1`` keeps the
        active-set read cheap. ``preferred_genres`` is preserved across the
        upsert (the spec/85 §3 wizard sets it via :meth:`set_gear_genres`).
        """
        self._guard_read_only()
        self._validate_gear_kind(kind)
        if not key:
            raise ValueError("gear_profile.key must not be empty")
        now = self._now()
        with self.user_store.transaction() as conn:
            conn.execute(
                "INSERT INTO gear_profile (kind, key, is_active, "
                "preferred_genres, updated_at) "
                "VALUES (?, ?, ?, NULL, ?) "
                "ON CONFLICT(kind, key) DO UPDATE SET "
                "  is_active = excluded.is_active, "
                "  updated_at = excluded.updated_at",
                (kind, key, 1 if is_active else 0, now))

    def set_gear_genres(self, kind: str, key: str,
                        genres: Optional[Sequence[str]]) -> None:
        """Replace the row's preferred genres (spec/85 §3). ``genres`` is a
        list of :class:`core.vocabulary.Scenario` keys (the wizard hands
        these in); ``None`` or empty clears the tag. Upserts — see
        :meth:`set_gear_active`; ``is_active`` is preserved across the
        upsert (the wizard sets both independently)."""
        self._guard_read_only()
        self._validate_gear_kind(kind)
        if not key:
            raise ValueError("gear_profile.key must not be empty")
        encoded: Optional[str] = (
            None if not genres else json.dumps([str(g) for g in genres]))
        now = self._now()
        with self.user_store.transaction() as conn:
            conn.execute(
                "INSERT INTO gear_profile (kind, key, is_active, "
                "preferred_genres, updated_at) "
                "VALUES (?, ?, 0, ?, ?) "
                "ON CONFLICT(kind, key) DO UPDATE SET "
                "  preferred_genres = excluded.preferred_genres, "
                "  updated_at = excluded.updated_at",
                (kind, key, encoded, now))

    @staticmethod
    def gear_preferred_genres(row: um.GearProfile) -> List[str]:
        """Decode ``preferred_genres`` from its JSON envelope — tolerant
        readers (malformed JSON / non-list payloads collapse to ``[]``).
        Slice-7 classifier reads through this."""
        if not row.preferred_genres:
            return []
        try:
            data = json.loads(row.preferred_genres)
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [str(g) for g in data]

    def gear_fingerprint(self) -> str:
        """Stable hash of the full gear-profile state (spec/85 §5).

        Used by the background classification pass to bump
        ``item.classification_rules_version`` whenever the user changes a
        gear flag or a preferred-genre tag — the next pass then re-
        classifies untouched items (``classification_source != 'user'``).
        Empty profile → fixed empty token so the stamp stays deterministic
        across reboots before the user ever touches the wizard.
        """
        import hashlib
        h = hashlib.sha256()
        for row in self.get_gear_profile():
            h.update(f"{row.kind}|{row.key}|{int(bool(row.is_active))}|"
                     f"{row.preferred_genres or ''}\n".encode("utf-8"))
        return h.hexdigest()[:12]

    def make_gear_hint(
        self,
        *,
        camera_id: Optional[str],
        lens_model: Optional[str],
    ) -> Callable:
        """Build a per-item gear-hint callable for the classifier (spec/85
        §5). The returned closure takes a ``PhotoContext`` and returns
        ``(Scenario, confidence)`` for the first tagged kit it finds,
        else ``None``.

        Resolution order is **lens first**, then camera (spec/85 §6 lean
        — the lens is the more specific optic, so a "macro lens" beats a
        "wildlife body"). The first preferred genre in the row is the
        hint — multi-genre rows are treated as ranked preference (the
        user puts their primary intent first); future work might weigh
        the whole list."""
        # Captured at build time so the closure is fast — no per-call SQL
        # for items shot with un-tagged gear.
        lens_row = (self.gear_profile_for("lens", lens_model)
                    if lens_model else None)
        cam_row = (self.gear_profile_for("camera", camera_id)
                   if camera_id else None)
        # Pre-resolve the Scenario so the closure stays cheap and the
        # error path (unknown genre string) handles once at build time.
        from core.vocabulary import Scenario as _Scenario

        hint_scenario: Optional[_Scenario] = None
        for row in (lens_row, cam_row):
            if row is None:
                continue
            for genre in self.gear_preferred_genres(row):
                try:
                    hint_scenario = _Scenario(genre)
                    break
                except ValueError:
                    log.warning(
                        "gear_profile preferred_genres has unknown "
                        "scenario %r for (%s, %s) — skipping",
                        genre, row.kind, row.key)
                    continue
            if hint_scenario is not None:
                break

        confidence = self.USER_GEAR_HINT_CONFIDENCE

        def _hint(_ctx):
            if hint_scenario is None:
                return None
            return (hint_scenario, confidence)

        return _hint

    # =================================================================== #
    # Cross-event Cuts (spec/93 §3, spec/94 Phase 4a-ii)
    #
    # mira.db's ``cut`` + ``cut_member`` tables hold the dishes that span
    # events (one Cut row, members carry their source event's UUID). The
    # bytes never move — only references. No FK across stores: a member's
    # ``event_id`` is opaque TEXT here, validated against the events
    # index out-of-band (gateway sweeps). The freeze invariant (spec/81
    # §5) lives at this seam too: ``delete_dc`` NULLs ``source_dc_id``
    # on any Cut that pointed at the dropped DC.
    # =================================================================== #

    def cross_event_cuts(self) -> List[um.Cut]:
        """Every cross-event Cut, most-recently-updated first. Same
        ordering :class:`mira.gateway.gateway.CrossEventCutRow` used in
        the legacy event.db-walk surface so the dialogs see the same
        sequence on the flip."""
        return self.user_store.query_raw(
            um.Cut, "SELECT * FROM cut ORDER BY updated_at DESC, id")

    def cross_event_cut(self, cut_id: str) -> Optional[um.Cut]:
        """Lookup by id; ``None`` when the Cut was deleted (the freeze
        invariant lives on the Cut's frozen members, not the id)."""
        return self.user_store.get(um.Cut, cut_id)

    def cross_event_cut_by_tag(self, tag: str) -> Optional[um.Cut]:
        """Lookup by tag — the cross-event name namespace is global at
        the user level. COLLATE NOCASE matches the SQL constraint."""
        rows = self.user_store.query_by(um.Cut, tag=tag)
        return rows[0] if rows else None

    def cross_event_cut_members(self, cut_id: str) -> List[um.CutMember]:
        """Membership rows for one cross-event Cut, ordered by source
        event then ``added_at`` (chronological insertion). The flat-grid
        + export pipelines re-sort by capture-time downstream; this
        order is the insertion order, useful for the detail viewer's
        per-event grouping."""
        return self.user_store.query_raw(
            um.CutMember,
            "SELECT * FROM cut_member WHERE cut_id = ? "
            "ORDER BY event_id, added_at, member_id",
            (cut_id,))

    def create_cross_event_cut(
        self,
        name: str,
        *,
        source_dc_id: Optional[str] = None,
        source_dc_kind: Optional[str] = "user",
        expr_snapshot: Optional[Sequence[Sequence]] = None,
        target_s: Optional[int] = None,
        max_s: Optional[int] = None,
        photo_s: float = 6.0,
        default_state: str = "skipped",
        music_category: Optional[str] = None,
        separators: bool = False,
        overlay_fields: Optional[Sequence[str]] = None,
        overlay_mode: Optional[str] = None,
        card_style: str = "black",
        aspect: str = "16:9",
    ) -> um.Cut:
        """Create a cross-event Cut from a user-typed name (slugified
        + validated against the cross-event Cut + DC namespaces).
        Returns the persisted row.

        ``expr_snapshot`` is the spec/81 §2 frozen formula — the Cut
        never re-resolves against it (spec/81 §5); the membership is
        the source of truth. ``card_style`` lands in ``extras_json``
        per the standing house pattern (spec/61 §4)."""
        self._guard_read_only()
        slug = cut_names.slugify(name)
        # Collide-check against existing cross-event Cuts + the cross-
        # event DC namespace (a tag means one thing across the library).
        taken = [c.tag for c in self.cross_event_cuts()]
        taken.extend(d.tag for d in self.dynamic_collections())
        err = cut_names.check_tag(slug, taken)
        if err:
            raise ValueError(err)
        cut_id = self._new_id()
        now = self._now()
        expr_list = [list(t) for t in (expr_snapshot or ())]
        extras = json.dumps(
            {"card_style": card_style} if card_style else {})
        from core.cut_aspect import normalise as _normalise_aspect
        row = um.Cut(
            id=cut_id, tag=slug,
            source_dc_id=source_dc_id,
            source_dc_kind=source_dc_kind,
            expr_snapshot_json=json.dumps(expr_list),
            target_s=target_s, max_s=max_s, photo_s=photo_s,
            default_state=default_state,
            music_category=music_category,
            separators=bool(separators),
            overlay_fields_json=json.dumps(list(overlay_fields or ())),
            overlay_mode=overlay_mode,
            # spec/111 — canvas aspect, coerced through the canonical
            # list so the DDL CHECK + migrated rows can both rely on
            # the value being one of the four enum members.
            aspect=_normalise_aspect(aspect),
            created_at=now, updated_at=now,
            extras_json=extras,
        )
        with self.user_store.transaction():
            self.user_store.upsert(row)
        return row

    def rename_cross_event_cut(self, cut_id: str, new_name: str) -> um.Cut:
        """Rename — slug + validate against the cross-event namespace
        (excluding this Cut's own tag). The freeze invariant is
        untouched (rename changes only the tag, not the membership)."""
        self._guard_read_only()
        cut = self.cross_event_cut(cut_id)
        if cut is None:
            raise KeyError(cut_id)
        slug = cut_names.slugify(new_name)
        taken = [c.tag for c in self.cross_event_cuts() if c.id != cut_id]
        taken.extend(d.tag for d in self.dynamic_collections())
        err = cut_names.check_tag(slug, taken)
        if err:
            raise ValueError(err)
        with self.user_store.transaction() as conn:
            conn.execute(
                "UPDATE cut SET tag = ?, updated_at = ? WHERE id = ?",
                (slug, self._now(), cut_id))
        return replace(cut, tag=slug, updated_at=self._now())

    def update_cross_event_cut_settings(
        self,
        cut_id: str,
        *,
        source_dc_id: Any = _UNSET,
        source_dc_kind: Any = _UNSET,
        expr_snapshot_json: Any = _UNSET,
        target_s: Any = _UNSET,
        max_s: Any = _UNSET,
        photo_s: Any = _UNSET,
        default_state: Any = _UNSET,
        music_category: Any = _UNSET,
        separators: Any = _UNSET,
        overlay_fields_json: Any = _UNSET,
        overlay_mode: Any = _UNSET,
        card_style: Any = _UNSET,
        aspect: Any = _UNSET,
    ) -> None:
        """Edit-in-place — passes update only the fields the caller
        names. Pass ``None`` to NULL a column; omit the kwarg to leave
        it untouched (the ``_UNSET`` sentinel). ``card_style`` rides
        in ``extras_json``."""
        self._guard_read_only()
        cut = self.cross_event_cut(cut_id)
        if cut is None:
            raise KeyError(cut_id)
        sets: Dict[str, Any] = {}
        for col, val in (
            ("source_dc_id", source_dc_id),
            ("source_dc_kind", source_dc_kind),
            ("expr_snapshot_json", expr_snapshot_json),
            ("target_s", target_s),
            ("max_s", max_s),
            ("photo_s", photo_s),
            ("default_state", default_state),
            ("music_category", music_category),
            ("overlay_fields_json", overlay_fields_json),
            ("overlay_mode", overlay_mode),
        ):
            if val is _UNSET:
                continue
            sets[col] = val
        # ``separators`` is the only column with a Boolean coercion;
        # handle it after the loop so the sentinel check stays clean.
        if separators is not _UNSET:
            sets["separators"] = 1 if bool(separators) else 0
        if aspect is not _UNSET:
            from core.cut_aspect import normalise as _normalise_aspect
            sets["aspect"] = _normalise_aspect(aspect)
        if card_style is not _UNSET:
            extras = {}
            try:
                extras = json.loads(cut.extras_json or "{}") or {}
                if not isinstance(extras, dict):
                    extras = {}
            except (ValueError, TypeError):
                extras = {}
            if card_style is None:
                extras.pop("card_style", None)
            else:
                extras["card_style"] = card_style
            sets["extras_json"] = json.dumps(extras)
        if not sets:
            return
        cols = ", ".join(f"{k} = ?" for k in sets)
        with self.user_store.transaction() as conn:
            conn.execute(
                f"UPDATE cut SET {cols}, updated_at = ? WHERE id = ?",
                (*sets.values(), self._now(), cut_id))

    def set_cross_event_cut_members(
        self,
        cut_id: str,
        members: Iterable[Mapping[str, Any]],
    ) -> None:
        """Replace-all membership: drop every existing row for ``cut_id``
        and insert the passed-in set in one transaction. Each member is a
        mapping with ``event_id`` (REQUIRED, non-empty), ``kind`` ('export'
        | 'grab'), and the matching relpath (``export_relpath`` for
        'export', ``origin_relpath`` for 'grab'). ``member_id`` is
        auto-derived from the relpath of the kind in use; the caller
        may pass an explicit value to override.

        The cut row's ``updated_at`` stamps to ``now`` so the list
        ordering ("newest" first) reflects the membership change."""
        self._guard_read_only()
        if self.cross_event_cut(cut_id) is None:
            raise KeyError(cut_id)
        now = self._now()
        prepared: list = []
        for m_raw in members:
            event_id = (m_raw.get("event_id") or "").strip()
            if not event_id:
                raise ValueError(
                    "cross_event cut_member.event_id is required "
                    "(spec/93 §3 — cross-event Cuts span events)")
            kind = m_raw.get("kind") or "export"
            export_relpath = m_raw.get("export_relpath")
            origin_relpath = m_raw.get("origin_relpath")
            member_id = m_raw.get("member_id")
            if member_id is None:
                member_id = (origin_relpath if kind == "grab"
                             else export_relpath)
            if not member_id:
                raise ValueError(
                    "cross_event cut_member requires either "
                    "export_relpath (kind='export') or origin_relpath "
                    "(kind='grab')")
            prepared.append((
                cut_id, event_id, member_id, kind,
                export_relpath, origin_relpath, now))
        with self.user_store.transaction() as conn:
            conn.execute(
                "DELETE FROM cut_member WHERE cut_id = ?", (cut_id,))
            for row in prepared:
                conn.execute(
                    "INSERT INTO cut_member "
                    "(cut_id, event_id, member_id, kind, "
                    " export_relpath, origin_relpath, added_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", row)
            conn.execute(
                "UPDATE cut SET updated_at = ? WHERE id = ?",
                (now, cut_id))

    def delete_cross_event_cut(self, cut_id: str) -> None:
        """Drop a cross-event Cut. Members cascade via the cut_id FK
        (same store; the FK is safe). Already-exported folders on disk
        are untouched — :meth:`export_cross_event_cut` writes
        per-call, not as a freezing operation."""
        self._guard_read_only()
        with self.user_store.transaction() as conn:
            conn.execute("DELETE FROM cut WHERE id = ?", (cut_id,))

    def cross_event_cut_member_count(self, cut_id: str) -> int:
        """Count members of one cross-event Cut — used by the Cuts list
        row for the "N members" line without re-reading every row."""
        row = self.user_store.conn.execute(
            "SELECT COUNT(*) FROM cut_member WHERE cut_id = ?",
            (cut_id,)).fetchone()
        return int(row[0] if row else 0)

    def cross_event_cut_usage_count(
        self,
        event_uuid: str,
        export_relpaths: Iterable[str],
    ) -> int:
        """spec/147 §4 — count the distinct cross-event Cuts that
        reference any ``(event_uuid, export_relpath)`` pair from the
        passed-in set.

        The Export-surface delete confirm reads this to warn the user
        before nuking a file that's still in a cross-event Cut. Counts
        DISTINCT cuts (not member rows) so a single Cut that contains
        two of the doomed files counts once, not twice.

        Returns 0 when ``export_relpaths`` is empty or ``event_uuid``
        is blank."""
        relpaths = [r for r in (export_relpaths or ()) if r]
        if not relpaths or not event_uuid:
            return 0
        placeholders = ",".join(["?"] * len(relpaths))
        sql = (
            "SELECT COUNT(DISTINCT cut_id) FROM cut_member "
            "WHERE event_id = ? AND kind = 'export' "
            f"AND export_relpath IN ({placeholders})"
        )
        row = self.user_store.conn.execute(
            sql, (event_uuid, *relpaths)).fetchone()
        return int(row[0] if row else 0)

    def delete_cross_event_cut_members(
        self,
        event_uuid: str,
        export_relpaths: Iterable[str],
    ) -> int:
        """spec/147 §4 — remove every cross-event ``cut_member`` row that
        references ``(event_uuid, export_relpath)`` for any relpath in
        the passed-in set.

        Mirrors the event-scope cleanup
        :meth:`EventGateway.delete_exported_file_by_relpath` already
        does (it drops event-scope members for the deleted relpath
        before the file unlink); this is the cross-event sibling so
        no library Cut is left with a dangling member after the user
        deletes an exported file.

        Updates each affected Cut's ``updated_at`` so the "newest"
        ordering in the Cuts list reflects the membership change.
        Returns the number of rows removed (0 on empty input)."""
        self._guard_read_only()
        relpaths = [r for r in (export_relpaths or ()) if r]
        if not relpaths or not event_uuid:
            return 0
        placeholders = ",".join(["?"] * len(relpaths))
        # The two-step shape — collect affected cut ids FIRST so the
        # UPDATE list is stable across the DELETE.
        affected = self.user_store.conn.execute(
            "SELECT DISTINCT cut_id FROM cut_member "
            "WHERE event_id = ? AND kind = 'export' "
            f"AND export_relpath IN ({placeholders})",
            (event_uuid, *relpaths)).fetchall()
        cut_ids = [r[0] for r in affected]
        if not cut_ids:
            return 0
        now = self._now()
        with self.user_store.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM cut_member "
                "WHERE event_id = ? AND kind = 'export' "
                f"AND export_relpath IN ({placeholders})",
                (event_uuid, *relpaths))
            removed = int(cursor.rowcount or 0)
            cut_id_placeholders = ",".join(["?"] * len(cut_ids))
            conn.execute(
                f"UPDATE cut SET updated_at = ? "
                f"WHERE id IN ({cut_id_placeholders})",
                (now, *cut_ids))
        return removed

    def stamp_cross_event_cut_exported(self, cut_id: str) -> None:
        """The export pipeline's stamp: update ``last_exported_at`` only
        (no other field). The Cut's identity is untouched."""
        self._guard_read_only()
        if self.cross_event_cut(cut_id) is None:
            raise KeyError(cut_id)
        now = self._now()
        with self.user_store.transaction() as conn:
            conn.execute(
                "UPDATE cut SET last_exported_at = ? WHERE id = ?",
                (now, cut_id))

    # =================================================================== #
    # Sync triggers — the projection stays in lockstep with event.db
    # =================================================================== #

    def sync_event(
        self,
        *,
        event_store: EventStore,
        event_uuid: str,
        event_name: str,
    ) -> int:
        """Sync one event's projection slice. The per-event close hook calls
        this so the cross-event index never goes stale on a clean close
        (spec/81 Phase 2 handover recommendation #2). Returns the row count
        written.

        Read-only sessions skip the projection write — the writer-half
        machine owns sync (spec/76 §B.1). Returning 0 keeps the caller's
        bookkeeping clean (it's the same shape as "event had no rows")."""
        if self._skip_if_read_only():
            log.debug("sync_event skipped for %s (read-only mode)", event_uuid)
            return 0
        return gis.sync_event(
            event_store=event_store,
            user_store=self.user_store,
            event_uuid=event_uuid,
            event_name=event_name,
            now=self._now,
        )

    def drop_event(self, event_uuid: str) -> int:
        """Drop one event's projection slice (event deleted from the
        library). Returns the row count removed.

        Read-only sessions skip the drop — event deletion itself is
        gated by :meth:`EventGateway._touch`, so reaching here in
        read-only mode means a maintenance pass triggered it
        unexpectedly. Skip silently rather than raise (spec/76 §B.1)."""
        if self._skip_if_read_only():
            log.debug("drop_event skipped for %s (read-only mode)", event_uuid)
            return 0
        return gis.drop_event(user_store=self.user_store, event_uuid=event_uuid)

    def reconcile_all(
        self,
        *,
        open_event_store: Callable[[str], Optional[EventStore]],
        known_events: Iterable[Tuple[str, str]],
    ) -> dict:
        """Startup reconcile: re-sync every known event + drop stale slices
        (spec/81 Phase 2 handover recommendation #2). ``known_events`` is
        ``(event_uuid, event_name)`` tuples from the events index;
        ``open_event_store(uuid) -> EventStore | None`` lets the caller
        decide the open-policy (read-only? snapshot?). Unopenable events are
        skipped + logged, never raised.

        Read-only sessions skip the reconcile pass — projection writes
        belong to the writer machine (spec/76 §B.1). Returns an empty
        summary dict so callers' bookkeeping stays uniform."""
        if self._skip_if_read_only():
            log.debug("reconcile_all skipped (read-only mode)")
            return {"synced": 0, "dropped": 0, "skipped": []}
        return gis.reconcile_all(
            user_store=self.user_store,
            open_event_store=open_event_store,
            known_events=known_events,
            now=self._now,
        )


__all__ = ["LibraryGateway"]
