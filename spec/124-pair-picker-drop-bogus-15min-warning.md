# 124 — Sync-pair picker: drop the bogus "more than 15 minutes apart" warning

**Status: PROPOSED (Nelson 2026-06-23). After picking a pair for the
measured-offset correction (spec/123 source 3), the picker shows the raw
delta (e.g. `+5:00:02`) to accept — but also a warning: *"⚠ The two photos
are more than 15 minutes apart in real time. Pick a closer pair…"*. That is
self-contradictory: the whole purpose of a measured pair is to capture a
clock/TZ offset, which is hours, so a large raw delta is the **signal**, not
an error. Remove the warning. One-block fix in
`mira/ui/base/sync_pair_picker.py` (the `configured_tz is None` branch,
~lines 482-509). No data-model change.**

## 1. The bug

In `_recompute_verdict` (the no-`configured_tz` / measured-pair branch):

```python
warn = abs(raw) > timedelta(minutes=15)
...
"⚠ The two photos are more than 15 minutes apart in real time.
 Pick a closer pair — the further apart they are, the less likely
 they really depict the same moment."
```

`raw = ref_t − cam_t` is exactly the offset to be applied (spec/123: the
measured delta, raw, no snapping). For any genuine TZ/clock correction this
is hours (Nepal: ~5h), so the `> 15 min` test is true in the normal case and
the warning fires precisely when the tool is working as intended. It is a
leftover of the snap-era "pairs must be ~simultaneous in *recorded* time"
assumption, which the raw-delta model abandoned.

## 2. The fix

In the `else` (no `configured_tz`) branch:

- **Delete** the `warn`/`abs(raw) > 15 min` logic and the warning HTML.
- Keep the verdict line showing the raw delta and the "applying the measured
  offset as-is (no snapping)" note.
- Button stays enabled; `_final_offset = raw` (unchanged).

The user asserts simultaneity by *choosing* the pair; the dialog cannot and
should not second-guess the magnitude — there is no upper bound on a
legitimate clock offset (two zones can differ by up to ~26h).

Optional (only if a guard is still wanted): replace the 15-minute test with
an **impossible-offset** sanity note at a much higher bound (e.g. `> 26h`,
beyond any real zone pair — suggesting a wrong date, not a wrong pair). Not
required; the clean removal is the intended fix.

## 3. Leave the other branch alone

The `configured_tz is not None` branch (validate a pair against a *declared*
TZ, 30-min tolerance) is a different, legitimate check and is **not** part of
this bug — do not touch it.

## 4. Acceptance

- Picking a measured pair with a multi-hour raw delta shows the delta and
  the "applied as-is" note, the Use button enabled, and **no** "15 minutes
  apart" warning.
- The accepted offset is still the raw delta (spec/123 source 3 unchanged).

## 5. Tests

- `tests/test_sync_pair_picker.py` — a pair with a 5-hour raw delta yields no
  warning text, Use enabled, `final_offset == raw`; (if the optional >26h
  guard is added) a 30-hour delta surfaces the impossible-offset note.
- Regress the `configured_tz`-present branch verdict (untouched).
