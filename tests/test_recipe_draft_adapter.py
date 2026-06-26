"""spec/90 §7 Phase 3 — Recipe ↔ :class:`CutDraft` adapter.

Round-trips and sugar-table cases for
:func:`mira.shared.recipe_draft_adapter.recipe_to_cut_draft` and
:func:`mira.shared.recipe_draft_adapter.cut_draft_to_recipe_composition`.

* Sugar-table (spec/90 §1.5) — each legacy pin mode round-trips through a
  no-rules composition with the matching Otherwise verdict.
* Rule-based — a non-trivial rule list survives the round trip (predicates,
  verdicts, and order all preserved).
* Cross-flavour misuse — adapting a Collection-flavoured Recipe raises.

Pure logic, no Qt, no DB.
"""
from __future__ import annotations

import json

import pytest

from mira.shared.cut_draft import (
    CrossEventCutDraft,
    CutDraft,
    CutDraftRule,
    OTHERWISE_PICK,
    OTHERWISE_SKIP,
    PIN_KEEP_ALL,
    PIN_PICK_IN,
    PIN_RULE_BASED,
    PIN_WEED_OUT,
)
from mira.shared.recipe_draft_adapter import (
    cross_event_cut_draft_to_recipe_composition,
    cut_draft_to_recipe_composition,
    recipe_to_cross_event_cut_draft,
    recipe_to_cut_draft,
)
from mira.user_store import models as um


NOW = "2026-06-20T12:00:00+00:00"


def _recipe(name: str, composition: dict) -> um.Recipe:
    return um.Recipe(
        id="rcp-1",
        name=name,
        flavour="cut",
        composition_json=json.dumps(composition),
        created_at=NOW,
        updated_at=NOW,
    )


# --------------------------------------------------------------------------- #
# Sugar-table (spec/90 §1.5) — Recipe → CutDraft
# --------------------------------------------------------------------------- #


def test_no_rules_otherwise_skip_maps_to_pick_in():
    """spec/90 §1.5 row: no rules + Otherwise → skip ≡ ``pick-in``."""
    recipe = _recipe("short", {
        "source": [["+", "exported"]],
        "otherwise": "skip",
    })
    draft = recipe_to_cut_draft(recipe)
    assert draft.pin_mode == PIN_PICK_IN
    assert draft.rules == ()
    assert draft.otherwise == OTHERWISE_SKIP
    assert draft.expr == (("+", "exported"),)


def test_no_rules_otherwise_pick_maps_to_weed_out():
    """spec/90 §1.5 row: no rules + Otherwise → pick ≡ ``weed-out``.

    The third sugar case (keep-all = no rules + Otherwise → pick + Picker
    session skipped) isn't expressible in CutDraft today; the adapter
    treats both as weed-out and the dialog can layer the skip-the-picker
    hint on top in Phase 4. Documented in spec/90 §5.1."""
    recipe = _recipe("trim", {
        "source": [["+", "exported"]],
        "otherwise": "pick",
    })
    draft = recipe_to_cut_draft(recipe)
    assert draft.pin_mode == PIN_WEED_OUT
    assert draft.rules == ()
    assert draft.otherwise == OTHERWISE_PICK


def test_filters_carry_through_to_styles_and_media_type():
    recipe = _recipe("short", {
        "source": [["+", "exported"]],
        "filters": {"styles": ["macro", "wildlife"], "media_type": "photo"},
        "otherwise": "skip",
    })
    draft = recipe_to_cut_draft(recipe)
    assert draft.styles == ("macro", "wildlife")
    assert draft.media_type == "photo"


def test_presentation_carries_through_to_draft_fields():
    recipe = _recipe("short", {
        "source": [["+", "exported"]],
        "otherwise": "skip",
        "presentation": {
            "target_s": 90,
            "max_s": 300,
            "photo_s": 4.0,
            "music_category": "happy",
            "card_style": "multi",
            "separators": False,
            "overlay_fields": ["when", "where"],
            "overlay_mode": "embedded",
        },
    })
    draft = recipe_to_cut_draft(recipe)
    assert draft.target_s == 90
    assert draft.max_s == 300
    assert draft.photo_s == 4.0
    assert draft.music_category == "happy"
    assert draft.card_style == "multi"
    assert draft.separators is False
    assert draft.overlay_fields == ("when", "where")
    assert draft.overlay_mode == "embedded"


# --------------------------------------------------------------------------- #
# Rule-based composition — non-trivial rule list survives
# --------------------------------------------------------------------------- #


