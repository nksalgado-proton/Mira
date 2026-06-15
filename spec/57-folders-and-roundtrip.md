# spec/57 — Event folders, the external round trip, and event creation

**Status:** design LOCKED, Nelson 2026-06-10 (design session, all questions
closed one by one). Implementation: slices 1–5 landed 2026-06-10 (slice-5
decisions in §4.3.1). Supersedes
[spec/52](52-event-creation-vision.md) wherever it contradicts it; amends the
charter's tree-projection bullet with one sanctioned carve-out (§2.3); fixes
the on-disk placement spec/51 left open. Sister design to
[spec/56](56-video-workshop.md) — same principle extended to folders: **bytes
at the two ends, the database in the middle.**

---

## 0. What this locks

Three intertwined decisions, taken together because they define each other:

1. **The event folder model** — what exists on disk, by name, and why.
2. **The external round trip** — how third-party software (LRC-class
   editors, focus/exposure stackers) reads picked content and how its
   results come back. A **parallel track to the Edit phase**: the user can
   use both tracks on the same event.
3. **Event creation** — the live-trip incremental flow and the backfill
   flow ("bring my old events in"), both defined by the folder model.

Plus one negative decision that simplifies everything: **there is no phase
lifecycle model** (§5).

## 1. The folder model

An event folder contains exactly these named things, plus `event.db` (+
backup) and internal caches:

| On disk | Role | Stratum |
|---|---|---|
| **`Original Media/`** | the captured tree — sacred, byte-pristine | D — irreplaceable |
| **`Original Media/Merged/`** | adopted stack masters (§2.3) — additive-only carve-out | D — irreplaceable |
| **`Edited Media/`** | Edit's export target; external editors return work into a subdir | D — outputs |
| **`Cuts/<cut name>/`** | Share handoffs (hardlinks for PTE; was "04 - Cuts") | derived — re-exportable |
| **`Picked Media/`** | the external tools' doorway — links projection of Pick state (§2) | derived — regenerable* |

\* …except tool outputs sitting at its root awaiting ingest (§2.3). Any
rebuild/cleanup must preserve real files, always.

Locked rules:

- **Folder names are fixed English on disk**, regardless of app language;
  the UI refers to them through `tr()`. No rename-on-language-switch, ever;
  paths in `event.db` stay stable.
