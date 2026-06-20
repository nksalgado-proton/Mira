# UI safety net (spec/92 §7)

Two guards protect the look of the app while the widget-consolidation
migration removes local style overrides. **Neither changes how the app
behaves** — they only watch styling.

Run everything below **on the host** (`python …` from the repo root), where
PyQt6 and the photo library live. The sandbox can't render the Qt UI.

## 1. Inline-style guard (automatic, runs in `verify.bat`)

Stops new local `setStyleSheet(...)` overrides from creeping into
`mira/ui/`. It does **not** demand zero today — it locks the current count
(57 across 24 files) and only lets it shrink.

```
python scripts/qss_guard.py            # check (exit 1 if styling grew)
python scripts/qss_guard.py --list     # show every current occurrence
python scripts/qss_guard.py --update-baseline   # re-lock after a stage removes some
```

- Runs automatically as `tests/test_no_inline_qss.py` inside `verify.bat`.
- A reviewed exception (e.g. the slideshow canvas in `shared/cut_play.py`)
  is marked with a trailing `# pragma: no-qss` on the line and is not counted.
- `mira/ui/theme.py` is excluded — it owns the single sanctioned global
  `app.setStyleSheet(...)` apply point.
- After a migration stage removes overrides, run `--update-baseline` and
  commit the new `scripts/qss_guard_baseline.json`. The number ratchets down
  toward the documented exceptions only.

## 2. Render diff (run by hand around each stage)

Catches any unintended visual change by comparing freshly-rendered smoke
screenshots against the locked baseline in `scripts/_ui_baseline/`.

```
# 1. regenerate the smoke renders after your change (host):
python scripts/smoke_surface_01.py        # ...and the others you touched
#    (each writes scripts/smoke_<name>_{dark,light}.png)

# 2. compare against the baseline:
python scripts/compare_renders.py          # table of per-surface deltas
python scripts/compare_renders.py --write-diffs   # also emit heatmaps to scripts/_ui_diff/
```

- **Golden surfaces** (initial app `smoke_surface_01`, phases
  `smoke_surface_03`, share/cuts `smoke_share_cuts_page`) must stay
  **identical** — any delta there is a hard fail. The New Cut golden lives at
  `Desktop/New cuts surface.png`; add a smoke render for it before Stage 2b.
- Non-golden surfaces allow a tiny tolerance (`--tol`, default 0.1%) to
  absorb anti-aliasing noise; real changes exceed it and fail.
- When a change **is** intended (a deviating widget brought onto the
  standard), eyeball the diff, then re-lock with
  `python scripts/compare_renders.py --update-baseline` and commit the
  updated `scripts/_ui_baseline/`.

## Per-stage checklist

1. `python scripts/compare_renders.py` → confirm clean start (or known state).
2. Do the migration step (one pattern / one surface).
3. `verify.bat` → suite green, inline-style guard green.
4. Regenerate touched smoke renders → `python scripts/compare_renders.py`.
5. Golden surfaces identical; intended deltas reviewed.
6. `--update-baseline` on guard + renders where the change was intended; commit.

## Files

- `scripts/qss_guard.py` — inline-style scanner + baseline manager.
- `scripts/qss_guard_baseline.json` — locked per-file counts (commit it).
- `tests/test_no_inline_qss.py` — runs the guard in the suite.
- `scripts/compare_renders.py` — render diff + baseline manager.
- `scripts/_ui_baseline/` — locked "before" renders (commit it).
- `scripts/_ui_diff/` — throwaway heatmaps (git-ignore it).
```
# add to .gitignore:
scripts/_ui_diff/
```
