# Visual fidelity audit — 2026-06-16

## Corrections applied (2026-06-16, follow-up session)

**Fixes landed:**

1. **New Event header** (`event_header_dialog.py`) — was a lonely generic
   "Event Header" line next to the icon tile on the create flow. Now a
   balanced two-line lockup: "New Event" + "Set up identity, logistics, and
   tags." while creating; "Event Header" + the event name while editing
   (window title matches).
2. **Filename timestamp recovery** (`core/scan_source.py`) — the
   existing-media collect path now recovers capture time from the filename
   (`IMG_20180224_204237.jpg` → 2018-02-24) before any fallback, via a new
   `_recover_filename_timestamps` pass in `build_scan_result` (covers both
   `scan_source()` and `build_scan_result()` entry points). mtime is never
   used for day routing. Mirrors the SD-card ingest engine.
3. **`_outros` day-title leak** (`core/autofill.py`) — `common_immediate_subdir`
   now rejects Mira's internal bucket folders (`_`-prefixed names +
   `RESERVED_DIR_NAMES`) so tokens like `_outros` never become a day
   description; the day falls back to "Day N".

**Verified already-done (spec/73 entries now stale):**

- **Days Lists bulk Pick/Skip-all** — already wired via
  `_apply_days_lists_bulk` (confirm dialog → `set_items_phase_state` → refresh).
  Only "Start a new pass" remains a deliberately-deferred feature.
- **New Cut match count** — already binds the live gateway probes
  (`pool_probe` / `totals_probe` in `new_cut_dialog._refresh_pool_summary`);
  declared-count arithmetic is only the no-probe fallback.

**Still open (heavy UI — best done where the app can be run + screenshotted):**
Editor crop drag handles, Editor Look-preset mini-previews, the brand layer
(M✦ıra wordmark / "See the keepers." / splash-About).

*Note: this follow-up session's sandbox couldn't run the Qt app or pytest
(mount served truncated copies of freshly-edited files); the three fixes above
were verified by host-file inspection + pure-logic checks of the parsing.*

---

Current surface screenshots (`Desktop/Mira Surfaces/Picture1–15`, captured
2026-06-16) compared against the design intent in
`Desktop/MiraCrafter Redesign/` (`00-design-system.md` + each
`surface-NN-*.html/.md`). Suspected gaps were checked against live code,
not just the screenshots — three "gaps" turned out to be already built or
intentionally cut, and are recorded as such so they don't get re-opened.

## How this differs from spec/65

spec/65 (2026-06-13) was the "port + recolor" punch list. A lot of it has
since landed. This audit reflects the *current* state: most chrome-level
intent is in; what's left behind is concentrated in (a) the capture-level
overlay vocabulary not being exercised with real data, (b) a couple of
single-surface visual touches, and (c) the brand identity layer.

---

## What actually landed (so it isn't re-litigated)

- **Events (S01):** stat-tile band (Open / Closed / Days), Cross-Event Cuts
  accent band with the `Preview` tag and focal search field, four-stage
  Collect/Pick/Edit/Export pipeline, and the closed-event card with a real
  **Carousel** (`_event_card_redesign.py:482`, 4 s auto-advance) + stat-tile
  grid. The "static cover photo" look in the screenshot is just one carousel
  frame / limited sample pixmaps, not a missing carousel.
- **Event Header (S02)** and **Event Days Table (S04):** sectioned dialog,
  custom **accent checkboxes** (no longer native Qt), accent left-edge on the
  selected row, uppercase micro headers.
- **Days Lists (S05):** day cards with number badge, green/red summary bars,
  and a per-day capture **spark histogram** (beyond the original mockup).
- **Days Grid (S06):** the new `ThumbGrid` with **real blurred-fill photos**
  and locked green/red state borders — the blurred-fill pattern that spec/65
  said "never shines" now does.
- **Editor (S08):** full control panel (Look segments / Strength / Style /
  Filter / Crop), **floating ‹ / › nav arrows + filmstrip**
  (`editor_page.py:350,368`), F10/F11.
