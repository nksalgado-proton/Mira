"""Looks calibration workbench — the spec/54 §5 data experiment.

Ported from Miracraft's ``tools/compare_auto.py`` (audit pass
2026-06-10: new ``-2`` pairing pattern, case-insensitive stem matching,
``_catalog`` exclusion, sweep/analyze split) and extended from a
compare-only harness into the evidence pipeline for the Edit-phase
tone redesign (spec/54).

Three subcommands:

``census``
    Pair discovery only — prints what the sweep would process.
    Fast sanity check that the pairing patterns match the folder.

``sweep``
    Decode every (original, LRC-target) pair, mirror the live AUTO
    input (≤1280 px preview, ``core.photo_decoder`` semantics),
    extract per-pair evidence and write ``pairs.json`` into a run
    directory. Decode once, analyze many.

``analyze``
    Read a run's ``pairs.json`` and produce the spec/54 §5.3 evidence
    report: per-scenario residual structure of the CURRENT single fit
    (does it miss in systematic directions?), feature↔residual
    correlations, and k-means cluster candidates with per-cluster
    LRC-correction vectors. Writes ``analysis.md`` + ``analysis.json``
    next to the ``pairs.json``.

``fit``
    The spec/54 §5.2 step-3 fit phase: per-scenario clustering (same
    seeded path as ``analyze``), coordinate-descent constant fitting
    per cluster through ``core.photo_auto``'s real helpers (zero
    formula duplication), exact held-out validation at 1280 px, 4-up
    contact sheets (ORIGINAL | TODAY | FITTED | TARGET) and an
    integration-ready ``fit.json`` (router + per-cluster constants).

Per-pair evidence captured by ``sweep`` (all on Rec.709 luminance in
[0, 1], percentiles p1/p5/p25/p50/p75/p95/p99 + mean):

* ``orig``        — the original's stats (the formula's input world)
* ``lrc``         — the target's stats (where Nelson+LRC landed)
* ``ours``        — stats after the CURRENT formula's render
* ``correction``  — lrc − orig   (what the ground truth did)
* ``residual``    — ours − lrc   (how the current fit misses)
* ``rmse_ours_lrc`` / ``rmse_orig_lrc`` — 256-px luminance RMSE
  (the second one measures how much LRC intervened at all)
* ``params``      — the current formula's computed Params

Pure-Python; no Qt; safe headless. Usage::

    python -m tools.calibrate_looks census
    python -m tools.calibrate_looks sweep [--only Macro] [--limit 20]
    python -m tools.calibrate_looks analyze [--run <dir>]

Default data root: ``D:\\Photos\\Compare LRC Auto correction``
(spec/54 §5.1). Default run output: ``<root>/_calibration_runs/
<timestamp>/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from core.photo_auto import compute_auto_params
from core.photo_decoder import HEIC_EXTENSIONS, RAW_EXTENSIONS, decode_image
from core.photo_render import apply_params

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path("D:/Photos/Compare LRC Auto correction")

# Mirror the live editor's AUTO input size (adjustment_surface.
# PREVIEW_MAX_WIDTH) so the formula sees the same statistics it sees
# in the app.
PREVIEW_MAX_WIDTH = 1280

# Luminance percentile points used everywhere below.
PERCENTILES = (1, 5, 25, 50, 75, 95, 99)
STAT_KEYS = tuple(f"p{p}" for p in PERCENTILES) + ("mean",)

# Map LRC folder names → canonical scenario strings (Scenario enum
# values in core.vocabulary). Wildlife folders share one scenario —
# constants are owned per scenario, so analysis groups them too.
FOLDER_TO_SCENARIO: dict[str, str] = {
    "General":           "general",
    "Landscape":         "landscape",
    "Macro":             "macro",
    "Portrait":          "portrait",
    "Selfie":            "selfie",
    "Wildlife - Action": "wildlife",
    "Wildlife - Static": "wildlife",
}

_JPEG_EXTS = {".jpg", ".jpeg"}


# ── Pair discovery ────────────────────────────────────────────


@dataclass(frozen=True)
class Pair:
    """One (original, LRC-corrected target) match."""
    folder: str               # display name ("Wildlife - Action")
    scenario: str             # canonical scenario ("wildlife")
    name: str                 # display label (the original's stem)
    original: Path
    target: Path


def find_pairs(style_dir: Path) -> list[Pair]:
    """Pair originals with their LRC-corrected targets.

    Target patterns per original ``<stem>``, in priority order
    (spec/54 §5.1):

    1. ``<stem>-2.JPG`` — the primary pattern of the expanded set
       (LR's export collision counter).
    2. ``<stem>.JPG`` for RAW/HEIC originals — legacy pairs.
    3. ``<stem>(1).JPG`` — legacy GoPro pairs.

    Originalness is decided by EVIDENCE, not stem shape (census
    2026-06-10 found two stem-shape traps): RAW/HEIC files are always
    original candidates (LRC never exports those formats), and a JPEG
    whose own stem ends in ``-2``/``(1)`` can still be an original —
    LR collision-renames on export, so ``X-2.jpg`` paired with
    ``X-2-2.JPG`` is a real pair.

    Candidates claim targets in rank order — RAW/HEIC first, plain-stem
    JPEGs, then suffix-stemmed JPEGs — with each target claimed at most
    once, so chain-ambiguous stems resolve deterministically and the
    leftovers surface in the census orphan report instead of silently
    double-pairing.

    Matching is case-insensitive on stem and extension (the set mixes
    ``.JPG``/``.jpg``).
    """
    folder = style_dir.name
    scenario = FOLDER_TO_SCENARIO.get(folder, "general")

    files = [f for f in style_dir.iterdir() if f.is_file()]
    # Case-insensitive lookup: lowercased stem → path, jpegs only
    # (targets are always JPEG exports).
    jpeg_by_stem: dict[str, Path] = {
        f.stem.lower(): f for f in files
        if f.suffix.lower() in _JPEG_EXTS
    }
    rawish = RAW_EXTENSIONS | HEIC_EXTENSIONS

    def rank(f: Path) -> int:
        if f.suffix.lower() in rawish:
            return 0
        s = f.stem.lower()
        return 2 if (s.endswith("-2") or s.endswith("(1)")) else 1

    pairs: list[Pair] = []
    claimed: set[Path] = set()
    for src in sorted(files, key=lambda f: (rank(f), f.name.lower())):
        ext = src.suffix.lower()
        if ext not in rawish and ext not in _JPEG_EXTS:
            continue                       # not a photo we decode
        if src in claimed:
            continue                       # already someone's target
        stem = src.stem.lower()
        candidates = [jpeg_by_stem.get(f"{stem}-2")]
        if ext in rawish:
            candidates.append(jpeg_by_stem.get(stem))
        candidates.append(jpeg_by_stem.get(f"{stem}(1)"))
        target = next(
            (t for t in candidates
             if t is not None and t != src and t not in claimed),
            None)
        if target is not None:
            claimed.add(target)
            pairs.append(Pair(folder, scenario, src.stem, src, target))
    return pairs


def discover(root: Path, only: Optional[str] = None) -> list[Pair]:
    """All pairs under ``root``, folder-by-folder. ``_``/``.``-prefixed
    directories (``_catalog``, ``_compare_runs``, …) are not data."""
    style_dirs = [
        d for d in sorted(root.iterdir())
        if d.is_dir() and not d.name.startswith((".", "_"))
    ]
    if only:
        style_dirs = [d for d in style_dirs if d.name == only]
        if not style_dirs:
            raise SystemExit(f"--only {only!r} matched no style folder")
    pairs: list[Pair] = []
    for d in style_dirs:
        found = find_pairs(d)
        log.info("%s: %d pair(s)", d.name, len(found))
        pairs.extend(found)
    return pairs


# ── Stats helpers ─────────────────────────────────────────────


def _luminance(img: np.ndarray) -> np.ndarray:
    """Rec. 709 luminance in [0, 1] (matches core.photo_auto)."""
    return (
        0.2126 * img[..., 0].astype(np.float32)
        + 0.7152 * img[..., 1].astype(np.float32)
        + 0.0722 * img[..., 2].astype(np.float32)
    ) / 255.0


def _stats(lum: np.ndarray) -> dict[str, float]:
    """The luminance stat vector: PERCENTILES + mean."""
    vals = np.percentile(lum, PERCENTILES)
    out = {f"p{p}": float(v) for p, v in zip(PERCENTILES, vals)}
    out["mean"] = float(lum.mean())
    return out


def _downsample(img: np.ndarray, max_width: int = PREVIEW_MAX_WIDTH) -> np.ndarray:
    """Lanczos downsample to ≤max_width (mirrors the live preview)."""
    h, w = img.shape[:2]
    if w <= max_width:
        return img
    new_w = max_width
    new_h = int(round(h * new_w / w))
    pil = Image.fromarray(img).resize(
        (new_w, new_h), Image.Resampling.LANCZOS)
    return np.asarray(pil)


def _rmse_256(a: np.ndarray, b: np.ndarray) -> float:
    """Luminance RMSE on 256×256 squashed thumbnails — coarse,
    scene-invariant, tolerant of small size mismatches between the
    original and LRC's export."""
    ta = np.array(Image.fromarray(a).resize((256, 256), Image.Resampling.LANCZOS))
    tb = np.array(Image.fromarray(b).resize((256, 256), Image.Resampling.LANCZOS))
    return float(np.sqrt(np.mean((_luminance(ta) - _luminance(tb)) ** 2)))


# ── Sweep ─────────────────────────────────────────────────────


def _process_pair(p: Pair) -> dict:
    """Decode + extract one pair's evidence record (spec/54 §5.2)."""
    orig = _downsample(decode_image(p.original))
    target = _downsample(decode_image(p.target))

    orig_stats = _stats(_luminance(orig))
    lrc_stats = _stats(_luminance(target))

    params = compute_auto_params(
        orig, style=p.scenario if p.scenario != "general" else None)
    ours = apply_params(orig, params)
    ours_stats = _stats(_luminance(ours))

    return {
        "folder": p.folder,
        "scenario": p.scenario,
        "name": p.name,
        "original": str(p.original),
        "target": str(p.target),
        "orig": orig_stats,
        "lrc": lrc_stats,
        "ours": ours_stats,
        "correction": {
            k: lrc_stats[k] - orig_stats[k] for k in STAT_KEYS},
        "residual": {
            k: ours_stats[k] - lrc_stats[k] for k in STAT_KEYS},
        "rmse_ours_lrc": _rmse_256(ours, target),
        "rmse_orig_lrc": _rmse_256(orig, target),
        "params": {
            "exposure": params.exposure, "contrast": params.contrast,
            "highlights": params.highlights, "shadows": params.shadows,
            "whites": params.whites, "blacks": params.blacks,
        },
    }


def sweep(
    root: Path,
    out_dir: Path,
    *,
    only: Optional[str] = None,
    limit: Optional[int] = None,
    workers: int = 6,
) -> Path:
    """Process every discovered pair → ``<out_dir>/pairs.json``.

    Decode runs on a small thread pool (PIL/numpy/rawpy release the
    GIL for the heavy parts). Failures are logged and skipped — one
    unreadable file must not lose a 500-pair run."""
    pairs = discover(root, only)
    if limit:
        pairs = pairs[:limit]
    log.info("sweep: %d pair(s), %d worker(s)", len(pairs), workers)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    failures: list[dict] = []
    t0 = time.time()
    done = 0

    def _safe(p: Pair) -> Optional[dict]:
        try:
            return _process_pair(p)
        except Exception as exc:                       # noqa: BLE001
            log.warning("pair %s/%s failed: %s", p.folder, p.name, exc)
            failures.append(
                {"folder": p.folder, "name": p.name, "error": repr(exc)})
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rec in pool.map(_safe, pairs):
            done += 1
            if rec is not None:
                records.append(rec)
            if done % 25 == 0 or done == len(pairs):
                log.info("  %d/%d  (%.0fs)", done, len(pairs),
                         time.time() - t0)

    payload = {
        "root": str(root),
        "created": datetime.now().isoformat(timespec="seconds"),
        "preview_max_width": PREVIEW_MAX_WIDTH,
        "pair_count": len(records),
        "failures": failures,
        "pairs": records,
    }
    out_path = out_dir / "pairs.json"
    out_path.write_text(
        json.dumps(payload, indent=1), encoding="utf-8")
    log.info("sweep: wrote %s (%d ok, %d failed, %.0fs)",
             out_path, len(records), len(failures), time.time() - t0)
    return out_path


# ── Analyze ───────────────────────────────────────────────────

# Features the router will be able to see at runtime (original-photo
# statistics only). Clustering happens in this space.
FEATURE_KEYS = ("p50", "p25", "p99", "p1", "spread")


def _feature_row(rec: dict) -> list[float]:
    o = rec["orig"]
    return [o["p50"], o["p25"], o["p99"], o["p1"], o["p95"] - o["p5"]]


def _vec_table(records: list[dict], key: str) -> dict[str, dict]:
    """mean ± std of a stat-vector field across records."""
    out: dict[str, dict] = {}
    for k in STAT_KEYS:
        vals = np.array([r[key][k] for r in records], dtype=np.float64)
        out[k] = {
            "mean": float(vals.mean()),
            "std": float(vals.std()),
            "n": len(vals),
        }
    return out


def _correlations(records: list[dict]) -> dict[str, float]:
    """Pearson r between original features and residual components —
    a strong |r| means the single fit misses *as a function of* the
    photo's statistics: direct 'average is wrong' evidence even
    without discrete clusters."""
    out: dict[str, float] = {}
    feats = np.array([_feature_row(r) for r in records], dtype=np.float64)
    for ri, rk in enumerate(("p50", "p99", "p25", "mean")):
        res = np.array(
            [r["residual"][rk] for r in records], dtype=np.float64)
        if res.std() < 1e-9:
            continue
        for fi, fk in enumerate(FEATURE_KEYS):
            col = feats[:, fi]
            if col.std() < 1e-9:
                continue
            r = float(np.corrcoef(col, res)[0, 1])
            out[f"orig_{fk} ~ residual_{rk}"] = round(r, 3)
    return out


def _kmeans(
    feats: np.ndarray, k: int, *, seed: int = 7, tries: int = 8,
) -> Optional[np.ndarray]:
    """K-means labels on z-scored features. scipy's kmeans2 with a
    few seeded restarts, best inertia wins. Returns None when a try
    degenerates (empty cluster) and no try survives."""
    from scipy.cluster.vq import kmeans2

    mu = feats.mean(axis=0)
    sd = feats.std(axis=0)
    sd[sd < 1e-9] = 1.0
    z = (feats - mu) / sd

    best_labels: Optional[np.ndarray] = None
    best_inertia = np.inf
    for t in range(tries):
        centroids, labels = kmeans2(
            z, k, minit="++", seed=seed + t)
        sizes = np.bincount(labels, minlength=k)
        if sizes.min() == 0:
            continue
        inertia = float(
            ((z - centroids[labels]) ** 2).sum())
        if inertia < best_inertia:
            best_inertia = inertia
            best_labels = labels
    return best_labels


def _cluster_report(
    records: list[dict], k: int, *, min_cluster: int,
) -> Optional[list[dict]]:
    """Cluster a scenario's pairs in feature space; per cluster report
    size, feature centroid, mean correction vector, mean residual
    vector, and the exemplars nearest the centroid. ``None`` when the
    clustering degenerates (any cluster below ``min_cluster``)."""
    feats = np.array([_feature_row(r) for r in records], dtype=np.float64)
    labels = _kmeans(feats, k)
    if labels is None:
        return None
    sizes = np.bincount(labels, minlength=k)
    if sizes.min() < min_cluster:
        return None

    clusters: list[dict] = []
    for c in range(k):
        idx = np.where(labels == c)[0]
        sub = [records[i] for i in idx]
        centroid = feats[idx].mean(axis=0)
        # Exemplars: nearest the centroid, for targeted contact
        # sheets in the fit phase.
        d = np.linalg.norm(feats[idx] - centroid, axis=1)
        nearest = [sub[i]["name"] for i in np.argsort(d)[:5]]
        clusters.append({
            "size": int(len(idx)),
            "feature_centroid": {
                fk: round(float(v), 3)
                for fk, v in zip(FEATURE_KEYS, centroid)},
            "correction_mean": {
                k2: round(v["mean"], 3)
                for k2, v in _vec_table(sub, "correction").items()},
            "residual_mean": {
                k2: round(v["mean"], 3)
                for k2, v in _vec_table(sub, "residual").items()},
            "rmse_ours_lrc_mean": round(float(np.mean(
                [r["rmse_ours_lrc"] for r in sub])), 3),
            "exemplars": nearest,
            "folders": sorted({r["folder"] for r in sub}),
        })
    return clusters


def _fmt_vec(vec: dict, keys: tuple[str, ...] = ("mean", "p1", "p25", "p50", "p75", "p99")) -> str:
    return "  ".join(f"{k}={vec[k]:+.3f}" for k in keys if k in vec)


def analyze(run_dir: Path) -> Path:
    """The spec/54 §5.3 evidence report for one sweep run."""
    payload = json.loads((run_dir / "pairs.json").read_text(encoding="utf-8"))
    records: list[dict] = payload["pairs"]
    by_scenario: dict[str, list[dict]] = {}
    for r in records:
        by_scenario.setdefault(r["scenario"], []).append(r)

    report: dict = {"run": str(run_dir), "scenarios": {}}
    lines: list[str] = [
        "# Looks calibration — evidence report (spec/54 §5.3)",
        "",
        f"Run: `{run_dir}`  —  {payload['pair_count']} pairs, "
        f"{len(payload.get('failures', []))} failures",
        "",
        "Question: does the CURRENT single per-style fit miss in",
        "systematic directions, and does the miss vary with the",
        "photo's own statistics (= cluster structure = layer A is real)?",
        "",
        "All numbers are Rec.709 luminance in [0,1]. `correction` =",
        "what LRC+Nelson did (lrc − orig). `residual` = how the current",
        "fit misses (ours − lrc); positive = we are brighter than the",
        "target at that point.",
        "",
    ]

    for scenario in sorted(by_scenario):
        recs = by_scenario[scenario]
        n = len(recs)
        residual = _vec_table(recs, "correction")
        residual_cur = _vec_table(recs, "residual")
        corr = _correlations(recs)
        rmse_ours = float(np.mean([r["rmse_ours_lrc"] for r in recs]))
        rmse_orig = float(np.mean([r["rmse_orig_lrc"] for r in recs]))

        sc: dict = {
            "pair_count": n,
            "folders": sorted({r["folder"] for r in recs}),
            "correction_mean_std": {
                k: {"mean": round(v["mean"], 3), "std": round(v["std"], 3)}
                for k, v in residual.items()},
            "residual_mean_std": {
                k: {"mean": round(v["mean"], 3), "std": round(v["std"], 3)}
                for k, v in residual_cur.items()},
            "rmse_ours_lrc_mean": round(rmse_ours, 3),
            "rmse_orig_lrc_mean": round(rmse_orig, 3),
            "feature_residual_correlations": corr,
            "clusterings": {},
        }

        lines += [
            f"## {scenario}  ({n} pairs — {', '.join(sc['folders'])})",
            "",
            f"- LRC intervention size (rmse orig→lrc): **{rmse_orig:.3f}**"
            f"  |  current fit's distance to target (rmse ours→lrc):"
            f" **{rmse_ours:.3f}**",
            f"- correction (target) mean: {_fmt_vec({k: v['mean'] for k, v in residual.items()})}",
            f"- residual (current fit)  : {_fmt_vec({k: v['mean'] for k, v in residual_cur.items()})}",
            "- residual std            : "
            + "  ".join(f"{k}={residual_cur[k]['std']:.3f}"
                        for k in ("mean", "p25", "p50", "p99")),
            "",
            "Feature ↔ residual correlations (|r| ≥ 0.4 bolded):",
            "",
        ]
        for key, r in sorted(corr.items(), key=lambda kv: -abs(kv[1])):
            mark = "**" if abs(r) >= 0.4 else ""
            lines.append(f"- {mark}{key}: r = {r:+.3f}{mark}")
        lines.append("")

        # Cluster candidates — k=2 always, k=3 when the scenario has
        # enough pairs for three non-trivial clusters.
        min_cluster = max(5, int(round(0.08 * n)))
        for k in (2, 3):
            if n < k * min_cluster:
                continue
            clusters = _cluster_report(recs, k, min_cluster=min_cluster)
            if clusters is None:
                lines.append(f"k={k}: degenerate (cluster < {min_cluster}) — skipped")
                lines.append("")
                continue
            sc["clusterings"][f"k{k}"] = clusters
            lines.append(f"### k = {k}")
            lines.append("")
            for i, c in enumerate(clusters):
                lines += [
                    f"**Cluster {i}** — {c['size']} pairs "
                    f"({', '.join(c['folders'])})",
                    f"- features: " + "  ".join(
                        f"{fk}={fv}" for fk, fv in
                        c["feature_centroid"].items()),
                    f"- correction mean: {_fmt_vec(c['correction_mean'])}",
                    f"- residual mean  : {_fmt_vec(c['residual_mean'])}"
                    f"  (rmse {c['rmse_ours_lrc_mean']})",
                    f"- exemplars: {', '.join(c['exemplars'])}",
                    "",
                ]
        report["scenarios"][scenario] = sc

    out_md = run_dir / "analysis.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    (run_dir / "analysis.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    log.info("analyze: wrote %s", out_md)
    return out_md


# ── Fit (spec/54 §5.2 step 3) ─────────────────────────────────
#
# Per-scenario, per-cluster constant optimization. The fitter NEVER
# re-implements the formula: candidate constant sets are real
# ``_TuningConstants`` instances pushed through ``core.photo_auto``'s
# own per-slider helpers, and the render is the real tone LUT from
# ``core.photo_render._tone_curve`` (AUTO never sets saturation /
# vibrance / sharpness, so the LUT IS the full apply_params for AUTO
# params). Fitted constants therefore behave bit-identically when
# they land in the app.
#
# Optimization loop: seeded coordinate descent with shrinking bounded
# line search — the mechanical version of the historical "nudge one
# constant, re-run the harness, eyeball" loop. The fast objective
# applies the per-pair tone LUT to a cached 256-px thumb; the
# REPORTED validation numbers are recomputed exactly with full
# ``apply_params`` renders at the app-faithful 1280 px.

import dataclasses as _dc

from core.photo_auto import (
    _TuningConstants,
    _compute_blacks,
    _compute_contrast,
    _compute_exposure,
    _compute_highlights,
    _compute_shadows,
    _compute_whites,
    _resolve_tuning,
)
from core.photo_render import Params, _tone_curve

# Default cluster count per scenario (evidence run 2026-06-10: the
# big folders support a meaningful third mode; the small ones don't).
K_BY_SCENARIO: dict[str, int] = {
    "macro": 3, "wildlife": 3, "portrait": 3,
    "general": 2, "landscape": 2, "selfie": 2,
}

# The fitted constants and their sane photographic bounds. Order is
# the coordinate-descent sweep order: exposure first (the dominant
# axis per the evidence run), then the point controls, then the
# masked lifts, then contrast.
FIT_BOUNDS: tuple[tuple[str, float, float], ...] = (
    ("exposure_target",             0.25, 0.55),
    ("exposure_max_ev",             0.10, 1.50),
    ("exposure_highlight_ceiling",  0.80, 0.99),
    ("whites_p99_target",           0.75, 0.98),
    ("whites_gain",                 0.0,  300.0),
    ("blacks_p1_target",            0.00, 0.15),
    ("blacks_gain",                 0.0,  400.0),
    ("shadows_p25_threshold",       0.05, 0.45),
    ("shadows_gain",                0.0,  800.0),
    ("shadows_max",                 0.0,  60.0),
    ("highlights_p75_threshold",    0.50, 0.95),
    ("highlights_gain",             0.0,  800.0),
    ("highlights_max",              0.0,  100.0),
    ("contrast_compression_threshold", 0.30, 0.90),
    ("contrast_gain",               0.0,  300.0),
    ("contrast_max",                0.0,  60.0),
)

# Stat-vector components the objective scores (equal weights — the
# contact sheets are the tie-breaker for perceptual disagreements).
OBJECTIVE_KEYS = ("mean", "p1", "p25", "p50", "p75", "p99")

THUMB_WIDTH = 256          # fast-objective render size
SPLIT_SEED = 11            # train/val shuffle
SHEET_TILE = 560           # contact-sheet tile width (Nelson 2026-06-10:
                           # 320 was too small to judge subtle tone)
SHEET_MAX_ROWS = 10


def _params_for(stats: dict, tuning: _TuningConstants) -> Params:
    """The runtime formula, fed from a precomputed stat dict — same
    helpers, same order, same caps as ``compute_auto_params``."""
    return Params(
        exposure=_compute_exposure(
            stats["p50"], tuning, p99=stats["p99"]),
        contrast=_compute_contrast(stats["p5"], stats["p95"], tuning),
        highlights=_compute_highlights(stats["p75"], tuning),
        shadows=_compute_shadows(stats["p25"], tuning),
        whites=_compute_whites(stats["p99"], tuning),
        blacks=_compute_blacks(stats["p1"], tuning),
    )


_LUT_RAMP = np.arange(256, dtype=np.float32) / 255.0


def _toned_stats(thumb: np.ndarray, params: Params) -> dict[str, float]:
    """Stat vector of ``thumb`` (uint8 RGB) after the tone LUT —
    the fast-objective stand-in for a full apply_params render."""
    lut = np.clip(_tone_curve(_LUT_RAMP.copy(), params), 0.0, 1.0)
    toned = lut[thumb]                       # (H, W, 3) float32
    lum = (0.2126 * toned[..., 0] + 0.7152 * toned[..., 1]
           + 0.0722 * toned[..., 2])
    vals = np.percentile(lum, PERCENTILES)
    out = {f"p{p}": float(v) for p, v in zip(PERCENTILES, vals)}
    out["mean"] = float(lum.mean())
    return out


def _objective_one(ours: dict, target: dict) -> float:
    """Mean squared stat delta for one pair."""
    return float(np.mean(
        [(ours[k] - target[k]) ** 2 for k in OBJECTIVE_KEYS]))


def _cluster_objective(
    tuning: _TuningConstants,
    recs: list[dict],
    thumbs: list[np.ndarray],
) -> float:
    """RMS stat distance to target across a cluster (fast path)."""
    total = 0.0
    for rec, thumb in zip(recs, thumbs):
        params = _params_for(rec["orig"], tuning)
        total += _objective_one(_toned_stats(thumb, params), rec["lrc"])
    return float(np.sqrt(total / max(len(recs), 1)))


def _fit_cluster(
    recs: list[dict],
    thumbs: list[np.ndarray],
    start: _TuningConstants,
    *,
    sweeps: int = 5,
    points: int = 7,
) -> tuple[_TuningConstants, float]:
    """Coordinate descent from ``start``: per sweep, line-search each
    constant over a shrinking bounded window; keep improvements."""
    current = start
    best = _cluster_objective(current, recs, thumbs)
    for s in range(sweeps):
        shrink = 0.6 * (0.5 ** s)
        improved = False
        for field, lo, hi in FIT_BOUNDS:
            cur_v = getattr(current, field)
            half = (hi - lo) * shrink / 2.0
            cands = np.clip(
                np.linspace(cur_v - half, cur_v + half, points), lo, hi)
            for v in cands:
                if v == cur_v:
                    continue
                trial = _dc.replace(current, **{field: float(v)})
                obj = _cluster_objective(trial, recs, thumbs)
                if obj < best - 1e-7:
                    best, current, improved = obj, trial, True
        if not improved:
            break
    return current, best


def _neutral_start(base: _TuningConstants) -> _TuningConstants:
    """A restrained second start — important for 'leave-it-alone'
    clusters where the optimum is near do-nothing and the shipped
    defaults are a poor basin."""
    return _dc.replace(
        base,
        exposure_max_ev=0.2, whites_gain=40.0, blacks_gain=60.0,
        shadows_gain=120.0, shadows_max=12.0,
        highlights_gain=120.0, highlights_max=20.0,
        contrast_gain=40.0, contrast_max=15.0,
    )


def _split_train_val(n: int) -> tuple[list[int], list[int]]:
    """Seeded 80/20 split (val ≥ 2 when the cluster allows it)."""
    rng = np.random.default_rng(SPLIT_SEED)
    order = list(rng.permutation(n))
    n_val = max(2, int(round(0.2 * n))) if n >= 5 else 1
    return order[n_val:], order[:n_val]


def _build_fit_cache(
    records: list[dict], cache_path: Path, *, workers: int = 6,
) -> list[np.ndarray]:
    """Decode every pair's ORIGINAL once → 256-px thumbs, cached as
    a compressed npz keyed by pair index (pairs.json order)."""
    if cache_path.exists():
        log.info("fit: loading thumb cache %s", cache_path)
        z = np.load(cache_path)
        return [z[f"t{i}"] for i in range(len(records))]
    log.info("fit: building thumb cache (%d decodes)…", len(records))
    t0 = time.time()

    def _one(rec: dict) -> np.ndarray:
        img = decode_image(Path(rec["original"]))
        return _downsample(img, THUMB_WIDTH)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        thumbs = list(pool.map(_one, records))
    np.savez_compressed(
        cache_path, **{f"t{i}": t for i, t in enumerate(thumbs)})
    log.info("fit: cache built (%.0fs) → %s", time.time() - t0, cache_path)
    return thumbs


# ── Spread (spec/54 §5.2 step 4) ──────────────────────────────
#
# The B-layer candidates: designed Params biases applied ON TOP of
# the A-routed Natural correction (spec/54 §3.2-3.3 — fitted center,
# designed spread). Each includes a character component
# (contrast/vibrance) that differs even at zero correction (spec
# §3.4 convergence guard). Iterate by editing + re-running.
#
# v1 (Nelson in-app checkpoint 2026-06-10): v0's ±0.30-0.35 EV read
# as "a bit too bright / a bit too dark — I would always choose
# Natural". Brightness push halved; character components kept close
# so the grid tiles stay visibly distinct.

SPREADS: tuple[tuple[str, "Params"], ...] = (
    ("Brighter", Params(exposure=0.18, shadows=8.0, whites=6.0,
                        contrast=-4.0, vibrance=8.0)),
    ("Deeper",   Params(exposure=-0.15, blacks=-10.0, contrast=10.0,
                        highlights=-5.0, vibrance=10.0)),
)


def _add_params(a: Params, b: Params) -> Params:
    """Component-wise sum — the mood bias rides on the Natural
    correction. AUTO never sets vibrance, so the bias's vibrance is
    effectively absolute character."""
    return Params(**{
        f: getattr(a, f) + getattr(b, f)
        for f in a.__dataclass_fields__})


def _route_cluster(feat_row: list[float], sc: dict) -> int:
    """The runtime router: nearest fitted centroid in the scenario's
    z-scored feature space. This is the exact logic that lands in
    core/photo_auto when the A-layer integrates."""
    mu = np.array(sc["zscore"]["mean"], dtype=np.float64)
    sd = np.array(sc["zscore"]["std"], dtype=np.float64)
    z = (np.array(feat_row, dtype=np.float64) - mu) / sd
    cents = np.array(sc["centroids"], dtype=np.float64)
    return int(np.argmin(((cents - z) ** 2).sum(axis=1)))


def spread(run_dir: Path, *, samples: int = 8) -> Path:
    """Render the Look-spread sheets: per scenario, ``samples``
    photos evenly spaced across the brightness range (orig p50),
    each shown as ORIGINAL | NATURAL | <each SPREAD mood> — the
    future 2×2 grid moment, flattened for eyeballing."""
    payload = json.loads(
        (run_dir / "pairs.json").read_text(encoding="utf-8"))
    fit_path = run_dir / "fit.json"
    if not fit_path.exists():
        raise SystemExit("spread needs fit.json — run `fit` first")
    fitj = json.loads(fit_path.read_text(encoding="utf-8"))

    by_scenario: dict[str, list[dict]] = {}
    for r in payload["pairs"]:
        by_scenario.setdefault(r["scenario"], []).append(r)

    sheets_dir = run_dir / "sheets"
    sheets_dir.mkdir(exist_ok=True)
    col_labels = ("ORIGINAL", "NATURAL",
                  *(n.upper() for n, _ in SPREADS))
    html: list[str] = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>Look spread — Natural + mood candidates</title>",
        "<style>",
        "body{background:#202225;color:#ddd;font-family:Segoe UI,sans-serif;",
        " margin:0;padding:24px;max-width:1380px}",
        "h1{font-size:20px} h2{font-size:16px;margin:28px 0 4px}",
        ".meta{color:#9ab;font-size:13px;margin:0 0 8px}",
        "img{width:100%;border:1px solid #444;border-radius:4px;",
        " margin-bottom:8px}",
        "</style>",
        "<h1>Look spread — the future chooser, flattened</h1>",
        "<p class='meta'>Each row: ORIGINAL | NATURAL (fitted) | "
        + " | ".join(n.upper() for n, _ in SPREADS)
        + ". Rows span the scenario's brightness range. Click a sheet "
        "for full resolution.</p>",
    ]

    for scenario in sorted(by_scenario):
        sc = fitj["scenarios"].get(scenario)
        if sc is None:
            continue
        recs = sorted(by_scenario[scenario],
                      key=lambda r: r["orig"]["p50"])
        pick = sorted({int(round(i)) for i in np.linspace(
            0, len(recs) - 1, min(samples, len(recs)))})
        base = _resolve_tuning(
            scenario if scenario != "general" else None)
        rows: list[dict] = []
        for i in pick:
            rec = recs[i]
            try:
                orig = _downsample(decode_image(Path(rec["original"])))
            except Exception:                          # noqa: BLE001
                log.exception("spread: decode failed %s", rec["name"])
                continue
            ci = _route_cluster(_feature_row(rec), sc)
            tuning = _dc.replace(base, **{
                f: sc["clusters"][ci]["constants"][f]
                for f, _, _ in FIT_BOUNDS})
            nat = _params_for(rec["orig"], tuning)
            tiles = [_tile_pil(orig), _tile_pil(apply_params(orig, nat))]
            for _name, bias in SPREADS:
                tiles.append(_tile_pil(
                    apply_params(orig, _add_params(nat, bias))))
            rows.append({
                "label": (f"{rec['name']}  |  p50 {rec['orig']['p50']:.2f}"
                          f"  |  routed c{ci}"),
                "tiles": tiles,
            })
        out_png = sheets_dir / f"spread_{scenario}.png"
        _sheet_for_cluster(
            rows, f"{scenario} — Look spread ({len(rows)} samples)",
            out_png, col_labels=col_labels)
        html += [
            f"<h2>{scenario}</h2>",
            f"<a href='sheets/spread_{scenario}.png' target='_blank'>"
            f"<img loading='lazy' src='sheets/spread_{scenario}.png'></a>",
        ]

    out = run_dir / "spread.html"
    out.write_text("\n".join(html), encoding="utf-8")
    log.info("spread: wrote %s", out)
    return out


