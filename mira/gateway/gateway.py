"""``Gateway`` — the umbrella facade (spec/08 §3.2, charter §2).

The one object the UI holds. Fronts the cross-event tier (the events list, the
``photos_base_path`` anchor, materialising an ``event.db`` from a JSON dump) and hands
out a per-event :class:`EventGateway` for one open event. Other data domains (knowledge
/ rules / tone-corpus, spec/02) plug in here later behind the same umbrella; settings
(Domain 5) and the events index are wired now.

The single absolute anchor is ``settings.photos_base_path`` (charter §5.9); the index
mirrors it. Setting it rewrites both together so they never drift.
"""
from __future__ import annotations

import logging

from core.path_builder import ensure_event_tree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mira.gateway.event_gateway import EventGateway, _utc_now_iso
from mira.gateway.index import EventsIndex, make_entry
from mira.gateway.originals_health import (
    OriginalsCheck,
    OriginalsHealth,
    classify as _classify_originals,
)
from mira.paths import user_data_dir
from mira.settings.repo import SettingsRepo
from mira.store import json_dump, models as m
from mira.store.repo import EventStore
from mira.user_store.repo import UserStore


# ── spec/44 — events_index_filtered query + result shapes ────────────────────


SORT_NEWEST = "newest"
SORT_OLDEST = "oldest"
SORT_NAME   = "name"
SORT_TYPE   = "type"
_VALID_SORTS = (SORT_NEWEST, SORT_OLDEST, SORT_NAME, SORT_TYPE)


# spec/79 §7 — per-event backup snapshots live in a sibling tree to
# the event roots so a "delete files" wipe of one event doesn't take
# its own pre-destructive snapshot with it.
BACKUPS_DIR_NAME = ".mira-backups"


def _live_app_version() -> str:
    """Same metadata read the About dialog uses; ``"dev"`` fallback so
    tests / source checkouts still attach a value."""
    try:
        from importlib import metadata
        return metadata.version("mira")
    except Exception:                                      # noqa: BLE001
        return "dev"


@dataclass
class CrossEventCutRow:
    """One cross-event Cut surfaced to the cross-event Cuts list page.

    The cross-event surface gathers cuts whose ``source_dc_kind = 'user'``
    across every event.db in the index. Each row carries enough to render
    the list without re-opening the event store for display.
    """

    cut_id: str
    tag: str
    anchor_event_id: str
    anchor_event_name: str
    source_dc_id: Optional[str]
    member_count: int
    last_exported_at: Optional[str]
    created_at: str
    updated_at: str


@dataclass
class EventsQuery:
    """Filter parameters for :meth:`Gateway.events_index_filtered`.

    All axes are optional and combine with AND. ``search`` is whitespace-tokenized
    and EVERY token must appear in the joined haystack (name + description).
    """
    search: str = ""
    status: Optional[bool] = None      # None=all, True=closed-only, False=open-only
    type: Optional[str] = None          # event_type enum value or None=all
    subtypes: Optional[List[str]] = None  # multi-select (None=all)
    year: Optional[int] = None
    sort: str = SORT_NEWEST


@dataclass
class EventsListing:
    """:meth:`Gateway.events_index_filtered` result.

    ``rows`` are the filtered + sorted index entries (each with the path columns
    + the classification cache + a resolved ``event_root``). The four aggregate
    fields feed the filter rail's chip labels and dropdown options; they are
    computed over the **unfiltered** catalog so chip counts stay stable while
    the user types.
    """
    rows: List[Dict[str, Any]] = field(default_factory=list)
    type_counts: Dict[str, int] = field(default_factory=dict)
    subtype_counts: Dict[str, int] = field(default_factory=dict)
    year_options: List[int] = field(default_factory=list)
    custom_subtypes: List[str] = field(default_factory=list)


def _row_matches(row: Dict[str, Any], q: EventsQuery) -> bool:
    """Predicate for a single index row against the query. Defaults err toward
    *match* so an old (pre-v2) index row without classification keys still
    appears in unfiltered views."""
    if q.status is not None and bool(row.get("is_closed")) != q.status:
        return False
    if q.type and (row.get("event_type") or "unclassified") != q.type:
        return False
    if q.subtypes:
        st = row.get("event_subtype")
        if st not in q.subtypes:
            return False
    if q.year is not None:
        sd = row.get("start_date") or ""
        if not (len(sd) >= 4 and sd[:4].isdigit() and int(sd[:4]) == q.year):
            return False
    if q.search:
        haystack_parts = [
            row.get("name") or "",
            row.get("description") or "",
        ]
        haystack = " ".join(haystack_parts).lower()
        for term in q.search.lower().split():
            if term not in haystack:
                return False
    return True


def _sort_rows_inplace(rows: List[Dict[str, Any]], sort_mode: str) -> None:
    """Sort ``rows`` in place per ``sort_mode``. Unknown modes leave the list
    untouched (callers' default 'newest' is what nearly everyone gets)."""
    if sort_mode == SORT_NEWEST:
        rows.sort(key=lambda r: (r.get("start_date") or "", (r.get("name") or "").lower()), reverse=True)
    elif sort_mode == SORT_OLDEST:
        rows.sort(key=lambda r: (r.get("start_date") or "", (r.get("name") or "").lower()))
    elif sort_mode == SORT_NAME:
        rows.sort(key=lambda r: (r.get("name") or "").lower())
    elif sort_mode == SORT_TYPE:
        rows.sort(key=lambda r: (r.get("event_type") or "", r.get("start_date") or ""))
    # unknown sort_mode: leave as-is

log = logging.getLogger(__name__)


