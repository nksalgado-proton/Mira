# Build brief — single-writer lock: close §A, then read-only mode (§B.1)

**For:** a full-access coding agent. **Authored:** 2026-06-17.
**Governing spec:** [`spec/76-home-library-and-cut-publishing.md`](spec/76-home-library-and-cut-publishing.md) §A + §B.1. Read §A and §B.1 in full before any code.

> **This is NOT a from-scratch build.** The core primitive and most of
> the wiring already shipped in commit `520c339` ("spec/76 §A — Single-writer
> library lock"). Audit what exists first, then close the two gaps below.
> Do not rewrite working code to restyle it (view-over-engine).

---

## Already done (verify, don't rebuild)

- `core/library_lock.py` — complete, pure-logic + filesystem, no Qt:
  `acquire` / `refresh` / `release` / `read_holder` / `is_stale`, `LockInfo`,
  `LockResult`. Advisory `.mira-writer.lock` at the library root, JSON payload,
  atomic write-then-rename, heartbeat, staleness judged on filesystem **mtime**
  (clock-skew defence).
- `tests/test_library_lock.py` — 16 tests covering fresh acquire, second-acquire
  declines, stale takeover, release, heartbeat, corrupt/non-object payloads,
  no-Qt assertion.
- `mira/ui/app.py` — `acquire` in a retry loop at startup, the heartbeat
  `QTimer`, and the conflict dialog (`_show_lock_conflict_dialog`).

Confirm all of the above still passes `verify.bat tests\test_library_lock.py`
before changing anything.

---

## Hard rules

- `core/` never imports from `mira/ui/`; the lock primitive stays Qt-free.
- No network calls — filesystem-only (charter invariant #3). The lock must not
  introduce `socket` for anything beyond `gethostname()`.
- No hardcoded paths — the library root comes from `settings` / `mira/paths.py`
  (invariant #2). Reuse `_resolve_library_root` in `app.py`.
- Atomic write-then-rename for any persisted state (invariant #6) — already used
  by the lock module; keep it.
- Every user-visible string passes through `tr()`.
- Run `verify.bat` after **each** slice; commit per slice.

---

## Slices (in order, each its own commit)

### SLICE 1 — Release the lock on clean exit (closes §A.6 gap)

Today nothing calls `library_lock.release(library_root)` on shutdown, so a clean
exit leaves the lock file behind and the next launch only recovers via the
5-minute staleness path. Wire `release` to `QApplication.aboutToQuit` (or the
MainWindow close path) so a normal quit removes the lock immediately. Stop the
heartbeat `QTimer` in the same teardown. Keep crash-recovery intact (staleness
takeover already handles the unclean case). Add a test or a manual check that a
clean quit removes `.mira-writer.lock`.

### SLICE 2 — Read-only library mode (spec/76 §B.1)

When `acquire` returns `acquired=False` with a live holder, open the library
**read-only** instead of declining. Read-only means: decision verbs (Pick/Skip),
Edit writes, Export, and event-header/plan saves are all no-ops, greyed with a
quiet "read-only" hint (spec/05 hint grammar). A persistent banner names the
editing machine (`holder.hostname`, `holder.acquired_at`). Define one read-only
flag on the app/session that surfaces consult; do not scatter per-widget guards.

### SLICE 3 — Upgrade the conflict dialog to the §A.4 contract

Replace the interim "Retry / Cancel" with the spec/76 §A.4 dialog: *"This library
is open for editing on {hostname} (since {time}). Opening in read-only mode."*
with **Open read-only** (→ slice 2 mode) and **Cancel**. The **Take over editing**
button is only relevant for a stale holder — but `acquire` already auto-takes-over
stale locks, so it never reaches this dialog. Confirm that behaviour and either
drop the button or, if Nelson wants an explicit prompt before stale takeover,
surface it here (a spec question — raise it, don't guess).

---

## Per-slice loop

After each slice: `verify.bat`, launch the app to eyeball (open a second instance
to exercise the conflict path), then commit.

## Definition of done

`verify.bat` green including `test_library_lock.py`; clean quit removes the lock
file; a second instance opens read-only with a banner naming the writer; mutations
are disabled in read-only mode; offline-first preserved (no new network imports).

## Open questions (raise to Nelson, update the spec — don't let code drift)

- **Stale takeover prompt** — silent auto-takeover (current behaviour) vs. an
  explicit "Take over editing" confirmation (spec/76 §A.4 implies the latter).
- **Read-only coverage** — confirm the full list of mutating surfaces to gate
  (decision verbs, Edit, Export, event-header, plan editor, day management) so
  none is missed.
- **§B.2/B.3 sequencing** — NAS validation + Cut publish target are the next
  slices after this (spec/76 §E); out of scope here unless Nelson pulls them in.
