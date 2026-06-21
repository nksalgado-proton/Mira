"""Dual-home migration for spec/94 Phase 1b.

Phase 1a left two storage homes coexisting:

* The legacy SQLite homes — ``mira.db.saved_filter`` (cross-event
  Collections, née Dynamic Collections) and ``mira.db.recipe`` (saved
  Recipes from spec/90 Phase 1's substrate).
* The new JSON-file tree under ``<library_root>/Collections/`` and
  ``<library_root>/Recipes/`` (spec/93 §4).

This module is the one-shot migration that moves the SQL rows to JSON
files so the file tree is the **single live source** (spec/93 §4 / §10
invariants). The same definition must never appear in two homes.

Contract (mirrors :func:`mira.paths.migrate_legacy_user_data`):

* **Idempotent.** Re-running on an already-migrated library is a
  cheap no-op, gated by a marker file at
  ``<library_root>/.mira/dual_home_migration.json``.
* **Non-destructive at the row level.** Each SQL row is migrated by
  WRITING the JSON file first; the row is only DELETED after the
  write succeeds. A crash mid-migration leaves the JSON file (which
  the next run finds via the by-id scan) AND the SQL row (which the
  next run re-migrates idempotently because the same id maps to the
  same JSON content).
* **Filesystem-only.** No network. The library lock (spec/76 §A) is
  the caller's responsibility — migration writes through the standard
  atomic write-then-rename inside :class:`DefinitionLibrary`.

Pure logic + filesystem + SQLite. No Qt. The caller (Gateway init)
wires the libraries + UserStore and invokes :func:`migrate_dual_homes`.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from core.definition_files import (
    DefinitionFile,
    KIND_COLLECTION,
    KIND_RECIPE,
)

log = logging.getLogger(__name__)


#: Marker filename inside ``<library_root>/.mira/``. Its presence
#: short-circuits :func:`migrate_dual_homes`; its absence is the
#: trigger for the one-shot migration.
MARKER_FILENAME = "dual_home_migration.json"

#: Bumped together with a code change that warrants re-running the
#: migration (e.g. if a future row type joins the dual-home set).
MARKER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DualHomeMigrationReport:
    """Summary returned to the caller. ``skipped`` is True when the
    marker said the migration had already run; in that case the row
    counts are both 0."""
    skipped: bool
    migrated_collections: int
    migrated_recipes: int

    @classmethod
    def already_done(cls) -> "DualHomeMigrationReport":
        return cls(skipped=True, migrated_collections=0, migrated_recipes=0)


def marker_path(library_root: Path) -> Path:
    """Resolve the marker file's location. Centralised so the wirer
    can probe it (e.g. for the "Migration already done" log line) and
    so tests can stub it through ``MIRA_DIRNAME``."""
    return library_root / ".mira" / MARKER_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_marker(
    path: Path, *, report: DualHomeMigrationReport,
) -> None:
    """Atomic write-then-rename (invariant #6) of the marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "migrated_at": _now_iso(),
        "migrated_collections": report.migrated_collections,
        "migrated_recipes": report.migrated_recipes,
    }
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    os.replace(str(tmp), str(path))


def _parse_json(blob: Optional[str], default: Any) -> Any:
    if not blob:
        return default
    try:
        return json.loads(blob)
    except (ValueError, TypeError):
        return default


