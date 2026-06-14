# spec/00 — Rebuild charter

**The constitution of the clean rebuild. Read before every working session.**
Authored 2026-05-30 (Nelson + Claude). Branch: `rebuild/from-spec`.

---

## 0. THE WORKING AGREEMENT — read first, every session, no exceptions (Nelson 2026-05-31)

> **We are NOT creating a new application. We are PORTING an existing application that works
> perfectly, so that it now uses a database accessed through a gateway, instead of a bunch of
> JSON files accessed directly in various parts of the application. THAT IS IT.**
>
> - **UI and flow are kept EXACTLY the same.** Same windows, same dialogs, same titles, same
>   order, same wording, same look. The user spent months tuning the legacy UI; it is not to
>   be redesigned, re-sequenced, simplified, "improved", or substituted.
> - **No new UI/flow code is to be created.** The legacy code is *adapted* — its data-access
>   calls (direct JSON / `save_event` / journals / `load_settings` / filesystem reads) are
>   the ONLY thing that changes, rewired to go through the gateway. Everything else is copied
>   verbatim.
> - The **gateway + database layer** is the single sanctioned new code — it is the thing
>   being *added*; the ported legacy calls target it. That is the whole job.

**This is the supreme rule. Everything below serves it. If anything below — or any instinct
to build/compose/redesign — conflicts with this, THIS wins.**

> **Amendment (Nelson 2026-05-31):** this is not a ban on *improvement*. **Nelson is happy to
> consider changes that would improve the quality of the solution implemented in the legacy
> and/or make it more suitable for use within the new database-driven environment.** The
> discipline is *propose first* — surface the change (manifest / a short ask) and get the OK;
> the prohibition is on **unilaterally** reinventing or substituting a surface as a surprise,
> not on thoughtful, agreed improvements.

**Operationally, for every surface, in this order (no shortcuts):**
1. **Reuse manifest first.** Open the legacy entry point for that surface and list, in order,
   every dialog/widget it opens + the exact persistence calls inside each. Show Nelson the
   manifest. Get an explicit OK **before writing any code**.
2. **Port those exact files verbatim** — copy into `mira/`, swap `ui.*`→`mira.ui.*`
   imports, change ONLY the data-access calls to the gateway. Nothing else moves.
3. **Never substitute a different surface** because it "does something similar." If the legacy
   surface opens dialog X, the rebuild opens the ported dialog X. Full stop.

Anti-patterns that already happened and must never recur (see
`feedback_reuse_legacy_ui_dont_recreate`): building a *fresh* create-event page; and
substituting the Create-Event wizard for the **Capture** flow instead of porting its real
chain (`PreingestPlanConfirmDialog` "Confirm trip plan and timezone…" → `CaptureActionDialog`
→ Fast Culler → `BackUpCardDialog`).

---

## 1. Why we are doing this

The current Mira persistence — a "virtual database" of JSON files and journals
spread across directories, with the folder tree doubling as a source of truth —
**was never designed. It accreted**, one locally-convenient fix at a time, as each
phase was built. The audit (`docs/29`) confirmed the consequence: there is **no
single authoritative record per captured unit**; state is fragmented across
per-phase journals; classification lives in folder *names*; capture time is mutated
in place at ingest (violating the non-destructive principle); paths are absolute and
break on relocate/restore; in-session phase re-entry silently goes stale.

The verdict (ratified): the broken thing is the **data model**, not JSON-vs-SQLite.
So we rebuild the model deliberately, and — because the model rework is unavoidable
anyway and the current code is too entangled to safely mutate in place — we rebuild
the **whole app as a clean reassembly** rather than a series of edits.

## 2. The method — parallel reconstruction behind a hard interface

We build a **new app alongside the old one**, in an isolated namespace, and never
edit the old app's code. The old app keeps running. It serves three roles:

- **Parts source ("lego").** The UI — the expensive, polished, user-perceived part
  — is reused. We keep its visual + interaction code and re-wire its data binding to
  the new interface.
- **Test oracle.** Because it still runs on the same event tree, we diff old-vs-new
  decisions and projection on the *same real event*, per phase. Free regression net.
