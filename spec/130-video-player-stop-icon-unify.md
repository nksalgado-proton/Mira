# 130 — Video player: ESC must stop audio · fix play/pause icon · unify Picker + Editor transport

**Status: PROPOSED (Nelson 2026-06-23). Three video-player issues, found in
the Picker (audit the Editor for the same): (1) hitting **Esc** returns to
the grid but **audio keeps playing** — the Picker's leave path never stops
the player. (2) the **play/pause icon doesn't track state** — the play glyph
shows while the video is playing (should be pause). (3) Picker and Editor use
**different** transport widgets (`VideoTransportBar` vs the nicer
`VideoWorkshopBar`); they should share one (the Editor's is the better
source). Fixes: stop the video on leave; drive the icon authoritatively +
re-sync on reveal (+ verify the glyph convention); and unify both surfaces on
one shared transport widget. Touches `mira/ui/pages/picker_page.py`,
`mira/ui/edited/` (the Editor video host), `mira/ui/edited/video_workshop_bar.py`
(promoted to a shared module), `mira/ui/pages/video_transport.py` (retired),
and `mira/ui/base/surface.py` (`_TransportButton` glyph check). The engine
(`PhotoViewport`) is unchanged — `_disarm_video` already stops the player +
audio and `_on_playback_state` already emits `video_playing_changed`.**

## 1. Esc must stop the video + audio

`PhotoViewport._disarm_video` correctly `self._player.stop()` +
`setSource(QUrl())` (releases the `QAudioOutput`), exposed as
`shutdown_video()`. But the Picker's **close / back / Esc** path
(`closed` emit → host returns to the Days Grid) never calls it — the only
`.stop()` calls in `picker_page` are the cluster-sweep `_film_timer`. So the
player keeps running off-screen and the audio is audible.

- **Fix:** when the Picker leaves the viewer (the `closed`/back/Esc handler,
  and any path that hides the surface), call
  `self.viewport.shutdown_video()` (or at minimum `video_toggle_play`-to-
  paused; full `shutdown_video` is cleaner — the next video re-arms). 
- **Audit the Editor** video host for the same leave path and apply the same
  stop-on-leave. Belt-and-braces: a surface that hides its media region
  should stop its viewport's video.

## 2. Play/pause icon must track the real state

The icon is driven by `PhotoViewport.video_playing_changed(bool)` →
`bar.set_playing()` → `set_transport_playing()` →
`_TransportButton.set_playing()` → `_refresh_icon()`. Two things to fix:

- **Convention check:** confirm `_TransportButton._refresh_icon` shows the
  **pause** glyph when `_playing is True` and the **play** glyph when paused
  (the standard "button shows the action it will perform"). If inverted, fix
  it here (one place; fixes both surfaces).
- **Re-sync on reveal:** the transport bar is hidden on photos and revealed
  on videos (`setVisible(False)` initially). If a clip is already playing
  when the bar is revealed, no fresh `video_playing_changed` fires and the
  bar shows its stale default (play) glyph. **On reveal**, push the current
  truth: `bar.set_playing(self.viewport.video_is_playing())` (and
  `set_position`/`set_duration`) whenever the bar becomes visible — not only
  on the next state transition.

(Once §3 unifies the widget, this lives in one place.)

## 3. Unify the transport — one shared widget (Editor's)

Picker's `VideoTransportBar` and the Editor's `VideoWorkshopBar` are two
implementations of the same thing. Promote **`VideoWorkshopBar`** (the
better timeline + buttons) to a shared module (e.g.
`mira/ui/media/transport_bar.py`) and use it on **both** surfaces.

- Both already speak the same host contract — `play_pause_requested` /
  `seek_requested` / `volume_changed` / `speed_changed` signals and
  `set_playing` / `set_position` / `set_duration` setters — so the Picker
  rewires to the shared widget with minimal change.
- Picker-specific bits that aren't video transport (the cluster-sweep
  `_film_btn`, Pick/Skip affordances) stay in the Picker; only the
  **video timeline + transport controls** unify.
- Retire `mira/ui/pages/video_transport.py` (`VideoTransportBar`) once no
  caller remains. Keep the Editor's segment-aware extras
  (`set_segment_info`, prev/next-frame) — they're harmless / unused on the
  Picker and already part of the shared widget.

## 4. Acceptance

- Pressing Esc (or Back) from a playing video returns to the grid **and the
  audio stops immediately**; re-entering re-arms and plays cleanly. Same in
  the Editor.
- The play/pause button shows **pause while playing** and **play while
  paused**, including the case where the clip is already playing when the
  transport bar first appears.
- Picker and Editor show the **same** timeline + transport controls (the
  Editor's design); a single widget backs both.

## 5. Tests

- `tests/test_picker_video_stop_on_leave.py` — leaving the Picker viewer
  (closed/back/Esc) calls `viewport.shutdown_video()`; the player is stopped
  (`video_is_playing()` is False, source cleared). Same assertion on the
  Editor host.
- `tests/test_transport_icon_state.py` — `set_playing(True)` selects the
  pause glyph, `False` the play glyph; revealing the bar while
  `video_is_playing()` is True shows the pause glyph (re-sync on reveal).
- `tests/test_transport_unified.py` — both Picker and Editor instantiate the
  shared transport widget; the host signal/setter contract
  (`play_pause_requested` / `seek_requested` / `set_playing` / `set_position`
  / `set_duration`) is wired on each.
- Regress the existing Editor video-workshop tests against the relocated
  module path.
