# spec/76 — Home library, single-writer lock & Cut publishing

**Status:** written 2026-06-16 from a design session with Nelson. Captures the
**home multi-device model** for Mira and specs the **single-writer lock** as a
near-term v1 deliverable (Nelson: "the single-writer lock is important right
now"). The rest is the recorded target so v1's foundations don't box it out —
built incrementally after the production freeze, not before.

**One-line model:** the library lives on the NAS; **exactly one machine opens it
read-write** (holds a lock), every other machine opens it **read-only**, and
**Cuts are published as files** that an off-the-shelf home-media server streams
to the smart TVs. The entire model is **filesystem-only — no sockets, no server
process, no network calls** — so it stays inside charter invariant #3
(offline-first). The only networking in the house is the NAS share itself and
whatever media server the TVs already use.

```
  Smart TVs (living room · kitchen · bedroom)
        ▲  stream / display
  Home media server  (DLNA / Jellyfin / web slideshow — NOT built into Mira)
        ▲  reads files, never the database
  Published Cuts  (materialised exports on the NAS + a manifest; spec/61 already
        ▲          materialises Cuts to files)
        │  writer publishes
  Mira workstation (READ-WRITE, holds the lock)   |   Other PCs (Mira READ-ONLY)
        │  writes                                       │  reads only
        ▼                                               ▼
  NAS shared drive — the one library: per-event SQLite DBs + captured/exported
  media + the single-writer lock file at the root.
```

Why files-not-DB for readers: SQLite over SMB/NFS is unreliable for concurrent
**writes** (network lock semantics), and you never want N smart TVs touching a
live DB. The single-writer rule removes write contention; TVs read **published
files**, so they never open the DB at all.

---

## §A. Single-writer lock — BUILD NOW (v1)

Goal: guarantee that at most one Mira process holds the library read-write, so
two machines (or the same PC opened twice) can never corrupt the store. This is
valuable even on a single PC today.

### A.1 Where the lock lives
One advisory lock file at the **library root** (the base that holds all events +
the user-data store — resolved via `core/settings.py` / `mira/paths.py`, never a
hardcoded path, per invariant #2): `<library_root>/.mira-writer.lock`.

It is library-wide, not per-`event.db` — a writer owns the whole library for the
session.

### A.2 Advisory lock, not OS file-locking
Do **not** rely on `flock`/`LockFileEx` — their semantics are unreliable over
SMB/NFS, which is exactly where this runs. Use an **advisory lock file with a
heartbeat**:

- **Lock file contents** (JSON): `hostname`, `pid`, `app_version`,
  `acquired_at`, `heartbeat_at`. Write it with the project's **atomic
  write-then-rename** (invariant #6) so a reader never sees a half-written file.
- **Acquire** (on opening the library, app startup):
  1. If no lock file → create it (atomic) and proceed read-write.
  2. If a lock file exists and is **fresh** → another live writer owns it →
     open **read-only** (§B.1) and tell the user (§A.4).
  3. If a lock file exists but is **stale** (heartbeat older than the timeout) →
     the previous writer crashed; take it over (overwrite atomically) and
     proceed read-write.
- **Heartbeat:** while holding the lock, refresh `heartbeat_at` every ~30 s
  (a `QTimer` in the UI layer drives it; the primitive itself is in `core/`).
- **Staleness timeout:** ~5 minutes (generous, to ride out brief NAS hiccups).
  **Clock-skew caveat:** machines on a LAN may disagree on wall-clock time;
  prefer comparing against the lock file's **filesystem mtime** (a single NAS
  clock for all machines) over trusting the in-file timestamp, or keep the
  timeout generous. Document whichever the implementation picks.
- **Release:** delete the lock file on clean shutdown. On crash it's recovered
  via the staleness path above.

### A.3 Module shape
- `core/library_lock.py` (pure logic + filesystem, **no Qt**): `acquire(root)
  -> LockResult{acquired: bool, holder: LockInfo|None}`, `refresh(root)`,
  `release(root)`, `read_holder(root) -> LockInfo|None`, `is_stale(info, now)`.
- UI wiring (`mira/ui/shell/main_window.py` or app startup): call `acquire`
  when the library opens; start the heartbeat `QTimer`; `release` on close.

### A.4 Conflict UX
When the lock is held by a live writer, the second instance must not open
read-write. Final behaviour (Nelson 2026-06-17 confirmation):

- Show a clear dialog (reuse `mira/ui/design/dialogs.py`): *"This library is open
  for editing on **{hostname}** (since {time}). Opening in read-only mode —
  decisions, edits, exports and plan changes will be disabled in this window
  until the other Mira closes."* — with **Open read-only** (→ §B.1) and
  **Cancel**.
- **No "Take over editing" button.** `acquire` already auto-takes-over stale
  locks (`mtime` older than the 5-minute timeout in §A.2), so a stale holder
  never reaches this dialog — the button has no path to fire. The auto-takeover
  is silent except for a startup log line. *Originally drafted as conditional
  on a stale holder; dropped 2026-06-17 because the conditional branch is
  unreachable with the current `acquire` semantics, and Nelson confirmed the
  silent path is the desired behaviour.*
- Enter read-only mode (§B.1) when the user accepts. Cancel aborts launch.
  Never silently proceed without one of these.

### A.5 Tests
`tests/test_library_lock.py` (no Qt): fresh acquire succeeds; second acquire
sees the holder and does not acquire; stale lock is taken over; release removes
the file; heartbeat updates keep it fresh; a corrupt/half lock file is treated
as stale, not a crash.

### A.6 DoD for §A
Lock acquired on open, heartbeat running, released on clean exit, stale takeover
works, second instance blocked from read-write with a clear message,
`verify.bat` green incl. `test_library_lock.py`. Offline-first preserved — the
module uses only the filesystem (no `socket`/`urllib`/etc.).

---

## §B. v1 foundations (soon — small, keep the door open)

Small additions that make the multi-device model incremental rather than a
rewrite. Schedule around the freeze; none are large.

### B.1 Read-only library mode
A library opened without the writer lock runs in read-only mode: browse events,
view days/picks, watch Cuts — **all mutation disabled** (decision verbs, Edit
writes, Export, event-header saves are no-ops/greyed with a quiet "read-only"
hint). A persistent banner names the editing machine. This is the reader half of
the model and the natural landing spot for §A.4.

### B.2 Library-on-NAS validation
Paths already route through `settings`/`paths.py` (invariant #2), so a UNC
(`\\NAS\share\…`) or mapped-drive base should mostly work. Needs: a "library
location" setting + validation (reachable? writable? lock acquirable?) and a
test pass with the library on a real share, including the atomic
write-then-rename over SMB.

### B.3 Cut publish target
Cuts already materialise to exported files (spec/61). Add a "published" export
destination (a NAS folder convention) + a small **manifest** (ordered file list,
titles, day separators, durations) so a media layer can present the Cut as an
ordered show. Mostly settings + convention over the existing export.

### B.4 Library root — user-defined location, layout, first-run & recovery
*(Design agreed Nelson 2026-06-21. Precipitated by spec/93's filesystem recipe
trees, which need a known home alongside the database.)*

**The root is user-defined.** Today `mira/paths.py::user_data_dir()` resolves a
hidden base (`%LOCALAPPDATA%\Mira`) holding settings, the events index, and
`mira.db`; per-event `event.db` lives elsewhere. v1 makes a single **library
root** the base of *everything*, and lets the user choose where it sits (local
disk or NAS) — the seam already exists (the `MIRA_DATA_DIR` override + invariant
#2); this turns it into a first-class choice.

**Layout — machinery hidden, user content visible:**

```
<library_root>/                 the user picks this
  .mira/                        hidden machinery (dot-dir + Windows hidden+system attr)
    mira.db                     the user store (moved out of AppData)
    settings.json · events index
    writer.lock                 the §A single-writer lock now lives HERE
    logs/ · cross-event caches
  Collections/                  spec/93 — DC JSON trees (user-facing)
  Recipes/                      spec/93 — Recipe JSON trees (user-facing)
  <event folders>/              each with its own event.db + media (user-facing)
```

The `.mira/` dot-folder follows the convention already used per-event
(`.cache/`), so it is not a new idea to learn. Hidden ≠ excluded from a copy:
`.mira/` is *inside* the root, so relocating or re-mounting the root moves the
database, the recipes, and the events **as one unit** — the portability the NAS
model needs. *(This refines §A.1: the lock file is `<library_root>/.mira/writer.lock`,
not a root-level `.mira-writer.lock`.)*

**The bootstrap pointer is the only thing outside the root.** "Where is the
library?" cannot live inside the library (`mira.db` is in there). One tiny config
stays in the OS location (`%LOCALAPPDATA%\Mira\config.json` / `~/.config/mira`)
holding just `{ "library_root": "…" }`. Resolution order: `MIRA_DATA_DIR` env →
the pointer → else first-run. Everything durable about the library — including
*where its events are* — lives in `.mira/`, **not** in this disposable pointer.

**First-run — two doors:**
- **Create a new library** → pick an empty location; scaffold `.mira/` +
  `Collections/` + `Recipes/`; write the pointer.
- **Open an existing library** → browse to a root; if `<root>/.mira/mira.db`
  exists, re-point to it and write the pointer.

**Reinstall / OS-wipe recovery falls out of the second door.** A Windows reinstall
(or a clean app reinstall) wipes only the bootstrap pointer; the library on D: or
the NAS is intact, and `.mira/` still holds the DB, settings, and the events
location. Recovery is: install Mira → **Open existing library** → browse to the
root the user chose and remembers → everything reconnects in one step. The
durable memory is always *inside the library*; the OS pointer is a convenience.

**Events live under the root, with relative paths.** Storing event locations
*relative to `library_root`* (in the events index) is what makes the library
movable — a new drive letter (D: → E:) or a NAS re-mount just resolves. This also
collapses recovery to a **single** question (the root) instead of two. The
power-user option of events on a *separate* media drive is supported by recording
that location **inside `.mira/`** (so the user never re-supplies it after a
wipe); the only time a second prompt appears is the genuine edge case where the
recorded media path no longer resolves — then "locate your events," verify,
remember.

**Migration.** Existing installs do a one-shot move of the current
`%LOCALAPPDATA%\Mira` contents into the chosen root's `.mira/`, then write the
pointer — the same shape as the existing `migrate_legacy_user_data()`, so there
is a proven, idempotent, non-destructive pattern to follow.

---

## §C. Post-v1 (recorded, not built before freeze)

- **TV distribution = off-the-shelf.** Mira's responsibility ends at "publish
  the Cut + manifest to the NAS." A standard home-media server distributes it —
  **Jellyfin** is a strong offline, self-hosted pick; DLNA or even a TV's
  built-in folder slideshow also work. **Do not build a media server into
  Mira.**
- **Manifest format** for slideshows (transition hints, music handoff to PTE per
  the Cut model) — design if/when a richer TV show is wanted.
- **Live cross-PC querying / web UI** — explicitly **out of scope / a v2 trap.**
  The filesystem + media-server model covers the "Cuts on every TV" vision
  without any network service. Only revisit if a genuine need appears, and only
  then with a scoped, LAN-only, allow-listed exception to invariant #3.

---

## §D. Invariant interplay
- **#3 (no network):** the whole model is filesystem-only — preserved. Flag any
  future network service as a charter-level decision, not an incidental import.
- **#2 (no hardcoded paths):** library root + publish target come from settings.
- **#6 (atomic write-then-rename):** used for the lock file + its heartbeat and
  for all published artifacts.
- **B7 synergy (backups/redundancy):** a NAS brings RAID + snapshots, which
  strengthens redundancy — but "RAID isn't a backup," so versioned copies still
  belong in B7's design.

## §E. Sequencing
1. **§A single-writer lock — now** (this slice).
2. §B.1 read-only mode — with or just after §A (completes the conflict UX).
3. §B.2 NAS validation + §B.3 Cut publish target — around the freeze.
4. §C — after the production freeze, mostly off-the-shelf assembly.