# ── Creative-filter recipes (spec/55, v0 candidates) ──────────
#
# The nine locked filters (Nelson 2026-06-10), recipes as
# FilterRecipe dicts (core.photo_render.FilterRecipe.from_dict).
# Applied ON TOP of the Natural render (pipeline: correction → mood
# → filter). Iterate by editing + re-running ``filters``. ``crisp``
# carries per-style overrides (macro: specimen-dark; wildlife: warm
# feather detail) — the _TUNING_BY_STYLE pattern.

FILTERS: tuple[tuple[str, dict], ...] = (
    ("vivid", {"params": {"saturation": 18.0, "vibrance": 20.0,
                          "contrast": 8.0}}),
    ("bw", {"bw_mix": (0.42, 0.40, 0.18),
            "params": {"contrast": 15.0}}),
    ("sepia", {"bw_mix": (0.38, 0.42, 0.20), "tint": (1.08, 1.0, 0.82),
               "params": {"contrast": 6.0}, "fade": 0.04}),
    ("faded", {"params": {"saturation": -25.0, "contrast": -8.0},
               "fade": 0.10, "tint": (1.02, 1.0, 0.96)}),
    ("golden", {"tint": (1.10, 1.02, 0.86),
                "split_highlights": (1.06, 1.0, 0.92),
                "params": {"contrast": -4.0, "vibrance": 6.0}}),
    ("cinema", {"split_shadows": (0.92, 1.0, 1.12),
                "split_highlights": (1.08, 1.02, 0.92),
                "params": {"saturation": -15.0, "contrast": 6.0}}),
    ("bleach", {"params": {"saturation": -55.0, "contrast": 22.0,
                           "exposure": -0.05}}),
    ("dramatic", {"clarity": 0.55,
                  "params": {"contrast": 10.0, "saturation": -8.0,
                             "highlights": -20.0}}),
    ("crisp", {"clarity": 0.35, "vignette": 0.18,
               "params": {"vibrance": 15.0, "blacks": -8.0}}),
)

