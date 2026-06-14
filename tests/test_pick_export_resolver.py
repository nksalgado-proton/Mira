"""Tests for core.cull_export_resolver (Stage C inc.2).

Pure. Proves the shared Style recipe (override ?? cached ??
classify ?? GENERAL) matches the canvas/commit one, and that the
manifest lands kept photos under 02 - Selected/<Dia N>/<Style>
(bracket → sub-folder) with the courtesy prefix.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.cull_export_resolver import (
    KeptItem,
    build_export_manifest,
    effective_style,
)
from core.genre import set_genre_override

# Cached-auto entry shape is private to core.genre; build it via the
# documented peek contract. Since 00.090 the entry MUST include v +
# src stamps for the cache to be considered current — entries without
# them are treated as stale and recompute.
from core.genre import _AUTO_KEY, _rules_version  # noqa: PLC2701


def _stamped(scenario: str, *, review: bool = False, src: str = "camera"):
    """Build a current-stamped genre_auto entry for fixtures."""
    return {
        "s": scenario, "r": review,
        "v": _rules_version(src), "src": src,
    }


def test_effective_style_prefers_override():
    j: dict = {}
    set_genre_override(j, "a.rw2", "wildlife")
    # Override wins over any cache / classification.
    j[_AUTO_KEY] = {"a.rw2": _stamped("landscape")}
    assert effective_style(j, "a.rw2", {"Make": "x"}) == "wildlife"


def test_effective_style_uses_cached_auto_when_no_override():
    j = {_AUTO_KEY: {"a.rw2": _stamped("landscape")}}
    assert effective_style(j, "a.rw2", None) == "landscape"


def test_effective_style_classifies_when_nothing_cached():
    # No override, no cache, but EXIF present → classify now. We
    # don't assert the scenario (rules are tested elsewhere), only
    # that we get a non-empty folder-safe slug, not a crash.
    s = effective_style({}, "a.rw2", {"Make": "Panasonic",
                                      "Model": "DC-G9M2"})
    assert isinstance(s, str) and s and "/" not in s and "\\" not in s


def test_effective_style_general_when_no_exif():
    assert effective_style({}, "a.rw2", None) == "general"
    assert effective_style({}, "a.rw2", {}) == "general"


def test_manifest_day_style_layout_and_courtesy_name():
    items = [
        KeptItem(
            src=Path("/card/P1010001.RW2"),
            capture_dt=datetime(2025, 10, 26, 7, 8, 9),
            day_label="Dia 1 - Kathmandu",
            style="wildlife",
        ),
    ]
    # dest_root is laid Day/Style DIRECTLY — never re-derives
    # "02 - Selected" (the user picks the destination).
    out = build_export_manifest(items, Path("/picked/dest"))
    assert len(out) == 1
    e = out[0]
    assert e.dest_dir == Path(
        "/picked/dest/Dia 1 - Kathmandu/wildlife")
    assert "02 - Selected" not in str(e.dest_dir)
    assert e.dest_name == "20251026_070809_P1010001.RW2"
    assert e.src == Path("/card/P1010001.RW2")


def test_event_default_dest_is_01_culled():
    """Pipeline-taxonomy freeze 2026-05-19: an in-event Cull Export
    defaults to ``01 - Culled`` (the Cull-phase output); the Select
    phase later consolidates → ``02 - Selected``."""
    from core.cull_export_resolver import event_default_dest
    assert event_default_dest(Path("/ev/trips/2025 - Nepal")) == \
        Path("/ev/trips/2025 - Nepal/01 - Culled")


def test_manifest_bracket_gets_subfolder():
    items = [
        KeptItem(
            src=Path("/c/f1.rw2"), capture_dt=None,
            day_label="Dia 9 - EBC", style="landscape",
            bracket_id="FB_0007",
        ),
    ]
    e = build_export_manifest(items, Path("/dest"))[0]
    assert e.dest_dir == Path(
        "/dest/Dia 9 - EBC/landscape/FB_0007")
    # No timestamp → courtesy prefix omitted (name unchanged).
    assert e.dest_name == "f1.rw2"


def test_manifest_sanitises_style_and_bracket():
    from core.path_builder import sanitize_folder_name
    e = build_export_manifest([
        KeptItem(Path("/c/a.jpg"), None, "Dia 1",
                 "wild/life:x", "br*?id"),
    ], Path("/ev"))[0]
    # The Style + bracket path components are folder-safe.
    parts = e.dest_dir.parts
    assert parts[-2] == sanitize_folder_name("wild/life:x")
    assert parts[-1] == sanitize_folder_name("br*?id")
    for comp in (parts[-2], parts[-1]):
        assert not (set(comp) & set('<>:"/\\|?*'))