- **Fallback.** Until a phase is proven on the new store, the old app is always there.

The **interface layer (the gateway)** is the heart of the design: one large facade of
queries and mutators that is the **only** way any UI code touches data. The UI never
knows a database exists. If the gateway lacks something a surface needs, we add it to
the gateway. If something crashes, we harden the gateway so it can never crash that
way again, in any context — **subject to the discipline in §5.3**.

All interpretation of the old, messy world is **quarantined in one disposable
module** — the migration extractor (§4 step 3). The new app contains none of that
logic; the extractor is deleted after migration.

## 3. Locked decisions (do not re-litigate)

- **Model:** one authoritative record per captured unit; everything hangs off it.
- **Live store:** SQLite, one `event.db` per event. **Backup/portability format:**
  JSON (the same JSON is also the migration intermediate and the test-fixture shape).
- **Virtual EXIF:** originals are byte-pristine; capture-time correction lives in the
  record (`capture_time_raw` never mutated + `capture_time_corrected`). Courtesy
  filenames in the rendered tree still reflect corrected time so external tools sort
  right. The SD-wipe gate is redesigned around this and gets *simpler* (no mid-flow
  bake to invalidate the verify hash) — and remains the only sanctioned deletion of
  user originals.
- **Tree is a projection.** `01 - Culled/` … `03 - Processed/` are rendered from the
  store, 100% rebuildable, and read by nobody as truth. External tools (LRC/PTE/
  Helicon) still see exactly the files they see today.
  *(Sharpened 2026-06-10 by [spec/57](57-folders-and-roundtrip.md): the numbered
  phase dirs retire entirely. An event holds `Original Media/` + `Edited Media/`
  (the two ends, real bytes), `Cuts/` (handoffs) and `Picked Media/` — the ONE
  remaining projection, a links doorway for external tools. Fixed English names
  on disk. One sanctioned carve-out to the byte-pristine rule: externally-merged
  stack masters are ADOPTED into additive-only `Original Media/Merged/`;
  card-derived subtrees stay untouchable and the SD-wipe gate remains the only
  sanctioned deletion.)*
- **Relative paths from the user default path** (§5.9, frozen 2026-05-30, Nelson —
  *"a very strong design principle"*). The `photos_base_path` setting is the **single
  absolute anchor** of the whole system; every other persisted path is stored relative
  to it — including each event's `event_root` (in `events_index.json`, relative to the
  base) and, transitively, every in-event path (relative to `event_root`). Relocate the
  whole library ⇒ change one setting. Absolute is permitted *only* as a cross-volume
  fallback (an event on a different drive than the base, where Windows has no relative
  path); store absolute + a flag, mirroring the hardlink copy-fallback philosophy.
- **Namespace:** new app = `mira/` package (the free, collision-proof name).
  Legacy `core/ ui/ data/` stay top-level and untouched until archived.
- **Specs live in `spec/`.** Legacy `docs/` is archived at completion.
- **Five data domains, one discipline.** The gateway fronts not only event data but
  also **user knowledge** (the wizard's output), **classification rules** (+ an
  opt-in user-hardware layer), the **tone-learning corpus**, and **app settings**.
  Substrates differ (SQLite vs JSON), but all share one protection contract and the
  typed-access-only rule. Hardware data is banned as a *dependency* and permitted only
  as *opt-in enrichment that degrades gracefully* (the system is fully correct without
  it). See `spec/02`.
- **First-pass scope:** the **full pipeline** is built before the new store goes
  default. A feature flag selects old vs new app; we A/B a real event before flipping.

## 4. The build sequence

1. **Define the model** — entities, fields, names, types — driven by *final use*
   (§5.1). The SQLite schema and the JSON schema are two renderings of this one model.
2. **Build the JSON → model → store reader** first, against hand-written fixtures.
   Locks the canonical shape and the round-trip before any real data exists.
3. **Build the extractor**: current messy event → clean model-JSON. The quarantine.
4. **Run the extractor** over every existing event → model-JSON backups.
5. **Materialise** each `event.db` from its JSON (same reader as step 2 — restore and
   migration are one code path).
