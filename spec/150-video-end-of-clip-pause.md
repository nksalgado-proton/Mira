# 150 — The end-of-clip pause: PTE freezes the last frame · cut-play waits on EndOfMedia · exported mp4 carries an audio tail · encoder mislabels source fps

**Status: PROPOSED (Nelson 2026-06-25). When a video plays inside a Cut — both
the in-app rehearsal AND a generated PTE show — the clip's last frame freezes
for a beat before the next slide appears. This is NOT the spec/144 wrong-length
timer (that's fixed) nor the spec/140 `Duration=0` bug (also fixed). It is
THREE separate causes that happen to share one symptom:**

**(A) PTE adds `transition_ms` (default 2000 ms) of dead hold to every video
slide's `[Times]` slot while the `:Video` object only plays for `clip_ms`, so
PTE holds the frozen last frame for ~2 s before the dissolve — deterministic,
"always," and the dominant cause. (B) The in-app rehearsal hard-cuts on
`QMediaPlayer.EndOfMedia`, which on Windows lags the last visible frame by a
few hundred ms to ~1 s, holding the frame during the gap. (C) The exported
mp4 muxes AAC with no `-shortest`, so the container/audio runs tens of ms past
the last video frame — a small freeze both surfaces inherit. Fix all three.
Touches `mira/shared/pte_project.py`, `mira/ui/shared/cut_play.py`,
`core/video_export_run.py`, and `tests/test_pte_video_duration.py`.**

## 1. Cause A — PTE video slides hold a frozen frame (primary)

`mira/shared/pte_project.py::generate()` times slides as:

```python
photo_ms = int(round(photo_seconds * 1000)) + int(transition_ms)
...
if m.kind == "video":
    clip_ms = _safe_video_duration_ms(int(m.duration_ms), path=m.path)
    body = _set_slide_video_paths(body, path_s, clip_ms)      # :Video Duration = clip_ms
    slide_durations_ms.append(clip_ms + int(transition_ms))   # [Times] slot = clip_ms + 2000
    video_clips.append(_build_video_clip(... duration_ms=clip_ms ...))  # VideoClip = clip_ms
```

So a video slide's three duration sites disagree:

| Site | Value |
|---|---|
| `:Video` object `Duration=` | `clip_ms` |
| `[Tracks]` `VideoClip` `Duration=` | `clip_ms` |
| `[Times]` slot (slide on-screen time) | `clip_ms + transition_ms` |

`DEFAULT_TRANSITION_MS = 2000`, and both export callers
(`mira/ui/pages/share_cuts_page.py`, `mira/ui/pages/library_page.py`) invoke
`generate_into_folder` WITHOUT overriding it. In PTE the `[Times]` slot is the
slide's on-screen time: the clip plays for `clip_ms`, then the slide holds the
**frozen last frame** for `transition_ms` before the next slide's dissolve
starts. That fixed 2 s hold is the "long pause," identical on every clip.

For photos the same `+ transition_ms` is invisible (a static frame held longer
looks identical), which is why the symptom is video-only.

### This also contradicts the budget + spec/61

`core/cut_budget.py::ShowTotals.seconds()` times videos at true duration with
**no** transition (`+ self.video_ms_total / 1000.0`), and spec/61 §Play says
"clips at their TRUE length." The PTE video `[Times]` slot of
`clip_ms + transition_ms` is out of step with the projected show length AND
the in-app rehearsal.

### The existing test masks it

`tests/test_pte_video_duration.py::test_video_duration_lands_in_times_cumulative`
asserts the video slot advances `[Times]` by exactly `clip_ms` — but it calls
`generate(..., transition_ms=0)`, so it never exercises the production default
of 2000. It passes today while the freeze ships.

### Fix A — no dead hold after a video

In `generate()`, for video members append the clip's own length to
`slide_durations_ms` with **no transition added**:

```python
slide_durations_ms.append(clip_ms)          # was: clip_ms + int(transition_ms)
```

The incoming slide's dissolve then overlaps the clip's tail (motion keeps
running through the transition) instead of dissolving a frozen frame. This
also realigns `[Times]` with `cut_budget` and the rehearsal.

(Alternative considered — `max(_MIN_VIDEO_DURATION_MS, clip_ms - transition_ms)`
so the dissolve starts before the clip ends. Rejected as the default: it
silently eats the last ~2 s of every clip. Option A keeps every frame and
still removes the freeze.)

## 2. Cause B — cut-play waits on a laggy EndOfMedia

`mira/ui/shared/cut_play.py` advances a video entry only on the player signal:

```python
def _on_video_status(self, status):
    if status == QMediaPlayer.MediaStatus.EndOfMedia:
        self.advance()
    elif status == QMediaPlayer.MediaStatus.InvalidMedia:
        self.advance()
```

This is correct per spec/144 (it replaced the wrong-length precomputed timer).
But it now depends on `QMediaPlayer` firing `EndOfMedia` promptly. On Windows
(WMF / default Qt6 backend) that signal commonly lags the last visible frame
by a few hundred ms up to ~1 s while the player drains audio and flips
`playbackState`; the last frame stays on the `QVideoWidget` during the gap.
Shorter and less consistent than cause A, same visible shape.

### Fix B — a last-frame watchdog (symmetric to the first-frame one)

The file already has a first-frame watchdog (`_VIDEO_SWAP_TIMEOUT_MS`,
`_force_video_swap`). Add the symmetric guard for the clip's END:

- When a clip starts in `_show_video`, arm a single-shot timer for
  `duration_ms + slack` (e.g. `_VIDEO_END_SLACK_MS = 150`), using the entry's
  segment `duration_ms` (the spec/144 true segment length; skip arming when
  it's 0/unknown and rely on EndOfMedia alone).
- Keep `EndOfMedia` as the primary advance path. The watchdog only fires if
  EndOfMedia hasn't already advanced — i.e. it catches the lag.
- The watchdog and the EndOfMedia path must both funnel through one
  idempotent advance so a clip can't double-advance (e.g. a `_video_advanced`
  guard reset per entry, or check the index hasn't moved).
- Compound with the spec/145 rehearsal speed override: arm the watchdog for
  `duration_ms / video_rate + slack` so a 2× clip's backstop fires at the
  right wall-clock time. Re-arm on a live rate change (mirrors
  `_apply_video_rate`).
- Tear the timer down in `_reset_video_swap_state`/`_teardown_media` paths and
  on pause (re-arm with remaining time on resume, or simplest: stop on pause
  and let EndOfMedia carry resumed playback, re-arming on next `_show_video`).

## 3. Cause C — exported mp4 audio tail

`core/video_export_run.py` `_start_encode()` and `_run_ffmpeg_only()` mux AAC
with **no `-shortest`** and no `apad`/`-async` alignment:

```python
cmd += ["-c:a", "aac", "-b:a", "192k"]
cmd += ["-movflags", "+faststart", str(output_path)]
```

AAC priming/padding plus frame-boundary rounding means the audio stream (and
the container duration) runs a few tens of ms past the last video frame. Any
player — the rehearsal and PTE — holds the last video frame until the longer
stream ends. Small (tens of ms), so a contributor not the main event, but it
lives in the exported bytes both surfaces share, so fix it at the source.

### Fix C — end on the video stream

Add `-shortest` to BOTH encode commands (keep `-map 0:v` first so video is the
authority where mapped). The muxed clip then ends with the last video frame.
Verify the encode still completes for clips with no audio (`-an` path is
unaffected) and for the numpy-pipe path where video is `pipe:0` and audio is a
second `-i`.

## 4. Order, scope, non-goals

Independent fixes; land in this order (biggest, most deterministic first):

1. **Fix A** (PTE `[Times]`) — one line + test update; removes the ~2 s freeze
   in exported shows.
2. **Fix B** (cut-play watchdog) — removes the rehearsal pause; matches an
   existing idiom in the same file.
3. **Fix C** (`-shortest`) — cleans the shared artifact.

Non-goals: no change to transitions for PHOTO slides; no change to the
spec/144 segment-duration source of truth (this spec consumes it); no new
PTE transition model (the dissolve effect baked into the skeleton is
unchanged — we only stop padding video slides with hold time).

## 5. Tests

- **A:** extend `tests/test_pte_video_duration.py` — a new case calling
  `generate(..., transition_ms=2000)` asserting a video slide's `[Times]`
  cumulative advances by exactly `clip_ms` (photo cumulative + `clip_ms`), NOT
  `clip_ms + 2000`. Keep the existing photo-tier transition behaviour pinned.
- **B:** in the cut-play suite (mirror `tests/test_cut_play_video_advance.py`'s
  stub-player harness): arming the watchdog on a video entry, firing it
  advances the show when EndOfMedia hasn't; EndOfMedia arriving first does NOT
  double-advance; the watchdog is torn down on teardown/pause; rate override
  scales the armed interval.
- **C:** unit-assert both encode command builders include `-shortest`; if an
  integration clip is available, `ffprobe` the exported mp4 and assert the
  audio and video stream durations match within one frame.

## 6. Addendum — Cause D (encoder mislabels source fps)

**Discovered 2026-06-25 after A/B/C landed: the user re-played exported
clips directly in Windows Media Player and saw a multi-second image
freeze before the file ends. The source clips played cleanly; only
Mira's re-encoded versions had the freeze, and only on SOME clips.
That points at the export pipeline itself, not the container tail
that C addressed.**

`core/edit_export_walker.py:154` hardcodes `src_fps = 30.0` and leaves
a comment promising the runner re-probes at render time:

```python
# Source fps — probe_video would touch ffmpeg, deliberately
# NOT done here (the walker is gateway-only and called inside
# the UI thread); the runner re-probes at render time, the
# plan's src_fps is a hint only.
src_fps = 30.0
```

`core/video_export_run.py:export_processed_clip` DOES call
`probe_video(video_path)` (line ~101), but only consumes
`display_width` / `display_height`. `meta.fps` is thrown away. The
encoder's rawvideo input then runs at the unverified hint:

```python
"-s", f"{out_w}x{out_h}", "-r", f"{plan.src_fps:g}",
"-i", "pipe:0",
```

For any source not at exactly 30 fps the decoded-frame count and the
encoder's assigned PTS don't agree:

| Source fps | Decoded frames per dur_s | Encoder writes them at | Output video duration |
|---|---|---|---|
| 24 | dur_s × 24 | 30 fps | dur_s × 0.80 |
| 25 (PAL) | dur_s × 25 | 30 fps | dur_s × 0.833 |
| 29.97 (NTSC) | dur_s × 29.97 | 30 fps | dur_s × 0.999 (sub-frame) |
| 30 | dur_s × 30 | 30 fps | dur_s × 1.0 (no bug) |
| 60 | dur_s × 60 | 30 fps | dur_s × 2.0 (slow motion, audio cut short) |

A 24 fps source produces an output video stream that's 20 % shorter
than the trimmed audio. WITHOUT the §3 `-shortest` fix the muxed clip
holds the last video frame for that 20 % gap → the freeze the user
reported. WITH `-shortest` the muxed clip ends with the (shortened)
video and 20 % of the audio is lost — a different bug, but still
wrong. A 60 fps source comes out at half speed; once `-shortest`
lands it loses the latter half of the visible action instead. Both
need cause D fixed before §3's `-shortest` lands cleanly.

### Why only some clips show it

This only hits the **numpy-pipe path** (`_run_pipe` →
`_start_encode`). The fast path (`_run_ffmpeg_only`) is a single
ffmpeg invocation with no `-r` and inherits the source's natural rate
through ffmpeg's stream-copy heuristics, so duration is preserved
regardless of camera fps. The numpy-pipe path engages whenever a
clip has any colour or crop / Box-Rotation adjustment
(`plan.has_colour or plan.has_crop` in `export_processed_clip`).
"Sometimes but not all clips" matches: graded clips hit the bug,
trim-only / mute-only / speed-only / stabilise-only clips don't.

### Fix D — use the probed source fps

`export_processed_clip` already probes the source for dimensions. After
the probe, replace the plan's `src_fps` with the probed value (falling
back to the hint when the probe couldn't read a rate). One line via
`dataclasses.replace`:

```python
meta = probe_video(video_path)
...
if meta.fps > 0:
    plan = dataclasses.replace(plan, src_fps=float(meta.fps))
```

The walker's hardcoded 30.0 stays as the fall-back hint — it's only
used when `probe_video` returns `fps == 0` (parse failed). The plan
remains frozen everywhere else; we just rebind to a corrected copy
once.

### Tests

- 24 fps source via `_make_test_video(... fps=24)`, exported through
  the numpy-pipe path (a tiny `Params(exposure=0.1)` trips
  `has_colour`). Assert output duration ≈ source duration. Before the
  fix this came out ~80 % of source duration (which under §3's
  `-shortest` also shortens the audio — strictly worse, hence why D
  needs to land alongside C).
- Same for 60 fps to pin the over-rate side: output ≈ source, not 2 ×.
- Add a `src_fps` probe assertion: after `probe_video` returns a
  non-zero fps the runner's `plan.src_fps` reflects it (i.e., the
  walker's hint is overridden).
