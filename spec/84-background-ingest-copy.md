# spec/84 — Ingest as a background job (queue · progress · cancel, like export)

**Status:** design agreed with Nelson 2026-06-17 — the final Collect-phase
priority. Today, after the user finishes event creation / Collect (header, days
table, timezone, plan confirm), pressing OK starts the **file copy into
`Original Media/`** behind a **modal progress dialog that freezes the whole
app** until it finishes. This spec makes ingest a **background job on the
existing batch queue**, with **all the same features as a batch export job** —
queue, per-file progress line, cancel — so the user fills in the details, hits
OK, and keeps working while the copy runs.

Read with: `spec/60` (the export engine — the job model being matched), `spec/52`
(event creation flow), `spec/57` (folder rules + interrupted-ingest resume),
`spec/82` (the day-add snapshot trigger). Constraints: `core/` Qt-free; atomic;
invariant #7 (captured tree only grows via sanctioned ingest); `tr()`.

---

## 1. What blocks today
- The copy is `core/ingest_pipeline.run_ingest` (route → copy → bake), driven by
  the UI-facing `mira/ingest/engine.run_ingest`. It is **already Qt-free and
  already takes a `progress(msg, cur, tot)` callback** — the engine is fine.
- The UI runs it through `mira/ui/base/progress.py`, which by its own docstring
  runs the work **on the GUI thread behind a modal `QProgressDialog`**. That
  modal-on-the-GUI-thread is the freeze.

The fix is to stop running it modally and run it as a queued background job.

## 2. Reuse the batch queue — ingest is just another job
The app already has the exact infrastructure, in `mira/ui/shell/batch_queue.py`:
- **`BatchExportQueue`** — a strictly-serial job runner (`enqueue(worker, label,
  on_finished)`, `cancel_current()`, a `changed` signal, `idle` / `queued_count`
  / `progress` properties). The user keeps working anywhere while it runs.
- **`BatchProgressLine`** — the one progress line below the menubar (label ·
  per-file progress · how many wait · Cancel; hidden when idle).
- **The job contract is duck-typed:** any object with `progress(int, int, str)`
  + `finished_result(object)` signals and `start()` / `cancel()`.
  `BatchExportJob(QThread)` (`mira/ui/edited/export_job.py`) is the reference
  implementation.

**Plan:**
1. **Generalise the queue** — it is already generic; rename `BatchExportQueue`
   → **`BatchJobQueue`** (keep a thin `BatchExportQueue` alias if churn is a
   concern) and make `BatchProgressLine` label job-type-aware (*"Importing…"* vs
   *"Exporting…"*). One queue serves both job types, app-wide.
2. **Add `IngestJob(QThread)`** (`mira/ui/ingest/ingest_job.py` or near the
   ingest UI) mirroring `BatchExportJob`: `run()` calls the existing
   `run_ingest`, the engine's `progress` callback drives `self.progress.emit`,
   `cancel()` sets a flag the copy loop checks, `finished_result` carries the
   ingest result. **Same contract, so the existing queue + progress line work
   unchanged.**
3. **OK enqueues the job** instead of opening the modal dialog. The dialog
   returns immediately; the copy runs on the queue.

## 3. Database safety — copy on the worker, commit on the UI thread
Mirror the export model exactly (spec/60: *"the worker never touches
`event.db`"*):
- The `IngestJob` thread does **copy + hash + bake** (the Qt-free
  `core/ingest_pipeline` half) and emits per-file progress; it returns a result
  carrying the per-file `CopyResult`s.
- The **`item` rows are written in `on_finished`, on the UI thread** (the queue
  already runs `on_finished` there) — or batched through the gateway on the
  owner thread. No two threads share a SQLite connection. This is the same
  split the export queue uses for its commit.

## 4. Serial queue — one ingest at a time (resolves the old open question)
The queue runs **strictly one job at a time, app-level** — exports and ingests
share it, so they never thrash one disk and there is no concurrency to manage.
This settles spec/84's earlier "concurrent vs queued" question: **queued**, like
export. A per-event **"ingest in progress"** state still guards against opening
Pick on half-copied media or enqueuing a second copy into the *same* event
("still importing — N of M").

## 5. The event surfaces in the Events screen only when the copy completes
The event **record** is created at OK — the job needs an event to copy into and
to write `item` rows against. But the **tile appears in the Events screen only
on the job's `finished_result`** (copy done) — until then the import lives only
in the progress line (§2). A tile = a finished import, never a half-copied
placeholder.
- **Completes →** tile appears; the spec/82 per-day-add snapshot fires (on the
  *done* signal, not at OK).
- **Cancelled / failed with zero media →** remove the event record (spec/57
  §4.3.1 "cancel = clean no-op").
- **Cancelled with some media copied →** surface a resumable tile (spec/57's
  re-run-resumes rule). *(Disposition — §9.)*

## 6. Cancel / crash / close — process-shaped, like export
- **Cancel** = the progress line's Cancel → `queue.cancel_current()` →
  `IngestJob.cancel()`; the copy loop stops at the next file. Partial copies are
  safe (spec/57 §4.3.1: re-run keeps in-place copies, identical bytes ingest
  once, invariant #7 never overwrites). Message: *"Import cancelled — N of M
  copied; re-run to finish."*
- **App close mid-copy** — the `QThread` dies with the app; next open the
  interrupted-ingest reconcile finishes the remainder; the trailing, atomic
  per-row DB write means no corruption.

## 7. What this unlocks — the batch wish, natively
Because OK returns immediately and the queue accepts many jobs, the user can
**enqueue the next event's import while the previous one copies** — the queue
serialises them and the progress line shows the backlog ("…· 2 waiting"). The
decades-of-photos import becomes: create, OK, create the next, OK — no waiting
on each. A future "point at a parent folder, import many events unattended"
orchestrator can simply enqueue a job per discovered folder onto this same
queue; that orchestrator is out of scope here.

## 8. Slices (each its own commit + `verify.bat`)
1. **Generalise the queue** — `BatchExportQueue` → `BatchJobQueue` (+ alias);
   `BatchProgressLine` label job-type-aware; existing export path still green.
2. **`IngestJob`** — the `QThread` mirroring `BatchExportJob` around
   `run_ingest`; progress relay + cancel flag; result carries the `CopyResult`s.
   Tests (a stub-engine job emits progress and a result).
3. **Enqueue on OK + commit on finish** — OK enqueues the job and returns;
   `on_finished` writes the `item` rows on the UI thread (§3); kill the modal
   `QProgressDialog` path for ingest.
4. **Deferred tile + concurrency guard** — event tile appears on
   `finished_result`; zero-media cancel cleans up; per-event "ingest in
   progress" flag + the Pick-while-importing warning (§4–§5).
5. **Cancel + interrupted-run polish** — wire Cancel through the queue; confirm
   spec/57 resume + the messages; snapshot fires on the done signal (§6).

Slices 1–3 deliver the headline ("OK and keep working, with a real progress
line + queue"); 4–5 make it safe and tidy.

## 9. Open questions
- **Partial-cancel disposition** — surface a resumable tile immediately (lean)
  vs. keep it hidden until a re-run finishes (§5).
- **Queue-name churn** — full rename to `BatchJobQueue` vs. keep
  `BatchExportQueue` and just generalise the label (lean: rename, it now serves
  both).
- **Progress-line detail** — show per-file name for ingest (like export) vs. a
  simpler "Copying N of M"; lean: match export exactly.
