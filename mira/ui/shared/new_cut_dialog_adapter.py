"""Adapter — legacy ``NewCutDialog`` ctor surface over the redesigned dialog.

``mira/ui/pages/new_cut_dialog.py`` is the redesigned Surface 13 (the indigo
design system look + pool formula display + the cut-glyph header). The
legacy dialog at ``mira/ui/shared/new_cut_dialog.py`` is still wired by
``cuts_shell.py`` and is tested directly; the call-site contract is its
7-keyword ctor + an ``exec()`` + a ``draft()`` returning :class:`CutDraft`.

This module gives the redesigned page that same call-site contract so
``cuts_shell.py`` can swap one import (and one import only) to land the
new dialog live without:

* touching the legacy module (tests stay green),
* touching :class:`CutDraft` (the downstream
  ``CutSession.from_draft`` / ``for_cut_with_draft`` contract is unchanged).

The translation work lives in three helpers:

* :func:`_build_context` — legacy kwargs + an optional ``prefill`` → a
  :class:`mira.ui.pages.new_cut_dialog.NewCutContext`.
* :func:`_draft_from_info` — the redesigned ``cut_info()`` dict back into
  a :class:`CutDraft`. Reused by ``.draft()`` AND by the
  template-save wrapper so the host's ``template_saver`` always
  receives a :class:`CutDraft`-shaped object.
* :meth:`NewCutDialog._make_template_saver` — wraps the host saver so
  the redesigned dialog can fire it with its own ``(name, info_dict)``
  signature.

The pool probe + totals probe are passed straight through to the
redesigned dialog so the pool size and the match count read live off
the gateway on every change (no more multiplied-out static counts).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional, Sequence, Tuple

from PyQt6.QtWidgets import QDialog, QWidget

from mira.shared.cut_draft import (
    CutDraft, PIN_KEEP_ALL, PIN_PICK_IN, PIN_WEED_OUT,
)
from mira.ui.pages.new_cut_dialog import (
    NewCutContext,
    NewCutDialog as _RedesignedNewCutDialog,
    PoolOption,
)

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


def _expr_from_counts(counts: dict) -> Tuple[Tuple[str, Any], ...]:
    """Translate the dialog's ``{"#name": signed_mult}`` shape into the
    spec/81 DC operand encoding: ordered ``((op, operand), ...)`` where the
    base token ``"exported"`` stays bare and any user tag becomes a typed
    ``{"kind":"cut","id":None,"tag":...}`` ref. Signed multiplier expands
    to repeated terms (``+`` for >0, ``-`` for <0; ``-`` is the ASCII
    operator the resolver accepts)."""
    out: list[Tuple[str, Any]] = []
    for prefixed, mult in counts.items():
        mult = int(mult)
        if mult == 0:
            continue
        tag = prefixed.lstrip("#")
        op = "+" if mult > 0 else "-"
        operand: Any = tag if tag == "exported" else {
            "kind": "cut", "id": None, "tag": tag}
        out.extend([(op, operand)] * abs(mult))
    return tuple(out)


def _counts_from_expr(expr: Sequence[Sequence[Any]]) -> dict:
    """Inverse — fold a DC expression back into the dialog's signed-mult
    counts dict so a saved template round-trips. The bare base token
    ``"exported"`` and a ``{"kind":"cut|dc","tag":...}`` ref both surface as
    ``"#tag"`` keys (the dialog renders chips by tag — the kind is implicit
    in the operand inventory)."""
    counts: dict[str, int] = {}
    for pair in expr:
        try:
            op, operand = pair[0], pair[1]
        except (IndexError, TypeError):
            continue
        if isinstance(operand, str):
            tag = operand
        elif isinstance(operand, dict):
            tag = operand.get("tag") or ""
        else:
            continue
        if not tag:
            continue
        key = f"#{tag}"
        counts[key] = counts.get(key, 0) + (1 if op == "+" else -1)
    return counts


def _pin_mode_from_info(info: dict) -> str:
    """Derive the spec/81 pin mode (keep-all / weed-out / pick-in) from the
    dialog's emit. The dialog's 3-way Build mode (keep_all / weed_out /
    pick_in) maps 1:1; legacy emits without ``build_mode`` fall back to
    ``start_as`` ("all_picked" → weed-out, "all_skipped" → pick-in)."""
    mode = info.get("build_mode")
    if mode == "keep_all":
        return PIN_KEEP_ALL
    if mode == "pick_in":
        return PIN_PICK_IN
    if mode == "weed_out":
        return PIN_WEED_OUT
    return PIN_WEED_OUT if info.get("start_as") == "all_picked" else PIN_PICK_IN


def _default_state_from_pin_mode(pin_mode: str) -> str:
    """The legacy template column. keep-all & weed-out start all-in
    (``"picked"``); pick-in starts all-out (``"skipped"``)."""
    return "picked" if pin_mode in (PIN_KEEP_ALL, PIN_WEED_OUT) else "skipped"


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
    Any missing field falls back to the dataclass default. Everything
    goes into the context BEFORE the dialog is constructed — the
    dialog's ``__init__`` reads these fields to seed its initial state,
    avoiding post-build mutations (which broke the add-row chips' paint
    pass)."""
    name = getattr(prefill, "name", "") or ""
    if name:
        ctx.name = name

    pool_json = getattr(prefill, "pool_expr_json", None)
    if pool_json:
        try:
            expr = [tuple(t) for t in json.loads(pool_json)]
            counts = _counts_from_expr(expr)
            ctx.selected_pool_counts = counts
            ctx.selected_pools = list(counts.keys())
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
    # spec/80 — seed the dialog's build mode from the persisted default
    # state. An existing Cut was built by trimming, so it lands on a
    # pinning refinement mode (not live): picked → weed_out, skipped →
    # pick_in. (Live re-evaluation persistence is the gated backend piece;
    # until then a re-opened Cut presents as pinned.)
    ctx.build_mode = "weed_out" if state == "picked" else "pick_in"

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