6. **Build the gateway** (can overlap 1–2): the full query/mutator facade. *No app
   code touched through here. Nothing above this line edits the old app.*
7. **Reassemble, moving downstream** — ingest → Cull → Select → Process → Curate →
   Distribute → backup/restore/audit. For each: take the legacy UI parts, sever their
   data tendrils, bind them to the gateway only, parity-test against the oracle.
8. **Flip the flag, archive the legacy** (`core/ ui/ data/` + `docs/`), leaving a
   clean structure from spec to implementation.

## 5. Principles that must hold (the hard-won ones)

**5.1 — The model is the union of every read and every write.** The schema is
defined by enumerating *what every UI surface reads to render* + *what every user
action produces*. This census is the first real artifact (`spec/01`). Miss a field
there and the new app cannot show it. This is harder than the DDL and is where the
care goes.

**5.2 — The UI is not cleanly separable today; extracting the seam is the labor.**
The data seam runs *through* the widgets (they call journal modules directly). Reuse
means: keep the visual/interaction code, cut the tendrils, bind to the gateway. Price
this in; it is the bulk of step 7.

**5.3 — The gateway must not become the new accretion site.** Hardening it for
*robustness* (never throw on a legitimate state; tolerate missing files) is right.
But when a crash reveals a **missing concept**, fix the **model**, do not paper the
symptom in the facade. Every guard added must answer: *invariant the gateway should
always enforce, or model gap?* Prefer the model. Otherwise we rebuild the old mess
inside the new wall.

**5.4 — 100% of the UI *surface*, not its *behavior*.** Some current behavior is
driven by the broken model (folder-name classification, stale-on-re-entry, silent
omission of missing files). Rewired, those behaviors change — *for the better*. The
promise is identical look and interaction with *corrected* data semantics. Never
preserve a bug because the old app had it.

**5.5 — Coexistence is per-event; mark migrated events.** Both apps share the event
tree. Once an event is worked in the new app, the old app must treat it as
read-only — a per-event "this lives in the new world" marker prevents the fallback
silently re-diverging it.

**5.6 — Assembly starts at ingest.** Cull needs items to *exist* in the store, so the
downstream order begins with import (populate items + virtual EXIF + wipe gate),
then Cull onward.

**5.7 — Settings from day one.** Every customizable default lives in one application
Settings class, JSON-backed in the install/user-data area under the standard
protection contract — no magic numbers scattered through code. Some keys are
user-editable in a tabbed Settings dialog; others are app-managed but hand-editable in
the JSON if ever needed. All defaults reside in the class regardless of tier. Built
*before* the UI rebuild so every module reads its defaults from there. (Per-event
overrides still live in the event store; only the *defaults* live in settings.)

**5.8 — UI affordance + translation standards.** Every widget admitted to the new UI
passes the admission test in `spec/05`: pointing-hand cursor + hint on clickables,
I-beam cursor + hint on editables, visual states in both QSS themes, and **every
user-visible string (tooltips included) through `tr()`**. Translation stays a
fill-in-the-catalog task, never a rewrite. Restated here so the clean UI enforces it
from its first widget.

