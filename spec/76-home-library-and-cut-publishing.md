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

### A.4 Conflict UX (minimum for now)
When the lock is held by a live writer, the second instance must not open
read-write. Minimum viable behaviour for this deliverable:

- Show a clear dialog (reuse `mira/ui/design/dialogs.py`): *"This library is open
  for editing on **{hostname}** (since {time}). Opening in read-only mode."* —
  with **Open read-only** and **Cancel**; if the holder is **stale**, add
  **Take over editing**.
- Enter read-only mode (§B.1). If full read-only mode isn't in this slice yet,
  the acceptable interim is to **decline to open** with the same message + a
  Retry, rather than ever opening a second writer. Never silently proceed.

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