def test_non_trivial_rules_yield_rule_based_pin_mode():
    """The #short worked example (spec/90 §10) — two rules + Otherwise →
    skip. The draft enters ``rule-based`` mode and the rule list carries
    through verbatim."""
    recipe = _recipe("short", {
        "source": [["+", {"kind": "cut", "tag": "long"}]],
        "rules": [
            {"predicate": [["+", {"kind": "cut", "tag": "blurry"}]],
             "verdict": "skip"},
            {"predicate": [
                ["+", {"kind": "cut", "tag": "best_wildlife"}],
                ["+", {"kind": "cut", "tag": "best_landscapes"}]],
             "verdict": "pick"},
        ],
        "otherwise": "skip",
    })
    draft = recipe_to_cut_draft(recipe)
    assert draft.pin_mode == PIN_RULE_BASED
    assert draft.otherwise == OTHERWISE_SKIP
    assert len(draft.rules) == 2
    # First rule preserves predicate, verdict, AND order.
    assert draft.rules[0].verdict == OTHERWISE_SKIP
    assert draft.rules[0].predicate == (
        ("+", {"kind": "cut", "tag": "blurry"}),
    )
    assert draft.rules[1].verdict == OTHERWISE_PICK
    assert draft.rules[1].predicate == (
        ("+", {"kind": "cut", "tag": "best_wildlife"}),
        ("+", {"kind": "cut", "tag": "best_landscapes"}),
    )


def test_malformed_rules_are_dropped_silently():
    """Charter §5.3 — tolerate, don't crash. A rule with no predicate or
    a bad verdict is skipped; well-formed siblings still surface."""
    recipe = _recipe("short", {
        "source": [["+", "exported"]],
        "rules": [
            {"predicate": [], "verdict": "skip"},        # empty predicate
            {"predicate": [["+", "exported"]]},          # no verdict
            {"predicate": [["+", "exported"]], "verdict": "maybe"},  # bad
            {"predicate": [["+", "exported"]], "verdict": "pick"},   # ok
        ],
        "otherwise": "skip",
    })
    draft = recipe_to_cut_draft(recipe)
    assert len(draft.rules) == 1
    assert draft.rules[0].verdict == OTHERWISE_PICK


# --------------------------------------------------------------------------- #
# Source DC inference
# --------------------------------------------------------------------------- #


def test_single_dc_source_populates_source_dc_id():
    """When source is exactly ``[("+", {"kind": "dc", "id": X})]`` the
    legacy ``source_dc_id`` field on CutDraft surfaces — the spec/81
    "Cut from DC" shape."""
    recipe = _recipe("short", {
        "source": [["+", {"kind": "dc", "id": "dc-42", "tag": "long"}]],
        "otherwise": "skip",
    })
    draft = recipe_to_cut_draft(recipe)
    assert draft.source_dc_id == "dc-42"


def test_composed_source_leaves_source_dc_id_none():
    """A multi-term source is authoritative on ``expr`` — the legacy
    ``source_dc_id`` stays ``None`` because there is no single DC behind
    the formula."""
    recipe = _recipe("short", {
        "source": [
            ["+", "exported"],
            ["-", {"kind": "cut", "tag": "rejects"}],
        ],
        "otherwise": "skip",
    })
    draft = recipe_to_cut_draft(recipe)
    assert draft.source_dc_id is None


# --------------------------------------------------------------------------- #
# Flavour gate
# --------------------------------------------------------------------------- #


def test_collection_flavour_raises():
    """spec/90 §5.5 — the cross-pollination check belongs to the dialog
    (Cut dialog can't render Collection sections); the adapter fails
    loudly when handed the wrong flavour."""
    recipe = um.Recipe(
        id="rcp-c", name="curated", flavour="collection",
        composition_json='{"source":[["+","exported"]],"otherwise":"pick"}',
        created_at=NOW, updated_at=NOW,
    )
    with pytest.raises(ValueError, match="cut"):
        recipe_to_cut_draft(recipe)


# --------------------------------------------------------------------------- #
# CutDraft → composition  + round-trip
# --------------------------------------------------------------------------- #


def _draft_minimal(**overrides) -> CutDraft:
    base = dict(
        name="short", tag="short",
        expr=(("+", "exported"),),
        pin_mode=PIN_PICK_IN,
    )
    base.update(overrides)
    return CutDraft(**base)


def test_pick_in_draft_serialises_to_otherwise_skip():
    draft = _draft_minimal(pin_mode=PIN_PICK_IN)
    comp = cut_draft_to_recipe_composition(draft)
    assert comp["source"] == [["+", "exported"]]
    assert comp["otherwise"] == "skip"
    assert "rules" not in comp


