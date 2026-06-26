"""The pin session — DC → Cut on a SEPARATE ledger (spec/81 §4, spec/61 §2).

**pin** (spec/81 §4) freezes a DC's live resolution into a Cut's stored
members. The session sources its candidate set from a **DC resolution**
(:meth:`EventGateway.resolve_dc`) and holds Pick/Skip decisions **in memory
per exported FILE**. The phase decisions the user made in Pick
(``phase_state``) are never read or written here — same surfaces, different
ledger (spec/61 §2).

Pin modes (spec/81 §4 / spec/80 §2):
  * **keep-all** — pin the DC 1:1, no session: every resolved file is a
    member, no skipping. :meth:`CutSession.from_draft` returns a session with
    everything picked and the keep-all flag set (the UI may skip the picker).
  * **weed-out** — start all-in (default Pick), skip rejects down to budget.
  * **pick-in** — start all-out (default Skip), pick keepers up to budget.

Nothing persists until **Create Cut**: :meth:`CutSession.commit` writes the
cut row + the replace-all membership AND the frozen ``expr_snapshot_json`` in
one go (spec/81 §5 — the Cut never re-queries its DC live). An abandoned
session leaves no orphan rows. Re-entering an existing Cut re-resolves the
source DC against today's universe and seeds decisions from the committed
membership.

No Qt (charter invariant 8); the UI page drives this object.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from core import cut_budget
from mira.shared.cut_draft import (
    PIN_KEEP_ALL,
    PIN_PICK_IN,
    PIN_RULE_BASED,
    PIN_WEED_OUT,
)

#: A DC expression: ordered ``(op, operand)`` pairs (spec/81 §2).
Expr = Tuple[Tuple[str, Any], ...]


@dataclass(frozen=True)
class SessionFile:
    """One cell of the session — an exported FILE resolved to the source
    facts the surfaces need (kind for Play/duration, day for grouping +
    separators, capture time for order)."""

    export_relpath: str
    kind: str = "photo"                    # 'photo' | 'video'
    day_number: Optional[int] = None
    capture_time: Optional[str] = None
    duration_ms: int = 0                   # true clip length; 0 for photos
    source_item_id: Optional[str] = None


def files_from_lineage(gateway, rows) -> List[SessionFile]:
    """Join lineage rows to session cells: each row resolved to its source
    item (or, for bracket-sourced exports, the stack's merged output item) for
    kind / day / time / duration. Preserves the rows' order.

    spec/144 — for **video** members, ``SessionFile.duration_ms`` MUST be
    the exported **clip segment's** true length, NEVER the source video's
    whole duration. Resolution order:

    1. ``lineage.duration_ms`` if recorded at export (the render worker
       emits ``(out_ms - in_ms) / speed`` on the clip "ok" message; the
       lineage writer persists it).
    2. ffprobe the on-disk file (``probe_video``) — robust for legacy
       pre-migration lineage rows whose ``duration_ms`` is NULL. Sole
       fallback; we never use the source's whole-video length here.
    3. ``0`` — the file is unreachable or unparseable. The scrubber
       reads this as "use ``photo_ms``" so the show still advances on
       :data:`QMediaPlayer.MediaStatus.EndOfMedia`."""
    by_id = {it.id: it for it in gateway.items()}
    out_of_bracket = {}
    for br in gateway.stacks():
        if br.output_item_id:
            out_of_bracket[br.bracket_id] = by_id.get(br.output_item_id)
    files: List[SessionFile] = []
    event_root = getattr(gateway, "event_root", None)
    for ln in rows:
        src = by_id.get(ln.source_item_id) if ln.source_item_id else \
            out_of_bracket.get(ln.source_bracket_id)
        if src is None:
            files.append(SessionFile(export_relpath=ln.export_relpath))
            continue
        if src.kind == "video":
            duration_ms = _clip_segment_duration_ms(ln, event_root)
        else:
            duration_ms = 0
        files.append(SessionFile(
            export_relpath=ln.export_relpath,
            kind=src.kind,
            day_number=src.day_number,
            capture_time=src.capture_time_corrected,
            duration_ms=duration_ms,
            source_item_id=src.id,
        ))
    return files


def _clip_segment_duration_ms(lineage_row, event_root) -> int:
    """spec/144 — the clip's TRUE on-disk duration. Prefers the
    persisted lineage column; falls back to ffprobing the file.

    Never returns the source video's whole ``duration_ms`` — that's
    the source-of-truth bug spec/144 exists to fix. A probe failure
    (file missing, codec unreadable) returns ``0``; the cut-play
    scrubber treats ``0`` as "use ``photo_ms`` for layout" and
    advance still rides ``EndOfMedia``."""
    raw = getattr(lineage_row, "duration_ms", None)
    if isinstance(raw, (int, float)) and int(raw) > 0:
        return int(raw)
    if event_root is None:
        return 0
    from pathlib import Path
    path = Path(event_root) / lineage_row.export_relpath
    if not path.is_file():
        return 0
    try:
        from core.video_extract import probe_video
        meta = probe_video(path)
    except Exception:                                              # noqa: BLE001
        # The probe layer logs; we stay silent and report zero so
        # the calling surface degrades gracefully.
        return 0
    duration = int(getattr(meta, "duration_ms", 0) or 0)
    return duration if duration > 0 else 0


def session_files(
    gateway,
    expr: Sequence[Sequence],
    *,
    filters: Optional[Mapping] = None,
) -> List[SessionFile]:
    """Resolve a DC formula into session cells, in chronological show order.
    Two exports of one photo are two distinct cells — the file-based universe,
    spec/61 §1.2."""
    rows = gateway.resolve_dc(expr, filters)
    return files_from_lineage(gateway, rows)


def show_entries(gateway, cut, *, separators_on: bool) -> List[Tuple[str, object]]:
    """One Cut as THE SHOW: the ``("opener", None)`` title slide first (the
    Cut's name + facts), then ``("sep", day_number)`` at every day boundary,
    interleaved with ``("file", SessionFile)`` in chronological order. The
    flat grid, the rehearsal player, and the export walk this same sequence
    — WYSIWYG by construction.

    **The opener is the title of the show, not a day separator.** It rides
    every Cut that has at least one file, regardless of ``separators_on``;
    the toggle only gates the per-day boundary cards. The pre-fix path
    coupled both under the same flag, so turning separators OFF dropped
    the title card too — exactly the user's "Separators OFF should keep
    the initial header" report."""
    files = files_from_lineage(gateway, gateway.cut_member_files(cut.id))
    entries: List[Tuple[str, object]] = []
    if files:
        entries.append(("opener", None))
    last_day: object = object()
    for f in files:
        if separators_on and f.day_number != last_day:
            last_day = f.day_number
            entries.append(("sep", f.day_number))
        entries.append(("file", f))
    return entries


@dataclass
class CutSession:
    """The in-memory pin session: draft fields + ordered files + decisions.

    ``cut_id`` is ``None`` for a fresh session (commit creates) and the
    existing id when re-entering a Cut (commit updates settings + membership;
    the tag is NOT renamed here — renaming is a list-page action). ``expr`` +
    ``filters`` are the DC formula the session resolved from; they freeze into
    the Cut's ``expr_snapshot_json`` on commit (spec/81 §5). ``keep_all`` marks
    a 1:1 pin (no real skipping)."""

    name: str
    expr: Expr
    filters: Mapping[str, Any]
    pin_mode: str                           # keep-all | weed-out | pick-in
    target_s: Optional[int]
    max_s: Optional[int]
    photo_s: float
    music_category: Optional[str]
    files: Tuple[SessionFile, ...]
    source_dc_id: Optional[str] = None
    # spec/152 §3 — per-Cut crossfade transition (ms). ``None`` defers
    # to ``Settings.default_transition_ms`` at read time.
    transition_ms: Optional[int] = None
    separators_on: bool = True
    overlay_fields: Tuple[str, ...] = ()
    overlay_mode: Optional[str] = None
    card_style: str = "black"               # 'black' | 'single' | 'multi'
    # spec/111 — slideshow canvas aspect (sibling to ``photo_s``).
    aspect: str = "16:9"
    cut_id: Optional[str] = None
    #: spec/94 Phase 3 — Rules/Otherwise seed verdicts (export_relpath →
    #: picked) computed by the dialog's :class:`recipe_resolver` call. When
    #: non-empty it overlays the :attr:`pin_mode` default in
    #: :meth:`__post_init__`, so a rule-based Recipe opens the session with
    #: pick/skip already partitioned. Files NOT in the seed fall back to the
    #: pin_mode default.
    seed: Mapping[str, bool] = field(default_factory=dict)
    _picked: dict = field(default_factory=dict, repr=False)
    _undo: list = field(default_factory=list, repr=False)

    @property
    def keep_all(self) -> bool:
        return self.pin_mode == PIN_KEEP_ALL

    def __post_init__(self) -> None:
        if not self._picked:
            # keep-all + weed-out start all-in; pick-in starts all-out.
            start = self.pin_mode in (PIN_KEEP_ALL, PIN_WEED_OUT)
            self._picked = {f.export_relpath: start for f in self.files}
            # spec/94 Phase 3 — the Recipe's Rules/Otherwise verdicts overlay
            # the pin_mode default: a rule-based draft seeds each member
            # individually so the picker opens pre-curated. Files outside the
            # seed (or with seed=None) keep the pin_mode default. The map is
            # a plain dict, not a defaultdict — missing keys are not picked
            # at random.
            if self.seed:
                for relpath, picked in self.seed.items():
                    if relpath in self._picked:
                        self._picked[relpath] = bool(picked)

    # ── the ledger ───────────────────────────────────────────────────

    def is_picked(self, relpath: str) -> bool:
        return bool(self._picked.get(relpath, False))

    def set_state(self, relpath: str, picked: bool) -> None:
        """Pick (True) / Skip (False) one file, with undo recorded. A
        no-op when the file isn't in this session's candidate set."""
        if relpath not in self._picked:
            return
        prev = self._picked[relpath]
        if prev == picked:
            return
        self._undo.append((relpath, prev))
        self._picked[relpath] = picked

    def toggle(self, relpath: str) -> bool:
        """Flip one file's state; returns the new state."""
        new = not self.is_picked(relpath)
        self.set_state(relpath, new)
        return new

    def undo(self) -> Optional[str]:
        """Revert the most recent decision; returns its relpath (the surface
        scrolls back to it) or ``None`` when nothing to undo."""
        if not self._undo:
            return None
        relpath, prev = self._undo.pop()
        self._picked[relpath] = prev
        return relpath

    # ── reads the surfaces render from ───────────────────────────────

    def picked_files(self) -> List[SessionFile]:
        return [f for f in self.files if self.is_picked(f.export_relpath)]

    def picked_count(self) -> int:
        return sum(1 for f in self.files if self.is_picked(f.export_relpath))

    def days(self) -> List[Tuple[Optional[int], List[SessionFile]]]:
        """Files grouped by day in show order (undated last, as one group) —
        the session's days panel + grid sections."""
        groups: List[Tuple[Optional[int], List[SessionFile]]] = []
        for f in self.files:
            if groups and groups[-1][0] == f.day_number:
                groups[-1][1].append(f)
            else:
                groups.append((f.day_number, [f]))
        return groups

    def totals(self) -> cut_budget.ShowTotals:
        """Budget composition of the CURRENT picks — feeds the live
        green/amber/red line. Separators = distinct picked days (one card per
        day, spec/61 §4), zeroed when the setting is off."""
        photos = videos = video_ms = 0
        days = set()
        for f in self.picked_files():
            if f.kind == "video":
                videos += 1
                video_ms += f.duration_ms
            else:
                photos += 1
            if f.day_number is not None:
                days.add(f.day_number)
        return cut_budget.ShowTotals(
            photo_count=photos,
            video_count=videos,
            separator_count=len(days) if self.separators_on else 0,
            video_ms_total=video_ms,
        )

    def show_seconds(self, transition_s: float = 0.0) -> float:
        """spec/152 §3 — the show's projected wall time. Adds
        ``transition_s`` to every non-video slide slot, counts ONE
        opener whenever the Cut has at least one picked file (the
        opener is the title slide — it rides every Cut regardless of
        the ``separators_on`` toggle), and adds the per-day separator
        cards only when separators are on. When the caller doesn't
        have a global default to inject, ``transition_s`` falls back
        to the per-session ``transition_ms`` (the user's per-Cut
        override) or 0 — picker hosts pass an effective value so the
        bar matches the rehearsal."""
        totals = self.totals()
        has_files = bool(self.picked_files())
        if has_files:
            from dataclasses import replace as _replace
            totals = _replace(totals, opener_count=1)
        # ``transition_s`` overrides the per-session value when the
        # host wants to factor the Settings global default in.
        if transition_s <= 0.0 and self.transition_ms is not None:
            transition_s = max(0.0, float(self.transition_ms) / 1000.0)
        return totals.seconds(self.photo_s, transition_s)

    def pool_seconds(self, transition_s: float = 0.0) -> float:
        """spec/152 §3 — show length for the FULL pool (every
        :class:`SessionFile`, regardless of pick state). The picker's
        budget bar anchors its scale to this value so the user can
        SEE the consumption (``show_seconds``) shrink as they unpick
        items past the target — the scale doesn't shrink with them.
        Matches the value the New / Adjust Cut dialog displays before
        any selections are made (= "all the media making the cut").
        """
        photos = videos = video_ms = 0
        days = set()
        for f in self.files:
            if f.kind == "video":
                videos += 1
                video_ms += f.duration_ms
            else:
                photos += 1
            if f.day_number is not None:
                days.add(f.day_number)
        from dataclasses import replace as _replace
        totals = cut_budget.ShowTotals(
            photo_count=photos,
            video_count=videos,
            separator_count=len(days) if self.separators_on else 0,
            video_ms_total=video_ms,
        )
        # Opener always rides when the pool is non-empty (the title
        # slide is independent of the separators toggle, mirroring
        # :meth:`show_seconds`).
        if self.files:
            totals = _replace(totals, opener_count=1)
        if transition_s <= 0.0 and self.transition_ms is not None:
            transition_s = max(0.0, float(self.transition_ms) / 1000.0)
        return totals.seconds(self.photo_s, transition_s)

    def zone(self, transition_s: float = 0.0) -> str:
        return cut_budget.zone(
            self.show_seconds(transition_s), self.target_s, self.max_s)

    # ── the one persistence moment ───────────────────────────────────

    def commit(self, gateway):
        """Create Cut (spec/81 §4-§5): write the frozen definition + the
        replace-all membership + the formula snapshot. Fresh session →
        ``create_cut`` (the gateway re-validates the name); re-entered session
        → settings update + membership replace. Returns the cut row."""
        picked = [f.export_relpath for f in self.picked_files()]
        expr_list = [list(t) for t in self.expr]
        if self.cut_id is None:
            cut = gateway.create_cut(
                self.name,
                source_dc_id=self.source_dc_id,
                expr_snapshot=expr_list,
                target_s=self.target_s, max_s=self.max_s,
                photo_s=self.photo_s,
                # spec/152 §3 — persist the per-Cut transition value
                # the dialog captured (None = defer to Settings).
                transition_ms=self.transition_ms,
                default_state=(
                    "picked" if self.pin_mode in (PIN_KEEP_ALL, PIN_WEED_OUT)
                    else "skipped"),
                music_category=self.music_category,
                separators=self.separators_on,
                overlay_fields=list(self.overlay_fields),
                overlay_mode=self.overlay_mode,
                card_style=self.card_style,
                aspect=self.aspect,
            )
        else:
            current = gateway.cut(self.cut_id)
            # The dialog-first edit flow can change the NAME too — rename first
            # (the gateway validates).
            from core import cut_names as _names
            if current is not None and \
                    _names.slugify(self.name) != current.tag:
                gateway.rename_cut(self.cut_id, self.name)
            gateway.update_cut_settings(
                self.cut_id,
                source_dc_id=self.source_dc_id,
                expr_snapshot_json=json.dumps(expr_list),
                target_s=self.target_s, max_s=self.max_s,
                photo_s=self.photo_s,
                # spec/152 §3 — same as create_cut above.
                transition_ms=self.transition_ms,
                default_state=(
                    "picked" if self.pin_mode in (PIN_KEEP_ALL, PIN_WEED_OUT)
                    else "skipped"),
                music_category=self.music_category,
                separators=self.separators_on,
                overlay_fields_json=json.dumps(list(self.overlay_fields)),
                overlay_mode=self.overlay_mode,
                card_style=self.card_style,
                aspect=self.aspect,
            )
            cut = gateway.cut(self.cut_id)
        gateway.set_cut_members(cut.id, picked)
        return cut

    # ── constructors ─────────────────────────────────────────────────

    @classmethod
    def from_draft(cls, gateway, draft, *, separators_on: Optional[bool] = None) -> "CutSession":
        """A fresh pin session from the New Cut dialog's draft. Sources its
        candidate set from the draft's DC (saved ``source_dc_id`` resolution OR
        the inline ad-hoc formula). ``separators_on`` defaults to the draft's
        own ``separators`` flag (spec/61 §4 default ON).

        spec/94 Phase 3 — when the draft carries a ``seed`` (the dialog's
        Recipe Rules/Otherwise verdicts), the session opens with each
        member's initial Pick/Skip already set. When the draft is rule-
        based but lacks an explicit seed (defensive — the dialog
        should always populate it), we derive the seed by calling the
        gateway's :meth:`resolve_recipe` so the legacy
        ``CutSession.from_draft(...)`` callers without a seed still
        light up correctly for rule-based Recipes.
        """
        expr, filters = cls._draft_expr_filters(gateway, draft)
        files = session_files(gateway, expr, filters=filters)
        seps = draft.separators if separators_on is None else separators_on
        seed = cls._resolve_draft_seed(gateway, draft, expr, filters)
        return cls(
            name=draft.name,
            expr=tuple(tuple(t) for t in expr),
            filters=dict(filters),
            pin_mode=getattr(draft, "pin_mode", PIN_WEED_OUT),
            source_dc_id=getattr(draft, "source_dc_id", None),
            target_s=draft.target_s, max_s=draft.max_s,
            photo_s=draft.photo_s,
            # spec/152 §3 — carry the per-Cut transition through to
            # commit. Drafts with None defer to the global default.
            transition_ms=getattr(draft, "transition_ms", None),
            music_category=draft.music_category,
            files=tuple(files),
            separators_on=seps,
            overlay_fields=tuple(getattr(draft, "overlay_fields", ()) or ()),
            overlay_mode=getattr(draft, "overlay_mode", None),
            card_style=getattr(draft, "card_style", "black"),
            aspect=getattr(draft, "aspect", "16:9"),
            seed=seed,
        )

    @classmethod
    def _resolve_draft_seed(
        cls, gateway, draft, expr, filters,
    ) -> Mapping[str, bool]:
        """Decide the per-member seed for a draft.

        Order:

        1. **Explicit seed on the draft.** The dialog computed it from
           :class:`recipe_resolver.RecipeResolution.seed` at Start time;
           the adapter ships it on :attr:`CutDraft.seed`.
        2. **Rule-based with no explicit seed.** Reconstruct a Recipe
           composition from the draft's rules + otherwise + source/
           filters and call :meth:`gateway.resolve_recipe` so the
           verdicts still land. A defensive path: in production the
           dialog supplies the seed, but legacy / test callers that
           skip it shouldn't silently fall through to the pin_mode
           default for rule-based drafts.
        3. **Legacy modes.** Return an empty mapping — the session's
           :meth:`__post_init__` keeps the pin_mode default.
        """
        existing = getattr(draft, "seed", ()) or ()
        if existing:
            # ``CutDraft.seed`` is a tuple of (relpath, picked) for
            # frozen-dataclass friendliness; CutSession holds a dict.
            return dict(existing)
        pin_mode = getattr(draft, "pin_mode", PIN_WEED_OUT)
        rules = getattr(draft, "rules", ()) or ()
        if pin_mode != PIN_RULE_BASED or not rules:
            return {}
        composition = {
            "source": [list(t) for t in expr],
            "filters": dict(filters),
            "rules": [
                {"predicate": [list(t) for t in r.predicate],
                 "verdict": r.verdict}
                for r in rules
            ],
            "otherwise": (
                getattr(draft, "otherwise", "")
                or ("pick" if pin_mode == PIN_WEED_OUT else "skip")
            ),
        }
        try:
            resolution = gateway.resolve_recipe(composition)
        except Exception:                                   # noqa: BLE001
            # Resolver failure is non-fatal here — the session opens with
            # the pin_mode default and the user can still curate. The
            # dialog's own probe surfaces the error to the user.
            return {}
        return dict(resolution.seed or {})

    @staticmethod
    def _draft_expr_filters(gateway, draft):
        """Resolve a draft to the (expr, filters) the session resolves from:
        an inline formula on the draft wins; otherwise the saved DC's stored
        formula. A saved DC with no inline override still keeps its own
        filters.

        spec/94 Phase 2 — when the draft's ``source_dc_id`` doesn't
        resolve in event.db, fall through to the file-based Collection
        library (spec/93 §6 GLOBAL ∪ BOUND-to-E). The EventGateway's
        cached library snapshot handles the lookup; if no library is
        wired (legacy / unit-test path) the fallback is a no-op and the
        draft's inline ``expr`` carries the load.
        """
        expr = tuple(tuple(t) for t in (getattr(draft, "expr", ()) or ()))
        filters = dict(getattr(draft, "filters", {}) or {})
        dc_id = getattr(draft, "source_dc_id", None)
        if not expr and dc_id:
            dc = gateway.dynamic_collection(dc_id)
            if dc is not None:
                expr = tuple(tuple(t) for t in gateway.dc_expr(dc))
                if not filters:
                    filters = gateway.dc_filters(dc)
            else:
                # File-library fallback (spec/94 Phase 2). The resolver
                # accessor returns a ``DCExpr`` carrying the file's
                # ``expr`` and ``filters``; reuse the same lookup so the
                # name-fallback contract (spec/93 §4) holds here too.
                library_dc = gateway._resolve_library_collection({
                    "kind": "dc", "id": dc_id,
                })
                if library_dc is not None:
                    expr = tuple(tuple(t) for t in library_dc.expr)
                    if not filters:
                        filters = dict(library_dc.filters)
        return expr, filters

    @classmethod
    def for_cut_with_draft(
        cls, gateway, cut, draft, *, separators_on: Optional[bool] = None,
    ) -> "CutSession":
        """The dialog-first EDIT flow: an existing Cut re-entered through the
        dialog — the session carries the dialog's NEW DC formula
        (expr/filters/times/name/cards may all have changed), the DC resolves
        against today's universe, and decisions seed from the committed
        membership (stray members appended, never dropped). Commit updates
        settings (+rename) + membership + the frozen snapshot."""
        session = cls.from_draft(gateway, draft, separators_on=separators_on)
        member_rows = gateway.cut_member_files(cut.id)
        have = {f.export_relpath for f in session.files}
        stray = [ln for ln in member_rows if ln.export_relpath not in have]
        files = list(session.files)
        if stray:
            files = sorted(
                files + files_from_lineage(gateway, stray),
                key=lambda f: (f.capture_time or "", f.export_relpath))
        members = {ln.export_relpath for ln in member_rows}
        session.files = tuple(files)
        session.cut_id = cut.id
        session._picked = {
            f.export_relpath: f.export_relpath in members for f in files}
        return session

    @classmethod
    def for_cut(cls, gateway, cut, *, separators_on: Optional[bool] = None) -> "CutSession":
        """Re-enter an existing Cut: its FROZEN formula re-resolves against
        today's universe (new exports appear, deleted ones are gone), and
        decisions seed from the committed membership — members picked, the rest
        skipped. Committed members the formula no longer covers (recipe edited,
        a source operand deleted, an empty recipe) are APPENDED to the session
        — re-entering must never silently drop what the user already picked.

        The Cut carries no filters of its own (they live on the DC); when the
        Cut still points at a live DC we re-resolve through that DC's current
        filters, else through the frozen snapshot with no filters."""
        expr = tuple(tuple(t) for t in gateway.cut_expr_snapshot(cut))
        filters: dict = {}
        if cut.source_dc_id:
            dc = gateway.dynamic_collection(cut.source_dc_id)
            if dc is not None:
                expr = tuple(tuple(t) for t in gateway.dc_expr(dc))
                filters = gateway.dc_filters(dc)
        files = session_files(gateway, expr, filters=filters)
        member_rows = gateway.cut_member_files(cut.id)
        have = {f.export_relpath for f in files}
        stray = [ln for ln in member_rows if ln.export_relpath not in have]
        if stray:
            files = sorted(
                files + files_from_lineage(gateway, stray),
                key=lambda f: (f.capture_time or "", f.export_relpath))
        members = {ln.export_relpath for ln in member_rows}
        seps = bool(cut.separators) if separators_on is None else separators_on
        session = cls(
            name=cut.tag,
            expr=expr,
            filters=filters,
            pin_mode=(PIN_WEED_OUT if cut.default_state == "picked"
                      else PIN_PICK_IN),
            source_dc_id=cut.source_dc_id,
            target_s=cut.target_s, max_s=cut.max_s,
            photo_s=cut.photo_s,
            # spec/152 §3 — re-entering a Cut surfaces its stored
            # per-Cut transition (if any) in the dialog.
            transition_ms=getattr(cut, "transition_ms", None),
            music_category=cut.music_category,
            files=tuple(files),
            separators_on=seps,
            overlay_fields=tuple(gateway.cut_overlay_fields(cut)),
            overlay_mode=cut.overlay_mode,
            card_style=gateway.cut_card_style(cut),
            aspect=getattr(cut, "aspect", "16:9"),
            cut_id=cut.id,
        )
        session._picked = {
            f.export_relpath: f.export_relpath in members for f in files}
        return session
