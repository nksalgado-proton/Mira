# 100 — Delete-event: quiesce file handles before the folder wipe

**Status: PROPOSED (Nelson 2026-06-22, after a 3rd reproduction). Fixes a
real bug: "Delete photos too" intermittently fails with "some files could
not be deleted" AND leaves the event in Mira's list — a zombie. Root
cause is the spec/63 §7 background preview/proxy subsystem holding OS
file handles under the event root when `shutil.rmtree` runs. Touches
`mira/gateway/gateway.py` (`delete_event`), `mira/ui/shell/main_window.py`
(`_on_delete_event`), `mira/ui/media/photo_cache.py`, and
`core/photo_proxy_cache.py` (`ProxyBuilder`). No keymap / charter-invariant
impact (this strengthens invariant #9's delete path). Respects invariant
#1: the quiesce is driven from the UI layer — the gateway never imports
`mira/ui`.**

## 1. The bug (observed, intermittent)

"Delete photos too" → `_on_delete_event` → `gateway.delete_event(
delete_files=True)` → `shutil.rmtree(root)`. The rmtree runs with nothing
releasing the background preview machinery first:

- The `PhotoCache` singleton's `ProxyBuilder` is a daemon thread still
  building proxies for the event the user was just browsing — it holds an
  original open (`with Image.open(source)`) and writes
  `<event_root>/.cache/proxies/*.jpg`.
- The decode worker may also have an original open transiently.
- An armed `QMediaPlayer` (if a video was shown) holds its source file.

On Windows an open handle makes a file un-deletable, so `rmtree` raises
`PermissionError` (WinError 32, "in use by another process") on the first
locked file and aborts. The exception propagates out of `delete_event`
**before** `self.index.remove(event_id)` runs, so:

1. some files remain (rmtree stopped partway), AND
2. the event row is never dropped — it stays in Mira's list.

Both of the user's symptoms, together. **Intermittent** because it only
fails when a builder/worker holds a handle under the root at the instant
of the wipe — i.e. right after browsing that event, proxies still
trailing. Browse-then-immediately-delete is the trigger; an idle builder
deletes cleanly.

## 2. Fix — three parts

### A. Quiesce the preview subsystem before the wipe (the real fix)

Add a release path that lets every background handle under the root go,
driven from the **UI layer** (invariant #1 — the gateway must not import
`mira/ui`):

- **`ProxyBuilder.quiesce(timeout=2.0)`** (new, in `core/photo_proxy_cache.py`)
  — clear the queue AND wait for the in-flight build to finish, WITHOUT
  permanently stopping the thread (unlike `stop()`, which sets
  `_stopping` for the session and makes later `seed()` a no-op). Track a
  `_building` flag set while inside `ensure`; `quiesce` clears the queue,
  then waits on the condition until `_building` is False or the timeout
  lapses. `clear()` alone is insufficient — it empties the queue but
  does not interrupt the `Image.open` already in progress, so the handle
  would survive.
- **`PhotoCache.release_for_delete()`** (new, in
  `mira/ui/media/photo_cache.py`) — `clear()` the pixmap/scaled/thumb
  tiers, `set_event_context(None, {})`, and `quiesce()` BOTH builders
  (the proxy builder and the export-thumb builder). Also briefly drain
  the decode worker (bump generation + ensure no in-flight job) so a
  transient original handle is released.
- **Disarm video.** Before delete, `_on_delete_event` tears down any
  `QMediaPlayer` on the surfaces that showed this event (the viewport's
  `shutdown_video` / `_disarm_video`) so no clip holds its source.

`_on_delete_event` calls `photo_cache().release_for_delete()` (and the
video teardown) BEFORE invoking `gateway.delete_event(...)`.

### B. Resilient rmtree (ride out transient locks)

In `delete_event`, replace the bare `shutil.rmtree(root)` with a helper
that passes an error handler (`onexc` on 3.12+, `onerror` on older):
clear the read-only attribute (`os.chmod(..., stat.S_IWRITE)`) and retry
the unlink with a short backoff (e.g. 3 tries × ~150 ms). This rides out
a transient antivirus / Windows Search-indexer lock that §A can't
control. Collect any paths that still fail after retries.

### C. Never leave a zombie — always drop the index row

Reorder `delete_event` so the events-index row is removed **even if a few
files stubbornly remain** after §A+§B best-effort. Wrap the rmtree in a
try; on residual failure, still call `self.index.remove(event_id)`, log
the residue, and surface it (return value / so the UI can say "event
removed; N file(s) could not be deleted and remain at <path>"). The event
must disappear from Mira regardless — the user's deliberate, confirmed
choice was honoured for the record even if the OS held a file.

## 3. Acceptance

- Browse an event (let proxies build), immediately "Delete photos too" —
  the folder is removed and the event leaves the list, repeatably, with
  no "could not be deleted" dialog.
- If a file is genuinely held by an outside process (simulated lock), the
  event is STILL removed from Mira's list and the dialog reports the
  specific residual file(s) and where they remain — never a silent
  half-state that re-appears on next launch.
- A delete with no open handles behaves exactly as today (fast path).
- Invariant #1 holds: `mira/gateway` gains no `mira/ui` import (grep the
  one-way-dependency guard).

## 4. Tests

- `tests/test_proxy_builder_quiesce.py` — `quiesce()` returns after the
  in-flight build completes and the thread stays seedable afterwards
  (a later `seed()` still queues).
- `tests/test_delete_event_residue.py` — monkeypatch `shutil.rmtree` to
  raise `PermissionError`; assert `delete_event` STILL removes the index
  row and reports residue (no zombie).
- Regress the existing delete tests + `tests/test_no_inline_qss.py` and
  the one-way-dependency guard.
