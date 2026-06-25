# 149 — Standalone "Generate PTE" for an exported Cut folder (+ Open-in-PTE auto-generates)

**Status: IMPLEMENTED (Nelson 2026-06-25). The new
`mira.shared.cut_pte_generation.generate_pte_for_folder` scans an exported
Cut folder, builds the member list + audio tracks from the files there,
and writes ``<stem>.pte`` (overwrite=True) via the existing
`pte_project.generate_into_folder` — no media re-materialisation. Surfaces:
the cut detail page (`CutDetailPage`) and the cross-event Cut detail dialog
(`CrossEventCutDetailDialog`) gained a **Generate PTE** button next to the
spec/117 Open folder / Open in PTE buttons. **Open in PTE** in both
surfaces now auto-generates when `pte_file` is `None` but `use_pte` is on
and the folder exists — one click, no re-export. Tests:
`tests/test_generate_pte_standalone.py` (data layer: media + audio walk,
rename self-heals paths, no media re-write) and
`tests/test_open_in_pte_autogenerate.py` (handler dispatch: auto-generate
when missing, skip when `use_pte` off, per-event + cross-event).**

## 1. The gap

PTE generation runs only inside the export flow; the only callers are
`_generate_pte_if_enabled` (per-event export) and the cross-event equivalent
(`library_page:614`). `_open_in_pte` resolves `loc.pte_file` and, when it's
`None`, just logs and returns. So a folder can have media but **no `.pte`**
— after a manual rename (spec/148: baked paths broke), when `use_pte` was off
at export time, or if the `.pte` was deleted — and the only recovery is a
full re-export.

## 2. The fix

### A. Standalone "Generate PTE"
A **"Generate PTE"** action that runs the generator against an existing
exported Cut folder (the spec/117-resolved location) **without** re-exporting
media: walk the folder's files into the member list (the same way
`_generate_pte_into_folder` already does), build audio from `audio/`, and
write the `.pte` (overwrite the existing one) with **correct paths for that
folder**. Homes:

- the **cut detail page**, beside the spec/117 "Open folder" / "Open in PTE"
  buttons; and/or
- the **export-complete dialog**.

Gate it like the launch buttons: visible when `use_pte` is on and the folder
exists. (It does not need `pte_path` valid — generating is independent of
launching.)

### B. Open in PTE auto-generates when missing
In `_open_in_pte` (spec/117), if the resolved `pte_file` is `None` but the
exported folder exists and `use_pte` is on, **generate it first** (via §A),
then launch. So "Open in PTE" always has something to open — covering the
rename / flag-off / deleted-`.pte` cases transparently.

## 3. Acceptance

- A "Generate PTE" action (re)writes the `.pte` into an existing exported
  Cut folder with paths correct for that folder, without re-materialising
  media; it opens cleanly in PTE.
- After renaming a `(2)` folder to `<tag>/` (spec/148 context), Generate PTE
  fixes the stale-path `.pte`.
- "Open in PTE" with no `.pte` present generates one and launches, instead
  of no-op'ing.
- Per-event and cross-event both supported.

## 4. Tests

- `tests/test_generate_pte_standalone.py` — Generate PTE on a folder with
  media + `audio/` but no `.pte` writes a valid `.pte` whose paths match the
  folder; running it on a renamed folder produces correct (renamed) paths;
  no media files are re-written.
- `tests/test_open_in_pte_autogenerate.py` — `_open_in_pte` with
  `pte_file=None`, `use_pte` on, folder present → generates then launches;
  with `use_pte` off → unchanged (no generate).
