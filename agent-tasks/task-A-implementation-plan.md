# Task A — Data layer — implementation plan (grounded in real code)

Produced by the planning pass, 2026-06-16. Reviewed against the live
`mira/store/*` + `mira/shared/cut_draft.py`. Read alongside
`agent-tasks/task-A-data-layer.md` and `spec/81`. **Open decisions at the
bottom are gated on Nelson** before coding.

## Mismatches found between the task file and the real code

1. **Schema version is 6, not "v3+".** `SCHEMA_VERSION = 6` (`schema.py:77`).
   The new migration is **v6 → v7** (`_migrate_v6_to_v7`, appended to
   `MIGRATIONS` ~`schema.py:803`). The fixture to migrate from is a **v6** DB.
2. **`cut` already has `style_filter_json` + `type_filter` columns**
   (`schema.py:467-485`) — the task-file gap list omitted them. The DC's
   `filters_json` is the new home for what these two columns hold today; the
   migration must fold them in.
3. **No `build_mode` / `live` / `start_as` exists in the store layer.** Those
   live only conceptually in spec/80. So "drop build_mode/live" is mostly a
   docstring/naming change in `cut_draft.py`, not an enum deletion.
4. **`cut.tag` is table-wide `UNIQUE` = per-event** (single-event `event.db`).
   The new `dynamic_collection.tag` uses the identical
   `TEXT NOT NULL COLLATE NOCASE UNIQUE` pattern.
5. **`#exported` is never a stored row** — it stays a live query. A DC operand
   referencing the base universe stores the literal token `"exported"`; the
   cycle-guard/FK logic must treat it (and any `cut` ref) as terminal.
6. **Generic CRUD coupling:** `repo.py` builds SQL from dataclass fields and
   round-trips through `json_dump.py`. A new table needs all of: a `models.py`
   dataclass, a `_TableInfo` in `_REGISTRY`, an entry in `_DOC_CLASSES`, a list
   field on `EventDocument`, slots in `save_document`/`load_document`, and
   `to_json`/`from_json` lines in `json_dump.py`. Miss one → silent round-trip
   break. Cycle-guard cannot be a SQL CHECK — it goes at the repo/gateway write
   seam.
7. **A and B must co-land (or B keeps a shim).** Dropping `cut.pool_expr_json`
   breaks `gateway.cut_pool_expr` (`event_gateway.py:939`), `resolve_pool`
   callers, and `create_cut` (`event_gateway.py:1964`). The generic CRUD stops
   emitting the dropped column and gateway code reading it will `AttributeError`.

## 1. New `dynamic_collection` table

```sql
CREATE TABLE dynamic_collection (
  id           TEXT PRIMARY KEY,
  tag          TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  expr_json    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(expr_json)),
  filters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(filters_json)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  extras_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
```

- **`expr_json`** — ordered left-to-right pairs `[<op>, <operand>]`. `<op>` ∈
  `"+"` union / `"-"` difference / `"&"` intersection (display `∩`); all three
  ship. `<operand>` is either the base-universe token `"exported"` or a typed
  ref `{"kind":"dc"|"cut","id":"...","tag":"..."}`. No precedence rules;
  grouping by nesting a sub-DC operand (spec/81 §2).
- **`filters_json`** — an object, extensible to the spec/32 Phase-2 catalogue:
  `{"styles":["macro","wildlife"],"media_type":"both"}`. Event scope sets only
  `styles` + `media_type`; readers tolerate missing keys.
- **Cycle-guard** — cheap non-resolving check at the write seam: walk DC→DC refs
  (cut + exported operands are terminal), reject if the DC's own id is
  reachable. Raise `ValueError("cycle")` for `tr()`-able UI. Full resolution is
  Task B.

## 2. `cut` reshape (`schema.py:467-485`)

- **Add** `source_dc_id TEXT REFERENCES dynamic_collection(id) ON DELETE SET NULL`
  (Cut survives a DC delete — freeze invariant, spec/81 §5).
- **Add** `expr_snapshot_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(...))`
  — the formula frozen at pin.
- **Add overlays** (spec/81 §3.1): `overlay_fields_json TEXT NOT NULL
  DEFAULT '[]'` (`when`/`where`/`how1`/`how2`; `[]` = off) and `overlay_mode
  TEXT CHECK (overlay_mode IN ('embedded','burn_in') OR overlay_mode IS NULL)`.
- **Add** `separators INTEGER NOT NULL DEFAULT 1 CHECK (separators IN (0,1))`
  (spec/61 §4 default ON). `card_style` colour stays in `extras_json`.
- `default_state` stays (seeds the pin session). No `build_mode` to drop.
- **`pool_expr_json` → dropped** after the migration moves it to the DC +
  `expr_snapshot_json`. `style_filter_json`/`type_filter` disposition = open
  decision #1.
