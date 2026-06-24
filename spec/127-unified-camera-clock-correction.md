# 127 — Unified "Camera Clock Correction" dialog (one menu, TZ segments + fine nudge)

**Status: PROPOSED (Nelson 2026-06-23). The definitive replacement for the
two overlapping Collect menu items "Camera Clocks…" and "Adjust TZ…". They
do the same underlying thing — set each camera's correction offset and call
`recompute_corrected_times` — but with two **inconsistent** multi-TZ models:
Camera Clocks collapses the trip to one predominant TZ (ignores the second
zone entirely); Adjust TZ makes the user hand-type per-day deltas to
re-encode a zone the schedule already knows. Collapse both into **one**
dialog built on spec/123's model: per camera, per **trip-TZ segment**, the
correction is a **base** (Correct / known TZ / measured pair) **plus an
optional fine nudge** (±MM:SS for a clock that's simply wrong, e.g. the
GoPro running 3 min fast after its TZ fix). Segments come from the schedule,
not the user. This **supersedes spec/125** (folds in the offset-honest
representation) and retires the Adjust-TZ per-day matrix. Touches
`mira/ui/pages/camera_clock_dialog.py` (rebuilt) + `adjust_event_tz_dialog.py`
(retired/merged), `mira/ui/shell/main_window.py` (one menu item), and a
per-(camera, segment) correction store (migration). Integer seconds, H:M:S
throughout (spec/123). Honors the spec/126 ingest guard.**

## 1. The model

The canonical per-item truth stays `item.capture_time_corrected = raw +
total_offset` with `tz_offset_seconds` + reassigned `day_number` (spec/123,
virtual-EXIF). The dialog is a UI for setting `total_offset` per camera **per
trip-TZ segment**.

### 1.1 Trip-TZ segments
A **segment** = the set of plan days sharing one trip TZ (the schedule's
`trip_day.tz_minutes`). A normal trip = one segment; a TZ-crossing trip
(e.g. Nepal +5:45 with a day at India +5:30) = two. Keyed by the segment's
`trip_tz_seconds`. This mirrors Collect's `tz_camera_groups`
(`{trip_tz_hours: [CameraInput]}`, reconcile_pipeline.py) — the menu must use
the **same** per-segment model Collect already uses, instead of a single
`Counter(...).most_common(1)` TZ.

### 1.2 Correction = base + fine nudge
For each camera × segment (only segments where the camera **has captured
items** — filter out cameras absent from a segment's days):

- **base** — a spec/123 source:
  - *Clock was correct* → 0.
  - *Camera was on TZ X* → `segment_trip_tz − camera_tz` (zone picker).
  - *Measured pair* → raw delta from the sync-pair picker (spec/124, no
    snapping).
- **fine nudge** — optional `±H:MM:SS`, added on top, for residual clock
  error independent of zone (the GoPro 3-min case: base `+8:45`, nudge
  `−0:03:00` → `+8:42:00`).
- `total_offset_seconds = base + nudge`.

Apply each via `recompute_corrected_times(camera_id, total_offset_seconds,
day_number=…)` **scoped to that segment's days** (loop the segment's days, or
extend recompute to accept a day set). A camera spanning two segments gets
its right offset per segment — the second adjustment is no longer dropped.

## 2. Data model (migration)

The per-camera single `applied_offset_seconds` / `configured_tz_seconds`
cannot hold two segments. Persist a per-(camera, segment) correction:

`camera_tz_correction(camera_id, trip_tz_seconds, configured_tz_seconds
NULL|int, nudge_seconds INT DEFAULT 0, applied_offset_seconds INT,
applied_at TEXT)` — PK `(camera_id, trip_tz_seconds)`.

- `configured_tz_seconds` set → base was a declared zone (source 1);
  NULL → base was a measured pair / manual (source 3). This is the spec/125
  discriminator, now **per segment**.
- `nudge_seconds` → the fine adjustment.
- `applied_offset_seconds` → the total actually applied (base + nudge),
  denormalized for quick reconstruction.

Migrate existing `camera.applied_offset_seconds` / `configured_tz_seconds`
into a row keyed by the event's (single) trip TZ; then the camera-level
columns are retired (or kept as a read-only summary). Reconstruction reads
these rows; the dialog never back-derives a zone from an offset (kills the
spec/125 "+0:45" artifact by construction).

## 3. Dialog UX