FILTER_STYLE_OVERRIDES: dict[str, dict[str, dict]] = {
    "crisp": {
        "macro": {"clarity": 0.40, "vignette": 0.22,
                  "params": {"vibrance": 18.0, "blacks": -16.0}},
        "wildlife": {"clarity": 0.40, "vignette": 0.15,
                     "tint": (1.04, 1.0, 0.94),
                     "params": {"vibrance": 14.0, "blacks": -6.0}},
    },
}

FILTER_TILE = 300          # filters sheet: 11 columns — smaller tiles


def _filter_recipe_for(key: str, scenario: str) -> dict:
    """Resolve a filter's recipe dict for a scenario (per-style
    override when one exists; base otherwise)."""
    override = FILTER_STYLE_OVERRIDES.get(key, {}).get(scenario)
    if override is not None:
        return override
    return dict(FILTERS)[key]


def filters(run_dir: Path, *, samples: int = 6) -> Path:
    """Render the spec/55 candidate sheets: per scenario, ``samples``
    photos across the brightness range, each as NATURAL + the nine
    filters applied on top of it. Columns are many — tiles shrink to
    FILTER_TILE; click a sheet in filters.html for full size."""
    from core.photo_render import FilterRecipe, apply_filter

    payload = json.loads(
        (run_dir / "pairs.json").read_text(encoding="utf-8"))
    fit_path = run_dir / "fit.json"
    if not fit_path.exists():
        raise SystemExit("filters needs fit.json — run `fit` first")
    fitj = json.loads(fit_path.read_text(encoding="utf-8"))

    by_scenario: dict[str, list[dict]] = {}
    for r in payload["pairs"]:
        by_scenario.setdefault(r["scenario"], []).append(r)

    sheets_dir = run_dir / "sheets"
    sheets_dir.mkdir(exist_ok=True)
    keys = [k for k, _ in FILTERS]
    col_labels = ("NATURAL", *(k.upper() for k in keys))
    html: list[str] = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>Creative filters — v0 candidates</title>",
        "<style>",
        "body{background:#202225;color:#ddd;font-family:Segoe UI,sans-serif;",
        " margin:0;padding:24px;max-width:1380px}",
        "h1{font-size:20px} h2{font-size:16px;margin:28px 0 4px}",
        ".meta{color:#9ab;font-size:13px;margin:0 0 8px}",
        "img{width:100%;border:1px solid #444;border-radius:4px;",
        " margin-bottom:8px}",
        "</style>",
        "<h1>Creative filters — the nine, on your photos</h1>",
        "<p class='meta'>Each row: NATURAL then the nine filters on top "
        "of it. Click a sheet for full resolution. CRISP uses its "
        "macro/wildlife per-style recipe where the scenario matches.</p>",
    ]

    global SHEET_TILE
    prev_tile = SHEET_TILE
    SHEET_TILE = FILTER_TILE
    try:
        for scenario in sorted(by_scenario):
            sc = fitj["scenarios"].get(scenario)
            if sc is None:
                continue
            recs = sorted(by_scenario[scenario],
                          key=lambda r: r["orig"]["p50"])
            pick = sorted({int(round(i)) for i in np.linspace(
                0, len(recs) - 1, min(samples, len(recs)))})
            base = _resolve_tuning(
                scenario if scenario != "general" else None)
            rows: list[dict] = []
            for i in pick:
                rec = recs[i]
                try:
                    orig = _downsample(
                        decode_image(Path(rec["original"])))
                except Exception:                      # noqa: BLE001
                    log.exception("filters: decode failed %s", rec["name"])
                    continue
                ci = _route_cluster(_feature_row(rec), sc)
                tuning = _dc.replace(base, **{
                    f: sc["clusters"][ci]["constants"][f]
                    for f, _, _ in FIT_BOUNDS})
                natural = apply_params(
                    orig, _params_for(rec["orig"], tuning))
                tiles = [_tile_pil(natural)]
                for key in keys:
                    recipe = FilterRecipe.from_dict(
                        _filter_recipe_for(key, scenario))
                    tiles.append(_tile_pil(apply_filter(natural, recipe)))
                rows.append({
                    "label": f"{rec['name']}  |  p50 {rec['orig']['p50']:.2f}",
                    "tiles": tiles,
                })
            out_png = sheets_dir / f"filters_{scenario}.png"
            _sheet_for_cluster(
                rows, f"{scenario} — filter candidates "
                f"({len(rows)} samples)", out_png, col_labels=col_labels)
            html += [
                f"<h2>{scenario}</h2>",
                f"<a href='sheets/filters_{scenario}.png' target='_blank'>"
                f"<img loading='lazy' src='sheets/filters_{scenario}.png'>"
                f"</a>",
            ]
    finally:
        SHEET_TILE = prev_tile

    out = run_dir / "filters.html"
    out.write_text("\n".join(html), encoding="utf-8")
    log.info("filters: wrote %s", out)
    return out


