# spec/10 — Create event from photos (ingest)

**Build-sequence step 7, the first real data surface (charter §5.6 — assembly starts at
ingest).** This is also the **production path for the rebuild-fresh plan**: legacy events
are re-created here, not migrated (PROGRESS). The user points Mira at a folder of
photos/videos; Mira scans it, calibrates each camera's clock + timezone, copies the
originals into the event tree, and writes one authoritative `event.db` through the gateway.

Scope (Nelson 2026-05-30): **full fidelity now** — multi-camera scan, per-camera clock
calibration (declared offset **and** the sync-pair-picker with multi-pair interpolation),
per-day timezone, copy-verbatim, no-timestamp quarantine + filename recovery,
out-of-day-range binning, and source→dest hash integrity verification. The concrete target
is importing **Nepal** correctly (multi-camera, UTC+5:45).

---

## 1. The one new-model change vs. legacy: virtual EXIF (no bake)

The legacy flow (`core/reconcile_pipeline.py`) **bakes** the corrected `DateTimeOriginal`
into every file in `00 - Captured` (via `core/exif_rewriter.py`). The new model
(charter §3, locked) does **not**:

- Originals are copied **byte-for-byte** into `00 - Captured` and never modified.
- Each item record stores `capture_time_raw` (the camera's recorded time, never mutated)
  **and** `capture_time_corrected` (raw + the per-camera calibration offset), plus
  `tz_offset_minutes` and `tz_source`.
- The SD-wipe gate gets **simpler** (charter §3): the destination is byte-equal to the
  source through the whole flow, so the verify hash never has to be re-computed after a
  mutation. (Wipe gate itself is a later surface; ingest just guarantees the byte-equal
  copy + verifies it.)
- Courtesy filenames that reflect corrected time live only in the **projected** tree
  (`01 - Culled` … rendered later); `00 - Captured` keeps the original filenames.

Same calibration **math** as legacy — only the *destination of the correction* moves from
the file's EXIF into the record.

## 2. Reuse map — pure logic reused verbatim from `core/`, commit rebuilt

**Reused verbatim (Qt-free, no data tendril — imported from legacy `core/` for now;
ported into `mira/` at the §4-step-8 archive):**
- `core/clock_calibration.py` — `CalibrationPair`, `CameraCalibration`, `build_calibration`
  (snap-to-15-min covers +5:45; 1-pair constant vs 2+-pair linear interpolation; 3+-pair
  median outlier rejection; `trip_tz − configured_tz` math; pair↔TZ cross-check warnings).
- `core/fresh_source.py` — `read_source_items` (single batched EXIF read → `SourceItem`),
  `camera_id_for` (Model-alone identity), `cameras_in`.
- `core/day_assignment.py` — `build_day_index`, `assign_one`, `corrected_timestamp`,
  `UNDATED_LABEL` (corrected date → smallest-matching `Dia N`).
- `core/filename_timestamp.py` — `parse_timestamp_from_filename` (Android/WhatsApp/Drive
  recovery; filename times are wall-clock trip-local ⇒ calibration is **skipped** for them).
- `core/path_builder.py` — `day_folder_name` (`Dia N - YYYY-MM-DD - desc`) +
  `CAPTURED_*` constants; `core/exif_reader.py`; `core/video_discovery.VIDEO_EXTENSIONS`.

**Rebuilt against the gateway:** the *commit* (legacy `reconcile_commit` — copy/bake/
`save_event`). The new commit copies verbatim (no bake), builds new-model records, and
materialises via `Gateway.create_event`.

## 3. `Gateway.create_event(doc, event_root)` (BUILT)

The fresh-event composition enumerated in spec/08 §3.2. Builds the index entry via the
single `make_entry` re-anchoring rule (charter §5.9), materialises the `event.db` through
the **same** `materialise_event` path (one db-creation path for restore / migration /
create), and returns the open `EventGateway`. `EventDocument` → `event.json` via
`json_dump.to_json`, so create shares the one reader/writer.

## 4. The ingest engine — `mira/ingest/`

`mira/ingest/model.py` — the engine's own typed inputs (decoupled from legacy
`ReconcileConfig`):
- `DayPlan(day_number, date, description, location, tz_offset_hours)` — one planned `Dia`,
  with the trip-local UTC offset the user set in the plan editor (the wizard seeds each
  day from the `home_timezone` setting; a changed day then cascades forward, spec/05 §4b).
- `CameraPlan(camera_id, is_phone, is_reference, configured_tz_hours, calibration)` — one
  detected camera + its calibration answer. `calibration` is a pre-built
  `CameraCalibration` (from the pair-picker and/or a declared offset); if `None` the engine
  builds a TZ-only calibration from `configured_tz_hours` + the trip TZ. Phones pass
  through uncorrected (NTP-synced wall-clock).
- `IngestPlan(event_id, event_name, event_root, source_root, days, cameras, start_date,
  end_date)` — the whole job.
- `IngestResult(event_id, db_path, photos, videos, quarantined, filename_recovered,
  out_of_day_range, integrity_failures, warnings)` — what happened, for the UI summary.

`mira/ingest/engine.py` — `run_ingest(plan, gateway) -> IngestResult`:
1. **Scan** `source_root` once → `SourceItem`s (path, raw timestamp, `camera_id`).
2. **Calibration map** per `camera_id`: the `CameraPlan.calibration`, else a TZ-only build
   from `configured_tz_hours` + the dominant per-day `tz_offset_hours`. Phones → `None`.
