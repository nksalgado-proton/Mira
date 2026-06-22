"""``UserStore`` — the SQLite repository for the user-level data store (spec/53).

The substrate-hiding API the gateway is built on: open/close, transactions,
generic typed CRUD, and the indexed :meth:`query_by` primitive. Row mapping is
generic and driven by the dataclass field names, which mirror the column names
one-for-one (spec/53 → :mod:`mira.user_store.models`). Booleans are
coerced back from the ``INTEGER 0/1`` columns on read; SQLite stores Python
``bool`` as ``0/1`` natively on write.

Mirrors :class:`mira.store.repo.EventStore` field-for-field — same
``_TableInfo`` registry pattern, same upsert/get/all/query_by/query_raw/delete
shape, same explicit BEGIN/COMMIT transaction semantics.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import fields
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Type, TypeVar, Union, get_type_hints

from mira.user_store import models as m
from mira.user_store import protection, schema

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


# Registry. Order is the deterministic load/save order; the user-store has no
# FK relationships between tables so the order is purely for readability.
_REGISTRY: List[_TableInfo] = [
    _TableInfo(m.InstallationProfile, "installation_profile", ("id",)),
    _TableInfo(m.Setting, "setting", ("key",)),
    _TableInfo(m.WizardAnswer, "wizard_answer", ("question_id",)),
    _TableInfo(m.EventIndex, "event_index", ("event_uuid",), ("event_uuid",)),
    _TableInfo(m.CutTemplate, "cut_template", ("id",), ("name",)),
    _TableInfo(m.GlobalItem, "global_items", ("event_uuid", "item_id"),
               ("event_uuid", "capture_time", "item_id")),
    _TableInfo(m.SavedFilter, "saved_filter", ("id",), ("created_at", "id")),
    _TableInfo(m.Recipe, "recipe", ("id",), ("flavour", "name", "id")),
    _TableInfo(m.EventCollection, "event_collection", ("id",), ("created_at", "id")),
    _TableInfo(m.Person, "person", ("id",), ("display_name",)),
    _TableInfo(m.UserCamera, "user_camera", ("camera_id",)),
    _TableInfo(m.GearProfile, "gear_profile", ("kind", "key")),
    _TableInfo(m.Cut, "cut", ("id",), ("created_at", "id")),
    _TableInfo(m.CutMember, "cut_member",
               ("cut_id", "event_id", "member_id"),
               ("cut_id", "added_at", "event_id", "member_id")),
    _TableInfo(m.FeatureFlag, "feature_flag", ("key",)),
]
_BY_CLS: Dict[type, _TableInfo] = {info.cls: info for info in _REGISTRY}


class UserStore:
    """Repository over the user-level ``mira.db``.

    The lifecycle methods (:meth:`create`, :meth:`open`, :meth:`close`) carry
    the spec/53 §3.1 protection contract:

    * **Open** — verify the SHA-256 sidecar (mismatch logs visibly, does not
      auto-restore); run ``PRAGMA integrity_check`` before any read (anything
      other than ``'ok'`` is surfaced).
    * **Close** — checkpoint the WAL so every committed transaction is in the
      main DB file, close the connection, recompute the sidecar, and rotate
      the rolling backups (``.bak.1`` becomes the freshest copy).
    """

    def __init__(self, conn: sqlite3.Connection, path: Optional[Path] = None):
        self.conn = conn
        self.path = Path(path) if path is not None else None

    # ----- lifecycle ----------------------------------------------------- #

    @classmethod
    def create(
        cls,
        path: Union[str, Path],
        *,
        app_version: str = "",
        created_at: Optional[str] = None,
    ) -> "UserStore":
        """Create a fresh ``mira.db`` at ``path`` and initialise the schema.

        No verification step (the file doesn't exist yet); the sidecar + first
        backup are written on the first clean :meth:`close`.
        """
        path = Path(path)
        conn = schema.connect(path)
        schema.initialize(conn, app_version=app_version, created_at=created_at)
        return cls(conn, path)

    @classmethod
    def open(cls, path: Union[str, Path]) -> "UserStore":
        """Open an existing ``mira.db``, applying any pending migrations.

        Protection order matches spec/53 §3.1: sidecar verify first (cheap, no
        connection needed), then connect, then ``integrity_check``. A failure
        in either layer logs visibly but does NOT prevent open — callers are
        responsible for the restore decision.
        """
        path = Path(path)

        verify = protection.verify_sidecar(path)
        if not verify.ok and not verify.sidecar_missing:
            log.warning(
                "mira.db sidecar mismatch (expected %s, got %s) — "
                "the file may have been edited outside Mira; opening anyway",
                verify.expected_sha256[:12], verify.actual_sha256[:12],
            )

        conn = schema.connect(path)
        if schema.get_version(conn) is None:
            conn.close()
            raise RuntimeError(f"{path} is not an initialised mira.db")

        integrity = schema.integrity_check(conn)
        if integrity != "ok":
            # spec/53 §3.1 + 2026-06-17 corruption incident: a malformed
            # mira.db otherwise crashes the app the moment a query hits a
            # bad page (e.g. opening a day to Pick → unhandled
            # ``sqlite3.DatabaseError: database disk image is malformed``).
            # Auto-restore from the newest rolling backup that itself
            # passes integrity_check, keeping the corrupt file aside for
            # forensics. Only fall back to opening the corrupt file when no
            # clean backup exists.
            log.error(
                "mira.db integrity_check FAILED (%r) — attempting "
                "auto-restore from the newest clean backup", integrity,
            )
            conn.close()
            restored = protection.restore_from_backup(path)
            conn = schema.connect(path)
            if restored is not None:
                post = schema.integrity_check(conn)
                if post == "ok":
                    log.warning(
                        "mira.db auto-restored from %s and now passes "
                        "integrity_check", restored.name)
                else:
                    log.error(
                        "mira.db still fails integrity_check (%r) after "
                        "restoring %s", post, restored.name)
            else:
                log.error(
                    "mira.db has no clean backup to restore — opening the "
                    "corrupt file; reads may fail")

        schema.migrate(conn)
        return cls(conn, path)

    def close(self) -> None:
        """Close cleanly: WAL checkpoint, close connection, recompute sidecar,
        rotate rolling backups. Errors in the protection layer log but do NOT
        propagate — close() is called from ``__exit__`` and finally blocks
        where a raise would mask the real exception."""
        try:
            self._checkpoint_truncate()
        except sqlite3.Error as exc:
            log.warning("WAL checkpoint failed on close: %s", exc)

        self.conn.close()

        if self.path is None or not self.path.is_file():
            return

        try:
            protection.recompute_sidecar(self.path)
        except OSError as exc:
            log.warning("sidecar recompute failed: %s (file is still valid)", exc)

        try:
            protection.roll_backup(self.path)
        except OSError as exc:
            log.warning("rolling backup failed: %s", exc)

    def _checkpoint_truncate(self, attempts: int = 3) -> None:
        """Checkpoint and TRUNCATE the WAL, retrying a BUSY result.

        ``wal_checkpoint(TRUNCATE)`` returns ``(busy, log_frames,
        checkpointed)``; ``busy != 0`` means a reader (e.g. an in-flight
        backup connection) blocked the truncation, leaving committed frames
        in the ``-wal``. Previously a BUSY result was silently accepted, so
        the WAL could grow without bound across sessions. Retry a few times;
        if it still can't truncate, log it. Data is never at risk — the
        rolling backup now reads committed state via the online backup API
        regardless of WAL state (see ``protection._backup_db``)."""
        import time
        for _ in range(max(1, attempts)):
            row = self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            busy = row[0] if row else 1
            if not busy:
                return
            time.sleep(0.05)
        log.warning(
            "WAL checkpoint(TRUNCATE) still BUSY after %d attempts; WAL left "
            "in place (data is safe; rolling backup unaffected)", attempts)

    def __enter__(self) -> "UserStore":
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
        """Upsert via ``INSERT … ON CONFLICT(pk) DO UPDATE`` — NOT ``INSERT OR REPLACE``
        (``feedback_never_insert_or_replace_with_fks``). The user-store has no
        FK relationships today, but the discipline is the same as the per-event
        store so future inter-table FKs don't break the existing call sites."""
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
        """Filtered query using SQL ``WHERE`` — leverages the table's indexes
        instead of full-table scans. Each keyword is a column name
        equality-matched against its value; rows come back in the table's
        deterministic ``order_by``. With no filters it degenerates to
        :meth:`all`."""
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
        """Map an arbitrary ``SELECT`` to dataclasses — the escape hatch for
        gateway hot paths that need JOINs or aggregates. ``sql`` MUST project
        every column of ``cls`` (use ``SELECT <table>.* FROM <table> JOIN …`` so
        the column names come back bare)."""
        info = _BY_CLS[cls]
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_obj(r, info) for r in rows]

    def delete(self, cls: Type[T], *pk) -> None:
        """Delete one row by primary key."""
        info = _BY_CLS[cls]
        where = " AND ".join(f"{c} = ?" for c in info.pk)
        with self.transaction() as conn:
            conn.execute(f"DELETE FROM {info.table} WHERE {where}", pk)
