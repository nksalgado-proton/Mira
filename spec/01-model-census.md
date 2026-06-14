# spec/01 — Model census (the field catalog)

**Build-sequence step 1.** The new model is the *union of every read and every
write* across the legacy app (charter §5.1). This doc is that union, consolidated
from a five-region census of the legacy surfaces (ingest/TZ/wipe · cullers/Select ·
Process · Curate/Distribute · event/plan/backup). It is the **authoritative field
catalog**; the SQLite + JSON schema (step 2) is derived mechanically from it.

Each entity lists its fields with: **P** = must be persisted (owned record),
**T** = transient (stays in-memory, listed at the end of each section for context),
**FS→own** = today derived from the filesystem/folder-names and **must become an
owned column** (eliminating the dir-as-truth bug), **¬rec** = today *not*
recoverable if the journal is lost (the new model fixes this by owning it).

Conventions for the new model (locked by charter §3): every path is **relative to
event root**; capture time is **virtual** (raw never mutated); the directory tree is
**output only**.

---

## A. Item — the spine (one row per captured unit)

Everything hangs off `item.id`. A photo *is* a photo; a snapshot *is* a photo; a clip
*is* a video. Provenance is bookkeeping, not a user-visible kind (docs/24).

| field | type | P/FS/¬rec | meaning | legacy source |
|---|---|---|---|---|
| id | uuid | P | stable identity, survives rename/relocate/export | (new — legacy keys on filename) |
| kind | enum photo\|video | P | the two-kind model | bucket_navigator_model.py:73 |
| origin_relpath | relpath | P | location under `00 - Captured`, relative | event_backup_card.py:175 (abs today) |
| sha256 | hex | P | integrity; wipe-gate anchor | event_backup_card.py:176,596 |
| byte_size | int | P | integrity; wipe-gate | event_backup_card.py:177 |
| camera_id | str | P | owning camera/device | event_backup_card.py:146 |
| capture_time_raw | datetime | P | **virtual EXIF**: camera's recorded time, never mutated | exif_reader.py:197; UserComment marker exif_rewriter.py:50 |
| capture_time_corrected | datetime | P | raw + applied offset; what the app sorts by | capture_bake.py:161 (baked into bytes today) |
| tz_offset_minutes | int | P | applied correction | clock_calibration.py:67; adjust_event_tz.py:74 |
| tz_source | enum pair\|tz\|manual\|none | P | provenance of the offset | clock_calibration.py:107,224 |
| classification | str\|null | P, FS→own | scenario/genre as DATA (null = unclassified) | **today parsed from folder names** process_discovery.py:136-256 |
| classification_source | enum auto\|user\|null | P | user override vs auto-classify | genre.py:32 (override) / :37 (auto cache) |
| classification_rules_version | str | P | invalidates stale auto-classify | genre.py:37 (`v`) |
| sharpness_score | float\|null | P, ¬rec | cached Laplacian-variance; slow to recompute | sharpness.py:44,60 |
| sharpness_metric | str\|null | P | metric version (e.g. lapvar_wf_v1) | sharpness.py:50 |
| provenance | enum captured\|snapshot\|clip | P | how this item came to exist | video_marks (synthesised today) |
| parent_item_id | uuid\|null | P | for snapshot/clip → source video (N→1 lineage) | cull_phase_sync.py:65-90 |
| day_number | int | P, FS→own | event-timeline day (routing) | **today encoded in folder name** event_backup_card.py:178 |
| quarantine_status | enum ok\|no_timestamp\|recovered | P | files with no EXIF time | event_backup_card.py:504; quarantine_recovery.py |
| recovered_from_filename | bool | P | timestamp parsed from filename, not EXIF | reconcile_pipeline.py:886 |

**T (ingest-time, not persisted):** day-routing decision, corrected-time delta math,
BakeResult/VerifyResult/OffloadResult diagnostics (clock_calibration.py:124;
capture_bake.py:48; event_backup_card.py:214,228).

---

## B. PhaseState — per item × phase (replaces all per-phase journals)

One model across Cull / Select / Process / Curate. The fragmentation (separate
journal systems per phase) collapses here.