# ── Export (fit.json → core/photo_looks_data.py) ──────────────


def export_data_module(run_dir: Path) -> Path:
    """Generate ``core/photo_looks_data.py`` from the run's
    ``fit.json`` + the SPREADS biases — the shipped form of the
    calibration (spec/54 §3). The engine imports the generated
    module; this tool is the only writer."""
    fit_path = run_dir / "fit.json"
    if not fit_path.exists():
        raise SystemExit("export needs fit.json — run `fit` first")
    fitj = json.loads(fit_path.read_text(encoding="utf-8"))

    def _tup(vals, nd=6) -> str:
        return "(" + ", ".join(repr(round(float(v), nd)) for v in vals) \
            + ("," if len(vals) == 1 else "") + ")"

    lines: list[str] = [
        '"""Fitted Looks calibration data — GENERATED, do not edit.',
        "",
        "Generated by ``python -m tools.calibrate_looks export`` from",
        f"run ``{run_dir.name}`` (fitted {fitj.get('created', '?')}).",
        "Regenerate after any refit (spec/54 §5). The router + cluster",
        "constants are the A-layer; LOOK_BIASES are the B-layer",
        '(spec/54 §3).  Keys in LOOK_BIASES are INTERNAL identifiers —',
        "display names + final vocabulary belong to the UI slice",
        '(spec/54 §7).',
        '"""',
        "",
        "FEATURE_KEYS = (\"p50\", \"p25\", \"p99\", \"p1\", \"spread\")",
        "",
        "ROUTER = {",
    ]
    for scenario in sorted(fitj["scenarios"]):
        sc = fitj["scenarios"][scenario]
        lines.append(f"    {scenario!r}: {{")
        lines.append(
            f"        \"zscore_mean\": {_tup(sc['zscore']['mean'])},")
        lines.append(
            f"        \"zscore_std\": {_tup(sc['zscore']['std'])},")
        lines.append("        \"centroids\": (")
        for cent in sc["centroids"]:
            lines.append(f"            {_tup(cent)},")
        lines.append("        ),")
        lines.append("        \"clusters\": (")
        for cl in sc["clusters"]:
            consts = ", ".join(
                f"{f!r}: {round(cl['constants'][f], 6)!r}"
                for f, _, _ in FIT_BOUNDS)
            lines.append("            {" + consts + "},")
        lines.append("        ),")
        lines.append("    },")
    lines.append("}")
    lines.append("")
    lines.append("# Look biases (spec/54 §3.4 — v1, Nelson-accepted "
                 "2026-06-10).")
    lines.append("LOOK_BIASES = {")
    for name, bias in SPREADS:
        fields = ", ".join(
            f"{f!r}: {getattr(bias, f)!r}"
            for f in bias.__dataclass_fields__
            if getattr(bias, f) != 0.0)
        lines.append(f"    {name.lower()!r}: {{{fields}}},")
    lines.append("}")
    lines.append("")

    def _recipe_literal(d: dict) -> str:
        parts = []
        for k, v in d.items():
            if isinstance(v, tuple):
                parts.append(f"{k!r}: {tuple(round(float(x), 4) for x in v)!r}")
            elif isinstance(v, dict):
                inner = ", ".join(
                    f"{k2!r}: {round(float(v2), 4)!r}" for k2, v2 in v.items())
                parts.append(f"{k!r}: {{{inner}}}")
            else:
                parts.append(f"{k!r}: {round(float(v), 4)!r}")
        return "{" + ", ".join(parts) + "}"

    lines.append("# Creative-filter recipes (spec/55 — the locked nine, "
                 "v0 recipes Nelson-approved 2026-06-10).")
    lines.append("# Shape: key -> {'base': recipe, 'by_style': {style: "
                 "recipe}} (FilterRecipe.from_dict).")
    lines.append("FILTER_RECIPES = {")
    for key, recipe in FILTERS:
        lines.append(f"    {key!r}: {{")
        lines.append(f"        \"base\": {_recipe_literal(recipe)},")
        overrides = FILTER_STYLE_OVERRIDES.get(key)
        if overrides:
            lines.append("        \"by_style\": {")
            for style, o in sorted(overrides.items()):
                lines.append(
                    f"            {style!r}: {_recipe_literal(o)},")
            lines.append("        },")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    out = Path(__file__).resolve().parent.parent / "core" \
        / "photo_looks_data.py"
    out.write_text("\n".join(lines), encoding="utf-8")
    log.info("export: wrote %s", out)
    return out


