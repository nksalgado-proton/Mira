"""``EventCollectionStore`` — CRUD over the library-level ``event_collection``
table (spec/90 §5.3 + §7 Phase 4b).

An Event Collection is the cross-event analogue of a Dynamic Collection at
the **event** level: a saved set of events the user composes via the same
set-algebra grammar (spec/81 §2 / spec/90 §3.2) — `#adventure_events`,
`#wildlife_trips`, `#2018_2020_travel`. It appears as a chip in the
**Scope** sentence of the Collection-face Recipe dialog (spec/90 §1.1).

The store owns:

* CRUD primitives — :meth:`create`, :meth:`update`, :meth:`delete`,
  :meth:`get`, :meth:`by_tag`, :meth:`list`.
* JSON parse / serialise. Callers pass / receive Python dicts for the
  ``expr`` + ``filters`` blobs; the JSON encoding lives inside the store
  (matches :class:`mira.shared.recipe_store.RecipeStore`'s discipline).
* Tag uniqueness guard. The SQL ``tag COLLATE NOCASE UNIQUE`` surfaces
  as a typed :class:`EventCollectionTagTakenError` rather than a raw
  ``sqlite3.IntegrityError``.

Sibling of :class:`mira.shared.recipe_store.RecipeStore` — same shape, same
contract, different table. Lifecycle is the wirer's job (wraps an open
:class:`UserStore`).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, List, Mapping, Optional, Sequence

from mira.user_store import models as um
from mira.user_store.repo import UserStore

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_uuid() -> str:
    return uuid.uuid4().hex


class EventCollectionTagTakenError(ValueError):
    """A new / renamed Event Collection collides with an existing tag.

    The DDL CHECK (``tag COLLATE NOCASE UNIQUE``) is case-blind; this
    error fires on both ``#wildlife`` ↔ ``#wildlife`` and ``#WILDLIFE``
    collisions so the dialog can surface a single uniform message."""

    def __init__(self, tag: str) -> None:
        super().__init__(f"event collection tag {tag!r} already exists")
        self.tag = tag


class EventCollectionStore:
    """CRUD service over the ``event_collection`` table (spec/90 §5.3).

    Wraps an open :class:`UserStore`; lifecycle is owned by the wirer.
    """

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

    # ----- helpers --------------------------------------------------------- #

    @staticmethod
    def expr(ec: um.EventCollection) -> list:
        """Decode an Event Collection's ``expr_json`` to a list. Tolerant:
        a malformed blob reads as ``[]`` (matches the spec/81 / spec/90
        resolver posture)."""
        try:
            data = json.loads(ec.expr_json or "[]")
            return list(data) if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    @staticmethod
    def filters(ec: um.EventCollection) -> dict:
        """Decode an Event Collection's ``filters_json`` to a dict.
        Tolerant — malformed blob reads as ``{}``."""
        try:
            data = json.loads(ec.filters_json or "{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _check_unique(
        self, tag: str, *, excluding_id: Optional[str] = None,
    ) -> None:
        """Pre-check the COLLATE NOCASE UNIQUE constraint so the typed
        error fires before SQL. Excludes ``excluding_id`` so a same-tag
        rename on the same row passes."""
        row = self.user_store.conn.execute(
            "SELECT id FROM event_collection WHERE tag = ? COLLATE NOCASE "
            "LIMIT 1",
            (tag,),
        ).fetchone()
        if row is not None and row["id"] != excluding_id:
            raise EventCollectionTagTakenError(tag)

    @staticmethod
    def _normalise_tag(tag: str) -> str:
        """Strip + non-empty check. The DDL CHECK is ``tag <> ''`` —
        catching the empty case here gives a friendlier error than the
        raw IntegrityError."""
        if not isinstance(tag, str):
            raise ValueError("event collection tag must be a string")
        stripped = tag.strip()
        if not stripped:
            raise ValueError("event collection tag must be non-empty")
        return stripped

    # ----- CRUD ------------------------------------------------------------ #

    def create(
        self,
        tag: str,
        expr: Sequence[Sequence[Any]],
        filters: Optional[Mapping[str, Any]] = None,
    ) -> um.EventCollection:
        """Create a new Event Collection row. ``expr`` + ``filters`` are
        serialised to JSON internally. Raises
        :class:`EventCollectionTagTakenError` on a case-blind tag collision;
        raises ``ValueError`` for empty tag."""
        normalised = self._normalise_tag(tag)
        self._check_unique(normalised)

        now = self._now()
        ec = um.EventCollection(
            id=self._new_id(),
            tag=normalised,
            expr_json=json.dumps([list(t) for t in (expr or [])]),
            filters_json=json.dumps(dict(filters or {})),
            created_at=now,
            updated_at=now,
        )
        try:
            with self.user_store.transaction():
                self.user_store.upsert(ec)
        except sqlite3.IntegrityError as exc:
            # Race fallback — another writer landed a colliding row between
            # :meth:`_check_unique` and the COMMIT.
            raise EventCollectionTagTakenError(normalised) from exc
        return ec

    def update(
        self,
        id: str,
        *,
        tag: Optional[str] = None,
        expr: Optional[Sequence[Sequence[Any]]] = None,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> um.EventCollection:
        """Partial update — rename and / or replace the expr / filters
        blobs. Touches ``updated_at``; leaves ``created_at`` alone.

        Returns the updated row. Raises :class:`KeyError` if ``id`` is gone;
        raises :class:`EventCollectionTagTakenError` on a rename collision."""
        ec = self.get(id)
        if ec is None:
            raise KeyError(id)

        sets: dict[str, Any] = {}
        if tag is not None:
            normalised = self._normalise_tag(tag)
            if normalised.lower() != ec.tag.lower():
                self._check_unique(normalised, excluding_id=id)
            if normalised != ec.tag:
                sets["tag"] = normalised
        if expr is not None:
            sets["expr_json"] = json.dumps([list(t) for t in expr])
        if filters is not None:
            sets["filters_json"] = json.dumps(dict(filters))
        if not sets:
            return ec

        now = self._now()
        sets["updated_at"] = now
        cols = ", ".join(f"{k} = ?" for k in sets)
        params = (*sets.values(), id)
        try:
            with self.user_store.transaction() as conn:
                conn.execute(
                    f"UPDATE event_collection SET {cols} WHERE id = ?",
                    params)
        except sqlite3.IntegrityError as exc:
            raise EventCollectionTagTakenError(
                sets.get("tag", ec.tag)) from exc
        refreshed = self.get(id)
        assert refreshed is not None
        return refreshed

    def delete(self, id: str) -> None:
        """Drop an Event Collection by id. No-ops when the row is gone."""
        with self.user_store.transaction() as conn:
            conn.execute("DELETE FROM event_collection WHERE id = ?", (id,))

    # ----- queries --------------------------------------------------------- #

    def get(self, id: str) -> Optional[um.EventCollection]:
        return self.user_store.get(um.EventCollection, id)

    def by_tag(self, tag: str) -> Optional[um.EventCollection]:
        """Look up an Event Collection by its (case-blind) tag.

        The DDL UNIQUE is ``COLLATE NOCASE`` — case differences collapse,
        and so does this lookup. Returns ``None`` when no row matches."""
        if not isinstance(tag, str) or not tag:
            return None
        row = self.user_store.conn.execute(
            "SELECT * FROM event_collection WHERE tag = ? COLLATE NOCASE",
            (tag,),
        ).fetchone()
        return self._row_to_ec(row) if row else None

    def list(self) -> List[um.EventCollection]:
        """All Event Collections, ordered by tag (alphabetical) — the
        order the Scope picker renders them in (spec/90 §3.4)."""
        rows = self.user_store.conn.execute(
            "SELECT * FROM event_collection ORDER BY tag COLLATE NOCASE, id"
        ).fetchall()
        return [self._row_to_ec(r) for r in rows]

    # ----- internal -------------------------------------------------------- #

    @staticmethod
    def _row_to_ec(row: sqlite3.Row) -> um.EventCollection:
        return um.EventCollection(
            id=row["id"],
            tag=row["tag"],
            expr_json=row["expr_json"],
            filters_json=row["filters_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            extras_json=row["extras_json"],
        )


__all__ = [
    "EventCollectionStore",
    "EventCollectionTagTakenError",
]
