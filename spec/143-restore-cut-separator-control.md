# 143 — Restore the separator (on/off + card style) control in the Cut dialog

**Status: PROPOSED (Nelson 2026-06-23). The Cut-creation dialog lost its
**separators** control (toggle + card style / colour) — a `grep separator`
in `new_recipe_dialog.py` finds no control, only a stale comment. So the user
can no longer choose whether day-separator / opener cards are inserted, or
their style, per Cut; it silently falls back to the global `use_separators`
setting. Same dropped-Phase-4-field pattern as spec/106 (music) / spec/114
(overlay). Restore the control — separators **on/off** + **card style** —
seeded from the Cut's `separators` flag + `card_style`, in both the
per-event and **cross-event** Cut dialogs. Touches
`mira/ui/pages/new_recipe_dialog.py` (+ the recipe→draft adapter +
`share_cuts_page` / `events_page` wiring). The data already exists
(`cut.separators`, `cut_card_style`); no model change.**

## 1. The gap

`Cut.separators` (store `models.py:297` default True; cross-event
`user_store` default False) and `card_style` (`eg.cut_card_style(cut)`) exist
and are honoured by export + play, and the dialog used to set them — but the
dialog now has **no separator control**, so `presentation_payload()` omits
them and the value can't be chosen per Cut. It defaults to the global
`use_separators` (`share_cuts_page._separators_on`), not the Cut's own
choice.

## 2. The fix

In the dialog's Runtime row, beside music / aspect / overlay (spec/106/111/
114):

- **Separators on/off** — a toggle (or "Off / Day cards" select), seeded
  from `cut.separators` (default from `use_separators` for a brand-new Cut).
- **Card style** — a small select for the separator/opener card style /
  colour (`card_style`; the existing style vocabulary, e.g. "black" + the
  others the renderer supports), seeded from `eg.cut_card_style(cut)`;
  disabled when separators are Off.
- `presentation_payload()` **emits** `separators` + `card_style`;
  `recipe_to_cut_draft` / `create_cut` / `update_cut` (+ cross-event) carry
  them (the gateways already accept them, like overlay).
- Cross-event dialog identical (`show_scope=True` path); its default for
  `separators` stays the cross-event default (False) unless the user opts in.

## 3. Acceptance

- The Cut dialog shows a separators toggle + card-style control; choosing
  them persists and pre-selects on edit (per-event and cross-event).
- Export + play render separators per the Cut's own choice (not just the
  global default).
- Separators Off → no cards, exactly today's off behaviour.

## 4. Tests

- `tests/test_cut_separator_control.py` — the control sets `separators` +
  `card_style` into `composition()["presentation"]`; prefill pre-selects;
  Off disables the style; end-to-end `create_cut` persists; cross-event
  identical under `INVENTORY_LIBRARY`.
- Regress the dialog composition schema for the added fields.
