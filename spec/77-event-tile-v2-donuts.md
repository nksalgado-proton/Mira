# spec/77 — Event tile v2 (header + 4:3 area + four phase donuts)

**Status:** written 2026-06-16 from a design session with Nelson (interactive
mockups reviewed + approved, one gauge at a time). For a full-access coding
agent that can launch the app + run `verify.bat`.

**Supersedes** the tile sections of spec/75: §5 (open tile) and §6/§5's
fixed-150px whole-tile box and the succession-strip pipeline. The slim chrome
(spec/75 §2), filters rework (§3), and grid reflow (§4) stay as built. The
`PhotoCycler` from spec/75 §6 is reused inside the new closed tile.

Read first: `spec/05-ui-standards.md` (QSS roles in **both** themes, **no inline
`setStyleSheet`**, pointing-hand cursor on clickables), `spec/66` (phase
definitions), and `Desktop/MiraCrafter Redesign/00-design-system.md`. All
user-facing strings via `tr()`.

---

## §1. Tile structure & size

A tile is **a fixed title row on top of a 4:3 content area** — the 4:3 applies
to the *content area below the title*, not the whole tile:

- **Title row** — fixed height (~50px): category icon tile (the existing SVG
  family) · name + one-line meta · status pill · `⋮` menu.
- **4:3 content area** — width = tile width, height = `width × 3/4`. For an open
  event this holds the four donuts; for a closed event it's the photo.

Total tile height = title row + 4:3 area (≈ 233px at ~244px wide). Every tile is
identical size; the grid (spec/75 §4 `FlowLayout`, `minmax(~220px,1fr)`) reflows
by width. Tune the exact width on real exported photos, then everything follows.

---

## §2. Open-event tile

- **Title row:** icon tile · a block with **name on its own near-full-width
  line** (ellipsis only as last resort — it must stop truncating in the common
  case, which was the bug in Picture21) + a muted meta line `Trip · year ·
  category · Nd` · a small **Open** status pill (green dot + "Open") · `⋮` menu.
- **4:3 area:** the four phase donuts in a **2×2 grid** (Collect top-left, Pick
  top-right, Edit bottom-left, Export bottom-right — reading order = pipeline
  order). Each donut: the phase icon centred, the value `%` beneath. Sizes/colours
  per §4. 2×2 (not a single row) so the donuts are large enough to read and fill
  the 4:3 area.

## §3. Closed-event tile

- **Title row:** same shape — icon · name + meta · **Closed** pill (pink dot) ·
  `⋮` menu.
- **4:3 area:** the `PhotoCycler` (spec/75 §6 — chrome-free, shuffled
  auto-advance, blurred-fill, no arrows/dots) showing the event's **exported
  keepers**, with a single thin translucent strip across the bottom carrying the
  counts (`N exported · M shot`). Nothing else covers the photo.

---

## §4. The four phase donuts (the heart of this spec)

Reuse the existing `Donut` / `DonutSlice` widget (`mira/ui/design`, already used
by the Phases page) so the tile and the Phases page agree. Each donut is a ring
over a faint `track`; the icon sits in the centre, the `%` beneath. Two donuts
are single-arc **progress** gauges (amber while < 100, green at 100); two are
**green/red survival-pass** gauges.

### Collect — progress (amber → green)
- **Metric:** `days_with_captures ÷ total_days`, where **total_days comes from
  the event header date range** (see §5), not from the count of days that have
  photos. (Today `EventCardData.total_days` = `len(days-with-items)`, which makes
  Collect always 100% — that must change to the header span.)
- **Colour:** amber while < 100%, green at 100%, faint remainder. Icon: camera.

### Pick — green/red survival pass
- **Metric:** green arc = **picked ÷ captured**; red arc = **skipped ÷ captured**;
  faint remainder = not-yet-reviewed. Centre `%` = the **picked** share.
- **Why:** default is Skip, so green grows as you keep; it never reaches 100%
  (by design), green+red shows review completeness, the gap is what's left.
- **Data:** picked = `phase_picked_count('pick')` (state='picked'); decided =
  `phase_decided_count('pick')` (any explicit mark); skipped = decided − picked;
  captured = count of `visible_item` `provenance='captured'`. Icon: checks.

