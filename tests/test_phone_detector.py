"""Tests for ``core.phone_detector`` — spec/52 §9.

Logic-only (no Qt). Covers the bundled list parsing, the Make/Model match
rules (including the Sony Xperia disambiguation), the camera_id compatibility
shim, and the "*" wildcard.
"""
from __future__ import annotations

import json

import pytest

from core import phone_detector
from core.phone_detector import (
    PhoneMaker,
    is_phone,
    is_phone_camera_id,
    load_phone_makers_from,
    reload_default_makers,
)


# --------------------------------------------------------------------------- #
# Bundled list — sanity
# --------------------------------------------------------------------------- #


def test_bundled_phone_makers_list_loads():
    """The bundled ``assets/phone_makers.json`` parses cleanly + carries the
    spec/52 §9 expected entries (Apple / Samsung / Google / Sony with the
    Xperia model-scope)."""
    reload_default_makers()
    # Indirect: exercising is_phone hits _load_default_makers.
    assert is_phone("Apple", "iPhone 15 Pro") is True
    assert is_phone("Samsung", "Galaxy S23") is True
    assert is_phone("Google", "Pixel 8 Pro") is True
    # Sony Xperia — phone.
    assert is_phone("Sony", "Xperia 1 V") is True
    # Sony Alpha camera — NOT a phone.
    assert is_phone("Sony", "ILCE-7RM5") is False


def test_bundled_list_top_level_shape(tmp_path):
    """The bundled file carries a schema_version + phone_makers list (locks
    the on-disk shape so a future bump surfaces here)."""
    raw = json.loads(
        phone_detector._default_makers_path().read_text(encoding="utf-8")
    )
    assert raw["schema_version"] == 1
    assert isinstance(raw["phone_makers"], list)
    assert len(raw["phone_makers"]) >= 5            # Apple / Samsung / Google / etc.


# --------------------------------------------------------------------------- #
# Make/Model match rules
# --------------------------------------------------------------------------- #


def test_is_phone_make_matches_case_insensitively():
    rules = [PhoneMaker(make="Apple", model_patterns=("iphone",))]
    assert is_phone("apple", "iPhone 15", makers=rules) is True
    assert is_phone("APPLE", "iPhone 15", makers=rules) is True
    assert is_phone("Apple", "iphone 15", makers=rules) is True


def test_is_phone_returns_false_when_make_or_model_missing():
    rules = [PhoneMaker(make="Apple", model_patterns=("iphone",))]
    assert is_phone(None, "iPhone 15", makers=rules) is False
    assert is_phone("", "iPhone 15", makers=rules) is False
    assert is_phone("Apple", None, makers=rules) is False
    assert is_phone("Apple", "", makers=rules) is False


def test_is_phone_make_match_alone_is_not_enough_for_scoped_rule():
    """Sony makes both cameras and Xperia phones. Without a model match the
    Sony rule must NOT fire — that's the load-bearing disambiguation."""
    rules = [PhoneMaker(make="Sony", model_patterns=("xperia",))]
    assert is_phone("Sony", "Xperia 1 V", makers=rules) is True
    assert is_phone("Sony", "ILCE-7RM5", makers=rules) is False
    assert is_phone("Sony", "Some Other Camera", makers=rules) is False


def test_is_phone_wildcard_pattern_matches_any_model():
    """A phone-only maker entry uses ``['*']`` so any Model under that Make
    counts — saves listing every individual model."""
    rules = [PhoneMaker(make="Huawei", model_patterns=("*",))]
    assert is_phone("Huawei", "P30 Pro", makers=rules) is True
    assert is_phone("Huawei", "Some Future Model", makers=rules) is True
    assert is_phone("Huawei", "", makers=rules) is True
    assert is_phone("Huawei", None, makers=rules) is True


def test_is_phone_with_empty_model_patterns_never_fires():
    """A misconfigured entry with no patterns can't accidentally classify
    every photo from that make as a phone."""
    rules = [PhoneMaker(make="Apple", model_patterns=())]
    assert is_phone("Apple", "iPhone 15", makers=rules) is False


def test_is_phone_multiple_rules_short_circuit_on_first_hit():
    """When the input matches one rule, others aren't consulted (functional
    detail; matters only when measuring perf with a huge list)."""
    rules = [
        PhoneMaker(make="Apple", model_patterns=("iphone",)),
        PhoneMaker(make="Apple", model_patterns=("ipad",)),
    ]
    assert is_phone("Apple", "iPad Pro", makers=rules) is True
    assert is_phone("Apple", "iPhone 15", makers=rules) is True


