"""Per-photo genre (scenario) classification + override — E8b core.

Frozen design: ``docs/18-culler-spec.md`` §"Genre classification +
override". The genre is the photo's **destination** (J4 auto-
classify → J6 map-to-folders-by-genre). In the culler:

* the **auto** classification is recomputed (cheap) and cached in
  the ingest journal like the sharpness score — *not* durable
  state;
* the user's **override** is a sticky per-photo entry in the
  journal (same crash-safe, sparse discipline as the 3-state mark);
* **effective genre = override ?? auto** — that is what
  ``commit_from_session`` routes a kept photo by.

Qt-free (UI builds the readout/dropdown on top). Classification
itself never raises — a bad photo or profile falls back to
``GENERAL`` flagged for review, never crashes the cull.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from core.classifier_v2 import ClassificationResult, Scenario, classify
from core.scenario_bootstrap import describe_scenario

log = logging.getLogger(__name__)

_OVERRIDE_KEY = "genre"          # sparse {filename: scenario_value}
# Cache: {filename: {"s": val, "r": bool, "v": rules_version, "src": source}}.
# The v + src stamps invalidate stale entries when the rules JSON is
# bumped (00.090 fix — without them, a rules change would leave UI
# cached genre values stuck on the old classification forever).
_AUTO_KEY = "genre_auto"

# Rules are stable across a session; loading them (+ merging the
# wizard's user scenarios) is not free → load once per source.
_rules_cache: dict[str, Any] = {}

# Per-session cache of the BUNDLED rules-file version for each
# source — read once from disk, reused for every cache validate.
_rules_version_cache: dict[str, int] = {}


def _rules_version(source: str) -> int:
    """Bundled rules-file version for ``source`` ("camera"|"phone").

    Used to invalidate the genre_auto cache when the rule set changes.
    Read from ``assets/refinement_rules{,_phone}.json`` — the bundled
    files, NOT the user override. The user override gets re-seeded
    from the bundle when the bundle is newer (see
    :func:`core.classifier_v2.ensure_user_rules_exist`); the same
    bundled version is the right cache-invalidation key.

    Cached per-session — the bundled file doesn't change at runtime.
    Returns ``0`` on any read failure (which compares unequal to any
    real version, conservatively forcing recompute)."""
    if source in _rules_version_cache:
        return _rules_version_cache[source]
    filename = (
        "refinement_rules_phone.json" if source == "phone"
        else "refinement_rules.json"
    )
    rules_path = (
        Path(__file__).resolve().parent.parent / "assets" / filename
    )
    try:
        with rules_path.open("r", encoding="utf-8") as f:
            version = int(json.load(f).get("version", 0))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        version = 0
    _rules_version_cache[source] = version
    return version


def reset_rules_version_cache() -> None:
    """Drop the per-session bundled-version cache. Used by tests that
    mutate the assets file or want to force a re-read."""
    _rules_version_cache.clear()


def rules_version_for(source: str) -> str:
    """The PERSISTED rules-version stamp for ``source`` ("camera"|"phone")
    — what ``item.classification_rules_version`` stores (spec/58 §3).

    Composes the bundled rules-file version with a fingerprint of the
    user-scenario files, so BOTH a shipped-rules update AND a wizard
    re-run (which rewrites ``user-*.json``) change the stamp and make
    Edit-untouched auto classifications re-classifiable."""
    from core.scenario_loader import user_scenarios_fingerprint

    return f"{_rules_version(source)}.{user_scenarios_fingerprint()}"


def _rules(source: str):
    rs = _rules_cache.get(source)
    if rs is None:
        from core.scenario_loader import (
            load_camera_rules_with_user_scenarios,
            load_phone_rules_with_user_scenarios,
        )
        rs = (
            load_phone_rules_with_user_scenarios()
            if source == "phone"
            else load_camera_rules_with_user_scenarios()
        )
        _rules_cache[source] = rs
    return rs


def reset_rules_cache() -> None:
    """Drop the cached rule sets (call after the wizard edits the
    user scenario library so the next classify picks them up)."""
    _rules_cache.clear()


def classify_exif(
    path: Path,
    exif: dict[str, Any],
    *,
    source: Optional[str] = None,
) -> ClassificationResult:
    """Classify one photo from its raw EXIF dict. Mirrors the
    import-pipeline recipe (brand → body → lens → context →
    refinement rules + user scenarios). Never raises: any failure
    → ``GENERAL`` low-confidence (flagged for review).

    **Source auto-detection (00.090 fix).** When ``source`` is None
    (the default), it's derived from the resolved body profile's
    ``kind`` field: phone bodies → "phone" rules; cameras → "camera"
    rules. This is the legitimate device-class signal (phone vs
    camera) per the brand-agnostic invariant — phones don't have a
    mode dial, cameras do; the rule sets diverge accordingly. Before
    this fix every call site defaulted to "camera", so iPhone shots
    in the UI culler never reached the phone_selfie / phone_close_
    focus / phone_face_detected / phone_default_street rules. The
    explicit ``source`` kwarg stays as an override for tests and any
    caller that needs to force one or the other."""
    try:
        from core.brand_profile import match_brand_profile_for_photo
        from core.import_pipeline import (
            RawExifEntry,
            _build_photo_context,
            _resolve_body_profile,
        )
        from core.lens_registry import load_lens_registry

        brand = match_brand_profile_for_photo(exif)
        body = _resolve_body_profile(exif)
        # Auto-derive source from the body kind when not supplied.
        if source is None:
            source = "phone" if body.kind == "phone" else "camera"
        lens = None
        if brand is not None:
            lens_model = brand.lens_normalization.read_raw_lens(exif)
            if lens_model:
                lens = load_lens_registry().match(lens_model)
        ctx = _build_photo_context(
            RawExifEntry(path=path, exif=exif), brand, body, lens,
            source=source,
        )
        return classify(ctx, _rules(source))
    except Exception as exc:  # noqa: BLE001 — must never break the cull
        log.warning("genre classify failed for %s: %s", path, exc)
        return ClassificationResult(
            scenario=Scenario.GENERAL,
            confidence=0.0,
            reason=f"classify error: {exc}",
            rule_id=None,
            source=source or "camera",        # type: ignore[arg-type]
            tag="needs_review",
        )


# ── journal: sticky override (sparse) ────────────────────────────


def get_genre_override(journal: dict, filename: str) -> Optional[str]:
    """The user's override scenario value for ``filename``, or
    ``None`` if they never reclassified it."""
    ov = journal.get(_OVERRIDE_KEY)
    if isinstance(ov, dict):
        v = ov.get(filename)
        if isinstance(v, str) and v:
            return v
    return None


def set_genre_override(journal: dict, filename: str, scenario: str) -> None:
    """Pin ``filename`` to ``scenario`` (a ``Scenario`` value).
    Raises ``ValueError`` on an unknown value."""
    if scenario not in {s.value for s in Scenario}:
        raise ValueError(f"unknown scenario: {scenario!r}")
    ov = journal.get(_OVERRIDE_KEY)
    if not isinstance(ov, dict):
        ov = {}
        journal[_OVERRIDE_KEY] = ov
    ov[filename] = scenario


def clear_genre_override(journal: dict, filename: str) -> None:
    ov = journal.get(_OVERRIDE_KEY)
    if isinstance(ov, dict):
        ov.pop(filename, None)


# ── journal: auto-classification cache (perf, like sharpness) ────


def _entry_is_current(e: Any) -> bool:
    """True iff ``e`` is a cache entry whose v + src stamps match the
    bundled rules version for its source. Old entries (no v / no src,
    written before 00.090) and stamps from a stale rules version
    return False — caller must recompute."""
    if not isinstance(e, dict):
        return False
    if not isinstance(e.get("s"), str):
        return False
    src = e.get("src")
    if src not in ("camera", "phone"):
        return False
    return e.get("v") == _rules_version(src)


def cached_auto_genre(
    journal: dict,
    filename: str,
    compute: Callable[[], ClassificationResult],
) -> tuple[str, bool]:
    """Return ``(scenario_value, needs_review)`` for ``filename``,
    computing + caching once (the classifier is cheap but not
    free; navigation must not re-run it every frame — Speed is
    King). The cache is a perf aid, *not* durable truth — only the
    override is.

    **Cache invalidation (00.090).** Each entry carries the rules
    version + source it was computed under. When the bundled rules
    file is bumped (e.g., a new rule lands), every existing entry
    becomes stale automatically and recomputes on next access. The
    source is read from the freshly-computed
    :class:`ClassificationResult.source`, which now derives from
    the body profile's kind (see :func:`classify_exif`)."""
    cache = journal.get(_AUTO_KEY)
    if isinstance(cache, dict):
        e = cache.get(filename)
        if _entry_is_current(e):
            return e["s"], bool(e.get("r", False))
    res = compute()
    val, review = res.scenario.value, bool(res.needs_review)
    src = res.source if res.source in ("camera", "phone") else "camera"
    if not isinstance(cache, dict):
        cache = {}
        journal[_AUTO_KEY] = cache
    cache[filename] = {
        "s": val, "r": review,
        "v": _rules_version(src), "src": src,
    }
    return val, review


