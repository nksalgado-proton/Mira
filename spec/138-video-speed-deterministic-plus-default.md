# 138 — Video playback speed: make it deterministic + add a global default (definitive)

**Status: PROPOSED (Nelson 2026-06-23). Video speed is a mess: a clip set to
2× sometimes keeps 2× on the next clip, sometimes drops to 1×, and the
indicator routinely lies (shows 1× while playing 2×). Two **independent**
uncontrolled reset paths cause it; spec/137 (indicator-only) missed both.
Fix all of it: (1) **deterministically re-apply the rate after every
`setSource()`** so the engine always plays at the intended speed; (2) make
the speed **sticky for the session**, **initialised from a new global
default in Settings**; (3) keep the **indicator faithful** by syncing it to
the engine truth under `blockSignals` and never letting a programmatic combo
update push a stray `speed_changed` back. Touches
`mira/ui/media/photo_viewport.py`, the transport bar (Picker/Editor),
`mira/settings/model.py` + the Settings dialog. **Supersedes spec/137.****

## 1. Root causes (both real, both in the code)

1. **`setSource` resets the player rate, never re-applied.**
   `_ensure_player` builds the `QMediaPlayer` **once** and sets
   `setPlaybackRate(self._video_rate)`. Every subsequent clip goes through
   `_arm_video` → `self._player.setSource(...)` → `play()` — with **no**
   `setPlaybackRate` after `setSource`. `QMediaPlayer.setSource()` resets
   playbackRate to 1.0, and whether/when that takes effect is
   backend/timing-dependent → the clip plays at 1× or 2× unpredictably while
   `self._video_rate` (the cache) is stale-correct. This is the "sometimes
   goes back to 1×" mess.
2. **The combo can shove the engine to 1×.** On reveal / new clip the speed
   combo defaults to `1×`; if that programmatic change emits `speed_changed`,
   it calls `video_set_playback_rate(1.0)` and resets the engine. The
   indicator and engine fight each other.

## 2. The fix

### A. Deterministically apply the rate to every clip (engine)
In `_arm_video`, **after** `setSource(...)` (and before/with `play()`),
**always** `self._player.setPlaybackRate(self._video_rate)`. The rate is then
applied deterministically to each clip regardless of Qt's setSource reset.
(`video_set_playback_rate` keeps updating `_video_rate` + the live player.)

### B. Sticky session rate, seeded from a global default
- `self._video_rate` initialises from the new **`Settings.default_video_speed`**
  (default `1.0`), not a hardcoded 1.0.
- It is **sticky across clips for the session** (carry-over is wanted — the
  decided behaviour). It only changes when the user changes the speed combo.
- Add `PhotoViewport.video_playback_rate() -> float` (the engine truth).

### C. Indicator always faithful, one-way sync (UI)
- On the transport bar's reveal / new-clip beat, push engine→UI:
  `bar.set_speed(viewport.video_playback_rate())`. `set_speed` updates the
  combo **under `blockSignals`** so it does NOT re-emit `speed_changed`
  (kills root cause #2). Pairs with spec/130's reveal-resync.
- `speed_changed` fires **only** from a real user interaction with the combo;
  programmatic syncs never do.
- The combo's default text comes from `Settings.default_video_speed`, not a
  hardcoded `1×`.

### D. Global default in Settings
- `Settings.default_video_speed: float = 1.0` (model + dialog control — a
  small select: 0.25 / 0.5 / 1 / 1.5 / 2). New viewports/sessions start at
  this rate; the transport combo shows it.

## 3. Acceptance

- Set a clip to 2×; the next clip **plays at 2×** (sticky) **and** the
  dropdown **shows 2×** — every time, no "sometimes 1×."
- The indicator never disagrees with the actual playback speed.
- Changing the speed combo changes the real rate live; programmatic syncs
  never bounce the engine.
- A Settings "Default video speed" applies to fresh sessions; e.g. set 1.5 →
  the first clip plays and shows 1.5×.
- Picker and Editor behave identically (shared transport, spec/130).

## 4. Tests

- `tests/test_video_rate_applied_per_clip.py` — after `setSource` for a new
  clip, `setPlaybackRate(self._video_rate)` is invoked so the live player
  rate equals `_video_rate` (assert the call / the player rate); a 2× clip
  followed by a new arm still drives the player at 2×.
- `tests/test_speed_indicator_no_bounce.py` — `bar.set_speed(...)` updates the
  combo without emitting `speed_changed` (blockSignals); a user combo change
  DOES emit and reaches `video_set_playback_rate`; reveal sync shows the
  engine's current rate.
- `tests/test_default_video_speed_setting.py` — `_video_rate` + combo seed
  from `Settings.default_video_speed`; round-trips; default 1.0.
