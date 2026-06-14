# spec/56 — The video workshop (Pick uniformity + Edit-time clips)

**Status:** design LOCKED, Nelson 2026-06-10 (same-day design session,
all questions closed). Implementation not started. Supersedes the
Pick-phase clip/snapshot creation flow wherever it contradicts this
document.

> **2026-06-11 — surface description superseded by
> [spec/59](59-edit-surface.md)** (the Edit Surface design: top grid +
> cursor-driven visibility, the Stop model, modeless development, the
> two bottom lines, "cut"→Marker vocabulary). The marker-partition
> DATA model in this document (§1 rules, §3) stands unchanged.

---

## 0. The mistake being corrected

Clip/snapshot creation has lived in the **Picker**. Nelson 2026-06-10:
*"That was a terrible mistake."* Two costs:

1. **It broke Pick's grammar.** Pick is ONE uniform decision pass —
   Pick/Skip, P/D, the same gesture on all content (spec/48). For
   videos it became an authoring session instead.
2. **It committed bytes during deciding.** Clips/snapshots were
   materialised at Pick — the phase that should only write decisions.

The schema never agreed with the Picker: spec/30's item model defines
clips/snapshots as **virtual child items** (NULL file identity) that
"*Edit materialises by filling the file columns*". This correction
makes the UI obey the data model's original intent.

## 1. The corrected shape

### Pick (video) — uniform again

Watch the video; **Pick or Skip the whole video**. P/D, same as
photos. **No markers, no moments, no clip thinking, no bytes.** All
video special-casing leaves the phase.

### Edit (video surface) — the workshop

- **Top: development.** The same tools as photos — Look chooser,
  Filter, crop (spec/54 + spec/55). Context follows selection.
- **Bottom: the marker timeline + snapshots.**

### The marker-partition model (locked)

- Every video is born with **two implicit markers — start and end** —
  so it begins life as ONE segment.
- The user adds markers = **cut points**. Consecutive markers define
  **segments**; segments tile the timeline (no gaps, no overlaps).
- Each segment is independently **Pick/Skip, default Skip**. Placing
  the selector on a segment and toggling to Pick makes it **a clip to
  be exported**. Same phase-state grammar as everything else.
- **Whole-video export is not a special case**: it is the original
  single segment, picked. (Nelson: "No difference. Beautiful.")
- **Overlapping clips are impossible** by construction — confirmed
  and accepted (segments partition; freeform in/out pairs retire).
- **Trim deltas retire**: trimming IS moving markers. One mechanism.
- **Marker edits vs segment state (locked rules):** a segment keeps
  its Pick state + adjustments when its boundary markers MOVE (its
  identity is its position in the marker order, not its
  milliseconds). A marker inserted INSIDE a segment splits it; both
  halves inherit the parent's state + adjustments; the user
  re-decides as needed.

### Snapshots

- Placing a snapshot **auto-Picks it** — creating one IS the intent.
- A snapshot gets **full photo treatment** in the top panel: Look,
  Filter, crop — identical to a photo.

### Adjustment scoping

Selection drives the top panel: select a frame within a segment → the
tools edit THAT segment's adjustment state; select a snapshot → the
tools edit that snapshot's (photo-shaped) state. Per-segment video
extras (audio, speed, stabilise, fade) stay per segment.

### Bytes

**Nothing materialises before Export.** Export walks picked segments
and picked snapshots, renders each through its own adjustments
(correction → mood → filter → crop, per frame for clips), and commits
files — composing with versions-as-exports (spec/54 §8): re-exports
are versions with lineage snapshots, same as photos.

## 2. Subject — the universal media-unit annotation

Clip labels (the old ``clip_span.label``) RETIRE in favour of the
**universal ``item.subject`` field** (already in the schema since
2026-06-08: free-text — bird species, plant name, person, landmark —
on the item spine, so photos, videos, clips and snapshots all carry
it). For now the storage is the requirement; **where the user fills
it in is a deferred design conversation** (Nelson 2026-06-10).

## 3. Data + migration posture

- Clean slate: Nelson recreates all events — **full schema freedom**,
  no grandfathering of Picker-era materialised clips.
- Markers become first-class rows (per source video); segments are
  DERIVED from marker order; segment decisions/adjustments key on the
  segment's order-identity per the locked rules above. Exact DDL is
  the implementation slice's job (spec/03+30 amendments ride with it).
- ``clip_span`` (freeform in/out + label) and the Pick-phase
  materialisation path (``picked/materialize.py`` etc.) retire.

## 4. What this retires (cleanup inventory)

- Picker video special-casing: moment marking, clip/snapshot creation
  UI, pick-time materialisation, the moments machinery.
- ``VideoAdjustment.trim_start_delta_ms`` / ``trim_end_delta_ms``
  (markers are the trim).
- ``clip_span.label`` (→ ``item.subject``).
- The "full-span clip" special case.

## 5. Open (deferred, explicitly)

1. **Where the user fills ``subject``** — its own design session.
2. Exact marker-timeline interaction details (snap, nudge keys,
   waveform?) — implementation-time decisions within this design.

## 6. Implementation slices (when Nelson pulls the trigger)

1. **Schema v4** — markers + segment-keyed decisions/adjustments;
   retirements (§4); spec/03+30 sync; migration (clean DDL, events
   recreated).
2. **Pick simplification** — video page → watch + P/D; delete the
   workshop chrome + materialisation calls.
3. **Edit workshop** — EditVideoPage rebuild: development on top
   (existing AdjustmentSurface), marker timeline + snapshot strip on
   the bottom; selection-scoped adjustment state.
4. **Export** — picked-segment walker + snapshot stills through the
   photo pipeline; lineage/versions intact.
5. **Cleanup** — retire the §4 inventory; targeted tests per slice.
