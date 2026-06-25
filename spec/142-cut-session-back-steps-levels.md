# 142 — Stopgap: title-bar Back in a Cut session must step levels, not close the Cut

**Status: PROPOSED (Nelson 2026-06-23, stopgap for the spec/141 unification).
While picking items for a Cut, pressing **Back** from a day's grid (to go
pick the **next day**) **closes the Cut unsaved** instead of returning to the
day panel. Root cause is one method: `ShareCutsPage.on_titlebar_back`
(share_cuts_page.py:1142) blindly fires the current sub-page's
`back_requested` signal — and `CutSessionPage` wires its `back_requested` to
`_on_cancel` (cut_session_page.py:555 = leave/close). So the title-bar Back
closes the session from **every** level, and `CutSessionPage`'s own
**level-stepping** `on_titlebar_back()` (single→grid→days→leave) is never
called. Fix: have `ShareCutsPage.on_titlebar_back` **call the sub-page's
`on_titlebar_back()` when it has one** (the session does), falling back to
emitting `back_requested` only for sub-pages that don't. One-method change,
no data-model change. The full reuse of the Days surfaces stays spec/141.**

## 1. The bug

```python
# share_cuts_page.py — on_titlebar_back
cur = self._stack.currentWidget()
sig = getattr(cur, "back_requested", None)
if sig is not None:
    sig.emit()                       # CutSessionPage.back_requested → _on_cancel (close)
```

`CutSessionPage` already has the correct dispatcher:

```python
# cut_session_page.py — on_titlebar_back (level-stepping, currently never called from the title bar)
idx = self._stack.currentIndex()
if idx == 2:   self._back_to_grid()      # single → grid
elif idx == 1: self._back_to_days()      # grid → days panel
else:          self.back_requested.emit() # days panel → leave the session
```

But `ShareCutsPage` bypasses it and emits `back_requested` directly, so Back
always lands on `_on_cancel`.

## 2. The fix

`ShareCutsPage.on_titlebar_back` prefers the sub-page's own dispatcher:

```python
cur = self._stack.currentWidget()
fn = getattr(cur, "on_titlebar_back", None)
if callable(fn):
    fn()                 # level-stepping (CutSessionPage: grid → days, single → grid)
    return
sig = getattr(cur, "back_requested", None)
if sig is not None:
    sig.emit()           # list / detail / pool — unchanged
```

- **Cut session:** Back from a day's grid → `_back_to_days()` (the day
  panel), where the user picks the next day; Back from single view → the
  grid; only Back **at the day panel** emits `back_requested` → the existing
  cancel-with-confirm. **The in-progress Cut draft is held in `CutSession`
  the whole time — stepping levels never touches it.**
- **List / detail / pool pages:** they have no `on_titlebar_back`, so they
  fall through to `back_requested` exactly as today (no regression).

## 3. Acceptance

- In a Cut session, Back from a day's grid returns to the **day panel** so
  the user can pick another day; the Cut is **not** closed and the
  already-picked items remain in the draft.
- Back from the single view returns to the grid.
- Back at the day panel still leaves the session (cancel-with-confirm), and
  **Create Cut** still commits — unchanged.
- List / Detail / Pool Back behave exactly as before.

## 4. Tests

- `tests/test_cut_session_titlebar_back.py` — with the session stack at the
  grid level, `ShareCutsPage.on_titlebar_back()` calls
  `CutSessionPage.on_titlebar_back()` (steps to the day panel) and does **not**
  reach `_on_cancel`; at the single level it steps to the grid; at the day
  panel it leaves; a list/detail/pool current page still emits its
  `back_requested`.
- `tests/test_cut_draft_survives_back.py` — picking items, Back to the day
  panel, opening another day: the first day's picks are still in the draft
  (the reported bug, pinned).
