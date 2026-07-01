# spec/161 — Vocabulary migration audit (the pre-implementation gate)

> **Status: PROPOSED (Nelson 2026-06-30). Companion to
> [spec/160](160-media-pool-format-cut.md): where spec/160 says
> *what* the target vocabulary is, spec/161 says *how you discover
> every touchpoint you need to move*. Deliverable of this spec is a
> single audit document; implementation of spec/160 is gated on that
> document + Nelson sign-off.**

Reads with:
- [`spec/160`](160-media-pool-format-cut.md) — the target vocabulary
  (Media Pool · Format · Cut) + the §1.1 event-scope simplification
  principle + the §6.2 field-by-field visibility matrix. spec/160 is
  the *what*; this spec is the *how you find everything that needs
  to move*.
- [`spec/93`](93-recipe-collection-storage-and-placement.md) — the
  storage placement rule the audit must respect (schema-internal
  names stay; only user-facing strings + public gateway APIs move).
- [`spec/03`](03-schema.md) — the schema source of truth the audit
  cross-checks against.

---

## 0. Why this spec exists

spec/160's vocabulary shift touches every surface that renders "Cut",
"Collection", "Recipe", "Dynamic Collection", or their inflections;
every dialog that composes any of those; every gateway API that names
them; every test that mentions them. Miss one and either:

- **The user's mental model splits.** A rogue "Recipe" string on some
  seldom-visited dialog defeats the simplification.
- **The code drifts.** New code lands using the old vocabulary because
  a developer read the wrong file and copied its style.
- **Migration silently fails.** A dropped compatibility shim rots
  because we didn't know a legacy caller existed.

The audit's job is to make sure none of that happens. It produces one
document — the touchpoint map — that becomes the checklist implementation
walks through. No touchpoint = no move. Every touchpoint listed = one
line item to migrate.

**Implementation is gated on the audit landing + Nelson sign-off.** No
code changes begin until the audit doc names every touchpoint and Nelson
has read it.

---

## 1. Scope — what the audit must walk

### 1.1 User-facing strings

Every string the user reads, hears, or copies out of the app that
mentions any of the retiring nouns. This is the largest bucket.

**Sources to walk:**
- Every `tr("…")` call site under `mira/ui/` — regex candidates:
  `Collection` (case-insensitive), `Dynamic Collection`, `\bDC\b`,
  `Recipe`, `Cut Recipe`, `Collection Recipe`, `Cut Template`,
  `Save as DC`, `Load DC`, `Save as Recipe`, `Load Recipe`.
- Menu labels + shortcuts (`main_window.py` and any other menu
  builder).
- Dialog titles + section headers (grep for `setWindowTitle`,
  `setTitle`, `ProcessGroupTitle`, `PageHeader`, tooltips).
- Toolbar button labels + button text (`setText`, `QPushButton`,
  `ghost_button(…)`, etc.).
- Error / info / warning messages (`QMessageBox`, log messages
  that surface to the user).
- Onboarding / wizard copy (`mira/ui/wizard/`).
- The `#tag` display convention (`#exported`, `#best_wildlife`) —
  where does the tag string get computed + rendered; does the
  rename affect any of it.
- Any developer-facing docstrings that name the retiring nouns in
  a way a maintainer would copy into future code (docstrings that
  read like tutorials).

**What to record per hit:**
- File path + line number
- Exact current string (verbatim)
- Which retiring noun it uses
- Target replacement string per spec/160 §7
- Confidence: `certain` (a mechanical rename works) / `needs review`
  (context-sensitive, may need rewording) / `keep` (internal
  developer note, not user-facing)

### 1.2 Gateway API surface

Every public gateway method / property / signal / dataclass whose name
carries the retiring vocabulary. These are the API-shape changes that
downstream callers depend on.

**Sources to walk:**
- `mira/gateway/event_gateway.py` — methods named `*_dc_*`,
  `*_collection_*`, `*_recipe_*`, `dynamic_collection*`, etc.