def _draft_from_info(info: dict) -> CutDraft:
    """Translate the redesigned dialog's ``cut_info()`` payload into a
    spec/81 :class:`CutDraft`. Shared between ``.draft()`` and the
    template-save wrapper so the host always receives the same
    canonical shape.

    Spec/81 reshape: the dialog still emits ``pool`` (signed-mult dict),
    ``styles`` / ``include_photos`` / ``include_videos`` (filters), and
    ``build_mode`` / ``start_as`` (pin choice). The adapter folds those
    into the new CutDraft fields — ``expr`` with the typed operand
    encoding, ``styles`` / ``media_type`` filters, and ``pin_mode``.
    Overlays + separators ride the dialog reframe (Task C continued)."""
    name = (info.get("name") or "").strip()
    expr = _expr_from_counts(info.get("pool", {}))
    target_min = int(info.get("target_minutes", 0) or 0)
    max_min = int(info.get("max_minutes", 0) or 0)
    per_photo = float(info.get("per_photo_seconds", 0.0) or 0.0)
    music_choice = str(info.get("music", "(no music)"))
    music_category: Optional[str]
    if music_choice in ("(no music)", "(none)", ""):
        music_category = None
    else:
        music_category = music_choice
    overlay_fields = tuple(info.get("overlay_fields", ()) or ())
    overlay_mode = info.get("overlay_mode") or None
    if overlay_mode not in ("embedded", "burn_in"):
        overlay_mode = None     # NULL = inherit the settings default
    return CutDraft(
        name=name,
        tag=_slug(name),
        expr=expr,
        styles=tuple(info.get("styles", ()) or ()),
        media_type=_type_filter_from(
            bool(info.get("include_photos", True)),
            bool(info.get("include_videos", True)),
        ),
        pin_mode=_pin_mode_from_info(info),
        target_s=(target_min * 60) if target_min > 0 else None,
        max_s=(max_min * 60) if max_min > 0 else None,
        photo_s=per_photo,
        music_category=music_category,
        overlay_fields=overlay_fields,
        overlay_mode=overlay_mode,
        card_style=_card_style_from_slide(
            info.get("slide_cards", "all_black")),
    )


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
        dc_saver: Optional[Callable[[str, dict], None]] = None,
        music_hint: Optional[str] = None,
        prefill: Optional[object] = None,
        heading_text: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        self._pool_probe = pool_probe
        self._totals_probe = totals_probe
        self._music_categories = tuple(music_categories or ())
        self._separators_on = bool(separators_on)
        self._prefill = prefill
        self._templates = list(templates or [])
        self._template_saver = template_saver
        # Spec/81 §2 — Save as DC. Receives ``(name, cut_info_dict)`` and
        # is expected to call :meth:`EventGateway.create_dc`; the dialog
        # surfaces any :class:`ValueError` ('taken'/'reserved'/'empty'/
        # 'cycle') as a user-friendly message.
        self._dc_saver = dc_saver

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
        to :class:`CutDraft`."""
        if self._dlg is None:
            # Edge case — caller never exec'd; produce a sensible default.
            self._build()
        return _draft_from_info(self._dlg.cut_info())

    # ── construction ──────────────────────────────────────────────────

    def _build(self) -> None:
        if self._dlg is not None:
            return
        # The redesigned dialog reads every prefill field off ``ctx`` in
        # its own ``__init__`` — no post-build mutation needed (post-
        # build chip rewrites broke the add-row chips' paint pass).
        # Probes + templates + the wrapped saver flow straight through:
        # the dialog calls them live on every state change (probes) and
        # on Save as template… / Load template… (templates).
        self._dlg = _RedesignedNewCutDialog(
            ctx=self._ctx,
            pool_probe=self._pool_probe,
            totals_probe=self._totals_probe,
            templates=self._templates,
            template_saver=self._make_template_saver(),
            dc_saver=self._dc_saver,
            separators_on=self._separators_on,
            parent=self._parent,
        )
        if self._heading_text:
            self._dlg.setWindowTitle(self._heading_text)

    def _make_template_saver(self):
        """Wrap the host saver so the dialog can fire it with its own
        ``(name, info_dict)`` signature. The host expects a
        :class:`CutDraft`, so translate before forwarding. Returns
        ``None`` when there is no host saver — the dialog's Save button
        stays disabled in that case."""
        host = self._template_saver
        if host is None:
            return None

        def _save(name: str, info: dict) -> None:
            host(name, _draft_from_info(info))

        return _save


__all__ = ["NewCutDialog", "CutDraft"]
