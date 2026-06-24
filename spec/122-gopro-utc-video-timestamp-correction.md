# 122 — Fix: GoPro (QuickTime-UTC) video timestamps are corrected 3h wrong

**Status: REVERTED by [spec/123](123-time-correction-rewrite.md) (Nelson
2026-06-23). The UTC inference (mvhd-as-UTC + a per-camera "Timestamps
are UTC" checkbox) was a wrong reading of the GoPro evidence: the
empirical observation came from a recovery-tool filename, not the
embedded clock. The user-confirmed correct answer is that the GoPro is
simply **source 1** in the spec/123 three-source model — its
configured TZ was −3, the trip was +5:45, so the offset is +8:45 (not
+5:45 alone). spec/123 reverts every artifact: drops
`PhotoExif.capture_time_is_utc` + `_detect_capture_time_is_utc` /
`_has_local_offset_signal` / `_has_tz_trailer`, the
`timestamps_are_utc` plumbing through `build_calibration` /
`fresh_source.build_tz_calibrations` / `CameraInput` / `CameraPlan` /
ingest engine + plan, and the per-camera "Timestamps are UTC" checkbox
in `camera_clock_dialog.py`. The 25-test UTC suite is retired in
favour of `test_tz_correction_sources.py` (the three-source unit
tests).**

**Original status: SHIPPED (Nelson 2026-06-23) in 3 commits.
C1 reader (`core/exif_reader.py`): new `PhotoExif.capture_time_is_utc`
(naive value kept; flag records UTC-ness); `_pick_capture_timestamp` makes
the fallback chain explicit so the detector sees the winning field;
`_detect_capture_time_is_utc` = True when `Make=GoPro` OR (QuickTime
container field won AND no local-offset signal — no `OffsetTimeOriginal` /
`DateTimeOriginal` / `CreationDate`-TZ-trailer); out-of-spec local-into-UTC
stays False (dialog override flips it); `_has_local_offset_signal` +
`_has_tz_trailer` helpers.
C2 calibration (`core/clock_calibration.py` + `core/reconcile_pipeline.py`
+ `mira/ingest/model.py` + `engine.py` + `plan.py`):
`CameraInput.timestamps_are_utc` + `CameraPlan.timestamps_are_utc`;
`build_calibration(..., timestamps_are_utc=False)` treats `configured_tz`
as 0 when True → offset = `+trip_tz` alone (and a UTC source with
`configured_tz=None` still calibrates — the instant is unambiguous);
threaded through both reconcile builders + ingest engine + plan.
C3 dialog (`mira/ui/pages/camera_clock_dialog.py` + `core/fresh_source.py`):
per-camera "Timestamps are UTC" checkbox (col 4), default = auto-detected
provenance, user-overridable; `_live_answers`/`result_answers` carry the
flag; `_utc_cameras` → `build_tz_calibrations(..., utc_cameras=…)`, UTC flag
WINS over a user-declared wrong configured_tz.
25 new tests green: `test_exif_reader_video_utc.py` (pure detection +
`read_exif_batch` with mocked exiftool) and
`test_clock_calibration_utc_source.py` (Nepal fixture UTC 07:55:34 + trip
+5:45 + configured −3:00 → **13:40:34, not 16:40:34**; non-UTC sources keep
`trip_tz − configured_tz` byte-for-byte; UTC-set threading + override-wins).
Original proposal follows.**

**Status: PROPOSED (Nelson 2026-06-23, evidence-based). GoPro mp4 capture
times come out of Reconcile **off by the camera's UTC offset** (3h, in the
Nepal trip). Root cause empirically confirmed on
`D:\Photos\trips recovered\2025 - Nepal\GoPro\2025-10-27` (HERO12 Black):
the camera writes its QuickTime container timestamps
(`CreateDate`/`MediaCreateDate`/`TrackCreateDate`, the `mvhd` atom) in
**UTC** and writes **no** local-offset tag, but Mira reads every video
timestamp as **naive local wall-clock** and then applies the full
`trip_tz − configured_tz` TZ correction on top — double-counting the
camera's offset. Fix: recognise QuickTime-UTC video timestamps and convert
to trip-local via the **trip offset alone** (the camera's configured TZ is
irrelevant for a true-UTC instant). Touches `core/exif_reader.py`
(flag the timestamp's UTC provenance) and the Reconcile calibration
(`core/reconcile_pipeline.py` + `core/clock_calibration.py`: a per-source
"timestamps are UTC" path). No schema change required for the core fix.**

## 1. Evidence (measured, all files in the dir)

Filename carries the Brazil wall-clock the camera was set to (UTC−3); the
`mvhd` `CreateDate` Mira reads is consistently **+3h** = UTC:

| Filename (Brazil local) | `CreateDate` mvhd (what Mira reads) | Δ |
|---|---|---|
| 04:56:40 | 07:55:34 | +3h |
| 05:00:18 | 08:00:16 | +3h |
| 20:37:00 | 23:36:09 | +3h |
| 22:27:56 | 01:27:38 (next day) | +3h |
| 23:10:46 | 02:09:42 (next day) | +3h |

No `CreationDate` / `DateTimeOriginal` / offset tag is present (a full
head+tail scan found none) — only the UTC `mvhd` fields. Make/Model =
`GoPro` / `HERO12 Black`.

## 2. Root cause

- `exif_reader._parse_timestamp` truncates to 19 chars and returns a
  **naive datetime treated as local wall-clock**, regardless of which field
  it came from. The video fallback chain is
  `DateTimeOriginal → CreationDate → CreateDate → MediaCreateDate →
  TrackCreateDate`. For this GoPro the first two are absent, so Mira reads
  `CreateDate` = the **UTC** mvhd value and mislabels it "local."
- Reconcile treats the GoPro as TZ-only and applies
  `offset = trip_tz − configured_tz = (+5:45) − (−3:00) = +8:45`.
- Net for clip 1: `07:55:34` (already UTC) `+ 8:45` → **16:40**. Correct
  Nepal time = `UTC 07:55:34 + 5:45` = **13:40**. Every clip lands **3h
  late** (= the camera's −3 offset, applied twice), which also spills some
  across the wrong day boundary.

The QuickTime spec defines `mvhd` creation/modification time as seconds
since 1904 **UTC**. So for a true-UTC instant the camera's *displayed*
(configured) TZ is irrelevant: trip-local = `UTC + trip_offset`.

## 3. The fix

### 3.1 Reader — mark the timestamp's UTC provenance

`exif_reader`: when the chosen capture timestamp comes from a **QuickTime
container field** (`CreateDate` / `MediaCreateDate` / `TrackCreateDate`)
**and** there is no local-offset signal (no `CreationDate` with a TZ
trailer, no `DateTimeOriginal` + `OffsetTimeOriginal`), flag the parsed
time as **UTC** (e.g. `PhotoExif.capture_time_is_utc = True`). Local-bearing
sources (phones, stills with `OffsetTimeOriginal`, GoPro models that *do*
write `CreationDate`±offset) stay local — unchanged behaviour. Keep
treating the value as naive in storage; the flag only records "this naive
value is UTC, not configured-local."

### 3.2 Reconcile — convert UTC sources by trip offset alone

When a source's timestamps are UTC (auto-detected per §3.1, surfaced as a
per-source `timestamps_are_utc` on the Reconcile `CameraInput`, defaulting
to the detection but **user-overridable** in the calibration dialog):

- The calibration offset for that source becomes `trip_tz − 0` (treat
  `configured_tz` as UTC), i.e. **`+trip_offset`** only. For Nepal: `+5:45`.
- Pairs, if any, still work — but a true-UTC source rarely needs a clock
  pair; the trip TZ alone is exact.

Result for clip 1: `07:55:34 + 5:45` = `13:40:34` Nepal — correct. Clips
genuinely cross to the next Nepal day where the real instant does (Brazil
evening → Nepal morning), but at the right hour.

### 3.3 Detection + override (safety, per the spec/101 lesson)

- **Auto-detect** UTC-source by Make/Model = GoPro (and/or "container field
  won + no offset tag present"). Don't silently re-interpret *every*
  video's mvhd as UTC — some non-GoPro cameras write local into the UTC
  field out of spec.
- **User override:** the calibration dialog shows the detected
  "timestamps are UTC" state per source with a checkbox, so a user with a
  camera that violates the spec can flip it. This keeps the change
  surgical and reversible — no repeat of the spec/101 over-broad correction.

## 4. Acceptance

- The GoPro clips in the Nepal dir correct to **Nepal local** (clip 1 →
  13:40, not 16:40); the +3h overshoot is gone.
- Day routing places each clip on the Nepal day its true instant falls on.
- Stills cameras and phones (local `DateTimeOriginal` / `OffsetTimeOriginal`)
  are unchanged; a non-GoPro video with a proper local `CreationDate`+offset
  is unchanged.
- The detected UTC state is visible and overridable in the calibration
  dialog.

## 5. Tests

- `tests/test_exif_reader_video_utc.py` — a synthetic/parametrised PhotoExif
  from QuickTime-only fields flags `capture_time_is_utc=True`; a source with
  `OffsetTimeOriginal` or a `CreationDate` offset trailer flags False;
  Make=GoPro + bare mvhd → True.
- `tests/test_clock_calibration_utc_source.py` — a UTC-flagged source
  computes `offset = trip_tz` (not `trip_tz − configured_tz`); the Nepal
  fixture (`UTC 07:55:34`, trip `+5:45`, configured `−3:00`) →
  `13:40:34`, NOT `16:40:34`; a non-UTC source is unchanged.
- Regress the existing reconcile / `_parse_timestamp` tests.

## 6. Implementation plan (commit order)

1. **Reader flag** — `exif_reader` records `capture_time_is_utc` from the
   winning field + offset-tag absence; pure, unit-tested.
2. **Calibration honours it** — `clock_calibration` / `reconcile_pipeline`
   use `trip_tz` alone for UTC sources; `CameraInput.timestamps_are_utc`
   (auto from detection).
3. **Dialog surface** — show + allow override of the per-source UTC state.