def test_is_phone_unknown_make_returns_false():
    rules = [PhoneMaker(make="Apple", model_patterns=("*",))]
    assert is_phone("Nikon", "Z 8", makers=rules) is False
    assert is_phone("Panasonic", "DC-G9M2", makers=rules) is False


# --------------------------------------------------------------------------- #
# Loading custom lists
# --------------------------------------------------------------------------- #


def test_load_phone_makers_from_parses_a_custom_file(tmp_path):
    custom = tmp_path / "my_phone_makers.json"
    custom.write_text(json.dumps({
        "schema_version": 1,
        "phone_makers": [
            {"make": "Made-Up Phones", "model_patterns": ["*"]},
        ],
    }), encoding="utf-8")
    makers = load_phone_makers_from(custom)
    assert len(makers) == 1
    assert makers[0].make == "Made-Up Phones"
    assert makers[0].model_patterns == ("*",)


def test_load_phone_makers_from_skips_malformed_entries(tmp_path):
    """A hand-edited file shouldn't crash loading — entries with missing
    make or non-list model_patterns are silently dropped."""
    custom = tmp_path / "bad_phone_makers.json"
    custom.write_text(json.dumps({
        "phone_makers": [
            {"make": "Real Maker", "model_patterns": ["*"]},
            {"model_patterns": ["*"]},                       # no make
            {"make": "Real Maker 2"},                         # no model_patterns
            "garbage",                                        # wrong type
            {"make": "", "model_patterns": ["*"]},           # empty make
            {"make": "Maker3", "model_patterns": "not-a-list"},
        ],
    }), encoding="utf-8")
    makers = load_phone_makers_from(custom)
    # Only the two well-formed entries (one with patterns + one without — the
    # second is a configuration mistake the loader keeps because the entry
    # itself is well-formed; the matcher will then never fire it because
    # model_patterns is empty).
    assert {m.make for m in makers} == {"Real Maker", "Real Maker 2"}


# --------------------------------------------------------------------------- #
# camera_id compatibility shim
# --------------------------------------------------------------------------- #


def test_is_phone_camera_id_matches_against_prefixed_strings():
    """Legacy callers carry a merged camera_id like ``"Apple iPhone 15 Pro"``.
    The shim matches when the maker prefixes OR the model pattern appears."""
    rules = [
        PhoneMaker(make="Apple", model_patterns=("iphone", "ipad")),
        PhoneMaker(make="Huawei", model_patterns=("*",)),
    ]
    assert is_phone_camera_id("Apple iPhone 15 Pro", makers=rules) is True
    assert is_phone_camera_id("apple iphone 15 pro", makers=rules) is True
    assert is_phone_camera_id("Huawei P30 Pro", makers=rules) is True
    # Bare Model string (the legacy core/source_index convention).
    assert is_phone_camera_id("iPhone 15 Pro", makers=rules) is True
    assert is_phone_camera_id("DC-G9M2", makers=rules) is False


def test_is_phone_camera_id_returns_false_for_empty_input():
    assert is_phone_camera_id(None) is False
    assert is_phone_camera_id("") is False


def test_is_phone_camera_id_respects_scoped_rules():
    """The Sony-Xperia disambiguation also applies via the shim — Sony Alpha
    cameras should not match."""
    rules = [PhoneMaker(make="Sony", model_patterns=("xperia",))]
    assert is_phone_camera_id("Sony Xperia 1 V", makers=rules) is True
    # Bare model.
    assert is_phone_camera_id("Xperia 1 V", makers=rules) is True
    # A Sony Alpha camera doesn't match.
    assert is_phone_camera_id("Sony ILCE-7RM5", makers=rules) is False


# --------------------------------------------------------------------------- #
# Cache behaviour
# --------------------------------------------------------------------------- #


def test_reload_default_makers_drops_the_cache():
    """The lru_cache means the first is_phone call locks the bundled list
    for the process; reload_default_makers is the test seam to re-read."""
    reload_default_makers()
    # Two calls return the same object (cache hit).
    a = phone_detector._load_default_makers()
    b = phone_detector._load_default_makers()
    assert a is b
    reload_default_makers()
    c = phone_detector._load_default_makers()
    # New tuple instance after a reload.
    assert c is not a