| field | type | P/FS/¬rec | meaning | legacy source |
|---|---|---|---|---|
| item_id | uuid | P | FK to item | — |
| phase | enum cull\|select\|process\|curate | P | which phase | — |
| state | enum discarded\|candidate\|kept | P, ¬rec(candidate) | the 3-state mark | cull_state.py:109-115 |
| decided_at | datetime\|null | P | when the mark was set | — |
| derived_dirty | bool | P | **new**: upstream change invalidated this (fixes re-entry S1/S2) | (absent today — the re-entry bug) |
| committed_at | datetime\|null | P | phase-exit commit stamp | cull_session.py:247 |

Note: video keep-state is **synthesised** today (kept iff any clip/snapshot kept) and
never stored (FS→own). In the new model it is a real `phase_state` row plus the
`video_moment` rows below — no synthesis.

---

## C. Bucket — per-bucket soft state (grouping + resume)

Buckets are a *grouping* (Day → Bucket → item). Most are stable; transient "Moment"
clusters are keyed by content. Soft state is owned, not inferred.

| field | type | P/FS/¬rec | meaning | legacy source |
|---|---|---|---|---|
| bucket_key | str | P | identity: camera/day/bucket, or content-hash for clusters | cull_state.py:219-230 (SHA1 of filenames) |
| default_state | enum discarded\|kept | P | unmarked-item fallback (brackets default kept) | cull_state.py:78-86 |
| reviewed | bool | P, ¬rec | user declared bucket done (reversible, never inferred) | cull_state.py:269 |
| browsed | bool | P, ¬rec | opened, no marks | cull_state.py:284 |
| current_index | int | P, ¬rec | resume cursor | cull_session.py:145 |

**Design note (→ schema D5):** content-hash bucket identity is a legacy
micro-optimisation; decide whether the new model keys soft-state on a stable
bucket id or retains content-hashing for re-cluster stability.

---

## D. VideoMoment — clips & snapshots (first-class rows, never synthesised)

The single biggest ¬rec hazard today: clip ranges/labels/snapshot times live *only*
in the journal. The new model owns them.

| field | type | P/¬rec | meaning | legacy source |
|---|---|---|---|---|
| id | str (c1,s1…) | P, ¬rec | stable lineage id, survives re-trim; join key downstream | video_marks.py:96,122 |
| source_item_id | uuid | P | the source video item | video_marks.py:97 |
| kind | enum clip\|snapshot | P | — | video_marks.py:136,150 |
| in_ms / out_ms | int | P, ¬rec | clip range | video_marks.py:114-115 |
| at_ms | int | P, ¬rec | snapshot position | video_marks.py:123 |
| state | enum kept\|discarded | P | per-moment K/D (Cull + Select) | video_marks.py:109,124 |
| label | str | P, ¬rec | user free-text ("yak crossing") | video_marks.py:101 |
| created_at | datetime | P | — | video_marks.py:102 |
| source_duration_ms | int | P | for time-weighted kept tally (F-034) | video_marks.py:103 |
| produced_item_id | uuid\|null | P | the materialised snapshot-photo / exported clip | cull extract → JPEG (docs/24 Step 1) |

Note: snapshot JPEGs get a baked-EXIF DateTimeOriginal today so they sort among
photos. Under virtual EXIF the produced snapshot item carries
`capture_time_corrected = source.corrected + at_ms` as data (no bake).

---

## E. Adjustment — per-item Process edits (photo)

| field | type | P | meaning | legacy source |
|---|---|---|---|---|
| item_id | uuid | P | FK | — |
| params | {exposure,contrast,highlights,shadows,whites,blacks,vibrance,sharpness,saturation,clarity} floats | P | tone (LRC vocabulary) | process_decisions.py:141; photo_render.py:52 |
| crop_norm | [x,y,w,h]∈[0,1] | P | crop rect (post-rotation) | process_decisions.py:155 |
| crop_angle | float deg | P | tilt (applied before crop) | process_decisions.py:201 |
| rotation | int {0,90,180,270} | P | 90° steps (applied first) | process_decisions.py:232 |
| aspect_label | str | P | per-bucket aspect | process_decisions.py:85 |
| auto_on | bool | P | apply AUTO vs manual | photo_render.py:52 |
| strength | float 0–2 | P | AUTO intensity | photo_render.py:93 |
| process_exported | bool | P | materialised by an Export | process_decisions.py:262 |

