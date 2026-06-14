"""spec/61 slice 5 (model half) — the Cut picking session ledger.

The SEPARATE ledger: decisions per exported FILE, in memory, phase_state
never touched; nothing persists until commit. Runs over the same real
event.db fixture as the cuts gateway tests.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import CutSession, session_files
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.shared.new_cut_dialog import CutDraft

from tests.test_gateway_cuts import _doc, _now

POOL = [("+", "exported"), ("-", "short_version")]   # e2, e3a, e3b, v1


def _draft(**over) -> CutDraft:
    kw = dict(
        name="Pássaros 2026", tag="passaros_2026",
        pool_expr=tuple(POOL), style_filter=(), type_filter="both",
        default_state="skipped", target_s=600, max_s=720, photo_s=6.0,
        music_category="happy",
    )
    kw.update(over)
    return CutDraft(**kw)


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


# --------------------------------------------------------------------------- #
# session_files — the pool resolved to cells
# --------------------------------------------------------------------------- #


def test_session_files_join_source_facts(gw):
    files = session_files(gw, POOL)
    assert [f.export_relpath for f in files] == [
        "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4"]
    by_rel = {f.export_relpath: f for f in files}
    assert by_rel["Exported Media/e2.jpg"].kind == "photo"
    assert by_rel["Exported Media/e2.jpg"].day_number == 1
    v = by_rel["Exported Media/v1.mp4"]
    assert v.kind == "video" and v.duration_ms == 30_000 and v.day_number == 2
    # two versions of p3 = two distinct cells sharing the source item
    assert by_rel["Exported Media/e3a.jpg"].source_item_id == "p3"
    assert by_rel["Exported Media/e3b.jpg"].source_item_id == "p3"


# --------------------------------------------------------------------------- #
# The ledger — defaults, toggle, undo; phase_state untouched
# --------------------------------------------------------------------------- #


def test_default_skipped_starts_empty(gw):
    s = CutSession.from_draft(gw, _draft())
    assert s.picked_count() == 0
    assert s.zone() == "green"          # nothing picked, under target


def test_default_picked_starts_full(gw):
    s = CutSession.from_draft(gw, _draft(default_state="picked"))
    assert s.picked_count() == 4
    assert [f.export_relpath for f in s.picked_files()][0] == "Exported Media/e2.jpg"


def test_toggle_and_undo(gw):
    s = CutSession.from_draft(gw, _draft())
    assert s.toggle("Exported Media/e2.jpg") is True
    s.set_state("Exported Media/v1.mp4", True)
    assert s.picked_count() == 2
    assert s.undo() == "Exported Media/v1.mp4"
    assert s.picked_count() == 1
    assert s.undo() == "Exported Media/e2.jpg"
    assert s.picked_count() == 0
    assert s.undo() is None
    # unknown relpath is a no-op, not a crash
    s.set_state("nope.jpg", True)
    assert s.picked_count() == 0


def test_session_never_touches_phase_state(gw):
    before = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM phase_state").fetchone()["n"]
    s = CutSession.from_draft(gw, _draft(default_state="picked"))
    s.toggle("Exported Media/e2.jpg")
    s.commit(gw)
    after = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM phase_state").fetchone()["n"]
    assert after == before


# --------------------------------------------------------------------------- #
# Live budget — minutes are the truth
# --------------------------------------------------------------------------- #


def test_totals_and_zone_follow_picks(gw):
    s = CutSession.from_draft(gw, _draft(target_s=48, max_s=90))
    s.set_state("Exported Media/e2.jpg", True)      # day 1 photo
    s.set_state("Exported Media/e3a.jpg", True)     # day 2 photo
    t = s.totals()
    assert (t.photo_count, t.video_count, t.separator_count) == (2, 0, 2)
    # (2 photos + 2 separators) × 6 s = 24 s → green under the 48 s target
    assert s.show_seconds() == 24.0 and s.zone() == "green"
    s.set_state("Exported Media/v1.mp4", True)      # +30 s of clip, no new day
    assert s.show_seconds() == 54.0
    assert s.zone() == "amber"                    # past target, under max


def test_separators_off_zeroes_the_cards(gw):
    s = CutSession.from_draft(gw, _draft())
    s.separators_on = False
    s.set_state("Exported Media/e2.jpg", True)
    assert s.totals().separator_count == 0
    assert s.show_seconds() == 6.0


def test_days_grouping_in_show_order(gw):
    s = CutSession.from_draft(gw, _draft())
    groups = s.days()
    assert [(d, [f.export_relpath for f in fs]) for d, fs in groups] == [
        (1, ["Exported Media/e2.jpg"]),
        (2, ["Exported Media/e3a.jpg", "Exported Media/e3b.jpg",
             "Exported Media/v1.mp4"]),
    ]


# --------------------------------------------------------------------------- #
# Commit — the one persistence moment
# --------------------------------------------------------------------------- #


def test_commit_creates_cut_with_membership(gw):
    s = CutSession.from_draft(gw, _draft())
    s.set_state("Exported Media/e3b.jpg", True)
    s.set_state("Exported Media/v1.mp4", True)
    cut = s.commit(gw)
    assert cut.tag == "passaros_2026"
    assert cut.music_category == "happy" and cut.target_s == 600
    assert gw.cut_pool_expr(cut) == [("+", "exported"), ("-", "short_version")]
    rels = [ln.export_relpath for ln in gw.cut_member_files(cut.id)]
    assert rels == ["Exported Media/e3b.jpg", "Exported Media/v1.mp4"]


def test_abandoned_session_leaves_nothing(gw):
    before = [c.tag for c in gw.cuts()]
    s = CutSession.from_draft(gw, _draft())
    s.set_state("Exported Media/e2.jpg", True)
    del s                                          # walk away — no commit
    assert [c.tag for c in gw.cuts()] == before


def test_for_cut_with_draft_edits_settings_name_and_picks(gw):
    """The dialog-first Adjust (Nelson round 3): a new recipe through
    the dialog — rename + setting changes + membership in one commit."""
    first = CutSession.from_draft(gw, _draft())
    first.set_state("Exported Media/e2.jpg", True)
    cut = first.commit(gw)

    new_draft = _draft(
        name="Pássaros v2", tag="passaros_v2",
        target_s=300, card_style="multi",
        pool_expr=(("+", "exported"),))        # pool widened
    again = CutSession.for_cut_with_draft(gw, cut, new_draft)
    assert again.cut_id == cut.id
    assert again.is_picked("Exported Media/e2.jpg")     # membership seeded
    assert any(f.export_relpath == "Exported Media/e1.jpg"
               for f in again.files)                  # widened pool visible
    again.set_state("Exported Media/v1.mp4", True)
    again.commit(gw)

    refreshed = gw.cut(cut.id)
    assert refreshed.tag == "passaros_v2"             # renamed via dialog
    assert refreshed.target_s == 300
    assert gw.cut_card_style(refreshed) == "multi"
    rels = [ln.export_relpath for ln in gw.cut_member_files(cut.id)]
    assert rels == ["Exported Media/e2.jpg", "Exported Media/v1.mp4"]


def test_reenter_seeds_from_membership_and_updates(gw):
    first = CutSession.from_draft(gw, _draft())
    first.set_state("Exported Media/e2.jpg", True)
    cut = first.commit(gw)

    again = CutSession.for_cut(gw, cut)
    assert again.cut_id == cut.id
    assert again.is_picked("Exported Media/e2.jpg")
    assert not again.is_picked("Exported Media/e3a.jpg")

    again.set_state("Exported Media/e2.jpg", False)
    again.set_state("Exported Media/e3a.jpg", True)
    again.target_s = 300
    again.commit(gw)

    refreshed = gw.cut(cut.id)
    assert refreshed.target_s == 300
    rels = [ln.export_relpath for ln in gw.cut_member_files(cut.id)]
    assert rels == ["Exported Media/e3a.jpg"]
    # still exactly one cut — re-entry never duplicates
    assert len(gw.cuts()) == 2                    # fixture's short_version + this one