- **Phases (S03):** Back + category tile + status pill, breadcrumb meta, a
  **hero metric line** ("Reviewed 92/169 · Edited 23/83 · Exported 18/83 ·
  1 of 1 days touched"), and the 2×2 donut grid with step badges, status
  chips, mini progress bars and captions. The per-camera **Collect legend**
  is built (`phases_page.py:178 _DonutLegend`) — it just isn't visible on
  this single-camera event (one slice).
- **Picker (S07):** blurred-fill stage with locked state border, floating
  ‹ / › nav arrows + neighbour-thumb **filmstrip**, and a **structured EXIF**
  chip (`caption_html` over a real exiftool batch prefetch), F10 inspection
  lens.
- **Video Editor (S12):** marker-partition timeline, transport, snapshots.
- **Export grid:** legend strip, review-progress bar, type-stamped
  clip/snapshot cells.
- **Share / Cuts (S09):** `#exported` pool card, **kebab** menus (rare actions
  hidden), Cut session view with cover slide + day-separator slides. The
  cut-row cover thumbnail is **intentionally absent** per spec/61 §3
  (`share_cuts_page.py:298`) — not a regression.
- **New Cut (S13):** pool-algebra chips + steppers, live `18 of 18 match`
  count, **Load / Save template**, double-spin per-photo, slide/start radio
  cards. This was the weakest surface in spec/65 and is now close to the mock.

---

## Item 1 (overlay vocabulary) — VERIFIED BUILT, not a gap

My first-pass concern was that the capture-level overlay vocabulary
(visited eye, cluster pile/badge/count, mixed-cluster yellow + split, exported
badge) wasn't on stage. Verification on 2026-06-16 shows it **is** — it just
wasn't present in the 15 day-views that got screenshotted:

- **Code:** `DaysGridPage._build_*` populates every field from gateway data —
  `cluster_type` from `cluster.kind`, `cluster_split` via `_cluster_split_for`,
  `visited=bool(cell.visited)`, `exported=bool(cell.exported)`, exported IDs
  from `self._eg.exported_item_ids()`. Render forwards them all to `Thumb`
  (`days_grid_page.py:1213`).
- **Render proof:** `scripts/smoke_surface_06_dark.png` shows it all painting
  — cluster covers on offset piles, "Burst shot" / "Focus bracket" type
  badges, `×20`/`×40` count chips, a **mixed cluster with yellow border +
  `3✓·2✗` split chip**, visited eye chips, the state-border set, and the
  legend. `smoke_surface_07_cluster_dark.png` shows the Picker's structured
  EXIF chip (`ƒ/5.6 · 1/200 · 50mm`).

Conclusion: cross item 1 off. The overlays appear whenever a day actually
contains clusters / mixed states / visited frames; the macro-session days in
the screenshots simply didn't.

## What was left behind (prioritized)

### 1. Editor "Look" presets are text-only
Mockup intent (S08): each Look preset shows a **tiny preview of the photo with
that look applied**. The screenshot shows text pills (Original / Natural /
Brighten / Deeper / Grid). Functional, but the at-a-glance preview is left
behind.

### 2. Brand identity layer
`mira-logo.html` is a full kit the migration never adopted: the wordmark
**`M✦ıra`** (the `i` as a star), the tagline **"See the keepers."**, and a
splash/About lockup. The title bar now shows a "Mira" label + a small logo
tile, but the distinctive wordmark, the tagline, and any splash/About
identity are still absent (spec/65 §0.1–§0.2).

### 3. Phases — only the optional center delta is missing
Picture16 closed this one out: the hero metric line and per-camera legend are
both built. The single remaining mockup nicety not present is a donut
**center delta** ("+22% this week" / "8 reviewed today"). Low priority.

---

## Functional stubs that look finished but aren't (from spec/73)

Not visual-fidelity, but they read as "done" on screen while doing nothing —
worth folding into the same cleanup:

- **Days Lists bulk actions** (`+ Start a new pass`, `Pick all days`,
  `Skip all days`, per-row Pick/Skip-all) are **log-only stubs**
  (`main_window.py:2217-2233`).
- **Editor crop overlay** paints the frame but has **no drag handles / aspect
  enforcement**.
- **New Cut match count** may still be cosmetic — verify the `18 of 18`
  reading is bound to the real `pool_probe` / `totals_probe`, not multiplied
  declared counts (spec/73 Tier-1 #2).

---

## Suggested order of attack

With item 1 verified built, the only true "left behind" visual work is small:

1. **Functional stubs first** (the addendum below) — Days Lists bulk actions,
   crop drag handles, match-count binding. These are buttons that look done
   and aren't; that's a trust issue, not polish.
2. **Look-preset thumbnails** (item 1) and the **brand layer** (item 2) as
   genuine last-mile polish.
3. The Phases **center delta** (item 3) is optional.
