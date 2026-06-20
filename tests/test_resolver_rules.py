"""spec/90 §3.5 sugar table — every old pin mode expressed as rules + Otherwise.

The rule-list model is strictly more expressive than spec/80's
``keep_all / weed_out / pick_in`` pill group (spec/90 §1.5). The pin modes fall
out as zero-rule compositions with the right Otherwise verdict; the new
``#short`` scenario (pre-pick the bests, rest skipped) becomes expressible.

Headless logic only — no Qt — driven through :meth:`EventGateway.resolve_recipe`.
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


def _photo(item_id, day, t, classification=None):
    return m.Item(
        id=item_id, kind="photo", created_at=FIXED_NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.jpg", sha256="a" * 64,
        byte_size=1000, materialized_at=FIXED_NOW, materialized_phase="ingest",
        camera_id="G9", day_number=day,
        capture_time_raw=t, capture_time_corrected=t,
        classification=classification,
    )


def _doc() -> m.EventDocument:
    """Six exported photos: three feature `#bests`, one features `#rejects`,
    two feature neither — the canvas for every sugar-table case below."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-r", name="Rules fixture", created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        _photo("p1", 1, "2026-04-01T08:00:00"),
        _photo("p2", 1, "2026-04-01T09:00:00"),
        _photo("p3", 1, "2026-04-01T10:00:00"),
        _photo("p4", 1, "2026-04-01T11:00:00"),
        _photo("p5", 1, "2026-04-01T12:00:00"),
        _photo("p6", 1, "2026-04-01T13:00:00"),
    ]
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/p1.jpg", phase="edit",
                  source_kind="item", source_item_id="p1", exported_at="t1"),
        m.Lineage(export_relpath="Exported Media/p2.jpg", phase="edit",
                  source_kind="item", source_item_id="p2", exported_at="t2"),
        m.Lineage(export_relpath="Exported Media/p3.jpg", phase="edit",
                  source_kind="item", source_item_id="p3", exported_at="t3"),
        m.Lineage(export_relpath="Exported Media/p4.jpg", phase="edit",
                  source_kind="item", source_item_id="p4", exported_at="t4"),
        m.Lineage(export_relpath="Exported Media/p5.jpg", phase="edit",
                  source_kind="item", source_item_id="p5", exported_at="t5"),
        m.Lineage(export_relpath="Exported Media/p6.jpg", phase="edit",
                  source_kind="item", source_item_id="p6", exported_at="t6"),
    ]
    # #bests = {p1, p2, p3}, #rejects = {p4}. Both Cuts must be frozen here
    # so the Recipe's strict-ref guard finds them.
    doc.cuts = [
        m.Cut(id="cut-b", tag="bests",
              created_at=FIXED_NOW, updated_at=FIXED_NOW),
        m.Cut(id="cut-r", tag="rejects",
              created_at=FIXED_NOW, updated_at=FIXED_NOW),
    ]
    doc.cut_members = [
        m.CutMember(cut_id="cut-b", export_relpath="Exported Media/p1.jpg",
                    added_at=FIXED_NOW),
        m.CutMember(cut_id="cut-b", export_relpath="Exported Media/p2.jpg",
                    added_at=FIXED_NOW),
        m.CutMember(cut_id="cut-b", export_relpath="Exported Media/p3.jpg",
                    added_at=FIXED_NOW),
        m.CutMember(cut_id="cut-r", export_relpath="Exported Media/p4.jpg",
                    added_at=FIXED_NOW),
    ]
    return doc


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-r")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


ALL_SIX = [
    "Exported Media/p1.jpg",
    "Exported Media/p2.jpg",
    "Exported Media/p3.jpg",
    "Exported Media/p4.jpg",
    "Exported Media/p5.jpg",
    "Exported Media/p6.jpg",
]


def _seed_picked(result) -> set:
    return {k for k, picked in result.seed.items() if picked}


def _seed_skipped(result) -> set:
    return {k for k, picked in result.seed.items() if not picked}


# --------------------------------------------------------------------------- #
# Sugar-table cases — spec/90 §3.5
# --------------------------------------------------------------------------- #


def test_keep_all_equivalent_no_rules_otherwise_pick(gw):
    """`keep_all` ≡ no rules, Otherwise → pick. Every item starts picked."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [],
        "otherwise": "pick",
    })
    assert result.pool == ALL_SIX
    assert _seed_picked(result) == set(ALL_SIX)
    assert _seed_skipped(result) == set()


def test_pick_in_equivalent_no_rules_otherwise_skip(gw):
    """`pick_in` ≡ no rules, Otherwise → skip. Every item starts skipped
    (the user hand-picks the keepers in the Picker session)."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [],
        "otherwise": "skip",
    })
    assert result.pool == ALL_SIX
    assert _seed_picked(result) == set()
    assert _seed_skipped(result) == set(ALL_SIX)


