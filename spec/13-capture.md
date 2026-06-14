# spec/13 — Capture (ingest into an existing event)

**Build-order #5.** The during-trip / after-the-fact path that brings a new card's photos
**into an event that already exists** (Create-from-Photos, spec/10, made the event; Capture
keeps feeding it). Two legacy ways, both preserved:

- **Mode A — copy the entire source** (full SD-card offload): copy everything verbatim into
  `00 - Captured`, verify, then optionally wipe the card.
- **Mode B — cull before copying** (the Fast Culler): a fast, default-**Keep**, binary K/D
  triage over the scanned source; only the kept files are copied. Discards never enter the
  event.

The legacy flow (`ui/main_window._on_capture_phase`) is: pick source → EXIF scan →
**F-019 plan-confirm** (per-day plan/TZ reconcile + per-camera offset) → **mode chooser**
(`CaptureActionDialog`) → [Fast Culler if Mode B] → **`BackUpCardDialog`** (offload + verify
+ **bake** + wipe). The rebuild keeps the shape and rewires the commit to the new engine; the
legacy EXIF **bake is dropped** (charter §3 virtual EXIF), which also makes the wipe simpler.

---

## 1. Engine — `run_ingest` append + Mode-B filter (BUILT)

One ingest engine serves both Create and Capture (Nelson — extend `run_ingest`, don't port
the legacy offload engine). `mira/ingest/engine.py`:

- **`append_to_event_id`** — when set, the per-item scan/copy/verify/record is identical to
  create, but the commit **adds** the new items (+ newly-seen cameras) to the existing
  `event.db` via the gateway (`add_items` + `add_cameras`) instead of materialising a fresh
  one. The existing plan / cameras / decisions are left intact. `plan.event_root` /
  `plan.event_id` are the existing event's. The Capture UI owns plan edits (via the
  plan-confirm dialog + `Gateway.save_trip_days`) *before* this runs — append never rewrites
  trip days.
- **`include_paths`** — the Mode-B kept set. When given, only `SourceItem`s whose `path` is
  in the set are ingested; discards are skipped. Copy + record are otherwise identical.
- **`EventGateway.add_cameras`** — inserts only newly-seen cameras (satisfies the
  `item.camera_id` FK on append) **without clobbering** an existing camera's calibration.

`tests/test_ingest_append.py` (3): append adds items + new camera while preserving the
existing camera's calibration + correcting the new frames; append doesn't create a 2nd
event; the Mode-B filter ingests only kept paths (discards never copied).

## 2. The SD-card wipe gate (BUILT) — CLAUDE.md invariant #9

`mira/ingest/wipe.py` — the only sanctioned deletion of user originals.

- `core/removable_drive.is_removable` reused verbatim (pure logic): a removable Windows
  volume only; internal SSD / network share never get the offer (off-Windows → False).
- **`wipe_eligible(result, source_root)`** — True iff removable **and** no integrity
  failures **and** at least one verified copy.
- **`wipe_sources(paths)`** — deletes the given files + audit-logs; per-file errors
  collected, never raised.
