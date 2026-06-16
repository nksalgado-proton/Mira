# spec/77 — Event tile (final, consolidated)

**Status:** consolidated 2026-06-16 after a full design pass with Nelson
(mockups approved one element at a time, then corrected against the first two
builds). **This is the single source of truth for the events-grid tile** — it
replaces the earlier layered version of this spec and **supersedes the tile
sections of spec/75 (§5/§6)**. Where spec/75 differs on the tile, this wins.

For a full-access agent that can launch the app + run `verify.bat`. The events
*screen* chrome (slim header, cross-event band, Filters popover, grid reflow)
stays as built per spec/75 §2–§4. This doc is only the **tile** + the data
behind its donuts.

Read first: `spec/05-ui-standards.md` (QSS roles in **both** themes, **no inline
`setStyleSheet`** in widget modules, pointing-hand cursor on clickables),
`spec/66` (phase definitions). All user-facing strings via `tr()`.

---

## 1. Tile structure & size

A tile = a fixed **title row** on top of a **4:3 content area** (4:3 is the area
*below* the title, not the whole tile):

- **Title row** (~50px): category icon tile · name + one-line meta · `⋮` menu.
  **No status badge** (see §6).
- **4:3 content area** (height = tile width × 3/4): open event → the four phase
  donuts (2×2); closed event → the photo.

One **fixed tile size** (≈248px wide; tune on real photos). Every tile identical.
The `FlowLayout` reflows columns by window width. **No size slider** (a live
slider re-layouts the whole grid per tick — janky; do not build it).

Tiles are **square** (`TILE_RADIUS = 0`, no corner rounding — Nelson's final
call after the rounded-corner rendering kept breaking). See §7.2 for the
painted square border.

---

## 2. Open-event tile

- **Title row:** icon tile · a block with the **name on its own line, taking the
  full remaining width** (so it stops truncating — this was the recurring bug)
  + a muted meta line `Trip · year · category · Nd` · the **flat `⋮`** menu
  (§6). No badge eating the name's width.
- **4:3 area:** the four donuts in a **2×2** — Collect (top-left), Pick
  (top-right), Edit (bottom-left), Export (bottom-right); reading order = pipeline
  order. Each donut: phase icon centred, `%` to the **left** of the ring (§4).

## 3. Closed-event tile

- **Title row:** same shape — icon · name + meta · `⋮`.
- **4:3 area:** the `PhotoCycler` (chrome-free ambient cycler from spec/75 §6 —
  shuffled auto-advance, blurred-fill, no arrows/dots) over the event's
  **exported keepers**. **NO text overlay — no counts strip.** The photo fills
  the area and shines; nothing covers it. Tiles are square (§1), so the photo is
  square too (`top_radius=0`, `bottom_radius=TILE_RADIUS=0`) and meets the tile's
  square border flush.

---

## 4. The four phase donuts

Each donut is painted (`_PhaseDonut`); the **phase icon sits centred inside the
ring** and the **`%` sits to the LEFT of the ring** (right-aligned in a fixed
`"100%"`-width slot, baseline-centred on the ring) — never stacked in the centre,
and not below (below cramped the 2×2 rows). All four **always paint a complete
ring** — a not-started donut shows a **full faint track ring**, never just an
icon + `%`.

Two donuts are **progress gauges** (amber → green over a faint track); two are
**green/red, default-Skip** gauges (start a **full red ring**, green grows out,
no faint).

### Collect — progress (amber → green)
`days_with_captures ÷ total_days`, where `total_days` = the event header date
span (§5). Amber < 100%, green at 100%, faint track for the rest. Icon: collect
glyph. (≈100% once fully imported.)

### Pick — green/red, **starts ALL RED**
- green = `picked ÷ captured`; red = the rest = `(captured − picked) ÷ captured`.
  Centre `%` = picked share.
- **Default-Skip:** an undecided capture counts as skipped, so a fresh event is a
  **full red ring** and green grows as the user picks. green + red = 100% always
  (no faint). Almost never reaches all-green, by design.
- Data: `picked = phase_picked_count('pick')` (state='picked'); `captured =
  COUNT(visible_item WHERE provenance='captured')`; `red = captured − picked`.
  Icon: pick glyph.

### Edit — progress (amber → green)
`developed ÷ picked`. Reaches 100% when every keeper has been through Edit.
Amber < 100%, green at 100%, faint track for the rest. Data: `developed` =
keepers with an `adjustment` row (see §8 flag); `picked` as above. Icon: edit
glyph.

### Export — green/red, **starts ALL RED** (mirrors Pick)
- green = `exported ÷ picked`; red = the rest = `(picked − exported) ÷ picked`.
  Centre `%` = shipped share.
- **Default-Skip:** a keeper defaults to not-shipped, so it starts a **full red
  ring** and green grows as the user ships. green + red = 100% (no faint).
- Data: `exported = COUNT(adjustment WHERE edit_exported=1)`; `picked` as above;
  `red = picked − exported`. Icon: export glyph.

**Colours** (palette tokens, not inline hex): green `#34d399`, red `#ef4444`,
amber `#fbbf24`, faint ring = the `track` token. Only **Collect & Edit** use the
faint track for their remainder; **Pick & Export** use **red** for theirs. Note:
light `track` was bumped from `#eceef4` (invisible on the white card) to
`#d3d7df` so a 0% ring reads in light theme.