- **The intermediate phase dirs retire** (`01 - Culled/`, `02 - Selected/`,
  numbered prefixes generally). Nothing in the rebuilt pipeline writes
  bytes between the ends (spec/56 locked "nothing materialises before
  Export"); decisions live in `phase_state`, not in folders. The charter's
  "tree is a projection" bullet becomes literal: the only projection left
  IS `Picked Media/`, and it is honest about being one.
- **The database handles all intermediate state.** If a feature seems to
  need a new folder, it first has to fail to be a table.

## 2. `Picked Media/` — the external doorway

External software cannot read `event.db`; the Pick decisions therefore get
ONE filesystem projection, built from links (hardlinks; same volume as the
event root by construction).

### 2.1 Shape

- **Flat root** of links to every picked item, named with a deterministic
  prefix: ``D03_G9M2_P1000001.RW2`` (day + camera + original filename).
  Collision-free by construction, sorts by day in any tool, and the stem
  itself identifies the source for the return leg (§3.2).
- **One subdir per focus/exposure bracket**, holding links to the bracket
  members the user actually picked (possibly a subset of the original
  cluster). **Member links exist only in the subdirs, never at the root** —
  the root is for whole items and merged results.

### 2.2 Lifecycle

- Built when the user **enters Edit**, plus a manual **Refresh links**
  action for later re-picks. No churn during Pick; no background watchers.
- Rebuild may be brutal (wipe + recreate from the database) **except** for
  real files at the root (§2.3) — those are never touched by a rebuild.

### 2.3 Stacker outputs — the one exception

> **Refined by [spec/72](72-third-party-roundtrips.md) §2 (2026-06-14):** the
> root-file→bracket match is a small **confirm step** ("Review merged results");
> on adopt, the bracket **collapses to its merged master** and the member frames
> are **auto-Skipped**; the master carries an item-level consolidation badge. No
> schema change (`provenance='stack_output'` + `StackBracket.output_item_id` /
> `action='stacked'` already exist).

Stack consolidation **never happens in Mira** — always in external
tools. The round trip:

1. The stacker consumes a bracket subdir as input.
2. It writes its merged result to the **root of `Picked Media/`** — at that
   moment, the only real bytes in a folder of links.
3. On ingest, the result is **adopted into `Original Media/Merged/`** (it
   is, in effect, a new master: it flows into Edit like a captured photo)
   and recorded as the bracket's final item
   (``provenance='stack_output'``, ``stack_bracket.output_item_id`` — the
   schema's existing receiving end).
4. **Continuity is seamless:** the instant it is adopted, a link to it
   stands at the picked root — the user (and LRC) never see the file
   disappear; it transitions from loose bytes to a linked, picked master.

This is the single sanctioned addition to the captured tree (charter
amendment): `Merged/` is **additive-only**; card-derived subtrees remain
byte-pristine and untouchable, and the SD-wipe gate remains the only
sanctioned deletion.

## 3. The external round trip

A **parallel track to Edit** — develop in Mira, in LRC, or both.

### 3.1 The editor leg (LRC-class)

Out: the user points LRC at `Picked Media/`. Back: the user exports from
LRC **into a subdir of `Edited Media/`**. Mira ingests those files
as edited outputs of their source items — they compose with
versions-as-exports (spec/54 §8): an LRC edit and a Mira edit of the
same photo coexist as versions the Cut picker chooses between.

### 3.2 The return match

A returned file associates to its source when its stem **starts with a
link's stem** (``D03_G9M2_P1000001*``) — unambiguous because prefixes are
unique by construction, and it survives LRC's appended suffixes
("-Edit", "-2"). Files matching nothing are **flagged in a small report —
never silently ignored.**

### 3.3 Discovery

**Scan on surface entry + an explicit scan action.** Entering Edit (or the
relevant page) scans for new files — stacker outputs at the picked root,
editor returns in `Edited Media/` — and a button does the same on demand.
No filesystem watchers.

### 3.4 Reminders (derived, dismissible)

On entering Edit (and on link refresh), the system may surface **computed
facts**, e.g. *"3 picked brackets have no merged result yet — best to run
your stacker before editing."* These are derived from current database
state at a concrete moment — never stored to-do flags, never walls.

## 4. Event creation

### 4.1 The live trip (incremental)

- Create the event **empty** — no plan days required. **The plan is a
  product of Collecting**: each Collect run creates the day(s) it covers.
- A single-date run ingests straight through. A **multi-date run shows the
  proposed day split** (dates + counts) for confirmation first — the moment
  to pull 00:30 night shots into the previous evening's day — then prompts
  metadata per new day.
- With no phone in the run, the user sets **day location, TZ and
  description manually**; the camera-TZ reminder fires at each Collect.
- A **late phone Collect** (end of trip) reconciles against the
  day-by-day plan: phone EXIF TZ that *matches* is silent; a mismatch
  prompts for correction. The plan the user built is never silently
  overridden.
- Daily full-cycle use (Collect → Pick → Edit every evening, to catch a
  wrong camera setting on day 1 instead of at home) is a first-class
  pattern — nothing gates Edit on "finishing" Pick (§5).

### 4.2 The single-day TZ fix

The plan editor's day rows gain a **post-ingest unlock for one day's TZ**,
with an explicit "this re-times day N's photos and may move some across
days" confirmation. One surface owns all day metadata; `camera_day_tz`
already carries the per-(camera, day) grain, and corrected time is a
read-time projection, so the fix is cheap and reversible.

### 4.3 Backfill — "create event from …"

One flow, three landing levels, for bringing old events into the base:

| Entry | What runs automatically | User lands at |
|---|---|---|
| **from Collected media** | Collect (Quick Sweep optional) | Pick |
| **from Picked media** | + all media written `picked` | Edit |
| **from Edited media** | + Edit treated as done (no re-render) | Share |

- The system **works backwards**: media copied into `Original Media/`,
  database rows and phase states written **as if the phases had run in
  order** — the event is indistinguishable from one that lived its whole
  life in Mira. Phone EXIF, when present, fills country/TZ.
- **From-Edited takes ONE folder, and it is both**: the files copy into
  `Original Media/` AND are treated as the edited output. Simplest
  possible ask; no separate originals required.
- The bar: **simple and flawless** — this is a first-contact flow; one bad
  experience and the user never comes back.

### 4.3.1 Slice-5 locked decisions (Nelson, 2026-06-10 checkpoint)

- **One menu entry.** "New event from photos" is absorbed: the entry becomes
  **"New event from existing media…"** and its first step asks the landing
  level ("Where does this media stand?"). From-Collected IS the old flow —
  now with **auto-Collect**: the wizard runs the same ingest gate + engine
  Collect uses (the Quick Sweep offer included, from-Collected level only)
  instead of parking the new event for a manual Collect pass.
- **from-Edited bytes.** The one folder copies into `Original Media/` as
  the master AND the same bytes stand under `Edited Media/` as hardlinks
  (copy fallback cross-volume). `lineage` rows point at the `Edited Media/`
  placement — `phase='edit'`, `recipe_json` NULL (the external-return
  shape), `exported_at` = backfill time — so the Cut picker and the folder
  tree read exactly like an event that lived its whole life in Mira.
  Placement: one fixed-English subdir **`Edited Media/Imported/`**
  (same-name finals divert to `name (2).ext`, like ingest). Both phases
  get explicit picked states — the in-app mirror of "Pick and Edit ran".
- **Landing surfaces.** from-Collected → Pick; from-Picked → Edit (the
  entry seams fire as usual). from-Edited → the event dashboard while the
  Cuts page is the placeholder; switches to the real Share surface when
  the Cuts rebuild lands.
- **Cancel posture.** Cancel before event creation = clean no-op (no
  orphaned records). Cancel at the ingest gate leaves the created event
  (plan baked, no media) and lands on its dashboard — Collect runs later
  from there.
- **Backfill duplicates (2026-06-10, first-run fix).** A source carrying
  the same file in several subtrees (a legacy event folder's captured +
  selected copies) ingests it **once** — identical bytes at one
  destination = one item; the completion box reports *"N duplicate(s)
  ingested once"*. Same-name files with **different** bytes both survive
  via `name (2).ext` diversion; an already-ingested copy is never
  overwritten (invariant #7), and re-running an interrupted ingest keeps
  in-place copies and records their rows.
- **Name collisions refused.** Creating an event whose folder already
  exists under the photos base is refused with a clear message —
  `materialise_event` would otherwise delete the existing `event.db` and
  orphan its index card.

## 5. Phase lifecycle — there is none

- No per-phase closed bit, no closure criteria, no reopening semantics.
  **Phases are surfaces the user visits; the user manages their own
  progress.**
- The system's help is the **breadcrumb trail**: the existing per-(item,
  phase) visited ticks, extended wherever "where have I been" matters,
  with "Start a new pass…" to wipe.
- Reminders are **derived facts at concrete moments** (§3.4) —
  dismissible, computed fresh, never stored state and never gates. The
  `derived_dirty` machinery continues to mark downstream work stale when
  upstream decisions change; that is all the cross-phase bookkeeping there
  is.

## 6. What this retires (cleanup inventory)

- `01 - Culled/` + `02 - Selected/` dir creation and every numbered-prefix
  folder name (`00 - Captured` → `Original Media`, `03 - Processed` →
  `Edited Media`, `04 - Cuts` → `Cuts`).
- Any internal stack-merging remnants (consolidation is external-only;
  Mira builds inputs and ingests outputs).
- spec/52's plan-first assumption wherever it survives in code (PlanDialog
  remains; days now also arrive via Collect runs).

## 7. Open (deferred, explicitly)

1. The exact `Picked Media` link-prefix grammar for edge cases (undated
   items, phone-only days) — implementation-time within this design.
2. The unmatched-returns report's surface (dialog vs. inline notice) —
   implementation-time.
3. Cross-volume events (hardlinks impossible) — copy fallback for the
   picked projection; design the warning when it triggers.

## 8. Implementation slices (when Nelson pulls the trigger)

1. **Folder model** — fixed-English names, `Original Media`/`Edited
   Media`/`Cuts` renames, kill 01/02 creation, `Merged/` adoption path,
   charter/CLAUDE sync. (Nelson: "we probably have to start the
   implementation from here.")
2. **Picked Media projection** — link builder (flat root + bracket
   subdirs, deterministic prefixes), build-on-Edit-entry + Refresh action,
   rebuild-preserves-real-files rule.
3. **Return seams** — scan on entry + scan action; stacker-output adoption
   (with seamless re-link) + bracket-final item creation; editor-return
   association (starts-with rule) + unmatched report; derived
   unmerged-brackets reminder.
4. **Incremental Collect** — day creation from runs, multi-date
   auto-split + confirm, per-day metadata prompts, late-phone TZ
   reconciliation, plan-editor single-day TZ unlock.
5. **Backfill wizard** — the three landing levels over the same engine,
   from-Edited one-folder rule.

Targeted tests per slice. Sequencing against spec/56 slices 3–5 (Edit
workshop, Export, cleanup) is Nelson's call at the next checkpoint.
