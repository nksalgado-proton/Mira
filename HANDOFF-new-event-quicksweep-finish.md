# Handoff — new-event Quick Sweep Finish control (spec/97)

Implement **spec/97** (`spec/97-new-event-quicksweep-finish.md`) — read
it first. Small, targeted fix in one place.

Branch: **main**. Read CLAUDE.md; update the spec with the code if you
change a detail.

## The bug

New-event Collect runs Quick Sweep in a **modal** (`QDialog`) in
`mira/ui/shell/main_window.py::_run_quick_sweep_first`. The only finish
trigger is `lists_page.back_requested.connect(finalize)`, but
`DaysListsPage` keeps Back in the app title bar and the modal has **no
title bar** — so from the days list there's no way to finish. The user
closes the window → the dialog rejects → `_run_quick_sweep_first` returns
`None` → Collect aborts and **the picked files are never copied into the
event**. (No data loss — the import just never runs.)

## The fix (all in `_run_quick_sweep_first`)

The modal `host` currently is: `QVBoxLayout` → `QStackedWidget` only
(see ~line 4626). Add a persistent footer and a guarded close:

1. **Footer button row** below the stack, always visible on every page:
   - Primary **"Finish & import…"** → `finalize` (existing function; it
     shows the "Import and finish?" summary, sets `result["kept"]`, and
     `host.accept()`). This replaces the unreachable
     `lists_page.back_requested` as the finish trigger. (You can keep the
     `back_requested → finalize` connection too; it's just no longer the
     only path.)
   - Secondary **"Cancel"** → discard-confirm ("Discard this Quick Sweep?
     Nothing will be imported.") → on confirm `host.reject()`.
   Use the design-system buttons (`primary_button` / `ghost_button`).
2. **Guard the window-close [X]:** route it through the same Cancel
   confirm instead of silently rejecting — override the dialog's `reject`
   (or install a `closeEvent` handler) so an accidental close asks first.
3. Leave the in-page Back buttons as level navigation (viewer→grid→list,
   the bug-#4 grid footer). Back = up one level; Finish & import = commit.

Do NOT touch the standalone or per-event Quick Sweep flows — they run on
the main page stack (real title-bar Back) and finish via
`_qs_finalize_via_back`, which is reachable.

## Tests

- The modal exposes a visible Finish control; clicking it runs `finalize`
  and, on accept, `_run_quick_sweep_first` returns the kept set (not
  `None`). After Finish, the kept set reaches `_run_collect_copy_all`
  (assert via the collect-copy seam / a fake).
- Cancel and the window-close path both prompt the discard confirm;
  confirming returns `None`, dismissing keeps the modal open.

Run any quick-sweep / collect suites, then full `verify.bat`.

## Commit + push (on main)

```
fix: new-event Quick Sweep modal had no visible Finish control (spec/97)

The Collect Quick Sweep runs in a modal with no app title bar, so the
only finish trigger (lists_page.back_requested -> finalize) was
unreachable from the days list — the user couldn't commit and the picked
files were never copied into the event. Add a persistent
"Finish & import…" + "Cancel" footer to the modal and route the window
close through a discard confirm. Standalone / per-event flows unchanged.
```

Then `git push` on `main`.
