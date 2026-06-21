"""spec/94 Phase 3 — Rules/Otherwise seed plumbing.

The Cut session's initial Pick/Skip verdicts come from two sources:

1. The pin_mode default (legacy three modes — keep-all/weed-out → all in,
   pick-in → all out).
2. The Recipe's Rules/Otherwise verdicts (computed by
   :func:`core.recipe_resolver.resolve_recipe`), shipped on the draft as
   :attr:`CutDraft.seed`. When present this OVERLAYS the pin_mode default
   so a rule-based Recipe opens the picker pre-curated.

These tests pin the contract end-to-end: the Cut draft carries the seed;
the session's :meth:`__post_init__` applies it; the from_draft path
derives the seed for a rule-based draft when the caller forgot to set
it; legacy pin_mode drafts still work unchanged.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_draft import (
    CutDraft,
    CutDraftRule,
    OTHERWISE_PICK,
    OTHERWISE_SKIP,
    PIN_PICK_IN,
    PIN_RULE_BASED,
    PIN_WEED_OUT,
)
from mira.shared.cut_session import CutSession
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now


# ── fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


def _draft(**over) -> CutDraft:
    """The cuts fixture exports {e1,e2,e3a,e3b,v1}; the legacy ``short_version``
    cut frozen-holds e1, so the session pool is {e2,e3a,e3b,v1}."""
    kw = dict(
        name="seed_test", tag="seed_test",
        expr=(("+", "exported"),
              ("-", {"kind": "cut", "tag": "short_version"})),
        styles=(), media_type="both",
        pin_mode=PIN_WEED_OUT,
        target_s=600, max_s=720, photo_s=6.0,
        music_category=None,
    )
    kw.update(over)
    return CutDraft(**kw)


# ── seed plumbing in __post_init__ ───────────────────────────────


def test_no_seed_falls_back_to_pin_mode_default(gw):
    """An empty seed → legacy three-mode behaviour. weed-out starts all-in,
    pick-in starts all-out."""
    s = CutSession.from_draft(gw, _draft(pin_mode=PIN_WEED_OUT))
    assert s.picked_count() == 4                      # all in (weed-out)
    s = CutSession.from_draft(gw, _draft(pin_mode=PIN_PICK_IN))
    assert s.picked_count() == 0                      # all out (pick-in)


def test_explicit_seed_overlays_pin_mode_default(gw):
    """A draft that ships a seed dict overrides the pin_mode default
    per-file. Files NOT in the seed keep the pin_mode default."""
    # pin-in default is all-out; the seed picks e3a, leaves the others.
    draft = _draft(
        pin_mode=PIN_PICK_IN,
        seed=(("Exported Media/e3a.jpg", True),
              ("Exported Media/v1.mp4", True)),
    )
    s = CutSession.from_draft(gw, draft)
    assert s.is_picked("Exported Media/e3a.jpg") is True
    assert s.is_picked("Exported Media/v1.mp4") is True
    # Files absent from the seed fall back to pin-in's all-out default.
    assert s.is_picked("Exported Media/e2.jpg") is False
    assert s.is_picked("Exported Media/e3b.jpg") is False


def test_seed_false_overrides_weed_out_default(gw):
    """A False entry in the seed skips a file even though the pin_mode
    default would have picked it. This is the spec/90 §1.3 "skip" rule
    verdict making the file open as skipped."""
    draft = _draft(
        pin_mode=PIN_WEED_OUT,
        seed=(("Exported Media/e2.jpg", False),),
    )
    s = CutSession.from_draft(gw, draft)
    assert s.is_picked("Exported Media/e2.jpg") is False
    # Others keep weed-out's all-in default.
    assert s.is_picked("Exported Media/e3a.jpg") is True
    assert s.is_picked("Exported Media/v1.mp4") is True


def test_seed_entries_outside_pool_are_ignored(gw):
    """A seed key not in the resolved pool doesn't crash + doesn't smuggle
    a file in. Defensive contract — the dialog computes the seed from the
    same resolver call, but a race or stale composition shouldn't break."""
    draft = _draft(
        seed=(("Exported Media/never-exported.jpg", True),
              ("Exported Media/e3a.jpg", True)),
    )
    s = CutSession.from_draft(gw, draft)
    # Pool stays the four legitimate members.
    assert {f.export_relpath for f in s.files} == {
        "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    }
    # The real seed entry took effect.
    assert s.is_picked("Exported Media/e3a.jpg") is True


# ── from_draft auto-derives seed for rule-based drafts ───────────


def test_from_draft_derives_seed_for_rule_based_draft(gw):
    """A rule-based draft WITHOUT an explicit seed (legacy / test caller
    that didn't go through the dialog) auto-derives one via
    ``gateway.resolve_recipe``. The defensive path — production goes
    through the dialog which always supplies the seed."""
    # Rule: "if it's in the short_version cut → pick" / otherwise skip.
    # short_version holds e1, which is NOT in the session pool (pool is
    # #exported − short_version). So the seed marks every pool member as
    # NOT picked (none match the rule).
    rule = CutDraftRule(
        predicate=(("+", {"kind": "cut", "tag": "short_version"}),),
        verdict=OTHERWISE_PICK,
    )
    draft = _draft(
        pin_mode=PIN_RULE_BASED,
        rules=(rule,),
        otherwise=OTHERWISE_SKIP,
        seed=(),                                    # no explicit seed
    )
    s = CutSession.from_draft(gw, draft)
    # No pool member matches the rule (short_version is e1, excluded by
    # the source formula), so they all fall to otherwise=skip.
    assert s.picked_count() == 0


def test_from_draft_keeps_explicit_seed_for_rule_based(gw):
    """When the draft ships a seed AND is rule-based, the explicit seed
    wins (no re-derivation). The dialog path takes this branch in
    production."""
    rule = CutDraftRule(
        predicate=(("+", {"kind": "cut", "tag": "short_version"}),),
        verdict=OTHERWISE_PICK,
    )
    draft = _draft(
        pin_mode=PIN_RULE_BASED,
        rules=(rule,),
        otherwise=OTHERWISE_SKIP,
        # Explicit seed picks e2 — disagrees with the rule (which would
        # pick nothing). The explicit seed wins.
        seed=(("Exported Media/e2.jpg", True),),
    )
    s = CutSession.from_draft(gw, draft)
    assert s.is_picked("Exported Media/e2.jpg") is True


def test_from_draft_no_rules_no_derivation(gw):
    """A draft without rules but flagged rule-based (degenerate) doesn't
    call resolve_recipe — saves the gateway round-trip."""
    draft = _draft(
        pin_mode=PIN_RULE_BASED,
        rules=(),                                   # rule-based but empty
        otherwise=OTHERWISE_SKIP,
    )
    s = CutSession.from_draft(gw, draft)
    # No seed → pin-mode-default. PIN_RULE_BASED isn't in the keep-all/
    # weed-out set, so it falls to all-out (the conservative default).
    assert s.picked_count() == 0


# ── Save-as-Recipe round trip drops the seed ─────────────────────


def test_save_as_recipe_round_trip_drops_seed():
    """The seed is a runtime artefact of one pin session, not part of the
    saved Recipe's identity. The Recipe-shaped composition produced by
    :func:`cut_draft_to_recipe_composition` carries source/filters/rules/
    otherwise/presentation only — no seed."""
    from mira.shared.recipe_draft_adapter import cut_draft_to_recipe_composition
    draft = _draft(
        pin_mode=PIN_RULE_BASED,
        rules=(CutDraftRule(
            predicate=(("+", "exported"),), verdict=OTHERWISE_PICK,
        ),),
        otherwise=OTHERWISE_SKIP,
        seed=(("Exported Media/e2.jpg", True),),
    )
    composition = cut_draft_to_recipe_composition(draft)
    # The composition's keys don't include seed; the saved Recipe will
    # re-derive at next instantiation against the live data.
    assert "seed" not in composition
    assert "rules" in composition
    assert composition["otherwise"] == OTHERWISE_SKIP


# ── Commit semantics unchanged ──────────────────────────────────


def test_seed_doesnt_break_commit(gw):
    """A seeded session commits the same way a legacy session does."""
    draft = _draft(
        pin_mode=PIN_PICK_IN,
        seed=(("Exported Media/e3a.jpg", True),
              ("Exported Media/v1.mp4", True)),
    )
    s = CutSession.from_draft(gw, draft)
    cut = s.commit(gw)
    rels = [ln.export_relpath for ln in gw.cut_member_files(cut.id)]
    assert rels == ["Exported Media/e3a.jpg", "Exported Media/v1.mp4"]
