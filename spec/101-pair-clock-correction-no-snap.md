# 101 — Pair-based clock correction must apply the RAW offset, not the TZ snap

**Status: SHIPPED (Nelson 2026-06-22) for the core change — the no-TZ
branch in `mira/ui/base/sync_pair_picker.py` now sets
`_final_offset = round(raw, minutes)` (was: `snap_to_tz_offset(raw)`),
so a sub-15-min clock error is preserved instead of rounded away.
`build_calibration` consumes the `CalibrationPair.offset` as a
`timedelta` verbatim, so the ingest path carries the raw offset
through with no further wiring. The TZ-declared branch is unchanged
(a declaration is genuinely grid-aligned).**

**Existing-event apply path — HELD (out of scope for this PR).** Audit
of the Mira UI surfaces (`mira/ui/shell/main_window.py`,
`mira/ui/pages/past_photos_cameras.py`, `mira/ui/pages/clock_recognition_dialog.py`,
`mira/ui/pages/past_photos_dialog.py`, `mira/ui/pages/offload_calibration_dialog.py`)
shows `SyncPairPickerDialog` is only opened from the **ingest / new-event**
flow (PastPhotosCamerasDialog / clock_recognition / past_photos_dialog).
The **existing-event** clock correction entry (`_open_camera_clocks_for_event`
→ `CameraClockDialog`) is whole-TZ only — it never opens
`SyncPairPickerDialog`. So the spec/101 bug bites the new-event ingest
path today; an existing-event "correct from a pair" entry would need a
new dialog + new menu / plan-page entry + new wiring. Re-open spec/101
to add it the moment a user needs it after ingest has run.

Touches `mira/ui/base/sync_pair_picker.py` (the apply value); revises
the spec/45 / spec/88 assumption that a hand-picked pair is always a
*timezone* declaration. No keymap / charter-invariant impact; the
ordering and day-assignment code is untouched (it was faithfully
sorting by `capture_time_corrected` — it was just being handed a
grid-rounded value).

## 1. The bug

`sync_pair_picker` computes the true measured delta `raw = reference_time
− camera_time` and shows it ("Δ raw"), then applies a **snapped** value:

```python
snapped = snap_to_tz_offset(raw)     # nearest 15-min multiple
self._final_offset = snapped         # ← the value actually applied
```

`selected_pair()` builds the `CalibrationPair` so the engine's `reference
− camera` math yields `_final_offset` — i.e. the snapped offset, never
`raw`. Both branches apply a grid value: the TZ-declared branch applies
`tz_expected`, the no-TZ branch applies `snap_to_tz_offset(raw)`. There is
**no path that applies the precise measured offset.**

Consequence: if the camera's real clock error is 6 min → snaps to 0 (no
correction); 8 min → snaps to 15 (over-correction). The few-minute offset
that interleaves the photos correctly is thrown away. Symptoms: photos
out of order within a day, and a near-midnight frame that should cross
into the adjacent day doesn't (wrong day).

## 2. The root confusion

One gesture (pick a camera photo + a phone photo) serves two DIFFERENT
intents that were collapsed onto one snapped path:

- **"Declare / confirm a timezone."** The 15-minute grid is *correct*
  here — real-world UTC offsets land on that grid (spec/45, spec/88's
  recognition flow). Snap is right.
- **"Measure this camera's clock error / drift."** The whole point is an
  *arbitrary* offset (the clock was set wrong by N minutes/seconds).
  Snapping to 15 min is exactly wrong — it destroys the signal.

**Key insight: applying the RAW offset is never worse than snapping.** If
the camera was set to a clean timezone, `raw` already equals the grid
point (± a few sub-minute seconds), so applying raw gives the same result
as the snap. If the camera had a clock error, raw captures it. The snap
can only discard real information — it never adds any.

## 3. The fix

In `sync_pair_picker`, the no-TZ (measure-from-pair) branch applies the
**raw** measured offset, not the snap:

- `self._final_offset = raw` (rounded to whole minutes at the apply
  boundary, since `recompute_corrected_times` takes
  `applied_offset_minutes: int` — minute precision is what ordering needs;
  sub-minute is noise).
- The 15-min `snapped` / `snap_disagreement` value becomes a **display-only
  sanity hint**, not the applied value: "looks like UTC±X; your pair is N
  off a clean timezone grid." Keep the existing `snap_diff > 5 min`
  warning (re-anchored to `snap_disagreement`) — a large disagreement
  still means a likely mis-timed pair, which the 15-min-apart gate
  (`MAX_PAIR_RAW_DELTA`, spec/88) and this warning both guard.
- The TZ-declared branch (`within` → `tz_expected`) is **unchanged** — a
  declaration is genuinely grid-aligned.

`selected_pair()` already derives `reference_time = camera_time +
_final_offset`, so once `_final_offset` is raw, the engine consumes the
precise offset with no further change.

## 4. Carry the raw offset through to apply

Trace and preserve the offset end-to-end (the implementer confirms the
exact wiring):

- **Ingest path** — `build_calibration` consumes the `CalibrationPair`
  offset as a `timedelta`; sub-minute is fine. Apply raw verbatim.
- **Existing-event path** — whichever dialog routes the pair to
  `EventGateway.recompute_corrected_times(camera_id,
  applied_offset_minutes=…)`: ensure the raw offset (rounded to whole
  minutes) is what reaches `applied_offset_minutes`, not a snapped value.
  If the existing-event correction currently only routes through the
  TZ-declaration `CameraClockDialog` (whole-TZ), add the pair as a
  first-class "correct this camera's clock from a pair" entry that feeds
  the raw minute offset.

## 5. Acceptance

- A pair whose raw delta is a non-grid value (e.g. 6 min, 1 h 07 min)
  applies that offset to the minute; the camera's photos interleave in
  correct chronological order with the phone / other cameras in the day
  grids, and near-midnight frames land on the correct day.
- A pair whose raw delta is a clean timezone (e.g. exactly +5:45) applies
  the same correction it does today (raw ≈ grid → identical result).
- The "declare a timezone" branch is unchanged.
- The mis-timed-pair warning still fires when raw is far from any grid
  point.

## 6. Tests

- `tests/test_sync_pair_apply_raw.py` — `selected_pair()` for a 6-min raw
  delta returns a `CalibrationPair` whose `.offset` is 6 min (not 0); a
  1 h 07 min delta returns 67 min (not 60/75); a clean +5:45 returns 5:45.
- An end-to-end test through `recompute_corrected_times` asserting the
  affected items' `capture_time_corrected` shift by the raw minutes and
  re-order correctly within/across days.
- Regress the existing pair-picker / clock-recognition tests and the
  `snap_disagreement` warning behaviour.
