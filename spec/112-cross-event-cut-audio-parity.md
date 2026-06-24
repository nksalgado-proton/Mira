# 112 — Cross-event Cut export: audio playlist parity

**Status: SHIPPED (Nelson 2026-06-22). The per-event audio block was
extracted into a shared `cut_export.write_audio_playlist(dest,
music_category, photo_count, separator_count, video_ms_total, photo_s,
audio_root, audio_tracks, copy_mode, rng) -> (audio_files, audio_short)`
(the clean-helper option); `export_cut` calls it (17 per-event audio tests
stay green). `export_cross_event_cut` gained `_kind_index_for_source_event`
(mirrors `_origin_index_for_source_event`: open each source event once →
`{relpath → (kind, duration_ms)}`), accumulates photo/video totals in the
member loop, then calls the helper; new kwargs `audio_root`/`audio_tracks`/
`rng`, summary gains `audio_files`/`audio_short`. All three callers wired
(`library_page`, `events_page`, `cut_publish.publish_cross_event_cut`)
passing `settings.audio_library_path`. 5 tests in
`tests/test_cross_event_audio.py` (category→audio; none→none; copy_mode
distinct inodes; default hardlinks; `audio_short` undercoverage). Full
`verify.bat` green (4476 + 24 quarantine). Original proposal follows.**

**Status: PROPOSED (Nelson 2026-06-22). Bug/parity fix: `export_cut`
builds an `audio/` playlist from the Cut's `music_category` + the audio
library, sized to the show; `export_cross_event_cut` does **not** (verified
— zero audio references). So cross-event Cuts export with no soundtrack.
This ports the per-event audio-playlist step into the cross-event exporter
so the two are at parity. Prerequisite for cross-event PTE (spec/107) and
correct on its own. Touches `mira/shared/cross_event_cut_export.py` (reuse
`core.audio_library.build_playlist` + the §5/spec-105 link/copy switch).**

## 1. The gap

`mira/shared/cut_export.py` (per-event) builds the soundtrack: scans the
audio library for tracks matching the Cut's `music_category`, builds a
playlist sized to the show's total duration
(`audio_library.build_playlist(tracks, show_s)`), and writes them into an
`audio/` subdir (now via the spec/105 §5 link/copy switch).
`mira/shared/cross_event_cut_export.py` has **none** of this — cross-event
Cuts get frames (+ originals) but **no `audio/`**.

## 2. The fix

In `export_cross_event_cut`, after placing the members, replicate the
per-event audio step:

- Resolve the cross-event Cut's `music_category` (via `LibraryGateway` —
  `update_cross_event_cut_settings` already stores it).
- Compute the show's total duration from the cross-event members (same
  `ShowTotals` math the per-event path uses — photo_s × photos +
  separators + video_ms).
- `build_playlist(tracks, show_s)` over the library tracks for that
  category, write into `<target>/audio/` through the **same link/copy
  switch** (`_place`, spec/105 §5) so the `copy_mode` flag applies.
- Mirror the `ExportResult` audio counters (`audio_files`,
  `audio_short`).

Factor the per-event audio block into a small shared helper if it makes
the port clean (both exporters call it); otherwise duplicate the ~15 lines.

## 3. Acceptance

- A cross-event Cut with a `music_category` exports an `audio/` subdir with
  the playlist, sized to the show — identical to a per-event Cut.
- The `copy_mode` switch (spec/105 §5) governs cross-event audio too.
- No `music_category` → no `audio/` (same as per-event).

## 4. Tests

- `tests/test_cross_event_audio.py` — a cross-event Cut with a category
  exports a non-empty `audio/`; without a category, none; `copy_mode`
  produces copies vs links.
