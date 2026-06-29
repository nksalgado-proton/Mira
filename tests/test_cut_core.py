"""spec/61 slice 2 — the pure-logic Cut helpers.

``core.cut_names``: the name→tag transform the New Cut dialog previews live
(lowercase, accents stripped, separators→underscores, junk dropped) + the
validation codes (empty / reserved / taken, case-blind).

``core.cut_budget``: minutes-are-the-truth accounting (photos + separators at
photo_s, clips at true duration), the green/amber/red zones, and the
photo-only rough hint.
"""
from core import cut_budget, cut_names


# --------------------------------------------------------------------------- #
# cut_names.slugify — the transform shown live in the dialog
# --------------------------------------------------------------------------- #


def test_slugify_basic_lowercase_and_underscores():
    assert cut_names.slugify("Best Macro Shots") == "best_macro_shots"


def test_slugify_strips_accents():
    # Nelson's users type Portuguese — accents transform, never choke.
    assert cut_names.slugify("Pássaros do Pantanal") == "passaros_do_pantanal"
    assert cut_names.slugify("São João — Família") == "sao_joao_familia"


def test_slugify_collapses_separator_runs():
    assert cut_names.slugify("best - macro -- shots") == "best_macro_shots"
    assert cut_names.slugify("  trim me  ") == "trim_me"


def test_slugify_drops_junk_keeps_digits():
    assert cut_names.slugify("Top 10! (2026) ★") == "top_10_2026"


def test_slugify_case_blind_collision():
    # "Best Macro" and "best macro" must reduce identically — uniqueness
    # is checked on the transformed result (spec/61 §1.5).
    assert cut_names.slugify("Best Macro") == cut_names.slugify("best macro")


def test_slugify_empty_when_nothing_usable():
    assert cut_names.slugify("★ ♥ ✓") == ""


def test_display_tag_prefixes_hash():
    assert cut_names.display_tag("best_macro_shots") == "#best_macro_shots"


# --------------------------------------------------------------------------- #
# cut_names.check_tag — validation codes
# --------------------------------------------------------------------------- #


def test_check_tag_ok():
    assert cut_names.check_tag("best_macro_shots", ["other_cut"]) is None


def test_check_tag_empty():
    assert cut_names.check_tag("", []) == "empty"


def test_check_tag_reserved_builtins():
    # The built-in live queries can never be shadowed by a user Cut.
    for reserved in ("exported", "collected", "picked", "edited"):
        assert cut_names.check_tag(reserved, []) == "reserved"


def test_check_tag_taken_is_case_blind():
    assert cut_names.check_tag("best_macro", ["BEST_MACRO"]) == "taken"


def test_uniquify_returns_base_when_free():
    assert cut_names.uniquify("macro", ["wildlife", "street"]) == "macro"


def test_uniquify_suffixes_when_taken():
    # Pinning a Collection to a Cut: the Collection's own tag is always
    # taken (shared namespace), so the default must shift to a free variant.
    assert cut_names.uniquify("macro", ["macro"]) == "macro_2"
    assert cut_names.uniquify("macro", ["macro", "macro_2"]) == "macro_3"


def test_uniquify_is_case_blind():
    assert cut_names.uniquify("Macro", ["macro"]) == "Macro_2"


def test_uniquify_empty_base_passes_through():
    assert cut_names.uniquify("", ["macro"]) == ""


# --------------------------------------------------------------------------- #
# cut_budget — minutes are the truth
# --------------------------------------------------------------------------- #


def test_show_totals_seconds_mixes_photos_separators_and_clip_durations():
    totals = cut_budget.ShowTotals(
        photo_count=10, video_count=2, separator_count=3, video_ms_total=90_000)
    # (10 photos + 3 separators) × 6 s + 90 s of clips = 168 s
    assert totals.seconds(photo_s=6.0) == 168.0


def test_zone_green_amber_red():
    assert cut_budget.zone(500, target_s=600, max_s=720) == cut_budget.ZONE_GREEN
    assert cut_budget.zone(600, target_s=600, max_s=720) == cut_budget.ZONE_GREEN
    assert cut_budget.zone(650, target_s=600, max_s=720) == cut_budget.ZONE_AMBER
    assert cut_budget.zone(721, target_s=600, max_s=720) == cut_budget.ZONE_RED


def test_zone_degenerate_budgets():
    # No limits at all — the Cut simply has no zone.
    assert cut_budget.zone(10_000, None, None) == cut_budget.ZONE_NONE
    # Target only: over-target reads amber (no max to breach).
    assert cut_budget.zone(650, target_s=600, max_s=None) == cut_budget.ZONE_AMBER
    # Max only: a single hard line.
    assert cut_budget.zone(700, target_s=None, max_s=720) == cut_budget.ZONE_GREEN
    assert cut_budget.zone(721, target_s=None, max_s=720) == cut_budget.ZONE_RED


def test_photo_only_hint_keep_rate():
    # 600 s target at 6 s/photo = 100 slots, minus 8 separators = 92 slides;
    # 500 pool photos → keep roughly 1 in 5.
    hint = cut_budget.photo_only_hint(500, separator_count=8, photo_s=6.0, target_s=600)
    assert hint.slides_fit == 92
    assert hint.keep_one_in == 5


def test_photo_only_hint_everything_fits():
    hint = cut_budget.photo_only_hint(40, separator_count=2, photo_s=6.0, target_s=600)
    assert hint.slides_fit == 98
    assert hint.keep_one_in is None  # no culling pressure — hide the ratio


def test_photo_only_hint_absent_without_target():
    assert cut_budget.photo_only_hint(500, 8, 6.0, None) is None