class Gateway:
    """The umbrella over the per-domain repositories (spec/02 §1).

    spec/53: the new user-level data store (``mira.db``) is exposed via
    :meth:`user_store` — lazily opened on first access, created + imported
    from the legacy JSON files if it doesn't exist yet (spec/53 §4). The
    existing ``settings`` and ``index`` repos stay unchanged on the public
    surface so existing callers (gateway methods, UI dialogs, tests) keep
    working; new surfaces (Cuts, People, etc.) read from
    :meth:`user_store` directly.
    """

    def __init__(
        self,
        *,
        settings: Optional[SettingsRepo] = None,
        index: Optional[EventsIndex] = None,
        user_store_path: Optional[Path] = None,
        installation_profile: str = "XMC",
        now: Callable[[], str] = _utc_now_iso,
    ) -> None:
        self.settings = settings or SettingsRepo()
        self.index = index or EventsIndex()
        self._now = now
        # Lazy user-store: opened on first .user_store access. Path defaults to
        # the standard ``user_data_dir() / "mira.db"`` so production
        # code gets the right location; tests pass ``user_store_path=tmp_path
        # / "mira.db"`` (paired with their custom settings/index paths
        # under the same tmp_path) to keep the three artefacts colocated.
        self._user_store_path = (
            Path(user_store_path) if user_store_path is not None
            else user_data_dir() / "mira.db"
        )
        self._installation_profile = installation_profile
        self._user_store: Optional[UserStore] = None

    # ----- the user-level data store (spec/53) -------------------------- #

    @property
    def user_store(self) -> UserStore:
        """The user-level :class:`UserStore`. Opens (or creates+imports) on
        first access.

        Three paths:

        * **File exists** — opens with the spec/53 §3.1 protection contract
          (sidecar verify + integrity_check). The legacy JSON files (if they
          still exist) are NOT touched; the importer already retired them
          on the previous launch that created the file.
        * **File missing, legacy JSON present** — creates a fresh
          ``mira.db``, runs :func:`import_legacy_state` against the
          existing ``self.settings.path`` and ``self.index.path``, retiring
          those files as the spec/53 §4 step-4 atomic move.
        * **File missing, no legacy JSON** — truly fresh install. Creates
          the database; the importer's step-1 still stamps the
          installation_profile + seeds the feature_flag rows. Settings /
          wizard / events tables stay empty until the wizard runs.

        The store is cached on the instance; close it via :meth:`close`."""
        if self._user_store is not None:
            return self._user_store

        from mira.user_store.import_legacy import import_legacy_state

        if self._user_store_path.is_file():
            self._user_store = UserStore.open(self._user_store_path)
            return self._user_store

        # Fresh-create path. The importer's step-4 retire is idempotent — a
        # missing legacy file is a no-op.
        self._user_store = UserStore.create(self._user_store_path)
        import_legacy_state(
            self._user_store,
            settings_path=self.settings.path,
            events_index_path=self.index.path,
            profile_name=self._installation_profile,
        )
        return self._user_store

    def close(self) -> None:
        """Close the user-store handle if open (the spec/53 §3.1 clean-close
        path: WAL checkpoint + sidecar recompute + backup roll). No-op when
        the lazy store was never accessed."""
        if self._user_store is not None:
            self._user_store.close()
            self._user_store = None

    # ----- the base anchor ----------------------------------------------- #

    def photos_base_path(self) -> Optional[Path]:
        """The single absolute anchor (charter §5.9), or ``None`` if unset."""
        raw = self.settings.load().photos_base_path
        return Path(raw) if raw else None

    def set_photos_base_path(self, path: Optional[str]) -> None:
        """Write the anchor to settings **and** the index mirror together.

        Callers that let the user *edit* the anchor (the Settings dialog) MUST first
        consult :meth:`base_change_blockers` and refuse the change when it is non-empty —
        otherwise relative-anchored events are silently orphaned (charter §5.9). This setter
        itself stays unconditional: it is also the re-anchor primitive used right after a
        verified library move."""
        self.settings.update(photos_base_path=path or "")
        self.index.set_base(path or "")

    def base_change_blockers(self, new_base: Optional[str]) -> List[Dict[str, Any]]:
        """Events that switching ``photos_base_path`` to ``new_base`` would orphan — the
        verify-then-allow gate (Nelson 2026-06-01, charter §5.9).

        An event is *anchored to the base* when it resolves **relative** to it
        (``event_relpath`` set, no ``event_root_abs``). Absolute-anchored events
        (cross-volume) are immune — their root never depends on the base, so they are
        skipped. For each relative-anchored event we verify whether its ``event.db`` is
        actually present at the prospective location (``new_base / event_relpath``):

          * **found** ⇒ the bytes are already at the new location (a genuine whole-library
            move, or the two bases share that subtree) — not a blocker;
          * **missing** ⇒ the change would leave the event pointing at nothing — a blocker.

        Returns the blockers as ``{id, name, relpath}`` dicts (empty ⇒ the change is safe to
        apply). An empty/unset ``new_base`` orphans every relative-anchored event (they become
        unresolvable), so they are all returned."""
        new_base_path = Path(new_base) if new_base else None
        blockers: List[Dict[str, Any]] = []
        for row in self.index.entries():
            relpath = row.get("event_relpath")
            if row.get("event_root_abs") or relpath is None:
                continue  # abs-anchored or unanchored — base change doesn't move it
            found = (
                new_base_path is not None
                and (new_base_path / relpath / "event.db").exists()
            )
            if not found:
                blockers.append({
                    "id": row.get("id"),
                    "name": row.get("name") or "",
                    "relpath": relpath,
                })
        return blockers

    # ----- missing-originals detection (charter §7, locate/relink flow) -- #

    def check_originals(self, event_id: str) -> OriginalsCheck:
        """Classify whether this event's ``Original Media/`` is reachable.

        The detection layer for the captured tree (charter §7). Pure read:
        no ``event.db`` opened, no writes, no side effects. The UI calls
        this *before* opening the activity dashboard; a non-OK result
        routes the user into the locate/relink dialog.

        Three outcomes — see :class:`OriginalsHealth` for the contract:

        * ``OK`` — ``event_root`` exists and ``Original Media/`` is
          present and non-empty.
        * ``STORAGE_OFFLINE`` — the storage anchor is unreadable: for
          relative-anchored events that means ``photos_base_path`` is
          gone; for any event it also means the drive root the event
          lives on is gone. Action: alert and reconnect, **no data
          change**.
        * ``ORIGINALS_MOVED`` — the storage is mounted but the event's
          folder (or the ``Original Media/`` leaf) isn't where the index
          expects. Action: Locate dialog → re-anchor via
          :meth:`relink_event`.

        Unknown ``event_id`` returns an OK verdict (the missing-originals
        flow has nothing to do here — the events list will catch it).
        """
        entry = self.index.get(event_id)
        if entry is None:
            return OriginalsCheck(
                state=OriginalsHealth.OK,
                event_root=None,
                base_path=self.photos_base_path(),
                originals_dir=None,
            )
        base = self.photos_base_path()
        event_root = self.index.resolve_root(entry, base)
        requires_base = (
            entry.get("event_relpath") is not None
            and not entry.get("event_root_abs")
        )
        return _classify_originals(
            base_path=base,
            event_root=event_root,
            requires_base=requires_base,
        )

    def relink_event(self, event_id: str, new_event_root: Path) -> None:
        """Re-anchor one event to a new on-disk location (single-event move).

        The verify-then-allow primitive for :meth:`check_originals`'
        ``ORIGINALS_MOVED`` branch. Mirrors :meth:`set_photos_base_path`
        for the per-event case: confirm the bytes are at the new path,
        then rewrite the events-index row through :func:`make_entry` so
        the relative-vs-absolute decision is made by the same primitive
        that ingest and restore use (never hand-rolled here).

        * ``new_event_root`` must contain ``event.db`` (and, normally,
          ``Original Media/``). Refused with :class:`FileNotFoundError`
          when ``event.db`` is absent — protects against re-anchoring to
          an arbitrary folder.
        * If ``new_event_root`` lives under the current
          ``photos_base_path``, the row stays *relative-anchored*
          (``event_relpath`` set, ``event_root_abs`` cleared). If it
          lives on a different drive (or no base is configured), the row
          flips to *abs-anchored* (``event_root_abs`` set,
          ``event_relpath`` cleared). The two columns are mutually
          exclusive at the resolver layer.
        * The index write is atomic (``EventsIndex._save`` →
          ``protect.write_protected``); the on-disk Original Media tree
          is never touched (charter §7).

        Items, decisions, edits, markers, snapshots all survive: only
        the index row's path columns change.
        """
        new_event_root = Path(new_event_root)
        db_path = new_event_root / "event.db"
        if not db_path.is_file():
            raise FileNotFoundError(
                f"no event.db at {new_event_root} — refusing relink "
                f"(verify-then-allow gate)"
            )
        prev = self.index.get(event_id)
        if prev is None:
            raise KeyError(f"no event {event_id} in the index")

        # Read the freshly-pointed event.db so the cached classification
        # fields (name/dates/event_type/…) in the rewritten index row
        # stay current — same projection refresh_index_entry does.
        eg = EventGateway.open(db_path, event_root=new_event_root, now=self._now)
        try:
            ev = eg.event()
        finally:
            eg.close()

        new_entry = make_entry(
            event_id=event_id,
            name=ev.name,
            start_date=ev.start_date,
            end_date=ev.end_date,
            is_closed=ev.is_closed,
            event_root=new_event_root,
            photos_base_path=self.photos_base_path(),
            event_type=ev.event_type or "unclassified",
            event_subtype=ev.event_subtype,
            description=ev.description,
        )
        self.index.upsert(new_entry)

    # ----- events list --------------------------------------------------- #

    def list_events(self) -> List[Dict[str, Any]]:
        """Resolved events list (rows + absolute ``event_root``) — the events render."""
        return self.index.list_events(self.photos_base_path())

    def events_index_filtered(self, query: "EventsQuery") -> "EventsListing":
        """Filter + sort + aggregate over the events index in a single read.

        The dashboard filter rail (spec/44 §3.2) runs this on every keystroke;
        the index's denormalised classification cache is what keeps the keystroke
        responsive — no ``event.db`` is opened during a filter (compare to the
        review's finding #2 / #6 about per-keystroke N-scan patterns). Aggregates
        are computed over the *unfiltered* set so the chip labels show the full
        catalog ("Trip (4)") regardless of the current filter state.

        See :class:`EventsQuery` for the filter axes and :class:`EventsListing`
        for the returned shape.
        """
        from mira import event_classification

        rows = self.list_events()
        # Aggregates over ALL rows — these feed the filter rail chip labels.
        type_counts: Dict[str, int] = {}
        year_set: set[int] = set()
        custom_subtypes_for_type: set[str] = set()
        subtype_counts: Dict[str, int] = {}
        for r in rows:
            t = r.get("event_type") or event_classification.EVENT_TYPE_UNCLASSIFIED
            type_counts[t] = type_counts.get(t, 0) + 1
            sd = r.get("start_date") or ""
            if len(sd) >= 4 and sd[:4].isdigit():
                year_set.add(int(sd[:4]))
            st = r.get("event_subtype")
            # Subtype counts are scoped to the selected type so the chip row
            # only lists subtypes of the type currently in play.
            if query.type is None or t == query.type:
                if st:
                    subtype_counts[st] = subtype_counts.get(st, 0) + 1
            # The "Custom" subtype group lists user-typed values for the
            # currently-selected type.
            if query.type and t == query.type and st and \
                    not event_classification.is_preset_subtype(t, st):
                custom_subtypes_for_type.add(st)

        # Filter.
        filtered = [r for r in rows if _row_matches(r, query)]
        # Sort.
        _sort_rows_inplace(filtered, query.sort)

        return EventsListing(
            rows=filtered,
            type_counts=type_counts,
            subtype_counts=subtype_counts,
            year_options=sorted(year_set, reverse=True),
            custom_subtypes=sorted(custom_subtypes_for_type),
        )

    # ----- classification mutator (spec/44) ------------------------------ #

    def set_classification(
        self,
        event_id: str,
        *,
        event_type: Optional[str] = None,
        event_subtype: Optional[str] = None,
        description: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        duration_value: Optional[int] = None,
        duration_unit: Optional[str] = None,
        participants: Optional[List[str]] = None,
        context: Optional[str] = None,
        experience_type: Optional[str] = None,
        creative_focus: Optional[List[str]] = None,
        extras_updates: Optional[Dict[str, Any]] = None,
    ) -> None:
        """One transactional update to the singleton event row + index cache refresh.

        Each kwarg is independently optional — pass ``None`` (or omit) to leave a
        field unchanged. ``event_subtype=""`` clears the subtype (stored as NULL).
        ``participants=[]`` clears the list (stored as JSON ``"[]"``).
        ``creative_focus=[]`` clears the multi-select (stored as JSON ``"[]"``).
        ``context=""`` / ``experience_type=""`` clear to NULL.
        ``extras_updates`` shallow-merges into the existing ``extras_json`` blob so
        IPTC location facets aren't clobbered.

        ``event_type``, ``duration_unit``, ``context``, ``experience_type``,
        and every value in ``creative_focus`` and ``participants`` are
        **rejected** with :class:`ValueError` when not in the closed enum —
        *not* silently coerced. Callers with possibly-unknown values should
        normalise upstream via :mod:`mira.event_classification`. The
        gateway raises rather than letting bad data through.

        spec/64 (2026-06-13): Scope / Mood / Transport retired; replaced by
        Context (single-select) / Experience Type (single-select) / Creative
        Focus (multi-select). Old per-unit duration cap also gone — any
        ``duration_value > 0`` accepted in any unit.

        On success, the cached index entry is refreshed
        (:meth:`refresh_index_entry`) so the dashboard's filter rail sees the
        change without re-reading every ``event.db``.
        """
        import json as _json
        from mira import event_classification as _ec

        # Validate up-front so we don't half-apply a partial transaction.
        if event_type is not None and not _ec.is_known_type(event_type):
            raise ValueError(
                f"unknown event_type {event_type!r}; "
                f"expected one of {_ec.ALL_EVENT_TYPES}"
            )
        if duration_unit is not None and duration_unit != "" and duration_unit not in _ec.DURATION_UNITS:
            raise ValueError(
                f"unknown duration_unit {duration_unit!r}; "
                f"expected one of {_ec.DURATION_UNITS}"
            )
        # Context / Experience Type are closed enums (spec/64 §3.2 + §3.3).
        # Empty string clears the column to NULL.
        if context is not None and context != "" and not _ec.is_known_context(context):
            raise ValueError(
                f"unknown context {context!r}; "
                f"expected one of {_ec.CONTEXT_OPTIONS}"
            )
        if experience_type is not None and experience_type != "" and not _ec.is_known_experience_type(experience_type):
            raise ValueError(
                f"unknown experience_type {experience_type!r}; "
                f"expected one of {_ec.EXPERIENCE_TYPE_OPTIONS}"
            )
        # Creative Focus is a multi-select (spec/64 §3.4). Empty list clears;
        # each member must be a known option ("none" is one of them — it's
        # the explicit "not a photo event" answer).
        if creative_focus is not None:
            for cf in creative_focus:
                if not _ec.is_known_creative_focus(cf):
                    raise ValueError(
                        f"unknown creative_focus {cf!r}; "
                        f"expected one of {_ec.CREATIVE_FOCUS_OPTIONS}"
                    )
        if participants is not None:
            for p in participants:
                if p not in _ec.PARTICIPANT_OPTIONS:
                    raise ValueError(
                        f"unknown participant {p!r}; "
                        f"expected one of {_ec.PARTICIPANT_OPTIONS}"
                    )

        eg = self.open_event(event_id)
        try:
            updates: List[str] = []
            values: List[Any] = []
            if event_type is not None:
                updates.append("event_type = ?")
                values.append(event_type)
            if event_subtype is not None:
                updates.append("event_subtype = ?")
                # Empty string normalises to NULL so subtype filtering / chip
                # rendering treats "no subtype" uniformly.
                values.append(event_subtype if event_subtype else None)
            if description is not None:
                updates.append("description = ?")
                values.append(description)
            # spec/77 §5 — start_date / end_date are now editable from
            # the Event Header dialog. Both are ISO date strings;
            # passing "" clears the column to NULL.
            if start_date is not None:
                updates.append("start_date = ?")
                values.append(start_date if start_date else None)
            if end_date is not None:
                updates.append("end_date = ?")
                values.append(end_date if end_date else None)
            if duration_value is not None:
                updates.append("duration_value = ?")
                # 0 / falsy clears (stored as NULL).
                values.append(duration_value if duration_value else None)
            if duration_unit is not None:
                updates.append("duration_unit = ?")
                values.append(duration_unit if duration_unit else None)
            if context is not None:
                updates.append("context = ?")
                values.append(context if context else None)
            if experience_type is not None:
                updates.append("experience_type = ?")
                values.append(experience_type if experience_type else None)
            if creative_focus is not None:
                updates.append("creative_focus = ?")
                values.append(_json.dumps(list(creative_focus)))
            if participants is not None:
                updates.append("participants = ?")
                values.append(_json.dumps(list(participants)))
            if extras_updates:
                cur = _json.loads(eg.event().extras_json or "{}")
                cur.update(extras_updates)
                updates.append("extras_json = ?")
                values.append(_json.dumps(cur))
            if not updates:
                return
            updates.append("updated_at = ?")
            values.append(self._now())
            sql = f"UPDATE event SET {', '.join(updates)} WHERE id = 1"
            eg.store.conn.execute(sql, values)
        finally:
            eg.close()

        self.refresh_index_entry(event_id)

    def recover_orphan_events(self) -> List[Dict[str, Any]]:
        """Scan ``photos_base_path`` for ``event.db`` files not tracked by the index.

        For each orphan found, opens the db, reads the event record, builds a
        proper index entry, and registers it.  Returns the list of entries that
        were added (empty if nothing new was found or the base path is unset).

        This is the self-healing path: if the index gets out of sync with the
        filesystem (e.g. the index file was reset, the base path was moved, or
        a bug cleared the index), the user never loses access to their events.
        """
        base = self.photos_base_path()
        if base is None or not base.exists():
            return []

        known_roots: set = set()
        for row in self.index.entries():
            resolved = self.index.resolve_root(row, base)
            if resolved is not None:
                known_roots.add(resolved.resolve())

        recovered: List[Dict[str, Any]] = []
        for db_path in sorted(base.rglob("event.db")):
            event_root = db_path.parent
            if event_root.resolve() in known_roots:
                continue
            try:
                eg = EventGateway.open(db_path, event_root=event_root, now=self._now)
                ev = eg.event()
                days = eg.trip_days()
                eg.close()
            except Exception:
                log.exception("recover_orphan_events: could not open %s — skipping", db_path)
                continue
            entry = make_entry(
                event_id=ev.uuid,
                name=ev.name,
                start_date=ev.start_date,
                end_date=ev.end_date,
                is_closed=ev.is_closed,
                event_root=event_root,
                photos_base_path=base,
                event_type=ev.event_type or "unclassified",
                event_subtype=ev.event_subtype,
                description=ev.description,
            )
            # Guard: don't register if the uuid is already tracked under a different path.
            if self.index.get(ev.uuid) is None:
                self.index.upsert(entry)
                known_roots.add(event_root.resolve())
                recovered.append(entry)
                log.info("recover_orphan_events: registered %r at %s", ev.name, event_root)
            else:
                log.warning(
                    "recover_orphan_events: uuid %s already in index (different path?) "
                    "— skipping %s", ev.uuid, event_root,
                )
        return recovered

    # ----- delete one event ---------------------------------------------- #

    def delete_event(self, event_id: str, *, delete_files: bool = False) -> bool:
        """Remove an event from Mira's record. Returns ``True`` if a row was present.

        ``delete_files=False`` (default) is **index-only** — the photos, folders and
        ``event.db`` on disk are NOT touched; only the events-index row is dropped, so the
        event can be re-added later via Import plan from folder (the legacy
        ``data.event_store.delete_event`` contract).

        ``delete_files=True`` ALSO deletes the event's **entire folder on disk** (the
        ``Original Media`` originals + the rebuildable projections + ``event.db``)
        — the user's deliberate, confirmed choice (spec/14 §5D delete-event). This deletes
        user *originals*: it is the second sanctioned place the app removes them (after the
        SD-wipe gate, invariant #9), so callers MUST gate it behind a blunt confirmation. It
        only ever touches **this event's own folder** under the resolved event root — never a
        camera card or any path outside it. The folder is removed first, then the index row;
        an unresolvable root falls back to an index-only removal (nothing to delete)."""
        if self.index.get(event_id) is None:
            return False
        if delete_files:
            entry = self.index.get(event_id)
            root = self.index.resolve_root(entry, self.photos_base_path())
            if root is not None and root.exists():
                # spec/79 §7.2 — a pre-destructive snapshot of the
                # event.db before the folder wipe. The backups dir is
                # a SIBLING of the event root, so the snapshot rides
                # out the rmtree. Failure here is logged but does NOT
                # block the delete — the user explicitly confirmed.
                db_path = root / "event.db"
                backups_dir = self.event_backups_dir(event_id)
                if db_path.exists() and backups_dir is not None:
                    try:
                        from core import db_backup
                        # spec/82 §A.1 — pre-risky-op (the delete-all
                        # wipe) is a milestone trigger.
                        db_backup.snapshot(
                            db_path,
                            backups_dir,
                            reason=db_backup.REASON_MILESTONE,
                            app_version=_live_app_version(),
                        )
                        log.info(
                            "delete_event: pre-destructive snapshot of "
                            "%s saved before folder wipe", event_id,
                        )
                    except Exception as exc:               # noqa: BLE001
                        log.warning(
                            "delete_event: pre-destructive snapshot "
                            "FAILED for %s: %s", event_id, exc,
                        )
                import shutil
                shutil.rmtree(root)
                log.info("delete_event: removed event folder %s", root)
        self.index.remove(event_id)
        return True

    # ----- open one event ------------------------------------------------ #

    def open_event(self, event_id: str) -> EventGateway:
        """Resolve the root, open its ``event.db``, return the per-event facade.

        Wires the spec/79 §7.2 close-if-dirty snapshot context — the
        EventGateway snapshots its db on close when the session
        wrote at least one row. Also wires the spec/81 Phase 2 cross-event
        projection sync (Item 1 recommendation): on close,
        :class:`LibraryGateway` re-projects the event's items into
        ``global_items`` so cross-event reads stay in lockstep.
        """
        entry = self.index.get(event_id)
        if entry is None:
            raise KeyError(f"no event {event_id} in the index")
        root = self.index.resolve_root(entry, self.photos_base_path())
        if root is None:
            raise RuntimeError(f"event {event_id} root is unresolvable (relocated?)")
        return EventGateway.open(
            root / "event.db",
            event_root=root,
            now=self._now,
            backups_dir=self.event_backups_dir(event_id),
            app_version=_live_app_version(),
            on_close=self._make_sync_hook(event_id),
        )

    def _make_sync_hook(self, event_id: str) -> Callable[[EventGateway], None]:
        """Build the close-time sync callable for ``open_event``. Captures
        ``event_id`` + the lazy user_store; runs
        :meth:`LibraryGateway.sync_event` against the event's name (looked
        up at sync time so a rename mid-session lands the new name)."""
        def _hook(eg: EventGateway) -> None:
            from mira.gateway.library_gateway import LibraryGateway
            try:
                ev = eg.event()
            except Exception:                              # noqa: BLE001
                return                                     # malformed event row
            lg = LibraryGateway(self.user_store, now=self._now)
            lg.sync_event(
                event_store=eg.store,
                event_uuid=ev.uuid,
                event_name=ev.name,
            )
        return _hook

    def cross_event_cuts(self) -> list:
        """All cross-event Cuts across the library — one row per cut row
        with ``source_dc_kind = 'user'`` in any event.db. Events that can't
        open (relocated, in-use lock) are skipped + logged, never raised.

        Returns ``list[CrossEventCutRow]`` ordered by ``updated_at`` desc
        (most recent on top — the list page's default order)."""
        out: list = []
        for entry in self.list_events():
            event_id = entry.get("id") or entry.get("uuid")
            if not event_id:
                continue
            event_name = entry.get("name") or event_id
            root = self.index.resolve_root(entry, self.photos_base_path())
            if root is None:
                continue
            db_path = root / "event.db"
            if not db_path.exists():
                continue
            try:
                store = EventStore.open(db_path)
            except Exception:                              # noqa: BLE001
                log.warning(
                    "cross_event_cuts: could not open %s — skipping", db_path)
                continue
            try:
                rows = store.conn.execute(
                    "SELECT c.id, c.tag, c.source_dc_id, c.last_exported_at, "
                    "c.created_at, c.updated_at, "
                    "(SELECT COUNT(*) FROM cut_member cm "
                    "  WHERE cm.cut_id = c.id) AS member_count "
                    "FROM cut c "
                    "WHERE c.source_dc_kind = 'user' "
                    "ORDER BY c.updated_at DESC"
                ).fetchall()
                for r in rows:
                    out.append(CrossEventCutRow(
                        cut_id=r["id"], tag=r["tag"],
                        anchor_event_id=event_id,
                        anchor_event_name=event_name,
                        source_dc_id=r["source_dc_id"],
                        member_count=int(r["member_count"] or 0),
                        last_exported_at=r["last_exported_at"],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    ))
            finally:
                store.close()
        # Sort by updated_at desc across all events (each event's slice is
        # already sorted; flat-merge them).
        out.sort(key=lambda r: r.updated_at, reverse=True)
        return out

    def delete_cross_event_dc(self, dc_id: str) -> dict:
        """Delete a cross-event Dynamic Collection (saved_filter row) AND
        sweep its references across every event.db (spec/81 Phase 2 polish):
        cuts that pointed at it get ``source_dc_id`` + ``source_dc_kind``
        NULLed. The cut survives — freeze invariant (spec/81 §5).

        Returns the sweep summary so the UI can surface what happened."""
        from mira.gateway.library_gateway import LibraryGateway
        from mira.shared.cross_event_sweeps import sweep_dc_references
        lg = LibraryGateway(self.user_store, now=self._now)
        lg.delete_dc(dc_id)
        return sweep_dc_references(self, dc_id)

    def sweep_dangling_cross_event_members(self) -> dict:
        """Walk every event.db; drop cross-event cut_member rows whose
        source event is no longer in the index (spec/81 Phase 2 polish).
        Cuts survive — only the stale members go."""
        from mira.shared.cross_event_sweeps import (
            sweep_dangling_cross_event_members as _sweep,
        )
        return _sweep(self)

    def delete_cross_event_cut(self, anchor_event_id: str,
                               cut_id: str) -> None:
        """Delete a cross-event Cut by opening its anchor event's gateway
        and calling :meth:`EventGateway.delete_cut`. Members cascade via the
        cut_id FK."""
        eg = self.open_event(anchor_event_id)
        try:
            eg.delete_cut(cut_id)
        finally:
            eg.close()

    def reconcile_global_items(self) -> dict:
        """Reconcile the cross-event ``global_items`` projection against
        every event in the index. Called from app startup (spec/81 Phase 2
        Item 1 recommendation) so an event closed by another process / a
        new install with existing events catches up before the user's
        first cross-event read.

        Returns the :func:`global_items_sync.reconcile_all` summary.
        Unopenable events are skipped + logged, never raised."""
        from mira.gateway.library_gateway import LibraryGateway

        known: list = []
        for entry in self.list_events():
            uuid = entry.get("id") or entry.get("uuid")
            name = entry.get("name") or ""
            if uuid:
                known.append((uuid, name))

        def _open(uuid):
            """Raw EventStore — no on_close sync hook (reconcile_all already
            calls sync_event directly; the hook would re-run it on close
            and recurse through open_event)."""
            entry = self.index.get(uuid)
            if entry is None:
                return None
            root = self.index.resolve_root(entry, self.photos_base_path())
            if root is None:
                return None
            try:
                return EventStore.open(root / "event.db")
            except Exception:                              # noqa: BLE001
                return None

        lg = LibraryGateway(self.user_store, now=self._now)
        return lg.reconcile_all(
            open_event_store=_open,
            known_events=known,
        )

    def event_backups_dir(self, event_id: str) -> Optional[Path]:
        """``<photos_base_path>/.mira-backups/<event_id>/`` — where
        :mod:`core.db_backup` writes this event's snapshots. ``None``
        when ``photos_base_path`` isn't set yet (pre-wizard); callers
        treat that as "skip backups, the library has no anchor"."""
        base = self.photos_base_path()
        if base is None:
            return None
        return base / BACKUPS_DIR_NAME / event_id

    # ----- materialise (restore from backup, charter §4 step 5) ---------- #

    def materialise_event(
        self,
        event_json: Dict[str, Any],
        entry: Dict[str, Any],
        *,
        app_version: str = "",
    ) -> Path:
        """Create an ``event.db`` from a JSON dump and register its index row.

        The restore-from-backup path (``json_dump.from_json`` is the one reader). ``entry``
        is an :func:`mira.gateway.index.make_entry` row; the ``event.db`` is
        written under the entry's resolved ``event_root``. Returns the db path."""
        doc = json_dump.from_json(event_json)
        root = self.index.resolve_root(entry, self.photos_base_path())
        if root is None:
            raise RuntimeError("cannot materialise: entry root is unresolvable")
        root.mkdir(parents=True, exist_ok=True)
        # spec/57: every creation/restore path births the same skeleton
        # (Original Media/{_cameras,_phones,_other} + Edited Media + Cuts)
        # so an event always reads identically in Explorer.
        ensure_event_tree(root)
        db_path = root / "event.db"
        if db_path.exists():
            db_path.unlink()
        store = EventStore.create(db_path, event_id=doc.event.uuid, app_version=app_version)
        try:
            store.save_document(doc)
        finally:
            store.close()
        self.index.upsert(entry)
        return db_path

    # ----- create a brand-new event (ingest, charter §5.6) --------------- #

    def create_event(self, doc: "m.EventDocument", event_root: Path) -> EventGateway:
        """Materialise a freshly-assembled event and return its open facade.

        The ingest surface (`mira/ingest`) copies the originals into ``event_root``,
        assembles the ``EventDocument`` (event + trip days + cameras + items), and calls
        this. Composition of the one re-anchoring rule (:func:`make_entry`, charter §5.9)
        + the one db-creation path (:meth:`materialise_event`): create and restore both
        build the ``event.db`` the same way. ``event_root`` must already exist (the
        originals live under it)."""
        entry = make_entry(
            event_id=doc.event.uuid,
            name=doc.event.name,
            start_date=doc.event.start_date,
            end_date=doc.event.end_date,
            is_closed=doc.event.is_closed,
            event_root=event_root,
            photos_base_path=self.photos_base_path(),
            event_type=doc.event.event_type or "unclassified",
            event_subtype=doc.event.event_subtype,
            description=doc.event.description,
        )
        self.materialise_event(json_dump.to_json(doc), entry)
        return self.open_event(doc.event.uuid)

    # ----- refresh the index cache for one event ------------------------ #

    def refresh_index_entry(self, event_id: str) -> None:
        """Re-read the open ``event.db`` for ``event_id`` and rewrite the events-index
        row to match. Single seam used by :meth:`EventGateway.set_classification`
        (and any future mutator that needs the dashboard cache to stay current).

        The per-event ``event.db`` is the source of truth; this method projects the
        fields the dashboard filter rail consumes — name, start/end date,
        is_closed, event_type, event_subtype, description, tags — into the
        index cache. Path columns (``event_relpath`` / ``event_root_abs``) are
        preserved by re-running :func:`make_entry` against the same
        ``event_root`` the entry already pointed at. A missing entry is a
        no-op + warning (don't silently create); a missing event.db raises
        like the rest of the open/read path."""
        prev = self.index.get(event_id)
        if prev is None:
            log.warning("refresh_index_entry: %s not in index — skipping", event_id)
            return
        base = self.photos_base_path()
        root = self.index.resolve_root(prev, base)
        if root is None:
            raise RuntimeError(f"event {event_id} root is unresolvable")
        eg = EventGateway.open(root / "event.db", event_root=root, now=self._now)
        try:
            ev = eg.event()
        finally:
            eg.close()
        new_entry = make_entry(
            event_id=event_id,
            name=ev.name,
            start_date=ev.start_date,
            end_date=ev.end_date,
            is_closed=ev.is_closed,
            event_root=root,
            photos_base_path=base,
            event_type=ev.event_type or "unclassified",
            event_subtype=ev.event_subtype,
            description=ev.description,
        )
        self.index.upsert(new_entry)

    # ----- move days between events (subdivide a trip, spec/14 §5C.3/§5D) ----- #

    def _resolve_target_day(self, tgt: EventGateway, src_day) -> int:
        """The target ``day_number`` for a moved source day: **merge** into a same-date day
        if the target already has one (smallest-day-number-wins — ``trip_days`` is ordered by
        ``day_number``, so the first date match is the smallest; spec/14 §5D Q3); else create a
        new day (next number) carrying the source day's date/description/location/tz. Called
        inside the target write transaction so a day created for an earlier moved day is visible
        to the next."""
        existing = tgt.trip_days()
        if src_day is not None and src_day.date:
            for t in existing:
                if t.date == src_day.date:
                    return t.day_number
        new_dn = max((t.day_number for t in existing), default=0) + 1
        tgt.store.upsert(m.TripDay(
            day_number=new_dn,
            date=(src_day.date if src_day else None),
            description=(src_day.description if src_day else ""),
            location=(src_day.location if src_day else None),
            tz_minutes=(src_day.tz_minutes if src_day else None),
        ))
        return new_dn

    def move_days(
        self, source_event_id: str, day_numbers: List[int], target_event_id: str,
    ) -> Dict[str, int]:
        """Move whole trip days — their captured items + files + cull/select decisions — from
        one event to another: the subdivide-a-trip primitive (spec/14 §5C.3/§5D). **Copy +
        sha256-verify into the target first, then remove from the source**, so an interrupted
        move never loses bytes (worst case: a duplicate). Target-day mapping merges into a
        same-date day or creates a new one (§5D Q3). **Blocks** (``ValueError``) a day with
        downstream Process/Curate work (lineage/stacks) or with video clips/snapshots — v1
        scope is cull/select-level captured days (§5D Q4). Returns ``{'moved_days','moved_items'}``."""
        from dataclasses import replace
        import shutil

        from mira.ingest.engine import _hash_size

        if source_event_id == target_event_id:
            raise ValueError("source and target events must differ")
        wanted = sorted(set(day_numbers))
        src = self.open_event(source_event_id)
        tgt = self.open_event(target_event_id)
        try:
            src_root, tgt_root = src.event_root, tgt.event_root
            if src_root is None or tgt_root is None:
                raise RuntimeError("move_days needs both event roots resolvable")

            # 1. Validate every requested day up front (all-or-nothing on validation).
            plan: list[tuple[int, list]] = []
            for dn in wanted:
                items = src.items(day=dn, include_hidden=True)
                ids: set = set()
                has_children = False
                for it in items:
                    ids.add(it.id)
                    kids = src.children(it.id)
                    has_children = has_children or bool(kids)
                    ids.update(ch.id for ch in kids)
                ref = src._downstream_refs(ids)
                if ref is not None:
                    raise ValueError(f"day {dn} has downstream {ref} work — can't move it")
                if has_children:
                    raise ValueError(
                        f"day {dn} has video clips/snapshots — moving days with clips "
                        "isn't supported yet"
                    )
                plan.append((dn, items))

            # 2. Copy + verify every file into the target BEFORE any DB change (no-loss order).
            copied: list = []
            for _dn, items in plan:
                for it in items:
                    if not it.origin_relpath:
                        continue
                    dest = tgt_root / it.origin_relpath
                    if dest.exists():
                        raise FileExistsError(
                            f"target already has {it.origin_relpath} — move aborted")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_root / it.origin_relpath, dest)
                    sha, _size = _hash_size(dest)
                    if it.sha256 and sha != it.sha256:
                        raise RuntimeError(f"copy verify failed for {it.origin_relpath}")
                    copied.append(it)

            # 3. Write the records into the target (one transaction).
            with tgt.store.transaction() as conn:
                conn.execute("PRAGMA defer_foreign_keys = ON")
                seen_cameras: set = set()
                for dn, items in plan:
                    src_day = next(
                        (d for d in src.trip_days() if d.day_number == dn), None)
                    target_dn = self._resolve_target_day(tgt, src_day)
                    for it in items:
                        if it.camera_id and it.camera_id not in seen_cameras:
                            seen_cameras.add(it.camera_id)
                            cam = src.store.get(m.Camera, it.camera_id)
                            if cam is not None and tgt.store.get(m.Camera, it.camera_id) is None:
                                tgt.store.upsert(cam)
                                for pair in src.store.query_by(
                                        m.CameraCalibrationPair, camera_id=it.camera_id):
                                    tgt.store.upsert(pair)
                        tgt.store.upsert(replace(it, day_number=target_dn))
                        for ps in src.store.query_by(m.PhaseState, item_id=it.id):
                            tgt.store.upsert(ps)
                        adj = src.store.get(m.Adjustment, it.id)
                        if adj is not None:
                            tgt.store.upsert(adj)
                        # Cuts do NOT travel (spec/61): membership rides on
                        # lineage (exported finals), and exports stay with the
                        # source event — a Cut lives and dies with its event.
                        # photo_person is M:N — copy all person links.
                        for pp in src.store.query_by(m.PhotoPerson, item_id=it.id):
                            tgt.store.upsert(pp)
                conn.execute("UPDATE event SET updated_at = ? WHERE id = 1", (self._now(),))

            # 4. Now safely remove the days (files + records) from the source.
            moved_items = 0
            for dn, items in plan:
                src.delete_day(dn)
                moved_items += sum(1 for it in items if it.origin_relpath)
            log.info("move_days: %d day(s), %d item(s) %s → %s",
                     len(plan), moved_items, source_event_id, target_event_id)
            return {"moved_days": len(plan), "moved_items": moved_items}
        finally:
            tgt.close()
            src.close()
