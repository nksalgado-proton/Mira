# Task A — Data layer: the DC entity + frozen Cut (event.db)

**Owns:** `mira/store/schema.py`, `mira/store/models.py`, `mira/store/repo.py`,
`mira/shared/cut_draft.py`. **Depends on:** nothing. **Blocks:** B, C.
**Read first:** spec/81 (§2–§5), spec/61 (§1.4 storage, §1.5 names), the
README invariants.

## The gap

`event.db` today (schema v3+, `mira/store/schema.py`) has:
- `cut` — id, name/tag, target_s, max_s, seconds-per-photo, `pool_expr_json`
  (`[["+"|"-","<tag>"], …]`), default_state, music category, created/updated,
  last_exported, `extras_json`.
- `cut_member` — `(cut_id, export_relpath)`, FILE-based, references `lineage`.

spec/80 modelled the live formula as a *mode on the Cut* (`build_mode`,
`pool_expr_json`, the live/pinned split). spec/81 makes the formula a
**first-class noun (the DC)** and makes a **Cut always frozen**. The schema must
follow.

## Build

1. **New table `dynamic_collection`** (event-scope, lives in `event.db`):
   - `id` TEXT PK, `tag` TEXT (transformed name, unique per event — reuse
     `core/cut_names.py`), `expr_json` (the operand algebra: ordered
     `[[<op>, <operand>], …]` where `<op>` ∈ `"+"` union / `"-"` difference /
     `"&"` intersection (display `∩`) — **all three ship**; evaluated
     left-to-right, grouping via nested-DC operands (spec/81 §2). An operand is a
     base-universe token `#exported` **or** a ref to another
     `dynamic_collection`/`cut` by id+tag),
     `filters_json` (event-level = Style list + media type; schema must allow
     the Phase-2 catalogue to extend it), `created_at`, `updated_at`,
     `extras_json`. **No stored membership** — a DC is a recipe, resolved live
     (Task B).
   - Self-referential operands → guard against cycles at write time (Task B
     resolves; A just stores + a cheap cycle check on insert/update).
2. **Reshape `cut` to "frozen, made-from-a-DC":**
   - Add `source_dc_id` TEXT NULL REFERENCES `dynamic_collection(id)` — the DC it
     was pinned from (NULL = ad-hoc/expr captured inline).
   - Keep a **frozen `expr_snapshot_json`** of the resolved formula at pin time
     (so re-export is reproducible even if the source DC later changes — spec/81
     §5; the Cut never re-queries its DC live).
   - **Retire the live-Cut notion:** drop `build_mode`/`live` semantics; there is
     no live Cut. `default_state` stays (it seeds the pin session: all-in vs
     all-out). `pool_expr_json` → migrate into the DC / `expr_snapshot_json`.
   - Keep budget (`target_s`/`max_s`/seconds), `music_category`, separators flag
     (add if absent — spec/61 §4, default ON), `last_exported_at`. **No target
     path column** (spec/81 §5; charter invariant #2).
   - **Attachments — overlays** (spec/81 §3.1): add `overlay_fields_json` (the
     selected provenance fields: when / where / how¹ / how², empty = off) and
     `overlay_mode` (`embedded` | `burn_in`, NULL = inherit the settings
     default). Defaults are scope-aware (event OFF / cross-event ON) but that's
     a settings/UI concern (Tasks C/D); the column just stores the per-Cut
     choice.
   - `cut_member` unchanged (FILE-based, lineage-backed, frozen at pin).
3. **Migration** (new `schema_version`, follow the existing migration-fn
   pattern near the bottom of `schema.py`): create `dynamic_collection`; for each
   existing `cut`, synthesize a DC from its `pool_expr_json` (or inline the
   snapshot) and point `source_dc_id` at it. Idempotent, atomic.
4. **Models + repo** (`models.py`, `repo.py`): row dataclasses + CRUD for
   `dynamic_collection`; update `cut` model/repo for the new columns. Rename
   "pool" → "dc"/"source" in any store-layer identifiers.
5. **`CutDraft`** (`mira/shared/cut_draft.py`): drop `build_mode`/`start_as`
   live-Cut framing; carry a DC reference (or composed expr) + the optional pin
   mode (keep-all / weed-out / pick-in) + budget + filters + music + separators.

## Done when

- `verify.bat tests\test_store*.py` (and any `test_schema*`) green; add tests:
  DC CRUD, cycle rejection, cut↔DC FK, migration from a v-prev fixture DB,
  `cut_member` cascade on cut delete and on lineage delete.
- No `pool` / `build_mode` / `live`-Cut identifiers remain in the store layer.
- Round-trip: create DC → create Cut with `source_dc_id` → frozen members
  survive a DC edit (Cut unchanged).

## Out of scope

`app.db` / `global_items` / `saved_filter` (Task D). The resolution algebra
itself (Task B) — A only stores the formula + a cycle guard.