def _tile_pil(img: np.ndarray, width: int = SHEET_TILE) -> Image.Image:
    h, w = img.shape[:2]
    return Image.fromarray(img).resize(
        (width, max(1, int(h * width / w))), Image.Resampling.LANCZOS)


def _sheet_for_cluster(
    rows: list[dict], title: str, out_path: Path,
    col_labels: tuple[str, ...] = ("ORIGINAL", "TODAY", "FITTED", "TARGET"),
) -> None:
    """N-up contact sheet. ``rows`` carry pre-tiled PIL images + a
    label; ``col_labels`` name the columns (fit sheets use the
    default; spread sheets pass ORIGINAL | NATURAL | <moods>)."""
    from PIL import ImageDraw, ImageFont
    if not rows:
        return
    try:
        font = ImageFont.truetype("arial.ttf", 14)
        big = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font = big = ImageFont.load_default()
    gut, label_h = 8, 26
    n_cols = len(col_labels)
    row_w = n_cols * SHEET_TILE + (n_cols - 1) * gut
    heights = [max(t.height for t in r["tiles"]) + label_h for r in rows]
    sheet = Image.new(
        "RGB", (row_w, sum(heights) + gut * (len(rows) - 1) + 40),
        color=(245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 8), title, fill=(20, 20, 20), font=big)
    y = 40
    for r, rh in zip(rows, heights):
        draw.text((6, y + 4), r["label"], fill=(40, 40, 40), font=font)
        ty = y + label_h
        for i, (t, sub) in enumerate(zip(r["tiles"], col_labels)):
            x = i * (SHEET_TILE + gut)
            sheet.paste(t, (x, ty))
            band_w = 10 + 9 * len(sub)
            draw.rectangle(
                (x + 4, ty + 4, x + band_w, ty + 22), fill=(0, 0, 0))
            draw.text((x + 8, ty + 5), sub, fill=(255, 255, 255), font=font)
        y += rh + gut
    sheet.save(out_path, "PNG")
    log.info("fit: wrote %s", out_path)


