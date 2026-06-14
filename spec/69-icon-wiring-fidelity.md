# spec/69 — Icon wiring: retire the remaining Unicode glyph placeholders

**Authored 2026-06-14 (Nelson + Claude). A scoped sub-task of
[spec/65](65-redesign-fidelity-pass.md) (the redesign fidelity pass).**

This is a small, self-contained fidelity job, carved out so it can run in its
own session **without colliding with the spec/66/67 phase work** (it touches
shared surface chrome but no phase logic, no gateway, no engine).

## The problem (verified 2026-06-14)

The 2026-06-13 migration shipped the SVG line-icon family as assets but left
**Unicode glyph placeholders in the surface code** (spec/65 §2.1, §3.7). So the
new icons exist on disk while the running app — and any rendered test/smoke —
still draws the old glyphs. This is why old cluster marks and tick marks still
appear when the agent runs tests: it is the unfinished fidelity wiring, **not a
cache and not a regression** (there is no compiled `*_rc.py` resource bundle;
the glyphs are hardcoded string literals in source).

## What is ALREADY wired (do NOT redo)

- **Cluster cover icon** — `mira/ui/design/thumbs.py:Thumb._paint_cluster_badge`
  already renders `assets/icons/clusters/badge/{burst,exposure,focus,repeated}.svg`
  via `QSvgRenderer`.
- **Event-card category tile** — `mira/ui/pages/_event_card_redesign.py:_CategoryTile.paintEvent`
  renders `assets/icons/categories/*.svg` and tints it with the
  `QSvgRenderer` + `QPainter.CompositionMode_SourceIn` pattern. **This is the
  reference implementation** — reuse it (or factor it into a shared helper)
  for everything below.

## What is still Unicode (the job)

| Glyph | Where (verified) | Target |
|---|---|---|
| Visited **eye** `◉` | `mira/ui/pages/picker_page.py:126`, `mira/ui/pages/editor_page.py:113` (both `QLabel("◉")`) | a real **eye** line-icon SVG (does not exist yet — must be drawn) |
| Visited **tick** `✓` | `mira/ui/base/day_grid_cell.py:145` (`QLabel("✓")`) and the `✓`/`✗` button labels in `days_grid_page.py`, `days_lists_page.py`, `toolbar.py`, `bucket_navigator.py`, `exported/export_page.py` | `assets/icons/check.svg` (already exists) for the badge; for button labels, an icon-button or leave text per the mockup — decide per surface |
| Mixed-cluster **split chip** `3✓·2✗` | `mira/ui/design/thumbs.py:351` | the §5a split chip with real ✓/✗ line-icons (draw a small check + cross pair) |
| Dialog status glyphs `i ✓ ▲ ✕ ?` | `mira/ui/design/dialogs.py` (spec/65 §3.14) | line-icon SVGs (out of scope if time-boxed — note it) |

**Verify-then-wire (don't assume):** these bundled SVGs already exist —
`assets/icons/check.svg`, `assets/icons/glyphs/search.svg`,
`assets/icons/glyphs/cross_event.svg`, `assets/icons/mira-mark.svg`. Confirm
each is actually consumed; spec/65 §0.3 flagged the search field still using
Unicode `🔍` in `mira/ui/design/inputs.py:_SearchFieldWrap` and the cross-event
band using `❖` in `mira/ui/pages/_cross_event_band.py`. Wire any that are still
on a Unicode placeholder.

**Reconcile the duplicate cluster dirs:** both `assets/icons/clusters/*.svg`
and `assets/icons/clusters/badge/*.svg` exist, plus a legacy
`mira/ui/base/cluster_icons.py` helper. Pick the `badge/` set (what `Thumb`
uses) as canonical and note/retire the other, so two sessions don't diverge.

## How to do it

1. **Draw only the genuinely-missing glyphs** in the line-icon family — 24×24
   `viewBox`, `stroke="currentColor"`, `stroke-width:1.8`, round caps (spec/65
   §2.1) — into `assets/icons/glyphs/`. The missing ones are at least: **eye**
   (visited), and the **check + cross** pair for the split chip if `check.svg`
   doesn't cover it.
2. **Wire each call site** to render the SVG tinted via the `_CategoryTile`
   pattern (or a shared `tinted_svg_pixmap(path, size, color)` helper factored
   from it). Replace the `QLabel("◉")` / `QLabel("✓")` / split-chip text.
3. **Tint from the palette**, not a hardcoded color — the icon must read in both
   `dark.qss` and `light.qss` (charter: roles exist in both themes).
4. **Leave phase logic, gateway, and the batch engine untouched** — this is
   pure presentation.

## Scope guardrails

- This is presentation-only. No behavior, no gateway, no keymap, no phase model.
- **Run AFTER the phase spine (slices 4–6) is committed**, not before or
  concurrently — it edits shared widgets (`thumbs.py`, `picker_page.py`,
  `editor_page.py`, `day_grid_cell.py`) that slice 5 also leans on, and
  concurrent edits to the same files cause merge pain.
- **No need to pre-supply icons for the Export surface.** Export reuses the
  shared `Thumb` widget, so fixing `Thumb` here updates Export, Days Grid, and
  Picker together. Per [spec/68](68-phase-redesign-coordination.md) §3, slice 5
  builds Export's structure/voice from the catalog and leaves the shared glyphs
  (eye / tick / split chip) to this sweep — so this session corrects every
  surface, Export included, in one pass.
- Tests/smokes render with placeholder/gradient images (spec/65 §2.3), so a
  passing suite won't *show* the icons. Verify by a real-asset smoke screenshot,
  per spec/65 §6.
- Keep it to the icon wiring; do not absorb the rest of the spec/65 punch list
  (sizing/shadow/density work stays in spec/65 §2–§4).

## Related

- [spec/65 §2.1, §3.7, §3.14, §0.3](65-redesign-fidelity-pass.md) — the parent
  fidelity pass and the glyph inventory this draws from.
- [spec/68 §3](68-phase-redesign-coordination.md) — the new Export surface
  consumes these icons; build it fidelity-correct rather than re-placeholdering.