### Edit — progress (amber → green)
- **Metric:** `developed ÷ picked`. Reaches 100% when every keeper has been
  through the Edit pass. Colour: amber < 100, green at 100, faint remainder.
- **Data:** developed = keepers with an `adjustment` row; picked as above.
  Icon: adjustments/sliders.

### Export — green/red survival pass (mirrors Pick)
- **Metric:** green arc = **exported ÷ picked** (shipped); red arc = **dropped ÷
  picked** (deliberately not shipped); faint remainder = keepers not yet given a
  ship decision. Centre `%` = the **shipped** share.
- **Data:** exported = `adjustment.edit_exported = 1`; picked as above; dropped =
  see §7 flag. Icon: upload.

Colour tokens: green `#34d399`, red `#ef4444`, amber `#fbbf24`, track = faint
`line`. (Define as QSS/palette roles, not inline hex, per spec/05.)

---

## §5. Mandatory event date range (Collect denominator)

Collect needs an independent day count, so:

- Make **From / To dates required** in the Event Header dialog
  (`event_header_dialog.py`) — `Event.start_date` / `end_date` are currently
  `Optional`; add validation so Save is blocked until both are set (consistent
  with the existing required-field treatment). `tr()` the validation message.
- Compute **total_days = (end_date − start_date) + 1** and feed it to the
  tile/`EventCardData` for Collect's denominator (replacing `len(days-with-items)`).
- `days_with_captures` = count of distinct `day_number`s that have captured items.

Edge note we accepted: a legitimately photo-less day inside the range lowers
Collect below 100% — that's fine for v1 (the From/To range already lets the user
bound the event tightly). An "included days" refinement via the Days Table is a
later option, out of scope here.

---

## §6. Status pill + ⋮ menu (and the stranded-event fix)

- **Status pill** in the title row: green "Open" / pink "Closed".
- **`⋮` menu** carries the rare actions so the tile stays clean:
  - Open tile: **Close event**, Event header…, Days table…, Delete.
  - Closed tile: **Reopen event**, Event header…, Delete.
- **Bug fix (real, pre-production):** closing an event that has **no exported
  media** currently strands the user with no way back. **Reopen** in the closed
  tile's menu must always work regardless of export state, and closing must be
  reversible. Add a test: close an export-less event → it appears as a closed
  tile → Reopen returns it to open with its pipeline intact.

---

## §7. Data flags to verify while building (not design choices)

1. **Edit "developed" must reflect real develops.** spec/66 mentions an
   automatic standard-correction baseline on every keeper. If that writes an
   `adjustment` row for every keeper at Pick time, Edit would read 100%
   instantly. Confirm `developed` counts only keepers the user actually
   developed; if the baseline pre-creates rows, count a different signal (e.g. a
   user-touched/dirty flag).
2. **Export "dropped" (red) needs an explicit drop decision.** If the model only
   records exported = yes/no and not a deliberate red drop, render Export as
   green (shipped) + faint (not yet shipped) with **no red** — the look is
   otherwise identical. Wire the red arc only if a drop decision is recorded.

---

## §8. Constraints & reuse
- Reuse: `Donut`/`DonutSlice`, the category-icon SVG family, `PhotoCycler`
  (spec/75 §6), `StatTile`/status pills.
- QSS roles for every new colour/state, present in `light.qss` **and** `dark.qss`;
  no inline `setStyleSheet` in widget modules; pointing-hand cursor on the tile,
  the `⋮`, and menu items.
- Tile, status pill, and `⋮` are clickable affordances (hover/pressed/disabled).

## §9. Definition of done
1. `verify.bat` green, incl. the close→reopen test (§6) and a tile-render test.
2. Open tile: title row (non-truncating name) + 2×2 donuts matching §4 rules;
   closed tile: title row + clean cycling photo + counts strip.
3. From/To mandatory in the header; Collect uses the header span; an event with
   a photo-less day reads Collect < 100%.
4. Pick/Export show green/red/faint; Collect/Edit show amber→green.
5. ⋮ menu Close/Reopen works; export-less close is recoverable.
6. Capture a screenshot of the events grid (a few open + a closed) for Nelson.
