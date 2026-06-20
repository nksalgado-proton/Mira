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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    ) -> None:
        self.user_store = user_store
        self._now = now
        self._new_id = new_id

    def __enter__(self) -> "LibraryGateway":
        return self

    def __exit__(self, *exc) -> None:                         # noqa: D401
        """Do NOT close the user_store — lifecycle is owned by the wirer."""
        pass

    # =================================================================== #
    # Dynamic Collections (cross-event — ``saved_filter`` rows)
    # =================================================================== #

    def dynamic_collections(self) -> List[um.SavedFilter]:
        """All cross-event DCs, oldest first. Mirrors
        :meth:`EventGateway.dynamic_collections`; readers don't care that the
        rows come from ``saved_filter`` rather than ``dynamic_collection``."""
        return self.user_store.query_raw(
            um.SavedFilter,
            "SELECT * FROM saved_filter ORDER BY created_at, id")

    def dynamic_collection(self, dc_id: str) -> Optional[um.SavedFilter]:
        return self.user_store.get(um.SavedFilter, dc_id)

    def dc_by_tag(self, tag: str) -> Optional[um.SavedFilter]:
        rows = self.user_store.query_by(um.SavedFilter, tag=tag)
        return rows[0] if rows else None

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
        slug = cut_names.slugify(name)
        err = cut_names.check_tag(slug, [d.tag for d in self.dynamic_collections()])
        if err:
            raise ValueError(err)
        dc_id = self._new_id()
        expr_list = [list(t) for t in expr]
        self._check_dc_cycle(dc_id, expr_list)
        now = self._now()
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
        dc = self.dynamic_collection(dc_id)
        if dc is None:
            raise KeyError(dc_id)
        sets: Dict[str, str] = {}
        if expr is not None:
            expr_list = [list(t) for t in expr]
            self._check_dc_cycle(dc_id, expr_list)
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
        dc = self.dynamic_collection(dc_id)
        if dc is None:
            raise KeyError(dc_id)
        slug = cut_names.slugify(new_name)
        err = cut_names.check_tag(
            slug, [d.tag for d in self.dynamic_collections() if d.id != dc_id])
        if err:
            raise ValueError(err)
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
        with self.user_store.transaction() as conn:
            conn.execute("DELETE FROM saved_filter WHERE id = ?", (dc_id,))

    # =================================================================== #
    # Resolution + probes
    # =================================================================== #

    def resolve_dc(
        self,
        expr: Sequence[Sequence],
        filters: Optional[Mapping] = None,
    ) -> List[Tuple[str, str]]:
        """Resolve a cross-event DC formula (spec/81 §2). Returns
        ``(event_uuid, item_id)`` tuples in chronological show order. The
        formula's own filters apply at the top; nested DCs' filters apply
        before they compose upward (the resolver handles both)."""
        keys = cev.resolve_cross_event(self.user_store, expr, filters)
        return [cev.unpack_key(k) for k in keys]

    def resolve_dc_keys(
        self,
        expr: Sequence[Sequence],
        filters: Optional[Mapping] = None,
    ) -> List[str]:
        """The packed-key variant of :meth:`resolve_dc` — keep when the
        caller wants the resolver's native ``"event_uuid::item_id"`` strings
        (e.g. composing further set operations without re-packing). The flat
        grid + export pipelines use :meth:`resolve_dc` directly."""
        return cev.resolve_cross_event(self.user_store, expr, filters)

    def dc_probe(self, expr: Sequence[Sequence],
                 filters: Optional[Mapping] = None) -> int:
        """The dialog's live count for a draft DC formula (spec/81 §2)."""
        return len(cev.resolve_cross_event(self.user_store, expr, filters))

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
        written."""
        return gis.sync_event(
            event_store=event_store,
            user_store=self.user_store,
            event_uuid=event_uuid,
            event_name=event_name,
            now=self._now,
        )

    def drop_event(self, event_uuid: str) -> int:
        """Drop one event's projection slice (event deleted from the
        library). Returns the row count removed."""
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
        skipped + logged, never raised."""
        return gis.reconcile_all(
            user_store=self.user_store,
            open_event_store=open_event_store,
            known_events=known_events,
            now=self._now,
        )


__all__ = ["LibraryGateway"]
