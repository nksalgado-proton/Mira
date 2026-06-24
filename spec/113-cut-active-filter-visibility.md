# 113 — Cut dialog: make active filters visible (no more silently-shrunk cuts)

**Status: SHIPPED (Nelson 2026-06-22) in one commit. `#PillToggle:checked`
now full `{accent}` fill + `{accent_text}` contrast (+ hover/pressed),
unmistakable via the shared `pill_toggle` factory (style + camera + lens).
New `#FilterActiveIndicator` + `#FilterClear` QSS roles paint a soft-accent
attention banner (not error-tone) with an accented Clear CTA.
`_filter_indicator_row` sits between the metrics banner and line, hidden by
default; `_refresh_filter_indicator` runs after every probe — visible iff
`_active_filter_count() > 0` (checked style ∪ media-type ≠ both ∪ checked
camera/lens) — and reads "{n} filter(s) active — showing X of Y items" with
Y from a second probe with `filters` cleared (cached on
`_unfiltered_pool_count` so a transient probe failure falls back to the
last-known total). `_on_clear_filters` empties every axis under
`blockSignals`, hides the row, and re-probes to snap the count back to Y.
Works in **both** per-event (`INVENTORY_EVENT`) and cross-event
(`INVENTORY_LIBRARY`) dialogs. 8 tests in
`tests/test_cut_filter_visibility.py` + the 49-test dialog suite green.
(`verify.bat`: 4497 + 24 quarantine; the 9 `test_focus_keeper` failures are
a pre-existing Qt cross-test contamination flake — 9/9 alone — untouched by
this work.) Original proposal follows.**

**Status: PROPOSED (Nelson 2026-06-22). In the Cut composition dialog
(`NewRecipeDialog`), the style filters (Macro, Landscape, …) and media-type
/ hardware filters give only a **soft** "checked" tint and no summary — so
an accidental filter click silently shrinks the Cut and the user has no way
to see *why* their Cut is missing media. This adds (a) an unmistakable
active-filter visual and (b) a persistent "filters active — showing X of Y,
[Clear]" indicator tied to the live count. Touches
`mira/ui/pages/new_recipe_dialog.py` (the filters + metrics sections) and
`assets/themes/redesign.qss` (a stronger active-pill role). No data-model
change. Applies wherever the dialog is used (event + cross-event Cuts).**

## 1. The problem

The style filters are `#PillToggle` chips (`_build_style_row`,
`_style_chips`); media-type is the photos/videos checkboxes
(`include_photos`/`include_videos`); hardware filters (camera/lens/faces,
spec/90 §4) join the same Filters block. Today:

- **`#PillToggle:checked` is a soft tint only** (`background: accent_soft;
  color: accent`). On an accidental click it's easy to miss that a filter
  is now ON.
- **No summary.** The live metrics count (`pool_probe`/`totals_probe`)
  drops when a filter excludes media, but nothing connects the smaller
  number to "a filter is active." The user just sees fewer items than
  expected and can't tell why.

Result (Nelson): a stray click on a style button quietly produces a Cut
without all the expected media, with no visible cause.

## 2. Unmistakable active-filter visual

- Strengthen the **checked** state so "on" is obvious — a full-accent fill
  with contrasting text (the `#HelpInvite`/active-affordance precedent),
  and/or a leading ✓ glyph on checked pills. The unchecked state is
  unchanged.
- Apply the same "clearly active" treatment to an engaged media-type
  toggle and any active hardware-filter chip, so every filter kind reads
  the same when on.

## 3. "Filters active" indicator (the core fix)

Add a persistent notice in the Filters/metrics area that appears **iff any
filter is active** (style ∪ media-type-not-default ∪ hardware):

- Text: **"{n} filter(s) active — showing {X} of {Y} items"**, where `X` is
  the current (filtered) pool count and `Y` is the **unfiltered** pool
  count (probe the pool with filters cleared — both probes already exist:
  `totals_probe(expr, styles, media_type)` / `pool_probe`).
- A one-click **[Clear filters]** button that resets style + media-type +
  hardware to "no filter" and re-probes — instant recovery from an
  accidental click.
- Style it as a gentle attention cue (not an error) — it's informational,
  but visible. Hidden entirely when no filter is active (so a clean Cut has
  no clutter).

Optionally badge the **Filters section header** with the active count
("Filters · 2 active") as a secondary cue.

## 4. Acceptance

- Clicking a style pill makes it **unmistakably** ON, and the "filters
  active — showing X of Y" notice appears with the count drop explained.
- **[Clear filters]** removes all active filters in one click and the pool
  returns to Y; the notice disappears.
- The notice covers style, media-type (e.g. videos unchecked), and
  hardware filters; it is absent when nothing is filtered.
- Works in both the per-event and cross-event Cut dialogs.

## 5. Tests

- `tests/test_cut_filter_visibility.py` — selecting a style sets the chip's
  checked/active role and shows the indicator with `X<Y`; the indicator's
  count reflects the active-filter set across style + media-type +
  hardware; **[Clear filters]** empties `selected_styles` /
  resets media-type / hardware and hides the indicator; no indicator when
  no filter is active.