def test_weed_out_draft_serialises_to_otherwise_pick():
    draft = _draft_minimal(pin_mode=PIN_WEED_OUT)
    comp = cut_draft_to_recipe_composition(draft)
    assert comp["otherwise"] == "pick"


def test_keep_all_draft_serialises_to_otherwise_pick():
    """keep-all and weed-out both seed all-in; the composition stores the
    Otherwise verdict (pick) and loses the "Picker session skipped" hint
    spec/90 §1.5 calls out. Documented in spec/90 §5.1."""
    draft = _draft_minimal(pin_mode=PIN_KEEP_ALL)
    comp = cut_draft_to_recipe_composition(draft)
    assert comp["otherwise"] == "pick"


def test_rule_based_draft_serialises_rules_verbatim():
    draft = _draft_minimal(
        pin_mode=PIN_RULE_BASED,
        rules=(
            CutDraftRule(
                predicate=(("+", {"kind": "cut", "tag": "blurry"}),),
                verdict=OTHERWISE_SKIP,
            ),
            CutDraftRule(
                predicate=(
                    ("+", {"kind": "cut", "tag": "best_wildlife"}),
                    ("+", {"kind": "cut", "tag": "best_landscapes"}),
                ),
                verdict=OTHERWISE_PICK,
            ),
        ),
        otherwise=OTHERWISE_SKIP,
    )
    comp = cut_draft_to_recipe_composition(draft)
    assert comp["rules"] == [
        {
            "predicate": [["+", {"kind": "cut", "tag": "blurry"}]],
            "verdict": "skip",
        },
        {
            "predicate": [
                ["+", {"kind": "cut", "tag": "best_wildlife"}],
                ["+", {"kind": "cut", "tag": "best_landscapes"}],
            ],
            "verdict": "pick",
        },
    ]
    assert comp["otherwise"] == "skip"


def test_presentation_block_serialises_optional_fields():
    """Optional fields drop out of the presentation block when unset, so
    the composition stays compact for the simple cases. spec/111 added
    the always-emitted ``aspect`` field (round-trip exact regardless of
    value)."""
    draft = _draft_minimal()
    comp = cut_draft_to_recipe_composition(draft)
    presentation = comp["presentation"]
    assert presentation == {
        "photo_s": 6.0,
        "card_style": "black",
        "separators": True,
        "aspect": "16:9",
    }

    rich = _draft_minimal(
        target_s=90, max_s=300, music_category="happy",
        overlay_fields=("when", "where"), overlay_mode="embedded",
    )
    rich_comp = cut_draft_to_recipe_composition(rich)
    assert rich_comp["presentation"]["target_s"] == 90
    assert rich_comp["presentation"]["max_s"] == 300
    assert rich_comp["presentation"]["music_category"] == "happy"
    assert rich_comp["presentation"]["overlay_fields"] == ["when", "where"]
    assert rich_comp["presentation"]["overlay_mode"] == "embedded"


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #


def test_legacy_pick_in_round_trip():
    """pick-in draft → composition → draft preserves every legacy field
    plus the explicit Otherwise the adapter materialises."""
    original = _draft_minimal(
        pin_mode=PIN_PICK_IN,
        styles=("macro",),
        media_type="photo",
        target_s=120,
        photo_s=5.0,
    )
    comp = cut_draft_to_recipe_composition(original)
    recipe = _recipe(original.name, comp)
    restored = recipe_to_cut_draft(recipe)
    assert restored.pin_mode == original.pin_mode
    assert restored.expr == original.expr
    assert restored.styles == original.styles
    assert restored.media_type == original.media_type
    assert restored.target_s == original.target_s
    assert restored.photo_s == original.photo_s
    assert restored.otherwise == OTHERWISE_SKIP
    assert restored.rules == ()


def test_rule_based_round_trip():
    """Rule-based draft round-trips with rules + verdicts + order intact."""
    rule1 = CutDraftRule(
        predicate=(("+", {"kind": "cut", "tag": "blurry"}),),
        verdict=OTHERWISE_SKIP,
    )
    rule2 = CutDraftRule(
        predicate=(("+", {"kind": "cut", "tag": "best_wildlife"}),),
        verdict=OTHERWISE_PICK,
    )
    original = _draft_minimal(
        pin_mode=PIN_RULE_BASED,
        rules=(rule1, rule2),
        otherwise=OTHERWISE_SKIP,
    )
    comp = cut_draft_to_recipe_composition(original)
    recipe = _recipe(original.name, comp)
    restored = recipe_to_cut_draft(recipe)
    assert restored.pin_mode == PIN_RULE_BASED
    assert restored.otherwise == OTHERWISE_SKIP
    assert restored.rules == original.rules