def peek_auto_genre(
    journal: dict, filename: str,
) -> Optional[tuple[str, bool]]:
    """Cached ``(scenario_value, needs_review)`` if present and
    *current* (stamp matches the bundled rules version), else
    ``None`` — never computes. The grid uses this so opening it
    never classifies hundreds of photos.

    Stale entries (pre-00.090 writes with no v/src, or stamps from
    a rules version older than the bundled file) return None,
    forcing the caller to refresh via :func:`cached_auto_genre`."""
    cache = journal.get(_AUTO_KEY)
    if isinstance(cache, dict):
        e = cache.get(filename)
        if _entry_is_current(e):
            return e["s"], bool(e.get("r", False))
    return None


def effective_genre(journal: dict, filename: str, auto: str) -> str:
    """override ?? auto — what the photo IS, and where it's filed
    at commit (docs/18 J6)."""
    return get_genre_override(journal, filename) or auto


# ── Bucket-level style (docs/18 §"Bucket cull surfaces") ─────────
#
# Some bucket types carry ONE style for the whole bucket, not a
# per-item one: Burst / Video / Focus-bracket / Exposure-bracket.
# (Individual / Moment stay per-item — use the per-photo helpers
# above.) The value = the dominant EXIF-classified style across the
# bucket's frames, with a per-type default tie-breaker when EXIF is
# ambiguous (frozen table, docs/18). A single bucket-level override
# supersedes everything.

