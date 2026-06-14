"""Tests for the lens registry loader, inference, and persistence."""

import json

import pytest

from core.lens_registry import (
    LensEntry,
    LensRegistry,
    _entry_from_dict,
    _entry_to_dict,
    _registry_from_dict,
    _registry_to_dict,
    classify_confidence,
    create_stub_lens_entry,
    infer_lens_registry,
    load_lens_registry,
    refine_lens_entry,
    save_lens_registry,
    slugify,
)
from core.vocabulary import Scenario


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

def test_slugify_simple():
    assert slugify("Leica DG 100-400") == "leica_dg_100_400"


def test_slugify_with_special_chars():
    assert slugify("Olympus 60mm f/2.8 Macro") == "olympus_60mm_f_2_8_macro"


def test_slugify_collapses_multiple_separators():
    assert slugify("Sigma  30mm   F1.4") == "sigma_30mm_f1_4"


def test_slugify_trims_leading_trailing_underscores():
    assert slugify("---test---") == "test"


def test_slugify_unicode_normalization():
    assert slugify("Leica Summilux Céu") == "leica_summilux_ceu"


def test_slugify_empty_string():
    assert slugify("") == ""


def test_slugify_only_separators():
    # All non-alphanumeric — becomes empty after trim
    assert slugify("!!!---") == ""


# ---------------------------------------------------------------------------
# classify_confidence
# ---------------------------------------------------------------------------

def test_classify_confidence_certain():
    assert classify_confidence(1.0) == "certain"
    assert classify_confidence(0.95) == "certain"


def test_classify_confidence_high():
    assert classify_confidence(0.94) == "high"
    assert classify_confidence(0.80) == "high"


def test_classify_confidence_medium():
    assert classify_confidence(0.79) == "medium"
    assert classify_confidence(0.60) == "medium"


def test_classify_confidence_low():
    assert classify_confidence(0.59) == "low"
    assert classify_confidence(0.30) == "low"
    assert classify_confidence(0.0) == "low"


# ---------------------------------------------------------------------------
# LensEntry
# ---------------------------------------------------------------------------

def _make_entry(
    lens_id="olympus_60mm_macro",
    display="Olympus 60mm f/2.8 Macro",
    matches=None,
    primary=Scenario.MACRO,
    confidence=0.95,
) -> LensEntry:
    if matches is None:
        matches = ["Olympus 60mm", "M.ZUIKO DIGITAL ED 60mm"]
    return LensEntry(
        id=lens_id,
        display_name=display,
        lens_model_contains=matches,
        potential_scenarios=[primary, Scenario.PORTRAIT],
        confidence=confidence,
        source="bootstrap",
        evidence={"macro": 4, "portrait": 1},
    )


def test_lens_entry_matches_case_insensitive():
    entry = _make_entry()
    assert entry.matches_lens_model("M.ZUIKO DIGITAL ED 60mm f/2.8 Macro") is True
    assert entry.matches_lens_model("m.zuiko digital ed 60mm f/2.8 macro") is True


def test_lens_entry_matches_any_substring():
    entry = _make_entry(matches=["100-400", "Leica DG 100-400"])
    assert entry.matches_lens_model("LUMIX G VARIO 100-400/F4.0-6.3") is True
    assert entry.matches_lens_model("Leica DG 100-400 Elmar") is True
    assert entry.matches_lens_model("Sony 100-400 GM") is True  # matches "100-400"


def test_lens_entry_no_match():
    entry = _make_entry(matches=["Olympus 60mm"])
    assert entry.matches_lens_model("Sony 50mm") is False
    assert entry.matches_lens_model("") is False


def test_lens_entry_skips_empty_match_strings():
    entry = _make_entry(matches=["", "  ", "Olympus"])
    assert entry.matches_lens_model("Olympus 60mm") is True
    # The empty/whitespace entries should not match everything
    assert entry.matches_lens_model("Sony 50mm") is False


def test_lens_entry_confidence_band_property():
    entry = _make_entry(confidence=0.95)
    assert entry.confidence_band == "certain"
    entry2 = _make_entry(confidence=0.50)
    assert entry2.confidence_band == "low"


def test_lens_entry_total_evidence_count():
    entry = _make_entry()
    assert entry.total_evidence_count == 5  # 4 macro + 1 portrait


# ---------------------------------------------------------------------------
# LensRegistry operations
# ---------------------------------------------------------------------------

def test_registry_match_first_wins():
    reg = LensRegistry()
    reg.add(_make_entry(lens_id="a", display="First", matches=["100-400"]))
    reg.add(_make_entry(lens_id="b", display="Second", matches=["Leica"]))

    # "Leica DG 100-400" could match both; first in list wins
    result = reg.match("Leica DG 100-400")
    assert result is not None
    assert result.id == "a"


