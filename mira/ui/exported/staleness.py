"""Edited-since-export predicate (spec/118 §2).

An exported item is **stale** when its on-disk Mira render's recorded
``lineage.recipe_json`` no longer matches what the live
:class:`~mira.store.models.Adjustment` row would emit through
:func:`mira.ui.exported.batch.recipe_for_item`. Editing an exported item
NEVER auto-exports (spec/118 §1) — instead the on-disk JPEG goes stale,
and surfaces light the loud "edited since export" cue until the user
runs an export.

Lifted out of :class:`DaysGridPage._is_preview_item_stale` so the same
truth feeds:

* the Export grid cell (per cell) — :func:`is_cell_stale`,
* the versions-cluster cover (any member stale → cover stale) —
  :func:`is_cluster_cover_stale`,
* the preview dialog "Adjustments changed" chip (existing caller),
* the Edit-view exported badge (so the Editor doesn't show a clean
  "exported" state for an item whose recipe has since diverged).

Third-party returns never read stale: their file IS the recipe, and
:attr:`lineage.recipe_json` is ``NULL`` on a ``third_party`` row.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def _resolve_source_id(item_id: str) -> str:
    """Strip the ``mira:`` virtual-member prefix used by the versions
    cluster's sub-grid (spec/89 §11.1). Lineage rows already carry the
    source item id directly, so any other id passes through."""
    if isinstance(item_id, str) and item_id.startswith("mira:"):
        return item_id.split(":", 1)[1]
    return item_id


def _recipe_diverged(
    eg, source_item_id: str, shipped_recipe_json: Optional[str],
) -> bool:
    """Compare the live recipe with the shipped snapshot. ``shipped_recipe
    _json`` is the lineage row's stored JSON (``None`` / empty on a
    third-party return). The shipped snapshot may carry extra fields
    (``resolved_params``, ``tone_scaling``) that the live recipe never
    emits — strip them before comparing so a re-export under the same
    user choices stays clean."""
    from mira.ui.exported.batch import recipe_for_item

    try:
        shipped = (
            json.loads(shipped_recipe_json) if shipped_recipe_json
            else {})
    except Exception:                                              # noqa: BLE001
        log.exception(
            "edit-staleness: shipped recipe_json parse failed for %s",
            source_item_id)
        return False
    if isinstance(shipped, dict):
        shipped = {
            k: v for k, v in shipped.items()
            if k not in ("resolved_params", "tone_scaling")
        }
    try:
        current = recipe_for_item(eg, source_item_id)
    except Exception:                                              # noqa: BLE001
        log.exception(
            "edit-staleness: recipe_for_item(%s) failed", source_item_id)
        return False
    return current != shipped


def is_lineage_row_stale(eg, row) -> bool:
    """True when ``row`` is a Mira render whose stored recipe no longer
    matches the live adjustment for its source item. False for third-
    party returns (no recipe to diff against). ``row`` is a
    :class:`~mira.store.models.Lineage` or a ``sqlite3.Row``-shaped
    record carrying ``provenance``, ``source_item_id``, ``recipe_json``.
    """
    if row is None:
        return False
    provenance = (
        (row["provenance"] if not hasattr(row, "provenance")
         else getattr(row, "provenance", ""))
        or "")
    if provenance != "mira_render":
        return False
    source_item_id = (
        row["source_item_id"] if not hasattr(row, "source_item_id")
        else getattr(row, "source_item_id", None))
    if not source_item_id:
        return False
    recipe_json = (
        row["recipe_json"] if not hasattr(row, "recipe_json")
        else getattr(row, "recipe_json", None))
    return _recipe_diverged(eg, source_item_id, recipe_json)


def is_cell_stale(eg, item_id: Any) -> bool:
    """True when the Export-grid cell keyed by ``item_id`` reads stale.

    Three cell shapes ride this helper:

    * **Versions sub-grid member** — ``item_id`` is an ``Exported Media/``
      relpath; look up that one lineage row and diff. Third-party rows
      return False.
    * **Virtual Mira cluster member** — ``item_id`` starts with ``mira:``;
      no on-disk Mira render yet (by definition the virtual member is
      the live intent), so always False.
    * **Day-grid flat cell** — ``item_id`` is the source item id;
      compare against the newest Mira-render version of the source
      item. Items with no Mira render at all (third-party only, or no
      shipped file) read False.
    """
    if eg is None or getattr(eg, "event_root", None) is None:
        return False
    if not isinstance(item_id, str) or not item_id:
        return False
    # Virtual Mira member — the cluster's "I edited this in Mira" row.
    # There is no on-disk Mira render yet, so the staleness comparison
    # has nothing to bite on.
    if item_id.startswith("mira:"):
        return False
    if item_id.startswith("Exported Media/"):
        try:
            row = eg.store.conn.execute(
                "SELECT * FROM lineage WHERE export_relpath = ?",
                (item_id,)).fetchone()
        except Exception:                                          # noqa: BLE001
            log.exception(
                "edit-staleness: row lookup failed for %s", item_id)
            return False
        return is_lineage_row_stale(eg, row)
    source_id = _resolve_source_id(item_id)
    try:
        versions = eg.versions_for_item(source_id)
    except Exception:                                              # noqa: BLE001
        log.exception(
            "edit-staleness: versions_for_item(%s) failed", source_id)
        return False
    mira_rows = [
        v for v in versions
        if (getattr(v, "provenance", "") or "") == "mira_render"
    ]
    if not mira_rows:
        return False
    return is_lineage_row_stale(eg, mira_rows[0])


def is_cluster_cover_stale(eg, source_item_id: str) -> bool:
    """True when ANY Mira-render version of ``source_item_id`` is stale.

    Cluster covers read stale if at least one of their members would
    paint stale — so the user sees the loud cue at the day grid even
    before drilling into the cluster.
    """
    if eg is None or not source_item_id:
        return False
    try:
        versions = eg.versions_for_item(source_item_id)
    except Exception:                                              # noqa: BLE001
        log.exception(
            "edit-staleness: versions_for_item(%s) failed",
            source_item_id)
        return False
    for row in versions:
        if (getattr(row, "provenance", "") or "") != "mira_render":
            continue
        if is_lineage_row_stale(eg, row):
            return True
    return False


__all__ = [
    "is_cell_stale",
    "is_cluster_cover_stale",
    "is_lineage_row_stale",
]
