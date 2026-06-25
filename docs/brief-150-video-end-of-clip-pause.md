# Coding-agent brief — spec/150 (end-of-clip pause)

**Goal:** kill the freeze on the last frame of every video, in both the in-app
Cut rehearsal and generated PTE shows. Governing spec: `spec/150-video-end-of-clip-pause.md`
(read it first; it has the root-cause analysis and code references). Related
prior work: spec/140, spec/144, spec/145.

**Read before coding:** `spec/00-charter.md`, `spec/150`, then the three target
modules. Three independent fixes — land them as separate commits in order.

---

## Task 1 — PTE: stop padding video slides with hold time (PRIMARY)

`mira/shared/pte_project.py::generate()`. In the `m.kind == "video"` branch,
change the `[Times]` slot from `clip_ms + int(transition_ms)` to `clip_ms`
(no transition added). Leave the photo branch, the `:Video`/`VideoClip`
`Duration=` sites, and the dissolve effect untouched.

Test: extend `tests/test_pte_video_duration.py` with a case at the production
default `generate(..., transition_ms=2000)` asserting a video slide's
`[Times]` cumulative advances by exactly `clip_ms` (i.e. prior photo cumulative
+ `clip_ms`, NOT `+2000`). Don't weaken the existing transition=0 tests; keep
photo-tier transition behaviour pinned.

Done when: a video slide's on-screen time equals the clip length at any
`transition_ms`, and `[Times]` agrees with `core/cut_budget.ShowTotals`.

## Task 2 — cut-play: last-frame watchdog

`mira/ui/shared/cut_play.py`. Keep `EndOfMedia` as the primary advance. Add a
single-shot backstop timer (call it `_VIDEO_END_SLACK_MS = 150`) armed in
`_show_video` for the entry's segment `duration_ms + slack`, scaled by the
spec/145 `_video_rate` (`duration_ms / video_rate + slack`). On fire, advance
IF EndOfMedia hasn't already. Make advance idempotent so the two paths can't
double-advance (guard per entry). Skip arming when `duration_ms` is 0/unknown
(rely on EndOfMedia). Re-arm on live rate change; tear down in
`_reset_video_swap_state`/`_teardown_media`; handle pause/resume sanely.

Follow the existing first-frame watchdog idiom (`_VIDEO_SWAP_TIMEOUT_MS`,
`_video_swap_timer`, `_force_video_swap`, `_reset_video_swap_state`) — this is
the symmetric end-of-clip guard.

Test: mirror `tests/test_cut_play_video_advance.py`'s stub-player harness —
watchdog fires → advances when EndOfMedia is silent; EndOfMedia first → no
double-advance; teardown stops the timer; rate override scales the interval.

## Task 3 — export: end clips on the video stream

`core/video_export_run.py`. Add `-shortest` to both encode command builders
(`_start_encode` and `_run_ffmpeg_only`). Keep video mapped first. Confirm the
no-audio (`-an`) path and the numpy-pipe path (video `pipe:0` + 2nd `-i` audio)
both still build and run.

Test: assert both builders emit `-shortest`; if a real clip fixture exists,
`ffprobe` the output and assert audio/video stream durations match within one
frame.

---

## Constraints (charter)

- `core/` stays Qt-free; `mira/ui/` may import `core`/`gateway`, never the
  reverse.
- No inline `setStyleSheet` in widget modules (tests/test_no_inline_qss guard)
  — Task 2 adds logic only, no styling.
- No network, no telemetry.
- Update the spec with the code if anything in spec/150 turns out wrong —
  spec first, then code.

## Verify before done

- `verify.bat tests\test_pte_video_duration.py`
- `verify.bat tests\test_cut_play_video_advance.py` (+ the new watchdog test)
- full `verify.bat`
- Manual: generate a `.pte` from a Cut with a clip, open in PTE — no freeze at
  clip end; rehearse the same Cut in-app on Windows — advance within ~150 ms of
  the visible end.

## Out of scope

Photo-slide transition behaviour; the spec/144 segment-duration source; any new
PTE transition/effect model. Don't refactor the PTE generator beyond the one
line; don't touch the encoder ladder.