- **No target-path column** (spec/81 §5, charter invariant #2).
- **`cut_member` unchanged** (FILE-based, lineage-backed, cascade both ways).

## 3. Migration `_migrate_v6_to_v7`

Append to `MIGRATIONS`; bump `SCHEMA_VERSION` to 7. Follow the existing style:
no inner transaction (the `migrate()` loop ~`schema.py:849-861` wraps each step
in BEGIN/COMMIT/ROLLBACK + version bump → atomic + idempotent for free).
`ADD COLUMN` can't carry CHECK (added columns validated at the seam; fresh
installs get full CHECK from DDL — documented pattern `schema.py:694`).

1. `CREATE TABLE dynamic_collection`.
2–6. `ALTER TABLE cut ADD COLUMN` ×5 (source_dc_id, expr_snapshot_json,
   overlay_fields_json, overlay_mode, separators).
7. **Backfill:** per existing `cut`, synthesize a DC (fresh id, derived tag,
   `expr_json` translated from `pool_expr_json`, `filters_json` from
   `style_filter_json` + `type_filter`); set `cut.source_dc_id` +
   `cut.expr_snapshot_json`; copy timestamps.
8. Drop `cut.pool_expr_json` (+ filter columns per decision #1).

## 4. `models.py` / `repo.py` / `json_dump.py`

- `models.py`: add `@dataclass DynamicCollection`; update `Cut`
  (`models.py:328-351`) — remove `pool_expr_json`, add the new fields; add
  `dynamic_collections: List[...]` to `EventDocument`.
- `repo.py`: `_TableInfo` in `_REGISTRY`, entry in `_DOC_CLASSES` (before
  `Cut`), slots in `save_document`/`load_document`.
- `json_dump.py`: `to_json`/`from_json` lines + header comment.
- Rename `pool` → `dc`/`source` in Task A's files; gateway/session/dialog "pool"
  is Task B/C (co-land per mismatch #7).

## 5. `CutDraft` (`mira/shared/cut_draft.py`)

Replace `pool_expr`/`PoolExpr` with `source_dc_id` + inline `expr`; fold
`style_filter`/`type_filter` into a `filters` mapping; add pin mode + overlays +
`separators`; rewrite docstring to spec/81 framing (no "pool"). Co-lands with
`cut_session.from_draft` + `new_cut_dialog_adapter.py` (Task B/C).

## 6. Tests (`tests/test_store.py`, `tests/test_schema_migration.py`)

DC CRUD round-trip (extend `_rich_document()`); cycle rejection (self + A→B→A);
cut↔DC FK + `ON DELETE SET NULL` leaves `cut_member` intact; **v6→v7 migration**
from a v6 fixture (assert version 7, DC synthesized, source_dc_id set, filters
folded, `pool_expr_json` gone, idempotent, atomic-rollback); `cut_member`
cascade both directions; **freeze invariant** (edit DC → Cut snapshot + members
unchanged).

## Decisions — RESOLVED (Nelson, 2026-06-16)

1. **`cut.style_filter_json` + `type_filter`: DROP.** Filters live on the DC;
   the Cut's `expr_snapshot_json` captures the resolved formula. A Cut is fully
   self-describing. Migration folds both columns into the synthesized DC's
   `filters_json`, then drops them.
2. **DC vs Cut tag namespace: SEPARATE.** A DC and a Cut may share the same
   `#name`; operands are typed by `kind` (`dc`/`cut`), so there is no ambiguity.
   `check_tag` validates a DC tag against the DC list only, a cut tag against
   the cut list only. **The migration reuses each cut's own tag for its
   synthesized DC** (no `dc_` prefix needed).
3. **`source_dc_id` on delete: SET NULL.** Freeze invariant — the Cut survives
   a DC delete and becomes ad-hoc; `cut_member` untouched.
4. **Operand ref encoding: typed `{kind,id,tag}` object.** Forward-compat with
   cross-event; distinguishes live DC-refs (cycle-checked) from frozen cut-refs
   (terminal). `"exported"` stays a bare token.
5. **Pin-mode vocabulary: `keep-all` / `weed-out` / `pick-in`** (spec/80 §2).
   keep-all = pin the DC 1:1, no session; weed-out = start all-in, skip rejects;
   pick-in = start all-out, pick keepers.
6. **Added `source_dc_id` FK: accept `ADD COLUMN` new-writes-only enforcement**
   (no table rebuild).

## Build sequencing — RESOLVED

**Co-land A + B** (data layer + resolution/export engine) in one vertical —
because dropping `cut.pool_expr_json` breaks the gateway (mismatch #7), A and B
ship together and `verify.bat` must be green before the **UI pass (C)** begins.