def test_registry_match_returns_none_if_no_match():
    reg = LensRegistry()
    reg.add(_make_entry(matches=["Olympus 60mm"]))
    assert reg.match("Sony 50mm f/1.8") is None
    assert reg.match("") is None


def test_registry_find_by_id():
    reg = LensRegistry()
    entry = _make_entry(lens_id="olympus_60mm_macro")
    reg.add(entry)
    assert reg.find_by_id("olympus_60mm_macro") is entry
    assert reg.find_by_id("nonexistent") is None


def test_registry_add_rejects_duplicate_id():
    reg = LensRegistry()
    reg.add(_make_entry(lens_id="x"))
    with pytest.raises(ValueError, match="already in registry"):
        reg.add(_make_entry(lens_id="x"))


def test_registry_remove():
    reg = LensRegistry()
    reg.add(_make_entry(lens_id="a"))
    reg.add(_make_entry(lens_id="b"))
    assert reg.remove("a") is True
    assert reg.find_by_id("a") is None
    assert reg.find_by_id("b") is not None
    assert reg.remove("nonexistent") is False


def test_registry_replace():
    reg = LensRegistry()
    reg.add(_make_entry(lens_id="x", confidence=0.5))
    updated = _make_entry(lens_id="x", confidence=0.95)
    reg.replace(updated)
    found = reg.find_by_id("x")
    assert found is not None
    assert found.confidence == 0.95


def test_registry_replace_missing_raises():
    reg = LensRegistry()
    with pytest.raises(KeyError):
        reg.replace(_make_entry(lens_id="never_added"))


def test_registry_touch_updates_timestamp():
    reg = LensRegistry()
    assert reg.updated == ""
    reg.add(_make_entry())
    assert reg.updated != ""  # now set


# ---------------------------------------------------------------------------
# infer_lens_registry
# ---------------------------------------------------------------------------

def test_infer_single_lens_single_scenario():
    labeled = {
        Scenario.WILDLIFE: ["Leica DG 100-400"] * 5,
    }
    reg = infer_lens_registry(labeled)
    assert len(reg.lenses) == 1
    entry = reg.lenses[0]
    assert entry.display_name == "Leica DG 100-400"
    assert entry.potential_scenarios == [Scenario.WILDLIFE]
    assert entry.confidence == 1.0
    assert entry.evidence == {"wildlife": 5}
    assert entry.source == "bootstrap"


def test_infer_single_lens_multiple_scenarios():
    labeled = {
        Scenario.MACRO: ["Olympus 60mm Macro"] * 4,
        Scenario.PORTRAIT: ["Olympus 60mm Macro"],
    }
    reg = infer_lens_registry(labeled)
    assert len(reg.lenses) == 1
    entry = reg.lenses[0]
    assert entry.potential_scenarios == [Scenario.MACRO, Scenario.PORTRAIT]
    assert entry.confidence == pytest.approx(0.8)
    assert entry.evidence == {"macro": 4, "portrait": 1}


def test_infer_multiple_lenses():
    labeled = {
        Scenario.MACRO: ["Olympus 60mm Macro"] * 4,
        Scenario.PORTRAIT: ["Olympus 60mm Macro", "Leica DG 12-60"],
        Scenario.WILDLIFE: ["Leica DG 100-400"] * 5,
        Scenario.LANDSCAPE: ["Leica DG 12-60"] * 3,
    }
    reg = infer_lens_registry(labeled)
    assert len(reg.lenses) == 3

    olympus = next(e for e in reg.lenses if "Olympus" in e.display_name)
    assert olympus.potential_scenarios[0] == Scenario.MACRO
    assert olympus.confidence == pytest.approx(0.8)

    leica_zoom = next(e for e in reg.lenses if "12-60" in e.display_name)
    assert leica_zoom.potential_scenarios == [Scenario.LANDSCAPE, Scenario.PORTRAIT]
    assert leica_zoom.confidence == pytest.approx(0.75)  # 3/4

    tele = next(e for e in reg.lenses if "100-400" in e.display_name)
    assert tele.potential_scenarios == [Scenario.WILDLIFE]
    assert tele.confidence == 1.0


def test_infer_empty_input_returns_empty_registry():
    reg = infer_lens_registry({})
    assert len(reg.lenses) == 0
    assert reg.version == 1


def test_infer_ignores_empty_lens_names():
    labeled = {
        Scenario.WILDLIFE: ["Leica DG 100-400", "", "  ", "Leica DG 100-400"],
    }
    reg = infer_lens_registry(labeled)
    assert len(reg.lenses) == 1
    assert reg.lenses[0].evidence == {"wildlife": 2}  # empties skipped


