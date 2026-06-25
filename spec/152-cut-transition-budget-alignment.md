# 152 — Cut transitions: Mira/PTE timing alignment + smooth video→media swap

**Status: PROPOSED (Nelson 2026-06-25). Two related bugs share one fix:
(A) the in-app Cut rehearsal flashes black for ~1 s on every video →
media boundary because the QVideoWidget surface drops out before the
next entry's pixmap paints, and (B) the generated PTE show ends with
audio cut off because Mira's budget (`core/cut_budget.py`) and the
audio playlist built from it ignore the `transition_ms` that PTE
actually spends on every non-video slide's `[Times]` slot. The
proposal: treat the transition as a first-class duration unit, count
it in the budget on the same slides PTE counts it on, render it as
a crossfade in `cut_play.py`, and thread one shared value through to
PTE's generator so both surfaces clock the same total wall time.**

## 1. Why the budget and PTE disagree

After spec/150 §1 landed, the PTE generator times slides as:

| Slide kind | `[Times]` slot |
|---|---|
| photo / opener / separator | `photo_seconds * 1000 + transition_ms` |
| video | `clip_ms` (no transition added — dissolve overlaps the tail) |

`core/cut_budget.py::ShowTotals.seconds()` sums:

```python
(photo_count + separator_count) * photo_s + video_ms_total / 1000.0
```

with no `transition_ms` term anywhere. For a Cut with 4 photos + 1
video at `photo_s=6.0` and `transition_ms=2000`:

- Budget: `4 * 6 + clip` = `24 + clip` seconds.
- PTE shows: `4 * 8 + clip` = `32 + clip` seconds.
- Mismatch: `8 s` of unaccounted transition time.

The audio playlist is built to budget length
(`audio_library.build_playlist(tracks, totals.seconds(...))`), so PTE
runs the visuals for 8 s after the music ends. Exactly the user's
report.

## 2. Why the Mira rehearsal flashes black

[`cut_play._show_pixmap`](mira/ui/shared/cut_play.py) handles the
video → photo (or opener / separator) swap as:

```python
self._reset_video_swap_state()
if self._video_widget is not None:
    self._video_widget.hide()
self._photo.show()
...
self._stack_layout.setCurrentWidget(self._photo)
```

The `_video_widget.hide()` + new-pixmap path leaves a frame or two
where the video surface has dropped out but the photo hasn't painted
yet. On the WMF / default Qt6 backend on Windows that gap reads as a
~1 s black flash — same root family as spec/140 §1's photo→video
black frame (which is already fixed there with the first-frame
watchdog). A crossfade transition closes this gap by painting the
outgoing frame over the incoming widget throughout the swap.

## 3. The fix — one design, two phases

The single design: **transitions are part of the show's clock**. Mira
Play and PTE both spend `transition_ms` on every slide that contributes
one (every photo / opener / separator). Video slides are unchanged
from spec/150 §1 — the dissolve eats the head of the NEXT slide's
transition window, not extra time on the video. The budget reflects
this; the audio playlist matches.

### Phase 1 — count the transition in the budget + audio + PTE (small)

No rendering changes. Just align the numbers.

- `ShowTotals.seconds(photo_s, transition_s)` adds
  `(photo_count + separator_count + opener_count) * transition_s` to
  the running total. `opener_count` becomes a field (always `0` or
  `1`, set by `cut_show_totals`).
- Cut surfaces that call `seconds(photo_s)` switch to
  `seconds(photo_s, transition_s)` — both `share_cuts_page._on_play_cut`
  and the generator caller in `share_cuts_page._export_cut`.
- `audio_library.build_playlist(tracks, target_seconds)` keeps its
  shape; the caller passes the new budget value.
- `pte_project.generate(..., transition_ms=...)` already takes the
  parameter. Both export callers
  (`share_cuts_page`, `library_page`) thread the Cut's transition
  value through instead of falling back to `DEFAULT_TRANSITION_MS`.

Phase 1 alone: the audio finishes exactly when the PTE show ends. No
more cut-off track. Mira Play still has the black blink, but it's
visually consistent with the new (correct) total length.

### Phase 2 — render the transition in Mira Play (the harder part)

A crossfade transition between every consecutive pair of entries.
Closes the black blink and makes the rehearsal feel like the PTE
show.

Concrete plumbing:

- A `_TransitionOverlay` widget sits above the photo / video stack.
  At swap time the OUTGOING entry's pixel content is captured to a
  `QPixmap`:
  - photo / sep / opener — already a `QPixmap`, reuse it.
  - video — read `_player.videoSink().videoFrame()` and convert to
    `QImage` → `QPixmap` JUST before EndOfMedia (or the spec/150 §2
    end watchdog) fires. Cache it on the entry while the next entry
    sets up.
