# 144 — Clip segments carry their TRUE duration (fix budget, cut-play end-pause, PTE timing)

**Status: PROPOSED (Nelson 2026-06-23). Three Cut-video symptoms share one
root cause: a clip member's duration is taken from the **whole source
video**, not the **clip segment**. `cut_session.files_from_lineage`
(`cut_session.py:79`) sets `SessionFile.duration_ms = src.duration_ms` — the
source item's full length (or 0 when unprobed). Cut members are *segments*
(`…_clip1.mp4`), so this is wrong everywhere it's used: (2) the **budget**
undercounts video (showed ~25 min for a 1 h+ show); (3) the **Mira cut-play**
timer waits the wrong length — the short clip plays, then the timer holds the
last frame for the remainder ("pauses as if a slide"; sometimes black on
entry); (4) the same wrong duration feeds **PTE** `[Times]`. Fix: give each
clip member its **real segment duration** (probe the exported clip, or carry
the segment length recorded at export), use it for budget + play + PTE, and
make cut-play **advance a video on its actual end-of-media**, not a
precomputed timer. Touches `mira/shared/cut_session.py`,
`core/cut_budget` path (`event_gateway` totals), `mira/ui/shared/cut_play.py`,
and the PTE generator (aligns with spec/140).**

## 1. Root cause

`SessionFile.duration_ms = int(src.duration_ms or 0) if src.kind=='video'`
— `src` is the **source video item**; its `duration_ms` is the whole video
(and 0 when never probed). A Cut clip is a marker-partition **segment** of
that video, with its own much shorter length. Every consumer of
`duration_ms` then gets the wrong value:

- **Budget** (`event_gateway` `ShowTotals.video_ms_total`, summing
  `si.duration_ms`) — undercounts (0 or whole-video, not segment).
- **Cut play** (`cut_play` `_durations[i]` uses `SessionFile.duration_ms`)
  — the timer length ≠ the clip's real playback length → end-pause / black.
- **PTE** (`[Times]`, `VideoClip Duration`) — wrong slide length (spec/140
  fixes PTE by probing the file; this generalises the truth source).

## 2. Fix — one true clip duration, used everywhere

### A. Carry the segment's real duration
Populate `SessionFile.duration_ms` (and the lineage/budget) with the **clip
segment's actual length**, not `src.duration_ms`:

- **Preferred:** record the segment duration at **export time** (the
  marker-partition in/out is known — `out_ms − in_ms`) onto the clip's
  lineage row, and read that.
- **Robust fallback / verification:** **ffprobe the exported clip mp4** (it's
  the segment; same probe spec/140 uses). Either way, never use the whole
  source `duration_ms` for a clip member.

### B. Budget counts real clip time
`ShowTotals.video_ms_total` sums the **segment** durations → the budget
reflects the true show length (the 1 h+, not 25 min).

### C. Cut play advances on actual end-of-media
In `cut_play`, a **video** entry must not rely on a precomputed
`_durations[i]` timer that can over/under-run the real clip:

- Drive the clip with the player; **advance to the next entry on the
  player's `EndOfMedia`** (mediaStatus) — not a fixed timer. The scrubber's
  duration table still uses the real segment length for layout, but the
  **advance** is event-driven, so there's no hold-the-last-frame pause and no
  early cut-off.
- Keep the spec/140 entry fix (hold the previous image until the first video
  frame) so there's no black on entry. Net: photo→video→photo transitions
  are tight.

### D. PTE timing
With the real segment duration (A), the PTE `VideoClip Duration` + both
`:Video` objects + `[Times]` synchpos are the clip's true length (spec/140) —
no end-pause in PTE either.

## 3. Acceptance

- The Cut budget includes video at its **real** (segment) length — a show
  with ~35 min of clips reads ~1 h, not 25 min.
- Mira cut play runs each clip for exactly its length and advances **the
  instant the video ends** — no last-frame pause, no early cut, no entry
  black (with spec/140).
- The regenerated PTE plays each clip for its true length with no end-pause.
- Photos and separators are unaffected.

## 4. Tests

- `tests/test_clip_segment_duration.py` — `files_from_lineage` gives a clip
  member its **segment** duration (not the source video's); a source with a
  long `duration_ms` but a short segment yields the short value; probe
  fallback agrees within tolerance.
- `tests/test_cut_budget_includes_video.py` — `video_ms_total` sums segment
  durations; a cut with N clips of known length budgets correctly (regression
  for the 25-min bug).
- `tests/test_cut_play_video_advance.py` — a video entry advances on
  `EndOfMedia` (stubbed player), not on a fixed timer; no extra hold after the
  clip ends; photo entries still use the timer.
- Align with spec/140's PTE duration tests (shared truth source).
