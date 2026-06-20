"""Render diff — spec/92 §7 visual-regression net.

Compares freshly-rendered smoke PNGs (``scripts/smoke_*_{dark,light}.png``)
against the locked baseline in ``scripts/_ui_baseline/``. Use it around every
migration stage: a consolidation that is appearance-preserving (spec/92 §1.0)
must leave the protected/golden surfaces identical; any unintended delta shows
up here before it reaches Nelson's eyeball.

Workflow (on the host, where PyQt6 + the photo library live)::

    # one-time, or to re-baseline after an intended change:
    python scripts/compare_renders.py --update-baseline

    # before a stage: (renders already exist as scripts/smoke_*.png)
    # ...do the migration, then regenerate the smoke PNGs, then:
    python scripts/compare_renders.py            # report deltas
    python scripts/compare_renders.py --write-diffs   # also emit heatmaps

Exit code is non-zero if any surface changed beyond --tol (default 0.1% of
pixels past a small per-channel threshold — absorbs AA noise, catches real
changes). A changed *golden* surface (spec/92 §1.0b) is always a hard fail.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_BASELINE = _SCRIPTS / "_ui_baseline"
_DIFFS = _SCRIPTS / "_ui_diff"

# Surfaces that must never change under consolidation (spec/92 §1.0b).
GOLDEN = {
    "smoke_surface_01",        # initial app surface
    "smoke_surface_03",        # phases
    "smoke_share_cuts_page",   # share / cuts
    # New Cut golden is Desktop/New cuts surface.png (not in the smoke set yet).
}

_CHANNEL_THRESHOLD = 16  # per-channel 0..255 delta below this is treated as noise


def _smoke_pngs(folder: Path) -> dict[str, Path]:
    return {p.stem: p for p in sorted(folder.glob("smoke_*.png"))}


def _diff_fraction(a_path: Path, b_path: Path, write_to: Path | None):
    from PIL import Image, ImageChops

    a = Image.open(a_path).convert("RGB")
    b = Image.open(b_path).convert("RGB")
    if a.size != b.size:
        return None, a.size, b.size  # size change
    diff = ImageChops.difference(a, b)
    # Fraction of pixels whose max channel delta exceeds the noise threshold.
    bands = diff.split()
    changed = None
    for band in bands:
        mask = band.point(lambda v: 255 if v > _CHANNEL_THRESHOLD else 0)
        changed = mask if changed is None else ImageChops.lighter(changed, mask)
    n_changed = sum(1 for px in changed.getdata() if px)
    frac = n_changed / (a.size[0] * a.size[1])
    if write_to is not None and n_changed:
        write_to.parent.mkdir(parents=True, exist_ok=True)
        # amplified heatmap so small deltas are visible
        ImageChops.multiply(diff, Image.new("RGB", a.size, (6, 6, 6))).save(write_to)
    return frac, a.size, b.size


def update_baseline() -> int:
    cur = _smoke_pngs(_SCRIPTS)
    if not cur:
        print("No scripts/smoke_*.png found to baseline. Render them first.")
        return 1
    _BASELINE.mkdir(exist_ok=True)
    for stem, path in cur.items():
        shutil.copy2(path, _BASELINE / path.name)
    print(f"Baseline updated: {len(cur)} render(s) copied to {_BASELINE.relative_to(_SCRIPTS.parent)}.")
    return 0


def compare(tol: float, write_diffs: bool) -> int:
    base = _smoke_pngs(_BASELINE)
    cur = _smoke_pngs(_SCRIPTS)
    if not base:
        print(f"No baseline at {_BASELINE}. Run --update-baseline first (on the host).")
        return 1
    worst = 0.0
    failed = False
    print(f"{'surface':<34} {'status'}")
    print("-" * 60)
    for stem in sorted(base):
        if stem not in cur:
            print(f"{stem:<34} MISSING current render")
            failed = True
            continue
        out = (_DIFFS / f"{stem}_diff.png") if write_diffs else None
        frac, asz, bsz = _diff_fraction(base[stem], cur[stem], out)
        is_golden = stem in GOLDEN
        if frac is None:
            print(f"{stem:<34} SIZE CHANGED {asz} -> {bsz}{'  [GOLDEN]' if is_golden else ''}")
            failed = True
            continue
        worst = max(worst, frac)
        pct = frac * 100
        if frac == 0:
            status = "identical"
        elif is_golden and frac > 0:
            status = f"CHANGED {pct:.3f}%  [GOLDEN — must not change]"
            failed = True
        elif frac > tol:
            status = f"CHANGED {pct:.3f}%  (> tol {tol*100:.2f}%)"
            failed = True
        else:
            status = f"~ {pct:.3f}% (within tol)"
        print(f"{stem:<34} {status}")
    extra = set(cur) - set(base)
    for stem in sorted(extra):
        print(f"{stem:<34} (new surface, no baseline)")
    print("-" * 60)
    print(f"worst delta: {worst*100:.3f}%   {'FAIL' if failed else 'OK'}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Compare smoke renders to the spec/92 baseline.")
    ap.add_argument("--update-baseline", action="store_true", help="copy current smoke_*.png into the baseline")
    ap.add_argument("--write-diffs", action="store_true", help="emit amplified diff heatmaps to scripts/_ui_diff/")
    ap.add_argument("--tol", type=float, default=0.001, help="allowed changed-pixel fraction for non-golden surfaces (default 0.001 = 0.1%%)")
    args = ap.parse_args(argv)
    if args.update_baseline:
        return update_baseline()
    return compare(args.tol, args.write_diffs)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