**T:** in-memory clipboard `_CLIPBOARD` (tone params; copy/paste adjustments),
render caches, crop-overlay geometry, undo-paste single level (adjustment_surface.py).

---

## F. VideoOverride — per-clip Process refinements

| field | type | P | meaning | legacy source |
|---|---|---|---|---|
| moment_id | str | P | FK to video_moment (clip) | video_overrides.py:55 |
| params / crop_norm / box_angle / aspect_ratio_label | as Adjustment | P | colour + geometry | video_overrides.py:78-92 |
| auto_on / style / rep_frame_ms | bool/str/int | P | colour computed from rep frame | video_overrides.py:82-85 |
| include_audio / rotation_degrees | bool/int | P | mute, rotate | video_overrides.py:76-77 |
| trim_start_delta_ms / trim_end_delta_ms | int | P | shave inward | video_overrides.py:87-88 |
| audio_volume / audio_fade_ms / speed / stabilise | float/int/float/float | P | audio + motion | video_overrides.py:89-92 |

---

## G. StackBracket — focus/exposure brackets

| field | type | P/FS→own | meaning | legacy source |
|---|---|---|---|---|
| bracket_id | str | P, FS→own | identity (**today a folder-name hash**) | stack_discovery.py:68,161 |
| kind | enum focus\|exposure | P, FS→own | **today a folder-name prefix** | stack_discovery.py:67 |
| member_item_ids | [uuid] ordered | P, FS→own | frames in the bracket | stack_discovery.py:70 |
| action | enum stacked\|picked\|skipped | P | merge / pick-one / skip | stack_session.py:132 |
| picked_index | int | P | chosen frame (−1 = stack/skip) | stack_session.py:59 |
| output_relpath | relpath\|null | P | materialised result | stack_session.py:234 |

---

## H. CurateTag — per-item curation

| field | type | P | meaning | legacy source |
|---|---|---|---|---|
| item_id | uuid | P | FK | — |
| level | enum best\|short\|long\|composition\|collage_only\|null | P | tier (cascade Short ⊂ Long) | curate_tags.py:68 |
| theme | str\|null | P | user genre tag (portfolio) | curate_tags.py:120 |
| solo | bool | P | theme-only, skip cascade | curate_tags.py:121 |
| is_discarded | bool | P | explicit skip-all-output | curate_session.py:65 |
| tag_set_at | datetime | P | (today only the journal mtime) | curate_session.py |

**Subset** (id, name, base ∈ short\|long\|subset-uuid, genre_filter, target_s, max_s,
excluded[item_id]) — curate_session.py:155-161. Membership = (base ∩ genre_filter) −
excluded, resolved on demand (curate_session.py:585).
**TripBudget** (short_target_s, short_max_s, long_target_s, long_max_s, video_share)
— curate_session.py:100-104; per-tier slideshow seconds/max from settings.
**CurateMap** (kind=MAP slides from `_curate_maps.json`) — curate_discovery.py:219 —
minor, note for completeness.

---

## I. Lineage — export traceability (replaces stem-matching)

| field | type | P | meaning | legacy source |
|---|---|---|---|---|
| export_relpath | relpath | P | a materialised output file | curate_integrity.py:103 |
| item_id | uuid\|null | P | source item (null for N→1 bucket outputs) | (today stem-matched) |
| bucket_key | str\|null | P | for stacks/brackets (N→1) | stack_discovery.py |
| phase | enum process\|curate | P | which export produced it | process_export_engine.py |

Materialise-new-bytes (not hardlinks): Process JPEG/TIFF, video clip MP4, stack
output, Curate portfolio RAW/TIFF copies. Hardlinks: Cull/Select projection, Curate
tiers/subsets. (process_export_engine.py:139; curate_export.py:343-373.)

---

## J. Event + Camera (event-level)