def fit(run_dir: Path, *, only_scenario: Optional[str] = None,
        workers: int = 6,
        k_overrides: Optional[dict[str, int]] = None,
        sheets_only: bool = False) -> Path:
    """The fit phase: per-scenario clustering (same seeded path as
    ``analyze``), per-cluster coordinate-descent constant fitting,
    exact held-out validation at 1280 px, contact sheets, and an
    integration-ready ``fit.json``.

    A partial run (``only_scenario``) MERGES into an existing
    ``fit.json`` instead of clobbering it, so per-scenario refits
    (e.g. a ``--k`` change after a weak validation) keep the rest of
    the results intact. The report is regenerated from the merged
    result.

    ``sheets_only`` skips the descent and re-renders the validation
    sheets + report from the constants already in ``fit.json`` —
    used to iterate on sheet presentation (tile size etc.) without
    refitting. The seeded clustering + split reproduce the same
    cluster membership and the same held-out pairs."""
    payload = json.loads(
        (run_dir / "pairs.json").read_text(encoding="utf-8"))
    records: list[dict] = payload["pairs"]
    prior: Optional[dict] = None
    if sheets_only:
        fp = run_dir / "fit.json"
        if not fp.exists():
            raise SystemExit("sheets mode needs an existing fit.json — "
                             "run `fit` first")
        prior = json.loads(fp.read_text(encoding="utf-8"))
    thumbs = _build_fit_cache(
        records, run_dir / "fit_cache.npz", workers=workers)

    by_scenario: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        by_scenario.setdefault(r["scenario"], []).append(i)

    sheets_dir = run_dir / "sheets"
    sheets_dir.mkdir(exist_ok=True)
    fit_path = run_dir / "fit.json"
    result: dict = {
        "run": str(run_dir),
        "feature_keys": list(FEATURE_KEYS),
        "objective_keys": list(OBJECTIVE_KEYS),
        "scenarios": {},
    }
    if only_scenario and fit_path.exists():
        prior = json.loads(fit_path.read_text(encoding="utf-8"))
        result["scenarios"] = prior.get("scenarios", {})
    result["created"] = datetime.now().isoformat(timespec="seconds")

    for scenario in sorted(by_scenario):
        if only_scenario and scenario != only_scenario:
            continue
        idxs = by_scenario[scenario]
        recs = [records[i] for i in idxs]
        if prior is not None:
            k = prior["scenarios"][scenario]["k"]
        else:
            k = (k_overrides or {}).get(
                scenario, K_BY_SCENARIO.get(scenario, 2))
        feats = np.array([_feature_row(r) for r in recs], dtype=np.float64)
        labels = _kmeans(feats, k)
        if labels is None:
            log.warning("fit: %s k=%d degenerate — falling back to k=1",
                        scenario, k)
            labels = np.zeros(len(recs), dtype=int)
            k = 1
        mu, sd = feats.mean(axis=0), feats.std(axis=0)
        sd[sd < 1e-9] = 1.0
        z = (feats - mu) / sd
        centroids = [
            z[labels == c].mean(axis=0).tolist() for c in range(k)]

        base = _resolve_tuning(
            scenario if scenario != "general" else None)
        sc_out: dict = {
            "k": k,
            "pair_count": len(recs),
            "zscore": {"mean": mu.tolist(), "std": sd.tolist()},
            "centroids": centroids,
            "clusters": [],
        }
        # A refit may lower k — drop the scenario's stale sheets so
        # the sheets/ folder always mirrors the current fit.json.
        for stale in sheets_dir.glob(f"{scenario}_c*.png"):
            stale.unlink()

        for c in range(k):
            c_idx = [i for i, lab in enumerate(labels) if lab == c]
            c_recs = [recs[i] for i in c_idx]
            c_thumbs = [thumbs[idxs[i]] for i in c_idx]
            tr_i, va_i = _split_train_val(len(c_recs))
            tr_recs = [c_recs[i] for i in tr_i]
            tr_thumbs = [c_thumbs[i] for i in tr_i]
            va_recs = [c_recs[i] for i in va_i]

            t0 = time.time()
            if prior is not None:
                pc = prior["scenarios"][scenario]["clusters"][c]
                best_t = _dc.replace(base, **{
                    f: pc["constants"][f] for f, _, _ in FIT_BOUNDS})
                best_obj = pc["train_objective_fitted"]
                base_train = pc["train_objective_today"]
            else:
                best_t = None
                best_obj = np.inf
                for start in (base, _neutral_start(base)):
                    fitted, obj = _fit_cluster(tr_recs, tr_thumbs, start)
                    if obj < best_obj:
                        best_t, best_obj = fitted, obj
                base_train = _cluster_objective(base, tr_recs, tr_thumbs)
            log.info(
                "fit: %s c%d  n=%d  train %.4f → %.4f  (%.0fs)",
                scenario, c, len(c_recs), base_train, best_obj,
                time.time() - t0)

            # Exact validation + sheet rows: full decode + real
            # apply_params at the app-faithful preview size.
            sheet_rows: list[dict] = []
            base_val_sq: list[float] = []
            fit_val_sq: list[float] = []
            for vi, rec in enumerate(va_recs):
                try:
                    orig = _downsample(decode_image(Path(rec["original"])))
                    stats = _stats(_luminance(orig))
                    p_base = _params_for(stats, base)
                    p_fit = _params_for(stats, best_t)
                    r_base = apply_params(orig, p_base)
                    r_fit = apply_params(orig, p_fit)
                    s_base = _stats(_luminance(r_base))
                    s_fit = _stats(_luminance(r_fit))
                    base_val_sq.append(_objective_one(s_base, rec["lrc"]))
                    fit_val_sq.append(_objective_one(s_fit, rec["lrc"]))
                    if vi < SHEET_MAX_ROWS:
                        target = _downsample(
                            decode_image(Path(rec["target"])))
                        sheet_rows.append({
                            "label": (
                                f"{rec['name']}  |  today "
                                f"{np.sqrt(base_val_sq[-1]):.3f} → fitted "
                                f"{np.sqrt(fit_val_sq[-1]):.3f}"),
                            "tiles": [
                                _tile_pil(orig), _tile_pil(r_base),
                                _tile_pil(r_fit), _tile_pil(target)],
                        })
                except Exception:                      # noqa: BLE001
                    log.exception("fit: validation pair %s failed",
                                  rec["name"])
            base_val = float(np.sqrt(np.mean(base_val_sq))) \
                if base_val_sq else float("nan")
            fit_val = float(np.sqrt(np.mean(fit_val_sq))) \
                if fit_val_sq else float("nan")

            _sheet_for_cluster(
                sheet_rows,
                f"{scenario} — cluster {c}  ({len(c_recs)} pairs; "
                f"val: today {base_val:.3f} → fitted {fit_val:.3f})",
                sheets_dir / f"{scenario}_c{c}.png")

            sc_out["clusters"].append({
                "size": len(c_recs),
                "train_n": len(tr_recs), "val_n": len(va_recs),
                "constants": {
                    f: float(getattr(best_t, f)) for f, _, _ in FIT_BOUNDS},
                "train_objective_today": round(base_train, 4),
                "train_objective_fitted": round(float(best_obj), 4),
                "val_objective_today": round(base_val, 4),
                "val_objective_fitted": round(fit_val, 4),
            })
        result["scenarios"][scenario] = sc_out

    fit_path.write_text(
        json.dumps(result, indent=1), encoding="utf-8")
    out_md = _emit_fit_report(result, run_dir)
    log.info("fit: wrote %s", out_md)
    return out_md