One dialog, one Collect menu item ("Camera Clock Correction…"), replacing
both. `main_window` opens it; the old `_open_camera_clocks_for_event` /
`_open_adjust_tz_for_event` collapse into one handler.

- **Header** names the segments from the schedule, e.g. *"Days 1–6: +5:45 ·
  Day 7: +5:30."* (A single-segment trip shows no segment chrome.)
- **Per segment**, a section listing the cameras present in that segment.
  Each camera row:
  - **State**: *Clock was correct* / *Camera was on a known TZ* (zone
    picker, seeded from stored `configured_tz_seconds`) / *Measured offset*
    (raw `±H:MM:SS` via `HmsEntry`, or **Pick a pair…** → sync-pair picker).
  - **Fine nudge**: a small `±MM:SS` `HmsEntry`, default `00:00`.
  - **Resulting offset**: read-only `total = base + nudge`, in H:M:S.
- **Apply** writes the per-(camera, segment) rows and runs the day-scoped
  recompute for each changed row. Unchanged rows skip (the existing
  short-circuit).
- **Offset-honest representation** (spec/125, folded in): show a real zone
  only when `configured_tz_seconds` is set; otherwise show the raw offset.
  Never fabricate a "Custom TZ."

## 4. Guards + relationships

- **Ingest guard (spec/126):** the unified handler checks
  `is_ingesting(event_id)` and shows the "still finishing import" notice
  rather than reading a half-written db; refresh on ingest-finish applies.
- **Supersedes spec/125** (offset-honest representation folded in here).
- **Retires** the Adjust-TZ per-day delta matrix; per-day *drift* (a clock
  that drifts differently each day) is consciously **not** modeled — spec/123
  already dropped per-day drift from the applied path. A rare future need is
  a separate add-on.
- **Independent / still stand:** spec/124 (pair-picker warning — reused as
  the measured-pair source) and spec/126 (ingest race).

## 5. Acceptance

- A TZ-crossing trip shows a section per segment; a camera with photos in
  both gets the correct offset in **each** (the second adjustment is applied,
  not ignored); a camera absent from a segment doesn't appear in it.
- A single-TZ trip shows one simple section (one row per camera) — no
  regression in the common case.
- The GoPro corrected by zone (`−3 → +8:45`) plus a `−0:03:00` nudge lands at
  `+8:42:00`; re-opening round-trips the zone **and** the nudge.
- A measured-pair camera shows its raw offset (e.g. `+5:00:00`), never a
  fabricated "+0:45 Custom TZ"; round-trips exactly.
- Applying scopes each correction to its segment's days only; raw capture
  time never mutated; days reassign from corrected time.
- One Collect menu item; the two old ones are gone.

## 6. Tests

- `tests/test_tz_segments.py` — segment derivation from `trip_day.tz_minutes`
  (one TZ → one segment; two → two, with the right day sets); camera presence
  per segment from `items(camera_id, day)`.
- `tests/test_unified_correction_apply.py` — a 2-segment trip applies the
  per-segment total offset scoped to each segment's days; a camera in both
  segments gets both; recompute scoping never touches the other segment's
  days; raw untouched.
- `tests/test_correction_base_plus_nudge.py` — base (zone/pair/0) + nudge sums
  to the applied offset; GoPro `+8:45` + `−0:03:00` = `+8:42:00`; round-trip
  through the `camera_tz_correction` store (zone stays zone, NULL stays NULL).
- `tests/test_camera_tz_correction_migration.py` — existing single
  `camera.applied_offset_seconds`/`configured_tz_seconds` migrate to a row
  keyed by the event trip TZ.
- Ingest-guard regression (spec/126) on the unified handler.

## 7. Implementation plan (commit order)

1. **Segments + store** — segment derivation helper (plan days → `{trip_tz:
   [day_numbers]}` + cameras-present); `camera_tz_correction` table +
   migration from the single camera columns; day-scoped apply (extend/loop
   `recompute_corrected_times` over a segment's days).
2. **Unified dialog** — rebuild on the segment × (base + nudge) model;
   offset-honest representation (supersede spec/125); `HmsEntry` for offset +
   nudge; sync-pair picker as the measured-pair source.
3. **One menu item** — merge the two handlers in `main_window`; honor the
   spec/126 ingest guard + refresh; remove `adjust_event_tz_dialog.py` and
   the old Camera-Clocks handler.
4. **Cleanup** — retire dead per-day-matrix code + tests; update spec/125
   header to "superseded by spec/127"; note the merge in any menu docs.