**5.9 — Relative paths from the user default path** (frozen 2026-05-30, Nelson —
*"a very strong design principle"*). The `photos_base_path` setting is the **single
absolute anchor** of the whole system. It is **user data** — set during onboarding,
never hardcoded (CLAUDE.md invariant #2; the code default stays `""`). Every *other*
persisted path is stored **relative to this anchor**:
- `events_index.json` stores each `event_root` **relative to `photos_base_path`** (not
  absolute — supersedes the spec/03 D1 wording that called event_root "the only absolute
  path"); resolved at load as `base + relpath`.
- Everything inside `event.db` stays relative to `event_root` (charter §3).

Relocating the entire library is then a **one-setting edit**. Absolute is permitted
*only* as a cross-volume fallback — an event on a different drive than the base, where
Windows cannot express a relative path; store it absolute + a flag (mirrors the hardlink
copy-fallback philosophy). The system is correct either way; relative is the strong
default, absolute the rare, marked exception. This directly fixes the docs/29 audit's
"absolute paths break on relocate/restore" root cause.

## 6. Discipline (non-negotiable, this is what makes multi-session work)

- **Every session ends with `spec/PROGRESS.md` reflecting reality** — done, in
  progress, exact next action, open decisions. No exceptions. A session that wrote
  code but not progress is an incomplete session.
- **Spec and code land together.** As-built specs are fine; *incomplete* specs are
  not. If it exists in `mira/`, its spec exists in `spec/`.
- **Every downstream phase ships with a parity test against the oracle** (old vs new
  on a real event) before it is considered done.
- **Carry CLAUDE.md invariants forward:** one-way dependency (UI→gateway→store, never
  reverse), no network, no telemetry, atomic writes, `tr()` on every user string,
  no hardcoded user paths. These are not re-litigated; they apply to the new app too.
- **Commit in small, labelled steps** on `rebuild/from-spec`; tag a known-good commit
  before the §4-step-8 cutover.

---

## 7. THE STRATEGIC PIVOT — two-product split (Nelson 2026-06-04)

After two market-research workflows (`wf_0298193a-bcb` + `wf_5ec156fc-419`) and a
brainstorm session that surfaced the V1 / V2 product question, Nelson committed to a
**two-product strategy** that supersedes the original single-product framing of this
charter:

- **Mira X** (Extended) — the *enthusiast* product, Persona 2, Nelson's personal
  tool. The current `rebuild/relational-core` work continues to completion as this
  product. The Supreme Rule of §0 (PORT legacy verbatim, change only data-access calls)
  **still governs Mira X**. Scope-locked completion plan lives in
  [spec/41 — Mira X · Completion Sprint](41-xmc-completion.md).
- **Mira** (no suffix) — the *public* product, V1, Persona 1 (the new-camera
  transition user), free + donations, **"Effortless craft"** as the north star. Built
  from scratch on the locked design in
  [spec/40 — Mira V1 · Effortless Craft](40-v1-effortless-craft.md). Reuses
  shared pure-logic core from X; UI surfaces are different.

**Sequencing:**
1. **First** — complete Mira X 1.0 (2-3 week sprint per spec/41).
2. **Second** — kick off Mira V1 development from spec/40, reusing shared core.

**Why this preserves the charter's principles:**
- §1 (the relational rebuild justification) is shared by both products — same
  relational model, same gateway, same `event.db` per event.
- §2 (parallel reconstruction behind a hard interface) holds verbatim for X. For V1,
  the same discipline applies — V1 UI surfaces target the gateway, never the store
  directly. The "interface layer" is the same gateway code; V1 just consumes a different
  subset.
- §5.3 (the gateway must not become an accretion site) applies to both. Adding
  V1-specific helpers to the gateway is fine *if they represent missing model concepts*;
  not fine if they paper over V1-UI shortcuts.
- §5.4 (UI surface, not behavior) — V1 deliberately changes behaviour relative to legacy
  (3 phases instead of 7, simpler defaults). This is consistent with §5.4's "behavior
  driven by the broken model changes for the better."
- §6 (discipline) holds for both: spec and code land together. spec/40 is V1's
  constitution; spec/41 is X's completion plan. Every session ends with PROGRESS.md
  reflecting reality.

**What CHANGES from the original charter:**
- §4 step 8 ("flip the flag, archive the legacy") still applies to Mira X — but
  it happens at X 1.0 ship, not at "the rebuild's cutover" as originally framed.
- The "Mira" name in the original charter referred to a single product; it now
  refers specifically to V1 (the public product). When this charter says "Mira"
  in §0–§6, in the post-pivot context, read it as "Mira X" since that's the
  product the charter was originally written for.

**Authoritative sources for V1 vs X work:**
- V1 work — read [spec/40](40-v1-effortless-craft.md) first.
- X work — read [spec/41](41-xmc-completion.md) first.
- Both — this charter still applies for foundational principles.
