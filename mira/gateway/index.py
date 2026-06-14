"""``EventsIndex`` — the cross-event thin pointer (spec/08 §3.1, spec/03 §5).

``<user_data_dir>/events_index.json``: one row per event so the events list renders
without opening any ``event.db``. Paths are anchored to the single ``photos_base_path``
(charter §5.9): ``event_relpath`` (relative to the base) is the normal case;
``event_root_abs`` is the cross-volume fallback. :func:`make_entry` is the **one**
re-anchoring implementation (event creation + restore both build entries through it).

Under the §1 protection contract (atomic write-then-rename + SHA-256 sidecar +
history) via :mod:`mira.protect`. Tolerant load by contract (charter §5.3): a
missing file is an empty index, not an error; a corrupt file is backed up and treated
as empty rather than crashing the events list.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any, Dict, List, Optional

from mira import protect
from mira.paths import user_data_dir

log = logging.getLogger(__name__)

INDEX_FILENAME = "events_index.json"
# Bumped 2026-06-09 (spec/44): added denormalised classification fields
# (event_type, event_subtype, description) per row so the dashboard filter
# rail reads one JSON file instead of opening every event.db on every
# keystroke. Older v1 files load fine — missing fields default to
# unclassified / empty. spec/52 (2026-06-08) retired event-level `tags`.
INDEX_SCHEMA_VERSION = 2


def make_entry(
    *,
    event_id: str,
    name: str,
    start_date: Optional[str],
    end_date: Optional[str],
    is_closed: bool,
    event_root: PurePath,
    photos_base_path: Optional[PurePath],
    event_type: str = "unclassified",
    event_subtype: Optional[str] = None,
    description: str = "",
) -> Dict[str, Any]:
    """One ``events_index.json`` row, paths re-anchored per charter §5.9.

    ``event_relpath`` is ``event_root`` relative to ``photos_base_path`` when the event
    lives under the base (the normal case, resolved at load as ``base + relpath``).
    ``event_root_abs`` is the cross-volume fallback only: set (and preferred at resolve
    time) when the event is on a different drive than the base, or no base is configured.

    The classification triplet (``event_type`` + ``event_subtype`` + ``description``)
    is the denormalised cache the dashboard filter rail reads on every keystroke —
    without it the filter would open every ``event.db`` per keystroke, which gets
    slow once the library passes a few dozen events (spec/44 §4.2). The per-event
    ``event.db`` row stays authoritative; this is a stale-tolerant projection that
    gets rewritten via :meth:`Gateway.refresh_index_entry` whenever the event row
    mutates.
    """
    rel: Optional[str] = None
    abs_fallback: Optional[str] = None
    if photos_base_path is not None:
        try:
            rel = PurePath(event_root).relative_to(PurePath(photos_base_path)).as_posix()
        except ValueError:
            abs_fallback = str(event_root)
    else:
        abs_fallback = str(event_root)
    return {
        "id": event_id,
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
        "is_closed": is_closed,
        "event_relpath": rel,
        "event_root_abs": abs_fallback,
        # Classification cache (spec/44). Old indexes that pre-date v2 simply
        # don't have these keys; callers default them via .get() with the
        # same defaults this constructor uses.
        "event_type": event_type or "unclassified",
        "event_subtype": event_subtype,
        "description": description or "",
    }


def _resolve_root(entry: Dict[str, Any], base: Optional[Path]) -> Optional[Path]:
    """Absolute ``event_root`` for one row: the abs fallback wins (cross-volume);
    otherwise ``base + event_relpath``. ``None`` only when neither is resolvable."""
    abs_fallback = entry.get("event_root_abs")
    if abs_fallback:
        return Path(abs_fallback)
    rel = entry.get("event_relpath")
    if rel is not None and base is not None:
        return base / rel
    return None


class EventsIndex:
    """The events index file. The only sanctioned access to its bytes (spec/02 §1)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else user_data_dir() / INDEX_FILENAME

    # ── load ──────────────────────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        """Return the parsed index document. Never raises on a bad/missing file.

        Recovery order when the main file is absent or unparseable:
        1. Try to restore from ``events_index.json.bak`` (the previous good copy).
        2. Fall back to an empty index (``recover_orphan_events`` fills it in from disk).
        """
        if not self.path.exists():
            restored = self._try_restore_from_bak("main file missing")
            if restored is not None:
                return restored
            return self._empty()
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("events"), list):
                raise ValueError("events_index.json has no events list")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.error("events_index.json failed to load (%s)", exc)
            restored = self._try_restore_from_bak(str(exc))
            if restored is not None:
                self._save(restored)   # write back to main path with fresh sidecar
                return restored
            self._backup_bad_file()
            return self._empty()

        outcome = protect.verify(self.path)
        if not outcome.valid and not outcome.sidecar_missing:
            log.warning("events_index.json sidecar mismatch; loading anyway")
        return parsed

    def _try_restore_from_bak(self, reason: str) -> "Optional[Dict[str, Any]]":
        """Attempt to parse ``events_index.json.bak``; return the dict or ``None``."""
        bak = self.path.with_suffix(self.path.suffix + ".bak")
        if not bak.exists():
            return None
        try:
            parsed = json.loads(bak.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("events"), list):
                raise ValueError("bak file has no events list")
            n = len(parsed.get("events", []))
            log.warning(
                "events_index.json restored from .bak (%s; %d event(s) recovered)", reason, n
            )
            return parsed
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.warning("events_index.json.bak also unreadable (%s) — will scan disk", exc)
            return None

    def entries(self) -> List[Dict[str, Any]]:
        """The raw event rows (unresolved paths)."""
        return list(self.load().get("events", []))

    def get(self, event_id: str) -> Optional[Dict[str, Any]]:
        """One raw row by id, or ``None``."""
        for row in self.entries():
            if row.get("id") == event_id:
                return row
        return None

    def base_path(self) -> Optional[str]:
        """The mirrored ``photos_base_path`` (settings owns the authoritative copy)."""
        return self.load().get("photos_base_path") or None

    def list_events(self, base: Optional[Path]) -> List[Dict[str, Any]]:
        """Rows with ``event_root`` resolved against ``base`` — the events-list render.
        Each returned dict is the raw row plus a resolved ``event_root`` (a ``Path`` or
        ``None`` if unresolvable, which the UI shows as a missing/relocated event)."""
        out: List[Dict[str, Any]] = []
        for row in self.entries():
            resolved = _resolve_root(row, base)
            out.append({**row, "event_root": resolved})
        return out

    def resolve_root(self, entry: Dict[str, Any], base: Optional[Path]) -> Optional[Path]:
        """Absolute ``event_root`` for one row (fallback-aware)."""
        return _resolve_root(entry, base)

    # ── mutate ──────────────────────────────────────────────────────────────

    def upsert(self, entry: Dict[str, Any]) -> None:
        """Add or replace a row by id; protected write."""
        doc = self.load()
        events = [r for r in doc.get("events", []) if r.get("id") != entry["id"]]
        events.append(entry)
        events.sort(key=lambda r: (r.get("start_date") or "", r.get("name") or ""))
        doc["events"] = events
        self._save(doc)

    def remove(self, event_id: str) -> None:
        """Drop a row by id; protected write. No-op if absent."""
        doc = self.load()
        doc["events"] = [r for r in doc.get("events", []) if r.get("id") != event_id]
        self._save(doc)

    def set_base(self, base: Optional[str]) -> None:
        """Rewrite the mirrored ``photos_base_path`` (relocate = one edit)."""
        doc = self.load()
        doc["photos_base_path"] = base or ""
        self._save(doc)

    # ── internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {"schema_version": INDEX_SCHEMA_VERSION, "photos_base_path": "", "events": []}

    def _save(self, doc: Dict[str, Any]) -> None:
        doc.setdefault("schema_version", INDEX_SCHEMA_VERSION)
        protect.write_protected(self.path, doc)

    def _backup_bad_file(self) -> None:
        backup = self.path.with_suffix(self.path.suffix + ".bak")
        try:
            if backup.exists():
                backup.unlink()
            self.path.rename(backup)
            log.warning("Bad events_index.json backed up to %s", backup)
        except OSError as exc:
            log.warning("Could not back up bad events index: %s", exc)