def test_pre_pick_the_bests_rest_skipped(gw):
    """The first new shape: one rule pre-picks `#bests`, Otherwise → skip.
    This is the `#short` shape (spec/90 §3.5 row 4 / §10)."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [
            {
                "predicate": [["+", {"kind": "cut", "tag": "bests"}]],
                "verdict": "pick",
            }
        ],
        "otherwise": "skip",
    })
    assert result.pool == ALL_SIX
    assert _seed_picked(result) == {
        "Exported Media/p1.jpg",
        "Exported Media/p2.jpg",
        "Exported Media/p3.jpg",
    }
    assert _seed_skipped(result) == {
        "Exported Media/p4.jpg",
        "Exported Media/p5.jpg",
        "Exported Media/p6.jpg",
    }


def test_pre_skip_the_rejects_rest_picked(gw):
    """One rule pre-skips `#rejects`, Otherwise → pick (spec/90 §3.5 row 5)."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [
            {
                "predicate": [["+", {"kind": "cut", "tag": "rejects"}]],
                "verdict": "skip",
            }
        ],
        "otherwise": "pick",
    })
    assert _seed_picked(result) == set(ALL_SIX) - {"Exported Media/p4.jpg"}
    assert _seed_skipped(result) == {"Exported Media/p4.jpg"}


def test_two_sided_rules_first_match_wins(gw):
    """Two rules, ordered: skip rejects FIRST, pick bests SECOND, Otherwise
    → skip (spec/90 §3.5 row 6, §1.3 first-match-wins). The Cut `#rejects`
    and `#bests` are disjoint in the fixture so order can't be observed
    on overlap directly — but the seed map still confirms both rules fired."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [
            {
                "predicate": [["+", {"kind": "cut", "tag": "rejects"}]],
                "verdict": "skip",
            },
            {
                "predicate": [["+", {"kind": "cut", "tag": "bests"}]],
                "verdict": "pick",
            },
        ],
        "otherwise": "skip",
    })
    assert _seed_picked(result) == {
        "Exported Media/p1.jpg",
        "Exported Media/p2.jpg",
        "Exported Media/p3.jpg",
    }
    assert _seed_skipped(result) == {
        "Exported Media/p4.jpg",
        "Exported Media/p5.jpg",
        "Exported Media/p6.jpg",
    }


def test_rule_order_matters_when_predicates_overlap(gw):
    """The first-match-wins rule (spec/90 §1.3): when two rules cover the
    same item, the FIRST one in the list dictates the verdict."""
    # Make a synthetic overlap: a sub-Cut containing p1, p2.
    gw.store.upsert(m.Cut(id="cut-o", tag="overlap",
                          created_at=FIXED_NOW, updated_at=FIXED_NOW))
    gw.store.upsert(m.CutMember(
        cut_id="cut-o", export_relpath="Exported Media/p1.jpg",
        added_at=FIXED_NOW))
    gw.store.upsert(m.CutMember(
        cut_id="cut-o", export_relpath="Exported Media/p2.jpg",
        added_at=FIXED_NOW))

    # `bests` = {p1, p2, p3}; `overlap` = {p1, p2}.
    # Rule 1: in #bests → pick. Rule 2: in #overlap → skip.
    # p1, p2 match BOTH; first match (pick) wins.
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [
            {"predicate": [["+", {"kind": "cut", "tag": "bests"}]],
             "verdict": "pick"},
            {"predicate": [["+", {"kind": "cut", "tag": "overlap"}]],
             "verdict": "skip"},
        ],
        "otherwise": "skip",
    })
    # p1, p2 → pick (first rule); p3 → pick (only first rule covers it);
    # everything else → skip (Otherwise).
    assert result.seed["Exported Media/p1.jpg"] is True
    assert result.seed["Exported Media/p2.jpg"] is True
    assert result.seed["Exported Media/p3.jpg"] is True

    # Swap the rule order: skip first, pick second. Now p1, p2 → skip
    # (first match wins).
    swapped = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [
            {"predicate": [["+", {"kind": "cut", "tag": "overlap"}]],
             "verdict": "skip"},
            {"predicate": [["+", {"kind": "cut", "tag": "bests"}]],
             "verdict": "pick"},
        ],
        "otherwise": "skip",
    })
    assert swapped.seed["Exported Media/p1.jpg"] is False
    assert swapped.seed["Exported Media/p2.jpg"] is False
    # p3 isn't in overlap, so the second rule catches it.
    assert swapped.seed["Exported Media/p3.jpg"] is True


def test_rule_predicate_uses_set_algebra(gw):
    """Each rule predicate is a chip + join sentence resolved via the same
    spec/81 set-algebra engine (spec/90 §1.3). Union, difference, intersection
    all work inside a predicate."""
    # Pick if (in #bests but not in #rejects). Since they're disjoint
    # already, equivalent to "in #bests"; this exercises the '-' path.
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [
            {
                "predicate": [
                    ["+", {"kind": "cut", "tag": "bests"}],
                    ["-", {"kind": "cut", "tag": "rejects"}],
                ],
                "verdict": "pick",
            }
        ],
        "otherwise": "skip",
    })
    assert _seed_picked(result) == {
        "Exported Media/p1.jpg",
        "Exported Media/p2.jpg",
        "Exported Media/p3.jpg",
    }


def test_empty_source_raises_value_error(gw):
    """Source is the only required section (spec/90 §1.1). An empty / missing
    source is an author error, surfaced as a plain ValueError."""
    with pytest.raises(ValueError, match="source"):
        gw.resolve_recipe({"otherwise": "skip"})


def test_invalid_otherwise_raises_value_error(gw):
    """Otherwise must be ``'pick'`` or ``'skip'`` (spec/90 §1.1)."""
    with pytest.raises(ValueError, match="otherwise"):
        gw.resolve_recipe({
            "source": [["+", "exported"]],
            "otherwise": "maybe",
        })
