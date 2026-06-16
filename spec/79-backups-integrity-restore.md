# spec/79 — Backups, integrity & restore (backlog B7)

**Status:** written 2026-06-16 for a full-access coding agent. Closes the last
pre-freeze design gap before the permanent 30-year production library. Goal: a
local-only, offline-first safety net so an `event.db` (or the user-data store)
that gets corrupted, half-written, or fat-fingered can always be recovered.

Read first: `spec/00-charter.md` invariant #6 (atomic write-then-rename), #3
(no network — backups are **local-only**, never cloud), #4 (no telemetry).
Build on what exists; do not reinvent.

**Build on (don't duplicate):**
- `core/atomic_journal.py` — already does atomic write-then-rename + **last-N
  history rotation** + SHA-256 sidecars + `list_history()` for JSON state. The
  rotation + restore pattern here is the template.
- `core/event_backup_card.py` — byte-exact media offload + SHA-256 manifest +
  `verify_offload()` pass/fail. The integrity-verify pattern.
- SQLite is **WAL** (`mira/store/schema.py:652`). A plain file copy of an open
  WAL db is unsafe — use the **online backup API** (see §2).

---

## §1. What needs protecting
1. **Per-event `event.db`** — the decision ledgers, lineage, cut membership,
   classification. The crown jewels; losing one loses an event's curation.
2. **User-data store** — settings, library index, templates, audio library refs
   (whatever lives outside the per-event DBs).
3. *(Out of scope — already covered)* the captured/exported **media** itself:
   the originals tree is append-only + offload-verified (`event_backup_card`),
   and Export materialises from the DB, so media is not the fragile part.

## §2. Backups — use the SQLite online backup API
Never `shutil.copy` an open WAL database. Snapshot via the online backup API:

```python
src = sqlite3.connect(event_db_path)
dst = sqlite3.connect(backup_path)        # a fresh file
with dst:
    src.backup(dst)                        # consistent point-in-time copy
dst.close(); src.close()
```

- Write the backup with the project's **atomic** pattern (`<name>.tmp` →
  `os.replace`), mirroring `atomic_journal`.
- **Rotation:** keep the last **N** snapshots per database (suggest N=5),
  pruning oldest — reuse the `atomic_journal._prune_history` approach.
- **Location:** `<library_root>/.mira-backups/<event_id>/<UTC-timestamp>.db`
  (and `.../user-store/<UTC-timestamp>.db`). Library root from
  `settings`/`paths.py` (invariant #2). Keep backups **inside the library** so
  they ride the NAS snapshots (spec/76 §D) — but never inside the captured tree
  (invariant #7).
- Tag each snapshot with a tiny side-car: `app_version`, `schema_version`,
  `sha256`, `created_at` — so restore can warn on a version mismatch.

## §3. When a backup runs
- **On clean shutdown** of an event (gateway close) **if the db changed** this
  session — the common, cheap case.
- **Before any risky operation**: schema migration, a bulk import recording
  pass, and — explicitly — **before the production "delete all / start fresh"
  wipe** Nelson plans. A pre-destructive snapshot is the seatbelt.
- Backups run **synchronously but fast** (the backup API on a small db is ms);
  if a db is large, run it off the GUI thread. No scheduler needed for v1.

## §4. Integrity check
- On opening an `event.db`, run `PRAGMA quick_check` (fast) — full
  `PRAGMA integrity_check` only on demand / in a "verify library" action.
- On failure: do **not** silently proceed. Surface a clear dialog (reuse
  `mira/ui/design/dialogs.py`): "This event's database is damaged" + offer
  **Restore from the latest good backup** (§5) or open read-only.
- A library-wide **"Verify & back up"** action (Help/Library menu) runs
  `quick_check` + a fresh snapshot across all events — the user runs this before
  the big import or any milestone.

## §5. Restore path
- Restore = pick a snapshot (default: latest) for an event/user-store, validate
  its `sha256` + `quick_check`, then atomically swap it in (back up the current
  damaged file first, to `.mira-backups/.../corrupt-<ts>.db`, so a restore is
  itself reversible).
- Surface it from the integrity-failure dialog (§4) **and** a manual
  "Restore from backup…" entry (shows the snapshot list with timestamps +
  version, like `atomic_journal.list_history`).
- Version-mismatch (snapshot `schema_version` < current): restore the data,
  then run the normal migration path; warn the user.

## §6. Module shape
- `core/db_backup.py` (pure-ish, no Qt): `snapshot(db_path, backups_dir,
  keep=5) -> Path`, `list_snapshots(db_path) -> list[SnapshotInfo]`,
  `verify(snapshot) -> bool` (sha + quick_check), `restore(snapshot, db_path)`.
- Gateway wiring: call `snapshot` on close-if-dirty + before risky ops; call
  `quick_check` on open.
- UI: the integrity dialog + a "Restore from backup…" / "Verify & back up"
  action. `tr()` all strings.
- Tests (`tests/test_db_backup.py`, no Qt): snapshot a temp db → restore
  round-trips; rotation prunes to N; a deliberately-corrupted file fails
  `verify`; restore backs up the corrupt original first.

---

## §7. Minimal subset for the freeze (do this much before the production run)
Enough to make the 30-year library safe without building everything:
1. `core/db_backup.py` snapshot + rotation (§2).
2. Backup **on event close-if-dirty** + **before the delete-all wipe** (§3).
3. `quick_check` on open with the restore-or-readonly dialog (§4).
4. Restore from latest snapshot (§5).
The richer pieces — full library "Verify & back up" action, version-mismatch
migration, a secondary backup location — can be fast-follows after freeze.

## §8. Definition of done
- `verify.bat` green incl. `test_db_backup.py`.
- Closing a changed event writes a rotated snapshot under `.mira-backups/`.
- Opening a corrupted `event.db` is caught by `quick_check` and offers restore;
  restore round-trips and backs up the corrupt file first.
- The production wipe takes a pre-destructive snapshot.
- Everything local-only (no `socket`/`urllib`/etc.), atomic writes, `tr()`'d.

## §9. Interplay
- **#6 atomic write-then-rename** — every snapshot + restore swap uses it.
- **#3 offline-first** — backups are pure filesystem; flag any network idea as a
  charter decision.
- **spec/76** — backups live inside the library, so the NAS's RAID/snapshots add
  a second redundancy layer; but "RAID isn't a backup" — these versioned
  snapshots are the real restore path.
- **#7 captured tree untouched** — backups go in `.mira-backups/`, never in the
  originals tree.
