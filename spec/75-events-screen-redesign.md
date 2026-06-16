# spec/75 — Events screen redesign (Surface 01 layout)

**Status:** written 2026-06-16 from a design session with Nelson (two
interactive mockups reviewed + approved). For a full-access coding agent that
can launch the app + run `verify.bat`. Revises the **layout** of Surface 01
(`surface-01-initial-app`), not its data layer — `list_events` /
`events_index_filtered` / `EventCardData` are unchanged.

> **Update 2026-06-16 — layout landed.** §2 slim chrome, §3 filters
> popover + active-state + Clear + wheel-fix-at-source, §4 uniform tile
> grid (FlowLayout), §5 open-tile (fixed-height + compact pipeline
> strip), §6 closed-tile (chrome-free `PhotoCycler`) all built and
> wired into `events_page.py`. The closed photo tile drove the tile
> height (150 px), the open tile inherits the same box. Cross-event
> band now leads the screen and the filter controls live behind the
> Filters button — this layout description supersedes the prior
> "stat-tiles → CEC → 4-combo filter row → vertical card stack"
> description for Surface 01. §7 (grid/list toggle) deferred — keep
> as a follow-up if Nelson asks. §9 #7 screenshot remains a manual
> step (an agent without an interactive Qt session can't capture it).

**Goal:** today the screen is a vertical stack of tall event cards under ~250px
of fixed chrome (title + stat tiles + cross-event band + filter row), so only
~3 events are visible. Redesign it to a **uniform tile grid with slim chrome**
so ~9–12 events show at once, closed events read as little photo cards, and a
filtered list is never mistaken for "all my events."

**Absorbs two earlier punch-list items:** original fix #1 (mouse-wheel over a
filter dropdown silently changing it) is fixed here as part of the filter
rework (§3.3); original fix #3 (move the cross-event search to the very top) is
baked into §2. The remaining standalone items #2 (ask-once TZ/country) and #4
(background import job) get their own spec later — out of scope here.

Read first: `Desktop/MiraCrafter Redesign/00-design-system.md` and
`surface-01-initial-app.html`; `spec/05-ui-standards.md` (QSS roles, **no inline
`setStyleSheet`** in widget modules; every clickable gets hover/pressed/disabled
+ pointing-hand cursor). All user-facing strings via `tr()`. Every new QSS role
must exist in **both** `assets/themes/light.qss` and `dark.qss`.

---

## §1. The two space costs

1. **Fixed chrome** — title, the 3 stat tiles, the cross-event band, and the
   4-combo filter row each take a full row. Collapse them (§2).
2. **Per-event height** — each card stacks a header over four full-width phase
   bars. Replace with a fixed-height tile whose pipeline is a compact inline
   strip (§5).

---

## §2. Slim chrome (top of `events_page.py:_build_ui`)

New vertical order, top → bottom:

1. **Cross-event search bar — the very first element** (moved above the page
   title per Nelson). Full-width: leading search glyph + placeholder
   "Search across all events — captures, picks, cuts, tags…" + a small
   `cross-event` tag on the right. Keep the accent-wash framing from
   `_cross_event_band.py` so it still reads as the app-level entry point
   (surface-01 calls it "the designated entry point" — putting it first
   reinforces that). It stays independent of the per-events filter (§3).
2. **One-line toolbar** (single `QHBoxLayout`): `Events` title (left) ·
   the three stat chips inline (`8 open` green · `1 closed` pink · `85 days`
   accent — compact `StatTile`/chip, not the tall tiles) · spacer ·
   **Filters** button · **+ New event** primary button (right).
3. **The tile grid** (§4) fills the rest.

This reclaims ~150px versus today.

---

## §3. Filters rework (absorbs fix #1 + the "is it filtered?" safety net)

### 3.1 Filters move behind a button
Replace the always-visible 4-combo row (Status / Type / Year / Sort) with a
single **Filters** button that opens a small popover/menu holding those four
controls. Search-events (the per-list text filter, distinct from the
cross-event bar) can stay inline as a slim field next to the Filters button, or
move into the popover — agent's call based on width.

### 3.2 Make "filtered" unmistakable
The original bug #1 stranded the user with a hidden list. Even with the wheel
fixed, the screen must always show when events are hidden:

- A live **"showing N of M events"** label in the toolbar whenever the filtered
  count < total.
- The **Filters button shows an active state** — accent border + a small count
  badge (e.g. `2`) when any filter is off its default.
- A one-click **Clear** (an `×` on the active Filters button / next to the
  count) that resets every filter to default and re-shows all events.

### 3.3 Fix the wheel-over-combo bug at the source
Root cause: the app-wide `mira/ui/base/wheel_guard.py` decides "did the user
engage this control?" from Qt's focus state, but `WheelFocus` + window-activation
churn can mark a combo focused on mere hover, so the guard lets the wheel
through. The day's-table pickers avoid this by tracking an explicit
`_user_engaged` flag (see `mira/ui/base/tz_picker.py:187` and
`country_picker.py:104`), set only on real left-click / Tab / Backtab / Shortcut
focus and cleared on focus-out, with `wheelEvent` ignoring the wheel unless
engaged.

**Fix once, at the factory:** make `select()` in `mira/ui/design/inputs.py`
return a small `QComboBox` subclass carrying that same `_user_engaged` pattern
(`mousePressEvent` + Tab/Backtab/Shortcut `focusInEvent` set it; `focusOutEvent`
clears it except `PopupFocusReason`; `wheelEvent` ignores unless engaged). That
fixes the events filters **and every other design-system dropdown** at once, and
makes the global guard redundant for combos. Add a regression test that posts a
`QWheelEvent` to an unfocused `select()` combo and asserts `currentIndex()`
unchanged; after a synthetic left-click focus, the wheel changes it.