def test_infer_rejects_intermediate_scenarios():
    labeled = {
        Scenario.MACRO: ["Olympus 60mm Macro"],
        Scenario.FOCUS_BRACKET: ["Olympus 60mm Macro"],
    }
    with pytest.raises(ValueError, match="Intermediate scenarios"):
        infer_lens_registry(labeled)


def test_infer_rejects_exposure_bracket():
    labeled = {Scenario.EXPOSURE_BRACKET: ["Some Lens"]}
    with pytest.raises(ValueError, match="Intermediate scenarios"):
        infer_lens_registry(labeled)


def test_infer_tied_scenarios_picks_deterministic_primary():
    """When counts are equal, Counter.most_common() returns in insertion order.
    Not strictly deterministic across Python versions for ties, but within
    our usage (same dict iteration) it's stable. Verify the tied scenarios
    all end up in potential_scenarios."""
    labeled = {
        Scenario.MACRO: ["Lens X"] * 2,
        Scenario.PORTRAIT: ["Lens X"] * 2,
    }
    reg = infer_lens_registry(labeled)
    entry = reg.lenses[0]
    assert entry.confidence == 0.5
    assert set(entry.potential_scenarios) == {Scenario.MACRO, Scenario.PORTRAIT}


def test_infer_slugified_id():
    labeled = {Scenario.MACRO: ["Olympus 60mm f/2.8 Macro"]}
    reg = infer_lens_registry(labeled)
    assert reg.lenses[0].id == "olympus_60mm_f_2_8_macro"


# ---------------------------------------------------------------------------
# refine_lens_entry
# ---------------------------------------------------------------------------

def test_refine_adds_to_existing_evidence():
    entry = _make_entry()  # macro=4, portrait=1, confidence=0.95 (arbitrary)
    new = refine_lens_entry(entry, {Scenario.MACRO: 2, Scenario.LANDSCAPE: 1})

    assert new.id == entry.id  # preserved
    assert new.display_name == entry.display_name
    assert new.lens_model_contains == entry.lens_model_contains
    assert new.evidence == {"macro": 6, "portrait": 1, "landscape": 1}
    assert new.potential_scenarios[0] == Scenario.MACRO
    assert Scenario.PORTRAIT in new.potential_scenarios
    assert Scenario.LANDSCAPE in new.potential_scenarios
    assert new.confidence == pytest.approx(6 / 8)  # 0.75


def test_refine_does_not_mutate_original():
    entry = _make_entry()
    original_evidence = dict(entry.evidence)
    _ = refine_lens_entry(entry, {Scenario.MACRO: 5})
    assert entry.evidence == original_evidence


def test_refine_can_shift_primary_scenario():
    entry = _make_entry()  # evidence: macro=4, portrait=1
    # Add a lot of portrait evidence — top potential should flip
    new = refine_lens_entry(entry, {Scenario.PORTRAIT: 10})
    assert new.potential_scenarios[0] == Scenario.PORTRAIT
    assert new.evidence == {"macro": 4, "portrait": 11}
    assert new.confidence == pytest.approx(11 / 15)


def test_refine_with_empty_labels_returns_entry_unchanged_content():
    entry = _make_entry()
    new = refine_lens_entry(entry, {})
    assert new.evidence == entry.evidence
    assert new.potential_scenarios == entry.potential_scenarios


def test_refine_ignores_zero_counts():
    entry = _make_entry()
    new = refine_lens_entry(entry, {Scenario.LANDSCAPE: 0})
    assert "landscape" not in new.evidence


def test_refine_rejects_intermediate():
    entry = _make_entry()
    with pytest.raises(ValueError, match="Intermediate scenarios"):
        refine_lens_entry(entry, {Scenario.FOCUS_BRACKET: 1})


# ---------------------------------------------------------------------------
# create_stub_lens_entry
# ---------------------------------------------------------------------------

def test_stub_has_generic_fallback():
    stub = create_stub_lens_entry("Viltrox 13mm F1.4")
    assert stub.display_name == "Viltrox 13mm F1.4"
    assert stub.id == "viltrox_13mm_f1_4"
    assert stub.potential_scenarios == [Scenario.GENERAL]
    assert stub.confidence == 0.30  # low → flagged for review
    assert stub.confidence_band == "low"
    assert stub.source == "detected"
    assert stub.matches_lens_model("Viltrox 13mm F1.4") is True