**Event:** id, name, start_date, end_date, is_closed (the Open/Closed bit —
models.py:97), notes, created_at, updated_at, distribution metadata
(google_album_name/link, whatsapp_message), **event_root** (replaces the absolute
`photos_base_path`; the single resolved-at-load indirection). Legacy `status` enum is
**not read by the UI** (models.py:83) — drop from the model (decision D6).

**TripDay:** day_number, date, description, location, tz_offset (models.py:42-51).
**Participant**, **ChecklistItem** — carry forward as-is.

**Camera (unify the duplication):** legacy splits camera TZ data across `camera_clocks`
{correct, configured_tz} (camera_clocks.py:34) AND `camera_timezone_offsets`
{detected_offset_hours, expected_tz, applied_at, reversible, …} (adjust_event_tz.py:85)
AND `camera_day_tz_overrides` (adjust_event_tz.py:291). The new model has **one
camera-calibration record**: camera_id, is_reference, is_phone, configured_tz,
calibration_pairs[], applied_offset_minutes, per-day overrides, applied_at, reversible.

**DistributionAction:** timestamp, channel, item_count, share_url, notes
(models.py:70-74) → first-class rows.

**Phase progress / completion:** **FS→own / derived** — today a cache in
`event_settings.phase_progress` AND re-walked from folders (phase_progress.py:446).
In the new model it is a **query** over `phase_state`, never a stored cache.

---

## K. Cross-cutting lists (the three the rebuild must act on)

**FS→own (eliminate dir-as-truth — become owned columns):** classification/genre,
day_number, bracket_id + kind, video keep-state, lineage, snapshot "kind",
phase-progress/completion. *Folder names are read exactly once more, ever — inside
the migration extractor (charter §2).*

**¬rec today → owned by design (the new model's win):** candidate state, clip
ranges + labels, snapshot times, genre overrides, sharpness cache, reviewed/browsed,
current_index. All become first-class persisted columns; journal loss no longer
loses them.

**Absolute paths to make relative (charter §3):** Event.photos_base_path
(models.py:105), BackupManifest source_root/backup_root (event_backup_mirror.py:133-134),
OffloadManifest src/dest/source_dir/event_root (event_backup_card.py:174-175,449-450).
All become relpaths under the single `event_root` indirection.

**Genuinely transient (stay in-memory — do NOT persist):** peaking/zoom/pan, candidate
view filter, fullscreen, playhead, render caches, export-worker progress, undo stacks,
clipboard. **One judgement call (D7):** the classification-nudge "dismissed" flag and
video markers are transient today and annoying when lost on re-entry — decide whether
to promote to persisted.

---

## L. Schema-design implications (feeds step 2)

1. The spine (Item) + PhaseState + VideoMoment + Adjustment/VideoOverride +
   StackBracket + CurateTag/Subset + Lineage + Event/Camera/TripDay map cleanly to
   the docs/29 §7 table sketch — that sketch is **confirmed** by the census, with
   additions: sharpness, classification_source/rules_version, quarantine_status,
   Bucket soft-state, VideoOverride's full field set, StackBracket, CurateMap.
2. **Wipe-gate fields** (sha256, byte_size, origin_relpath, verify-pass) live on Item
   plus a thin per-offload verify record; the chain redesign (charter §3) reads them
   from the store, no bake.
3. The **JSON backup shape** is this catalog serialised; it doubles as migration
   intermediate and test fixture (charter §4 steps 2–3).

## M. Open decisions surfaced by the census (resolve at step 2)

- **D1** Event document home (thin pointer vs all-in-DB) — *lean: thin pointer.*
- **D2** Per-event DB — *confirmed by census (portable, backup = copy folder).*
- **D3** Snapshot/clip materialisation timing — Process owns byte materialisation; the
  `produced_item_id` link is set at materialise.
- **D4** `adjustment.params` columns vs JSON blob — *lean: blob until query-by-param needed.*
- **D5** Bucket identity: stable id vs legacy content-hash (re-cluster stability). **New.**
- **D6** Drop the legacy `EventStatus` enum (UI doesn't read it). **New — lean: drop.**
- **D7** Persist nudge-dismissed / video markers, or keep transient. **New.**