def _emit_fit_report(result: dict, run_dir: Path) -> Path:
    """Regenerate ``fit_report.md`` from the (possibly merged)
    ``fit.json`` payload, so partial refits keep a complete report."""
    lines: list[str] = [
        "# Looks calibration — fit report (spec/54 §5.2 step 3)",
        "",
        f"Run: `{run_dir}`",
        "",
        "Objective: RMS over (mean, p1, p25, p50, p75, p99) luminance",
        "deltas to the LRC+Nelson target. `today` = shipped constants;",
        "`fitted` = per-cluster constants. Validation numbers are exact",
        "1280 px re-renders on held-out pairs the fitter never saw.",
        "",
    ]
    for scenario in sorted(result["scenarios"]):
        sc = result["scenarios"][scenario]
        base = _resolve_tuning(
            scenario if scenario != "general" else None)
        lines.append(
            f"## {scenario}  ({sc.get('pair_count', '?')} pairs, "
            f"k={sc['k']})\n")
        for c, cl in enumerate(sc["clusters"]):
            changed = {
                f: round(cl["constants"][f], 3)
                for f, _, _ in FIT_BOUNDS
                if abs(cl["constants"][f] - getattr(base, f)) > 1e-6}
            lines += [
                f"### cluster {c} — {cl['size']} pairs "
                f"(train {cl['train_n']}, val {cl['val_n']})",
                "",
                f"- train: today {cl['train_objective_today']:.4f} → "
                f"fitted {cl['train_objective_fitted']:.4f}",
                f"- **val (exact): today {cl['val_objective_today']:.4f}"
                f" → fitted {cl['val_objective_fitted']:.4f}**",
                "- changed constants: " + (", ".join(
                    f"{k2}={v}" for k2, v in changed.items()) or "none"),
                f"- sheet: `sheets/{scenario}_c{c}.png`",
                "",
            ]
    out_md = run_dir / "fit_report.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    _emit_sheet_index(result, run_dir)
    return out_md


