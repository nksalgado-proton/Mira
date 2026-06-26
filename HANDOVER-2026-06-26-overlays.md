# Handover — 2026-06-26 — Overlay system (events + cross-event)

Big session. Everything below is **committed + pushed to `main`**. The
suite is green except two long-standing unrelated items (see bottom).

## What shipped this session (commit → topic)

1. `d462da8` — **fix: capture exposure EXIF through all ingest paths +
   backfill.** Exposure (aperture/shutter/iso/focal) was dropped in
   `source_index.py`, `past_photos_dialog.py`, and the Capture/offload
   flow → item rows stored NULL → overlay's "Exposure" line never drew.
   Fixed all paths. `scripts/backfill_item_exposure.py` repairs existing
   events (ran it: 4644 items across the user's 4 events).
2. `d1ccbdc` — **single-line, photo-anchored overlay pill + configurable
   text size.** The on-photo pill is one line, pinned to the displayed
   image's bottom edge (not the view). Size is the `overlay_exif_font_px`
   setting (default 9), a `{overlay_exif_font_px}` QSS token.
3. `0fbabfd` — anchor Quick Sweep pill to the photo edge too.
4. `85ba54c` — **`show_photo_overlays` master flag** (default ON) gates
   the pill across Quick Sweep / Picker / Editor (retired the QS-only
   `show_exposure_overlay`). Cuts keep their own per-Cut control.
5. `6c17abe` — **spec/153: PTE separate-text overlays.** The generated
   `.pte` carries overlay text as separate `:Text` objects over flat
   swappable backgrounds (photo caption / day separators / opener).
6. `6b5e9b4` — **spec/153: retire burn-in.** Overlay control = just the 4
   field flags (When/Where/Camera/Exposure). `overlay_mode` fixed to
   `"embedded"` internally (vestigial column — NOT purged).
7. `652f385` — re-flatten stale baked opener/separator cards on PTE gen
   (self-healing when regenerating without a full re-export).
8. `9c40f50` — fixed 2 pre-existing test failures (opener-independence
   assertion; moved a PTE fixture into `tests/data/`).
9. `9ae0713` — **spec/154: cross-event Play opener** (was blank → renders
   a provenance card: Cut name + source events + frame count).
10. `48ef343` — **spec/154: cross-event Play separators** labelled by
    source-event name + date (the `Day (tuple)` bug).
11. `961aa1d` — **spec/154: cross-event Play live photo captions**
    (When/Where/Camera/Exposure), composed from the `global_items`
    projection.

## The loose-ends list we were working through ("one by one")

- [x] Pre-existing test failures → `9c40f50`.
- [~] **Cross-event Cut overlays (spec/154)** — IN PROGRESS. 3 of 5 slices
      done (all Play). **Remaining: see next section.**
- [ ] Vestigial `cut.overlay_mode` column purge (it's always `"embedded"`
      now; safe to drop with a settings/schema cleanup — low priority).
- [ ] Reverse-migration fixture cleanup — **spawned as a background task
      chip** (the duplicated strip-this-column-in-N-fixtures pattern).

## spec/154 — cross-event overlays: what's left

Read **`spec/154-cross-event-pte-overlays.md`** first. Design (Nelson
approved): cross-event Cut = a *search result*; overlays explain
**provenance**, not a story. Three text kinds, on BOTH surfaces (Play =
text baked into cards / live caption overlay; PTE = separate editable text
objects), **composed once, reused** by both surfaces.

**DONE (Play):** opener card, separators (event-name title), live photo
captions.

**REMAINING:**

### A. Origin label (top of slide) + its flag
- A NEW per-slide line at the **top**: source **event name + capture
  date** (e.g. `Salta, Argentina · 28 Sep 2025`). Own on/off flag
  ("Source label per slide"), independent of the 4 field flags.
- Player today has ONE bottom overlay label (`CutPlayOverlay` in
  `cut_play.py`). The origin label needs a **second, top-anchored** label
  (mirror the `_position_overlay` / `foreground_rect` anchoring, but top).
  Feed it a per-payload origin string. For cross-event the data is on the
  payload already (`CrossEventPlayFile.event_uuid` + `capture_time`) +
  event names via `list_events_for_scope()`.
- Add the flag to the New Cross-event Cut dialog
  (`mira/ui/pages/new_recipe_dialog.py`, the `_build_overlay_box` area;
  cross-event uses the same dialog under `INVENTORY_LIBRARY`). Persist on
  the cross-event Cut (a new bool — `extras_json`, or a column).

### B. The whole PTE side for cross-event
- `library_page._generate_cross_event_pte_into_folder` currently builds
  **bare** `PteMember`s (no texts) and `cross_event_cut_export` wires **no
  opener/separator writers**. Mirror the per-event work (spec/153):
  - In `cross_event_cut_export`: wire a flat `opener_writer` +
    per-(event,day) flat `separator_writer` (use
    `separator_card.render_flat_background`).
  - In the cross-event PTE gen: detect card slides, re-flatten, and emit
    `PteText`s (opener title/sub, sep title/sub, photo caption, origin
    label) — reuse `pte_project.PteText` + roles
    (`TEXT_OPENER_TITLE/SUB`, `TEXT_SEP_TITLE/SUB`, `TEXT_PHOTO_CAPTION`;
    add a `TEXT_ORIGIN`/top role).
  - Compose the texts from the same helpers used for Play (see "reuse"
    below) — captions via `cross_event_provenance_resolver`, separators
    via the event-name map, opener via `_cross_event_opener_lines`.

## Key functions/files (cross-event)

- `mira/shared/cross_event_cut_play.py` (pure, no Qt):
  - `build_cross_event_entries` — opener now ALWAYS rides (decoupled from
    separators_on); separator `_SeparatorMeta.title` = source event name.
  - `cross_event_provenance_resolver(lg, cut_id)` → `(payload) →
    FrameProvenance`, built from `global_items` (one query). **The
    `global_items` projection has EVERY overlay field** — capture_time,
    country, day_city, day_sublocation, camera_id, lens_model, flash_fired,
    iso, aperture_f, shutter_speed_s, focal_length_mm — so NO need to open
    source events.
- `mira/ui/pages/library_page.py`:
  - `_on_play_cut` — wires `opener_image` + `overlay_fields` (parsed from
    `cut.overlay_fields_json`) + `provenance_resolver`.
  - `_cross_event_opener_lines(lg, cut)` / `_cross_event_opener_image` —
    the opener composer + render (reuses `render_cut_opener_image`).
  - `_generate_cross_event_pte_into_folder` (~L726) — the PTE gen to extend.
- `mira/ui/shared/cut_play.py`:
  - `_update_overlay` — the overlay resolver now takes the **PAYLOAD**
    (not just relpath), so cross-event keys on (event_uuid, relpath);
    event-scope wraps at `share_cuts_page.py` (~L2440).
  - `_separator_image` — passes `title=meta.title` (cross-event override).
- `mira/ui/shared/separator_card.py`:
  - `render_separator_image(..., title=None)` — title override; robust to
    non-int `day_number` ("More moments" fallback).
  - `render_flat_background(...)` — text-less swappable bg (spec/153).
  - `render_cut_opener_image(tag_text, lines, ...)` — **the generic
    title+lines card renderer**; reuse it for cross-event cards.

## The reuse backbone (user's explicit ask)

Compose the slide text content ONCE; three consumers:
1. card renderers (`render_cut_opener_image` / `render_separator_image`) →
   bake into Play cards ("burned in");
2. live caption overlay (`CutPlayOverlay`) → draws caption (+ origin) live;
3. PTE generator (`pte_project.PteText`) → separate editable text objects.
Event + cross-event each supply their own provenance source. For
cross-event, captions reuse `cross_event_provenance_resolver`; the per-event
side lives in `share_cuts_page._pte_slide_texts` + the card renderers.

## Per-event PTE overlays (spec/153) — DONE, for reference

- `mira/shared/pte_project.py`: `PteText(text, role)`, `PteMember.texts`,
  `_TEXT_STYLE` table (THE place to tune the look), `_text_object` emitter,
  `_inject_texts`. Roles: `photo_caption`, `sep_title`, `sep_sub`,
  `opener_title`, `opener_sub`.
- `share_cuts_page.py`: flat opener/separator writers,
  `_pte_slide_texts`, `_cut_photo_caption` (single line, `  •  ` sep),
  `_pte_card_text_context`, `_reflatten_card_image`.

## Gotchas / environment

- **Tests**: PyQt6 often crashes on interpreter teardown on Windows AFTER
  the summary prints (`0xC0000409`). Judge by the "N passed" line, not the
  exit code. Don't re-run `verify.bat` (user pref; ~min, pops Qt windows).
- **Two PRE-EXISTING failures**, unrelated, do NOT block:
  `test_grab_originals::test_v8_rows_migrate...` and
  `test_user_exposure::test_migration_v15_to_v16...` →
  `sqlite3.OperationalError: duplicate column name: transition_ms` at
  `schema.py:1528` (a broken migration; the "user_exposure" there is the
  editor tone slider, not EXIF). These are the migration-fixture issue
  spawned as a chip.
- The user's working tree has many `PTE example/*.jpg` + `*.pte` DELETIONS
  (their reorganisation, uncommitted) — leave them; stage only your files.
- Throwaway demo artifacts (deletable): `PTE example/mira_generated_sample.pte`
  and `D:/Photos/_mira_library/Cuts/salta_argentina/trip_long/_sample_flat_*.jpg`.
- Push without asking on this repo (solo trunk; user pref). Commit
  per-slice; the user reviews by running the app.
