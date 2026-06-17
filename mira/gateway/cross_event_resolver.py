"""Cross-event DC resolution — the accessor seam (spec/81 Phase 2).

Phase 1's resolver (``core/collection_resolver.py``) is **scope-agnostic by
design**: every data accessor is injected as a callable, so cross-event
resolution is "build a different set of callables", not "rewrite the engine"
(spec/81 §2 + handover guidance). This module is the cross-event side of that
seam — it produces the four callables ``resolve()`` expects, reading from the
user-level ``mira.db`` (``global_items`` + ``saved_filter``) instead of one
event's lineage.

What changes vs the event-scope accessors on :class:`EventGateway`:

* **Origin universe widens.** Event scope honours only the base token
  ``"exported"``; cross-event accepts the full ladder
  ``collected / picked / edited / exported`` (spec/81 §2.1) — the user can
  reach what didn't finish, not just what did. The ladder rungs map to
  ``global_items`` columns the projection sync wrote:

  ============  ===========================================================
  rung          predicate (one row per ``(event_uuid, item_id)``)
  ============  ===========================================================
  collected     every row
  picked        ``pick_state = 'picked'``
  edited        ``edit_state = 'picked'`` (spec/61 §1.1 — edited ≠ exported)
  exported      ``has_export = 1``
  ============  ===========================================================

* **DC home moves.** Event-scope DCs live in event.db's ``dynamic_collection``;
  cross-event DCs live as ``saved_filter`` rows (spec/32 §4 + spec/81 §2.1 —
  same typed-ref shape; "predicate tree" reconciled to ``expr_json``).

* **Filter catalogue widens.** Event scope offers Style + media type;
  cross-event offers the full spec/32 §2 catalogue — EXIF hardware (lens,
  camera, flash), settings (focal length + exposure triangle), temporal,
  location, curatorial. Tolerant readers: every key is optional, malformed
  values fall back to "no constraint".

* **Key encoding changes.** Event scope keys are export relpaths;
  cross-event keys identify items across events. Encoded as
  ``"<event_uuid>::<item_id>"`` via :func:`pack_key` / :func:`unpack_key`
  — the resolver only needs hashable strings, so the encoding is
  module-local.

* **Cross-event Cuts** (``cut_members``) are deferred to Item 4. The accessor
  installed here returns the empty set for any ``"cut"`` operand — the
  resolver treats that as "deleted operand, contributes nothing" (the same
  graceful-shrink rule that lets event-scope tolerate a deleted Cut).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterable, List, Mapping, Optional, Set, Tuple

from core import collection_resolver
from mira.user_store import models as um
from mira.user_store.repo import UserStore

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Composite-key encoding — cross-event keys identify items library-wide
# --------------------------------------------------------------------------- #


#: The separator between ``event_uuid`` and ``item_id`` in a packed key.
#: Both halves are UUID hex (no colons), so ``::`` is safe; kept module-local
#: so callers don't depend on the exact byte sequence.
_KEY_SEP = "::"


def pack_key(event_uuid: str, item_id: str) -> str:
    """Encode a cross-event item reference as the resolver's hashable
    ``str`` key. Inverse of :func:`unpack_key`."""
    return f"{event_uuid}{_KEY_SEP}{item_id}"


def unpack_key(key: str) -> Tuple[str, str]:
    """Decode a packed key into ``(event_uuid, item_id)``. Round-trips with
    :func:`pack_key`; an unpackable string returns ``("", key)`` so the
    caller can decide whether to log + skip or raise."""
    if _KEY_SEP not in key:
        return ("", key)
    event_uuid, _, item_id = key.partition(_KEY_SEP)
    return (event_uuid, item_id)


# --------------------------------------------------------------------------- #
# Filter dispatch — the spec/32 §2 catalogue
# --------------------------------------------------------------------------- #


def _filter_clauses(filters: Mapping[str, Any]) -> Tuple[List[str], list]:
    """Translate a cross-event ``filters_json`` mapping into SQL ``WHERE``
    fragments + parameter list (spec/32 §2 catalogue). Tolerant: every key
    is optional; unknown keys ignored; malformed values fall back to
    "no constraint". Combinable lists narrow as ``IN (?, ?, …)``; numeric
    ranges narrow as ``column BETWEEN ? AND ?`` (either end optional).

    Catalogue (matches spec/32 §2 + the cross-event surface in spec/81 §2.1):

    * Curatorial: ``styles`` (list of classifications, combinable),
      ``media_type`` ('photo'|'video'|'both'), ``stars_min`` (int 1-5),
      ``color_labels`` (list), ``flag`` (bool).
    * EXIF / hardware: ``iso_min`` / ``iso_max``, ``aperture_min`` /
      ``aperture_max``, ``shutter_min`` / ``shutter_max``, ``focal_min`` /
      ``focal_max``, ``flash_fired`` (bool), ``lens_models`` (list),
      ``camera_ids`` (list).
    * Temporal: ``capture_from`` / ``capture_to`` (ISO strings, half-open).
    * Location: ``country_codes`` (list of ISO alpha-2), ``cities`` (list).
    """
    clauses: List[str] = []
    params: list = []

    # ---- curatorial (spec/32 §2a + event-scope parity) -------------------- #
    styles = _list_of_strings(filters.get("styles"))
    if styles:
        clauses.append(f"classification IN ({_qs(len(styles))})")
        params.extend(styles)
    media = filters.get("media_type")
    if media in ("photo", "video"):
        clauses.append("kind = ?")
        params.append(media)
    stars_min = _opt_int(filters.get("stars_min"))
    if stars_min is not None:
        clauses.append("stars >= ?")
        params.append(stars_min)
    color_labels = _list_of_strings(filters.get("color_labels"))
    if color_labels:
        clauses.append(f"color_label IN ({_qs(len(color_labels))})")
        params.extend(color_labels)
    flag = _opt_bool(filters.get("flag"))
    if flag is not None:
        clauses.append("flag = ?")
        params.append(1 if flag else 0)

    # ---- EXIF / hardware (spec/32 §2d) ------------------------------------ #
    for col, lo_key, hi_key in (
        ("iso", "iso_min", "iso_max"),
        ("aperture_f", "aperture_min", "aperture_max"),
        ("shutter_speed_s", "shutter_min", "shutter_max"),
        ("focal_length_mm", "focal_min", "focal_max"),
    ):
        lo = _opt_number(filters.get(lo_key))
        hi = _opt_number(filters.get(hi_key))
        if lo is not None:
            clauses.append(f"{col} >= ?")
            params.append(lo)
        if hi is not None:
            clauses.append(f"{col} <= ?")
            params.append(hi)
    flash = _opt_bool(filters.get("flash_fired"))
    if flash is not None:
        clauses.append("flash_fired = ?")
        params.append(1 if flash else 0)
    lens_models = _list_of_strings(filters.get("lens_models"))
    if lens_models:
        clauses.append(f"lens_model IN ({_qs(len(lens_models))})")
        params.extend(lens_models)
    camera_ids = _list_of_strings(filters.get("camera_ids"))
    if camera_ids:
        clauses.append(f"camera_id IN ({_qs(len(camera_ids))})")
        params.extend(camera_ids)

    # ---- temporal (spec/32 §2b) ------------------------------------------- #
    capture_from = _opt_str(filters.get("capture_from"))
    capture_to = _opt_str(filters.get("capture_to"))
    if capture_from is not None:
        clauses.append("capture_time >= ?")
        params.append(capture_from)
    if capture_to is not None:
        clauses.append("capture_time <= ?")
        params.append(capture_to)

    # ---- location (spec/32 §2c) ------------------------------------------- #
    country_codes = _list_of_strings(filters.get("country_codes"))
    if country_codes:
        clauses.append(f"country_code IN ({_qs(len(country_codes))})")
        params.extend(country_codes)
    cities = _list_of_strings(filters.get("cities"))
    if cities:
        clauses.append(f"day_city IN ({_qs(len(cities))})")
        params.extend(cities)

    return clauses, params


def _qs(n: int) -> str:
    return ",".join(["?"] * n)


def _list_of_strings(v: Any) -> List[str]:
    """Coerce a value into a list of non-empty strings; anything else → []."""
    if not isinstance(v, (list, tuple)):
        return []
    return [s for s in v if isinstance(s, str) and s]


def _opt_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):                                       # bools are ints
        return None
    if isinstance(v, int):
        return v
    return None


def _opt_number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _opt_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    return None


def _opt_str(v: Any) -> Optional[str]:
    if isinstance(v, str) and v:
        return v
    return None


# --------------------------------------------------------------------------- #
# The four resolver accessors — built around an open UserStore
# --------------------------------------------------------------------------- #


class CrossEventAccessors:
    """The four callables :func:`core.collection_resolver.resolve` expects,
    bound to an open :class:`UserStore`. Build one per resolution pass (cheap
    — no caching; the resolver memoises within the pass)."""

    def __init__(self, user_store: UserStore) -> None:
        self.user_store = user_store

    # --- base universe (the ladder rungs, spec/81 §2.1) -------------------- #

    def base_universe(self, token: str) -> Set[str]:
        """Member keys for one ladder rung (``"collected"`` / ``"picked"`` /
        ``"edited"`` / ``"exported"``). Unknown token → empty set (the
        resolver tolerates an unknown base operand the same way it tolerates
        a deleted DC ref — graceful shrink, not raise)."""
        sql = ("SELECT event_uuid, item_id FROM global_items")
        if token == collection_resolver.BASE_COLLECTED:
            pass                                                  # every row
        elif token == collection_resolver.BASE_PICKED:
            sql += " WHERE pick_state = 'picked'"
        elif token == collection_resolver.BASE_EDITED:
            sql += " WHERE edit_state = 'picked'"
        elif token == collection_resolver.BASE_EXPORTED:
            sql += " WHERE has_export = 1"
        else:
            return set()
        return {pack_key(r["event_uuid"], r["item_id"])
                for r in self.user_store.conn.execute(sql)}

    # --- DC operand (a saved_filter row) ----------------------------------- #

    def dc_by_ref(self, ref: Mapping[str, Any]) -> Optional[collection_resolver.DCExpr]:
        """Resolve a ``{"kind":"dc","id"|"tag":…}`` operand against
        ``saved_filter``. Looks up by id first, falls back to tag. Returns
        ``None`` if the row is gone (graceful shrink)."""
        sf: Optional[um.SavedFilter] = None
        ref_id = ref.get("id")
        if ref_id:
            sf = self.user_store.get(um.SavedFilter, ref_id)
        if sf is None and ref.get("tag"):
            rows = self.user_store.query_by(um.SavedFilter, tag=ref["tag"])
            sf = rows[0] if rows else None
        if sf is None:
            return None
        try:
            expr = json.loads(sf.expr_json or "[]")
        except (ValueError, TypeError):
            expr = []
        try:
            filters = json.loads(sf.filters_json or "{}")
            if not isinstance(filters, dict):
                filters = {}
        except (ValueError, TypeError):
            filters = {}
        return collection_resolver.DCExpr(id=sf.id, expr=expr, filters=filters)

    # --- Cut operand (deferred to Item 4) ---------------------------------- #

    def cut_members(self, ref: Mapping[str, Any]) -> Set[str]:
        """Frozen members of a cross-event Cut — deferred to Item 4 (Pin
        across events). Returns the empty set for now; the resolver treats
        an empty operand as "deleted, contributes nothing", which is the
        correct behaviour while cross-event Cuts don't exist yet."""
        return set()

    # --- filter narrow + chronological order ------------------------------- #

    def apply_filters(self, keys: Iterable[str],
                      filters: Mapping[str, Any]) -> List[str]:
        """Narrow a member-key set against ``filters_json`` (spec/32 §2
        catalogue via :func:`_filter_clauses`) and order chronologically by
        ``capture_time``. Returns packed keys in show order. Empty input →
        empty output."""
        keys = list(keys)
        if not keys:
            return []
        # Match keys via a literal "event_uuid::item_id" comparison so the
        # query never grows past the global_items rows that survived the
        # algebra. Sort by capture_time then item_id (tie-break) so two
        # frames sharing a timestamp pick a deterministic winner.
        placeholders = _qs(len(keys))
        sql = (
            "SELECT event_uuid, item_id, capture_time FROM global_items "
            f"WHERE (event_uuid || '{_KEY_SEP}' || item_id) IN ({placeholders})"
        )
        params: list = list(keys)
        extra, extra_params = _filter_clauses(filters)
        if extra:
            sql += " AND " + " AND ".join(extra)
            params.extend(extra_params)
        sql += " ORDER BY COALESCE(capture_time, ''), item_id"
        rows = self.user_store.conn.execute(sql, params).fetchall()
        return [pack_key(r["event_uuid"], r["item_id"]) for r in rows]


# --------------------------------------------------------------------------- #
# Convenience entry — drive the resolver end-to-end
# --------------------------------------------------------------------------- #


def resolve_cross_event(
    user_store: UserStore,
    expr: Iterable[Iterable[Any]],
    filters: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Run the resolver against ``mira.db``. Returns packed keys
    (``event_uuid::item_id``) in chronological show order.

    Convenience over :class:`CrossEventAccessors`: builds the accessors,
    converts ``expr`` to a list of lists, and dispatches to
    :func:`core.collection_resolver.resolve`. Cycle-safe + memoised within
    the pass (the resolver does both)."""
    acc = CrossEventAccessors(user_store)
    return collection_resolver.resolve(
        [list(t) for t in expr],
        dict(filters or {}),
        base_universe=acc.base_universe,
        dc_by_ref=acc.dc_by_ref,
        cut_members=acc.cut_members,
        apply_filters=acc.apply_filters,
    )


__all__ = [
    "CrossEventAccessors",
    "pack_key",
    "resolve_cross_event",
    "unpack_key",
]
