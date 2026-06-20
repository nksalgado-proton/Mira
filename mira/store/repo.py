"""``EventStore`` — the SQLite repository (spec/30; charter §4 step G1).

The substrate-hiding API the gateway is built on: open/close, transactions, generic
typed CRUD, the indexed :meth:`query_by` primitive, whole-document load/save, and the
phase-progress query (completion is a *query* over ``phase_state``, never a stored cache).

Row mapping is generic and driven by the dataclass field names, which mirror the column
names one-for-one (spec/30 → :mod:`mira.store.models`). Booleans are coerced back
from the ``INTEGER 0/1`` columns on read; SQLite stores Python ``bool`` as ``0/1``
natively on write.

The derived cache tables (``bucket_cache`` / ``bucket_member`` / ``clustering``,
:data:`~mira.store.schema.CACHE_TABLES`) are registered for generic CRUD but are
**excluded** from :meth:`save_document` / :meth:`load_document` — they are regenerable
and never enter the backup.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import fields
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Type, TypeVar, Union, get_type_hints

from mira.store import models as m
from mira.store import schema

log = logging.getLogger(__name__)

T = TypeVar("T")


class _TableInfo:
    """Per-dataclass mapping: table name, primary-key columns, load order."""

    __slots__ = ("cls", "table", "pk", "order_by", "columns", "bool_fields")

    def __init__(self, cls, table: str, pk: tuple, order_by: Optional[tuple] = None):
        self.cls = cls
        self.table = table
        self.pk = pk
        self.order_by = order_by or pk
        self.columns = tuple(f.name for f in fields(cls))
        hints = get_type_hints(cls)
        self.bool_fields = frozenset(n for n in self.columns if hints.get(n) is bool)


# Registry. The durable tables come first (their order is the save_document write
# order; FK checks are deferred to commit so any order is safe), then the derived
# cache tables. ``_DOC_*`` below select which take part in the whole-document I/O.
_REGISTRY: List[_TableInfo] = [
    _TableInfo(m.Event, "event", ("id",)),
    _TableInfo(m.TripDay, "trip_day", ("day_number",)),
    _TableInfo(m.Camera, "camera", ("camera_id",)),
    _TableInfo(m.CameraCalibrationPair, "camera_calibration_pair", ("id",)),
    _TableInfo(m.CameraDayTz, "camera_day_tz", ("camera_id", "day_number")),
    _TableInfo(m.Item, "item", ("id",)),
    _TableInfo(m.PhaseState, "phase_state", ("item_id", "phase")),
    _TableInfo(m.VideoMarker, "video_marker", ("id",), ("video_item_id", "at_ms")),
    _TableInfo(m.VideoSegment, "video_segment", ("item_id",), ("video_item_id", "seg_index")),
    _TableInfo(m.VideoSnapshot, "video_snapshot", ("item_id",), ("video_item_id", "at_ms")),
    _TableInfo(m.Adjustment, "adjustment", ("item_id",)),
    _TableInfo(m.VideoAdjustment, "video_adjustment", ("item_id",)),
    _TableInfo(m.StackBracket, "stack_bracket", ("bracket_id",)),
    _TableInfo(m.StackMember, "stack_member", ("bracket_id", "item_id"), ("bracket_id", "ordinal")),
    _TableInfo(m.DynamicCollection, "dynamic_collection", ("id",), ("created_at", "id")),
    _TableInfo(m.Cut, "cut", ("id",), ("created_at", "id")),
    _TableInfo(m.CutMember, "cut_member", ("cut_id", "member_id")),
    _TableInfo(m.PhotoPerson, "photo_person", ("item_id", "person_id")),
    _TableInfo(m.Face, "face", ("id",), ("item_id", "id")),
    _TableInfo(m.Bucket, "bucket", ("bucket_key", "phase")),
    _TableInfo(m.ItemVisit, "item_visit", ("item_id", "phase")),
    _TableInfo(m.Lineage, "lineage", ("export_relpath",)),
    # Derived cache (spec/30 §3.18) — generic CRUD only, NOT in save/load_document.
    _TableInfo(m.BucketCache, "bucket_cache", ("bucket_key", "phase"), ("phase", "day_number", "ordinal")),
    _TableInfo(m.BucketMember, "bucket_member", ("bucket_key", "phase", "item_id"), ("bucket_key", "ordinal")),
    _TableInfo(m.Clustering, "clustering", ("phase", "day_number")),
]
_BY_CLS: Dict[type, _TableInfo] = {info.cls: info for info in _REGISTRY}

# The durable classes that make up a whole-event document, in FK-friendly write order
# (parents before children; FK checks are deferred to commit so this is for readability).
_DOC_CLASSES: tuple = (
    m.Event, m.TripDay, m.Camera, m.Item, m.CameraCalibrationPair,
    m.CameraDayTz,    # spec/45 Slice TZ-3 — fk to camera + trip_day, both already written above
    m.PhaseState, m.VideoMarker, m.VideoSegment, m.VideoSnapshot,
    m.Adjustment, m.VideoAdjustment,
    m.StackBracket, m.StackMember, m.PhotoPerson,
    m.Face,                # spec/90 §5.2 — references item; cascade-deletes with it
    m.Bucket, m.ItemVisit, m.Lineage,
    m.DynamicCollection,  # before Cut — cut.source_dc_id FKs it (deferred anyway)
    m.Cut, m.CutMember,   # after Lineage — cut_member FKs lineage (deferred anyway)
)


class EventStore:
    """Repository over one event's ``event.db``."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ----- lifecycle ----------------------------------------------------- #

    @classmethod
    def create(
        cls,
        path: Union[str, Path],
        *,
        event_id: str,
        app_version: str = "",
        created_at: Optional[str] = None,
    ) -> "EventStore":
        """Create a fresh ``event.db`` at ``path`` and initialise the schema."""
        conn = schema.connect(path)
        schema.initialize(conn, event_id=event_id, app_version=app_version, created_at=created_at)
        return cls(conn)

    @classmethod
    def open(cls, path: Union[str, Path]) -> "EventStore":
        """Open an existing ``event.db``, applying any pending migrations."""
        conn = schema.connect(path)
        if schema.get_version(conn) is None:
            conn.close()
            raise RuntimeError(f"{path} is not an initialised event.db")
        schema.migrate(conn)
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ----- transactions -------------------------------------------------- #

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit BEGIN/COMMIT with ROLLBACK on error."""
        conn = self.conn
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ----- generic typed CRUD -------------------------------------------- #

    def _row_to_obj(self, row: sqlite3.Row, info: _TableInfo):
        kwargs = {}
        for col in info.columns:
            val = row[col]
            if col in info.bool_fields and val is not None:
                val = bool(val)
            kwargs[col] = val
        return info.cls(**kwargs)

    def upsert(self, obj) -> None:
        """Insert-or-update a single dataclass row (update-in-place on PK conflict)."""
        info = _BY_CLS[type(obj)]
        self._upsert(self.conn, info, obj)

    @staticmethod
    def _upsert(conn: sqlite3.Connection, info: _TableInfo, obj) -> None:
        """Upsert via ``INSERT … ON CONFLICT(pk) DO UPDATE`` — NOT ``INSERT OR REPLACE``.

        ``INSERT OR REPLACE`` deletes the conflicting row and re-inserts, which in this
        schema fires the children's referential actions (``item`` has real FKs:
        ``camera_id … ON DELETE RESTRICT``, ``day_number … ON DELETE SET NULL``, and many
        child tables ``ON DELETE CASCADE``). So replacing a parent row would either throw
        (RESTRICT) or silently null/cascade-delete its children. ``ON CONFLICT DO UPDATE``
        updates the row in place — the existing row is never deleted, so no action fires.
        For all-PK tables (nothing to update) it degrades to ``DO NOTHING``."""
        cols = info.columns
        placeholders = ", ".join("?" for _ in cols)
        values = [getattr(obj, c) for c in cols]
        non_pk = [c for c in cols if c not in info.pk]
        conflict = ", ".join(info.pk)
        if non_pk:
            assignments = ", ".join(f"{c} = excluded.{c}" for c in non_pk)
            action = f"DO UPDATE SET {assignments}"
        else:
            action = "DO NOTHING"
        conn.execute(
            f"INSERT INTO {info.table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict}) {action}",
            values,
        )

    def get(self, cls: Type[T], *pk) -> Optional[T]:
        """Fetch one row by primary key, or ``None``."""
        info = _BY_CLS[cls]
        where = " AND ".join(f"{c} = ?" for c in info.pk)
        row = self.conn.execute(
            f"SELECT * FROM {info.table} WHERE {where}", pk
        ).fetchone()
        return self._row_to_obj(row, info) if row else None

    def all(self, cls: Type[T]) -> List[T]:
        """Fetch every row of a table, ordered for deterministic round-trips."""
        info = _BY_CLS[cls]
        order = ", ".join(info.order_by)
        rows = self.conn.execute(f"SELECT * FROM {info.table} ORDER BY {order}").fetchall()
        return [self._row_to_obj(r, info) for r in rows]

    def query_by(self, cls: Type[T], **filters) -> List[T]:
        """Filtered query using SQL ``WHERE`` instead of Python list-comprehension
        filtering — leverages the table's indexes instead of full-table scans.

        Each keyword is a column name equality-matched against its value; rows come
        back in the table's deterministic ``order_by``. The relational-first primitive
        the gateway's per-key lookups bind to (spec/30 §6). With no filters it
        degenerates to :meth:`all`."""
        info = _BY_CLS[cls]
        order = ", ".join(info.order_by)
        sql = f"SELECT * FROM {info.table}"
        if filters:
            where = " AND ".join(f"{k} = ?" for k in filters)
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order}"
        rows = self.conn.execute(sql, tuple(filters.values())).fetchall()
        return [self._row_to_obj(r, info) for r in rows]

    def query_raw(self, cls: Type[T], sql: str, params: tuple = ()) -> List[T]:
        """Map an arbitrary ``SELECT`` to dataclasses — the escape hatch for the
        gateway's JOIN-bearing hot paths (e.g. ``items(phase=...)``). ``sql`` MUST
        project every column of ``cls`` (use ``SELECT <table>.* FROM <table> JOIN …``
        so the column names come back bare and map one-for-one). The gateway owns the
        SQL; this just hydrates rows with the same bool coercion as the typed CRUD."""
        info = _BY_CLS[cls]
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_obj(r, info) for r in rows]

    def delete(self, cls: Type[T], *pk) -> None:
        """Delete one row by primary key."""
        info = _BY_CLS[cls]
        where = " AND ".join(f"{c} = ?" for c in info.pk)
        with self.transaction() as conn:
            conn.execute(f"DELETE FROM {info.table} WHERE {where}", pk)

    # ----- whole-document load / save ------------------------------------ #

    def save_document(self, doc: m.EventDocument) -> None:
        """Write a whole :class:`EventDocument` into the store (insert-or-replace).

        FK checks are deferred to commit so the flat lists can be inserted in any
        order (handles ``item.parent_item_id`` self-references and cross-table refs).
        Shared write path for restore and migration (charter §4 steps 2–5). The
        derived cache tables are **not** written here (regenerated from a re-scan)."""
        # Map each doc class to its list/scalar on the document.
        lists = {
            m.Event: [doc.event],
            m.TripDay: doc.trip_days,
            m.Camera: doc.cameras,
            m.CameraCalibrationPair: doc.camera_calibration_pairs,
            m.CameraDayTz: doc.camera_day_tz,
            m.Item: doc.items,
            m.PhaseState: doc.phase_states,
            m.VideoMarker: doc.video_markers,
            m.VideoSegment: doc.video_segments,
            m.VideoSnapshot: doc.video_snapshots,
            m.Adjustment: doc.adjustments,
            m.VideoAdjustment: doc.video_adjustments,
            m.StackBracket: doc.stacks,
            m.StackMember: doc.stack_members,
            m.DynamicCollection: doc.dynamic_collections,
            m.Cut: doc.cuts,
            m.CutMember: doc.cut_members,
            m.PhotoPerson: doc.photo_persons,
            m.Face: doc.faces,
            m.Bucket: doc.buckets,
            m.ItemVisit: doc.item_visits,
            m.Lineage: doc.lineage,
        }
        with self.transaction() as conn:
            conn.execute("PRAGMA defer_foreign_keys = ON")
            for cls in _DOC_CLASSES:
                info = _BY_CLS[cls]
                for obj in lists[cls]:
                    self._upsert(conn, info, obj)

    def load_document(self) -> m.EventDocument:
        """Read the whole store back into a flat :class:`EventDocument`."""
        events = self.all(m.Event)
        if not events:
            raise RuntimeError("event.db has no event row")
        return m.EventDocument(
            event=events[0],
            trip_days=self.all(m.TripDay),
            cameras=self.all(m.Camera),
            camera_calibration_pairs=self.all(m.CameraCalibrationPair),
            camera_day_tz=self.all(m.CameraDayTz),
            items=self.all(m.Item),
            phase_states=self.all(m.PhaseState),
            video_markers=self.all(m.VideoMarker),
            video_segments=self.all(m.VideoSegment),
            video_snapshots=self.all(m.VideoSnapshot),
            adjustments=self.all(m.Adjustment),
            video_adjustments=self.all(m.VideoAdjustment),
            stacks=self.all(m.StackBracket),
            stack_members=self.all(m.StackMember),
            dynamic_collections=self.all(m.DynamicCollection),
            cuts=self.all(m.Cut),
            cut_members=self.all(m.CutMember),
            photo_persons=self.all(m.PhotoPerson),
            faces=self.all(m.Face),
            buckets=self.all(m.Bucket),
            item_visits=self.all(m.ItemVisit),
            lineage=self.all(m.Lineage),
        )

    # ----- queries ------------------------------------------------------- #

    def phase_counts(self, phase: str) -> Dict[str, int]:
        """State histogram for a phase — the basis of phase progress (a query)."""
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS n FROM phase_state WHERE phase = ? GROUP BY state",
            (phase,),
        ).fetchall()
        return {r["state"]: r["n"] for r in rows}
