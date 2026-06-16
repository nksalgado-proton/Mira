# Phase 1 handover — DC / Cut model (spec/81) lands

**Session:** 2026-06-16 (Claude + Nelson).
**Branch:** `main`.
**Status:** Phase 1 (event-level DC + frozen Cut) functionally **complete**,
committed in one squash. Polish queue in C.7 deferred to a follow-up session.

Read first: [`spec/81`](../spec/81-dynamic-collection-and-cut.md) (the model),
[`agent-tasks/README.md`](README.md) (the build plan + checkpoints), this doc.

---

## What landed (read the commit)

### Data layer — Task A
- **`event.db` schema v7** (`mira/store/schema.py`):
  - `dynamic_collection (id, tag NOCASE UNIQUE, expr_json, filters_json, created_at, updated_at, extras_json)`.
    Tag is a separate namespace from `cut.tag` — operands disambiguate by `kind`.
  - `cut` reshaped: + `source_dc_id REFERENCES dynamic_collection(id) ON DELETE SET NULL`
    (freeze invariant — spec/81 §5), `expr_snapshot_json`, `separators` (default 1),
    `overlay_fields_json`, `overlay_mode`. **Dropped**: `pool_expr_json`,
    `style_filter_json`, `type_filter`.
  - Migration `_migrate_v6_to_v7` synthesizes one DC per existing cut (reusing
    the cut's tag — separate namespaces avoid collision), translates legacy
    pool expr to typed-ref encoding, folds filters into `filters_json`, sets
    `source_dc_id` + `expr_snapshot_json`, drops the three legacy columns.
    Atomic + idempotent via the existing `migrate()` wrapper.
- Models / repo / json_dump round-trip the new shape.
- `CutDraft` (`mira/shared/cut_draft.py`) reframed: `source_dc_id` + `expr`
  + `pin_mode` (`keep-all` / `weed-out` / `pick-in`) + overlays.

### Resolution + gateway + pin + export — Task B
- **`core/collection_resolver.py`** — pure resolver. `+`/`-`/`&` (display `∩`)
  left-to-right; nested-DC grouping; terminal cut/`"exported"` operands;
  memoised; both cycle guards (`reaches` at write seam + raise-on-loop at
  resolution time).
- **Gateway DC seam** (`mira/gateway/event_gateway.py`): `create_dc` /
  `update_dc` / `rename_dc` / `delete_dc` (with cycle-check), `dc_by_tag`,
  `dc_operand_inventory`, `resolve_dc` / `dc_probe` / `dc_show_totals`,
  `cut_overlay_fields`, `cut_card_style`, **`frame_provenance(relpath)`**
  (joins lineage → item → trip_day → `FrameProvenance` for embedded-mode
  IPTC writes).
- **`CutSession`** sources candidates from a DC resolution; separate decision
  ledger (`_picked` dict; phase_state never touched); commit snapshots
  `expr_snapshot_json` + members.
- **`cut_export.export_cut`**: `target` is a **parameter** (default
  `<event_root>/Cuts/<tag>/` via `default_target` — spec/81 §5; no path stored
  on the Cut); embedded overlay mode writes `where` IPTC via injected
  `iptc_writer`; burn-in mode emits copies via injected `overlay_renderer`;
  separators + audio playlist + missing-source reporting unchanged.
- **`core/cut_overlay.py`** — one shared formatter (`compose_overlay_lines`,
  `where_iptc_tags`, `needs_embedded_write`) for Play + embedded + burn-in.

### UI surfaces — Task C (five shape checkpoints, all signed off)
1. **Cuts list** (`mira/ui/pages/share_cuts_page.py`): crash-fixed (gateway
   method renames, dropped-column reads, `CutDraft` field renames). `#exported`
   pool card stays above the new tab widget. `_on_adjust_cut` prefill reads
   `expr_snapshot_json` + source DC's filters.
2. **DC list** (same file): new **Dynamic Collections** tab next to Cuts.
   `DCSnapshot` (id, name, expr_summary, live_count, filters_summary) +
   `DCRow` (primary `Pin → New Cut`, kebab Delete). Empty-state hint.
   Refresh wires `eg.dynamic_collections()` + `eg.dc_probe()`.
3. **New Cut dialog** (`mira/ui/pages/new_cut_dialog.py`): live/pinned hint
   dropped (a Cut is always frozen, spec/81 §1). "Build mode" → "Pin choice".
   **Overlays section**: 4 field checkboxes + 2-way mode radio. `cut_info()`
   emits `overlay_fields` / `overlay_mode`; **`live` retired**. Adapter
   propagates overlays into `CutDraft`.
4. **Pin chrome** (`mira/ui/shared/cut_session_page.py`): already aligned with
   spec/81 — no code change needed. Picker reused on DC resolution, separate
   decision ledger, live green/amber/red `CutBudgetLine`.
5. **Flat grid + Play** (`mira/ui/shared/cut_detail_page.py`): show order +
   separators unchanged. Share export now passes
   `provenance_resolver=eg.frame_provenance` when the Cut has overlay fields
   selected — embedded mode actually writes IPTC at export.

### C.7.a — Save-as-DC (first polish item)
- Footer button "Save as DC…" next to "Save as template…".
- Adapter plumbs `dc_saver(name, info)`; `ShareCutsPage._save_dc` translates
  to `gateway.create_dc(name, expr=..., styles=..., media_type=...)` and
  refreshes the page.
- ValueError surfacing for `taken` / `reserved` / `empty` / `cycle` in the
  dialog.

---

## Tests

- **179/179** Phase-1 + UI tests green via focused pytest sweep.
- New tests: `test_collection_resolver.py` (algebra + cycle guards),
  `test_cut_overlay.py` (overlay formatter + IPTC), and additions in
  `test_store.py` (DC CRUD, v6→v7 migration, FK SET NULL, freeze invariant),
  `test_gateway_cuts.py` (DC operations + `frame_provenance`),
  `test_cut_session.py` (DC-sourced session), `test_cut_export.py` (overlay
  + target defaulting), `test_cuts_shell.py`
  (`test_save_as_dc_creates_a_dc_and_refreshes`,
  `test_back_button_works_after_creating_cut`).
- Last full `verify.bat` sweep ran mid-session: **2931 passed / 277 skipped /
  0 failed** after the look-strength v4-fixture fix in C.5 — recommended to
  re-run once at session start.

Run focused subset (~5 s):
```
python -m pytest tests/test_store.py tests/test_collection_resolver.py \
  tests/test_cut_session.py tests/test_cut_session_page.py \
  tests/test_cut_export.py tests/test_cut_overlay.py tests/test_gateway_cuts.py \
  tests/test_new_cut_dialog_adapter.py tests/test_cuts_shell.py \
  tests/test_exported_watermark.py tests/test_look_strength_foundation.py -q
```

---

## Known issues

### KI-1 — Back button doesn't navigate after creating a cut (live app)
Nelson reported in live testing: after `Create Cut` returns to the Cuts list,
the header **Back** button no longer navigates away from Share.

**Code-level invariant verified:** `test_back_button_works_after_creating_cut`
in `tests/test_cuts_shell.py` exercises the full flow (start session → commit
→ click `list_page._back`) and the `closed` signal fires. So the signal
wiring is intact. The failure is **most likely visual / Qt-paint / focus**,
introduced by the new `QTabWidget` in `_CutsListView._build_ui` (the only
structural change to the list page in this session).

**First probes** when resuming:
- Launch `launch.bat`, reproduce, inspect with the Qt inspector — is the
  back button visible? Is it under another widget?
- Try `self.list_page._back.setFocus()` in `_on_session_done` after
  `setCurrentWidget`.
- Try `self.list_page.update()` / `self.list_page.repaint()` after the stack
  switch.
- Inspect whether the `QTabWidget` is intercepting clicks (event filter, or
  `mouseTracking` differences).

Task #12 in this session's task list tracks it.

### KI-2 — Live Play overlays not drawn
The export pipeline writes IPTC (embedded mode) so PTE renders overlays
natively, but the **in-app rehearsal slideshow** (`cut_play.py`) does not yet
draw overlays live on top of each frame. Spec/81 §3.1: "In-app Play always
draws them live on the frame." Phase 1 ships without this; users see overlays
only after export-to-PTE.

Path: wire `gateway.frame_provenance` into `cut_play.py`'s frame paint;
overlay text via `core.cut_overlay.compose_overlay_lines`.

### KI-3 — Editable export target not surfaced
`cut_export.export_cut` accepts `target` as a parameter (spec/81 §5
"defaulted, not frozen"). Share-page `_on_export_cut` doesn't yet expose a
file picker; it just uses the default. Phase 1 acceptable; spec wants the
target shown as an editable field on the export action.

---

## Polish queue (deferred to next session)

Roughly ordered by value/effort:

| Item | Effort | Value | Notes |
|---|---|---|---|
| **KI-1** — back-button live-app fix | 30–60 min | ★★★ | Likely a paint/focus tweak |
| **DC operands in dialog** | ~30 min | ★★ | Extend `available_pools` to include DCs (typed entries, disambiguate vs Cut chips with same tag) — enables `all-time-best = best-macro + best-wildlife`. The gateway already returns DCs in `dc_operand_inventory` |
| **KI-2** — Live Play overlays | 60–90 min | ★★ | `cut_play.py` per-frame draw; gateway.frame_provenance + `cut_overlay.compose_overlay_lines` |
| **KI-3** — Editable export target | ~30 min | ★ | QFileDialog before export, default selected |
| **∩ operator UI** | ~60 min | ★ | Tri-state per-operand control or separate column; resolver supports it already |
| **Pool→DC rename + string sweep** | ~60 min | ★ | `pool_detail_page.py` → `dc_detail_page.py`; scrub remaining `"pool"` UI strings (the `#exported` card / micro labels still say "pool" in places) |

Total ~5 hours if you drain it all.

---

## Where to start next session

1. **`git log -1`** to confirm you're on the Phase-1 commit.
2. Run **focused pytest** (command above) — should be 179/179 green.
3. Read this file + glance over [`spec/81`](../spec/81-dynamic-collection-and-cut.md).
4. **Launch the live app** and reproduce KI-1 — that's the smallest, highest-
   value remaining issue.
5. From there, pick polish items by appetite. Each is independent.

If you want to verify what the migration did to an existing event.db, the
fixture in `tests/test_store.py::test_migrate_v6_to_v7_synthesizes_dc_and_freezes_cut`
walks you through the synthesized-DC + freeze invariant; the same shape
applies to a real migrated event.

---

## What's NOT in this commit (still in the working tree)

- `spec/77-event-tile-v2-donuts.md` — Event Tile v2 work, predates this
  session.
- `DEVELOPMENT-BACKLOG.md`, `RESEARCH-faces-maps-collages.md` — your notes,
  not Phase 1.
- `agent-tasks/task-A-implementation-plan.md` — left in place as the
  contemporaneous planning artifact; included in the commit since it's part of
  the agent-tasks bundle. Safe to delete later.