**2×2 layout — `%` LEFT of each ring:** every donut cell is the **same size**.
In each cell the group lays out **left-to-right**: a fixed `"100%"`-width `%`
slot, a small gap, then the ring sized to the remaining area; the whole group is
centred on both axes, the `%` right-aligned in its slot and baseline-centred on
the ring. Putting the `%` beside (not below) its ring is what fixed the earlier
cramming where a top-row `%` sat closer to the bottom-row ring than its own.

---

## 5. Mandatory event date range (Collect's denominator)

- Make **From / To dates required** in the Event Header dialog
  (`event_header_dialog.py`): `Event.start_date`/`end_date` are `Optional` today;
  block Save until both are set (reuse the existing required-field pattern,
  `tr()` the message).
- `total_days = (end_date − start_date) + 1`; feed it to `EventCardData` for
  Collect's denominator (replacing the current `len(days-with-items)`, which made
  Collect always 100%).
- `days_with_captures` = distinct `day_number`s that have captured items.
- Accepted edge: a photo-less day inside the range lowers Collect below 100% —
  fine for v1 (trim the range via From/To).

---

## 6. No status badge · the `⋮` menu (and the stranded-event fix)

- **No Open/Closed pill.** The body already says it (donuts = open, photo =
  closed); dropping the badge gives the name full width.
- **`⋮` is a flat, borderless control** pinned top-right — transparent bg + no
  border, ~16px three-dot glyph, a *hover-only* faint background, pointing-hand
  cursor. Not a boxed button. On the photo tile, a translucent-dark chip behind
  it keeps it legible.
- **Menu:** open tile → **Close event**, Event header…, Days table…, Delete.
  Closed tile → **Reopen event**, Event header…, Delete.
- **Bug fix (pre-production):** closing an event with **no exported media**
  currently strands the user. **Reopen must always work** regardless of export
  state, and closing must be reversible. Test: close an export-less event → it
  shows as a closed tile → Reopen restores it to open with its pipeline intact.

---

## 7. Rendering techniques (the things that kept breaking)

### 7.1 Crisp icons — fix `tinted_svg_pixmap` for HiDPI
The phase icons come from the `PHASE_GLYPH` SVG family via `tinted_svg_pixmap`
(`mira/ui/design/icons.py`), but that function renders the SVG at logical
`size × size` and **never sets `devicePixelRatio`**, so on a HiDPI display Qt
upscales the pixmap and every icon looks soft. **Fix at the source:** render at
`size × devicePixelRatioF()`, then `pixmap.setDevicePixelRatio(dpr)` before
returning. This sharpens every icon in the app.

### 7.2 Tile border — PAINTED, square
The border is **painted in the tile's `paintEvent`** (QSS `border + border-radius`
gapped the corners and vanished in dark; the days-grid `Thumb` proved the painted
approach). As built — square, `MiterJoin`:

```python
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
painter.setClipping(False)                   # stroke not cut at the edge
painter.setBrush(Qt.BrushStyle.NoBrush)
pen = QPen(QColor(card_border), 2.0)
pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)  # sharp square corners
painter.setPen(pen)
painter.drawRect(rect.adjusted(1.0, 1.0, -1.0, -1.0))   # square — no radius
```

- **Painted, not QSS.** `QFrame#TileCard` QSS is fill only:
  `background: {card}; border: none; border-radius: 0px;` + `WA_StyledBackground`;
  the `paintEvent` draws the square border on top.
- **`card_border` is clearly visible in BOTH themes** — 2px, contrasty
  (`#5d6580` dark / `#a8aebf` light).
- `setContentsMargins(2, 2, 2, 2)` on the tile layout so children never paint
  over the border.
- The closed `PhotoCycler` is square too (`top_radius=0`, `bottom_radius=0`) — no
  corner clipping.

---

## 8. Data flag to verify while building
**Edit "developed" must reflect real develops.** spec/66 mentions an automatic
standard-correction baseline on every keeper. If that writes an `adjustment` row
for every keeper at Pick time, Edit would read 100% instantly. Confirm
`developed` counts only keepers the user actually developed; if the baseline
pre-creates rows, count a different signal (e.g. a user-touched/dirty flag).
(Export no longer needs a "dropped" flag — its red is simply `picked − exported`.)

---

## 9. Constraints & reuse
- Reuse: `Donut`/`DonutSlice`, the `PHASE_GLYPH` SVG family, `PhotoCycler`
  (spec/75 §6), the category-icon SVG family.
- QSS roles for every new colour/state, present in `light.qss` **and** `dark.qss`;
  no inline `setStyleSheet` in widget modules; pointing-hand cursor on the tile,
  the `⋮`, and menu items.

## 10. Definition of done
1. `verify.bat` green, incl. the close→reopen test (§6) and a tile-render test.
2. Tiles are **square** with a **continuous, clearly visible 2px border in both
   themes**; the closed photo is square and meets the border flush.
3. Open tile: full-width non-truncating name, flat `⋮`, no badge, four donuts in
   an even 2×2.
4. Donuts: **crisp HiDPI icons**, icon centred + `%` to the **left** of the ring,
   a **full ring even at 0%** (light `track` = `#d3d7df`); Collect/Edit amber→green
   over a track; **Pick & Export start full red** (default-Skip) with green
   growing out.
5. From/To mandatory; Collect uses the header span.
6. `⋮` Close/Reopen works; export-less close is recoverable.
7. No size slider. Fixed ~248px tile.
8. Screenshot the events grid (a few open + a closed) **at HiDPI** for Nelson —
   confirm sharp icons, clean square borders, even donuts.
