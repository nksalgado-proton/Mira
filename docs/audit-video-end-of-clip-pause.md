# Audit — the "long pause at the end of a video"

Scope: how Mira times videos when (A) played in-app during a Cut rehearsal,
and (B) exported into a `.pte` project for PTE AV Studio. The reported
symptom — the last frame of every clip freezes for a beat before the next
slide appears — has **two independent causes, one per surface**, plus a
**shared contributor** baked into the exported file itself.

---

## A. PTE export — deterministic ~2 s freeze (the primary culprit)

**File:** `mira/shared/pte_project.py`, `generate()`.

The generator times slides like this:

```python
photo_ms = int(round(photo_seconds * 1000)) + int(transition_ms)
...
if m.kind == "video":
    clip_ms = _safe_video_duration_ms(int(m.duration_ms), path=m.path)
    body = _set_slide_video_paths(body, path_s, clip_ms)   # :Video Duration = clip_ms
    slide_durations_ms.append(clip_ms + int(transition_ms))   # [Times] slot = clip_ms + 2000
    video_clips.append(_build_video_clip(... duration_ms=clip_ms ...))  # VideoClip = clip_ms
```

So for a video slide:

| Site | Value |
|---|---|
| `:Video` object `Duration=` | `clip_ms` |
| `[Tracks]` `VideoClip` `Duration=` | `clip_ms` |
| `[Times]` slot (how long the slide is on screen) | `clip_ms + transition_ms` |

`DEFAULT_TRANSITION_MS = 2000`, and both export pages
(`share_cuts_page`, `library_page`) call `generate_into_folder` without
overriding it, so **every exported clip gets the default 2000 ms added**.

In PTE the slide's on-screen time is the `[Times]` slot. The video plays
for `clip_ms`, then the slide stays up for another `transition_ms`
holding the **frozen last frame** before the dissolve to the next slide
begins. That extra hold is the "long pause," and because it is a fixed
constant it is the same on every clip — matching "always."

For photos this added time is invisible (a static image held 2 s longer
looks identical), which is why the bug only shows on video.

### Why this is also internally inconsistent

The budget model already says videos cost their *true* duration with no
transition:

```python
# core/cut_budget.py — ShowTotals.seconds()
return (self.photo_count + self.separator_count) * photo_s \
    + self.video_ms_total / 1000.0      # videos at true duration, no transition
```

and spec/61 §“Play” states "clips at their TRUE length." The PTE
`[Times]` slot for video (`clip_ms + transition_ms`) contradicts both —
the projected show length, the in-app rehearsal, and the exported `.pte`
disagree by `transition_ms` per clip.

### Pinned by a test (so the fix must update it)

`tests/test_pte_video_duration.py::test_video_duration_lands_in_times_cumulative`
asserts the video slot advances `[Times]` by exactly `clip_ms` — but only
because the test calls `generate(..., transition_ms=0)`. It does **not**
cover the production default of 2000, so it passes today while the freeze
ships.

### Fix options (PTE side)

1. **Recommended — don't add hold time after a video.**
   `slide_durations_ms.append(clip_ms)` for video members. The incoming
   slide's dissolve then overlaps the tail of the clip (motion keeps
   running through the transition) instead of dissolving a frozen frame.
   This also makes `[Times]` agree with `cut_budget` and the rehearsal.
2. **If you want the dissolve to start *before* the clip ends**, use
   `max(_MIN_VIDEO_DURATION_MS, clip_ms - transition_ms)`. This trims the
   last `transition_ms` of visible clip into the crossfade — smoother, at
   the cost of the final ~2 s of the clip.

Either removes the freeze; option 1 is the minimal, surprise-free change.
After changing, extend the test to assert the production-default
(`transition_ms=2000`) video slot equals `clip_ms`, not `clip_ms + 2000`.

---

## B. In-app Cut rehearsal — EndOfMedia latency (a different cause)

