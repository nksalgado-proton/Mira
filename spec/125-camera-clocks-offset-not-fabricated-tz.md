# 125 — Camera Clocks dialog: show measured offsets as offsets, not a fabricated "Custom TZ"

**Status: SUPERSEDED by spec/127 (Nelson 2026-06-23). The offset-honest
representation proposed here is folded into the unified Camera Clock
Correction dialog (spec/127 §1.2/§3), which also fixes the multi-TZ-segment
bug and adds the fine nudge. Implement spec/127 instead of this in
isolation. Original proposal retained below for context.**

**Status: PROPOSED (Nelson 2026-06-23). When a camera's correction came from
a **measured pair** (spec/123 source 3 — a raw clock delta, e.g. `+5:00`),
the "Camera Clocks…" dialog renders it as a non-existent zone — "Custom TZ
+00:45" (`trip +5:45 − offset 5:00`). That zone was never real; a measured
pair is an empirical clock delta, not a timezone. Root cause:
`main_window._open_camera_clocks_for_event` **back-derives**
`configured_tz_seconds = trip_tz_seconds − applied_offset_seconds`
(main_window.py:3919) and **ignores the camera's stored
`configured_tz_seconds`**, which is already `NULL` for pair-measured cameras.
Fix: drive the representation off the **stored** value — show a real zone
only when one was declared (source 1); show the **raw offset** (H:M:S, via
the spec/123 `HmsEntry`) when it wasn't (source 3); never fabricate a zone.
Touches `mira/ui/shell/main_window.py` (reconstruction + save) and
`mira/ui/pages/camera_clock_dialog.py` (a per-camera offset representation).
No schema change — the data already distinguishes the two.**

## 1. The problem

The per-camera state is, per spec/123, a single `applied_offset_seconds`,
reached via three sources; `configured_tz_seconds` records **only** the
source-1 case (a declared zone) and is `NULL` for source 2/3. But the dialog
reconstruction throws that away:

```python
# main_window.py ~3917
initial[c.camera_id] = {
    "correct": False,
    "configured_tz_seconds": int(trip_tz_seconds - ao)}   # ← fabricated
```

So a pair-measured camera (`applied_offset_seconds = +18000`,
`configured_tz_seconds = NULL`) is shown as a zone `+5:45 − 5:00 = +0:45`,
i.e. "Custom TZ +00:45" — a zone the camera was never in.

## 2. The fix — represent by source, off the stored value

Use the camera's **stored** `configured_tz_seconds` to choose the row's
representation; mirror spec/123's three sources:

- **`applied_offset_seconds` is 0 / None → "Clock was correct."**
- **`configured_tz_seconds` is set (source 1) → "Camera was on TZ: <zone>"**
  — the real zone picker, seeded from the stored seconds.
- **`configured_tz_seconds` is NULL but `applied_offset_seconds` ≠ 0
  (source 3) → "Clock offset: <±H:MM:SS>"** — shown/edited with the spec/123
  `HmsEntry`, **not** a zone. Optionally label it "measured" / "manual."

### Reconstruction (`main_window`)
Pass the camera's **stored** `configured_tz_seconds` and
`applied_offset_seconds` into the dialog's `initial` map verbatim — do not
recompute a zone from the offset.

### Dialog (`camera_clock_dialog.py`)
The dialog already accepts `configured_tz_seconds` in `prior` (line ~200).
Add the **offset representation** for the `configured_tz_seconds is None and
applied_offset_seconds != 0` case: an `HmsEntry` row showing the stored
offset, editable directly. The state control becomes a clean three-way
(Correct / On a known TZ / Manual offset) rather than the binary +
back-derived custom.

### Save (`main_window` + `result_answers`)
On accept, write per the chosen representation:
- Known TZ → `configured_tz_seconds = zone`, `applied_offset_seconds =
  trip_tz_seconds − zone`.
- Manual offset → `configured_tz_seconds = NULL`, `applied_offset_seconds =
  the entered H:M:S`.
- Correct → both 0/NULL.
Then `recompute_corrected_times(applied_offset_seconds)` as today.

## 3. Acceptance

- A pair-measured camera (offset +5:00) shows **"Clock offset +5:00:00"**,
  not "Custom TZ +00:45"; re-opening the dialog round-trips **+5:00:00**
  (not +0:45, not a snapped zone).
- A known-TZ camera (declared −3:00) still shows the **−3:00** zone and
  round-trips it.
- Editing either representation recomputes + applies the offset; no
  fabricated zones appear anywhere.

## 4. Tests

- `tests/test_camera_clocks_offset_repr.py` — reconstruction of a camera with
  `configured_tz_seconds=NULL, applied_offset_seconds=18000` yields the
  offset row (`+5:00:00`), NOT a derived zone; a camera with
  `configured_tz_seconds=−10800` yields the −3:00 zone; round-trip through
  save preserves each (NULL stays NULL — no zone is invented); editing the
  offset row writes `applied_offset_seconds` + `configured_tz_seconds=NULL`.
- Regress the known-TZ save path + `recompute_corrected_times` call.
