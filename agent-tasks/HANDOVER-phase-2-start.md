# Phase 2 handover — cross-event DCs (Task D)

**From:** 2026-06-16 Phase-1 polish session (Claude + Nelson).
**Branch:** `main`. **Last commit:** [`8c4d11b`](https://github.com/nksalgado-proton/Mira/commit/8c4d11b)
spec/81 Phase 1 polish — KI-1/2/3 fixes + DC operands + ∩ + Pool→DC.
**Status:** Phase 1 fully shipped at the event level. Phase 2 is **NEW
WORK** in a fresh session.

Read first, in this order:
- [`spec/81 §2.1`](../spec/81-dynamic-collection-and-cut.md) — the
  scope-asymmetry table (what changes vs Phase 1)
- [`spec/32`](../spec/32-dynamic-collections.md) — the full filter
  catalogue, `global_items`, `saved_filter`
- [`spec/61 §8`](../spec/61-share-event-cuts.md) — cross-event
  trailhead
- [`spec/75 §2`](../spec/75-cross-event-band.md) — the cross-event band
  entry point on the events screen
- [`agent-tasks/task-D-cross-event-phase2.md`](task-D-cross-event-phase2.md)
  — the original task brief
- [`agent-tasks/HANDOVER-phase-1-complete.md`](HANDOVER-phase-1-complete.md)
  — what landed in Phase 1 (the foundation)
- [`agent-tasks/README.md`](README.md) — invariants binding all tasks

---

## Foundation that's already in place

Phase 1 left the engine ready to extend, not lock-in to event scope.

### Resolver (`core/collection_resolver.py`)
- **Scope-agnostic by design** — every data accessor is **injected** as
  a callable: `base_universe(token)`, `dc_by_ref(ref)`,
  `cut_members(ref)`, `apply_filters(keys, filters)`. The resolver
  recurses + memoises + cycle-guards independent of where the keys come
  from. **Cross-event resolution is "inject a different accessor set",
  not "rewrite the resolver".**
- Operators `+`/`-`/`&` and operand kinds (`base`/`dc`/`cut`) are
  shared across scopes — Phase 2 reuses them verbatim.
- `BASE_EXPORTED = "exported"` is the only event-scope base token; the
  cross-event ladder (`collected`/`picked`/`edited`/`exported`) is left
  open. Add the three new tokens + their accessors at the cross-event
  injection seam.

### Gateway seam (`mira/gateway/event_gateway.py`)
- `EventGateway` is **per-event**. For cross-event you'll want a peer
  (`UserGateway`? `LibraryGateway`?) that wraps the user-level store
  and reads across all `event.db` files via `global_items`.
- `dc_operand_inventory` / `dc_probe` / `dc_show_totals` / `resolve_dc`
  are per-event today. The cross-event surface needs equivalents that
  read against `global_items` rather than one event's lineage rows.

### Dialog (`mira/ui/pages/new_cut_dialog.py` + adapter)
- `PoolOption` already carries `kind` (`base`/`dc`/`cut`) + `id`.
  Cross-event adds rungs (`#collected`/`#picked`/`#edited` alongside
  `#exported`) and DC operands sourced from the user-level store
  instead of an event's `dynamic_collection` table.
- `_pool_expr` emits the same typed-ref shape — no change needed at
  the dialog level for the operand encoding.
- The **filter surface is the divergence point** (spec/81 §2.1):
  cross-event needs the full spec/32 §2 catalogue (camera, lens,
  focal length, aperture, shutter, ISO, temporal, location,
  curatorial), not just Style + media type. This is the biggest UI
  build.

### Cut export (`mira/shared/cut_export.py`)
- `export_cut` is event-scoped (`event_root`). Cross-event Cuts span
  multiple events — the export needs per-member `event_root` lookup
  (the member's `export_relpath` is meaningless without the event
  context). Likely: the resolver returns members with event_id, and
  the export iterates events.
- **Embedded overlays are already cross-event-clean.** Each exported
  JPEG carries its own EXIF + IPTC; `where_iptc_tags` writes per-file.
  No new write needed for cross-event scope — that's free.

### CutDraft + Cut schema
- `CutDraft.expr` is typed-ref operands — same shape works for
  cross-event DCs (the operand `kind` says `"dc"`; the resolver
  looks up by id/tag).
- `cut.source_dc_id` is a foreign key into the **event-local**
  `dynamic_collection` table. For cross-event Cuts the `source_dc_id`
  needs to reference a cross-event DC (different home). Either: drop
  the FK and use a UUID + a kind discriminator, OR keep two
  `source_*_id` columns. **Recommendation (apply this):** drop the
  FK, store the id as opaque, and add `source_dc_kind` (`"event"` /
  `"user"`).

---

## Phase 2 build — what's open

The five build items from
[`task-D-cross-event-phase2.md`](task-D-cross-event-phase2.md) stand.
Reordered here by dependency (later items need earlier ones):

### 1. `app.db` global index (`global_items`) — spec/32 §3
Projection table that lets cross-event queries hit ONE SQLite file
instead of fanning out across every `event.db`. Columns per spec/32 §3
(reconciled names: **`pick_state`** was `cull_state`; **`flag`** was
`pick`). Plus an event_id FK so members can roundtrip to their event.

**Sync trigger** — on event close? on app startup? on demand? Spec/32
§3 leans "background, eventually consistent". **Recommendation (apply
this):** sync on event close + startup reconcile.

**Cross-event DC schema lives here too** — or as a sibling table:
- Option A — **extend `dynamic_collection`** to cross-event by making
  the `event_id` column nullable (NULL → user-level).
- Option B — **new `user_dynamic_collection`** table mirroring the
  Phase-1 schema.
- Option C — **wrap `saved_filter`** (spec/32 §4): cross-event DC IS a
  saved_filter, no separate table.

C is the spec-aligned answer (spec/32 already names `saved_filter`)
but requires reconciling the predicate-tree shape with our typed-ref
`expr_json`. **Recommendation (apply this):** wrap `saved_filter` —
reconcile predicate-tree ↔ `expr_json` as you go.

### 2. Resolver extension (`core/collection_resolver.py` + a new seam)
- Add the three new ladder tokens (`collected`/`picked`/`edited`) as
  recognised base universes (`BASE_*` constants).
- Provide cross-event accessor implementations: read from
  `global_items` instead of `lineage`. **Keep the resolver itself
  unchanged** — it's already injectable.
- Two query layers exist (event vs cross-event); the resolver doesn't
  care, the caller picks. **Flagged 2026-06-16: don't assume one query
  layer covers both.**

### 3. Cross-event gateway peer
- `mira/gateway/user_gateway.py` (or similar). Mirrors the per-event
  `dc_operand_inventory` / `dc_probe` / `dc_show_totals` / `resolve_dc`
  surface but reads from `app.db` + `global_items`.
- The same Cut session machinery can drive cross-event Cuts — the
  inputs are the same shape (a set of operand refs + filters).
- The big delta: `frame_provenance` needs an event-id lookup (the
  same relpath in two events resolves to two different items).

### 4. Pin across events + grab-originals (spec/61 §8)
- "Gathers DCs/Cuts **by tag across selected events**" — the
  lowercase-normalised tag is the glue.
- **Grab-originals** lands here (spec/61 §8, §6): for a cross-event
  Cut whose members are still `#collected` or `#picked` (not exported
  yet), pull the originals over so the export has bytes to link.
  Whether this is in the first cut or a fast follow is **open**.

### 5. Cross-event UI surface
- **Entry**: the cross-event band on the events screen (spec/75 §2).
- **Dialog**: the New Cut dialog reused — but with the wider operand
  inventory (ladder rungs + user-level DCs) and the full filter
  catalogue.
- **Filter surface**: this is the heaviest build. Spec/32 §2 lists
  hardware / settings / temporal / location / curatorial facets. Each
  facet needs its own widget (range, single-select, multi-select).
  Spec/61 §8: "more a search tool than a share-selection tool" — lean
  into Lightroom-style faceted search, not the small event picker.

---

## Attachment defaults flip cross-event (spec/81 §3.1)

| Attachment | Event default | Cross-event default |
|---|---|---|
| Separators | ON (single timeline to orient) | **OFF** (no single timeline) |
| Overlays | OFF | **ON** (the portfolio case — "how and when") |
| Audio | per-Cut | per-Cut (no flip) |

The Cut model already carries per-Cut overlay + separator settings.
The defaults are a **dialog-level** decision (what the New Cut form
seeds when scope = cross-event). No schema change needed.

---

## Constraints that bind Phase 2 (unchanged from Phase 1)

- **One-way deps**: `mira/ui/` → `mira/gateway` + `core/`. `core/`
  imports neither Qt nor `mira/ui/`. The cross-event resolver work
  stays in `core/`.
- **No hardcoded user paths.** Cross-event Cuts especially — the
  member files live under N different event roots; the export must
  resolve each per-event without baking absolutes.
- **No network, no telemetry.** Cross-event sync is local-only.
- **`tr()` every user-visible string.** The filter facet UI will have
  a lot — budget for translation.
- **Atomic write-then-rename** for `global_items` updates.
- **QSS only** for styling.
- **Spec lands with code** — if behaviour drifts from spec/81 / spec/32,
  fix the spec first.
- **Vocabulary locked**: DC + Cut are the only two nouns. No
  `cull/curate/keep/discard/select/pool/Dynamic Cut/Show profile` in
  new code. The legacy `core/cull_state` is still load-bearing for the
  Pick phase — don't touch it from Phase-2 code, but stay clear of its
  surface.

---

## Tests available as a foundation

Phase-1 focused subset (already passing, **run on session start**):

```
python -m pytest tests/test_store.py tests/test_collection_resolver.py \
  tests/test_cut_session.py tests/test_cut_session_page.py \
  tests/test_cut_export.py tests/test_cut_overlay.py tests/test_gateway_cuts.py \
  tests/test_new_cut_dialog_adapter.py tests/test_cuts_shell.py \
  tests/test_cut_play.py tests/test_dc_detail_page.py \
  tests/test_pool_delete_cascade.py tests/test_exported_watermark.py \
  tests/test_look_strength_foundation.py -q
```

Expected: **231/231 green** (~5 s). If anything is red, **STOP** and
diagnose — Phase 2 builds on Phase 1 staying green.

Resolver tests in `test_collection_resolver.py` cover the algebra +
cycle guards generically; they should extend cleanly to cross-event
because the resolver itself doesn't change.

---

## Open questions — pick the recommendation, don't ask

These are the brief's "settle at kickoff" plus what surfaced during
Phase 1. **Posture (Nelson 2026-06-16): pick the recommended path
on each and proceed.** Flag back only if the recommendation falls
apart on contact with the code. Each item below has its
recommendation in bold:

1. **Cross-event DC storage** — extend `dynamic_collection` (nullable
   `event_id`) vs new `user_dynamic_collection` vs wrap
   `saved_filter`. Recommendation: **wrap `saved_filter`** because
   spec/32 §4 already defines it; reconcile predicate-tree ↔
   `expr_json` at kickoff.
2. **`global_items` sync trigger** — on event close, on startup, on
   demand, or background. Recommendation: **on event close + startup
   reconcile**. Spec/32 §3 supports either.
3. **Grab-originals scope** — in the first cross-event cut or a fast
   follow. Recommendation: **fast follow** so the first cut surface
   stays small.
4. **`cut.source_dc_id` cross-event story** — drop the FK and use a
   UUID + kind discriminator, OR add a second column
   (`source_user_dc_id`). Recommendation: **drop the FK**, store the
   id as opaque, and add `source_dc_kind` (`"event"` / `"user"`).
5. **Event-id on cross-event member rows** — the Cut needs to know
   which event each member came from for the export to find the file.
   Add `event_id` to `cut_member` (nullable; NULL = legacy event-scope).

---

## Where to start

1. `git log -1` — confirm you're on `8c4d11b` or later.
2. Run the focused pytest subset above — must be green.
3. Read the spec/81 §2.1 table + spec/32 in full.
4. Apply the recommended answers to the five open questions above.
   Don't open a kickoff conversation for them — Nelson signed off on
   the "pick recommended path" posture at handover. Flag back only
   if a recommendation breaks against the code.
5. Build in this order:
   a. **`global_items`** projection + sync (smallest, no UI).
   b. **Cross-event resolver accessors** (ladder tokens + filter
      dispatch over `global_items`). All in `core/` + tests.
   c. **Cross-event gateway peer** + tests.
   d. **Pin-across-events session glue** + tests.
   e. **Filter facet UI** + cross-event-band entry point.
   f. **Grab-originals** (if scoped in).

Each item gets its own shape checkpoint with Nelson before
proceeding — same as Phase 1.

---

## What's NOT in the Phase-1 commits (still useful context)

- `DEVELOPMENT-BACKLOG.md` — Nelson's notes, predates this work.
- `RESEARCH-faces-maps-collages.md` — separate research thread (B4/B5/B6).
- `agent-tasks/task-A-implementation-plan.md` — Phase 1's planning
  artifact, kept for reference.

---

## A note from the Phase-1 session

The resolver-first / inject-everything design from Task A paid off in
Phase 1: every UI surface composed cleanly because the engine didn't
care where data came from. Phase 2 should lean on the same shape —
**add accessors, don't fork the engine**. If you find yourself
copy-pasting resolver logic for cross-event, stop and find the seam
instead.

The biggest live-app surprise in Phase 1 was a Qt focus/paint issue
(KI-1) that passed the regression test but failed real interaction.
Plan for an in-app walkthrough on a real library before declaring
Phase 2 done — the cross-event surface has more state to get wrong.

— Claude, 2026-06-16
