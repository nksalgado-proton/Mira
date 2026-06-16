# Task B — Resolution engine, pin, and export

**Owns:** a new `core/collection_resolver.py`, `mira/gateway/event_gateway.py`
(DC seam), `mira/shared/cut_session.py` (pin), `mira/shared/cut_export.py` +
`core/cut_budget.py` / `core/soundtrack_builder.py` / `core/audio_library.py` /
`core/aspect_ratio.py` (export). **Depends on:** A's tables. **Blocks:** C.
**Read first:** spec/81 (§2 resolution, §4 verbs, §5 export, §6 pacing),
spec/61 (§2 Picker session, §5 consuming).

## 1. DC resolution engine (pure `core/`, no Qt)

New `core/collection_resolver.py`:
- Input: a DC `expr_json` (ordered `[["+"|"-", operand], …]`) + `filters_json`,
  plus a data accessor injected by the gateway (so `core/` stays Qt-free **and**
  free of direct DB-seam assumptions — pass in callables/rows).
- Evaluate **set algebra left-to-right**: `+` union, `−` difference, `&`
  intersection (display `∩`) — **all three ship** (spec/81 §2). No precedence
  rules: **grouping is done by nesting a DC as an operand**, so the resolver just
  recurses. Operands resolve recursively: base universe `#exported` (event
  scope) → the lineage-backed exported-file set; a DC/Cut operand → that DC's
  live resolution / that Cut's frozen members.
- **Apply filters** to the resolved set: event-level = **Style (classification,
  combinable) + media type (photo/video)** only (spec/81 §2.1). Structure the
  filter application so the Phase-2 EXIF/settings/location catalogue (spec/32 §2)
  slots in without a rewrite.
- **Cycle safety** at resolution (A guards writes; B must not infinite-loop on a
  bad graph). Memoise within one resolution pass.
- Output: an ordered (chronological) list of member files with lineage links.

## 2. Gateway seam (`mira/gateway/event_gateway.py`)

- **Rename the existing `pool` surface to DC:** `resolve_pool` / `pool_probe` /
  `pool_show_totals` → `resolve_dc` / `dc_probe` / `dc_show_totals` (grep shows
  `pool` refs here and in the dialog — coordinate the rename with Task C).
- Expose: resolve a DC to its file set + count; the **operand inventory** for the
  dialog (`#exported` + every existing DC and Cut in this event); save/update a
  DC; list DCs.
- Keep `core/` pure: the gateway feeds rows/callables into
  `collection_resolver`, it does not put resolution logic in the UI or the DB
  layer.

## 3. Pin — DC → Cut (`mira/shared/cut_session.py`)

`CutSession` already drives the pick/skip session and `show_entries`. Make it:
- **Source its candidate set from a DC resolution** (not an inline pool).
- Run on a **separate decision ledger** — Pick/Skip here touches *this Cut only*,
  never the real Pick-phase `phase_state` (spec/61 §2).
- On commit, **snapshot** the resolved members into `cut_member` and write the
  Cut's `expr_snapshot_json` (frozen — never re-queries the DC; spec/81 §5).
- **Keep-all pin** = snapshot the DC's resolution one-to-one, no session.
- Live **budget line** (green ≤ target / amber ≤ max / red over) via
  `core/cut_budget.py`: photo = display seconds, clip = true duration, separator
  = one slide (spec/61 §2.5). Pacing is **not stored** — derived from the budget
  (spec/81 §6).

## 4. Export — Cut → directory (`mira/shared/cut_export.py`)

- Materialise to a target dir: **linked media** (NTFS hardlink, copy fallback —
  no byte copies), filenames sorted = chronological show order; **separator
  images** rendered in sequence (default ON, `core/aspect_ratio.py`, plan data
  per spec/61 §4); **`audio/` subdir** of linked songs
  (`core/soundtrack_builder.py` + `core/audio_library.py`, spec/61 §5.3).
- **Composition travels with the Cut; the target does NOT (spec/81 §5).**
  `export_cut(...)` takes `target` as a **parameter** defaulting to
  `<event_root>/Cuts/<tag>/` (via `mira/paths.py`). Do **not** persist an
  absolute target on the Cut. Re-export reproduces identical bundle *content*;
  location is a re-confirmed default.
- Export is a snapshot; update `last_exported_at`; never rewrite prior folders on
  rename.

## 5. Overlays — embedded metadata (default) + burn-in (opt-in)

Overlays are a Cut attachment (spec/81 §3.1): provenance text (when / where /
how¹ / how²) over fields Mira already holds. They **cost no budget** and **change
no membership**. **In-app Play always draws them live** on the frame
(non-destructive) — wire that in the play path regardless of export mode.

Two export modes (`cut.overlay_mode`, default from settings):
- **`embedded` (default, link-pure):** PTE renders text from metadata *embedded
  in the file* via its native *Add Text with EXIF/IPTC* feature — **no sidecar
  file.** Technical EXIF (when/how¹/how²) is already in the exported JPEG; only
  **where** needs writing → write IPTC City/Country/Sublocation (spec/32 §2c
  field names) with the bundled **ExifTool**. **Prefer writing it at Export
  phase** so Cut members stay hardlinks (coordinate with spec/60 / the
  export-phase writers); if a Cut needs metadata not in the file, fall back to
  copy-then-write for those members only. Optionally emit a starter PTE text
  template matching the selected fields.
- **`burn_in` (opt-in, self-contained):** render member *copies* with the chosen
  fields drawn into the pixels via the render pipeline (`core/photo_render.py` /
  `core/process_render.py`). These members are copies, not links. Used for
  non-PTE viewers / shareable bundles.

Keep the field-composition (which fields → what text) in pure `core/` so both
modes and Play share one formatter.

## Done when

- `verify.bat` green; new tests: resolver algebra (`+`/`−`/`∩`, nested DC operand
  as grouping, Style+media filters), cycle non-loop, pin snapshot freeze (edit DC → Cut
  members unchanged), budget zone math incl. separators, export link/fallback +
  audio playlist length rule (≥ duration, include crossing file) + target
  defaulting (no path stored on Cut); overlay field-formatter, embedded-mode
  IPTC write (where) + link preservation, burn-in emits copies, zero budget cost.
- `core/collection_resolver.py` imports no Qt and nothing from `mira/ui/`.
- No `pool` identifier remains in gateway/shared (coordinate with C).

## Out of scope

UI (Task C). Cross-event universes/filters + `global_items` (Task D) — but leave
the resolver's universe + filter dispatch open for them.
