# spec/60 — The batch export engine: one worker process, full hardware, zero foreground lag

**Status:** design LOCKED, Nelson 2026-06-11 (the batch engine design
session; proposal accepted with the hardware-fallback addendum).
**Implementation IN PROGRESS (Nelson's word, 2026-06-12)** — slice 1
landed; §11 carries the record. The consumers from
spec/59 §8 — `BatchExportQueue` (strictly one job at a time) and the
progress line below the menubar — are **locked and unchanged**; this
design replaces the worker *internals* only.

---

## 0. The mistake being corrected

Batch exports render inside the app process, strictly one file at a
time, at normal priority: a day-scope job leaves most cores idle while
still competing with the foreground for the ones it uses. Videos do
not participate in day/event batch at all (the spec/56 slice-4 walker
never landed). The engine inverts this: **all the hardware, none of
the lag** — and every machine, capable or not, completes the same job
correctly.

## 1. The shape — one worker process per job

- The app builds a **fully-resolved manifest** per job: every unit
  carries its source path, resolved recipe (params / filter / crop /
  rotation, or the clip's `ExportPlan`), output path, format, quality.
  All resolution happens in the app, where the gateway lives.
- The app spawns **one render-worker process** — **our own binary in
  worker mode** (identical from source and packaged) — ships the
  manifest, and receives streamed per-unit progress back over the
  process pipe.
- **The worker never touches `event.db`.** All gateway writes
  (exported marks, lineage) stay in the app on the UI thread, at the
  existing commit seam.
- Lifecycle: spawned at job start, dies at job end. No daemon, no
  pool of idle processes to manage.
- The worker runs the **exact preview pipeline** (`apply_params` /
  `apply_filter` / `extract_rotated_crop`) — colour parity with the
  preview stays true by construction, on every machine.

## 2. Zero foreground lag = OS priority, not throttle logic

- The worker process (and every ffmpeg child it spawns) runs at
  **below-normal priority class**. The Windows scheduler IS the
  yield-to-foreground mechanism: the app takes every cycle it asks
  for; the batch soaks up the rest.
- Capacity rules on top: worker pool sized **cores−2 (floor 1)**;
  concurrent photo decodes capped by a **memory budget** (24 MP
  float intermediates are ~300 MB each).
- **No intensity knob, no pause/resume heuristics** — coherent
  choices over sliders applies to the engine too.

## 3. Parallelism — two concurrent lanes

- **Photo lane:** photos + snapshot stills render **N-wide across
  cores** (today: 1-wide). Embarrassingly parallel; numpy/PIL release
  the GIL, so one worker process with an internal pool scales
  near-linearly — no process pool needed.
- **Clip lane:** **one clip at a time, frame-parallel inside** (the
  existing decode-subprocess → numpy frame pool → encode-subprocess
  pipe already spreads one clip across all cores). Clip-width is a
  **single constant = 1**; if future hardware justifies 2-wide, it is
  a one-number change, not a redesign.
- **The lanes run side by side** under the shared memory budget
  (Nelson: best performance). Rationale: fast-path clips (trim/mute
  only) are pure ffmpeg with NVENC on the GPU — the cores sit nearly
  idle; the photo lane fills them. When one lane empties, the other
  takes the full width. Modest oversubscription at below-normal
  priority costs a few percent; idle cores during clips cost far more.
- **The colour math stays CPU, deliberately** — parity with the
  preview is by-construction only if it is the same code.

## 4. Hardware adaptability — the fallback ladder (Nelson's addendum)

The desktop PC is the ceiling, not the assumption. Every capability
is **probed at runtime**, never presumed:

- **Encoder ladder:** NVENC → Intel Quick Sync (QSV) → AMD AMF →
  **libx264 (CPU, always works)**. Probed the way NVENC already is —
  a real test encode, cached per session, one calm INFO log of what
  the machine chose.
- **Decoder:** hardware decode probe (NVDEC / D3D11VA) → software
  decode fallback.
- **Sizing derives from the machine:** pool width from actual cores
  (cores−2, floor 1 — a dual-core laptop runs width-1); memory budget
  from actual available RAM (floor: one unit in flight).
- **Last-resort fallback:** if the worker process cannot spawn at all
  (exotic AV, hostile environment), the job falls back to **today's
  in-process sequential path** — slower, but every machine completes
  every job.
- **Correctness is identical everywhere.** The colour math is the
  same numpy code on every machine; hardware changes speed (and the
  encoder's byte stream, exactly as today's NVENC/libx264 split
  already does), never the look.

## 5. Per-unit truth

Streamed unit results replace the bucket-as-a-unit legacy semantic:
the commit marks exported + records lineage **only for units that
actually succeeded**. Failures are logged and counted on the progress
line while the job wraps; failed cells simply don't turn. No popups
(spec/59 §8 stands).

## 6. Crash and cancel are process-shaped

- Worker crash → the job fails cleanly, the queue moves on, the app
  never feels it. Partial files self-heal via the Edited Media return
  scan (spec/57 §3).
- **Cancel → kill the whole worker process tree** (ffmpeg
  grandchildren included) — sub-second, no cooperative polling.
- App close mid-job → the worker dies with it (job object); the next
  Edit entry self-heals via the same return scan.

## 7. The spec/56 slice-4 walker becomes manifest building

Green clips (each → its `ExportPlan`) + green snapshots (→ photo
stills) per video, collected like the photo walkers collect today.
The walker is the video half of the manifest, not its own export
machinery. Day/event batch jobs carry photos AND videos from then on.

## 8. As-you-go exports stay on their immediate path

The single-item export (Export on the item you're working on; flips
it green per spec/59 §8) does **not** travel through the queue — a
one-photo export must not line up behind a 400-file day job. The
engine serves batch jobs.

## 9. What this retires / changes

- The in-process sequential render inside `_ExportWorker` /
  `_VideoExportWorker` as the batch path (they remain for as-you-go
  and the spawn-failure fallback).
- The bucket-as-a-unit exported-marking semantic (→ per-unit truth).
- Day/event batch being photos-only.

## 10. Acceptance criteria (the eyeball)

1. A real full-day export runs while Nelson scrolls grids, opens
   development, plays a video — **no stutter**.
2. Cores visibly busy (below-normal) through the photo lane; GPU
   encoder busy through clips on capable hardware.
3. Cancel lands inside a second, process tree gone.
4. Outputs land visually identical to today's (same pipeline, same
   numbers).
5. Pulling the plug mid-job (kill the app) loses nothing after the
   next Edit entry's return scan.

## 11. Implementation record

**Slice 1 — the worker protocol + photo lane (LANDED 2026-06-12).**

- `core/export_manifest.py` — the work order (`PhotoUnit` = the exact
  `_render_one` inputs, journal-resolved app-side; JSON wire via a
  temp file; unknown keys dropped on load so an older worker survives
  a newer manifest).
- `core/render_worker.py` — `worker_main`: photo lane N-wide on a
  thread pool (width = cores−2 floor 1, capped by an available-RAM
  budget read from `GlobalMemoryStatusEx`); renders through the
  UNCHANGED `_render_one` / `_write_image` (§1 parity by
  construction); per-unit JSON-lines protocol on stdout
  (start/unit/done/fatal; pure-ASCII wire; logging on stderr) with
  the resolved tone numbers echoed per unit for the lineage snapshot;
  the worker lowers ITSELF to below-normal priority (ffmpeg children
  inherit the class — §2 holds regardless of spawner); `_NameReserver`
  arbitrates in-flight output-name collisions the serial engine never
  had (two same-named units land as `name` + `name (2)`, never a
  silent clobber). Per-unit truth (§5): bad files are `error` lines;
  the job always runs to `done`, exit 0. `worker_command()` returns
  the source-vs-packaged argv (§1, one binary).
- `mira/__main__.py` — NEW: the one-binary dispatch
  (`--render-worker` → the Qt-free worker; else the UI). Found on the
  way: **build.bat already pointed at this file but it didn't exist**
  — the packaged build was broken; this fixes it.
- `tests/test_render_worker.py` — 13, incl. a real-subprocess
  end-to-end via `worker_command`.

**Slice 2 — the app-side job + per-unit commit (LANDED 2026-06-12).**
Photo day/event batch now RUNS on the engine.

- `core/worker_job.py` — `WorkerJob`: spawn (below-normal + no
  console window — belt to the worker's own braces, covers the
  import-heavy startup), a Windows **job object with
  kill-on-close** (app dies → worker tree dies, §6; best-effort,
  logged when unavailable), `kill()` = `TerminateJobObject` (tree,
  sub-second), a daemon stderr-drain relaying worker logs into the
  app log + keeping a tail for diagnostics, and the blocking
  JSON-lines `messages()` reader (non-JSON noise skipped).
  `BatchJobResult(ExportResult)` + `build_batch_result`: success
  buckets filled ONLY from ok units — the lineage writer inherits
  per-unit truth UNCHANGED; raw `unit_results` + `resolved_by_name`
  (the params_sink twin) ride along for the commit.
- `core/render_worker.py` += `run_manifest_inline` — the §4 last
  resort: same manifest, in-process, deliberately SEQUENTIAL (an
  in-process pool would soak cores at normal priority and break §2).
- `mira/ui/edited/export_job.py` — `BatchExportJob(QThread)`,
  the queue-contract adapter: temp-file manifest → spawn → per-unit
  progress relay → result fold. Spawn failure → inline fallback; a
  worker that STARTED then died is NOT re-run inline (§6 — could
  double-write; the units that finished commit, the rest self-heal
  via the return scan).
- `edit_host_page._run_batched_export` rewired: builds the manifest
  (per-item Adjustment → recipe, resolved where the gateway lives);
  the per-bucket journal glue `_build_journal_for_items` DELETED
  (its docstring predicted exactly this retirement). The commit is
  per-unit: `set_edit_exported` + lineage for `ok_unit_ids` only;
  failures logged + cells simply don't turn; CANCEL now commits the
  units already on disk (they are real, atomic, finished exports —
  the legacy path threw that truth away). Modal no-window fallback
  path kept, same job object.
- `tests/test_worker_job.py` — 7: result folding, real spawn+stream,
  **kill-mid-job** (200-unit manifest; tree dead, partials honest,
  the reserver's same-stem fan-out verified on disk), inline
  fallback render + cancel, the Qt adapter end-to-end + spawn-fail
  fallback. Neighbors: test_render_worker 13 + test_export_status 11
  green; test_exported_watermark 14 pass (its teardown fastfail
  reproduces at baseline WITHOUT this slice — the documented
  machine-local crash, not ours); MainWindow construct-smoke OK.

**Slice 3 — the clip lane, the ladder, the walker (LANDED 2026-06-12).**
Day/event batch now carries videos too.

- `core/encoder_ladder.py` — the §4 hardware ladder. Probe order
  NVENC → Intel QSV → AMD AMF → libx264 (the floor — always works);
  every step is a real test encode (being LISTED in `ffmpeg
  -encoders` doesn't guarantee a working GPU/driver). Cached per
  process; ONE calm INFO log per session per the spec. The CRF +
  preset of every option targets the same quality.
  `video_export_run._video_encoder_args` now delegates here (the
  workshop single-clip Export rides the same ladder for free).
- `core/export_manifest.py` — new `ClipUnit` (source, dest_dir,
  base_name, plan dict, style for lineage). `ExportManifest.clips`
  defaults to `()` so slice-1 / slice-2 binaries keep parsing
  slice-3-shaped manifests; a slice-3 binary parses legacy
  no-clips manifests too.
- `core/render_worker.py` — the clip lane (§3): one-at-a-time,
  frame-parallel inside (`export_processed_clip` does its own
  internal ThreadPool). Runs SIDE BY SIDE with the N-wide photo
  lane under one `as_completed` loop — when one lane empties the
  other takes the full width. Every unit message now carries a
  `kind` (`"photo"`/`"clip"`) so the host's commit can route. The
  `start` message gains a `clips` count.
- `core/worker_job.py` — `BatchJobResult.ok_clip_results` exposes
  ok clip messages for the host; `build_batch_result` keeps clips
  OUT of `written`/`overwritten`/`renamed` (the photo lineage
  walker keys by stem; clip output names like `v_clip1.mp4` would
  mismatch). Photo-only `resolved_by_name` stays the
  lineage-snapshot bridge.
- `core/edit_export_walker.py` — the spec/56 slice-4 walker. Picked
  `VideoSegment` rows → `ClipUnit`s, geometry from
  `core.video_segments.segment_bounds`, plan from
  `build_export_plan` via a caller-supplied override-shim (keeps
  QtMultimedia out of the walker). Skips on missing source /
  bad geometry / no item — never trips the worker mid-batch.
- `edit_host_page._run_batched_export` extended:
  `_collect_clip_segments_for_day` / `_for_event` collect picked
  segments; the walker builds clip units alongside photo units;
  the commit closure adds `set_edit_exported` + `record_single_
  lineage` per ok clip (recipe re-read from `VideoAdjustment`,
  resolved params echoed back from the worker). The dialog prompt
  reads "N photo(s) and C clip(s)" when both are non-zero.
- Tests: `test_encoder_ladder.py` (5 — every rung + cache), `test_
  edit_export_walker.py` (5 — happy + 3 skip paths + plan-dict
  round-trip into ExportPlan), `test_clip_lane.py` (7 — manifest
  round-trip with clips, legacy-no-clips load, result fold keeps
  clips out of photo buckets, worker_main two-lane stream, missing
  source skip, runner-error per-unit truth, inline fallback
  ordering). The slice-1 `test_per_unit_truth` exact-dict-literal
  pin updated for the new `kind` field. Upstream `test_video_
  export_run.py` encoder probes re-pointed at the ladder module
  (the legacy private function retired). 60 across the spec/60
  stack + the upstream + neighbor sweeps; MainWindow construct-
  smoke OK.

**What lands next is the §10 eyeball — Nelson's call.** No more
build slices in spec/60; the engine is whole. spec/56 slices 1-3 +
slice 5 (cleanup) remain as their own program of work; the §4
inventory retirement (clip_span, trim deltas, Pick video special-
casing) is independent of this engine.
