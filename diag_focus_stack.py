"""diag_focus_stack.py — why didn't a folder get detected as a focus stack?

Runs Mira's REAL bracket-detection code (core.bucket_scanner +
core.bracket_detector) against a folder of photos and prints exactly what
the detector saw, frame by frame, plus the windowing/sequence result and a
plain-language diagnosis. No changes are made to anything — read-only.

Usage (from the repo root, with the same Python that runs Mira):

    python diag_focus_stack.py "D:\\Photos\\trips recovered\\2025 - Sales Junior\\stack corrected"

Paste the output back so the detection heuristic can be fixed precisely.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Make `core` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows console defaults to cp1252 which can't encode → / … / é etc.
# Reconfigure stdout/stderr to UTF-8 so the script never crashes on a glyph.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                              # noqa: BLE001
        pass

from core.folder_scanner import scan_folder
from core.bracket_detector import (
    detect_brackets,
    load_detector_config,
    _window_candidates,
    _classify_window,
    _same_context,
)
from core.bucket_scanner import _build_bracket_candidate_from_exif


def _fmt(v) -> str:
    if v is None:
        return "·"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python diag_focus_stack.py <folder>")
        return 2
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"Not a folder: {root}")
        return 2

    cfg = load_detector_config()
    print("=" * 78)
    print(f"FOLDER: {root}")
    print(
        f"CONFIG: window_seconds={cfg.window_seconds}  "
        f"min_sequence_size={cfg.min_sequence_size}  "
        f"max_sequence_size={cfg.max_sequence_size}  "
        f"require_monotonic_focus_distance={cfg.require_monotonic_focus_distance}"
    )
    print("=" * 78)

    print("Reading EXIF (bundled ExifTool)…")
    entries = scan_folder(root, recursive=False)
    if not entries:
        print("No photos found (check the path / extensions).")
        return 1
    candidates = [_build_bracket_candidate_from_exif(e) for e in entries]
    candidates_ts = [c for c in candidates if c.timestamp is not None]

    print(f"\nFRAMES: {len(candidates)} total, "
          f"{len(candidates_ts)} with a timestamp, "
          f"{len(candidates) - len(candidates_ts)} WITHOUT a timestamp.")

    # Aggregate signals.
    n_focus_tag = sum(1 for c in candidates if c.focus_bracket_tag_active)
    n_expo_tag = sum(1 for c in candidates if c.exposure_bracket_tag_active)
    n_focus_dist = sum(1 for c in candidates if c.focus_distance is not None)
    n_continuous = sum(1 for c in candidates if c.continuous_shooting_active)
    n_seq = sum(1 for c in candidates if c.sequence_number is not None)
    lenses = Counter(c.lens_name or "(empty)" for c in candidates)
    bodies = Counter(c.body_id or "(empty)" for c in candidates)
    orients = Counter(c.orientation for c in candidates)

    print("\nSIGNAL SUMMARY")
    print(f"  focus_bracket_tag_active  : {n_focus_tag}/{len(candidates)}")
    print(f"  exposure_bracket_tag_active: {n_expo_tag}/{len(candidates)}")
    print(f"  focus_distance present    : {n_focus_dist}/{len(candidates)}")
    print(f"  continuous_shooting_active: {n_continuous}/{len(candidates)}")
    print(f"  sequence_number present   : {n_seq}/{len(candidates)}")
    print(f"  lens_name values          : {dict(lenses)}")
    print(f"  body_id (Model) values    : {dict(bodies)}")
    print(f"  orientation values        : {dict(orients)}")

    # Per-frame table (first 12 + last 4 so big stacks stay readable).
    def _row(i, c):
        ts = c.timestamp.strftime("%H:%M:%S") if c.timestamp else "·"
        return (
            f"  [{i:>3}] {c.path.name:<28.28} ts={ts} "
            f"foc_tag={'Y' if c.focus_bracket_tag_active else '·'} "
            f"exp_tag={'Y' if c.exposure_bracket_tag_active else '·'} "
            f"fdist={_fmt(c.focus_distance):<6} "
            f"f={_fmt(c.aperture):<4} 1/s={_fmt(c.shutter_speed):<7} "
            f"iso={_fmt(c.iso):<5} cont={'Y' if c.continuous_shooting_active else '·'} "
            f"seq={_fmt(c.sequence_number)}"
        )

    print("\nPER-FRAME (capture-time order)")
    ordered = sorted(
        candidates,
        key=lambda c: (c.timestamp is None, c.timestamp or 0, c.path.name))
    show = ordered if len(ordered) <= 20 else ordered[:12] + ordered[-4:]
    skipped_marker_at = 12 if len(ordered) > 20 else None
    for i, c in enumerate(ordered):
        if skipped_marker_at is not None and i == skipped_marker_at:
            print(f"   … ({len(ordered) - 16} frames omitted) …")
        if c in show:
            print(_row(i, c))

    # Context consistency across the run (a single context change splits).
    print("\nCONTEXT CHECK (same lens+body+orientation across the run?)")
    if len(candidates_ts) >= 2:
        base = candidates_ts[0]
        mismatches = [
            c for c in candidates_ts[1:] if not _same_context(base, c)
        ]
        if mismatches:
            print(f"  {len(mismatches)} frame(s) DIFFER in context from frame 0 "
                  f"— this splits/blocks the window.")
        else:
            print("  OK — all frames share lens+body+orientation.")
        if not base.lens_name:
            print("  WARNING: lens_name is EMPTY — _same_context() refuses to "
                  "group frames with no lens, so NO window forms.")

    # Run the real windowing + classification.
    windows = _window_candidates(candidates, cfg)
    print(f"\nWINDOWING: {len(windows)} window(s) survived "
          f"(min={cfg.min_sequence_size}, max={cfg.max_sequence_size})")
    for wi, w in enumerate(windows):
        cls = _classify_window(w, cfg)
        verdict = (
            f"{cls.sequence_type.value} ({cls.detection_source}, "
            f"conf={cls.confidence})" if cls else "REJECTED (ambiguous)"
        )
        print(f"  window {wi}: {len(w)} frames → {verdict}")

    result = detect_brackets(candidates, cfg)
    n_focus = sum(1 for s in result.sequences if s.sequence_type.value == "focus")
    n_expo = sum(1 for s in result.sequences if s.sequence_type.value == "exposure")
    print(f"\nRESULT: {len(result.sequences)} sequence(s) "
          f"({n_focus} focus, {n_expo} exposure), "
          f"{len(result.orphans)} orphan(s).")

    # ── Plain-language diagnosis ──
    print("\nDIAGNOSIS")
    if n_focus >= 1 and len(result.orphans) == 0:
        print("  Detected as focus bracket(s). If it shows as MULTIPLE stacks, "
              f"the {cfg.max_sequence_size}-frame max_sequence_size cap split it.")
    else:
        reasons = []
        if len(candidates) - len(candidates_ts) > 0:
            reasons.append(
                "some frames have NO timestamp (excluded from windowing).")
        if n_focus_tag == 0 and n_focus_dist < 2:
            reasons.append(
                "no focus-bracket EXIF tag AND no/insufficient FocusDistance — "
                "the 'corrected'/re-saved files likely lost maker-note signals, "
                "so neither the explicit-tag nor the inferred monotonic-focus "
                "path can fire.")
        if any(not c.lens_name for c in candidates_ts):
            reasons.append(
                "empty lens_name on some frames — _same_context refuses to "
                "group them.")
        if len(candidates_ts) > cfg.max_sequence_size:
            reasons.append(
                f"{len(candidates_ts)} frames > max_sequence_size="
                f"{cfg.max_sequence_size}: the run is cut into chunks.")
        if not reasons:
            reasons.append(
                "window formed but classification rejected it — check the "
                "per-frame focus_distance monotonicity / constant exposure.")
        for r in reasons:
            print(f"  - {r}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
