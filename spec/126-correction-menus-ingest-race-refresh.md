# 126 — Camera Clocks / Adjust TZ read stale during background ingest (refresh + guard)

**Status: PROPOSED (Nelson 2026-06-23). Right after creating an event with
clock/TZ adjustments, "Camera Clocks…" and "Adjust TZ…" show **no
adjustments**; only an app restart reveals them. Not data loss — confirmed:
ingest runs on a **background QThread** (`run_ingest` on the IngestJob's
thread; in-flight events tracked in `_ingesting_event_ids`, cleared by
`_mark_ingest_finished`), and the camera offsets + corrected times are
committed at the **end** of that job (`mira/ingest/engine.py` →
`gateway.create_event` → `eg.close()`). `eg.cameras()` is a live read and
`save_camera` commits via WAL, so reads are normally instant — but the two
correction menus open a fresh gateway **without checking `is_ingesting()`**,
so when invoked before the background commit lands they read an `event.db`
whose offsets aren't written yet. Restart reads the long-since-committed db
→ correct. Fix: (a) **guard** the correction menus while the event is still
ingesting; (b) **refresh** current-event state when the background ingest
finishes, so the values appear without a restart. Touches
`mira/ui/shell/main_window.py`. No data-model change.**

## 1. Cause (confirmed from code)

- Ingest is asynchronous (background QThread, spec/84 family). The event
  becomes visible / current before its DB write necessarily completes.
- `_open_camera_clocks_for_event` and `_open_adjust_tz_for_event`
  (main_window.py) only check `self._current_event_id is not None`, then
  `eg = self.gateway.open_event(...)` + read. **No `is_ingesting()` check.**
- The camera offsets (`applied_offset_seconds`) and item corrected times are
  part of the background `create_event` commit. Read before that → absent.
- `is_ingesting(event_id)` already exists (`event_id in
  self._ingesting_event_ids`), set on ingest start and cleared by
  `_mark_ingest_finished`. The correction paths simply don't honour it.

## 2. Fix

### A. Guard the correction menus while ingesting
At the top of both `_open_camera_clocks_for_event` and
`_open_adjust_tz_for_event`, if `self._current_event_id is None` **or**
`self.is_ingesting(self._current_event_id)`, show a brief, honest notice —
*"This event is still finishing import. Try again in a moment."* — and
return, instead of opening the dialog on a half-written db. (Same guard
family as the existing import-state checks; reuse the wording so it's
consistent.)

### B. Refresh current-event state when ingest finishes
In `_mark_ingest_finished` (and/or `_finish_collect_ingest`), after clearing
`_ingesting_event_ids`, refresh the in-memory current-event view so the
just-committed data is reflected **without a restart**: re-read via a fresh
`open_event` where relevant, `self.events_page.refresh()` /
`self.phases_page.set_event(event_id)` as already done elsewhere, and (if the
finished event is the current one) re-enable the now-valid correction menus.
A subsequent open of Camera Clocks then reads the committed offsets.

### C. (Optional, belt-and-braces) Block on commit for the just-created event
If the product prefers the correction menus to *work immediately* after the
wizard rather than show a "still importing" notice, have the create flow
ensure `create_event`'s commit has landed (the job already runs
`eg.close()`); the guard in §A then only ever fires for genuinely in-flight
ingests. Not required — §A + §B resolve the reported bug.

## 3. Acceptance

- Opening "Camera Clocks…" / "Adjust TZ…" while the event is still ingesting
  shows the "still finishing import" notice, never a silently-stale
  no-adjustments view.
- Once the background ingest finishes, opening either menu shows the
  committed offsets **without restarting the app**.
- An already-settled event is unaffected (menus open immediately as today).

## 4. Tests

- `tests/test_correction_menu_ingest_guard.py` — with `event_id` in
  `_ingesting_event_ids`, `_open_camera_clocks_for_event` /
  `_open_adjust_tz_for_event` show the notice and do **not** open the dialog;
  with it cleared, they open and read the committed cameras.
- `tests/test_ingest_finish_refresh.py` — `_mark_ingest_finished` triggers
  the current-event refresh (events_page.refresh / phases set_event) so a
  follow-up `open_event().cameras()` returns the written
  `applied_offset_seconds` without a restart.
- Regress: a non-ingesting event opens the correction menus unchanged.
