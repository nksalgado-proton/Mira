# 151 — Video export: passthrough for unedited sources · stream-copy for trim-only segments

**Status: PROPOSED (Nelson 2026-06-25, after the spec/150 re-export
showed the wall-clock cost of re-encoding clips that didn't need any
pixel work). Two recipes that bypass the encoder entirely for the
cases where re-encoding adds zero value, plus the decision on how to
handle the one trade-off they carry. Touches
`core/video_export_run.py::export_processed_clip` only; the per-frame
numpy stage and the encoder ladder are unchanged.**

Motivation: spec/150 §6 (the fps probe-and-replace) exposed how often
the export pipeline runs the full numpy-pipe + h264 re-encode for
clips whose output bytes don't actually need to differ from the
source. For a re-export across many clips that's minutes of wall time
spent producing a byte stream that's effectively a copy of the input.
Two recipes, one per scenario:

## 1. Scenario A — entire source, no edits → hardlink (or copy)

A clip whose plan has:
- `in_ms == 0` AND `out_ms == probed source duration` (no trim), AND
- `not has_colour` AND `not has_crop`, AND
- `speed == 1.0`, AND
- `stabilise == 0`, AND
- `include_audio == True` AND no audio adjustments
  (`audio_volume == 1.0` AND `audio_fade_ms == 0`)

…is the source video, byte-for-byte. The recipe:

```python
try:
    os.link(source_path, output_path)   # hardlink — same-volume, free
except OSError:
    shutil.copy2(source_path, output_path)  # cross-volume fallback
```

Wall time: microseconds (hardlink) or file-copy speed. Zero disk
overhead on the hardlink path. Quality: byte-identical to the source
by definition. spec/150 §3 / §6 are irrelevant (no encoder runs).

The hardlink-then-copy precedent already exists in Mira's spec/89
Model B return scanner (third-party returns into `Exported Media/`)
so the path-resolution + atomic-rename plumbing reuses cleanly.

## 2. Scenario B — one source split into N segments (trim only) → stream copy

A clip whose plan has:
- A non-trivial trim window (`in_ms > 0` OR `out_ms < source duration`),
  AND
- `not has_colour` AND `not has_crop`, AND
- `speed == 1.0`, AND
- `stabilise == 0`, AND
- `include_audio == True` AND no audio adjustments

…is a verbatim slice of the source. Recipe — ffmpeg `-c copy`:

```
ffmpeg -ss <in_s> -i <source>
       -t <dur_s>
       -c copy
       -movflags +faststart
       <output>
```

Wall time: file-copy speed for the slice's bytes (no decode, no
encode). Roughly 10–20× faster than the current single-pass encode.

### The keyframe-shift trade-off (decided 2026-06-25, Nelson)

Stream copy snaps the in-point to the nearest **preceding keyframe**
in the source. On typical phone footage (keyframe interval ~1–2 s)
the realised in_ms can land up to ~2 s earlier than the marker the
user set in the workshop. The out-point is exact.

**Decision: accept the drift as the default.** Nelson 2026-06-25:
the difference is a fraction of a second in a slideshow context and
will not be noticeable. No per-clip ε-gating, no fallback to the slow
re-encode path on a per-marker basis. We log the *realised* in_ms so
the show-totals math (`core/cut_budget.py`) still sums correctly and
the rehearsal scrubber timing stays honest.

If a use case later surfaces where the drift IS noticeable (e.g. a
sub-second segment), the gating logic from the "be picky" variant
(probe keyframes, fall through to re-encode when the marker doesn't
land within one frame of a keyframe) is the future escape hatch.

## 3. Where this fits in `export_processed_clip`

Two new branches at the very top of `export_processed_clip`, after
the `probe_video` + dimensions check but BEFORE the spec/150 §6
fps-replace block (no fps work is needed for either passthrough
recipe — neither runs the encoder):

```python
meta = probe_video(video_path)
src_w, src_h = ...                       # existing

# spec/151 §1 — entire source, no edits → hardlink/copy passthrough.
if _is_full_source_passthrough(plan, meta):
    return _hardlink_or_copy(video_path, output_path)

# spec/151 §2 — trim-only segment → stream copy.
if _is_trim_only_segment(plan):
    return _ffmpeg_stream_copy(video_path, output_path, plan)

# spec/150 §6 — fps probe-and-replace stays here (only matters when
# the encoder will actually run on the bytes).
if meta.fps > 0 and abs(meta.fps - plan.src_fps) > 1e-3:
    plan = dataclasses.replace(plan, src_fps=float(meta.fps))

# … existing fast path / numpy-pipe branches unchanged.
```

The two predicates are pure functions on `ExportPlan` (+ probed
duration for §1); the helpers are thin wrappers over `os.link` /
`shutil.copy2` / `subprocess.Popen`.

Progress / cancel: §1 finishes near-instantly so the file-fraction
sink just snaps `0 → 1`. §2 reports progress at the rate the stream
copy consumes the source (single-pass ffmpeg, same monitoring shape
as `_run_ffmpeg_only`).

## 4. Acceptance

- A clip plan that names a full source with no edits exports as a
  hardlink (same-volume) or copy (cross-volume) in ≪ 1 s, regardless
  of source size.
- A clip plan that names a trim window with no other edits exports
  via `-c copy` in time proportional to the segment's byte length,
  not its duration × encoder cost.
- A clip plan with ANY of colour / crop / speed ≠ 1 / stabilise /
  audio adjustments stays on the existing fast path or numpy-pipe
  path. No behaviour change for those.
- Re-export of an event made up entirely of unedited clips finishes
  at file-copy speed end-to-end.
- The slideshow's timing math (`core/cut_budget.py`) still sums to
  the right wall-clock duration after the keyframe-snap drift on §2
  exports (logged realised in_ms is read back through whatever the
  per-clip duration source is — likely re-probing the exported file,
  same loop as the spec/144 segment-duration fix).

## 5. Tests

- `_is_full_source_passthrough` table-driven: identity plan + matching
  duration → True; any one field that breaks identity → False.
- `_is_trim_only_segment`: trim alone → True; trim + colour / crop /
  speed / etc. → False.
- Integration: build a tiny mp4 via `_make_test_video`, plan an
  identity export, assert the output's content matches the source
  byte-for-byte (and on Windows, that it's a hardlink via
  `os.stat(...).st_nlink > 1`).
- Integration: build a 5 s mp4, plan a trim to `[2.0, 4.0]`, run
  the stream-copy path, assert the output's duration is within one
  keyframe interval of 2 s. (Don't pin exact duration — the snap is
  the documented trade-off.)
- The existing speed / colour / crop tests are unchanged; they
  continue to exercise the numpy-pipe / fast paths as before.

## 6. Non-goals

- No change to the encoder ladder (spec/60 §4).
- No change to the spec/150 fixes — they remain the correctness
  contract for every clip that DOES re-encode.
- No "skip render if last export is still valid" pre-flight; that's a
  separate decision (would need plan + encoder + source-mtime
  fingerprinting). The Export-now flow today already filters out
  non-stale items; this spec is about making the re-renders that DO
  run faster, not about re-deciding which ones run.
- No keyframe-aware marker placement in the workshop (the upstream
  end). The drift is accepted at export time, not paved over at
  marker time.
