"""spec/105 §2 — volume-aware default target for Cut exports.

The new layout keeps hardlinks working wherever an event physically
lives:

* per-event Cut, event on the SAME volume as `library_root` →
  `<library_root>/Cuts/<event slug>/<cut slug>/`
* per-event Cut, event on a DIFFERENT volume (the
  `event_root_abs` external-event escape hatch) →
  `<event_root>/Cuts/<cut slug>/`
* cross-event Cut → `<library_root>/Cuts/Cross-event/<cut slug>/`
* `cuts_export_root` set → under it verbatim

These tests pin the resolver shape without spinning up a real
multi-volume setup — `_same_volume` is monkeypatched to simulate
off-volume events.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.shared import cut_export
from mira.shared.cut_export import (
    resolve_cross_event_cut_target,
    resolve_event_cut_target,
)


# ── Per-event Cut target — same volume → library_root/Cuts/<event>/<cut>


def test_same_volume_event_lands_under_library_cuts(tmp_path):
    """The encouraged "one root" install: event lives under
    `library_root`, so the resolver puts Cuts in one discoverable
    home `<library_root>/Cuts/<event slug>/<cut slug>/`."""
    library_root = tmp_path / "lib"
    event_root = library_root / "Costa Rica 2026"
    event_root.mkdir(parents=True)
    target = resolve_event_cut_target(
        event_root=event_root,
        event_name="Costa Rica 2026",
        cut_tag="best_macro_shots",
        library_root=library_root,
    )
    assert target == (
        library_root / "Cuts" / "costa_rica_2026" / "best_macro_shots")


def test_event_slug_uses_event_name_sanitiser(tmp_path):
    """The event-slug component is the canonical
    `cut_names.slugify_event_name` (lowercase, accents stripped,
    separators → underscores) so the Cuts home is predictable across
    renames touching only accents / punctuation."""
    library_root = tmp_path / "lib"
    event_root = library_root / "X"
    event_root.mkdir(parents=True)
    target = resolve_event_cut_target(
        event_root=event_root,
        event_name="Pássaros do Pantanal!!!",
        cut_tag="cut1",
        library_root=library_root,
    )
    # 'Pássaros do Pantanal!!!' → 'passaros_do_pantanal'
    assert target.parent.name == "passaros_do_pantanal"
    assert target.parent == library_root / "Cuts" / "passaros_do_pantanal"


def test_blank_event_name_falls_back_to_safe_slug(tmp_path):
    """A blank / unusable event name falls back to ``"event"`` so the
    path component is never empty (an empty component would either
    crash or land Cuts at `<library>/Cuts//<cut>` — neither is OK)."""
    library_root = tmp_path / "lib"
    event_root = library_root / "x"
    event_root.mkdir(parents=True)
    target = resolve_event_cut_target(
        event_root=event_root,
        event_name="!!!",
        cut_tag="cut1",
        library_root=library_root,
    )
    assert target == library_root / "Cuts" / "event" / "cut1"


# ── Per-event Cut target — off-volume → event_root/Cuts/<cut>


def test_off_volume_event_lands_under_event_root(tmp_path, monkeypatch):
    """An external-event (`event_root_abs`) on a different volume than
    `library_root` keeps its Cuts on the event's OWN volume so links
    still work. Simulate by patching `_same_volume` to return False."""
    library_root = tmp_path / "lib"
    event_root = tmp_path / "external" / "Foo Event"
    event_root.mkdir(parents=True)
    library_root.mkdir()
    monkeypatch.setattr(
        cut_export, "_same_volume", lambda a, b: False)
    target = resolve_event_cut_target(
        event_root=event_root,
        event_name="Foo Event",
        cut_tag="cut1",
        library_root=library_root,
    )
    assert target == event_root / "Cuts" / "cut1"
    # The event-slug component is NOT inserted in this branch — the
    # event's own folder name is its discriminator already.
    assert "Foo Event" not in str(target.relative_to(event_root))


# ── cuts_export_root set → under it verbatim


def test_cuts_export_root_overrides_per_event(tmp_path):
    """`cuts_export_root` honoured verbatim:
    `<cuts_export_root>/<event slug>/<cut slug>/`. The §6 dialog warns
    when this lands on a different volume than the event's media."""
    library_root = tmp_path / "lib"
    event_root = library_root / "Trip"
    event_root.mkdir(parents=True)
    cuts_root = tmp_path / "external_cuts"
    target = resolve_event_cut_target(
        event_root=event_root,
        event_name="Trip",
        cut_tag="cut1",
        library_root=library_root,
        cuts_export_root=cuts_root,
    )
    assert target == cuts_root / "trip" / "cut1"


def test_cuts_export_root_blank_falls_through_to_default(tmp_path):
    """Empty string is the same as None — falls through to the
    volume-aware default rather than landing at `/<event>/<cut>`."""
    library_root = tmp_path / "lib"
    event_root = library_root / "Trip"
    event_root.mkdir(parents=True)
    target = resolve_event_cut_target(
        event_root=event_root,
        event_name="Trip",
        cut_tag="cut1",
        library_root=library_root,
        cuts_export_root="",
    )
    assert target == library_root / "Cuts" / "trip" / "cut1"


# ── Cross-event Cut target


def test_cross_event_default_lands_under_library_cross_event(tmp_path):
    """Cross-event Cuts span events / volumes — the default home is
    the library: `<library_root>/Cuts/Cross-event/<cut slug>/`."""
    library_root = tmp_path / "lib"
    library_root.mkdir()
    target = resolve_cross_event_cut_target(
        cut_tag="best_of_2026",
        library_root=library_root,
    )
    assert target == library_root / "Cuts" / "Cross-event" / "best_of_2026"


def test_cross_event_honours_cuts_export_root(tmp_path):
    library_root = tmp_path / "lib"
    library_root.mkdir()
    cuts_root = tmp_path / "external_cuts"
    target = resolve_cross_event_cut_target(
        cut_tag="best_of_2026",
        library_root=library_root,
        cuts_export_root=cuts_root,
    )
    assert target == cuts_root / "Cross-event" / "best_of_2026"


# ── _same_volume helper sanity


def test_same_volume_within_one_tmp_dir_is_true(tmp_path):
    """The temp-dir tree is one volume — every path under it agrees
    with every other path. The regression guard: a future refactor
    that broke this would silently land all event Cuts under their
    own folder instead of the library's, doubling the per-event
    discovery cost."""
    a = tmp_path / "lib"
    b = tmp_path / "event"
    a.mkdir()
    b.mkdir()
    assert cut_export._same_volume(a, b) is True
