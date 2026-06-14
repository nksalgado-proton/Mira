"""Lens registry — user's hardware database for lenses.

The lens registry maps canonical lens names to typical use scenarios
(primary + ordered fallbacks). It is populated from bootstrap photos during
onboarding (see v2_design.md §9) and can be refined later as the user shoots
more and adds example photos via settings.

Unlike brand profiles and body profiles, the registry has NO built-in
defaults — it is always user-specific. An empty registry is valid (before
onboarding is complete).

Storage: %APPDATA%/Mira/lens_registry.json
"""

import json
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from core.logging_setup import log_activity
from core.settings import user_data_dir
from core.vocabulary import (
    FINAL_SCENARIOS,
    INTERMEDIATE_SCENARIOS,
    Scenario,
)

log = logging.getLogger(__name__)


LensSource = Literal["bootstrap", "manual", "user_confirmed", "detected"]
ConfidenceBand = Literal["low", "medium", "high", "certain"]


@dataclass
class LensEntry:
    """A single lens in the registry.

    Attributes:
        id: unique slug derived from display_name (e.g. "olympus_60mm_macro")
        display_name: human-readable name (e.g. "Olympus 60mm f/2.8 Macro")
        lens_model_contains: substrings to match against EXIF LensModel.
            First entry in registry whose list matches wins.
        potential_scenarios: ordered list of scenarios this lens is used for.
            All members are valid uses; for bootstrap-inferred entries the
            list is sorted by evidence count (most-used first), but the
            classifier treats the list as a set in Tier 2/3 disambiguation.
            Final scenarios only — intermediates are rejected.
        confidence: top_count / total_count from evidence for inferred entries;
            1.0 for user_confirmed entries (0.0 to 1.0).
        source: where this entry came from — bootstrap, manual, user_confirmed,
            or auto-detected.
        evidence: {scenario_name: count} from the sample photos that built this entry
        notes: optional free-text notes (user-editable)
    """
    id: str
    display_name: str
    lens_model_contains: list[str]
    potential_scenarios: list[Scenario] = field(default_factory=list)
    confidence: float = 1.0
    source: LensSource = "manual"
    evidence: dict[str, int] = field(default_factory=dict)
    notes: str = ""

    def matches_lens_model(self, lens_model: str) -> bool:
        """Case-insensitive substring match against any of lens_model_contains."""
        if not lens_model:
            return False
        lens_lower = lens_model.strip().lower()
        return any(
            sub.strip().lower() in lens_lower
            for sub in self.lens_model_contains
            if sub.strip()
        )

    @property
    def total_evidence_count(self) -> int:
        return sum(self.evidence.values())

    @property
    def confidence_band(self) -> ConfidenceBand:
        return classify_confidence(self.confidence)


def classify_confidence(value: float) -> ConfidenceBand:
    """Classify a confidence score into a named band.

    Bands follow v2_design.md §9.4 and §10:
      certain: >= 0.95
      high:    0.80 - 0.94
      medium:  0.60 - 0.79
      low:     <  0.60  (flagged for review)
    """
    if value >= 0.95:
        return "certain"
    if value >= 0.80:
        return "high"
    if value >= 0.60:
        return "medium"
    return "low"


@dataclass
class LensRegistry:
    """Container for all lens entries plus metadata.

    The registry is append-only at the collection level: adding a new lens
    appends to `lenses`. Updating an existing lens mutates its entry in place.
    """
    version: int = 1
    updated: str = ""
    lenses: list[LensEntry] = field(default_factory=list)

    def match(self, lens_model: str) -> Optional[LensEntry]:
        """Find the first registry entry whose lens_model_contains matches."""
        if not lens_model:
            return None
        for entry in self.lenses:
            if entry.matches_lens_model(lens_model):
                return entry
        return None

    def find_by_id(self, lens_id: str) -> Optional[LensEntry]:
        for entry in self.lenses:
            if entry.id == lens_id:
                return entry
        return None

    def add(self, entry: LensEntry) -> None:
        """Add a new entry. Raises ValueError if id already exists."""
        if self.find_by_id(entry.id) is not None:
            raise ValueError(f"Lens id '{entry.id}' already in registry")
        self.lenses.append(entry)
        self._touch()

    def remove(self, lens_id: str) -> bool:
        """Remove an entry by id. Returns True if removed, False if not found."""
        for i, entry in enumerate(self.lenses):
            if entry.id == lens_id:
                del self.lenses[i]
                self._touch()
                return True
        return False

    def replace(self, entry: LensEntry) -> None:
        """Replace an existing entry (matched by id). Raises if not found."""
        for i, existing in enumerate(self.lenses):
            if existing.id == entry.id:
                self.lenses[i] = entry
                self._touch()
                return
        raise KeyError(f"Lens id '{entry.id}' not in registry")

    def _touch(self) -> None:
        self.updated = datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Slugify (display_name → id)
# ---------------------------------------------------------------------------

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SLUG_MULTI_UNDERSCORE = re.compile(r"_+")