**File:** `mira/ui/shared/cut_play.py`.

The rehearsal does **not** add a transition (it hard-cuts between
entries), so cause A does not apply here. Advance is event-driven:

```python
def _on_video_status(self, status):
    if status == QMediaPlayer.MediaStatus.EndOfMedia:
        self.advance()
    elif status == QMediaPlayer.MediaStatus.InvalidMedia:
        self.advance()
```

This is the spec/144 design — it correctly removed the old precomputed
timer that held the last frame for the wrong (source-length) duration.
But it now depends entirely on `QMediaPlayer` emitting `EndOfMedia`
**promptly**. On Windows (the WMF / default Qt6 backend) that signal
commonly lags the last visible frame by a few hundred ms up to ~1 s
while the player drains its audio buffer and flips `playbackState`. The
last frame stays on the `QVideoWidget` during that gap → the in-app
pause.

It is shorter and less consistent than the PTE freeze (which is exactly
why the two surfaces "feel" different), but it has the same visible
shape.

### Fix options (in-app side)

1. **Backstop watchdog (recommended).** When a clip starts, arm a
   single-shot timer for `duration_ms + small_slack` (e.g. +150 ms). If
   `EndOfMedia` hasn't advanced by then, advance anyway. Mirrors the
   existing `_VIDEO_SWAP_TIMEOUT_MS` watchdog already used for the
   *first* frame — this is the symmetric guard for the *last* frame.
   Keep `EndOfMedia` as the primary path; the timer only catches the lag.
2. **Position-based early advance.** Connect `positionChanged` and
   advance once `position() >= duration() - one_frame`. More precise but
   needs a reliable `duration()` from the player.

Option 1 is consistent with the file's existing watchdog idiom and needs
no reliable backend duration.

---

## C. Shared contributor — the exported MP4's audio tail

**File:** `core/video_export_run.py`, `_start_encode()` / `_run_ffmpeg_only()`.

Both encode paths mux a separately-trimmed AAC audio stream and use
**no `-shortest`** and no `apad`/`-async` alignment:

```python
cmd += ["-c:a", "aac", "-b:a", "192k"]
cmd += ["-movflags", "+faststart", str(output_path)]
```

AAC adds encoder priming/padding and ends on a frame boundary, so the
audio stream (and therefore the MP4 container duration) typically runs a
few tens of ms **past the last video frame**. Any player — the in-app
rehearsal *and* PTE — holds the last video frame until the longer stream
ends. This is small (tens of ms, not seconds), so it is a *contributor*,
not the main event, but it sits underneath both A and B and is worth
removing because it affects the exported bytes themselves (the one
artifact both surfaces share).

### Fix option (encoder side)

Add `-shortest` to the encode command (and, if you want video to be the
authority, keep video first in the map) so the muxed clip ends with the
video stream. Optionally probe the *exported* clip's true video-stream
duration and feed *that* into PTE/rehearsal rather than the container
duration.

---

## Recommended order of work

1. **PTE `[Times]` for video** (cause A) — biggest, deterministic win;
   one line plus a test update. Removes the ~2 s freeze in exported
   shows.
2. **In-app last-frame watchdog** (cause B) — removes the rehearsal
   pause; small, matches an existing idiom in the same file.
3. **`-shortest` on export** (cause C) — cleans the shared artifact so
   neither surface inherits an audio tail; verify against a real clip
   with a longer audio stream.

## Suggested verification

- Generate a `.pte` from a Cut containing a known-length clip; assert the
  `[Times]` `opt_synchpos` for the video slide equals the photo cumulative
  + `clip_ms` (no `+2000`) at the production transition default.
- Export a clip whose source has video and audio of different lengths;
  `ffprobe` the stream durations and confirm they match within one frame.
- Rehearse a short clip in-app on Windows and confirm advance happens
  within ~150 ms of the visible end (watchdog fires if EndOfMedia lags).
