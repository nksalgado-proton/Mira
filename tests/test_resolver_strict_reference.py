"""spec/90 §1.4 — strict reference resolution.

Named operands (DC / Cut / Event Collection / Event / Person) that don't
exist at resolution time raise :class:`RecipeResolutionError` with the
operand's user-facing label and kind. Vocabulary-based filters (Style /
Media / Camera / Lens) are NOT in the strict set — they resolve leniently
to empty (see :mod:`tests.test_resolver_lenient_filter`).

Event-scope covers DC / Cut / Person. Cross-event adds Event Collection +
Event. Headless logic only — no Qt.
"""
from __future__ import annotations

import itertools

import pytest

from core.recipe_resolver import RecipeResolutionError
from mira.gateway.event_gateway import EventGateway
from mira.gateway.library_gateway import LibraryGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.user_store import models as um
from mira.user_store.repo import UserStore

FIXED_NOW = "2026-06-20T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _photo(item_id, t):
    return m.Item(
        id=item_id, kind="photo", created_at=FIXED_NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.jpg", sha256="a" * 64,
        byte_size=1000, materialized_at=FIXED_NOW, materialized_phase="ingest",
        camera_id="G9", day_number=1,
        capture_time_raw=t, capture_time_corrected=t,
    )


def _event_doc() -> m.EventDocument:
    """One exported photo, one DC, one Cut — the minimal scaffolding for
    every "exists vs missing" probe."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-s", name="Strict fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [_photo("p1", "2026-04-01T08:00:00")]
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/p1.jpg", phase="edit",
                  source_kind="item", source_item_id="p1", exported_at="t1"),
    ]
    doc.dynamic_collections = [m.DynamicCollection(
        id="dc-1", tag="real_dc",
        created_at=FIXED_NOW, updated_at=FIXED_NOW,
        expr_json='[["+", "exported"]]')]
    doc.cuts = [m.Cut(id="cut-1", tag="real_cut",
                      created_at=FIXED_NOW, updated_at=FIXED_NOW)]
    doc.cut_members = [m.CutMember(
        cut_id="cut-1", export_relpath="Exported Media/p1.jpg",
        added_at=FIXED_NOW)]
    return doc


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-s")
    store.save_document(_event_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def lib(tmp_path):
    store = UserStore.create(
        tmp_path / "mira.db", app_version="test", created_at=FIXED_NOW)
    # One known event, one DC, one Event Collection, one Person — the
    # cross-event existence-table for the strict cases below.
    store.upsert(um.EventIndex(
        event_uuid="evt-known", relpath_to_base="Known"))
    store.upsert(um.SavedFilter(
        id="sf-1", tag="real_xdc", expr_json='[["+", "exported"]]',
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    store.upsert(um.EventCollection(
        id="ec-1", tag="real_ec", expr_json='[]',
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    store.upsert(um.Person(
        id="person-1", display_name="Pedro",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    # Seed one global item so resolve_pool isn't empty.
    store.upsert(um.GlobalItem(
        event_uuid="evt-known", item_id="i-1", synced_at=FIXED_NOW,
        kind="photo", has_export=True,
        capture_time="2026-04-01T08:00:00",
        export_relpath="Exported Media/i1.jpg"))
    gw = LibraryGateway(user_store=store, now=_now)
    yield gw
    store.close()


# --------------------------------------------------------------------------- #
# Event scope — DC / Cut / Person
# --------------------------------------------------------------------------- #


def test_missing_dc_in_source_raises(gw):
    """The Recipe references a DC that's been deleted (or renamed) — the
    strict-ref guard raises before any resolution work runs."""
    with pytest.raises(RecipeResolutionError) as exc:
        gw.resolve_recipe({
            "source": [
                ["+", "exported"],
                ["-", {"kind": "dc", "tag": "ghost_dc"}],
            ],
            "otherwise": "skip",
        })
    assert exc.value.kind == "dc"
    assert exc.value.missing_operand == "ghost_dc"


def test_missing_cut_in_rule_predicate_raises(gw):
    """A rule predicate is walked the same way Source is."""
    with pytest.raises(RecipeResolutionError) as exc:
        gw.resolve_recipe({
            "source": [["+", "exported"]],
            "rules": [
                {
                    "predicate": [["+", {"kind": "cut", "tag": "ghost_cut"}]],
                    "verdict": "pick",
                }
            ],
            "otherwise": "skip",
        })
    assert exc.value.kind == "cut"
    assert exc.value.missing_operand == "ghost_cut"


def test_missing_person_in_filter_raises(gw):
    """``filters.person_ids`` is walked with its own existence check."""
    with pytest.raises(RecipeResolutionError) as exc:
        gw.resolve_recipe({
            "source": [["+", "exported"]],
            "filters": {"person_ids": ["ghost_person"]},
            "otherwise": "skip",
        })
    assert exc.value.kind == "person"
    assert exc.value.missing_operand == "ghost_person"


def test_existing_dc_and_cut_resolve_normally(gw):
    """The negative tests above need a positive control — the fixture's
    real DC + real Cut both resolve without raising."""
    result = gw.resolve_recipe({
        "source": [
            ["+", {"kind": "dc", "tag": "real_dc"}],
            ["-", {"kind": "cut", "tag": "real_cut"}],
        ],
        "otherwise": "skip",
    })
    # real_dc resolves to {p1}; real_cut already includes p1, so the
    # difference is empty. The point isn't the numbers — it's that no
    # exception fires.
    assert result.pool == []


# --------------------------------------------------------------------------- #
# Cross-event — Event Collection + Event
# --------------------------------------------------------------------------- #


def test_missing_event_collection_in_scope_raises(lib):
    """Scope expressions admit ``event_collection`` operands. A missing one
    raises ``RecipeResolutionError(kind='event_collection')``."""
    with pytest.raises(RecipeResolutionError) as exc:
        lib.resolve_recipe({
            "scope": [
                ["+", {"kind": "event_collection", "tag": "ghost_ec"}],
            ],
            "source": [["+", "exported"]],
            "otherwise": "skip",
        })
    assert exc.value.kind == "event_collection"
    assert exc.value.missing_operand == "ghost_ec"


def test_missing_event_in_scope_raises(lib):
    """An ``event`` operand pointing at a non-registered uuid raises."""
    with pytest.raises(RecipeResolutionError) as exc:
        lib.resolve_recipe({
            "scope": [
                ["+", {"kind": "event", "uuid": "evt-ghost"}],
            ],
            "source": [["+", "exported"]],
            "otherwise": "skip",
        })
    assert exc.value.kind == "event"
    assert exc.value.missing_operand == "evt-ghost"


def test_existing_event_and_event_collection_pass(lib):
    """Positive control — both names in the fixture resolve without raising."""
    result = lib.resolve_recipe({
        "scope": [
            ["+", {"kind": "event", "uuid": "evt-known"}],
            ["+", {"kind": "event_collection", "tag": "real_ec"}],
        ],
        "source": [["+", "exported"]],
        "otherwise": "skip",
    })
    # The Source resolves to one cross-event key (the seeded global item).
    assert len(result.pool) == 1


def test_missing_xdc_in_source_raises(lib):
    """Cross-event DC reference (``saved_filter``) follows the same rule."""
    with pytest.raises(RecipeResolutionError) as exc:
        lib.resolve_recipe({
            "source": [
                ["+", "exported"],
                ["-", {"kind": "dc", "tag": "ghost_xdc"}],
            ],
            "otherwise": "skip",
        })
    assert exc.value.kind == "dc"
    assert exc.value.missing_operand == "ghost_xdc"


def test_missing_person_in_cross_event_filter_raises(lib):
    """Cross-event Person filter uses the library-level ``person`` table for
    the existence gate (spec/90 §5.2)."""
    with pytest.raises(RecipeResolutionError) as exc:
        lib.resolve_recipe({
            "source": [["+", "exported"]],
            "filters": {"person_ids": ["ghost_person"]},
            "otherwise": "skip",
        })
    assert exc.value.kind == "person"
    assert exc.value.missing_operand == "ghost_person"


def test_known_person_resolves_to_lenient_empty_pool(lib):
    """A catalogued Person with no face detections resolves leniently to
    empty (spec/90 §4.3 — the Phase 1 face substrate ships empty; an
    empty face table is NOT an error). The pool comes back empty because
    the intersection with a no-detection Person produces nothing — but no
    exception fires."""
    result = lib.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"person_ids": ["person-1"]},
        "otherwise": "skip",
    })
    assert result.pool == []
    assert result.seed == {}