def _emit_sheet_index(result: dict, run_dir: Path) -> Path:
    """``index.html`` — the eyeball surface. All cluster sheets
    inline, scrollable, with the held-out numbers as captions. Opens
    in the default browser; Ctrl+wheel zooms. Local file, no
    network."""
    parts: list[str] = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>Looks calibration — contact sheets</title>",
        "<style>",
        "body{background:#202225;color:#ddd;font-family:Segoe UI,sans-serif;",
        " margin:0;padding:24px;max-width:1380px}",
        "h1{font-size:20px} h2{font-size:16px;margin:28px 0 4px}",
        ".meta{color:#9ab;font-size:13px;margin:0 0 8px}",
        "img{width:100%;border:1px solid #444;border-radius:4px;",
        " margin-bottom:8px}",
        "</style>",
        "<h1>Looks calibration — fitted vs today, held-out pairs</h1>",
        "<p class='meta'>Each row: ORIGINAL | TODAY | FITTED | TARGET. "
        "The question per row: is FITTED closer to TARGET than TODAY?</p>",
    ]
    for scenario in sorted(result["scenarios"]):
        sc = result["scenarios"][scenario]
        for c, cl in enumerate(sc["clusters"]):
            parts += [
                f"<h2>{scenario} — cluster {c}</h2>",
                f"<p class='meta'>{cl['size']} pairs &middot; val: today "
                f"{cl['val_objective_today']:.3f} &rarr; fitted "
                f"{cl['val_objective_fitted']:.3f}</p>",
                f"<a href='sheets/{scenario}_c{c}.png' target='_blank'>"
                f"<img loading='lazy' src='sheets/{scenario}_c{c}.png'>"
                f"</a>",
            ]
    out = run_dir / "index.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    log.info("fit: wrote %s", out)
    return out


def _print_orphans(
    root: Path, pairs: list[Pair], only: Optional[str],
) -> None:
    """Census data-hygiene tail: every ``-2``/``(1)`` target file that
    matched no original. A few orphans are tolerable (culled
    originals); a pattern of them means the pairing rules miss a case
    — silent data loss in a calibration set is not acceptable
    (spec/54 §5.1)."""
    matched = {p.target for p in pairs} | {p.original for p in pairs}
    style_dirs = [
        d for d in sorted(root.iterdir())
        if d.is_dir() and not d.name.startswith((".", "_"))
        and (only is None or d.name == only)
    ]
    any_orphan = False
    for d in style_dirs:
        orphans = [
            f for f in d.iterdir()
            if f.is_file() and f.suffix.lower() in _JPEG_EXTS
            and (f.stem.lower().endswith("-2")
                 or f.stem.lower().endswith("(1)"))
            and f not in matched
        ]
        if orphans:
            any_orphan = True
            print(f"\n{d.name}: {len(orphans)} unmatched target(s):")
            for f in orphans[:10]:
                print(f"  - {f.name}")
            if len(orphans) > 10:
                print(f"  ... and {len(orphans) - 10} more")
    if not any_orphan:
        print("\nno orphan targets — every -2/(1) file is paired")


# ── CLI ───────────────────────────────────────────────────────


def _latest_run(root: Path) -> Optional[Path]:
    runs_dir = root / "_calibration_runs"
    if not runs_dir.is_dir():
        return None
    runs = sorted(
        (d for d in runs_dir.iterdir()
         if d.is_dir() and (d / "pairs.json").exists()),
        key=lambda d: d.name)
    return runs[-1] if runs else None


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Looks calibration workbench (spec/54 §5).")
    parser.add_argument(
        "command",
        choices=("census", "sweep", "analyze", "fit", "sheets",
                 "spread", "filters", "export"))
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT,
        help="pair-set root (style subfolders)")
    parser.add_argument(
        "--only", type=str, help="one style folder only (e.g. Macro)")
    parser.add_argument(
        "--limit", type=int, help="sweep: cap pair count (dev runs)")
    parser.add_argument(
        "--workers", type=int, default=6, help="sweep: decode threads")
    parser.add_argument(
        "--run", type=Path,
        help="analyze/fit: run directory (default: latest under "
             "<root>/_calibration_runs)")
    parser.add_argument(
        "--only-scenario", type=str,
        help="fit: one scenario only (e.g. wildlife) — refits merge "
             "into the existing fit.json")
    parser.add_argument(
        "--k", type=str,
        help="fit: per-scenario k overrides, e.g. portrait=2,macro=3")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s")

    if args.command == "census":
        pairs = discover(args.root, args.only)
        by_folder: dict[str, int] = {}
        for p in pairs:
            by_folder[p.folder] = by_folder.get(p.folder, 0) + 1
        print()
        for folder, count in sorted(by_folder.items()):
            print(f"{folder:24s} {count:4d} pair(s)")
        print(f"{'TOTAL':24s} {len(pairs):4d}")
        _print_orphans(args.root, pairs, args.only)
        return 0

    if args.command == "sweep":
        out_dir = (
            args.root / "_calibration_runs"
            / datetime.now().strftime("%Y-%m-%d_%H%M%S"))
        sweep(args.root, out_dir, only=args.only,
              limit=args.limit, workers=args.workers)
        return 0

    # analyze / fit operate on an existing sweep run.
    run_dir = args.run or _latest_run(args.root)
    if run_dir is None or not (run_dir / "pairs.json").exists():
        print("no sweep run found — run `sweep` first "
              "(or pass --run <dir>)", file=sys.stderr)
        return 2
    if args.command == "analyze":
        analyze(run_dir)
    elif args.command == "spread":
        spread(run_dir)
    elif args.command == "filters":
        filters(run_dir)
    elif args.command == "export":
        export_data_module(run_dir)
    else:
        k_overrides: dict[str, int] = {}
        for part in (args.k or "").split(","):
            if "=" in part:
                name, _, val = part.partition("=")
                k_overrides[name.strip()] = int(val)
        fit(run_dir, only_scenario=args.only_scenario,
            workers=args.workers, k_overrides=k_overrides,
            sheets_only=(args.command == "sheets"))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