---

## §4. The tile grid (uniform tiles)

- Responsive grid: `grid-template-columns` equivalent of
  `repeat(auto-fit, minmax(210px, 1fr))` — use a `FlowLayout` or `QGridLayout`
  that reflows by width (the project already has `mira/ui/base/flow_layout.py`).
- **Every tile is identical size**: one **fixed height (~150px)** with equal
  widths from the grid. 150px is the floor where a landscape photo still reads;
  the closed photo tile drives this — **tune the height on real exported photos
  first, then the open tile inherits it.** Do the closed tile (§6) first to lock
  the height, then size the open tile (§5) to the same box.
- Roughly 3 columns × 3–4 rows ⇒ ~9–12 events before scrolling (vs ~3 today).

---

## §5. Open-event tile

Same fixed box as the closed tile. A `Card` with content distributed top↔bottom:

- **Top row:** category icon tile (28–30px, the existing SVG family) · name
  (ellipsized, 13/500) + `Trip`/`Session` `tag` · `StatusPill` **Open** (green)
  pinned right. A second line under the name: `year · category · Nd` in
  `ink_soft`.
- **Bottom row (pinned, `margin-top:auto`):** the **4-phase pipeline** as a
  compact inline strip — four equal segments labelled Collect / Pick / Edit /
  Export, each a slim track with a fill: **done = green, in-progress = amber,
  zero = faint `track`** (the locked phase semantics; sourced from
  `EventCardData.status_by_phase`). Keep the tiny per-phase `%` labels; dropping
  them is an option only if more density is needed later.

Reuse `StageProgress` styling but in the compact inline form (not four stacked
full-width bars).

## §6. Closed-event tile — ambient photo cycler

Nelson's call: a closed tile is **just the carousel**, same box as the others.

### 6.1 Do NOT reuse `Carousel` as-is
`mira/ui/design/carousel.py` (297 lines) is the full-feature version — overlaid
`‹`/`›` arrow buttons + a dot `QPushButton` row + hover-pause + click-to-jump.
Those are the artifacts Nelson sees (mis-painting child controls). The tile
needs none of it: the **whole tile is one click target** that opens the event.

Build a small **`PhotoCycler`** widget (or a `Carousel(ambient=True)` mode that
hides all chrome) that:
- Keeps the one genuinely useful piece of `carousel.py`: its `paintEvent`
  **blurred-fill backdrop** (≈lines 51–73) so each photo shows *contained* over
  its own blurred copy — never cropped, no letterbox bars (matches the
  design-system blurred-fill used by the grid/picker).
- **Shuffles** the photos into random order and auto-advances on a `QTimer`
  (~3–4 s). No arrows, no dots, no hover-pause, no click-to-jump.
- Caption strip across the bottom (translucent-dark, not a gradient): name
  (white, 13/500) + `year · counts` (e.g. "169 shot · 18 exported"); a small
  **Closed** pill top-right and the `Trip`/`Session` tag top-left.

### 6.2 Picture source
Feed it the event's **exported keepers** (the `Exported Media` survivors —
the same pool the closed card's `sample_pixmaps` already draws from), shuffled,
**capped at ~8–12** frames for the cycle. Fallbacks if empty: picked → any
capture. Decode through the existing thumb tier; don't block the UI.

---

## §7. Grid / list toggle (optional, secondary)

Nelson liked the first mock's grid↔compact-list toggle. Ship it only if cheap
once both renderers exist: a small segmented `Grid | List` control in the
toolbar that swaps the tile grid for dense single-line rows (icon · name · tag ·
inline 4-segment pipeline · status · days), **persisted** like the legacy
`events_dashboard_sort` so the choice sticks. Grid is the default.

---

## §8. Files in scope

- `mira/ui/pages/events_page.py` — chrome reorder (§2), filter rework (§3),
  grid container (§4), optional toggle (§7).
- `mira/ui/pages/_event_card_redesign.py` — recast open + closed cards as the
  fixed-height tiles (§5, §6); or split into a new `_event_tile.py`.
- `mira/ui/pages/_cross_event_band.py` — keep the band but it now sits first.
- `mira/ui/design/inputs.py` — `select()` wheel fix (§3.3).
- New `mira/ui/design/photo_cycler.py` (or `ambient` mode on `carousel.py`).
- `assets/themes/{light,dark}.qss` — any new roles, both themes.

## §9. Validation / DoD

1. `verify.bat` green, including the §3.3 wheel-guard regression test.
2. Events screen shows a uniform tile grid; open tiles show the compact 4-phase
   pipeline, closed tiles cycle exported keepers with **no arrows/dots**,
   blurred-fill (no crop), same box size as open tiles.
3. Mouse-wheel over a filter combo without clicking it **does not** change it;
   after a click it does. (Screenshot/interactive check.)
4. Applying a filter shows "showing N of M", an active Filters button with
   count, and a working Clear.
5. Cross-event search bar is the first element on the screen.
6. Resize the window → columns reflow; tiles stay identical size.
7. Capture a screenshot of the redesigned screen (mixed open/closed, a filter
   active) and attach for Nelson.
8. Note in `surface-01-initial-app.md` (or here) that the cross-event band now
   leads the screen and filters live behind a button, superseding the old
   layout description.

The design intent (uniform tile box, ambient cycler, slim chrome, filter-state
clarity) was approved live; build the closed tile first to lock the height, then
match the open tile to it.
