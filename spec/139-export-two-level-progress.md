# 139 — Export progress: two levels (aggregate + per-file), so long videos show movement

**Status: PROPOSED (Nelson 2026-06-23). The export progress bar shows only
**aggregate** progress (files done ÷ total). A single file — especially a
video that takes a long time to encode — leaves the bar **stationary** for
the whole encode, so the user can't tell anything is happening. Split the
display into **two levels**: an **aggregate** bar (file N of M + name) and a
**per-file** bar (the current file's own progress). The per-file data already
exists and is currently discarded: `core/video_export_run.py` polls
`progress(done_frames, total_frames)` per few frames (line ~217) but only for
cancellation. Thread that fraction up and show it. Touches
`core/process_export_engine.py` (progress contract),
`core/video_export_run.py` (surface the fraction),
`mira/ui/exported/batch.py` + the export progress UI (second bar). No
data-model change.**

## 1. The data is already there

- **Aggregate:** `process_export_engine` calls `progress(done, total,
  current_name) -> keep_going` — `done/total` files (+ the active filename).
- **Per-file (video):** `video_export_run` already computes
  `progress(done_frames, total_frames)` every `_PROGRESS_EVERY` frames — a
  real 0→100 % for the current encode — but it's consumed only as a
  cancel check, never surfaced.
- **Per-file (photo):** a single photo write is near-instant; its per-file
  bar just snaps to 100 % per file (or stays hidden for photos).

So the fix is plumbing + a second bar, not new computation.

## 2. The contract

Extend the export progress signal to carry **both** levels. Either:

- add a **`file_fraction: float` (0.0–1.0)** to the engine's progress
  callback — `progress(done, total, current_name, file_fraction)` — fed from
  the video frame ratio (and `1.0` for an instantly-written photo); or
- emit a **separate per-file progress** signal alongside the aggregate one.

The video path forwards its `done_frames/total_frames` as `file_fraction`;
the photo path reports the file then `file_fraction = 1.0`. The aggregate
`done` still advances only on file completion (correct), while
`file_fraction` moves continuously within the current file.

## 3. The UI — two bars

In the export progress surface (`batch.py` progress line / its host):

- **Aggregate bar:** "Exporting **N of M**" — `done/total`. **No filename** —
  the count + a moving per-file bar convey progress; the filename is clutter
  (and long names overflow). (The engine callback may still pass
  `current_name` for logs, but the UI does not display it.)
- **Per-file bar** (under it): the current file's **`file_fraction`** as a
  0–100 % bar — no filename; a generic "encoding…" hint is fine for videos.
  For a long video this bar fills smoothly so the user sees motion even while
  the aggregate count holds.
- (This realises "split the progress in two" as two clear bars — cleaner than
  literally halving one bar. If a single combined bar is ever preferred, the
  smooth form is `overall = (done + file_fraction) / total` — note it, but the
  two-bar layout is the spec.)
- The per-file bar resets to 0 when the next file starts; hidden / collapsed
  when no export is running. For pure-photo batches it can stay hidden (the
  aggregate already moves fast).

## 4. Acceptance

- During a long video export the **per-file bar advances continuously**
  (frame progress) while the aggregate shows "N of M" (no filename); the user
  always sees activity.
- Photos still fly by on the aggregate bar; the per-file bar is a non-issue
  for them.
- Cancellation still works (the same per-frame poll).
- Both the per-event and cross-event / Days-Grid export-now paths show the
  two-level progress.

## 5. Tests

- `tests/test_export_progress_two_level.py` — the engine progress callback
  emits `file_fraction` that advances within a (mocked) multi-frame video and
  is `1.0` for a photo; the aggregate `done` only increments on file
  completion.
- `tests/test_video_export_forwards_fraction.py` — `video_export_run` forwards
  `done_frames/total_frames` as the per-file fraction (not just cancel).
- `tests/test_export_progress_ui.py` — the per-file bar reflects
  `file_fraction` and resets per file; the aggregate bar reflects `done/total`
  as "N of M" with **no filename shown**.