# --------------------------------------------------------------------------- #
# spec/90 Phase 4f — Collection-flavour Recipe ↔ CrossEventCutDraft
# --------------------------------------------------------------------------- #


def _collection_recipe(name: str, composition: dict) -> um.Recipe:
    return um.Recipe(
        id="rcp-c",
        name=name,
        flavour="collection",
        composition_json=json.dumps(composition),
        created_at=NOW,
        updated_at=NOW,
    )


def test_collection_no_rules_otherwise_skip_maps_to_pick_in():
    """spec/90 §1.5 — Collection face: no rules + Otherwise=skip
    collapses to :data:`PIN_PICK_IN` on the cross-event draft."""
    recipe = _collection_recipe("curated", {
        "source": [["+", "exported"]],
        "otherwise": "skip",
    })
    draft = recipe_to_cross_event_cut_draft(recipe)
    assert isinstance(draft, CrossEventCutDraft)
    assert draft.pin_mode == PIN_PICK_IN
    assert draft.expr == (("+", "exported"),)


def test_collection_no_rules_otherwise_pick_maps_to_weed_out():
    """spec/90 §1.5 — Collection face: no rules + Otherwise=pick
    collapses to :data:`PIN_WEED_OUT`."""
    recipe = _collection_recipe("curated", {
        "source": [["+", "exported"]],
        "otherwise": "pick",
    })
    draft = recipe_to_cross_event_cut_draft(recipe)
    assert draft.pin_mode == PIN_WEED_OUT


def test_collection_filters_forward_full_catalogue():
    """spec/32 §2 / spec/81 §2.1 — every cross-event filter key the
    catalogue admits forwards verbatim from the composition's filters
    block into the draft's ``filters`` dict."""
    recipe = _collection_recipe("curated", {
        "source": [["+", "exported"]],
        "otherwise": "skip",
        "filters": {
            "styles": ["macro"],
            "media_type": "photo",
            "camera_ids": ["Pana+G9M2"],
            "lens_models": ["100-500mm"],
            "person_ids": ["person-pedro"],
            "stars_min": 4,
            "country_codes": ["BR"],
        },
    })
    draft = recipe_to_cross_event_cut_draft(recipe)
    assert draft.filters["styles"] == ["macro"]
    assert draft.filters["media_type"] == "photo"
    assert draft.filters["camera_ids"] == ["Pana+G9M2"]
    assert draft.filters["lens_models"] == ["100-500mm"]
    assert draft.filters["person_ids"] == ["person-pedro"]
    assert draft.filters["stars_min"] == 4
    assert draft.filters["country_codes"] == ["BR"]


def test_collection_separators_default_off():
    """spec/81 §3.1 — cross-event default for separators is OFF (no
    single timeline to orient). Honoured when the composition omits
    the field."""
    recipe = _collection_recipe("curated", {
        "source": [["+", "exported"]],
        "otherwise": "skip",
    })
    draft = recipe_to_cross_event_cut_draft(recipe)
    assert draft.separators is False


def test_collection_separators_explicit_override_honoured():
    """An explicit ``presentation.separators=True`` overrides the
    cross-event default."""
    recipe = _collection_recipe("curated", {
        "source": [["+", "exported"]],
        "otherwise": "skip",
        "presentation": {"separators": True},
    })
    draft = recipe_to_cross_event_cut_draft(recipe)
    assert draft.separators is True


def test_collection_presentation_fields_carry_through():
    """All presentation fields land on the draft."""
    recipe = _collection_recipe("curated", {
        "source": [["+", "exported"]],
        "otherwise": "skip",
        "presentation": {
            "target_s": 600,
            "max_s": 900,
            "photo_s": 4.5,
            "music_category": "ambient",
            "card_style": "single",
            "overlay_fields": ["when", "where"],
            "overlay_mode": "embedded",
        },
    })
    draft = recipe_to_cross_event_cut_draft(recipe)
    assert draft.target_s == 600
    assert draft.max_s == 900
    assert draft.photo_s == 4.5
    assert draft.music_category == "ambient"
    assert draft.card_style == "single"
    assert draft.overlay_fields == ("when", "where")
    assert draft.overlay_mode == "embedded"


