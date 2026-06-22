# 97 — New-event Quick Sweep: a visible Finish/Import control

**Status: PROPOSED (Nelson 2026-06-22). Fixes a flow dead-end: the
new-event (Collect) Quick Sweep modal gives the user no visible way to
finish, so the picked files are never copied into the event and Collect
aborts. Same root cause family as the spec/63/`a4c2a12` "Back moved to
the app title bar" regression (cf. the Days Grid footer fix): a modal
has no app title bar. Touches `mira/ui/shell/main_window.py`
(`_run_quick_sweep_first`). No charter-invariant or keymap impact. No
data loss occurs today — the import simply never runs.**

## 1. The bug (observed)

New-event creation runs Quick Sweep in a **modal** (`QDialog`) hosting a
3-page stack (DaysLists → DaysGrid → QuickSweep viewer),
`_run_quick_sweep_first`. The user sweeps, their Pick/Skip selections are
captured in the session ledger and shown correctly on the days list —
then there is **no visible control to finish**. The only finish trigger
wired is `lists_page.back_requested.connect(finalize)`, but
`DaysListsPage` keeps Back in the **app title bar** (`uses_titlebar_back
= True`), and the modal has no title bar. So from the days list the user
is stranded; the only exit is the window-close [X], which rejects the
dialog → `_run_quick_sweep_first` returns `None` → `_collect_*` aborts →
**the kept files are never copied into the event** and Collect shows "not
started".

(The Days Grid level was already given its own Back button in the bug-#4
fix, so grid→list works; the **list level + commit** is the gap.)

## 2. The fix

Give the modal its own persistent controls, independent of the pages'
title-bar Back:

1. **A persistent footer button row** on the modal `host`, below the
   `QStackedWidget`, visible on every stack page:
   - **Primary: "Finish & import…"** → calls `finalize` (the existing
     confirm + `host.accept()` + kept-set capture). This is the missing
     trigger that `lists_page.back_requested` used to be.
   - **Secondary: "Cancel"** → a discard-confirm ("Discard this Quick
     Sweep? Nothing will be imported."); on confirm → `host.reject()`.
2. **Window-close [X] routes through the same Cancel-with-confirm**
   rather than silently aborting (override the dialog's `reject` /
   `closeEvent` so the X asks first). This keeps an accidental close from
   throwing away a sweep.
3. The in-page Back buttons stay as level navigation (viewer → grid →
   list, via the bug-#4 grid footer). Two-tier model: **Back = navigate
   up one level; Finish & import = commit the whole sweep.**

`finalize` already builds the "Import and finish?" summary, sets
`result["kept"]`, and accepts — no change needed there beyond being
reachable from the footer.

## 3. Notes

- Only the **modal** (new-event Collect) flow is broken. The standalone
  Quick Sweep and per-event Quick Sweep run on the main page stack (real
  title-bar Back), so their finish path (`_qs_finalize_via_back`) is
  reachable; leave them as-is.
- Keep the footer label unambiguous ("Finish & import…", with the
  ellipsis signalling the confirm summary follows).

## 4. Tests

- The modal exposes a visible Finish control whose click runs `finalize`
  and, on accept, returns the kept set (assert `_run_quick_sweep_first`
  returns the kept set, not `None`).
- Cancel (and the window-close path) prompt a discard confirm; confirming
  returns `None` (abort), dismissing keeps the modal open.
- After Finish, the kept files reach `_run_collect_copy_all` (the import
  runs) — assert via the existing collect-copy seam / a fake.

## 5. Acceptance (Nelson eyeball)

- Create a new event, run Quick Sweep, make selections, and from the days
  list click **Finish & import** → the confirm appears, and on accept the
  picked files are copied into the event (Collect shows started/done).
- Closing the window asks before discarding.
