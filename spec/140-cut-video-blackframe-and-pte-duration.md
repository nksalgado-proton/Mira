# 140 — Two video bugs: cut-play photo→video black frame · PTE videos won't play (Duration=0)

**Status: PROPOSED (Nelson 2026-06-23, both evidenced). (1) Playing a Cut in
Mira, the transition from a photo to a video flashes a **black frame** —
`CutPlayer._show_video` hides the photo and shows the `QVideoWidget` *before*
the first video frame flows, so the user sees the empty (black) sink until
pixels arrive. (2) In a generated PTE presentation **videos don't play at
all** even though the mp4s are exported — confirmed in
`…/salta_argentina/trip_long/trip_long.pte`: every `VideoClip` (`[Tracks]`),
every `:Video` slide object, **and** the `[Times]` timing carry
**`Duration=0`** (the show clocks each video as a 7000 ms photo). PTE can't
play a zero-length clip. Cause: `_cut_video_duration_ms` resolves duration by
name-matching the exported clip to a source item, which never matches
(exported clips are Mira-named `NNN_<date>_clipN.mp4`, and clip *segments*
don't equal any source video's length). Fix #1: hold the last image until the
first video frame (no-black-frame, like `PhotoViewport`). Fix #2: **ffprobe
the actual exported mp4** for its real duration and write it everywhere.
Touches `mira/ui/shared/cut_play.py` and the PTE generator
(`mira/ui/pages/share_cuts_page.py` / `mira/shared/pte_project.py`).**

## 1. Bug 1 — cut-play black frame on photo→video

`CutPlayer._show_video(path)` (cut_play.py ~591):

```python
self._photo.hide()                 # photo gone immediately
self._video_widget.show()          # empty/black sink shown
self._stack_layout.setCurrentWidget(self._video_widget)
```

The `QVideoWidget` paints **black** until the first decoded frame arrives, so
the swap shows black for a beat. `PhotoViewport` already solves this (its
`_on_video_frame` holds the poster until pixels flow).

**Fix:** keep the outgoing **photo visible** (showing the last frame, or a
poster) and **defer the swap** until the player's first valid frame:

- Arm + `play()` the video, but do **not** hide `_photo` / show the video
  widget yet.
- Connect the player's `videoSink().videoFrameChanged` to a one-shot handler:
  on the first **valid** frame, hide `_photo`, show + raise the video widget,
  set it current. Disconnect after the first frame.
- Guard the usual edge cases (clip fails to produce a frame → fall back to
  showing the widget after a short timeout so it never hangs on the photo).

## 2. Bug 2 — PTE videos won't play (Duration=0)

Measured in `trip_long.pte`: `VidClip_NN:VideoClip` → `Duration=0`; both
`:Video` slide objects → `Duration=0`; `[Times]` `opt_synchposN` increments by
the photo seconds (7000) for video slides too. The mp4s exist and the binding
(`ClipGUID` / `MasterID` / `StartSlideIdx` / `Picture=…mp4`) is otherwise
correct — only the duration is wrong, and a zero-length clip doesn't play.

Cause: `_cut_video_duration_ms` strips the `NNN_` prefix and matches the
exported clip name against `item.origin_relpath` names — which never match
(Mira-named clips; and a clip is a *segment*, not the whole source video),
so it returns **0**.

**Fix — probe the real duration from the exported file:**

- For each exported `.mp4` in the cut folder, **ffprobe** it (bundled
  ffmpeg/ffprobe — the same binary the export pipeline uses) for its duration
  in ms. Robust for both whole-video and clip-segment exports; the file is
  right there in the folder, so no fragile name-matching.
- Write that duration into **all three** places the generator emits:
  1. the `[Tracks]` `VideoClip` `Duration`,
  2. **both** `:Video` slide objects' `Duration`,
  3. the `[Times]` `opt_synchpos` for the video slide — cumulative **+= clip
     duration** (not `photo_s`), so the timeline reflects the real clip length.
- Replace `_cut_video_duration_ms`'s name-match with the probe. (Same
  fragile-name-match class as the spec/120 overlay fix — prefer reading the
  truth from the file/member over guessing by filename.) Fall back gracefully
  (a failed probe → log + skip that clip's special timing, but never write
  `Duration=0`; if truly unknown, use a sane minimum rather than 0).

## 3. Acceptance

- **Cut play:** a photo→video transition shows the photo until the video's
  first frame, then the video — **no black flash**. Same for video→video and
  video→photo (no regression).
- **PTE:** a regenerated `.pte` has the **real clip duration** on every
  `VideoClip`, both `:Video` objects, and the `[Times]` synchpos; opening it
  in PTE AV Studio **plays the videos** for their true length; the rest of the
  show timing is correct (videos no longer counted as 7s photos).
- No `Duration=0` is emitted for any present mp4.

## 4. Tests

- `tests/test_cut_play_video_no_blackframe.py` — `_show_video` does not hide
  the photo / show the video widget until the first `videoFrameChanged`; on
  the first valid frame the swap happens; a no-frame clip falls back after the
  timeout. (Offscreen-safe via a stub player/sink.)
- `tests/test_pte_video_duration.py` — the generator probes the exported mp4
  and writes the same non-zero ms into the `VideoClip` `Duration`, both
  `:Video` objects, and the `[Times]` synchpos (cumulative uses clip length,
  not photo seconds); a probe failure never yields `Duration=0`.
- Regress the existing PTE generation tests (photos-only unchanged).
