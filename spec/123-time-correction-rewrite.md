# 123 — Time-correction rewrite: three explicit sources, integer seconds, H:M:S everywhere

**Status: SHIPPED (Nelson 2026-06-23) in 4 commits.
C1 schema+apply (`schema.py`, `models.py`, `event_gateway.py:3930`):
`applied_offset_minutes→applied_offset_seconds`,
`configured_tz_minutes→configured_tz_seconds`,
`item.tz_offset_minutes→tz_offset_seconds` (lossless ×60; `trip_day.tz_minutes`
stays minutes); `recompute_corrected_times(offset_seconds)` applies to ALL
captured items incl. video, raw never mutated, honest day reassignment (a
corrected date with no planned day → `None`, never keeps the stale day).
C2 derivation+reverts (`clock_calibration.py`, `exif_reader.py`,
`fresh_source.py`, `model.py`, `plan.py`, `reconcile_pipeline.py`): three
sources `offset_from_known_tz` / `offset_from_simultaneous` /
`offset_from_measured_pair` (raw delta, nearest second, no snapping);
spec/122 reverted (dropped `capture_time_is_utc` + UTC detection +
`timestamps_are_utc` plumbing); snap kept only as recognition-UI
clustering, never in the applied path.
C3 H:M:S UI (`adjust_event_tz_dialog.py`, `camera_clock_dialog.py`): new
`HmsEntry` (`±H:MM[:SS]`→seconds; `parse_hms_to_seconds("5:45")==20_700`,
decimal `5.45` rejected); camera-clock dialog drops the UTC checkbox, flows
integer seconds.
C4 cleanup: spec/88 + spec/122 headers note supersede/revert; removed
`test_clock_calibration_utc_source.py` + `test_exif_reader_video_utc.py`.
Tests: `test_tz_correction_sources.py` (Nepal 5h00m02s = 18_002 s, not
snapped), `test_recompute_seconds.py` (video included, raw untouched, no
stale-day), `test_adjust_tz_dialog_hms.py` (5:45→20_700, rejects 5.45),
v16→v17 migration in `test_store.py`. verify.bat: 4703 passed, 276 skipped,
9 failed = the documented `test_focus_keeper` colocated-Qt flakes (9/9 in
isolation). Original proposal follows.**

**Status: PROPOSED (Nelson 2026-06-23). The camera clock / TZ correction
has accumulated competing theories — TZ-grid snapping (spec/101, reverted),
UTC reinterpretation (spec/122, to be reverted here), decimal-hours entry —
and the net result is wrong: GoPro clips get **zero** correction from the
"I know the camera's TZ" path (proven: forcing the offset by hand fixes
them), and the force-TZ dialog applies the wrong amount because it takes
**decimal hours** (`5.45` read as 5h27m, not 5:45). Rewrite the correction
from scratch around **one offset per camera (integer seconds)** derived from
**three explicit sources**, applied through **one** virtual-EXIF function,
with **H:M:S** notation everywhere and **no snapping, no UTC special-case,
no decimal hours**. Supersedes spec/101's snap model and **reverts
spec/122**. Touches `core/clock_calibration.py`, `core/clock_recognition.py`,
`mira/ui/pages/camera_clock_dialog.py`,
`mira/ui/pages/adjust_event_tz_dialog.py`,
`mira/gateway/event_gateway.py::recompute_corrected_times`,
`core/fresh_source.py`, `core/exif_reader.py` (revert 122), and a
`*_minutes → *_seconds` schema migration.**

## 1. The model — one offset per camera, three sources

A camera's clock error is a single number: **`offset_seconds`** = how much to
**add** to the camera's recorded time to land on trip-local time
(`corrected = raw + offset`). It is stored once per camera (integer
seconds). It has exactly **three** derivation sources; the UI picks one, all
three produce the same kind of number, and all flow through the same apply
path (§3):

1. **Known TZ** — the user states which zone the camera's clock was on.
   `offset = trip_tz − camera_tz`, computed in **minutes/seconds** (never
   decimal hours). Nepal GoPro: `+5:45 − (−3:00) = +8:45 = +31 500 s`.
   Zones are whole 15-minute steps, so this is always a whole-minute offset.

2. **Recognized "these two were the same moment, clock looked right"** —
   the user confirms a presented pair is simultaneous **and** the camera
   needed no shift. `offset = 0`. (The "no correction necessary" outcome.)

3. **Measured pair** — the user picks a pair (one camera shot + one
   reference shot) they know is the same moment. `offset = reference_time −
   camera_time`, **applied as measured**, rounded to the nearest **second**
   (it already is seconds). **No TZ-grid snapping.** The pair *is* the
   measurement; snapping substitutes an assumption ("the error must be a
   clean zone") that is false in general — proven by the Nepal pair (5h00m02s
   measured, no zone is −5:00 from Kathmandu, snapping invented 4:45/5:45).
   Apply the ~5h the pair shows.

Sources 1 and 3 are two ways to get one number; source 2 is the zero case.
Nothing infers a zone from the measured delta; nothing snaps.

## 2. What is removed / reverted

- **Snapping** — `snap_to_tz_offset` / the 15-minute-grid logic in
  `core/clock_recognition.py` (spec/101 model) is removed from the applied
  path. `find_candidate_pairs` may still *present* near-simultaneous
  candidates to help the user choose (source 2/3), but the **offset it
  yields is the raw measured delta**, not a snapped value.