# bucket-type key → fixed tie-breaker scenario value. Burst is NOT
# here — it resolves from the user's `preferred_burst_genre`
# (settings / wizard) passed in by the caller.
BUCKET_STYLE_TIEBREAK: dict[str, str] = {
    "video": Scenario.GENERAL.value,
    "focus_bracket": Scenario.MACRO.value,
    "exposure_bracket": Scenario.LANDSCAPE.value,
}

_BUCKET_OVERRIDE_KEY = "genre_bucket"   # single scenario value


def get_bucket_genre_override(journal: dict) -> Optional[str]:
    """The user's bucket-level style override, or ``None``. Unlike
    the per-photo override this is a single value (the bucket has
    one journal)."""
    v = journal.get(_BUCKET_OVERRIDE_KEY)
    return v if isinstance(v, str) and v else None


def set_bucket_genre_override(journal: dict, scenario: str) -> None:
    """Pin the whole bucket to ``scenario``. ``ValueError`` on an
    unknown value (mirrors :func:`set_genre_override`)."""
    if scenario not in {s.value for s in Scenario}:
        raise ValueError(f"unknown scenario: {scenario!r}")
    journal[_BUCKET_OVERRIDE_KEY] = scenario


def clear_bucket_genre_override(journal: dict) -> None:
    journal.pop(_BUCKET_OVERRIDE_KEY, None)


def bucket_style_tiebreak(
    bucket_type: str, *, preferred_burst_genre: str,
) -> str:
    """The frozen per-type default (docs/18). Burst → the user's
    preferred action genre; unknown types → GENERAL (safe)."""
    if bucket_type == "burst":
        return preferred_burst_genre or Scenario.WILDLIFE.value
    return BUCKET_STYLE_TIEBREAK.get(bucket_type, Scenario.GENERAL.value)


def dominant_scenario(values: list[str]) -> Optional[str]:
    """The single dominant scenario among the bucket's per-frame
    auto values, or ``None`` when EXIF is **ambiguous** so the
    caller falls back to the per-type tie-breaker.

    Ambiguous = empty, the modal value is GENERAL (the frames
    classified as nothing specific), or there is no unique top
    non-GENERAL value (a tie). Pure counting — no EXIF I/O here;
    the caller feeds the already-cached per-frame values."""
    specific = [v for v in values if v and v != Scenario.GENERAL.value]
    if not specific:
        return None
    counts: dict[str, int] = {}
    for v in specific:
        counts[v] = counts.get(v, 0) + 1
    top = max(counts.values())
    winners = [v for v, c in counts.items() if c == top]
    return winners[0] if len(winners) == 1 else None


def effective_bucket_style(
    journal: dict,
    bucket_type: str,
    frame_scenarios: list[str],
    *,
    preferred_burst_genre: str,
) -> str:
    """bucket-override ?? dominant(frames) ?? per-type tie-breaker
    (docs/18 frozen). The one style the whole bucket files under."""
    ov = get_bucket_genre_override(journal)
    if ov:
        return ov
    dom = dominant_scenario(frame_scenarios)
    if dom:
        return dom
    return bucket_style_tiebreak(
        bucket_type, preferred_burst_genre=preferred_burst_genre,
    )


# ── labels (UI builds tr() on top) ───────────────────────────────


def genre_label(scenario_value: str) -> str:
    """Short title for the readout / dropdown item, e.g.
    ``"focus_bracket"`` → ``"Focus Bracket"``."""
    return scenario_value.replace("_", " ").title()


def genre_tooltip(scenario_value: str) -> str:
    """One-line description for a dropdown item's tooltip."""
    try:
        return describe_scenario(Scenario(scenario_value))
    except (ValueError, KeyError):
        return ""


def all_scenarios() -> list[str]:
    """Every scenario value, for the override dropdown."""
    return [s.value for s in Scenario]


__all__ = [
    "classify_exif",
    "reset_rules_cache",
    "get_genre_override",
    "set_genre_override",
    "clear_genre_override",
    "cached_auto_genre",
    "peek_auto_genre",
    "effective_genre",
    "genre_label",
    "genre_tooltip",
    "all_scenarios",
    "BUCKET_STYLE_TIEBREAK",
    "get_bucket_genre_override",
    "set_bucket_genre_override",
    "clear_bucket_genre_override",
    "bucket_style_tiebreak",
    "dominant_scenario",
    "effective_bucket_style",
]
