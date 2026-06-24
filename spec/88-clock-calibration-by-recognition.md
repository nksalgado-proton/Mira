# spec/88 — Camera-clock calibration by recognition (propose-and-confirm sync pairs)

**Status:** design draft, 2026-06-18. SUPERSEDED by
[spec/123](123-time-correction-rewrite.md) (Nelson 2026-06-23) for the
**applied-offset path**: the 15-minute snap (`snap_to_tz_offset` /
`snap_disagreement`) was wrong for non-clean offsets — the Nepal pair
(5h00m02s measured) sat in no zone at all from Kathmandu, and snapping
invented 4:45 / 5:45. spec/123 makes the applied offset the **raw
measured delta** (rounded to the second, never snapped). The
recognition front end may still *present* near-simultaneous candidates
to help the user pick a pair, but the offset that flows into the
calibration is the raw delta.

Replaces the *front end* of the
camera-clock calibration flow — the user no longer constructs a sync pair by
hand; the app proposes candidate pairs and the user only confirms one. The
calibration *math* (`core/clock_calibration.py`) is reused unchanged.

Read with: `spec/45` (discrete TZ slices + the per-(camera,day) declaration),
`core/clock_calibration.py` (`CalibrationPair`, `build_calibration`,
`snap_to_tz_offset`, `snap_disagreement`), `core/discrete_tz.py`
(the 15-minute offset grid), the `DiscreteTzDialog` + `sync_pair_picker`
surfaces (the current UI this fronts), and `spec/57 §4.2` (the per-day re-time
that applies a correction).

---

## 0. The failure being corrected

Cameras carry no timezone, so the user declares what TZ each camera was set
to, per day. When the user **doesn't know**, today's flow asks them to
*construct* evidence: pick one camera photo + one phone photo they believe were
shot at the same instant. The offset between those two EXIF times, snapped to
the 15-minute grid, becomes the correction.

This relies on the user's unaided judgement of simultaneity — and it is wrong
often enough to matter (Nelson, 2026-06-18: a pair that *felt* simultaneous was
~an hour apart, producing a wrong correction that silently mis-dated a whole
camera's photos). The fix is to stop asking the user to *construct* and instead
let them *recognize*: the app computes which pairs are plausibly simultaneous
at each discrete offset, ranks them, and the user confirms one.

## 1. Principle — propose → confirm, not construct

- **Statistics propose.** The camera's clock setting is *constant* over a trip
  (modulo negligible drift), so genuinely-simultaneous camera/phone pairs all
  imply the *same* snapped offset; non-simultaneous pairs scatter. The
  dominant pile is the proposal.
- **Recognition confirms.** The app shows a few tight, recognizable example
  pairs from the top pile; the user confirms the one they remember shooting at
  the same moment. Human recognition guards against a false statistical peak;
  statistics guard against a single mis-judged pair.
- **Manual is the last resort.** The current hand-picked-pair flow
  (`sync_pair_picker`) survives only for when the app can propose nothing the
  user recognizes.

## 2. The algorithm (per camera)

For every (camera photo `c`, phone photo `p`) within the trip:

1. Compute the offset the pair would imply and **normalize to the camera's
   constant set-TZ** so a multi-zone trip still clusters cleanly:

   ```
   off = Tp − Tc                       # CalibrationPair.offset convention
   κ   = phone_tz(p) − off             # = the camera's set TZ, constant per trip
   ```

   `phone_tz(p)` is the phone's UTC offset on `p`'s day (phone EXIF /
   `trip_day.tz_minutes`). For single-zone trips `phone_tz` is constant and
   clustering on `off` directly is equivalent.

2. Snap `κ` to the 15-minute grid (`snap_to_tz_offset`); keep
   `snap_disagreement(κ, snapped)` as the pair's **tightness**.

3. **Cluster** the snapped `κ` over all pairs. The peak is the camera's set TZ;
   per-day corrections fall out as `trip_day.tz − κ*` (spec/57 §4.2 applies
   them). Tolerance = tightness `<` ~7.5 min (half the 15-min step) so adjacent
   zones never blur — Nelson's 5 min is safe; clock drift (seconds/day) fits
   well inside it.

The only new logic is candidate generation + clustering + ranking. Confirmed
pairs are fed to the existing `build_calibration` (which already does median
outlier rejection and the pair-vs-TZ cross-check) and, when 2+ are confirmed
across the trip, its existing 2-pair interpolation yields drift correction for
free.

### Ranking the example pairs shown per cluster

Favor pairs that are easy to recognize and hard to fake: smallest tightness,
spread across the trip (not five frames from one minute), and — if a cheap
thumbnail similarity is available — visually corroborating (same scene). Do
**not** label each card with the offset it implies; that biases the eye.

## 3. The flow

1. **Lead with the strongest cluster.** "It looks like the *[camera]* clock was
   about **1h behind** your phone — do you recognize any of these as the same
   moment?" + 3–6 side-by-side `[camera | phone]` thumbnail cards.
2. **The 0-offset cluster is shown first** when it's the top pile, so a
   correctly-set camera is confirmed in one click (Nelson's original idea, as
   the common case).
3. **One click confirms** → the pair(s) become `CalibrationPair`s.
4. **"None of these / show another" →** reveal the next cluster(s); if the user
   recognizes nothing anywhere → fall back to the manual `sync_pair_picker`.
5. **Preview + undo before applying.** "Shifting 214 [camera] photos by +1h; 6
   move to a different day. Apply / Cancel." (The rail the bad correction
   lacked.)

## 4. Safeguards against a mis-confirm

- A confirmed pair that is an **outlier vs the cluster** warns before apply
  (reuse `build_calibration`'s median rejection + the pair-vs-TZ cross-check).
  A pair in *no* cluster is flagged.
- **Ambiguity is surfaced, not hidden:** two near-equal clusters are both
  shown so recognition disambiguates — the case pure statistics gets wrong.
- The preview/undo makes even a wrong confirm reversible, never a silently
  corrupted timeline.

## 5. Edge cases

- **No phone overlap** (phone barely used) → no proposal possible → manual
  pick, plus a hint to take a deliberate sync shot next trip.
- **Sparse overlap / no cluster** → manual pick.
- **Multiple cameras** → run per camera; the phone is the shared reference.
- **Drift** → one confirmed pair suffices for the discrete TZ; confirming a
  second pair late in the trip opts into the existing drift interpolation.

## 6. Two decisions (flagged for Nelson's veto)

1. **Auto-apply vs always-confirm.** Even when the top cluster is overwhelming,
   a *non-zero* correction still requires one recognized pair (never apply an
   hour shift on statistics alone); the *0* cluster allows a one-click "yes,
   the clock was right." — *decided this way pending veto.*
2. **Constant-offset vs per-day.** A recognized pair calibrates the camera for
   the **whole trip** (constant set-TZ — matches `clock_calibration`'s model).
   Per-day calibration (defending against the user changing the camera's clock
   mid-trip) is deferred; it can ride the same proposer later if a real need
   appears. — *decided constant pending veto.*

## 7. What this reuses vs adds

Reuses: `clock_calibration` (offset math, outlier rejection, cross-check,
drift), `snap_to_tz_offset` / `snap_disagreement`, `discrete_tz`,
`sync_pair_picker` (manual fallback), spec/57 §4.2 (apply). Adds: the candidate
generator + cluster + ranking (Qt-free, `core/`), and the recognition UI
(thumbnail-pair cards + preview/undo) that replaces the hand-pick as the
default entry point.
