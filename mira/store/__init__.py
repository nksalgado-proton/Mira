"""The event store — SQLite ``event.db`` per event (spec/03).

Substrate-hiding layer the gateway is built on. Public surface:

- :mod:`mira.store.schema`    — DDL, ``SCHEMA_VERSION``, migrations, connection setup.
- :mod:`mira.store.models`    — typed dataclasses, one per table + the ``EventDocument`` aggregate.
- :mod:`mira.store.json_dump` — ``EventDocument`` ⇄ ``event.json`` (backup / migration / fixture).
- :mod:`mira.store.repo`      — :class:`EventStore` (open/close, transactions, CRUD, query).
"""

from mira.store.repo import EventStore

__all__ = ["EventStore"]