3. **Per item**:
   - `raw_t = item.timestamp`; if `None`, try `parse_timestamp_from_filename` →
     `recovered_from_filename = True` (then calibration is skipped — filename times are
     already trip-local).
   - if still `None` → **no-timestamp quarantine**: copy to
     `00 - Captured/_no_timestamp/<camera_id>/<mtime-prefix>__<name>`, record
     `quarantine_status='no_timestamp'`, `day_number=None`.
   - else compute `corrected` (= raw for phones / recovered / uncalibrated; else
     `raw + calibration.offset_at(raw)`), assign a `Dia` (corrected date → smallest match).
   - if no matching `Dia` → **out-of-day-range**: copy to
     `00 - Captured/<bucket>/_out_of_day_range/<camera_id>/<name>`, `day_number=None`.
   - else copy to `00 - Captured/<bucket>/<Dia N - date - desc>/<camera_id>/<name>`,
     `bucket = _phones if is_phone else _cameras`.
   - **copy verbatim** (`shutil.copy2`) then **integrity-verify** (sha256 + size,
     source == dest); a mismatch is recorded in `integrity_failures` (never silent).
   - build the `Item`: `kind` (video ext → `'video'`), `origin_relpath` (dest relative to
     `event_root`, posix), `sha256`, `byte_size`, `capture_time_raw`/`_corrected` (ISO),
     `tz_offset_minutes` (= corrected − raw), `tz_source` (`pair`/`tz`/`none`),
     `day_number`, `quarantine_status`, `recovered_from_filename`, `provenance='captured'`.
4. **Build the `EventDocument`** — `Event`, new-model `TripDay` (`tz_minutes =
   round(tz_offset_hours*60)`), `Camera` (`configured_tz_minutes`, `applied_offset_minutes`,
   `applied_at`, `calibration_json`), and the `Item`s — then `gateway.create_event`.

The bridge to the reused `core/` engines (which type their day argument as the **legacy**
`TripDay`, accessed only for `.day_number` / `.date` / `.description`) is a tiny duck-typed
`_LegacyDayLike`; the engine never depends on the legacy `TripDay`'s full field set.

## 5. Gate (this session — engine only; UI + shell + events list next)

`tests/test_ingest.py`, logic-level (synthetic `SourceItem`s + real tiny files in a tmp
tree, no exiftool dependency in the unit path):
- corrected times: a camera declared on UTC−3 importing a UTC+5:45 trip lands at +8:45;
  a phone passes through uncorrected; a 2-pair camera interpolates.
- day routing by corrected date; out-of-day-range bin for a date outside the plan.
- no-timestamp quarantine (+ mtime-prefixed name) and filename recovery (skips calibration).
- copy is byte-verbatim and integrity-verifies; a corrupted dest is reported.
- the materialised event round-trips through the gateway (`list_events`, `items`,
  `day_tree`, `phase_progress`) and item `capture_time_corrected` matches the engine's math.

## 6. The UI — the **reused** legacy flow (BUILT; charter §5.2)

> **Course-correction (Nelson 2026-05-30, emphatic — [[feedback_reuse_legacy_ui_dont_recreate]]):**
> a first pass built a *fresh* create-event page. That was wrong: *"we are NOT recreating
> the UI… ONLY the changes required to integrate it onto the new database."* The fresh page
> was deleted; the **entire legacy flow is reused** and only its data seam rewired.

"Create Event from Photos" opens the **reused legacy `PastPhotosDialog`** verbatim — its
full step-by-step flow unchanged: Source + name → EXIF scan (`scan_source_tree` /
`reconcile_scan`, read-only) → the reused **`PlanEditorDialog`** (Import file / Paste / Save,
the named-location `TzPicker` with first-day propagation, resizable columns, focus-drift
fix) → the reused per-TZ **`PastPhotosCamerasDialog`** calibration loop (each camera: "know
the timezone?" → `TzPicker`, or "pick a sync pair" → the reused `SyncPairPickerDialog`) →
the reused **`OrphanDatesDialog`** check.

Ported into `mira/ui/` (copy + swap `ui.*`→`mira.ui.*` imports). **The only
changes from legacy** (the data seam, charter §5.2):
- the **commit**: legacy `reconcile_commit` (copy + EXIF-bake + `save_event`) → the new
  `_commit` converts the gathered plan + calibration via **`plan_from_reconcile`** into an
  engine `IngestPlan` and runs **`run_ingest`** (copy verbatim, virtual-EXIF records,
  materialise via the gateway — no bake);
- `photos_base_path` reads → `Gateway.photos_base_path()`;
- `name_collision` matches → `Gateway.list_events()` (the helper is now pure-UI);
- the `data.event_store.save_event` + legacy-settings calls are gone.

`plan_from_reconcile` (`mira/ingest/plan.py`, tested) is the conversion: legacy
`TripDay` → `DayPlan`; per-TZ `CameraInput` (duck-typed) → `CameraPlan` (pairs →
`CameraCalibration`, else declared `configured_tz`; phones pass through). Single-TZ trips
are exact; cross-TZ uses the constant-offset simplification (as legacy).

**What this drives next:** the **events-list** already shows imported events; next is the
**per-event surface** (open a card → event dashboard → Cull), each likewise reusing the
legacy widgets and rewiring only their data.
