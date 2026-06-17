# spec/87 — Deep dead-code elimination audit

**Status:** plan, 2026-06-17. **Runs after spec/84 lands** (the last feature
build) so the tree is stable. Goal: find and remove code that nothing reaches —
orphaned modules, classes, functions, branches, imports, and assets left behind
by the Miracraft→Mira fork and the recent surface rewrites (Collect/Pick/Edit/
Export, Cuts/DC, the filter rebuild). **Analysis first, deletion second** — a
reviewed report before anything is removed, then staged deletions with the test
suite green at every step.

Read with: `spec/00-charter.md` (invariants), `CLAUDE.md` (the intentionally-kept
"Miracraft" identifiers), `spec/PROGRESS.md` (the known tombstone list).

---

## 1. Known starting points (already found)
- **`mira/ui/pages/past_photos_dialog.py` (`PastPhotosDialog`)** — orphaned. The
  class is defined but **never imported or instantiated** anywhere live; it still
  uses the old modal `run_with_progress` ingest path that the queue replaced.
  Prime deletion candidate (plus its now-only-referenced helpers, e.g. parts of
  `past_photos_cameras.py` if they fall with it).
- **The five lazy legacy-import tombstones** catalogued in `spec/PROGRESS.md`
  (`from ui.X` / `from data.X` inside function bodies in `core/event_service.py`,
  `core/reconcile_pipeline.py`, `core/phase_progress.py`,
  `mira/ui/edited/edit_host_page.py`, `mira/ui/edited/edit_page.py`). Each fires
  only if its dead feature path is hit — triage: rewire or delete the branch.
- **Legacy vocabulary remnants** — any surviving `cull` / `curate` / `keep` /
  `discard` in code/QSS/assets (the charter says these are gone; verify).
- **Dead branches** noted across specs (e.g. the deleted P-export branch
  lineage) — confirm none linger.

## 2. Method — layered, evidence-based
1. **Unreferenced symbols** — run a dead-code finder (`vulture`) over `core/` +
   `mira/`, and cross-check each hit by grepping for its name across code, tests,
   QSS, and assets. Zero non-definition references = candidate.
2. **Dead modules** — files no other module imports (and no entry point /
   `__main__` / test references). `PastPhotosDialog` is the template.
3. **Unused imports + locals** — `ruff`/`pyflakes` pass.
4. **Legacy-vocabulary grep** — `cull|curate|keep|discard` and `miracraft`
   (minus the intentional list, §4).
5. **Unreachable branches** — feature flags permanently off, superseded code
   paths behind retired entries.

Produce a **report** first: each candidate with its evidence (no callers, no
test, no dynamic reference) grouped by confidence (safe / needs-eyes). Nelson
signs off before deletions.

## 3. The false-positive traps in THIS codebase
A static "unused" hit is **not** proof — verify each against these before
deleting:
- **PyQt signals/slots** connected by name or via `pyqtSignal`; methods invoked
  only through Qt connections look unused to static tools.
- **Dynamic dispatch / reflection** — `getattr`, schema-driven dialogs
  (`SETTINGS_SCHEMA`), the `_filter_family` factory closures, scenario/ruleset
  loaders.
- **Skills / plugins / entry points** — anything loaded by name or registered.
- **Test-only references** — used by `tests/` but not by app code: keep unless
  the tested thing itself is dead.
- **Worker-mode / packaged-binary paths** (spec/60 render worker) — reachable
  only when re-invoked as a subprocess.
- **Intentionally-kept "Miracraft" identifiers** (CLAUDE.md / PROGRESS): paths,
  `APP_NAME`/`ORG_NAME`, log namespace, `i18n` default context, theme palette
  key, settings filenames. **Do not sweep these.**

## 4. Process & safety
- Own **branch**; small, logical commits (one removal theme each).
- **`verify.bat` green after every deletion** — the suite is the safety net
  (3373+ tests today). A removal that reddens the suite is reverted or re-scoped.
- Launch the app and click through each phase after the UI-facing removals
  (static tools can't see a Qt-only path).
- **A verification subagent** re-checks the candidate list independently before
  bulk deletion (high-stakes — deleting reachable code is worse than leaving a
  dead file).
- Update specs/docs for anything whose removal changes a documented surface.

## 5. Deliverable
1. A **dead-code report** (candidates + evidence + confidence) — review gate.
2. Staged deletion commits, suite-green each, with the orphans from §1 first.
3. A short closeout note in PROGRESS: what was removed, what was deliberately
   kept and why (so a future audit doesn't re-flag the intentional refs).

## 6. Out of scope
- Refactoring / renaming live code (this is *removal*, not redesign).
- The intentional Miracraft-continuity identifiers (§3).
- Behavior changes — if removing dead code would change behavior, it wasn't dead;
  stop and surface it.
