# 104 — Quick Sweep day-navigator chevrons are dead (gateway-only step handler)

**Status: PROPOSED (Nelson 2026-06-22). Fixes a bug: inside a Quick Sweep
days grid, the day-navigator pill's ‹ / › buttons (next to the day
description) do nothing — the standalone QS has no gateway event, and the
day-step handler both early-returns on a missing event id and sources its
day list from the gateway. Touches one method,
`_on_days_grid_step_day` in `mira/ui/shell/main_window.py`. No keymap /
charter-invariant impact.**

## 1. The bug

The day pill's chevrons emit `prev_day_requested` / `next_day_requested`,
wired to `_on_days_grid_step_day(±1)`:

```python
def _on_days_grid_step_day(self, delta):
    if self._current_event_id is None:
        return                                   # ← standalone QS bails here
    cur = self.days_grid_page.current_day_number()
    eg = self.gateway.open_event(self._current_event_id)
    days = sorted(d.day_number for d in eg.trip_days() ...)   # ← gateway-only axis
    ...
    self._on_days_lists_day_activated(days[idx])
```

A **standalone** Quick Sweep (paths mode) has `_current_event_id is None`
and no `event.db`, so the handler returns on the first line — the chevrons
are inert. The day axis it would need lives in the QS session
(`self._quick_sweep["items_by_day"]` / the day snapshots), not the gateway.
(Per-event QS happens to limp through because the handler reaches
`_on_days_lists_day_activated`, which re-routes via `self._quick_sweep` —
but it still pays a needless gateway open and shares the broken
assumption.)

## 2. The fix — route day-step through the QS session when one is active

Mirror the QS branch that `_on_days_lists_day_activated` and `_qs_open_day`
already use. At the top of `_on_days_grid_step_day`:

```python
if self._quick_sweep is not None:
    cur = self.days_grid_page.current_day_number()
    days = sorted(self._quick_sweep["items_by_day"].keys())   # QS day axis
    if cur not in days:
        return
    idx = days.index(cur) + delta
    if 0 <= idx < len(days):
        self._qs_open_day(days[idx])
    return
# ... existing gateway path unchanged for the non-QS Pick/Edit/Export grid
```

`_qs_open_day` already handles BOTH standalone (paths mode via `setDay`)
and per-event (gateway via `open_for_day`) QS, so this single branch fixes
both QS modes and removes the redundant gateway open for per-event QS.
Derive `days` from the QS session's day set (the standalone
`items_by_day` keys, or the per-event day list the session tracks) — not
from `eg.trip_days()`.

## 3. Acceptance

- In a standalone Quick Sweep spanning ≥ 2 days, the day-pill ‹ / ›
  buttons move to the previous / next day's grid; no-op at the ends.
- In a per-event Quick Sweep, the chevrons still work and no longer open
  the gateway just to read the day list.
- The non-QS Pick / Edit / Export days grid keeps today's gateway-driven
  step behaviour exactly.

## 4. Tests

- `tests/test_qs_day_step.py` — with a standalone QS session of 3 days,
  `_on_days_grid_step_day(+1)` from day 1 lands on day 2 (and updates
  `self._quick_sweep["current_day"]`); `-1` from the first day and `+1`
  from the last are no-ops.
- Regression: with no QS session and a gateway event, the handler still
  steps via the gateway axis.