def slugify(text: str) -> str:
    """Convert a display name to a stable, file-safe id.

    Examples:
        "Olympus 60mm f/2.8 Macro" → "olympus_60mm_f_2_8_macro"
        "Leica DG 100-400" → "leica_dg_100_400"
        "Sigma 30mm F1.4 DC DN" → "sigma_30mm_f1_4_dc_dn"
    """
    if not text:
        return ""
    # Normalize unicode (é → e, etc.)
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    # Lowercase, replace non-alphanumeric with underscore
    lowered = ascii_text.lower()
    slugged = _SLUG_NON_ALNUM.sub("_", lowered)
    # Collapse multiple underscores and trim
    slugged = _SLUG_MULTI_UNDERSCORE.sub("_", slugged).strip("_")
    return slugged


# ---------------------------------------------------------------------------
# Confidence computation and inference
# ---------------------------------------------------------------------------

def _validate_final_scenarios(labeled_lenses: dict[Scenario, list[str]]) -> None:
    """Reject intermediate scenarios in input — registry is final-only.

    Raises ValueError if any intermediate scenario is present, naming it.
    """
    intermediate_found = [
        s for s in labeled_lenses.keys() if s in INTERMEDIATE_SCENARIOS
    ]
    if intermediate_found:
        names = ", ".join(s.value for s in intermediate_found)
        raise ValueError(
            f"Intermediate scenarios not allowed in lens registry: {names}. "
            f"Only final scenarios ({', '.join(s.value for s in FINAL_SCENARIOS)}) "
            f"are accepted."
        )


def _build_entry_from_counts(
    canonical_name: str,
    scenario_counts: Counter,
    source: LensSource,
) -> LensEntry:
    """Build a LensEntry from per-scenario sample counts.

    The potential_scenarios list is ordered by descending count (most-used
    first), preserving useful information for UI display and the Tier 3
    "first potential" fallback. Confidence reflects how dominant the top
    scenario is in the evidence.
    """
    scenarios_sorted = scenario_counts.most_common()
    total = sum(scenario_counts.values())
    if not scenarios_sorted or total == 0:
        return LensEntry(
            id=slugify(canonical_name),
            display_name=canonical_name,
            lens_model_contains=[canonical_name],
            potential_scenarios=[],
            confidence=0.0,
            source=source,
            evidence={},
        )
    potentials = [Scenario(s) for s, _ in scenarios_sorted]
    top_count = scenarios_sorted[0][1]
    confidence = top_count / total

    return LensEntry(
        id=slugify(canonical_name),
        display_name=canonical_name,
        lens_model_contains=[canonical_name],
        potential_scenarios=potentials,
        confidence=confidence,
        source=source,
        evidence={s: c for s, c in scenario_counts.items()},
    )


def infer_lens_registry(
    labeled_lenses: dict[Scenario, list[str]],
) -> LensRegistry:
    """Build a fresh registry from labeled lens samples.

    Args:
        labeled_lenses: mapping from Scenario to a list of canonical lens names
            appearing in that scenario's bootstrap photos. Duplicates are
            meaningful (they represent count). Canonical names should already
            have been normalized by the brand profile.

    Returns:
        A new LensRegistry with one entry per distinct canonical lens.

    Raises:
        ValueError: if any intermediate scenario appears in the input.

    Example:
        labeled = {
            Scenario.MACRO: ["Olympus 60mm Macro"] * 4,
            Scenario.PORTRAIT: ["Olympus 60mm Macro", "Leica DG 12-60"],
            Scenario.WILDLIFE: ["Leica DG 100-400"] * 5,
        }
        registry = infer_lens_registry(labeled)
        # Olympus 60mm Macro: primary=macro conf=0.8, fallback=[portrait]
        # Leica DG 12-60: primary=portrait conf=1.0
        # Leica DG 100-400: primary=wildlife conf=1.0
    """
    _validate_final_scenarios(labeled_lenses)

    # Group: canonical_lens_name -> Counter of scenarios
    lens_counts: dict[str, Counter] = {}
    for scenario, lens_names in labeled_lenses.items():
        for name in lens_names:
            name = name.strip()
            if not name:
                continue
            lens_counts.setdefault(name, Counter())[scenario.value] += 1

    registry = LensRegistry()
    for canonical_name, counts in lens_counts.items():
        entry = _build_entry_from_counts(canonical_name, counts, source="bootstrap")
        registry.lenses.append(entry)

    registry._touch()
    return registry