- `_show_index` for the NEW entry:
  1. Set up the incoming widget (photo painted, or video `setSource` +
     `play`) UNDERNEATH the overlay.
  2. Show the overlay with the captured pixmap at opacity 1.0.
  3. Animate `QGraphicsOpacityEffect.setOpacity()` 1.0 → 0.0 over
     `transition_ms`, scaled by the spec/145 rehearsal rate. (Photos
     run at 1×; videos already inherit speed_rate so the dissolve
     timing should follow.)
  4. On animation finish: hide the overlay and clear its pixmap.
- The audio (cut music) keeps playing through transitions — no
  audio crossfade. Matches PTE behaviour.

Edge cases the spec must pin:

- **Pause mid-transition** — freeze the opacity at its current
  value, resume from there.
- **Scrub past a transition** — skip the overlay; show the new entry
  fully. The transition is decorative, not load-bearing.
- **Video last-frame capture failed** (codec / Qt backend hiccup) —
  fall back to a fade-to-black-then-fade-in transition, half the
  duration each. Still smoother than a hard cut.
- **`transition_ms == 0`** — no overlay, no animation; instant swap
  (the legacy behaviour, opt-in via the setting).

### Phase 1 ships independently of Phase 2

If Phase 2's frame-capture path turns out flaky on some Qt backend,
Phase 1 has already fixed the audio mismatch. Phase 2 is a pure
visual improvement on top — never a correctness regression.

## 4. The setting

A new application setting (spec/138 shape):

- `default_transition_ms: int = 2000` — system default. Matches the
  current `pte_project.DEFAULT_TRANSITION_MS`.
- Editable in the New / Edit Cut dialog (spec/109 §X family) next to
  `photo_s`. Per-Cut value persists on the Cut row alongside
  `photo_s`, `target_s`, `max_s`, etc. Migration: existing Cuts pick
  up the application default at read time.

The same value drives:

1. `cut_budget.seconds(...)` — the total the user sees in the
   green/amber/red zone and the duration the audio is built to.
2. The Cut's `[Times]` slot math in `pte_project.generate(...)`.
3. The `_TransitionOverlay` animation duration in `cut_play`.

When the setting is `0`: budget reverts to pre-152 math, PTE
slides have no extra transition slot, Mira Play swaps instantly. The
"I want everything to feel snappy" path.

## 5. Acceptance

- For any Cut, `ShowTotals.seconds(photo_s, transition_s)` equals
  the `opt_synchpos<N>` of the LAST slide in the generated PTE's
  `[Times]` (within 1 ms rounding). Pin this as a generator test.
- For any Cut, the audio playlist built from
  `build_playlist(tracks, totals.seconds(...))` runs the full PTE
  show length. (Today it cuts off `(photo + sep + opener) *
  transition_s` early.)
- Mira Play's transition from a video clip to the next entry has no
  black flash on the WMF / Qt6 Windows backend. Visual smoke:
  capture frames around the boundary; assert no consecutive
  > 95 %-black frames where the previous frame had real content.
- `transition_ms = 0` produces a Cut whose budget, PTE timing, and
  Mira Play behaviour all match the pre-152 hard-cut design.
- The spec/150 §1 contract (video slot in `[Times]` is `clip_ms`, NOT
  `clip_ms + transition_ms`) is unchanged — the existing
  `tests/test_pte_video_duration.py` /
  `tests/test_pte_project.py::test_times_block_cumulative_with_clip_length_videos`
  assertions keep their values.

## 6. Tests

- `cut_budget`: parametric — given `(P, S, V_total, photo_s,
  transition_s)`, `seconds()` returns
  `(P + S + opener) * (photo_s + transition_s) + V_total / 1000`.
- `pte_project`: with `transition_ms=2000` and a 4-photo + 1-video
  Cut, the final `opt_synchpos` matches the budget formula above.
- `cut_play` (Phase 2): a stub-player harness that drives a video →
  photo transition, asserts `_TransitionOverlay.isVisible()` is True
  during the animation and False after, AND that the overlay's
  pixmap is non-null (a real captured frame, not a fallback to
  black).
- `cut_play` (Phase 2 fallback): stub the videoSink to return an
  invalid frame; assert the fade-to-black variant kicks in.

## 7. Non-goals

- No change to the spec/150 fixes (§1 video-no-padding, §2 end
  watchdog, §3 `-shortest`, §6 fps probe). They remain the
  per-clip correctness contract.
- No audio crossfade between music tracks. The music playlist
  continues at unity volume across transitions; visual transitions
  are decorative.
- No "different transitions per cut" (wipe / push / iris). The
  dissolve / crossfade is the only style spec/107's skeleton uses
  and the only style this spec contemplates. A future spec can add
  effect parity.
- No transition between the OPENER and the first content slide that
  differs from any other transition. Same crossfade, same
  `transition_ms`. The opener is structurally a separator-class
  slide and behaves identically.