- **spec/122 UTC reinterpretation** — revert: drop
  `PhotoExif.capture_time_is_utc`, `_detect_capture_time_is_utc`, the
  `timestamps_are_utc` plumbing through `build_calibration` /
  `build_tz_calibrations` / `CameraInput` / `CameraPlan`, and the dialog's
  "Timestamps are UTC" checkbox. The GoPro is simply **source 1** (`−3 →
  +5:45`); its mvhd time is treated like any other camera's recorded time.
  (The empirical UTC observation was a wrong inference from a recovery-tool
  filename; the user-confirmed correct answer is the pure two-TZ +8:45.)
- **Decimal hours** — no UI ever shows or accepts `5.75` / `8.45`. See §4.

## 3. The single apply path (keep virtual-EXIF)

Preserve the sound part of the current design: never mutate
`capture_time_raw`; write `capture_time_corrected`; reassign `day_number`.
Rewrite `recompute_corrected_times` to:

- Take `offset_seconds` (integer).
- For **every** captured item of the camera (photos **and** videos —
  verify the GoPro videos are matched; the zero-correction bug must be gone):
  `corrected = raw + offset`; reassign the day from the corrected date
  against the plan; on a date with no planned day, fall through to the
  corrected date's natural day / undated — **never silently keep the
  stale pre-correction day**.
- Store the offset on the camera and `tz_offset_seconds` on the item.

This one function is shared by the camera-clock dialog (source 1/2/3) and
the force/adjust-TZ dialog (direct entry). There is no second code path.

## 4. H:M:S notation everywhere (kills the decimal-hours bug)

Every offset/TZ the user sees or types is **H:M:S** (or `+HH:MM` for
zones), backed by integer seconds — **never** a decimal-hours float.

- **`adjust_event_tz_dialog.py`** (the menubar force-TZ dialog): replace the
  decimal-hours spinner with an **H:M:S** entry (sign + hours + minutes +
  seconds, or a masked `±H:MM:SS` field). `5:45` means 20 700 s, full stop —
  the source of the "off by minutes" error.
- **`camera_clock_dialog.py`**: zone pickers display `+5:45` / `−3:00`;
  internally integer seconds.
- No `value()/60.0` decimal-hours arithmetic anywhere in the corrected path.

## 5. Storage — integer seconds (migration)

Migrate the minute columns to seconds (lossless, ×60), so the measured-pair
offset and H:M:S entry are exact:
`camera.applied_offset_minutes → applied_offset_seconds`,
`item.tz_offset_minutes → tz_offset_seconds`,
`camera.configured_tz_minutes → configured_tz_seconds`,
`trip_day.tz_minutes → tz_seconds` (or keep day TZ in minutes and convert at
read — zones are whole minutes). One forward migration; read-compat shim if
any legacy reader remains.

## 6. Acceptance

- **Known TZ (the regression):** setting the GoPro to `−3` on a `+5:45` trip
  applies **+8:45** to **all** its items including videos; evening clips
  move to the correct next day. No more zero-correction.
- **Force dialog:** entering `5:45` applies exactly 5h45m (not 5h27m);
  `8:45` applies 8h45m. No decimal-hours field remains.
- **Recognized simultaneous:** correction is 0.
- **Measured pair:** the Nepal 5h00m02s pair applies ~**+5:00** (the
  measured delta, nearest second), NOT 4:45/5:45; no snapping occurs.
- spec/122's UTC checkbox and detection are gone; GoPro works purely via
  source 1.
- Raw capture time is never mutated; days reassign from corrected time.

## 7. Tests

- `tests/test_tz_correction_sources.py` — source 1 `trip−camera` (Nepal
  GoPro → +31 500 s); source 2 → 0; source 3 → raw measured delta to the
  second (5h00m02s pair → 18 002 s, NOT a snapped 4:45/5:45); no code path
  snaps.
- `tests/test_recompute_seconds.py` — `recompute_corrected_times` applies
  `offset_seconds` to **all** of a camera's captured items (a video item is
  included), reassigns day from corrected date, never touches raw, never
  keeps a stale day on a planned-date hit; the GoPro evening fixture lands
  on the next day.
- `tests/test_adjust_tz_dialog_hms.py` — H:M:S entry maps `5:45 → 20 700 s`
  (regression against the decimal-hours `5.45` bug); round-trip display.
- Migration test: `*_minutes ×60 → *_seconds`.
- Remove/replace the spec/122 UTC tests and the spec/101 snap tests.

## 8. Implementation plan (commit order)

1. **Schema + apply core** — `*_minutes → *_seconds` migration; rewrite
   `recompute_corrected_times(offset_seconds)` (virtual-EXIF, all items incl.
   video, honest day reassignment). Unit-tested standalone.
2. **Three-source derivation** — collapse `clock_calibration` to produce one
   `offset_seconds` from (TZ diff | 0 | measured pair raw); delete snapping
   from the applied path; revert spec/122 (exif_reader + plumbing + dialog
   checkbox).
3. **H:M:S UI** — rewrite the force-TZ spinner and the camera-clock zone
   widgets to H:M:S / `±HH:MM`; remove all decimal-hours arithmetic.
4. **Cleanup** — retire dead snap/UTC code + tests; update spec/101 +
   spec/122 headers to "superseded/reverted by spec/123".