def test_collection_source_label_reads_and_round_trips():
    """spec/154 — the per-slide origin-label flag survives composition →
    draft → composition. Off by default (absent key reads False); emitted
    only when ON so a default Cut's composition stays lean."""
    # Absent → OFF.
    recipe_off = _collection_recipe("curated", {
        "source": [["+", "exported"]], "otherwise": "skip",
        "presentation": {},
    })
    assert recipe_to_cross_event_cut_draft(recipe_off).source_label is False
    # Present + True → ON.
    recipe_on = _collection_recipe("curated", {
        "source": [["+", "exported"]], "otherwise": "skip",
        "presentation": {"source_label": True},
    })
    draft = recipe_to_cross_event_cut_draft(recipe_on)
    assert draft.source_label is True
    # Round-trip: ON survives, OFF stays omitted.
    comp_on = cross_event_cut_draft_to_recipe_composition(draft)
    assert comp_on["presentation"]["source_label"] is True
    off_draft = CrossEventCutDraft(
        name="x", tag="x", expr=(("+", "exported"),),
        pin_mode=PIN_PICK_IN, source_label=False)
    comp_off = cross_event_cut_draft_to_recipe_composition(off_draft)
    assert "source_label" not in comp_off["presentation"]


def test_collection_single_dc_source_populates_source_dc_id():
    """Mirror of the event-scope inference: a single ``+`` over a typed
    DC ref surfaces the DC id on the draft."""
    recipe = _collection_recipe("curated", {
        "source": [["+", {"kind": "dc", "id": "dc-42", "tag": "macro"}]],
        "otherwise": "skip",
    })
    draft = recipe_to_cross_event_cut_draft(recipe)
    assert draft.source_dc_id == "dc-42"


def test_collection_rules_collapse_to_pin_mode():
    """spec/90 Phase 4f — the cross-event session has no rule-list
    seeding yet; rules in the composition collapse to ``pin_mode`` via
    the §1.5 sugar (Otherwise verdict drives the mode). A future phase
    will extend the session to honour rule-based seeding directly."""
    recipe = _collection_recipe("curated", {
        "source": [["+", "exported"]],
        "rules": [{"predicate": [["+", "exported"]], "verdict": "pick"}],
        "otherwise": "skip",
    })
    draft = recipe_to_cross_event_cut_draft(recipe)
    # Otherwise=skip → pick-in regardless of rule list.
    assert draft.pin_mode == PIN_PICK_IN


def test_recipe_to_cross_event_cut_draft_rejects_cut_flavour():
    """spec/90 §5.5 — adapter fails loudly on cross-pollination misuse."""
    recipe = um.Recipe(
        id="rcp-cut", name="short", flavour="cut",
        composition_json='{"source":[["+","exported"]],"otherwise":"skip"}',
        created_at=NOW, updated_at=NOW,
    )
    with pytest.raises(ValueError, match="collection"):
        recipe_to_cross_event_cut_draft(recipe)


def test_cross_event_pick_in_round_trip():
    """A pick-in cross-event draft → composition → draft preserves
    every legacy field plus the explicit Otherwise the adapter
    materialises."""
    original = CrossEventCutDraft(
        name="curated", tag="curated",
        expr=(("+", "exported"),),
        filters={"styles": ["macro"], "media_type": "photo",
                 "camera_ids": ["G9"]},
        pin_mode=PIN_PICK_IN,
        target_s=120,
        photo_s=5.0,
    )
    comp = cross_event_cut_draft_to_recipe_composition(original)
    recipe = _collection_recipe(original.name, comp)
    restored = recipe_to_cross_event_cut_draft(recipe)
    assert restored.pin_mode == original.pin_mode
    assert restored.expr == original.expr
    assert restored.filters["styles"] == ["macro"]
    assert restored.filters["camera_ids"] == ["G9"]
    assert restored.target_s == original.target_s
    assert restored.photo_s == original.photo_s


def test_cross_event_weed_out_round_trip():
    """A weed-out draft round-trips to Otherwise=pick + back to
    weed-out."""
    original = CrossEventCutDraft(
        name="curated", tag="curated",
        expr=(("+", "exported"),),
        filters={"media_type": "both"},
        pin_mode=PIN_WEED_OUT,
    )
    comp = cross_event_cut_draft_to_recipe_composition(original)
    assert comp["otherwise"] == "pick"
    recipe = _collection_recipe(original.name, comp)
    restored = recipe_to_cross_event_cut_draft(recipe)
    assert restored.pin_mode == PIN_WEED_OUT
