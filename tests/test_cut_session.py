"""spec/81 §4 / spec/61 §2 — the pin session ledger (DC → Cut).

The SEPARATE ledger: decisions per exported FILE, in memory, phase_state
never touched; nothing persists until commit. The session sources its
candidate set from a DC resolution. Runs over the same real event.db fixture
as the cuts gateway tests.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_draft import CutDraft, PIN_KEEP_ALL, PIN_PICK_IN, PIN_WEED_OUT
from mira.shared.cut_session import CutSession, session_files
from mira.store import models as m
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now

# #exported − the frozen short_version cut → e2, e3a, e3b, v1.
EXPR = [["+", "exported"], ["-", {"kind": "cut", "tag": "short_version"}]]


def _draft(**over) -> CutDraft:
    kw = dict(
        name="Pássaros 2026", tag="passaros_2026",
        expr=tuple(tuple(t) for t in EXPR),
        styles=(), media_type="both",
        pin_mode=PIN_PICK_IN, target_s=600, max_s=720, photo_s=6.0,
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
# session_files — the DC resolved to cells
# --------------------------------------------------------------------------- #


def test_session_files_join_source_facts(gw):
    files = session_files(gw, EXPR)
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
# The ledger — pin modes, toggle, undo; phase_state untouched
# --------------------------------------------------------------------------- #


def test_pick_in_starts_empty(gw):
    s = CutSession.from_draft(gw, _draft())            # pick-in
    assert s.picked_count() == 0
    assert s.zone() == "green"          # nothing picked, under target


def test_weed_out_starts_full(gw):
    s = CutSession.from_draft(gw, _draft(pin_mode=PIN_WEED_OUT))
    assert s.picked_count() == 4
    assert [f.export_relpath for f in s.picked_files()][0] == "Exported Media/e2.jpg"


def test_keep_all_starts_full_and_flags(gw):
    s = CutSession.from_draft(gw, _draft(pin_mode=PIN_KEEP_ALL))
    assert s.keep_all is True
    assert s.picked_count() == 4        # pinned 1:1, everything in


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
    s = CutSession.from_draft(gw, _draft(pin_mode=PIN_WEED_OUT))
    s.toggle("Exported Media/e2.jpg")
    s.commit(gw)
    after = gw.store.conn.execute(
        "SELECT COUNT(*) AS n FROM phase_state").fetchone()["n"]
    assert after == before


# --------------------------------------------------------------------------- #
# Live budget — minutes are the truth
# --------------------------------------------------------------------------- #


def test_totals_and_zone_follow_picks(gw):
    s = CutSession.from_draft(gw, _draft(target_s=60, max_s=90))
    s.set_state("Exported Media/e2.jpg", True)      # day 1 photo
    s.set_state("Exported Media/e3a.jpg", True)     # day 2 photo
    t = s.totals()
    assert (t.photo_count, t.video_count, t.separator_count) == (2, 0, 2)
    # spec/152 §3 — (2 photos + 2 separators + 1 opener) × 6 s = 30 s.
    # Green under the 60 s target.
    assert s.show_seconds() == 30.0 and s.zone() == "green"
    s.set_state("Exported Media/v1.mp4", True)      # +30 s of clip, no new day
    # 30 + 30 = 60 s — right at the target, still green.
    assert s.show_seconds() == 60.0
    assert s.zone() == "green"
    # Bump the target down so the same picked set lands amber.
    s.target_s = 48
    assert s.zone() == "amber"


def test_separators_off_zeroes_the_cards(gw):
    s = CutSession.from_draft(gw, _draft())
    s.separators_on = False
    s.set_state("Exported Media/e2.jpg", True)
    assert s.totals().separator_count == 0
    # spec/152 §3 — no separators → no opener either. 1 photo × 6 s = 6 s.
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
# Commit — the one persistence moment (freeze the snapshot)
# --------------------------------------------------------------------------- #


def test_commit_creates_cut_with_membership_and_snapshot(gw):
    s = CutSession.from_draft(gw, _draft())
    s.set_state("Exported Media/e3b.jpg", True)
    s.set_state("Exported Media/v1.mp4", True)
    cut = s.commit(gw)
    assert cut.tag == "passaros_2026"
    assert cut.music_category == "happy" and cut.target_s == 600
    # The Cut freezes the formula it pinned from (spec/81 §5).
    assert gw.cut_expr_snapshot(cut) == [
        ["+", "exported"], ["-", {"kind": "cut", "tag": "short_version"}]]
    rels = [ln.export_relpath for ln in gw.cut_member_files(cut.id)]
    assert rels == ["Exported Media/e3b.jpg", "Exported Media/v1.mp4"]


def test_abandoned_session_leaves_nothing(gw):
    before = [c.tag for c in gw.cuts()]
    s = CutSession.from_draft(gw, _draft())
    s.set_state("Exported Media/e2.jpg", True)
    del s                                          # walk away — no commit
    assert [c.tag for c in gw.cuts()] == before


def test_from_saved_dc_resolves_and_freezes(gw):
    # A draft that names a SAVED DC (no inline expr) resolves through it.
    dc = gw.create_dc("Birds",
                      expr=[["+", "exported"],
                            ["-", {"kind": "cut", "tag": "short_version"}]])
    s = CutSession.from_draft(
        gw, _draft(source_dc_id=dc.id, expr=(), pin_mode=PIN_WEED_OUT))
    assert s.picked_count() == 4
    cut = s.commit(gw)
    assert gw.cut(cut.id).source_dc_id == dc.id
    # editing the DC after pin does NOT change the frozen Cut (freeze invariant)
    gw.update_dc(dc.id, expr=[["+", "exported"]])
    rels = [ln.export_relpath for ln in gw.cut_member_files(cut.id)]
    assert rels == ["Exported Media/e2.jpg", "Exported Media/e3a.jpg",
                    "Exported Media/e3b.jpg", "Exported Media/v1.mp4"]


def test_for_cut_with_draft_edits_settings_name_and_picks(gw):
    """The dialog-first Adjust: a new DC formula through the dialog —
    rename + setting changes + membership in one commit."""
    first = CutSession.from_draft(gw, _draft())
    first.set_state("Exported Media/e2.jpg", True)
    cut = first.commit(gw)

    new_draft = _draft(
        name="Pássaros v2", tag="passaros_v2",
        target_s=300, card_style="multi",
        expr=(("+", "exported"),))             # formula widened
    again = CutSession.for_cut_with_draft(gw, cut, new_draft)
    assert again.cut_id == cut.id
    assert again.is_picked("Exported Media/e2.jpg")     # membership seeded
    assert any(f.export_relpath == "Exported Media/e1.jpg"
               for f in again.files)                  # widened formula visible
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
    # still exactly two cuts — re-entry never duplicates
    assert len(gw.cuts()) == 2
