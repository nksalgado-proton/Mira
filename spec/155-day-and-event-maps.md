# 155 — Day and event maps

**Status: PROPOSED (Nelson 2026-06-29). Photographer-trip context is
geography. Today the schedule (days list) carries day-number, date,
location *text*, and a description, but nothing visual to anchor "where
this day actually happened." This spec adds an attached **map image** to
each `trip_day` and a single attached **event map** to the event header.
The image is **user-supplied** (a JPEG/PNG the user prepares externally
— Google Earth render, Strava export, annotated screenshot, scanned
paper map). Mira never fetches tiles or renders a basemap — strict
offline-first (charter rule #3) holds. The attached image surfaces in
two places: (1) a small button on each day's row (and on the event
header) that opens an attach dialog; (2) the Cut **day-separator** slide
— when a day has a map, the spec/61 §4 separator renders the map
(letterboxed, with a blurred copy of the same image filling the bezels)
instead of the text card. spec/61 explicitly parked the "map card"
separator style as a future style — this spec lands it. Touches
`mira/store/schema.py` (one nullable column on `trip_day`, plus a tiny
event-meta entry), `core/path_builder.py` (new `Maps/` reserved
subfolder + `maps_dir()` + ensure-tree), `mira/gateway/event_gateway.py`
(read/write/copy/clear helpers), `mira/ui/pages/days_lists_page.py` (the
chip on each day row + the event-header chip), a new map-attach dialog
under `mira/ui/base/`, and the Cut separator renderer (`mira/shared/`
or wherever spec/61's separator pipeline lives). No new dependency.**

## 1. Where map files live

A new **reserved** subfolder directly under the event root:

```
<event_root>/Maps/
  event.{jpg|png}      ← optional, one event-level map
  day-01.{jpg|png}     ← per-day map for day_number = 1
  day-02.{jpg|png}     ← per-day map for day_number = 2
  ...
```

- Add `MAPS_DIR_NAME = "Maps"` + `maps_dir(event_root)` to
  `core/path_builder.py`, following the `exported_media_dir` /
  `edited_media_dir` pattern. Join `RESERVED_DIR_NAMES`. Create in
  `ensure_event_tree` alongside the other tier dirs.
- **Filename convention**: `event.<ext>` and `day-<NN>.<ext>` (zero-padded
  to 2 digits; widen to 3 if any event ever holds >99 days).
- **Extensions accepted**: `.jpg` / `.jpeg` / `.png`. Saved with the
  source's extension (no re-encoding). Anything else is rejected by the
  picker (`tr("Pick a JPEG or PNG image.")`).
- **Replacement** is overwrite-by-slot: re-picking for day 2 overwrites
  whatever sits at `Maps/day-02.<ext>` (and deletes any stale sibling
  with a different extension). Atomic write-then-rename (charter rule #6).
- **Sacred-tree note**: `Original Media/` is untouched; `Maps/` is a
  Mira-owned sibling tier, free to write and delete.

## 2. Schema additions

### 2.1 `trip_day.map_image_path`

```sql
ALTER TABLE trip_day
  ADD COLUMN map_image_path TEXT NULL;
```

Stores the path **relative to `event_root`** (e.g. `Maps/day-02.jpg`).
`NULL` = no map attached. Relative so the event folder stays portable
(spec/82 backup/restore + event migration). No index; lookups are by
day_number.

### 2.2 Event-level map

Mira's event-level metadata today lives in `event.db` rows / per-event
config (no `event` row per se in `trip_day`'s schema view — events ARE
the folder + the per-event DB). Add the event map path as a singleton
in whichever event-meta surface owns the event-level header text /
country aggregation. Concretely: extend the existing event-meta JSON
(or add a one-row `event_meta` table key `map_image_path`) so the
gateway exposes:

```py
EventGateway.get_event_map_path() -> str | None      # relative to event_root
EventGateway.set_event_map_path(rel: str | None)     # write or clear
EventGateway.attach_event_map(src: Path) -> str      # copy + set + return rel
EventGateway.attach_day_map(day_number: int, src: Path) -> str
EventGateway.get_day_map_path(day_number: int) -> str | None
EventGateway.clear_day_map(day_number: int)
EventGateway.clear_event_map()
```

The exact storage of the event-level entry is the schema author's call;
the gateway surface above is what the UI binds to.

## 3. The schedule UI

Surface 05 (`mira/ui/pages/days_lists_page.py`). Each day row already
shows location + description on one inline meta line (per the
2026-06-29 design session). Insert the map chip **between** the
location pin and the description:

```
[ 1 ] Lisbon arrival          Jun 1   [▓▓▓▓·····]
        📍 Lisbon, PT   [ + Map ]   Late landing, sunset at Miradouro
```

Two visual states:

- **Empty** — dashed-border chip, `tr("Map")` + `+` icon, muted text
  color (`#Chip[tone="empty"]` or similar — name per spec/92's catalog).
  Tooltip: `tr("Attach a map for this day.")`.
- **Attached** — solid-border chip, a 28×18 thumbnail of the attached
  map at the left of the chip, then `tr("Map")`. Tooltip:
  `tr("Day 2 map — click to replace or remove.")`. Thumbnail decoded at
  cache size; same lazy-decode path that the row's sparkline uses, no
  full-res decode on row paint.

The event header (the band above the schedule on the event landing
page) carries the same chip — same empty/attached states, same dialog —
but bound to the event-level slot rather than a `day_number`.
Tooltip: `tr("Attach a map for the whole event.")`.

QSS-only styling — `setObjectName("MapChip")` + dynamic property
`attached="true|false"` so `polish/unpolish` flips the look; no inline
`setStyleSheet`. Add the new role to spec/92 §2.

## 4. The attach dialog

A small modal (`mira/ui/base/map_attach_dialog.py`):

```
┌─ tr("Day 2 map") ──────────────────────────┐
│ ┌────────────────────────────────────────┐ │
│ │            [ preview image ]            │ │
│ │  (or "no map yet" placeholder when      │ │
│ │   the slot is empty)                    │ │
│ └────────────────────────────────────────┘ │
│ Maps/day-02.jpg · 1280 × 720 · 184 KB      │
│                                             │
│  [ Replace… ]   [ Remove ]      [ Close ]   │
└─────────────────────────────────────────────┘
```

- Title: `tr("Day {n} map")` / `tr("Event map")`.
- Preview: aspect-fit to the dialog (cap height ~280 px), background
  uses `#Card[level="2"]` from spec/92. Empty state: a neutral tile
  with `tr("No map attached.")` + a single primary button
  `tr("Pick image…")` (the `Replace…` + `Remove` row collapses to
  just the picker).
- Footer:
  - `Replace…` opens a `QFileDialog` filtered to `*.jpg *.jpeg *.png`.
    On selection, the source is **copied** (not linked) via
    `attach_day_map` / `attach_event_map` into `Maps/`, the slot's
    DB path is updated, the preview re-renders. Source file untouched.
  - `Remove` calls `clear_day_map` / `clear_event_map`: deletes the
    `Maps/...` file (atomic) and nulls the DB path. Confirms with
    `tr("Remove the map for day {n}?")` via the spec/68 `confirm`
    helper.
  - `Close` dismisses.
- After every mutation the dialog emits `mapChanged` so the day row
  (or event header) re-polishes its chip without a full page reload.

## 5. Cut day-separator — letterboxed map

This implements the "map card" style spec/61 §4 parked.

When the Cut renderer reaches a day boundary, it asks the gateway for
the day's map path. If present, the separator renders as:

1. **Background fill**: a scaled-up, **blurred** copy of the same map
   image, fitted to **cover** the slide canvas (any aspect). Blur is a
   gaussian (`QPainter` + `QImage` blur, radius ~24 px at 1080 p; scale
   the radius with output height). This is the slide's edges — it
   guarantees no black bezels, regardless of map aspect.
2. **Foreground**: the same map at full sharpness, fitted to **contain**
   the slide canvas (preserves aspect, sits centered, leaves the
   blurred bg visible on the matte sides). A 1 px translucent white
   stroke around the foreground sells the "inset photo" feeling.
3. **Caption strip**: a translucent dark strip (≈15% of slide height)
   along the bottom, carrying the existing day metadata
   (`date · location · description` — same string the v1 text card
   uses). Two lines, sentence-case, centered.

When **no map** is attached for the day, the renderer falls back to the
existing spec/61 §4 text card. No new toggle — presence of the file is
the toggle. The `use_separators` setting still gates the whole feature
(off → no separator at day boundaries at all).

For the **event-level map**: when an event map is set and the Cut's
**intro slide** style is "auto," the intro slide uses the same
letterboxed renderer with caption `tr("{event_title} · {date_range}")`.
If no event map is set, the intro slide falls back to v1 (event title
on a flat card).

### 5.1 Performance

- The blur is computed once per render at output resolution and cached
  per (event_id, day_number) for the Cut session. Cuts can have dozens
  of separators; recomputing each frame is wasteful.
- Map images are typically ≤2 MB; decoding + blur + cover-scale on a
  modern machine is <200 ms. No worker needed for v1; if it bites,
  punt to a background prepare step inside the Cut play setup.

## 6. Acceptance

- Each day's row in Surface 05 has a `+ Map` chip between location and
  description; clicking opens the attach dialog; picking a JPEG/PNG
  copies it to `<event>/Maps/day-NN.<ext>`, updates the DB, and the
  chip switches to attached state with a thumbnail.
- The event landing header has the same chip bound to the event-level
  slot (`event.<ext>`).
- `Remove` deletes the file + nulls the DB entry; the chip returns to
  empty state.
- Replacing overwrites the slot (any stale sibling with a different
  extension is deleted).
- In a Cut at a day boundary, when the day has a map the separator
  renders the letterboxed map (sharp inset + blurred fill + caption
  strip). When the day has no map, the existing text card shows. Same
  rule for the intro slide vs. event map.
- Non-JPEG / non-PNG picks are rejected at the dialog with an i18n
  error message.
- Backing up the event folder (spec/82) carries `Maps/` along; restoring
  on another machine resolves paths correctly (they're relative).

## 7. Tests

- `tests/test_path_builder_maps_dir.py` — `maps_dir()` returns
  `<root>/Maps`; `ensure_event_tree` creates it; `MAPS_DIR_NAME` joins
  `RESERVED_DIR_NAMES`.
- `tests/test_gateway_maps.py` — `attach_day_map(src)` copies to
  `Maps/day-NN.<ext>` (extension preserved), writes the relative path
  to `trip_day.map_image_path`, returns the relative path. Re-attach
  with a different extension overwrites and deletes the old file.
  `clear_day_map` deletes + nulls. Event-level helpers mirror.
- `tests/test_map_attach_dialog.py` — empty-state shows the picker
  only; attached-state shows preview + Replace/Remove/Close;
  `Remove` confirm dialog fires; non-image picks emit the i18n error.
- `tests/test_days_list_map_chip.py` — chip's dynamic property
  `attached` flips on attach / clear; chip's thumbnail updates after
  attach without a page rebuild; tooltip strings translate.
- `tests/test_cut_separator_map.py` — at a day boundary, separator
  renders the letterboxed-map form when `trip_day.map_image_path` is
  set; renders the v1 text card when null; the foreground `contain`-fits
  the canvas (golden-image-by-shape assertion: the rendered pixmap's
  bounding box of the sharp inset matches the map's aspect, not the
  canvas's). Intro-slide parity with event-level map.

## 8. v2 — MP4 maps (Nelson 2026-06-29)

**Status: LANDED.** A map can be an MP4 in addition to JPEG/PNG. The
Cut Play day-separator slot then **plays the clip** instead of holding
the still QImage.

- **Accepted extensions** expand from `(.jpg, .jpeg, .png)` to
  `(.jpg, .jpeg, .png, .mp4)`. The constant `MAP_IMAGE_EXTENSIONS` is
  renamed to `MAP_MEDIA_EXTENSIONS`; the old name lives as an alias.
- **Storage**: the slot file lands at `Maps/day-NN.mp4` /
  `Maps/event.mp4`. The schema column stays `map_image_path` (a mild
  name lie — the rename would cost a migration that buys nothing).
- **First-frame sidecar**: on attach, the gateway extracts frame 0
  of the MP4 (via the bundled ffmpeg, `core.video_extract.extract_frame`)
  to `Maps/day-NN.mp4.thumb.jpg`. Sidecar suffix is
  `MAP_VIDEO_THUMB_SUFFIX = ".thumb.jpg"`. Lives alongside the source;
  swept on clear and on extension flip.
- **MapChip + MapAttachDialog**: the chip's thumb + the dialog's
  preview load the sidecar (never run ffmpeg on every paint). The
  dialog's meta line reads `Maps/day-02.mp4 · MP4 · 4 s · 184 KB` for
  video slots vs the existing `… · 1280 × 720 · …` for stills.
  Picker filter widens to `*.jpg *.jpeg *.png *.mp4`.
- **Cut Play** (the substantive piece):
  - At the day boundary, when `trip_day.map_image_path` ends in
    `.mp4`, the slot plays the clip via the existing QMediaPlayer
    machinery instead of `_show_image(...)`.
  - **Muted** (Cuts have music; the video's own audio would compete).
    Audio output's mute flag is set on sep-video entry; cleared on
    file-video entry so user clips keep their audio.
  - **Native duration** (not `photo_s`-clamped). The slot's
    contribution to the budget becomes the probed `duration_ms`. The
    probe is cached per day in the dialog instance.
  - **One play, no loop**. Advance is EndOfMedia-driven, same as a
    file video.
  - The `_entry_class` helper returns `'video'` for sep MP4 slots so
    the spec/152 boundary crossfade math reads the right shape (half
    transition on photo↔sep-video, zero on sep-video↔file-video).
- **Cut Export**: writes the **first-frame sidecar** as the separator
  JPG. PTE bundle integration with a video slot is **parked** —
  Nelson is preparing a manual PTE example to design the contract
  against. Until then, video maps degrade gracefully to a single
  first-frame still at export.
- **Event-level video maps**: same v2 storage/dialog/chip behaviour.
  Cut Play **plays** an MP4 event map as the **opener** slot (Nelson
  2026-06-29 — first eyeball reported the still-only opener and the
  parked playback was lifted). ``CutPlayerDialog`` gains an
  ``opener_video_path`` constructor kwarg; when set, the kind=="opener"
  branch in ``_show_index`` swaps the still-render for the same video
  playback path the day-separator uses (muted, EndOfMedia advance).
  ``opener_image`` stays the rendered first-frame still so the scrubber
  hover thumb and any fallback render stay readable.
- **Helpers added**: `core.path_builder.is_video_map_path(rel)`,
  `core.path_builder.MAP_VIDEO_THUMB_SUFFIX`,
  `EventGateway._video_map_thumb_path()`,
  `EventGateway._write_video_map_thumb()`,
  `CutPlayerDialog._sep_video_path()`,
  `CutPlayerDialog._sep_video_duration_ms()` (cached).
- **Tests added**: per-extension acceptance in
  `tests/test_path_builder.py` + `tests/test_gateway_maps.py`,
  sidecar generation + sweep, dialog preview meta line for MP4,
  and a dedicated `tests/test_cut_play_video_separator.py` covering
  `_sep_video_path` / `_sep_video_duration_ms` / `_entry_class` /
  `_entry_total_ms` for sep MP4 slots.

## 9. Out of scope (parked)

- **Mira-rendered GPS traces** from EXIF (the "constellation diagram"
  considered in the 2026-06-29 design session). Stays parked: a trace
  without a basemap can't be read by a viewer outside the
  photographer's head, and offline-first rules out the basemap.
  Revisit only if real-trip usage of the JPEG slot turns out to be
  Strava-style trace exports — at which point it's a 30-line
  matplotlib helper, not a new spec.
- **Interactive maps** with captures pinned by EXIF GPS (the
  "geographic backbone" — option C in the design session). A
  separate, much larger feature; its own spec.
- **Kind tagging** (map / portrait / other) on the attached image.
  v1 says the slot is a map; if usage shows portraits-as-day-covers
  emerging, add the tag column then, driven by data.
