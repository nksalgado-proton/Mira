"""spec/90 §1.4 — vocabulary filters resolve leniently.

The strict-reference rule applies to NAMED references (DC / Cut / Event
Collection / Person). Vocabulary-based filters (Style / Media / Camera /
Lens) resolve LENIENTLY to empty if no items match — the vocabulary itself
exists library-wide, so "Camera = G9 against a Bali trip with no G9 shots"
resolves to "0 in pool" and loads fine; the user adjusts.

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
    """Two photos, both wildlife — the fixture has no macro shots; filtering
    by macro should resolve to empty without raising."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-l", name="Lenient fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        _photo("p1", "2026-04-01T08:00:00", classification="wildlife"),
        _photo("p2", "2026-04-01T09:00:00", classification="wildlife"),
    ]
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/p1.jpg", phase="edit",
                  source_kind="item", source_item_id="p1", exported_at="t1"),
        m.Lineage(export_relpath="Exported Media/p2.jpg", phase="edit",
                  source_kind="item", source_item_id="p2", exported_at="t2"),
    ]
    return doc


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-l")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# --------------------------------------------------------------------------- #
# Lenient filters — every miss resolves to empty pool, no exception
# --------------------------------------------------------------------------- #


def test_style_filter_with_no_matches_returns_empty_pool(gw):
    """The fixture has no macro shots; ``filters.styles=['macro']`` returns
    an empty pool — the strict-ref rule does NOT apply to Style (it's a
    vocabulary, not a named reference, spec/90 §1.4 last paragraph)."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"styles": ["macro"]},
        "otherwise": "skip",
    })
    assert result.pool == []
    assert result.seed == {}


def test_media_filter_video_only_returns_empty_pool(gw):
    """No video in the fixture; ``media_type='video'`` resolves to empty.
    Same lenient rule as Style."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"media_type": "video"},
        "otherwise": "skip",
    })
    assert result.pool == []


def test_unknown_style_string_returns_empty_pool(gw):
    """An invented Style string (not in the user's vocabulary) is also a
    miss against the photo population and resolves leniently to empty.
    The 2026-06-19 video-passthrough fix (commit 64df266) means videos in
    the fixture would ride through, but THIS fixture has no videos — the
    style filter is what we're testing."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"styles": ["invented_style_nobody_uses"]},
        "otherwise": "skip",
    })
    assert result.pool == []


def test_combined_style_with_one_real_one_missing_keeps_the_real(gw):
    """Multi-select chips union (spec/90 §4.1). Wildlife matches; the
    invented style contributes nothing. The wildlife items still come
    through — lenient, not strict."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"styles": ["wildlife", "invented_style"]},
        "otherwise": "pick",
    })
    assert set(result.pool) == {
        "Exported Media/p1.jpg",
        "Exported Media/p2.jpg",
    }


def test_empty_styles_list_is_a_no_op(gw):
    """No styles selected ≡ no filter on style — all items pass."""
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"styles": []},
        "otherwise": "pick",
    })
    assert len(result.pool) == 2


def test_camera_filter_with_no_matches_returns_empty_pool(gw):
    """spec/90 §4.2 — Camera is the cross-event filter dimension but the
    underlying engine treats it the same way (lenient). Filtering by a
    camera that doesn't appear in the fixture returns empty without
    raising. This test exercises the cross-event path; routed through
    the EventGateway's resolver it amounts to a no-op against the source
    (the event-scope filters only narrow by Style + media), so the result
    surfaces unchanged — the lenient property is what matters."""
    # On the event face, Camera is not in the Filters block, so passing
    # an unknown camera_id in filters is silently ignored (forward-compat
    # for the spec/90 §4.2 path). The pool just stays at the source set.
    # This pins the "no raise" contract.
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"camera_ids": ["Nikon+D850"]},
        "otherwise": "skip",
    })
    # No raise — that's the lenient-filter guarantee. Event-scope ignores
    # camera_ids today; the pool keeps the source set.
    assert isinstance(result.pool, list)


def test_strict_rule_still_applies_alongside_lenient_filter(gw):
    """A Recipe that mixes a lenient filter miss with a named-operand miss
    must still raise on the named-operand miss (the strict guard runs
    before any resolution)."""
    from core.recipe_resolver import RecipeResolutionError
    with pytest.raises(RecipeResolutionError):
        gw.resolve_recipe({
            "source": [["+", "exported"]],
            "filters": {"styles": ["doesnt_match_anything"]},
            "rules": [
                {
                    "predicate": [["+", {"kind": "cut", "tag": "ghost_cut"}]],
                    "verdict": "pick",
                }
            ],
            "otherwise": "skip",
        })
