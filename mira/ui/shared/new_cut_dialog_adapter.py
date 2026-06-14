"""Adapter — legacy ``NewCutDialog`` ctor surface over the redesigned dialog.

``mira/ui/pages/new_cut_dialog.py`` is the redesigned Surface 13 (the indigo
design system look + pool formula display + the cut-glyph header). The
legacy dialog at ``mira/ui/shared/new_cut_dialog.py`` is still wired by
``cuts_shell.py`` and is tested directly; the call-site contract is its
7-keyword ctor + an ``exec()`` + a ``draft()`` returning :class:`CutDraft`.

This module gives the redesigned page that same call-site contract so
``cuts_shell.py`` can swap one import (and one import only) to land the
new dialog live without:

* touching the redesigned page (engines stay reusable),
* touching the legacy module (tests stay green),
* touching :class:`CutDraft` (the downstream
  ``CutSession.from_draft`` / ``for_cut_with_draft`` contract is unchanged).

The translation work lives in two helpers:

* :func:`_build_context` — legacy kwargs + an optional ``prefill`` → a
  :class:`mira.ui.pages.new_cut_dialog.NewCutContext`.
* :meth:`NewCutDialog.draft` — the redesigned ``cut_info()`` dict back into
  a :class:`CutDraft`, honoring ``pool_probe`` for the file count and the
  legacy slug/sign-conversion shapes.

A few legacy features are stubbed for now (Load template…, Save as
template…); they'll be filled in once the redesigned page grows its own
template store-front. Until then the adapter just hides the buttons
when there are no templates / no saver, matching the legacy's quiet
empty state.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional, Sequence, Tuple

from PyQt6.QtWidgets import QDialog, QWidget

from mira.ui.pages.new_cut_dialog import (
    NewCutContext,
    NewCutDialog as _RedesignedNewCutDialog,
    PoolOption,
)
from mira.ui.shared.new_cut_dialog import CutDraft

log = logging.getLogger(__name__)


# spec/61 reserved tag — the exported-files baseline pool always sits at
# the front of the available list. Mirrors core.cut_names.EXPORTED_TAG.
_EXPORTED_TAG = "exported"


# ── translation helpers ─────────────────────────────────────────────────


def _slug(name: str) -> str:
    """Mirror the legacy slug helper: lowercase, non-alnum → underscore,
    collapse repeats. Used when the redesigned dialog returns ``cut_info``
    without a tag of its own."""
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "untitled"


def _pool_expr_from_counts(counts: dict) -> Tuple[Tuple[str, str], ...]:
    """Translate the redesigned ``{"#name": signed_mult}`` shape into the
    legacy ``((op, tag), ...)`` shape — signed multiplier becomes that
    many ``(+, tag)`` or ``(−, tag)`` tuples."""
    out: list[Tuple[str, str]] = []
    for prefixed, mult in counts.items():
        mult = int(mult)
        if mult == 0:
            continue
        tag = prefixed.lstrip("#")
        op = "+" if mult > 0 else "−"
        out.extend([(op, tag)] * abs(mult))
    return tuple(out)


def _selected_pools_from_expr(
    expr: Sequence[Tuple[str, str]],
) -> dict:
    """Inverse — fold a legacy expression into the redesigned signed-mult
    dict so prefill round-trips."""
    counts: dict[str, int] = {}
    for op, tag in expr:
        key = f"#{tag}"
        counts[key] = counts.get(key, 0) + (1 if op == "+" else -1)
    return counts


def _build_context(
    *,
    existing_cuts: Sequence[Tuple[str, int]],
    exported_count: int,
    style_options: Sequence[str],
    music_categories: Sequence[str],
    music_hint: Optional[str],
    event_label: str,
    prefill: Optional[object],
) -> NewCutContext:
    """Compose a :class:`NewCutContext` from the legacy seven-key kwargs
    plus an optional ``prefill`` (Edit mode). ``existing_cuts`` carries
    ``(tag, count)`` tuples; the redesigned dialog wants prefixed names
    (``#tag``) so the formula reads naturally."""
    available: list[PoolOption] = [
        PoolOption(name=f"#{_EXPORTED_TAG}", count=int(exported_count or 0)),
    ]
    for tag, n in existing_cuts:
        available.append(PoolOption(name=f"#{tag}", count=int(n or 0)))

    ctx = NewCutContext(
        event_name=event_label or "",
        available_pools=available,
        styles=list(style_options or []),
        music_options=["(no music)"] + list(music_categories or []),
        music_choice="(no music)",
    )

    # Default for New Cut: start from the exported pool with +1; the user
    # composes from there.
    ctx.selected_pools = [f"#{_EXPORTED_TAG}"]

    if prefill is not None:
        _apply_prefill(ctx, prefill, music_categories)
    return ctx


def _apply_prefill(
    ctx: NewCutContext, prefill: object,
    music_categories: Sequence[str],
) -> None:
    """Edit-mode prefill (``cuts_shell._on_adjust_cut`` builds a
    ``SimpleNamespace`` from the cut row). JSON fields are decoded; the
    legacy boolean/state vocabulary is mapped to the redesigned strings.
    Any missing field falls back to the dataclass default."""
    name = getattr(prefill, "name", "") or ""
    if name:
        # The redesigned dialog reads ctx.event_name for the header only;
        # the prefill's name goes into the Name field which the dialog
        # populates from the ctx fields it owns. We don't have a direct
        # field for the cut's own name on the context — store on the ctx
        # anyway so a follow-up read can pick it up. (The redesigned
        # dialog's Name input is empty by default; the caller can write
        # `dlg._name_edit.setText(name)` after construction.)
        ctx.prefill_name = name  # type: ignore[attr-defined]

    pool_json = getattr(prefill, "pool_expr_json", None)
    if pool_json:
        try:
            expr = [tuple(t) for t in json.loads(pool_json)]
            ctx.selected_pools = list(_selected_pools_from_expr(expr).keys())
            # Stash the signed-mult dict so the dialog's _pool_counts is
            # seeded by the construct path (read in _build_ui).
            ctx.prefill_pool_counts = _selected_pools_from_expr(expr)  # type: ignore[attr-defined]
        except (TypeError, ValueError):
            log.warning("could not parse prefill pool_expr_json: %r", pool_json)

    style_json = getattr(prefill, "style_filter_json", None)
    if style_json:
        try:
            ctx.selected_styles = list(json.loads(style_json))
        except (TypeError, ValueError):
            log.warning("could not parse prefill style_filter_json: %r", style_json)

    type_filter = getattr(prefill, "type_filter", "both") or "both"
    ctx.include_photos = type_filter in ("both", "photo")
    ctx.include_videos = type_filter in ("both", "video")

    state = getattr(prefill, "default_state", "skipped") or "skipped"
    ctx.start_as = "all_picked" if state == "picked" else "all_skipped"

    target_s = getattr(prefill, "target_s", None)
    if target_s is not None:
        ctx.target_minutes = max(1, int(round(int(target_s) / 60)))
    max_s = getattr(prefill, "max_s", None)
    if max_s is not None:
        ctx.max_minutes = max(1, int(round(int(max_s) / 60)))
    photo_s = getattr(prefill, "photo_s", None)
    if photo_s is not None:
        try:
            ctx.per_photo_seconds = float(photo_s)
        except (TypeError, ValueError):
            pass

    music_category = getattr(prefill, "music_category", None)
    if music_category and music_category in (music_categories or ()):
        ctx.music_choice = music_category

    card_style = getattr(prefill, "card_style", "black") or "black"
    ctx.slide_cards = {
        "black": "all_black",
        "single": "one_random",
        "multi": "per_day",
    }.get(card_style, "all_black")


def _type_filter_from(include_photos: bool, include_videos: bool) -> str:
    """Legacy schema: ``type_filter`` is a tri-state string."""
    if include_photos and include_videos:
        return "both"
    if include_photos:
        return "photo"
    if include_videos:
        return "video"
    return "both"  # neither checked → treat as "no filter" rather than empty


def _card_style_from_slide(slide_cards: str) -> str:
    return {
        "all_black": "black",
        "one_random": "single",
        "per_day": "multi",
    }.get(slide_cards, "black")


# ── the adapter ─────────────────────────────────────────────────────────


class NewCutDialog:
    """Drop-in adapter exposing the legacy ``NewCutDialog`` surface
    (``exec`` + ``draft``) over the redesigned page.

    Constructor signature mirrors
    :class:`mira.ui.shared.new_cut_dialog.NewCutDialog` exactly so
    ``cuts_shell.py`` keeps its current calls unchanged. The redesigned
    dialog is created lazily on first ``exec()`` so test stubs that
    patch ``_exec_edit_dialog`` never instantiate the underlying widget.
    """

    def __init__(
        self,
        *,
        existing_cuts: Sequence[Tuple[str, int]],
        exported_count: int,
        style_options: Sequence[str] = (),
        music_categories: Sequence[str] = (),
        pool_probe: Optional[Callable[[list], int]] = None,
        totals_probe: Optional[Callable] = None,
        event_label: str = "",
        separators_on: bool = True,
        templates: Sequence[object] = (),
        template_saver: Optional[Callable[[str, CutDraft], None]] = None,
        music_hint: Optional[str] = None,
        prefill: Optional[object] = None,
        heading_text: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        self._pool_probe = pool_probe
        self._music_categories = tuple(music_categories or ())
        self._separators_on = bool(separators_on)
        self._prefill = prefill
        # Templates + template_saver are accepted but not yet surfaced in
        # the redesigned page (TODO: spec/65 §3.13 template strip). Stub
        # them so the adapter swallows the kwargs without complaint.
        self._templates = list(templates or [])
        self._template_saver = template_saver

        self._ctx = _build_context(
            existing_cuts=existing_cuts,
            exported_count=exported_count,
            style_options=style_options,
            music_categories=music_categories,
            music_hint=music_hint,
            event_label=event_label,
            prefill=prefill,
        )
        self._heading_text = heading_text
        self._parent = parent
        self._dlg: Optional[_RedesignedNewCutDialog] = None
        self._was_accepted = False

    # ── public surface ────────────────────────────────────────────────

    def exec(self) -> int:
        self._build()
        result = self._dlg.exec()
        self._was_accepted = result == QDialog.DialogCode.Accepted
        return result

    def draft(self) -> CutDraft:
        """Read the redesigned dialog's ``cut_info()`` and translate back
        to :class:`CutDraft`. ``pool_probe`` is honored for the file
        count when present; we don't need to surface the count on the
        draft itself (the legacy doesn't either) but we ensure it can be
        called without error so future surfaces relying on the probe
        still wire."""
        if self._dlg is None:
            # Edge case — caller never exec'd; produce a sensible default.
            self._build()
        info = self._dlg.cut_info()
        pool_expr = _pool_expr_from_counts(info.get("pool", {}))
        name = info.get("name", "").strip()
        if self._pool_probe is not None and pool_expr:
            try:
                self._pool_probe([list(t) for t in pool_expr])
            except Exception:                                      # noqa: BLE001
                log.exception("pool_probe raised — ignoring (count unused)")
        target_min = int(info.get("target_minutes", 0) or 0)
        max_min = int(info.get("max_minutes", 0) or 0)
        per_photo = float(info.get("per_photo_seconds", 0.0) or 0.0)
        music_choice = str(info.get("music", "(no music)"))
        if music_choice == "(no music)":
            music_category: Optional[str] = None
        else:
            music_category = music_choice
        return CutDraft(
            name=name,
            tag=_slug(name),
            pool_expr=pool_expr,
            style_filter=tuple(info.get("styles", ()) or ()),
            type_filter=_type_filter_from(
                bool(info.get("include_photos", True)),
                bool(info.get("include_videos", True)),
            ),
            default_state=(
                "picked" if info.get("start_as") == "all_picked"
                else "skipped"
            ),
            target_s=(target_min * 60) if target_min > 0 else None,
            max_s=(max_min * 60) if max_min > 0 else None,
            photo_s=per_photo,
            music_category=music_category,
            card_style=_card_style_from_slide(
                info.get("slide_cards", "all_black")),
        )

    # ── construction ──────────────────────────────────────────────────

    def _build(self) -> None:
        if self._dlg is not None:
            return
        self._dlg = _RedesignedNewCutDialog(ctx=self._ctx, parent=self._parent)
        if self._heading_text:
            self._dlg.setWindowTitle(self._heading_text)
        # Prefill round-trip: seed the redesigned dialog's internal state
        # from the fields we stashed on the ctx (the ctx dataclass has no
        # name field, so the adapter stashes via __dict__ in _apply_prefill).
        prefill_name = getattr(self._ctx, "prefill_name", "")
        if prefill_name:
            self._dlg._name_edit.setText(prefill_name)
        prefill_counts = getattr(self._ctx, "prefill_pool_counts", None)
        if prefill_counts:
            self._dlg._pool_counts = dict(prefill_counts)
            self._dlg._refresh_selected_chips()
            self._dlg._refresh_pool_summary()
            self._dlg._refresh_start_enabled()


__all__ = ["NewCutDialog", "CutDraft"]
