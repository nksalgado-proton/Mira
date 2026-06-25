# 145 — Cut rehearsal: a video-speed override (like the per-slide-seconds control)

**Status: PROPOSED (Nelson 2026-06-23). The Cut player lets the user tune
the photo seconds-per-slide live; there's no equivalent for **video speed**.
Add a rehearsal-time **video-speed override** to the Cut player. Background:
the per-clip speed is **baked at export** — `core/video_export_run.py:312`
applies `setpts=PTS/{plan.speed}` in ffmpeg, so the exported mp4's speed is
fixed in the bytes. But playback rate is a separate **runtime** knob, so Mira
can multiply a rehearsal speed on top via `QMediaPlayer.setPlaybackRate`
without re-encoding. Touches `mira/ui/shared/cut_play.py` (a speed control +
apply the rate to the clip players). PTE plays the baked file, so this
override is **Mira-rehearsal-only** (see §3). No data-model change.**

## 1. Is speed baked or runtime? — answer

- **Baked at export:** the clip's intrinsic speed (from the Edit/Workshop)
  is encoded into the mp4 (`setpts=PTS/speed`, video_export_run.py:312). The
  exported file already plays at that speed.
- **Runtime on top:** `QMediaPlayer.setPlaybackRate(r)` changes how fast Mira
  *plays* the file, independent of the baked speed — so a rehearsal override
  is possible without touching the bytes. (PTE has no Mira hook here; it
  plays the file at its baked speed.)

So a Cut-rehearsal speed control is feasible in Mira at runtime; it does
**not** alter the exported clips or the PTE output.

## 2. The fix — a rehearsal video-speed control

- Add a **video-speed** control to the Cut player transport, beside the
  per-slide-seconds spinbox (e.g. a select: 0.5 / 0.75 / 1 / 1.25 / 1.5 / 2,
  default 1×).
- On change, set the rate on the clip player(s): `player.setPlaybackRate(r)`
  for the current and subsequent clips during the rehearsal. It compounds
  with the baked speed (a 2×-baked clip at a 1.5× override plays 3× — that's
  the honest result; document it).
- **End-of-media advance (spec/144 §C) makes this clean:** since the player
  drives the advance on `EndOfMedia`, a faster rate simply ends the clip
  sooner and the show moves on — no timing desync. (Pair with spec/144; a
  fixed-timer model would fight the rate.)
- Persisted only as a rehearsal preference if desired (optional;
  `default_video_speed` from spec/138 can seed the initial value so the
  global default applies here too). Not written to the Cut.

## 3. PTE scope (honest limitation)

A rehearsal override is runtime and Mira-only. To make a Cut's videos play
faster/slower **in PTE** you would have to change the **baked** speed, i.e.
**re-export** the clips at the chosen speed (heavy: re-encode). That is out
of scope here — note it: the override affects the Mira rehearsal, not the
exported clips or the generated PTE. (A future "bake rehearsal speed into
export" option could re-encode, but it's a separate, costly feature.)

## 4. Acceptance

- The Cut player has a video-speed control; changing it changes how fast
  clips play in the rehearsal, live, without re-encoding.
- It compounds with any baked clip speed; default 1× = today's behaviour.
- With spec/144's end-of-media advance, the show timing stays correct at any
  rate (clips end sooner/later and advance cleanly).
- The exported clips and the PTE output are unchanged (override is
  rehearsal-only).

## 5. Tests

- `tests/test_cut_play_speed_override.py` — the speed control calls
  `setPlaybackRate(r)` on the clip player; changing it mid-show applies to the
  current/next clip; default 1×; (with spec/144) the EndOfMedia advance fires
  sooner at higher rates. (Stubbed player.)
- Assert the override does **not** alter export plans or the PTE generator
  (rehearsal-only).
