"""User-level data store (`mira.db`).

Per [spec/53](../../spec/53-user-data-store.md): one SQLite file at user level
(`%LOCALAPPDATA%\\Mira\\mira.db`) for cross-event state — settings,
the events index, Cut definitions, the people catalog, user hardware, and
feature flags. Replaces the loose `settings.rebuild.json` + `events_index.json`
pair on first launch (one-shot import).

The substrate is SQLite for the same reasons `event.db` is: ACID, schema
versioning, corruption resistance via WAL + sidecar + rolling backups. Mirrors
the per-event store's layering — `schema.py` (DDL + connect/initialize/migrate),
`models.py` (dataclasses, one per table), `repo.py` (UserStore: typed CRUD +
transactions).
"""
from __future__ import annotations
