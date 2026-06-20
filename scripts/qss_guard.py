"""Inline-style guard — spec/92 §7 safety net.

Visual treatment lives in QSS, never inline in widget code (CLAUDE.md
"QSS + clickable affordances"; spec/05 §5.1). This guard scans ``mira/ui``
for ``setStyleSheet(`` calls and fails if their number ever *grows* past a
recorded baseline. It does NOT demand zero today (there are existing
violations the spec/92 migration removes stage by stage) — it ratchets the
count downward and blocks regressions.

Usage::

    python scripts/qss_guard.py            # check against baseline → exit 1 on regression
    python scripts/qss_guard.py --list     # list every current occurrence
    python scripts/qss_guard.py --update-baseline   # re-record after removing some (ratchet down)

A line carrying the marker ``# pragma: no-qss`` is an explicit, reviewed
exception (e.g. the slideshow canvas in ``shared/cut_play.py``) and is not
counted.

The pytest wrapper ``tests/test_no_inline_qss.py`` calls ``check()`` so the
guard runs as part of ``verify.bat``.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_UI_ROOT = _REPO / "mira" / "ui"
_BASELINE = Path(__file__).resolve().parent / "qss_guard_baseline.json"

_PATTERN = re.compile(r"\.setStyleSheet\s*\(")
_PRAGMA = "# pragma: no-qss"

# theme.py owns the single sanctioned global ``app.setStyleSheet(...)`` apply
# point (it builds + installs the stylesheet). It is not a widget override, so
# it is excluded from the widget-styling guard.
_EXCLUDE = {"mira/ui/theme.py"}


def scan(ui_root: Path = _UI_ROOT) -> dict[str, int]:
    """Return {relative_posix_path: count} of inline setStyleSheet calls."""
    counts: dict[str, int] = {}
    for path in sorted(ui_root.rglob("*.py")):
        if path.relative_to(_REPO).as_posix() in _EXCLUDE:
            continue
        n = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if _PRAGMA in line:
                continue
            if _PATTERN.search(line):
                n += 1
        if n:
            counts[path.relative_to(_REPO).as_posix()] = n
    return counts


def occurrences(ui_root: Path = _UI_ROOT) -> list[tuple[str, int, str]]:
    """Return (path, lineno, text) for every counted occurrence."""
    hits: list[tuple[str, int, str]] = []
    for path in sorted(ui_root.rglob("*.py")):
        if path.relative_to(_REPO).as_posix() in _EXCLUDE:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PRAGMA in line:
                continue
            if _PATTERN.search(line):
                hits.append((path.relative_to(_REPO).as_posix(), i, line.strip()))
    return hits


def load_baseline() -> dict[str, int]:
    if not _BASELINE.exists():
        return {}
    return json.loads(_BASELINE.read_text(encoding="utf-8")).get("counts", {})


def write_baseline(counts: dict[str, int]) -> None:
    payload = {
        "_comment": (
            "spec/92 §7 inline-style guard baseline. Counts may only shrink. "
            "Run `python scripts/qss_guard.py --update-baseline` after a "
            "migration stage removes violations. New/grown entries fail the guard."
        ),
        "total": sum(counts.values()),
        "counts": dict(sorted(counts.items())),
    }
    _BASELINE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def check(ui_root: Path = _UI_ROOT) -> tuple[bool, list[str]]:
    """Return (ok, messages). ok=False if any file regressed past baseline."""
    current = scan(ui_root)
    baseline = load_baseline()
    msgs: list[str] = []
    for path, n in sorted(current.items()):
        allowed = baseline.get(path, 0)
        if n > allowed:
            if allowed == 0:
                msgs.append(f"NEW inline style in {path}: {n} call(s) (baseline 0)")
            else:
                msgs.append(f"INCREASED inline styles in {path}: {n} > baseline {allowed}")
    return (len(msgs) == 0, msgs)


def main(argv: list[str]) -> int:
    if "--update-baseline" in argv:
        counts = scan()
        write_baseline(counts)
        print(f"Baseline updated: {sum(counts.values())} occurrence(s) across {len(counts)} file(s).")
        return 0
    if "--list" in argv:
        for path, lineno, text in occurrences():
            print(f"{path}:{lineno}: {text}")
        print(f"\nTotal: {len(occurrences())} occurrence(s).")
        return 0
    ok, msgs = check()
    if ok:
        total = sum(scan().values())
        print(f"qss_guard OK — {total} known inline style(s), none new (baseline {sum(load_baseline().values())}).")
        return 0
    print("qss_guard FAILED — inline styling grew past baseline (spec/92 §7):")
    for m in msgs:
        print(f"  - {m}")
    print("\nMove the new styling into a QSS role (assets/themes/redesign.qss),")
    print("or, for a reviewed exception, mark the line with `# pragma: no-qss`.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
