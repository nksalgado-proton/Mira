"""The Cut picking session — the SEPARATE ledger (spec/61 §2 steps 6-7).

A session is a configured draft + the resolved pool, with Pick/Skip
decisions held **in memory per exported FILE**. The phase decisions the
user made in Pick (``phase_state``) are never read or written here —
same surfaces, different ledger.

Nothing persists until **Create Cut**: :meth:`CutSession.commit` writes
the cut row + the replace-all membership in one go, so an abandoned
session leaves no orphan rows (the dialog hands over a draft, not a
cut). Re-entering an existing Cut (spec/61 §5: changing a Cut =
re-enter the creation session) goes through :meth:`CutSession.for_cut`,
which re-resolves the recipe against today's pool and seeds decisions
from the committed membership.

No Qt (charter invariant 8); the UI page drives this object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from core import cut_budget

PoolExpr = Tuple[Tuple[str, str], ...]


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
    """Join lineage rows to session cells: each row resolved to its
    source item (or, for bracket-sourced exports, the stack's merged
    output item) for kind / day / time / duration. Preserves the rows'
    order."""
    by_id = {it.id: it for it in gateway.items()}
    out_of_bracket = {}
    for br in gateway.stacks():
        if br.output_item_id:
            out_of_bracket[br.bracket_id] = by_id.get(br.output_item_id)
    files: List[SessionFile] = []
    for ln in rows:
        src = by_id.get(ln.source_item_id) if ln.source_item_id else \
            out_of_bracket.get(ln.source_bracket_id)
        if src is None:
            files.append(SessionFile(export_relpath=ln.export_relpath))
            continue
        files.append(SessionFile(
            export_relpath=ln.export_relpath,
            kind=src.kind,
            day_number=src.day_number,
            capture_time=src.capture_time_corrected,
            duration_ms=int(src.duration_ms or 0) if src.kind == "video" else 0,
            source_item_id=src.id,
        ))
    return files


def session_files(
    gateway,
    pool_expr: Sequence[Tuple[str, str]],
    *,
    style_filter: Sequence[str] = (),
    type_filter: str = "both",
) -> List[SessionFile]:
    """Resolve a pool into session cells, in chronological show order.
    Two exports of one photo are two distinct cells — the file-based
    universe, spec/61 §1.2."""
    rows = gateway.resolve_pool(
        pool_expr, style_filter=style_filter, type_filter=type_filter)
    return files_from_lineage(gateway, rows)


def show_entries(gateway, cut, *, separators_on: bool) -> List[Tuple[str, object]]:
    """One Cut as THE SHOW: the ``("opener", None)`` title slide first
    (the Cut's name + facts — Nelson eyeball round 2), then ``("sep",
    day_number)`` at every day boundary, interleaved with ``("file",
    SessionFile)`` in chronological order. All card slides ride the
    separators setting. The flat grid, the rehearsal player, and the
    export walk this same sequence — WYSIWYG by construction."""
    files = files_from_lineage(gateway, gateway.cut_member_files(cut.id))
    entries: List[Tuple[str, object]] = []
    if separators_on and files:
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
    """The in-memory session: draft fields + ordered files + decisions.

    ``cut_id`` is ``None`` for a fresh session (commit creates) and the
    existing id when re-entering a Cut (commit updates settings +
    membership; the tag is NOT renamed here — renaming is a list-page
    action, spec/61 §3)."""

    name: str
    pool_expr: PoolExpr
    style_filter: Tuple[str, ...]
    type_filter: str
    default_state: str                      # 'picked' | 'skipped'
    target_s: Optional[int]
    max_s: Optional[int]
    photo_s: float
    music_category: Optional[str]
    files: Tuple[SessionFile, ...]
    separators_on: bool = True
    card_style: str = "black"               # 'black' | 'single' | 'multi'
    cut_id: Optional[str] = None
    _picked: dict = field(default_factory=dict, repr=False)
    _undo: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self._picked:
            start = self.default_state == "picked"
            self._picked = {f.export_relpath: start for f in self.files}

    # ── the ledger ───────────────────────────────────────────────────

    def is_picked(self, relpath: str) -> bool:
        return bool(self._picked.get(relpath, False))

    def set_state(self, relpath: str, picked: bool) -> None:
        """Pick (True) / Skip (False) one file, with undo recorded. A
        no-op when the file isn't in this session's pool."""
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
        """Revert the most recent decision; returns its relpath (the
        surface scrolls back to it) or ``None`` when nothing to undo."""
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
        """Files grouped by day in show order (undated last, as one
        group) — the session's days panel + grid sections."""
        groups: List[Tuple[Optional[int], List[SessionFile]]] = []
        for f in self.files:
            if groups and groups[-1][0] == f.day_number:
                groups[-1][1].append(f)
            else:
                groups.append((f.day_number, [f]))
        return groups

    def totals(self) -> cut_budget.ShowTotals:
        """Budget composition of the CURRENT picks — feeds the live
        green/amber/red line. Separators = distinct picked days (one
        card per day, spec/61 §4), zeroed when the setting is off."""
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

    def show_seconds(self) -> float:
        return self.totals().seconds(self.photo_s)

    def zone(self) -> str:
        return cut_budget.zone(self.show_seconds(), self.target_s, self.max_s)

    # ── the one persistence moment ───────────────────────────────────

    def commit(self, gateway):
        """Create Cut (spec/61 §2 step 7): write the definition + the
        replace-all membership. Fresh session → ``create_cut`` (the
        gateway re-validates the name); re-entered session → settings
        update + membership replace. Returns the cut row."""
        import json

        picked = [f.export_relpath for f in self.picked_files()]
        if self.cut_id is None:
            cut = gateway.create_cut(
                self.name,
                target_s=self.target_s, max_s=self.max_s,
                photo_s=self.photo_s,
                pool_expr=list(self.pool_expr),
                style_filter=list(self.style_filter),
                type_filter=self.type_filter,
                default_state=self.default_state,
                music_category=self.music_category,
                card_style=self.card_style,
            )
        else:
            current = gateway.cut(self.cut_id)
            # The dialog-first edit flow (Nelson 2026-06-12) can change
            # the NAME too — rename first (the gateway validates).
            from core import cut_names as _names
            if current is not None and \
                    _names.slugify(self.name) != current.tag:
                gateway.rename_cut(self.cut_id, self.name)
            gateway.update_cut_settings(
                self.cut_id,
                target_s=self.target_s, max_s=self.max_s,
                photo_s=self.photo_s,
                pool_expr_json=json.dumps([list(t) for t in self.pool_expr]),
                style_filter_json=json.dumps(list(self.style_filter)),
                type_filter=self.type_filter,
                default_state=self.default_state,
                music_category=self.music_category,
                card_style=self.card_style,
            )
            cut = gateway.cut(self.cut_id)
        gateway.set_cut_members(cut.id, picked)
        return cut

    # ── constructors ─────────────────────────────────────────────────

    @classmethod
    def from_draft(cls, gateway, draft, *, separators_on: bool = True) -> "CutSession":
        """A fresh session from the New Cut dialog's draft."""
        files = session_files(
            gateway, draft.pool_expr,
            style_filter=draft.style_filter, type_filter=draft.type_filter)
        return cls(
            name=draft.name,
            pool_expr=tuple(draft.pool_expr),
            style_filter=tuple(draft.style_filter),
            type_filter=draft.type_filter,
            default_state=draft.default_state,
            target_s=draft.target_s, max_s=draft.max_s,
            photo_s=draft.photo_s,
            music_category=draft.music_category,
            files=tuple(files),
            separators_on=separators_on,
            card_style=getattr(draft, "card_style", "black"),
        )

    @classmethod
    def for_cut_with_draft(
        cls, gateway, cut, draft, *, separators_on: bool = True,
    ) -> "CutSession":
        """The dialog-first EDIT flow (Nelson 2026-06-12): an existing
        Cut re-entered through the dialog — the session carries the
        dialog's NEW recipe (pool/filters/times/name/cards may all have
        changed), the pool resolves against it, and decisions seed from
        the committed membership (stray members appended, never
        dropped). Commit updates settings (+rename) + membership."""
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
    def for_cut(cls, gateway, cut, *, separators_on: bool = True) -> "CutSession":
        """Re-enter an existing Cut: the recipe re-resolves against
        today's pool (new exports appear, deleted ones are gone), and
        decisions seed from the committed membership — members picked,
        the rest skipped. Committed members the recipe no longer covers
        (recipe edited, a source Cut deleted, an empty recipe) are
        APPENDED to the session pool — re-entering must never silently
        drop what the user already picked."""
        pool_expr = tuple(gateway.cut_pool_expr(cut))
        style_filter = tuple(gateway.cut_style_filter(cut))
        files = session_files(
            gateway, pool_expr,
            style_filter=style_filter, type_filter=cut.type_filter)
        member_rows = gateway.cut_member_files(cut.id)
        have = {f.export_relpath for f in files}
        stray = [ln for ln in member_rows if ln.export_relpath not in have]
        if stray:
            files = sorted(
                files + files_from_lineage(gateway, stray),
                key=lambda f: (f.capture_time or "", f.export_relpath))
        members = {ln.export_relpath for ln in member_rows}
        session = cls(
            name=cut.tag,
            pool_expr=pool_expr,
            style_filter=style_filter,
            type_filter=cut.type_filter,
            default_state=cut.default_state,
            target_s=cut.target_s, max_s=cut.max_s,
            photo_s=cut.photo_s,
            music_category=cut.music_category,
            files=tuple(files),
            separators_on=separators_on,
            card_style=gateway.cut_card_style(cut),
            cut_id=cut.id,
        )
        session._picked = {
            f.export_relpath: f.export_relpath in members for f in files}
        return session
