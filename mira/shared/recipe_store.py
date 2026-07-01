"""``RecipeStore`` — CRUD over the library-level ``recipe`` table (spec/90
§7 Phase 3).

A Recipe is the saved Cut/Collection configuration (spec/90 §5.1):
everything the user composed except the Picker session's per-file
decisions. Stored at the library level so the same Recipe replays across
events (a `#short` Recipe made on Bali works on Costa Rica too — the named
operands resolve fresh against the target event's DCs / Cuts).

The store owns:

* CRUD primitives — :meth:`create`, :meth:`update`, :meth:`delete`,
  :meth:`get`, :meth:`by_name`, :meth:`list`.
* Cross-flavour visibility — :meth:`list` accepts an ``include_other``
  flag (spec/90 §5.5). Default is "filter to the requested flavour";
  with ``include_other=True`` Collection Recipes show alongside Cut ones
  (and vice versa), with same-flavour first so the dialog can render the
  cross-flavour set as an appended "show … here too" section.
* JSON parse / serialise. Callers pass / receive Python dicts for the
  ``composition``; the JSON encoding lives inside the store so the rest
  of the codebase never touches ``json.dumps`` / ``json.loads``
  around recipe blobs.
* Uniqueness guard. The SQL ``UNIQUE (flavour, name)`` constraint
  surfaces as a typed :class:`RecipeNameTakenError` rather than a raw
  ``sqlite3.IntegrityError`` so callers can pattern-match.

The store is **service-level** — wraps an open :class:`UserStore`; lifecycle
is owned by whoever wires it into the app. No Qt, no gateway, no resolver
coupling.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, List, Mapping, Optional

from mira.user_store import models as um
from mira.user_store.repo import UserStore

log = logging.getLogger(__name__)


# spec/162 Round 2d.D (2026-07-01) — the ``"cut"`` /
# ``"collection"`` / ``FLAVOURS`` public constants retired here.
# The ``flavour`` column still exists at the schema layer (a
# cheap-and-reversible artifact per spec/162 §2), so the store still
# writes / reads it, but the module no longer exposes the closed-enum
# vocabulary as a public API. Internal checks compare against the
# private ``_FLAVOURS`` tuple below directly.
_FLAVOURS = ("cut", "collection")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_uuid() -> str:
    return uuid.uuid4().hex


class RecipeNameTakenError(ValueError):
    """A new / renamed Recipe collides with an existing (flavour, name) pair.

    Carries ``flavour`` + ``name`` so the dialog can craft "A Cut Recipe
    named X already exists — choose a different name". The DDL CHECK
    splits the namespace by flavour (spec/90 §5.5), so the same name in
    the OTHER flavour is fine; this error only fires within one flavour."""

    def __init__(self, flavour: str, name: str) -> None:
        super().__init__(f"recipe ({flavour!r}, {name!r}) already exists")
        self.flavour = flavour
        self.name = name


class RecipeStore:
    """CRUD service over the ``recipe`` table (spec/90 §5.1).

    Wraps an open :class:`UserStore`; lifecycle is owned by the wirer.
    """

    def __init__(
        self,
        user_store: UserStore,
        *,
        now: Callable[[], str] = _utc_now_iso,
        new_id: Callable[[], str] = _new_uuid,
        recipes_library: Any = None,
    ) -> None:
        self.user_store = user_store
        self._now = now
        self._new_id = new_id
        # spec/94 Phase 1b — when wired, every Recipe read/write goes
        # through the JSON tree (the single live source). When None
        # (unit-test path), the legacy ``mira.db.recipe`` SQL surface
        # stays in place.
        self._recipes_library = recipes_library

    # ----- helpers --------------------------------------------------------- #

    @staticmethod
    def composition(recipe: um.Recipe) -> dict:
        """Decode a Recipe's ``composition_json`` to a dict. Tolerant: a
        malformed blob reads as ``{}`` rather than raising, matching the
        spec/81 / spec/90 resolver's posture (charter §5.3)."""
        try:
            data = json.loads(recipe.composition_json or "{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _check_flavour(self, flavour: str) -> None:
        if flavour not in _FLAVOURS:
            raise ValueError(
                f"invalid recipe flavour: {flavour!r} "
                f"(expected one of {sorted(_FLAVOURS)})")

    def _check_unique(self, flavour: str, name: str,
                      excluding_id: Optional[str] = None) -> None:
        """Pre-check the UNIQUE(flavour, name) constraint so the typed
        :class:`RecipeNameTakenError` fires before the SQL would. Excludes
        ``excluding_id`` so a same-name rename on the SAME row passes."""
        sql = ("SELECT id FROM recipe WHERE flavour = ? AND name = ? "
               "LIMIT 1")
        row = self.user_store.conn.execute(sql, (flavour, name)).fetchone()
        if row is not None and row["id"] != excluding_id:
            raise RecipeNameTakenError(flavour, name)

    # ----- CRUD ------------------------------------------------------------ #

    def create(
        self,
        name: str,
        flavour: Optional[str] = None,
        composition: Optional[Mapping[str, Any]] = None,
        *,
        scope: str = 'event',
    ) -> um.Recipe:
        """Create a new Recipe row. ``composition`` is serialised to JSON
        internally. Raises :class:`RecipeNameTakenError` on a (flavour, name)
        collision; raises ``ValueError`` for an invalid flavour, an empty
        name, or an out-of-vocabulary scope.

        spec/162 §9 — ``scope`` defaults to ``'event'`` so pre-spec/162
        call-sites (SaveRecipe path in event-scope NewCutDialog) don't
        need touching. Round 3's cross-event dialog surface will pass
        ``scope='cross-event'`` explicitly.

        spec/162 Round 2d.D — ``flavour`` gained a ``None`` default; when
        omitted it derives from ``scope`` (``'event'`` → ``'cut'``,
        ``'cross-event'`` → ``'collection'``). Callers can still pass an
        explicit flavour for edge cases (schema-preserving migrations,
        legacy tests) but new UI paths just spell out scope."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("recipe name must be a non-empty string")
        if flavour is None:
            flavour = 'cut' if scope == 'event' else 'collection'
        if composition is None:
            composition = {}
        self._check_flavour(flavour)
        if scope not in ('event', 'cross-event'):
            raise ValueError(
                f"scope must be 'event' or 'cross-event', got {scope!r}")

        if self._recipes_library is not None:
            return self._json_create(name, flavour, composition, scope=scope)

        self._check_unique(flavour, name)
        now = self._now()
        recipe = um.Recipe(
            id=self._new_id(),
            name=name,
            flavour=flavour,
            composition_json=json.dumps(dict(composition or {})),
            created_at=now,
            updated_at=now,
            scope=scope,
        )
        try:
            with self.user_store.transaction():
                self.user_store.upsert(recipe)
        except sqlite3.IntegrityError as exc:
            # Race-condition fallback: another writer may have inserted a
            # colliding row between :meth:`_check_unique` and the COMMIT.
            # Surface the typed error rather than the raw IntegrityError.
            raise RecipeNameTakenError(flavour, name) from exc
        return recipe

    # ── Phase 1b: JSON-tree-backed paths ──────────────────────────

    def _json_create(
        self,
        name: str,
        flavour: str,
        composition: Mapping[str, Any],
        *,
        scope: str = 'event',
    ) -> um.Recipe:
        """JSON-tree creation. Uniqueness check folds (flavour, name) by
        walking the library; on collision raises the typed error so the
        dialog can pattern-match."""
        for df in self._recipes_library.all_definitions():
            if (df.name == name
                    and (df.payload or {}).get("flavour") == flavour):
                raise RecipeNameTakenError(flavour, name)
        from core.definition_files import DefinitionFile, KIND_RECIPE
        now = self._now()
        payload = dict(composition or {})
        payload["flavour"] = flavour
        payload["scope"] = scope
        payload["created_at"] = now
        payload["updated_at"] = now
        df = DefinitionFile(
            id=self._new_id(),
            name=name,
            kind=KIND_RECIPE,
            payload=payload,
        )
        self._recipes_library.save(df)
        return self._df_to_recipe(df)

    @staticmethod
    def _df_to_recipe(df: Any) -> um.Recipe:
        """Project a :class:`DefinitionFile` (JSON-backed) onto a
        :class:`Recipe` so existing callers see the same row shape."""
        payload = dict(df.payload or {})
        flavour = payload.pop("flavour", "cut")
        # spec/162 §9 — scope is 'event' by default when the payload
        # predates the column (pre-Round 2c JSON files). Round 3's
        # cross-event save path writes 'cross-event' explicitly.
        scope = payload.pop("scope", "event")
        if scope not in ("event", "cross-event"):
            scope = "event"
        created_at = payload.pop("created_at", "")
        updated_at = payload.pop("updated_at", created_at)
        composition_json = json.dumps(payload)
        return um.Recipe(
            id=df.id,
            name=df.name,
            flavour=flavour,
            composition_json=composition_json,
            created_at=created_at or _utc_now_iso(),
            updated_at=updated_at or created_at or _utc_now_iso(),
            scope=scope,
        )

    def _json_update(
        self,
        recipe_id: str,
        *,
        name: Optional[str],
        composition: Optional[Mapping[str, Any]],
        existing: um.Recipe,
    ) -> um.Recipe:
        """JSON-tree update. Rename + composition swap in one atomic
        write."""
        df = self._recipes_library.by_id(recipe_id)
        if df is None:
            raise KeyError(recipe_id)
        new_name = existing.name
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("recipe name must be a non-empty string")
            if name != existing.name:
                for other in self._recipes_library.all_definitions():
                    if (other.id != recipe_id
                            and other.name == name
                            and (other.payload or {}).get("flavour")
                                == existing.flavour):
                        raise RecipeNameTakenError(existing.flavour, name)
                new_name = name
        payload = dict(df.payload or {})
        if composition is not None:
            comp = dict(composition)
            comp["flavour"] = existing.flavour
            # Preserve created_at; refresh updated_at.
            comp["created_at"] = payload.get(
                "created_at") or existing.created_at
            comp["updated_at"] = self._now()
            payload = comp
        else:
            payload["updated_at"] = self._now()
        if new_name != df.name:
            self._recipes_library.rename(recipe_id, new_name)
            df = self._recipes_library.by_id(recipe_id)
            assert df is not None
        df.payload = payload
        from core.definition_files import write_definition
        write_definition(df)
        self._recipes_library.refresh()
        return self._df_to_recipe(df)

    def _json_list(
        self,
        *,
        flavour: Optional[str],
        include_other: bool,
    ) -> List[um.Recipe]:
        """JSON-tree list. Sorts so the requested flavour comes first
        (matching the SQL ORDER BY CASE behaviour)."""
        all_defs = self._recipes_library.all_definitions()
        recipes = [self._df_to_recipe(df) for df in all_defs]
        if flavour is None:
            recipes.sort(key=lambda r: (r.flavour, r.name.lower(), r.id))
            return recipes
        if include_other:
            recipes.sort(
                key=lambda r: (
                    0 if r.flavour == flavour else 1,
                    r.name.lower(),
                    r.id,
                )
            )
            return recipes
        return [r for r in recipes if r.flavour == flavour]

    def update(
        self,
        id: str,
        *,
        name: Optional[str] = None,
        composition: Optional[Mapping[str, Any]] = None,
    ) -> um.Recipe:
        """Partial update — rename and / or replace the composition blob.
        Touches ``updated_at``; leaves ``created_at`` and ``flavour`` alone
        (a Recipe never crosses the flavour boundary in place — for a flavour
        change the caller deletes and re-creates per spec/90 §5.5).

        Returns the updated Recipe. Raises :class:`KeyError` if no row with
        ``id`` exists; raises :class:`RecipeNameTakenError` on a rename
        collision."""
        recipe = self.get(id)
        if recipe is None:
            raise KeyError(id)

        if self._recipes_library is not None:
            return self._json_update(
                id, name=name, composition=composition,
                existing=recipe,
            )

        sets: dict[str, Any] = {}
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("recipe name must be a non-empty string")
            if name != recipe.name:
                self._check_unique(recipe.flavour, name, excluding_id=id)
                sets["name"] = name
        if composition is not None:
            sets["composition_json"] = json.dumps(dict(composition))
        if not sets:
            return recipe

        now = self._now()
        sets["updated_at"] = now
        cols = ", ".join(f"{k} = ?" for k in sets)
        params = (*sets.values(), id)
        try:
            with self.user_store.transaction() as conn:
                conn.execute(
                    f"UPDATE recipe SET {cols} WHERE id = ?", params)
        except sqlite3.IntegrityError as exc:
            raise RecipeNameTakenError(
                recipe.flavour, sets.get("name", recipe.name)) from exc
        # Re-fetch so the dataclass round-trips with the freshly-written row.
        refreshed = self.get(id)
        assert refreshed is not None
        return refreshed

    def delete(self, id: str) -> None:
        """Drop a Recipe by id. No-ops if the row is already gone — the
        delete is idempotent."""
        if self._recipes_library is not None:
            self._recipes_library.delete(id)
            return
        with self.user_store.transaction() as conn:
            conn.execute("DELETE FROM recipe WHERE id = ?", (id,))

    # ----- queries --------------------------------------------------------- #

    def get(self, id: str) -> Optional[um.Recipe]:
        """Fetch one Recipe by id."""
        if self._recipes_library is not None:
            df = self._recipes_library.by_id(id)
            return self._df_to_recipe(df) if df is not None else None
        return self.user_store.get(um.Recipe, id)

    def by_name(self, flavour: str, name: str) -> Optional[um.Recipe]:
        """Look up a Recipe by ``(flavour, name)`` — the UNIQUE business
        key. Returns ``None`` when no row matches."""
        self._check_flavour(flavour)
        if self._recipes_library is not None:
            for df in self._recipes_library.all_definitions():
                if (df.name == name
                        and (df.payload or {}).get("flavour") == flavour):
                    return self._df_to_recipe(df)
            return None
        rows = self.user_store.query_by(
            um.Recipe, flavour=flavour, name=name)
        return rows[0] if rows else None

    def list(
        self,
        *,
        flavour: Optional[str] = None,
        include_other: bool = False,
        scope: Optional[str] = None,
    ) -> List[um.Recipe]:
        """List Recipes, ordered for the dialog's "Load Recipe…" list.

        Default ordering: by (flavour, name) so within one flavour the
        names sort alphabetically. With ``flavour`` set, that flavour's
        rows come first; with ``include_other=True``, the OTHER flavour's
        rows append after (the spec/90 §5.5 opt-in for the dialog).

        ``flavour=None`` returns every row, sorted by (flavour, name) —
        ``include_other`` is then a no-op (the filter is already off).

        spec/162 §6 — ``scope`` narrows the list to Recipes of that scope
        so the Load Recipe picker in the event-scope dialog never shows
        cross-event Recipes (and vice versa). Applied AFTER the flavour
        filter + ordering; ``scope=None`` (default) returns every scope.
        Tests in :mod:`tests.test_recipe_store` pin this contract."""
        if flavour is not None:
            self._check_flavour(flavour)
        if scope is not None and scope not in ('event', 'cross-event'):
            raise ValueError(
                f"scope must be 'event' or 'cross-event', got {scope!r}")

        if self._recipes_library is not None:
            rows = self._json_list(flavour=flavour, include_other=include_other)
            if scope is not None:
                rows = [r for r in rows if r.scope == scope]
            return rows

        if flavour is None:
            rows = self.user_store.conn.execute(
                "SELECT * FROM recipe ORDER BY flavour, name, id"
            ).fetchall()
            recipes = [self._row_to_recipe(r) for r in rows]
        elif include_other:
            rows = self.user_store.conn.execute(
                "SELECT * FROM recipe "
                "ORDER BY CASE flavour WHEN ? THEN 0 ELSE 1 END, name, id",
                (flavour,),
            ).fetchall()
            recipes = [self._row_to_recipe(r) for r in rows]
        else:
            recipes = self.user_store.query_by(um.Recipe, flavour=flavour)

        if scope is not None:
            recipes = [r for r in recipes if r.scope == scope]
        return recipes

    # ----- internal -------------------------------------------------------- #

    @staticmethod
    def _row_to_recipe(row: sqlite3.Row) -> um.Recipe:
        """Hand-roll the row → dataclass mapping for the queries above. The
        UserStore's generic ``query_by`` already does this for filter-by-
        column lookups; the ``ORDER BY CASE`` form on :meth:`list` uses raw
        SQL, so the same coercion happens here.

        spec/162 §9 — ``scope`` reads via ``sqlite3.Row.keys()`` guard so a
        row from a pre-v10 test fixture (no scope column) degrades cleanly
        to ``'event'`` rather than KeyError-ing."""
        try:
            scope = row["scope"] or "event"
        except (IndexError, KeyError):
            scope = "event"
        return um.Recipe(
            id=row["id"],
            name=row["name"],
            flavour=row["flavour"],
            composition_json=row["composition_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            extras_json=row["extras_json"],
            scope=scope,
        )


__all__ = [
    "RecipeNameTakenError",
    "RecipeStore",
]