- **What is deleted:** exactly `IngestResult.copied_sources` — the source paths that
  verified byte-equal — **never a blind `rglob`** of the card. So a Mode-B discard, never
  copied, is **never** deleted: a file with no verified backup is out of the gate's reach
  (invariant #9, *"no verified backup ⇒ delete never offered"*). This is **stricter than the
  legacy whole-card wipe**, on purpose — the safe reading of the invariant. The engine
  records each verified source in `result.copied_sources`.

Virtual EXIF (no mid-flow bake) keeps the verify hash valid right up to deletion — the card
stays byte-equal to `00 - Captured` until the user confirms. `tests/test_wipe_gate.py` (6).

## 3. The UI flow — BUILT (faithful verbatim port, Option 1; 2026-05-31)

> A first attempt substituted the Create-Event wizard and was **reverted** (charter §0 /
> [[feedback_reuse_legacy_ui_dont_recreate]]). This is the faithful port: the legacy
> `MainWindow._on_capture_phase` chain, dialog-for-dialog, with **only the data calls** rewired.

Housed in **`mira/ui/pages/capture_flow.py`** (`run_capture(parent, gateway, event_id)`),
wired to the Capture tile (`MainWindow._open_capture`). The chain, in the legacy order:

1. `_pick_capture_source` — folder picker (start dir = `gateway.photos_base_path()`).
2. `_scan_capture_source` — `read_source_items` behind the legacy "Reading photo metadata…"
   busy dialog.
3. **`PreingestPlanConfirmDialog`** (ported `preingest_dialog.py`) — *"Confirm trip plan and
   timezone for N day(s)"*, verbatim. **Data seam:** `save_event` → `gateway.save_trip_days`;
   remembered camera-TZ (`saved_camera_tz`) read/write → gateway `SettingsRepo` (new app-tier
   field); the `camera_clocks` suppression write is **dropped** (no consumer in the rebuild —
   the `Camera` row carries the offset). Fed a **legacy-`Event` adapter** built from the
   gateway (`run_capture` constructs it: id + name + start_date + legacy `TripDay`s).
4. **`CaptureActionDialog`** (ported verbatim) — copy-all vs cull-first.
5. Mode B → **`fast_culler_page`** (ported verbatim) in a modal host → kept basenames.
6. **`BackUpCardDialog`** (ported `back_up_card_dialog.py`) — UI + offload + verify + the
   rglob two-confirm wipe all **verbatim**. **Data seam (Option 1):** keep
   `offload_to_captured` + `verify_offload`; **drop** `bake_offload_manifest`; after a passed
   verify, **`record_offload`** (`mira/ingest/offload_record.py`) projects the offload
   manifest into `Item` rows (`capture_time_raw` + `_corrected = raw + offset`, no bake) +
   a `Camera` row via `gateway.add_items`/`add_cameras`; `photos_base`/`event_root` from the
   gateway. The `saved_camera_offsets` remember (sidebar path only) → gateway settings.

**`record_offload`** is the single piece of genuinely new code — the sanctioned gateway/DB
layer that replaces the legacy EXIF bake (Nelson OK 2026-05-31, *"database only now"*). Day
routing is unchanged (offload routes by raw time, `calibration=None`); only the *record*
carries the virtual correction. `tests/test_capture_offload.py` (3): manifest→items
projection (typed camera_id, corrected times, day), quarantine (no EXIF), zero-offset.

**One sanctioned data difference (charter §3):** the legacy baked the offset into the copied
files' EXIF; the rebuild leaves originals byte-pristine and stores the correction in the
record. Screens identical; the bake step is gone.

## 3a. Browse a day's photos from the plan editor (Nelson 2026-05-31)

The legacy plan editor let you **right-click a row** to browse that day's photos
("Browse photos for this day…"). Reassembled as a **per-row "Browse…" button** (Nelson's
preference) on a trailing `Browse` column in `PlanEditorDialog`:

- The column is shown only when a `day_photos_provider` is wired (an existing event); New
  Event hides it (`setColumnHidden`). Description keeps the table stretch; the Browse column
  is fixed-width. The legacy right-click browse item is removed.
- **Display = the ported Fast Culler in `browse_mode`** (Nelson's idea — reuse, don't build a
  new viewer; avoids porting the 2120-line `MediaCanvas`). `browse_mode` hides the K/D pill,
  Save, and the bulk buttons and ignores the K/D keys; navigation + fullscreen + video
  transport stay. Hosted in a modal; Back/Esc closes it.
- **Data seam:** `MainWindow._make_day_photos_provider(event_id)` returns
  `provider(row_date) -> list[SourceItem]` — the event's items whose `day_number` matches
  that date (via the gateway), resolved to absolute paths under `event_root`. The legacy
  `day_files` filesystem walk + `DayBrowseDialog` are *not* ported.

`tests/test_plan_browse_day.py` (6): column hidden without provider / shown with it; the
no-photos message; browser opens when photos exist; Fast Culler browse mode hides K/D; the
provider returns the right day's items.

## 3b. The UI flow — earlier sketch (pre-attempt notes, superseded by §3)

Reassemble from the per-event dashboard **Capture tile** (`_on_phase_activated('capture')`):

1. **Pick source** (folder picker; start dir = `photos_base_path`).
2. **Scan** — `read_source_items` (off-thread via `run_with_progress`, spec/05 >1s).
3. **Plan-confirm + calibration** — reconcile the source against the event's existing plan;
   set/extend per-day TZ and gather per-camera offsets. **Reuse** the ported calibration
   machinery already used by `PastPhotosDialog` (the per-TZ `CameraCalibrationDialog` loop +
   `SyncPairPicker` + `TzPicker` + `OrphanDatesDialog`), seeded from the event's `trip_days`
   (not a fresh skeleton). Persist plan edits via `Gateway.save_trip_days`.
4. **Mode chooser** — port `CaptureActionDialog` (Copy-all / Cull-first / Cancel).
5. **Mode B** — port `ui/culler/fast_culler_page.py` (pure UI: `list[SourceItem]` in →
   kept `set[Path]` out; its only seams are the scan input + the `saved` signal + image
   decode via `mira/ui/media/image_loader`). The kept set → `include_paths`.
6. **Commit** — build an append `IngestPlan` (via `plan_from_reconcile`, event_root/event_id
   = the existing event) → `run_ingest(..., append_to_event_id=<id>, include_paths=<kept>)`
   under a progress dialog.
7. **Wipe gate** — if `wipe_eligible`: **double confirmation** (port the two-step gate from
   `back_up_card_dialog._maybe_offer_wipe`) → `wipe_sources(result.copied_sources)` →
   summary.
8. Refresh the per-event dashboard (Capture tile counts) + events list.

**Reuse strategy:** the calibration/plan/commit machinery is the ported `PastPhotosDialog`
flow driven in *append* mode (event pre-seeded, no name/collision step, commit appends). The
genuinely new UI parts are the **mode chooser**, the **Fast Culler** port, and the **wipe
double-confirm**.

## 4. Open question (deferred to the UI slice)

The legacy F-019 produces a single dominant-camera offset; the rebuild's `run_ingest` wants
per-camera `CameraPlan`s. The append flow should use the multi-camera calibration loop
(as Create does) rather than the single-offset shortcut, so multi-camera cards calibrate
correctly. Confirm at build time whether to reuse `PastPhotosDialog` in an append mode or to
build a dedicated `CapturePage` that shares its step helpers.
