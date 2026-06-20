"""spec/90 §10 — the worked `#short` example, end-to-end.

The original session's scenario that motivated the rule-list model:

    Name:    `short`
    Source:  Start from `[#long]`
    Filters: (none)
    Rules:
      1. If items are in `[#blurry]`                         → skip
      2. If items are in `[#best_wildlife]` or `[#best_landscapes]` → pick
    Otherwise → skip

Pressing **Start** opens the Picker session pre-seeded with the items rule 2
covers as picked and everything else as skipped, MINUS the blurry items
(which rule 1 caught first — first-match-wins). The Cut grows from there.

Headless logic only — no Qt.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore

FIXED_NOW = "2026-06-20T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _photo(item_id, t, classification=None):
    return m.Item(
        id=item_id, kind="photo", created_at=FIXED_NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.jpg", sha256="a" * 64,
        byte_size=1000, materialized_at=FIXED_NOW, materialized_phase="ingest",
        camera_id="G9", day_number=1,
        capture_time_raw=t, capture_time_corrected=t,
        classification=classification,
    )


def _doc() -> m.EventDocument:
    """The `#short` scenario, scaled down to a hand-checkable fixture:

    * `#long` is the source Cut (8 photos: 3 wildlife, 3 landscape, 2 misc)
    * `#blurry` flags p1 and p4 (one wildlife, one landscape)
    * `#best_wildlife` flags p2 and p3 (the non-blurry wildlife shots)
    * `#best_landscapes` flags p5 and p6 (the non-blurry landscape shots)

    The expected seed map:

    * p1, p4   → skip (rule 1: in `#blurry`)
    * p2, p3   → pick (rule 2: in `#best_wildlife`)
    * p5, p6   → pick (rule 2: in `#best_landscapes`)
    * p7, p8   → skip (Otherwise — no rule matched)
    """
    doc = m.EventDocument(event=m.Event(
        uuid="evt-w", name="Short scenario fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        _photo("p1", "2026-04-01T08:00:00", "wildlife"),
        _photo("p2", "2026-04-01T09:00:00", "wildlife"),
        _photo("p3", "2026-04-01T10:00:00", "wildlife"),
        _photo("p4", "2026-04-01T11:00:00", "landscape"),
        _photo("p5", "2026-04-01T12:00:00", "landscape"),
        _photo("p6", "2026-04-01T13:00:00", "landscape"),
        _photo("p7", "2026-04-01T14:00:00"),
        _photo("p8", "2026-04-01T15:00:00"),
    ]
    doc.lineage = [
        m.Lineage(export_relpath=f"Exported Media/p{i}.jpg", phase="edit",
                  source_kind="item", source_item_id=f"p{i}",
                  exported_at=f"t{i}")
        for i in range(1, 9)
    ]
    # Each pinned Cut is the spec/90 §10 scenario's named operand.
    doc.cuts = [
        m.Cut(id="cut-long", tag="long",
              created_at=FIXED_NOW, updated_at=FIXED_NOW),
        m.Cut(id="cut-blur", tag="blurry",
              created_at=FIXED_NOW, updated_at=FIXED_NOW),
        m.Cut(id="cut-bw", tag="best_wildlife",
              created_at=FIXED_NOW, updated_at=FIXED_NOW),
        m.Cut(id="cut-bl", tag="best_landscapes",
              created_at=FIXED_NOW, updated_at=FIXED_NOW),
    ]
    members = []

    def _mb(cut_id: str, *idxs: int) -> None:
        for i in idxs:
            members.append(m.CutMember(
                cut_id=cut_id,
                export_relpath=f"Exported Media/p{i}.jpg",
                added_at=FIXED_NOW))

    _mb("cut-long", 1, 2, 3, 4, 5, 6, 7, 8)
    _mb("cut-blur", 1, 4)
    _mb("cut-bw", 2, 3)
    _mb("cut-bl", 5, 6)
    doc.cut_members = members
    return doc


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-w")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# --------------------------------------------------------------------------- #
# The end-to-end worked example
# --------------------------------------------------------------------------- #


def test_short_scenario_resolves_to_expected_seed_map(gw):
    """spec/90 §10 — the worked example. Two rules, first-match-wins,
    Otherwise → skip."""
    composition = {
        "source": [["+", {"kind": "cut", "tag": "long"}]],
        "rules": [
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
        ],
        "otherwise": "skip",
    }
    result = gw.resolve_recipe(composition)

    # The pool is every member of `#long`, in chronological order.
    assert result.pool == [
        f"Exported Media/p{i}.jpg" for i in range(1, 9)
    ]

    # Seed map: p2/p3 + p5/p6 → pick; everything else → skip.
    pick_keys = {k for k, picked in result.seed.items() if picked}
    skip_keys = {k for k, picked in result.seed.items() if not picked}
    assert pick_keys == {
        "Exported Media/p2.jpg",
        "Exported Media/p3.jpg",
        "Exported Media/p5.jpg",
        "Exported Media/p6.jpg",
    }
    assert skip_keys == {
        "Exported Media/p1.jpg",
        "Exported Media/p4.jpg",
        "Exported Media/p7.jpg",
        "Exported Media/p8.jpg",
    }


def test_short_scenario_blurry_rule_overrides_best_rule(gw):
    """A pathological synthetic — overlap p1 into both #blurry AND
    #best_wildlife. Rule 1 (skip) is FIRST in the list so it wins
    (spec/90 §1.3 first-match-wins). This pins the rule-order semantics
    that spec/90 §10 implicitly assumes ("rule 1: skip if blurry"
    catches blurry items even if they would have been "best" otherwise)."""
    # Add p1 to #best_wildlife — now it's BOTH blurry AND best_wildlife.
    gw.store.upsert(m.CutMember(
        cut_id="cut-bw", export_relpath="Exported Media/p1.jpg",
        added_at=FIXED_NOW))

    composition = {
        "source": [["+", {"kind": "cut", "tag": "long"}]],
        "rules": [
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
        ],
        "otherwise": "skip",
    }
    result = gw.resolve_recipe(composition)
    # p1 is blurry first, so rule 1 wins.
    assert result.seed["Exported Media/p1.jpg"] is False
    # p2/p3/p5/p6 still pick.
    assert result.seed["Exported Media/p2.jpg"] is True
    assert result.seed["Exported Media/p5.jpg"] is True


def test_short_scenario_lives_in_a_recipe_blob(gw):
    """End-to-end through a JSON round-trip — the same composition stored
    as ``recipe.composition_json`` resolves identically to the inline dict
    above. This pins the Phase 3 (Recipe persistence) call shape."""
    import json
    composition = {
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
        "presentation": {
            "target_s": 90, "photo_s": 6.0, "card_style": "multi",
        },
    }
    # The Recipe row would hold this as composition_json. Round-trip it.
    blob = json.dumps(composition)
    rehydrated = json.loads(blob)
    result = gw.resolve_recipe(rehydrated)
    pick_keys = {k for k, picked in result.seed.items() if picked}
    assert pick_keys == {
        "Exported Media/p2.jpg",
        "Exported Media/p3.jpg",
        "Exported Media/p5.jpg",
        "Exported Media/p6.jpg",
    }
