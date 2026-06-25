# 137 — Video playback speed: reset per clip + keep the indicator in sync

**Status: SUPERSEDED by spec/138 (Nelson 2026-06-23). The indicator-only fix
here missed the real root cause — `setSource()` resetting the player rate
with no re-apply, plus the combo emitting `speed_changed(1.0)` on reset.
Implement spec/138 instead. Original below.**

**Status: PROPOSED (Nelson 2026-06-23). Set one video to 2×, and the **next**
video still plays at 2× — **correct, that's the desired sticky behaviour** —
but the speed indicator wrongly shows **1×**. The bug is only the dropdown:
the engine rate is sticky (good), the indicator just isn't told. Cause:
`PhotoViewport._video_rate` carries across clips (init 1.0, applied to every
clip in `_ensure_player`), but the transport speed combo falls back to its
"1×" default on (re)appear and is never re-synced to the engine. Fix
(decided 2026-06-23): **keep the sticky rate** and **sync the indicator to
the engine's actual rate on every reveal / new video** so the dropdown is
always faithful to the real playback speed. Touches
`mira/ui/media/photo_viewport.py` (a getter) + the transport bar host wiring
(Picker / Editor; pairs with spec/130's reveal-resync). No data-model change.

## 1. The bug (indicator only)

- The engine rate is **sticky and correct**: `video_set_playback_rate(r)`
  caches `self._video_rate`; `_ensure_player` applies it to each new clip, so
  a clip after a 2× clip plays at 2× — the intended behaviour.
- The transport bar's speed control defaults to `1×` on (re)appear and is
  **not fed the engine's current rate** — so it reads `1×` while the engine
  runs 2×. That mismatch is the whole bug.

## 2. The fix — keep sticky rate, sync the indicator

### A. Authoritative getter (engine)
Add `PhotoViewport.video_playback_rate() -> float` (returns
`self._video_rate`) — the engine truth, mirroring `video_is_playing()`. **Do
not** reset `_video_rate` on clip change; the carry-over is wanted.

### B. Indicator sync (UI)
On the transport bar's **reveal / new-video** beat, push the truth:
`bar.set_speed(viewport.video_playback_rate())` (alongside the spec/130
`set_playing` / `set_position` / `set_duration` reveal-resync). The speed
dropdown then always equals the real playback speed — showing the carried 2×
on the next clip, not a stale 1×. `set_speed` must update the combo under
`blockSignals` so the sync doesn't re-emit `speed_changed` back into the
engine.

## 3. Acceptance

- Set video A to 2×; the next video B **plays at 2×** (sticky) **and** the
  dropdown **shows 2×** — engine and UI agree.
- Changing speed on the current clip updates both; re-revealing the transport
  shows the current clip's true rate.
- Applies in Picker and Editor (shared transport, spec/130).

## 4. Tests

- `tests/test_video_rate_sticky.py` — `video_playback_rate()` carries the
  prior clip's rate across an arm (stays 2× after a 2× clip);
  `video_set_playback_rate` applies live to the current clip.
- `tests/test_transport_speed_sync.py` — revealing / loading a new video calls
  `bar.set_speed(viewport.video_playback_rate())`; the dropdown shows 2× on
  the next clip after the user set 2× on the previous one; the sync does not
  re-emit `speed_changed` (blockSignals).