- `mira/gateway/library_gateway.py` — same.
- `mira/gateway/cross_event_resolver.py` — same.
- `mira/gateway/gateway.py` — same.
- Any `pyqtSignal` whose name mentions the retiring vocabulary.
- Any dataclass in `mira/store/models.py` or elsewhere whose name
  carries it (e.g., `DynamicCollection`, if we rename user-facing
  we probably don't rename the class — spec/93 rule — but note it).

**What to record per hit:**
- File path + symbol name (fully qualified)
- Callers: every place that names this symbol (linear scan over
  `mira/` + `tests/`)
- Target new name per spec/160 (or "keep — internal") + rationale
- Compatibility-shim strategy: `direct rename` (all callers
  migrate in one pass) / `shim + deprecation window` (old name
  kept as a thin wrapper) / `keep name, rename user-facing only`
  (schema/model classes)

**Breaking-change tag.** Every "direct rename" candidate is a
breaking-change point. The audit lists them; implementation batches
them into single commits (one PR per rename, so `git bisect` reads
cleanly).

### 1.3 Dialogs + surfaces

Every dialog / page / detail surface that currently renders the
retiring model. These are the visual + interaction migration targets.

**Sources to walk:**
- `mira/ui/pages/new_recipe_dialog.py` — the event-Cut / New Cut
  dialog. Section-by-section: which sections stay, which merge,
  which retire per the spec/160 target.
- `mira/ui/pages/new_cross_event_dc_dialog.py` — the cross-event
  Collection dialog. Same section walk.
- `mira/ui/pages/cross_event_cuts_dialog.py`,
  `cross_event_dcs_dialog.py`,
  `cross_event_cut_detail_dialog.py` — the cross-event surfaces
  that list and browse Cuts + Collections.
- `mira/ui/pages/share_cuts_page.py` — the event-scope Cuts list.
- `mira/ui/shared/dc_detail_page.py` — the Exported Collection
  detail (spec/159 §4.5 uses this today).
- Any other page under `mira/ui/pages/` or `mira/ui/shared/` that
  names the retiring vocabulary (grep for the same terms as §1.1).

**What to record per surface:**
- File path + top-level widget class name
- Current sections + widgets + user actions
- Target sections + widgets + user actions per spec/160 (§5, §6,
  §7). Include:
  - Which sections stay
  - Which sections merge (e.g., event-Cut Filters + cross-event
    Filters unifying under one Media Pool section)
  - Which sections retire outright
  - New sections spec/160 introduces (Format editor if not present
    today)
- Feature-flag matrix per spec/160 §6.2 field-by-field visibility
- Cross-refs: which of spec/160's target sentences (§8) this
  surface is expected to produce

### 1.4 Schema tables + columns

Every schema table / column / index / view whose name mentions the
retiring vocabulary. Per spec/93 §4 + spec/160 §7's guidance, the
default is **keep internal names** — no schema rename. The audit's
job is to confirm that's safe and document any exceptions.

**Sources to walk:**
- `mira/store/schema.py` — grep for `dynamic_collection`, `recipe`,
  `saved_filter`, `global_items`, plus any indexes on them.
- `mira/store/models.py` — dataclass names.
- Any migration in `MIGRATIONS` that manipulates these tables.

**What to record per hit:**
- Table / column / index / view name
- Recommended action: `keep` (default, per spec/93 §4) / `rename
  with compatibility view` (rare — only if the internal name is
  so misleading it hurts developer comprehension) / `retire` (rare
  — only if the entity itself dies)
- Any DB introspection surfaces where the internal name leaks to
  the user (SQL browser, developer console, log messages) — those
  count as §1.1 user-facing hits too

### 1.5 Test surface

Every test file that mentions the retiring vocabulary. Tests are
where the audit's completeness gets stress-tested — if a test
references a symbol we didn't catch, the rename PR fails CI. The
audit lists tests so the implementation batches test updates with
the code they cover.

**Sources to walk:**
- `tests/` — grep same terms as §1.1.
- Fixtures + factories under `tests/` — any that build up an
  `mira.store.models.DynamicCollection` or `Recipe` instance.

**What to record per hit:**
- File path + test function name
- Which category (§1.1–§1.4) this test protects
- Whether it needs mechanical update (imported symbol renamed) or
  semantic update (test assertion prose changes to match new
  vocabulary)

### 1.6 Documentation + specs

Every spec / doc that names the retiring vocabulary. spec/160 §7
already ratified retirement; other specs still speak the old
vocabulary and become stale immediately. The audit lists them so the
implementation batches spec-doc updates alongside code — no drift.

**Sources to walk:**
- `spec/` — grep same terms as §1.1.
- `docs/` — same.
- Any handover docs (`HANDOVER-*.md`), the top-level `CLAUDE.md`,
  and any agent prompts.

**What to record per hit:**
- File path + section (or line-number range if the doc has no
  sections)
- What the passage says today
- What the passage should say in spec/160-terms
- Whether the doc supersedes cleanly or needs a "revised by spec/160"
  banner (like the ones spec/61 §0 and spec/32 §0 already carry)

---

## 2. Deliverable — the touchpoint map

The audit produces **exactly one document**: `docs/spec-160-audit.md`
(or the nearest neighbouring path convention this repo uses for
audit docs). It has six sections mirroring §1.1–§1.6, and one summary
section at the top.

### 2.1 Summary block (top of the doc)

- Total hits per §1 subsection (rough count).
- Breaking-change count (§1.2 direct-rename tag).
- List of files that require a spec-level "revised by spec/160"
  banner (§1.6).
- Any surprises the audit turned up that spec/160 didn't anticipate
  (these become items to feed back into spec/160 amendments before
  implementation begins).

### 2.2 Per-subsection tables

Each of §1.1–§1.6 renders as a Markdown table with the columns
named in this spec's "What to record" bullets. Sorted by file path
then line number for easy diff review.

### 2.3 Cross-references

Every gateway API in §1.2 lists its callers (from a linear scan);
every dialog in §1.3 lists which gateway APIs it uses; every test
in §1.5 lists which category it protects. The audit produces the
call-graph the implementation follows.

### 2.4 What the doc is NOT

- **Not a target design.** The surface plan (spec/160 §9.2) is a
  separate deliverable, deferred to its own spec if needed. This
  audit lists *what exists*, not *what the redesign should look
  like*.
- **Not a code change.** The audit is 100 % read-only over the
  current tree. No renames, no refactors, no compatibility shims
  begin until Nelson signs off.
- **Not a decision-making doc.** Every ambiguous case gets an
  "unresolved — Nelson decides" flag in the audit; the audit reports
  the ambiguity, Nelson resolves it, the resolution amends spec/160.

---

## 3. Categorisation rubric — what maps to what

The audit reuses spec/160 §7's retirement table verbatim. Every user-
facing string mentioning a retiring noun categorises into exactly
one column:

| Current UI noun | New noun (spec/160 §7) | Rationale |
|---|---|---|
| Collection (as Dynamic Collection) | **Media Pool** | §2 |
| Collection (as frozen cross-event output) | **Cut** (library-scope) | §5 |
| Cut Recipe / Collection Recipe | Split → **Media Pool** + **Format** | §2 + §3 |
| Recipe | Split → **Media Pool** + **Format** | §2 + §3 |
| Save as DC | Save as Media Pool | §6.3 |
| Load DC | Load Media Pool | §6.4 |
| Save as Recipe | Split → Save as Media Pool + Save as Format | §6.3 |
| Load Recipe | Split → Load Media Pool + Load Format | §6.4 |
| Cut Template | Doesn't exist | §5.2 |
| pin-mode pill (`pick_in` / `weed_out` / `keep_all`) | "Start all picked" / "Start all skipped" toggle | §4.3 |

**Ambiguous cases the auditor flags for Nelson decision:**
- A UI string that says "Recipe" but refers only to the Format side.
  Direct rename? Or leave for the surface redesign to decide?
- A gateway method named `list_dcs()` that returns objects that
  become Media Pools. Rename the method, or leave the internal
  API? spec/160 says user-facing renames only; the auditor confirms
  which of these are user-facing vs internal.
- A schema table whose name leaks into an error message. Fix the
  error message per §1.1, or fix the underlying schema comment too?

---

## 4. Non-goals

- **Not the surface plan.** spec/160 §9.2 (target UI mocks) is not
  produced by this spec. Whoever writes the surface plan uses this
  audit as input; the audit itself doesn't design.
- **Not implementation.** The audit is read-only. No rename PRs, no
  compatibility shims, no schema changes emerge from the audit
  itself.
- **Not a spec/159 §8 fix.** Extending `global_items_sync` to
  carry lineage ratings is orthogonal to the vocabulary shift and
  stays deferred.
- **Not migration of legacy Cut / Recipe data.** The audit lists
  every existing legacy row it discovers; migration design lives
  in the implementation phase, informed by that list.

---

## 5. Acceptance criteria

The audit is complete when:

1. **Every hit is listed.** A fresh `git grep` (case-insensitive)
   for each retiring noun turns up nothing that isn't in the
   audit doc. The grep + the audit doc reconcile 1:1.
2. **Every hit has a target.** No row in any §2.2 table has an
   empty "target replacement" column. Ambiguous rows carry the
   "unresolved — Nelson decides" flag.
3. **The cross-refs resolve.** Every gateway API's caller list is
   complete; every dialog's API dependencies are named; every
   test's category is tagged.
4. **Surprises are surfaced.** The summary lists any spec/160
   assumption the audit disproved. These get fed back into spec/160
   as amendments *before* implementation begins.
5. **Nelson signs off.** Nelson reads the audit doc end-to-end and
   confirms the categorisations. Sign-off is captured in a
   commit message or a note on the audit doc itself.

---

## 6. Handoff to implementation

Once §5 lands, spec/160 §9.3's phased implementation begins. The
audit doc becomes the checklist:

1. **Vocabulary sweep (spec/160 §9.3 phase 1).** Every §2.2 row in
   §1.1 (user-facing strings) becomes a line item. Zero behavioural
   change; one PR per subsystem.
2. **Format split (phase 2).** Uses §1.4 schema audit + §1.2
   gateway audit to decide compatibility shim scope.
3. **Unified dialog (phase 3).** Uses §1.3 dialog audit as the
   target-state cross-check.
4. **Rules re-scoping (phase 4).** Uses §1.4 schema audit for
   legacy pin-mode data + §1.2 gateway audit for the API changes.
5. **Compatibility shim retirement (phase 5).** Uses §1.5 test
   audit to confirm no test still exercises the shim path.

Each phase lands as its own commit + eyeball. The audit doc is
updated at the end of each phase to mark migrated items ✓; a fully
✓'d audit doc is the definition of "implementation complete."

---

## 7. Estimated effort

Rough shape (not a commitment — this is a data point for planning):

| Section | Estimated hits | Estimated time |
|---|---|---|
| §1.1 user-facing strings | 200–500 | 4–6 h |
| §1.2 gateway APIs | 40–80 | 3–4 h |
| §1.3 dialogs / surfaces | 8–15 | 3–4 h |
| §1.4 schema tables | 4–8 | 1 h |
| §1.5 tests | 60–120 | 2–3 h |
| §1.6 docs + specs | 30–60 | 2–3 h |
| Cross-refs + summary | — | 2 h |
| **Total** | | **~16–22 h** |

Best done in one focused block (fewer context re-loads). A single agent
session or a single dedicated afternoon works better than fragments.

---

Nelson 2026-06-30 — audit spec captured. Audit deliverable follows; no
implementation begins until it lands + Nelson signs off.