def test_stub_custom_fallback_scenario():
    stub = create_stub_lens_entry("Some Macro Lens", fallback_scenario=Scenario.MACRO)
    assert stub.potential_scenarios == [Scenario.MACRO]


def test_stub_empty_input():
    stub = create_stub_lens_entry("")
    assert stub.display_name == "Unknown Lens"
    assert stub.id == "unknown_lens"


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

def test_entry_serialization_roundtrip():
    original = _make_entry(confidence=0.83)
    data = _entry_to_dict(original)
    restored = _entry_from_dict(data)
    assert restored.id == original.id
    assert restored.display_name == original.display_name
    assert restored.lens_model_contains == original.lens_model_contains
    assert restored.potential_scenarios == original.potential_scenarios
    assert restored.confidence == pytest.approx(original.confidence)
    assert restored.source == original.source
    assert restored.evidence == original.evidence


def test_registry_serialization_roundtrip():
    labeled = {
        Scenario.MACRO: ["Olympus 60mm Macro"] * 4,
        Scenario.WILDLIFE: ["Leica DG 100-400"] * 5,
        Scenario.LANDSCAPE: ["Leica DG 12-60"] * 3,
    }
    original = infer_lens_registry(labeled)
    data = _registry_to_dict(original)
    restored = _registry_from_dict(data)

    assert len(restored.lenses) == len(original.lenses)
    for orig, rest in zip(original.lenses, restored.lenses):
        assert orig.id == rest.id
        assert orig.potential_scenarios == rest.potential_scenarios
        assert orig.confidence == pytest.approx(rest.confidence)


def test_entry_from_dict_handles_missing_optional_fields():
    data = {
        "id": "x",
        "potential_scenarios": ["macro"],
    }
    entry = _entry_from_dict(data)
    assert entry.id == "x"
    assert entry.display_name == "x"  # falls back to id
    assert entry.lens_model_contains == []
    assert entry.potential_scenarios == [Scenario.MACRO]
    assert entry.confidence == 0.0
    assert entry.source == "manual"
    assert entry.evidence == {}


def test_entry_from_dict_legacy_schema_migration():
    """The deserializer must accept the OLD schema (primary_scenario +
    fallback_scenarios) so previously-saved files still load. The
    legacy fields are merged into potential_scenarios in order."""
    data = {
        "id": "old",
        "display_name": "Old Lens",
        "lens_model_contains": ["Old"],
        "primary_scenario": "macro",
        "fallback_scenarios": ["portrait", "general"],
        "confidence": 0.7,
        "source": "bootstrap",
        "evidence": {"macro": 7, "portrait": 2, "general": 1},
    }
    entry = _entry_from_dict(data)
    assert entry.potential_scenarios == [
        Scenario.MACRO, Scenario.PORTRAIT, Scenario.GENERAL,
    ]
    assert entry.confidence == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Load/save to disk (via MIRA_DATA_DIR env override)
# ---------------------------------------------------------------------------

def test_load_returns_empty_registry_if_file_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    reg = load_lens_registry()
    assert isinstance(reg, LensRegistry)
    assert len(reg.lenses) == 0


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    labeled = {
        Scenario.MACRO: ["Olympus 60mm Macro"] * 4,
        Scenario.WILDLIFE: ["Leica DG 100-400"] * 5,
    }
    reg = infer_lens_registry(labeled)
    save_lens_registry(reg)

    loaded = load_lens_registry()
    assert len(loaded.lenses) == 2
    olympus = next(e for e in loaded.lenses if "Olympus" in e.display_name)
    assert olympus.potential_scenarios[0] == Scenario.MACRO
    assert olympus.confidence == 1.0  # 4/4


def test_save_is_atomic(tmp_path, monkeypatch):
    """After save, the .tmp file should not remain on disk."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    reg = LensRegistry()
    reg.add(_make_entry())
    save_lens_registry(reg)

    reg_path = tmp_path / "lens_registry.json"
    tmp_file = reg_path.with_suffix(".tmp")
    assert reg_path.exists()
    assert not tmp_file.exists()


def test_load_corrupted_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    (tmp_path / "lens_registry.json").write_text("{not valid json", encoding="utf-8")
    reg = load_lens_registry()
    assert len(reg.lenses) == 0


def test_load_file_with_missing_keys_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    # JSON is valid but a lens entry is missing required potential_scenarios
    bad = {
        "version": 1,
        "lenses": [
            {"id": "broken"}  # no potential_scenarios or legacy primary_scenario
        ],
    }
    (tmp_path / "lens_registry.json").write_text(
        json.dumps(bad), encoding="utf-8"
    )
    reg = load_lens_registry()
    # Should silently fall back to empty rather than crash
    assert len(reg.lenses) == 0