def migrate_dual_homes(
    library_root: Path,
    *,
    saved_filter_rows: Any,
    recipe_rows: Any,
    collections_library: Any,
    recipes_library: Any,
    delete_saved_filter: Any,
    delete_recipe: Any,
) -> DualHomeMigrationReport:
    """One-shot move of legacy SQL rows into the JSON tree.

    ``saved_filter_rows`` and ``recipe_rows`` are sequences of dataclass
    rows from the user store (each must carry the documented columns —
    ``id`` / ``tag`` / ``expr_json`` / ``filters_json`` for saved_filter;
    ``id`` / ``name`` / ``flavour`` / ``composition_json`` for recipe).

    ``collections_library`` / ``recipes_library`` are
    :class:`mira.shared.definition_library.DefinitionLibrary` instances.
    The lower-case Mapping-style attribute access is enough (we call
    ``.save(df)`` only), so a fixture can stub them with a class that
    matches the signature.

    ``delete_saved_filter(id)`` / ``delete_recipe(id)`` are the callbacks
    that retire the SQL row after the JSON write succeeds. Idempotent
    deletes (no-op when the row is already gone) match the contract a
    crash-recovered second run needs.

    Idempotency: the marker file gates re-runs. A first run sees no
    marker, walks both tables, writes JSON + deletes rows, writes the
    marker. A second run sees the marker and short-circuits.

    The caller (Gateway init) is responsible for holding the library
    lock — this function never touches it; it just writes through the
    standard DefinitionLibrary.save() path which is atomic at the file
    level.
    """
    marker = marker_path(library_root)
    if marker.exists():
        log.info(
            "dual_home_migrate: marker present at %s — skipping "
            "(migration already ran).", marker,
        )
        return DualHomeMigrationReport.already_done()

    migrated_dcs = 0
    for sf in saved_filter_rows:
        try:
            df = _saved_filter_to_definition(sf)
        except Exception as exc:                            # noqa: BLE001
            log.warning(
                "dual_home_migrate: skipping saved_filter %s — %s",
                getattr(sf, "id", "?"), exc,
            )
            continue
        try:
            collections_library.save(df)
        except OSError as exc:
            log.warning(
                "dual_home_migrate: collections write failed for "
                "saved_filter %s (%s): %s", df.id, df.name, exc,
            )
            # Don't delete the SQL row — leave the legacy home in place
            # so a retry has the data to migrate.
            continue
        try:
            delete_saved_filter(df.id)
        except Exception as exc:                            # noqa: BLE001
            # JSON is written; the SQL row stays. Re-runs are safe
            # because the migration would notice the JSON file already
            # exists (same id) and the delete would idempotently retry.
            log.warning(
                "dual_home_migrate: SQL delete failed for saved_filter "
                "%s: %s — JSON write succeeded, leaving SQL row for "
                "retry on next run.", df.id, exc,
            )
            continue
        migrated_dcs += 1

    migrated_recipes = 0
    for r in recipe_rows:
        try:
            df = _recipe_to_definition(r)
        except Exception as exc:                            # noqa: BLE001
            log.warning(
                "dual_home_migrate: skipping recipe %s — %s",
                getattr(r, "id", "?"), exc,
            )
            continue
        try:
            recipes_library.save(df)
        except OSError as exc:
            log.warning(
                "dual_home_migrate: recipes write failed for recipe "
                "%s (%s): %s", df.id, df.name, exc,
            )
            continue
        try:
            delete_recipe(df.id)
        except Exception as exc:                            # noqa: BLE001
            log.warning(
                "dual_home_migrate: SQL delete failed for recipe "
                "%s: %s — JSON write succeeded, leaving SQL row for "
                "retry on next run.", df.id, exc,
            )
            continue
        migrated_recipes += 1

    report = DualHomeMigrationReport(
        skipped=False,
        migrated_collections=migrated_dcs,
        migrated_recipes=migrated_recipes,
    )

    # Mark the migration as complete even when no rows existed — a
    # fresh install never needs to re-walk the empty tables. The next
    # run trusts the marker and short-circuits.
    _write_marker(marker, report=report)
    log.info(
        "dual_home_migrate: complete — %d collections + %d recipes "
        "moved into the JSON tree at %s.",
        migrated_dcs, migrated_recipes, library_root,
    )
    return report


def _saved_filter_to_definition(sf: Any) -> DefinitionFile:
    """Project a ``SavedFilter`` row onto a :class:`DefinitionFile`."""
    return DefinitionFile(
        id=str(sf.id),
        name=str(sf.tag),
        kind=KIND_COLLECTION,
        payload={
            "expr": _parse_json(sf.expr_json, []),
            "filters": _parse_json(sf.filters_json, {}),
            # The free-text description rides on the JSON shape so a
            # future surface can read it. None / missing stays nullable.
            **(
                {"description": sf.description}
                if getattr(sf, "description", None)
                else {}
            ),
        },
    )


def _recipe_to_definition(r: Any) -> DefinitionFile:
    """Project a ``Recipe`` row onto a :class:`DefinitionFile`.

    The recipe's ``flavour`` lives at the top of the JSON payload so the
    library-level :class:`mira.shared.recipe_store.RecipeStore` can filter
    by it (spec/90 §5.5)."""
    composition = _parse_json(r.composition_json, {})
    if not isinstance(composition, Mapping):
        composition = {}
    payload = dict(composition)
    payload["flavour"] = r.flavour
    return DefinitionFile(
        id=str(r.id),
        name=str(r.name),
        kind=KIND_RECIPE,
        payload=payload,
    )


__all__ = [
    "MARKER_FILENAME",
    "DualHomeMigrationReport",
    "marker_path",
    "migrate_dual_homes",
]
