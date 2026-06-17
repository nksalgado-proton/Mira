# spec/82 — Backup, restore & event migration

**Status:** design draft, 2026-06-17 (design session with Nelson). Extends
[`spec/79`](79-backups-integrity-restore.md) (the corruption-recovery base) with
two things spec/79 deliberately left out: **time/milestone-based snapshots** and
**user-driven event migration between installations**. spec/79 stays the
authority on the snapshot primitive, integrity check, and in-place restore; this
spec adds triggers and the migration feature on top.

Read first: `spec/79` (backup primitive + restore), `spec/00-charter.md`
invariants #2/#3/#4/#6/#7, `spec/57` (event folder model), `spec/76 §A` (the
writer lock).

**Build on (don't duplicate):**
- `core/db_backup.py` *(spec/79 §6 — to build)* — the SQLite online-backup-API
  snapshot + rotation + verify + restore primitive. Both features below call it.
- `core/event_backup_card.py` — byte-exact media copy + SHA-256 manifest +
  `verify_offload()`. The media half of a migration bundle reuses this.
- `core/atomic_journal.py` — atomic write-then-rename + last-N rotation template.

---

## Part A — Database safety snapshots (extends spec/79)

Automatic, **DB-only**, internal, rotated — the corruption seatbelt. spec/79
defines the primitive and in-place restore; this part only widens **when a
snapshot fires** and how retention copes with frequent snapshots.

### A.1 Triggers (spec/79 §3 + new)
Existing (spec/79): on **clean event close if the db changed** this session;
**before any risky op** (schema migration, bulk import, the delete-all wipe).

New here:
1. **Per-day-add milestone.** After a day's ingest completes, snapshot the
   event.db. This is the natural rollback point for the trip workflow ("adding
   days one at a time") — every added day becomes a recoverable checkpoint.
2. **Periodic while open.** Every **N minutes** (suggest N=15) snapshot the
   open event.db **only if it changed** since the last snapshot. Crash/battery
   insurance for long sessions that never reach a clean close. Runs **off the
   GUI thread**; skipped entirely when the db is clean.

> WAL recovery already rebuilds the *live* file to its last committed state after
> a crash; snapshots are the separate *rollback-to-known-good* line. Periodic +
> per-day-add tighten the second line so it is never hours stale.

### A.2 Retention — two classes, so periodic churn never evicts milestones
A single keep-last-N (spec/79's N=5) would let a long session's periodic
snapshots flush out the good milestone copies. Split retention by a `reason`
field in the sidecar:

| Class (`reason`) | Sources | Keep |
|---|---|---|
| **milestone** | close-if-dirty · pre-risky-op · per-day-add · manual | last **10** |
| **periodic** | the N-minute timer | last **3** |

Prune each class independently. Everything else (location, sidecar fields,
atomic write) is exactly spec/79 §2.

### A.3 User-data / "global" store
The user-data store (settings, library index, templates) uses the **same
`db_backup` primitive**: snapshot on app close-if-changed + before any settings
migration; restore from a Settings/Help menu entry. Location
`<library_root>/.mira-backups/user-store/<UTC-ts>.db`. No periodic timer needed —
it changes rarely.

### A.4 Integrity + restore
Unchanged from spec/79 §4–§5: `quick_check` on open → on failure a dialog
offering **Restore from latest good backup** or **open read-only**; restore
verifies sha + quick_check, backs up the corrupt file to `corrupt-<ts>.db`
first, then atomically swaps. A manual "Restore from backup…" lists snapshots
with timestamp + reason + version.

---

## Part B — Event migration (user-driven transplant)

**Locked scope (Nelson 2026-06-17):** *simple, user-driven, one event, one
direction* — migrate a single event from one Mira installation to another (the
trip case: collect on the notebook day-by-day, then move the whole event to the
main PC). **Not** a sync engine, **not** incremental, **not** two-way. Decisions
from this session:

- **Whole-event transplant, one-way.** The event lives on the notebook for the
  whole trip; at the end the entire event moves to the main PC as one snapshot.
- **Bundle contains everything, verbatim** — `Original Media/`, `Edited Media/`,
  `Exported Media/`, `Cuts/`, the `.cache/` tiers, and a **consistent DB
  snapshot** (not the live WAL file). Caches travel so the main PC browses
  instantly with no rebuild.
- **Self-contained folder + manifest** — no single-archive-file; robust for
  tens-of-GB RAW events, resumable, no archive size limit.

### B.1 Two verbs
- **Back up event…** (export) — write the bundle to a user-chosen destination.
- **Restore event…** (import) — read a bundle into this installation's library.

Both are explicit menu actions. No automation.

### B.2 Back up event… (export)
1. User picks the event + a destination folder (file dialog; remember last-used
   as a convenience default — **never** a hardcoded path, invariant #2).
2. Snapshot the event.db via `db_backup.snapshot` (online backup API) — **never
   copy the open WAL file** (spec/79 §2).
3. Copy the whole event folder **verbatim** into `<dest>/<event-folder>.partial/`
   (the captured tree is read-only on the source — invariant #7 untouched),
   placing the **snapshotted** db as `event.db`, not the live one.
4. Write `mira-event.json` at the bundle root: `event_uuid`, `event_name`,
   `app_version`, `schema_version`, `created_at`, total file count + bytes, and a
   **per-file SHA-256 list** (reuse `event_backup_card`'s manifest shape).
5. **Verify** the copy by re-hashing against the manifest (`verify_offload`
   pattern). On pass, `os.replace` the `.partial` dir → final name (invariant
   #6); on fail, leave `.partial` and report. An interrupted copy is never
   mistaken for a complete bundle.

### B.3 Restore event… (import)
1. User points at a bundle folder (or it is auto-offered when a removable drive
   with a `mira-event.json` is mounted).
2. **Integrity gate:** verify every file's SHA-256 against the manifest **and**
   `quick_check` the bundled db. Refuse on any mismatch.
3. **Version gate:** bundle `schema_version` **>** local → refuse: "Update Mira
   on this PC first" (notebook newer than main PC). Bundle `schema_version`
   **<** local → copy in, then run the normal migration path; warn.
4. **Identity check** by `event_uuid` against the local library index:
   - **Absent → fresh transplant.** Copy the event folder into the local library
     base (atomic `.partial` → rename), register it in the library index. Done.
   - **Already present → Replace or Cancel** (no silent merge — out of scope).
     Replace takes a Part-A safety snapshot of the existing local copy first,
     then swaps the folder wholesale. Reversible via that snapshot.
5. The importing Mira holds the library **writer lock** (spec/76 §A) — import is
   a write into the local library, so it is gated by the lock like any mutation.

### B.4 Ownership after migration
After a transplant the **destination copy is authoritative**. The source
(notebook) copy is a spent backup — editing both diverges them, and the writer
lock is per-library/per-machine, so nothing cross-checks them. Surface a gentle
one-time note on successful export ("keep this as a backup; continue working on
the main PC"); do not try to enforce.

### B.5 Module shape
- `core/event_bundle.py` (no Qt): `export_event(event_root, event_db_path,
  dest_dir) -> BundleResult`, `verify_bundle(bundle_dir) -> VerifyResult`,
  `read_manifest(bundle_dir) -> BundleManifest`, `import_event(bundle_dir,
  library_base) -> ImportResult`. Composes `db_backup.snapshot` (the db) +
  `event_backup_card` (media hash/copy/verify).
- Library index (gateway / user-store): `find_event(uuid)` + `register_event`.
- UI: **Back up event…** / **Restore event…** menu entries, a progress dialog
  for large copies, the verify-result surface, and the identity/replace +
  version-gate dialogs. `tr()` all strings.

---

## Part C — How A and B fit together
One primitive, two products. `db_backup.snapshot` (Part A) produces the
consistent db for **both** an internal `.mira-backups` safety snapshot **and**
the `event.db` inside a migration bundle (Part B). `event_backup_card` supplies
the verified media copy that the bundle adds on top. A safety snapshot is
*DB-only, internal, automatic, frequent*; a migration bundle is *whole-event,
external, manual, verified, rare*. Restore likewise has two faces: roll an event
back in place (Part A) vs. bring an event in from another installation (Part B).

---

## Part D — Invariants
- **#2 no hardcoded paths** — destination is user-chosen; library base from
  `settings`/`paths.py`.
- **#3 offline-first** — pure filesystem; no `socket`/`urllib`/etc. anywhere in
  either feature.
- **#4 no telemetry** — failures log locally only.
- **#6 atomic write-then-rename** — every snapshot, bundle finalisation, and
  import swap uses `.partial`/`.tmp` → `os.replace`.
- **#7 captured tree untouched** — export reads the source tree read-only;
  import writes a fresh tree; Replace snapshots-then-swaps wholesale, never
  mutates an existing captured tree in place.

---

## Part E — Sequencing (slices, each its own commit + `verify.bat`)
1. **`core/db_backup.py`** — snapshot + two-class rotation + sidecar + verify +
   restore, with tests (spec/79 §7 minimal subset).
2. **DB triggers + integrity** — close-if-dirty, pre-risky-op, **per-day-add**;
   `quick_check` on open + restore-or-read-only dialog; "Restore from backup…".
3. **Periodic-while-open** snapshot (if-dirty, off-thread) + the periodic
   retention class.
4. **User-store** snapshot + restore (reuses slice 1).
5. **`core/event_bundle.py` export** — snapshot + verbatim copy + manifest +
   verify + `.partial` finalisation, with round-trip tests.
6. **`core/event_bundle.py` import** — integrity + version + identity gates +
   register + Replace/Cancel, with tests (tampered file refused, schema-newer
   refused, existing-uuid → replace, partial bundle ignored).
7. **UI** — Back up event… / Restore event… menu entries, progress + dialogs.
8. **Backups settings tab** (Part G) — new model fields + `core/settings.py`
   defaults + the `SETTINGS_SCHEMA` tab; reconcile `default_ssd_path` and
   `backup_on_quit_*`; make Parts A & B read their cadences/counts/destinations
   from settings instead of the hardcoded suggestions above.

Slices 1–4 are the corruption seatbelt (Part A); 5–7 are migration (Part B);
slice 8 puts both under user control. Part A slices alone satisfy the pre-freeze
safety goal; Part B + the tab can follow. (Wire each Part to read settings as it
is built — slice 8 is the consolidation + tab UI, not the first time settings
are introduced.)

## Part G — Settings: a dedicated **Backups** tab

All cadences, counts, and destinations live on a new **Backups** tab in the
settings dialog (`SETTINGS_SCHEMA` in `mira/ui/base/settings_dialog.py`) — the
same schema-driven pattern as every other tab (`folder` / `checkbox` / `spinbox`
widgets). New fields land on `mira/settings/model.py` via the `_u(...)`
user-visible helper, mirrored in `core/settings.py` defaults.

| Setting (model key) | Widget | Default | Notes |
|---|---|---|---|
| `backup_snapshots_enabled` | checkbox | `True` | Master toggle for automatic DB safety snapshots (Part A). The seatbelt — on by default. |
| `backup_periodic_minutes` | spinbox | `15` | Periodic-while-open cadence (A.1). **`0` = off** (milestone snapshots still run). Range 0–120, suffix " min". |
| `backup_keep_milestone` | spinbox | `10` | Retention for the milestone class (A.2). Range 1–50. |
| `backup_keep_periodic` | spinbox | `3` | Retention for the periodic class (A.2). Range 1–20. |
| `backup_snapshots_root` | folder | `""` | Optional override for where safety snapshots live. **Blank = `<library_root>/.mira-backups`** (spec/79 default, rides NAS snapshots). Set it to a *different* drive for true offsite of the DB. |
| `event_backup_destination` | folder | `""` | Default destination for **Back up event…** (Part B) — your external drive. Pre-fills the export dialog; still confirmable each time (invariant #2 — a default, not a frozen path). |
| `event_backup_verify` | checkbox | `True` | Re-hash the bundle against its manifest after copy (B.2 step 5). On by default; can be turned off to skip the verify pass on very large events. |

**Legacy reconciliation (do this on the same pass):**
- `default_ssd_path` ("Default backup SSD") → **rename/repurpose to
  `event_backup_destination`** and move onto the Backups tab. It already means
  "default external backup destination"; this is the same intent, now wired to
  the real feature. Migrate the stored value.
- `backup_on_quit_enabled` / `backup_on_quit_root` (the legacy incremental
  event-mirror on quit) → **rehome onto the Backups tab and re-point at the
  Part-B export primitive**, so "mirror the active event on quit" becomes an
  automatic Part-B bundle export to `event_backup_destination` rather than the
  old ad-hoc mirror. (Alternative: retire it entirely in favour of the explicit
  **Back up event…** action — Nelson's call; see Part F.) Either way it must not
  remain a parallel, separately-coded backup path.
- The existing **Paths** tab keeps only true path config; everything
  backup-related migrates to the new tab so there is one obvious home.

## Part F — Open questions
- **Backup-on-quit fate** — rehome the legacy auto-mirror onto the Backups tab
  (re-pointed at the Part-B export), or retire it for the explicit **Back up
  event…** action? (Part G.) **Resolved 2026-06-17:** rehome — both ship
  (automatic and manual), the auto path is the slice-5 export pointed at
  `event_backup_destination`.
- **Bundle auto-discovery** — should mounting a drive with a `mira-event.json`
  prompt a restore, or is it menu-only? (B.3 step 1.) **Resolved 2026-06-17:**
  **menu-only.** No polling, no auto-popups. The user explicitly clicks
  **Restore event…** and picks the bundle folder via a file dialog. Simpler, no
  false positives from leftover or test bundles on a drive, matches the
  deliberate one-way trip workflow Nelson described.
- **Schema-equal, app-version-different** — allow import freely, or note it?
  (Likely allow; `schema_version` is the real gate.)

*(Resolved here: periodic cadence and the last-used destination are now settings,
not hardcoded — Part G.)*
