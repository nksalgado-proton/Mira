"""The background classification pass — spec/58 §1.

Every captured item (photos AND videos) gets classified before the user
reaches Edit; this routine is the SOLE auto writer (the Pick surfaces'
lazy writers retire with their chrome, spec/58 §2). It is idempotent and
cheap to re-run — the triggers (post-ingest + event open) just call it.

The rules, per spec/58 §1 + §3:

* **Candidates:** captured items that have never been classified, or
  whose stored ``classification_rules_version`` differs from the current
  stamp (``core.genre.rules_version_for`` — bundled rules + the user-
  scenarios fingerprint, so a wizard re-run re-opens them).
* **Never touched:** ``classification_source='user'`` rows, and items
  FROZEN by Edit work (``EventGateway.edit_touched_item_ids`` — an
  adjustment row, a child segment/snapshot's adjustment, or an export).
  Untouched means re-classifiable; frozen means frozen — even for a
  first classification (writing one would change the item's render
  routing after the user already worked on it).
* **RAW-first ("Use the raw"):** a RAW+JPEG pair is ONE shot — grouped
  by (camera, day, filename stem), the RAW is classified and its photo
  siblings inherit the result verbatim. Files without a RAW sibling
  (and all videos) classify themselves.

Writes land in ONE bulk transaction (``set_classifications_bulk``) so a
worker-thread pass holds one short lock window against the UI thread's
connection. No Qt anywhere in this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from core.photo_decoder import RAW_EXTENSIONS

log = logging.getLogger(__name__)


@dataclass
class ClassifyPassReport:
    """What one pass did. ``classified`` counts real classifier runs;
    ``inherited`` the stem-sibling copies riding them."""

    candidates: int = 0
    classified: int = 0
    inherited: int = 0
    skipped_user: int = 0
    skipped_frozen: int = 0
    skipped_current: int = 0
    missing: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def wrote(self) -> int:
        return self.classified + self.inherited

    def __str__(self) -> str:  # log-friendly one-liner
        return (
            f"{self.wrote} written ({self.classified} classified, "
            f"{self.inherited} inherited) of {self.candidates} candidate(s); "
            f"skipped {self.skipped_user} user / {self.skipped_frozen} frozen "
            f"/ {self.skipped_current} current; {self.missing} missing; "
            f"{len(self.errors)} error(s)")


def classify_event_items(
    eg,
    event_root: Path,
    *,
    library_gateway=None,
    exif_batch_fn: Optional[Callable] = None,
    classify_fn: Optional[Callable] = None,
    rules_version_fn: Optional[Callable[[str], str]] = None,
) -> ClassifyPassReport:
    """Run one classification pass over an open event. The three ``*_fn``
    hooks default to the live machinery (``core.exif_reader.
    read_exif_batch`` / ``core.genre.classify_exif`` /
    ``core.genre.rules_version_for``) and exist so tests run without
    ExifTool or the bundled rules.

    ``library_gateway`` (optional) opts the pass into the spec/85 §5
    user-gear-hint tier: every candidate picks up its own gear-hint
    callable, and the persisted ``classification_rules_version`` stamp
    folds in :meth:`LibraryGateway.gear_fingerprint` so a gear-profile
    change forces untouched items to re-classify on the next pass
    (spec/58 §3 — ``classification_source='user'`` rows are never
    overwritten)."""
    if exif_batch_fn is None:
        from core.exif_reader import read_exif_batch as exif_batch_fn
    if classify_fn is None:
        from core.genre import classify_exif as classify_fn
    if rules_version_fn is None:
        from core.genre import rules_version_for as rules_version_fn

    report = ClassifyPassReport()
    event_root = Path(event_root)

    is_phone = {c.camera_id: bool(c.is_phone) for c in eg.cameras()}
    # Gear-profile fingerprint folds into the version stamp so a wizard
    # save bumps every untouched item to the new rules_version on the
    # next pass (spec/85 §5 + spec/58 §3).
    gear_fp = (
        library_gateway.gear_fingerprint() if library_gateway else "")
    version_for = {
        True: rules_version_fn("phone") + (
            ("." + gear_fp) if gear_fp else ""),
        False: rules_version_fn("camera") + (
            ("." + gear_fp) if gear_fp else ""),
    }
    frozen = eg.edit_touched_item_ids()

    # ── Candidate scan (spec/58 §3 stability guards) ──────────────────
    candidates = []
    for it in eg.items(provenance="captured", include_hidden=True):
        if not it.origin_relpath:
            continue
        if it.classification_source == "user":
            report.skipped_user += 1
            continue
        ver = version_for[is_phone.get(it.camera_id, False)]
        if it.classification and (it.classification_rules_version or "") == ver:
            report.skipped_current += 1
            continue
        if it.id in frozen:
            report.skipped_frozen += 1
            continue
        candidates.append((it, ver))
    report.candidates = len(candidates)
    if not candidates:
        return report

    # ── RAW-first pairing (spec/58 §1, "Use the raw") ─────────────────
    # Group by (camera, day, stem): a group with a RAW classifies the
    # RAW once and its PHOTO siblings inherit; everything else (videos,
    # unpaired files) classifies itself.
    groups: dict[tuple, list] = {}
    for it, ver in candidates:
        key = (it.camera_id, it.day_number,
               Path(it.origin_relpath).stem.lower())
        groups.setdefault(key, []).append((it, ver))

    jobs = []          # (rep_item, ver, followers)
    for members in groups.values():
        raws = sorted(
            (pair for pair in members
             if Path(pair[0].origin_relpath).suffix.lower() in RAW_EXTENSIONS),
            key=lambda pair: pair[0].origin_relpath)
        if raws:
            rep, ver = raws[0]
            followers = [
                it for it, _v in members
                if it.id != rep.id and it.kind == "photo"]
            jobs.append((rep, ver, followers))
            # Non-photo, non-rep members (videos sharing the stem)
            # classify themselves.
            for it, v in members:
                if it.id != rep.id and it.kind != "photo":
                    jobs.append((it, v, []))
        else:
            for it, v in members:
                jobs.append((it, v, []))

    # ── EXIF batch over the representatives (one ExifTool spawn) ─────
    paths, live_jobs = [], []
    for rep, ver, followers in jobs:
        p = event_root / rep.origin_relpath
        if not p.exists():
            report.missing += 1 + len(followers)
            continue
        paths.append(p)
        live_jobs.append((rep, ver, followers, p))
    exif_by_path: dict = {}
    if paths:
        try:
            for px in exif_batch_fn(paths):
                exif_by_path[Path(px.path)] = px
        except Exception as exc:  # noqa: BLE001
            log.exception("classify pass: EXIF batch failed")
            report.errors.append(f"exif batch failed: {exc}")
            return report

    # ── Classify + collect writes ─────────────────────────────────────
    rows = []
    for rep, ver, followers, p in live_jobs:
        px = exif_by_path.get(p)
        raw = getattr(px, "raw", None) or {}
        source = "phone" if is_phone.get(rep.camera_id, False) else "camera"
        # spec/85 §5 — per-item gear hint, built from the item's camera +
        # lens. The closure inside ``make_gear_hint`` does the SQL once at
        # build time so the classifier's hot loop stays cheap.
        gear_hint = (
            library_gateway.make_gear_hint(
                camera_id=rep.camera_id, lens_model=rep.lens_model)
            if library_gateway else None)
        try:
            res = classify_fn(p, raw, source=source, gear_hint=gear_hint)
        except Exception as exc:  # noqa: BLE001
            log.exception("classify pass: classify failed for %s", p)
            report.errors.append(f"{rep.origin_relpath}: {exc}")
            continue
        value = res.scenario.value
        row = (value, "auto", ver, bool(res.needs_review),
               float(res.confidence))
        rows.append((rep.id, *row))
        report.classified += 1
        for fol in followers:
            rows.append((fol.id, *row))
            report.inherited += 1

    eg.set_classifications_bulk(rows)
    log.info("classify pass: %s", report)
    return report
