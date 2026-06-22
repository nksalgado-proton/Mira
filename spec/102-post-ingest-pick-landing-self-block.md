# 102 — Post-ingest auto-landing self-blocks Pick (flag cleared too late)

**Status: PROPOSED (Nelson 2026-06-22). Fixes a self-inflicted bug: after
an import finishes, the app's own auto-landing into Pick hits the
"still importing" gate and refuses to open — because the in-progress flag
is cleared in a `finally` that runs AFTER the landing navigation. Pure
control-flow ordering; not a race, not a stuck flag. Touches one method,
`_finish_collect_ingest` in `mira/ui/shell/main_window.py`. No keymap /
charter-invariant impact.**

## 1. The bug

`_finish_collect_ingest` (the ingest's UI-thread tail, spec/84 §3) ends:

```python
try:
    ...
    self._record_collect_in_event_db(...)      # item rows written — import is DONE
    self.gateway.refresh_index_entry(event_id)
    ...
    self._on_event_created(event_id)
    if land_phase and self._current_event_id == event_id:
        self._on_phase_activated(land_phase)   # ← auto-land into Pick
finally:
    self._mark_ingest_finished(event_id)       # ← flag cleared HERE, too late
```

When `land_phase == "pick"`, `_on_phase_activated("pick")` consults the
Pick-entry gate (spec/84 §5):

```python
if self.is_ingesting(self._current_event_id):
    QMessageBox.information(self, tr("Still importing"),
        tr("This event is still importing — try Pick again when the "
           "import finishes."))
    return
```

`is_ingesting` is **still True** — the flag is cleared by
`_mark_ingest_finished` in the `finally`, which has not run yet (we are
still inside the `try`). So the sequence the user observes is: import
finishes → days/2×2 grid opens → app tries to advance into Pick → the
gate meant to stop a *premature user* entry instead blocks the *app's own*
landing → the flag clears a beat later in `finally`. Retrying Pick by hand
then works, which is the tell.

The gate is correct; it is just consulted one step before the flag that
feeds it is cleared, in the one flow (programmatic post-ingest landing)
that should never be gated.

## 2. The fix

Clear the in-progress flag **before** the navigation tail — the import is
genuinely complete once the `item` rows are written and the index is
refreshed, so mark it finished there, then navigate:

- Call `self._mark_ingest_finished(event_id)` immediately before
  `self._on_event_created(event_id)` (i.e. before the "Import complete"
  dialog + the `land_phase` landing).
- **Keep the `finally: self._mark_ingest_finished(event_id)`** as an
  idempotent backstop — `set.discard` is safe to call twice — so the
  early-return branches (crash / no-payload / zero-media cleanup), which
  never reach the navigation, still clear the flag exactly as today.

Net: by the time any landing navigation runs, `is_ingesting(event_id)` is
False, so the auto-land into Pick proceeds; every non-navigating branch
still clears via the finally.

## 3. Why not exempt the gate instead

An alternative would be to give the programmatic landing a
"bypass the gate" flag. Rejected: the gate should reflect truth, and the
truth at the navigation point is that the import IS finished (rows
written, index refreshed). Clearing the flag at the real completion
point is simpler and also fixes the Events-tile / second-enqueue
consumers of the same flag, which are momentarily wrong in the same
window.

## 4. Acceptance

- A new-event Collect that lands on Pick (`land_phase == "pick"`) opens
  the Pick dashboard directly after import — no "still importing" dialog,
  no manual retry.
- The Events tile reappears and the second-enqueue gate releases at the
  same (correct) moment.
- Crash / no-payload / zero-media-cancel branches still clear the flag
  (finally backstop) and do not navigate.

## 5. Tests

- `tests/test_post_ingest_pick_landing.py` — drive `_finish_collect_ingest`
  with a successful result and `land_phase="pick"`; assert
  `is_ingesting(event_id)` is False at the moment `_on_phase_activated` is
  invoked (e.g. spy/patch `_on_phase_activated` to record the flag state),
  and that no "still importing" dialog path is taken.
- Regression: an error-result run and a zero-media-cancel run both leave
  `is_ingesting(event_id)` False afterward (finally backstop).
