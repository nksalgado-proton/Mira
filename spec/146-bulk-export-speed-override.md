# 146 — Bulk "set export speed for all video clips" (event-level, one action)

**Status: PROPOSED (Nelson 2026-06-23). The only place to set a video's speed
is the Video-Editor speed dropdown, which sets **both** the Edit-surface
preview speed **and** the baked export speed (`VideoAdjustment.speed`,
models.py:228 → ffmpeg `setpts`, video_export_run.py:312) with the same
value. Changing it for an event with ~50 clips means editing each clip one at
a time. Add a single **event-level action** that sets the export speed for
**all** of the event's video clips at once, so the user can normalise/override
them before re-exporting. Keeps the existing single control as-is (no split,
no new default setting — explicitly out of scope per Nelson). Touches a
gateway bulk update + one menubar / Edit-toolbar action. No new render path —
`setpts` already bakes the value on the next export.**

## 1. Scope (decided)

- **In:** a bulk action to set `VideoAdjustment.speed = X` for every video
  item in the current event.
- **Out (explicitly):** splitting the preview vs export speed into two
  controls; any new default-export-speed setting. The single coupled control
  stays exactly as it is.

## 2. The action

- A **menubar item** (e.g. under the event/Edit menu) — "Set speed for all
  video clips → [X]" with a small speed select (0.5 / 0.75 / 1 / 1.25 / 1.5 /
  2). (Edit-phase toolbar placement is fine too — wherever the user manages
  the event's videos.)
- On confirm, a **gateway bulk update** writes `VideoAdjustment.speed = X`
  for **every video item** in the event, in one transaction (creating a
  default `VideoAdjustment(item_id=…)` for items that have none, like the
  per-clip handler does at editor_page.py:2636).
- A brief confirm names the count and that it applies on re-export:
  *"Set N video clips to X× — re-export to apply."* (The bake happens on the
  next Export; already-exported files are unchanged until then.)
- This is the same value the per-clip dropdown writes, so the next export
  bakes every clip at X (and the Edit-surface preview of each clip will also
  reflect X when opened — consistent with the single-control model).

## 3. Acceptance

- One action sets the export speed of **all** the event's video clips; the
  confirm names the count; re-export bakes every clip at X.
- Non-video items are untouched.
- Clips with no prior `VideoAdjustment` get one with `speed = X`.
- 1× = today's behaviour.
- The single per-clip Video-Editor control is unchanged.

## 4. Tests

- `tests/test_bulk_export_speed.py` — the bulk action sets
  `VideoAdjustment.speed = X` on every video item (one txn), creates rows for
  items lacking one, leaves non-video items untouched, and the value flows
  into the export plan's `speed` (→ `setpts`); the confirm count matches the
  number of video items.
