# 148 — Cut export: Overwrite vs Keep-both (stop accumulating `(2)` folders)

**Status: PROPOSED (Nelson 2026-06-23, design agreed). Re-exporting a Cut to
the same destination never overwrites — `export_cut` runs the target through
`_fresh_folder` (cut_export.py:89), so `Cuts/<tag>/` becomes
`Cuts/<tag> (2)/`, `(3)`, … The old folder is left untouched (safe — never
clobbers a project the user has edited in PTE), but re-exports **accumulate**
folders, and the manual "delete old + rename new" workaround **breaks the
`.pte`** (its baked absolute paths still point at `<tag> (2)/`). Add an
**Overwrite vs Keep-both** choice on Cut export — Overwrite writes straight
into `<tag>/` (replacing the prior bundle, `.pte` paths correct from the
start); Keep-both is today's `(2)` behaviour. Mirrors the media-level
spec/118 choice. Touches `mira/shared/cut_export.py` (target resolution),
`mira/shared/pte_project.py` (already has an `overwrite` flag), and the
export dialog/handlers (`share_cuts_page` / `library_page`).**

## 1. The bug / friction

`_fresh_folder(base)` returns `base` if free, else `base (2)`, `(3)`… So a
re-export of the same Cut to the same root always makes a new numbered
folder. Consequences:

- Folders pile up (`<tag>/`, `<tag> (2)/`, …) with no clean "replace."
- The safe-looking manual fix (delete `<tag>/`, rename `<tag> (2)/` →
  `<tag>/`) moves the media fine (self-contained hardlinks + audio), but the
  generated `.pte` has **absolute** paths baked to `<tag> (2)/`
  (`ProjectFilePath` / `ImagesFolder` / per-slide `FileName`/`Picture`), so
  it can't find its media after the rename.

## 2. The fix — choose Overwrite or Keep both

On Cut export, offer (default to the user's last choice):

- **Overwrite** — materialise into the **existing `<tag>/`**, replacing the
  prior bundle: clear/replace the folder's contents and write the `.pte`
  with the correct `<tag>/` paths. No `(2)`; no stale-path problem. (Reuse
  `pte_project.generate_into_folder(..., overwrite=True)` for the `.pte`;
  resolve the cut-export target to `base` without `_fresh_folder`
  disambiguation.)
- **Keep both** — today's behaviour: `_fresh_folder` → `<tag> (2)/`, old
  folder untouched.

Safety on Overwrite: confirm before replacing a non-empty existing folder
("Replace the previous export of this Cut?"), since the user may have edited
that project in PTE. (Keep-both stays the safe default for the cautious.)

- Same choice for **per-event and cross-event** Cut export.
- Hardlinks make Overwrite cheap (re-link the same `Exported Media/` bytes);
  audio re-copies, separators re-render, `.pte` regenerates — into `<tag>/`.

## 3. Acceptance

- Cut export offers Overwrite / Keep both; Overwrite writes into `<tag>/`
  (no new `(2)`), with a confirm when replacing a non-empty folder.
- After Overwrite, the `.pte` in `<tag>/` has correct `<tag>/` paths and
  opens cleanly in PTE.
- Keep both reproduces today's `(2)` behaviour.
- Per-event and cross-event both honour the choice.

## 4. Tests

- `tests/test_cut_export_overwrite.py` — Overwrite writes into `base` (not
  `base (2)`), replacing prior contents; Keep-both still disambiguates;
  the `.pte` written under Overwrite carries `base` paths
  (ProjectFilePath / ImagesFolder / FileName) — no `(2)`.
- `tests/test_cut_export_overwrite_confirm.py` — replacing a non-empty
  existing folder prompts; cancel leaves it intact.
