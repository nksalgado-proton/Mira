# 128 — "I don't know the TZ" recognition: tighten the window + one-pair-at-a-time browser

**Status: PROPOSED (Nelson 2026-06-23). When the user says they don't know a
camera's timezone, the app proposes near-simultaneous photo pairs (camera +
reference/phone) so the user can recognize one taken at the same real
moment — confirming the clock was correct (spec/123 source 2 → zero
correction). Two problems: (1) the candidate window is far too loose
(`clock_recognition.TIGHTNESS_TOLERANCE = 7m30s`, plus a snap-era
`MAX_PAIR_RAW_DELTA = 15min`), so pairs whose clocks are minutes apart get
offered as "same moment" evidence — which is meaningless. (2) the
presentation (`RecognitionDialog`, stacked 3–6 "camera-left / phone-right"
rows) is unreadable when photos differ in aspect ratio / orientation. Fix:
**tighten the window to ~60s**, and **rebuild the dialog as a one-pair-at-a-
time browser** reusing the (well-liked) `SyncPairPickerDialog` photo
framing, with **‹Prev / Next›** and two honest buttons. Also **drop the
snap-era impact/Apply page** — a confirmed recognition now just means zero
correction, nothing to preview. Touches `core/clock_recognition.py`
(window) and `mira/ui/pages/clock_recognition_dialog.py` (rebuild). Builds
on spec/123; the manual picker (`SyncPairPickerDialog`) is unchanged and
reused as the fallback.**

## 1. Tighten the candidate window

The recognition screen concludes "the clock was correct" only if the
recognized pair's two **clock times nearly coincide**. That requires a tight
window:

- Replace `TIGHTNESS_TOLERANCE` (7m30s) with a tight default **±60s**
  (a named const, e.g. `RECOGNITION_WINDOW = timedelta(seconds=60)`; 30s is
  an acceptable tighter value). Candidates = (camera item, reference item)
  pairs whose recorded clock times differ by ≤ the window.
- Remove the snap-era `MAX_PAIR_RAW_DELTA` / 15-minute-grid reasoning from
  **candidate selection** — the recognition path no longer snaps or infers a
  TZ (spec/123). (`find_candidate_pairs` may keep light scene clustering for
  ordering only.)
- **Rank closest-first** (smallest clock-time difference first).
- **No candidates in the window → skip straight to the manual picker**
  (don't show an empty recognition dialog).

## 2. Rebuild `RecognitionDialog` as a one-pair browser

Replace the stacked-rows picker with a single-pair viewer:

- **One candidate pair at a time, large, at the top.** Render each photo in
  its own **consistent letterboxed frame** (so mixed aspect ratio /
  orientation reads cleanly), each clearly **labeled** — "Camera
  (`<camera_id>`)" and "Reference (`<phone_id>`)." Reuse the
  `SyncPairPickerDialog` photo-panel treatment the user already likes.
- **‹ Prev / Next ›** steps through the (closest-first) candidate list, with
  a position indicator ("Pair 2 of 7").
- **Buttons (bottom):**
  - **"Photos were taken at the same real-life time"** → confirm: the clock
    was correct → **zero correction** (spec/123 source 2). Dialog accepts
    with that result.
  - **"Choose a pair yourself"** → accept with `fallback_to_manual = True`;
    the caller opens `SyncPairPickerDialog` (the measured-offset path,
    spec/123 source 3 — unchanged, well-liked).
  - **Cancel.**
- **Drop the preview/impact page** ("Shifting N photos by ±X; M move to a
  different day… Apply"). It was the snap-era application of an implied
  offset; a confirmed recognition is now just 0, so there is nothing to
  preview or apply.

## 3. Result contract

The dialog returns one of three outcomes (replacing the old "snapped
`CalibrationPair`"): **Recognized-correct** (apply offset 0 to the camera),
**Fallback-manual** (open `SyncPairPickerDialog`), or **Cancel**. The caller
applies 0 on recognized-correct via the normal correction path (spec/123 /
spec/127).

## 4. Acceptance

- Only pairs whose clock times are within ~60s are offered; a pair minutes
  apart never appears as "same moment" evidence.
- The dialog shows one pair at a time, each photo framed + labeled, readable
  regardless of aspect/orientation; ‹Prev/Next› browses closest-first.
- "Same real-life time" confirms → the camera gets **zero** correction (no
  snapped offset, no impact preview).
- "Choose a pair yourself" opens the unchanged manual picker.
- No in-window candidates → the manual picker opens directly, no empty
  recognition screen.

## 5. Tests

- `tests/test_recognition_window.py` — candidate selection includes a pair
  60s apart, excludes one 2 min apart; ranked closest-first; empty result
  signals fallback.
- `tests/test_recognition_dialog_browser.py` — one-pair-at-a-time render
  (frame + label per side), Prev/Next navigation + position indicator;
  "same time" → recognized-correct (offset 0) result; "choose a pair
  yourself" → `fallback_to_manual`; the impact/Apply page is gone.
- Remove the snap-era recognition/preview tests; the `SyncPairPickerDialog`
  tests are untouched.