def refine_lens_entry(
    entry: LensEntry,
    new_labels: dict[Scenario, int],
) -> LensEntry:
    """Return a new entry with additional evidence merged in.

    The original entry is not mutated. Evidence counts are summed and
    potential_scenarios is rebuilt from the new sorted counts; confidence
    is recomputed. The id and lens_model_contains are preserved.

    Args:
        entry: the existing lens entry to refine
        new_labels: additional {scenario: count} to merge into the evidence

    Raises:
        ValueError: if any intermediate scenario appears in new_labels.
    """
    intermediate = [s for s in new_labels if s in INTERMEDIATE_SCENARIOS]
    if intermediate:
        names = ", ".join(s.value for s in intermediate)
        raise ValueError(f"Intermediate scenarios not allowed: {names}")

    merged_counts = Counter(entry.evidence)
    for scenario, count in new_labels.items():
        if count <= 0:
            continue
        merged_counts[scenario.value] += count

    if not merged_counts:
        return entry  # nothing to refine

    scenarios_sorted = merged_counts.most_common()
    total = sum(merged_counts.values())
    potentials = [Scenario(s) for s, _ in scenarios_sorted]
    top_count = scenarios_sorted[0][1]
    confidence = top_count / total

    return LensEntry(
        id=entry.id,
        display_name=entry.display_name,
        lens_model_contains=list(entry.lens_model_contains),
        potential_scenarios=potentials,
        confidence=confidence,
        source=entry.source,
        evidence=dict(merged_counts),
        notes=entry.notes,
    )


def create_stub_lens_entry(
    raw_lens_model: str,
    fallback_scenario: Scenario = Scenario.GENERAL,
) -> LensEntry:
    """Create a placeholder entry for an unknown lens encountered at runtime.

    Used by the classifier when it sees a LensModel not in the registry.
    Confidence is intentionally low so the photo gets flagged for review
    until the user adds real bootstrap evidence via "Refine lens" in settings.
    The single potential_scenarios entry acts as the lens-driven fallback.
    """
    raw = raw_lens_model.strip() or "Unknown Lens"
    return LensEntry(
        id=slugify(raw) or "unknown_lens",
        display_name=raw,
        lens_model_contains=[raw],
        potential_scenarios=[fallback_scenario],
        confidence=0.30,
        source="detected",
        evidence={},
        notes="Auto-created stub — add example photos via Settings → Refine lens",
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _registry_path() -> Path:
    """Location of the lens registry file on disk."""
    return user_data_dir() / "lens_registry.json"


def _entry_to_dict(entry: LensEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "display_name": entry.display_name,
        "lens_model_contains": list(entry.lens_model_contains),
        "potential_scenarios": [s.value for s in entry.potential_scenarios],
        "confidence": round(entry.confidence, 4),
        "source": entry.source,
        "evidence": dict(entry.evidence),
        "notes": entry.notes,
    }


def _entry_from_dict(data: dict[str, Any]) -> LensEntry:
    """Deserialize a lens entry. Accepts both the new schema
    (``potential_scenarios``) and the legacy schema (``primary_scenario``
    + ``fallback_scenarios``) so previously-saved files still load.
    On the next ``save_lens_registry`` call, the entry is rewritten in
    the new schema, which is a one-way migration."""
    if "potential_scenarios" in data:
        potentials = [Scenario(s) for s in data["potential_scenarios"]]
    else:
        # Legacy migration — primary first, then fallbacks (in that order).
        potentials = [Scenario(data["primary_scenario"])]
        potentials.extend(
            Scenario(s) for s in data.get("fallback_scenarios", [])
        )
    return LensEntry(
        id=data["id"],
        display_name=data.get("display_name", data["id"]),
        lens_model_contains=list(data.get("lens_model_contains", [])),
        potential_scenarios=potentials,
        confidence=float(data.get("confidence", 0.0)),
        source=data.get("source", "manual"),
        evidence=dict(data.get("evidence", {})),
        notes=data.get("notes", ""),
    )


def _registry_to_dict(registry: LensRegistry) -> dict[str, Any]:
    return {
        "version": registry.version,
        "updated": registry.updated,
        "lenses": [_entry_to_dict(e) for e in registry.lenses],
    }


def _registry_from_dict(data: dict[str, Any]) -> LensRegistry:
    return LensRegistry(
        version=int(data.get("version", 1)),
        updated=data.get("updated", ""),
        lenses=[_entry_from_dict(e) for e in data.get("lenses", [])],
    )


def load_lens_registry() -> LensRegistry:
    """Load the lens registry from disk. Returns empty registry if file absent.

    An empty registry is valid — it simply means onboarding hasn't populated
    it yet or the user has no lenses registered.
    """
    with log_activity(log, "loading lens registry"):
        path = _registry_path()
        if not path.exists():
            log.debug("No lens registry file at %s — returning empty", path)
            return LensRegistry()
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            registry = _registry_from_dict(data)
            log.info("Loaded lens registry from %s (%d lenses)",
                     path, len(registry.lenses))
            return registry
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as exc:
            # Corrupted file: fall back to empty rather than crashing the app.
            # The user will see no lenses and can re-run bootstrap.
            log.warning(
                "Lens registry at %s is unreadable (%s) — falling back to empty",
                path, exc,
            )
            return LensRegistry()


def save_lens_registry(registry: LensRegistry) -> None:
    """Persist the registry to disk atomically (write-to-tmp-then-rename)."""
    with log_activity(log, f"saving lens registry ({len(registry.lenses)} lenses)"):
        registry._touch()
        path = _registry_path()
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(_registry_to_dict(registry), f, indent=2, ensure_ascii=False)
        tmp.replace(path)
        log.debug("Lens registry written to %s", path)
