# 111 — Cut aspect ratio + aspect-matched separator/opener cards

**Status: SHIPPED (Nelson 2026-06-22) in one commit, all four layers.**
`core/cut_aspect.py` is the single source of truth: closed enum
(16:9 / 4:3 / 3:2 / 1:1) + `aspect → (pte_string, w, h)` map
(16:9→("16-9",1920,1080), 4:3→("4-3",1024,768), 3:2→("3-2",1620,1080),
1:1→("1-1",1080,1080)) with `normalise()` / `aspect_dimensions()` /
`aspect_pte_string()` / `aspect_spec()` — read by **both** spec/107's PTE
override and the card renderer. Schema: event.db **v14→v15** + mira.db
**v8→v9** each `ADD COLUMN aspect TEXT NOT NULL DEFAULT '16:9'` (full CHECK
on fresh DDL; runtime `normalise()` guards migrated rows). `aspect` round-
trips through `create_cut`/`update_cut_settings`,
`create_cross_event_cut`/`update_cross_event_cut_settings`,
`CutSession`/`CutDraft`/`CrossEventCutDraft`, and `recipe_draft_adapter`
(the `presentation` block always emits it). UI: a `RuntimeAspectCombo` in
`new_recipe_dialog`; the separator/opener writers now read **`cut.aspect`**
(replacing the legacy global `settings.separator_aspect` — the actual cause
of the 16:9-card/4:3-photo mismatch) and render at the canonical canvas
dims; `export_cross_event_cut` gained `opener_writer`/`separator_writer`
kwargs + a `separators` count. 22 tests in `tests/test_cut_aspect.py`
(map; per-event + cross-event persist/round-trip/migration/CHECK; rendered
separator AND opener at every aspect; cross-event exporter invokes the
wired opener). Full `verify.bat` green (4498 + 24 quarantine). Original
proposal follows.**

**Status: PROPOSED (Nelson 2026-06-22). A Cut gains an **aspect ratio**
property (the slideshow canvas shape), and Mira's rendered **separator /
opener cards** are produced at that aspect so cards, photos, and the show
canvas all agree (today the cards mismatch — 16:9 cards in a 4:3 show).
Applies to **both** `export_cut` and `export_cross_event_cut`. Consumed by
spec/107 (the PTE `[Main]` `AspectRatio`/screen-size override). Touches
`mira/store` (the Cut/cross-event-Cut `aspect` field + migration), the cut
create/edit dialog, `core/cut_names.py`-adjacent aspect mapping, and the
`SeparatorWriter`/`opener_writer` card renderers used by both exporters.**

## 1. Why

The slideshow canvas has an aspect (16:9, 4:3, 3:2, …). It belongs to the
Cut, not the event. Two consequences flow from it: (a) a slideshow tool's
canvas should be set to it (spec/107 writes it into PTE `[Main]`), and (b)
Mira's own **separator/opener cards** — which it renders as image slides —
must be drawn at that aspect, or they letterbox/crop against the photos in
the show. Nelson observed exactly this: separators read 16:9 while photos
read 4:3 in the same cut.

## 2. The aspect property

- Add `aspect` to the Cut model (and the cross-event Cut) — a small enum /
  ratio (e.g. `"16:9"`, `"4:3"`, `"3:2"`, `"1:1"`), with a schema
  migration. Default to the most common (`"16:9"`), or infer from the
  event's dominant capture aspect on first set.
- A tiny mapping `aspect → (pte_aspect_string, width, height)` (e.g.
  `16:9 → ("16-9", 1920, 1080)`, `4:3 → ("4-3", 1024, 768)`) — store
  pixel dims or derive; spec/107 reads this for the PTE `[Main]` override.
- Surface an **aspect picker** in the Cut create/edit dialog (beside the
  per-photo duration, its sibling parameter).

## 3. Aspect-matched cards (both exporters)

- The `SeparatorWriter` / `opener_writer` (injected into `export_cut`, and
  the cross-event equivalent) must render the day-separator and opener
  cards at the Cut's **aspect** (canvas WxH from §2), not a fixed shape.
- The card layout (title text, day label) re-flows to the aspect. Render
  via the existing card renderer, parameterised by the target WxH.
- Result: in any tool — and in Mira's own flat-grid Play — cards, photos,
  and canvas share one aspect.

## 4. Acceptance

- A Cut carries an aspect; the create/edit dialog lets the user set it;
  it persists (per-event and cross-event).
- Exporting a Cut renders separator/opener cards at that aspect (a 16:9
  Cut → 16:9 cards; a 4:3 Cut → 4:3 cards) — no card/photo mismatch.
- spec/107 reads the aspect → writes `AspectRatio` + `opt_scr_*` into the
  generated `.pte`; the slides reflow (FitMode) with no per-slide edits.
- Both `export_cut` and `export_cross_event_cut` honour it.

## 5. Tests

- `tests/test_cut_aspect.py` — the aspect field persists + migrates; the
  `aspect → (string, w, h)` map is correct; a rendered separator card has
  the Cut's aspect dimensions, for both exporters.
