"""Regression for the Thumb._STATE_KEY → palette lookup.

Nelson 2026-06-18 — clicking a thumb's border in Pick mode cycles the
state Pick → Skip → Candidate → Pick (DaysGridPage._apply_verb_at_index
calls Thumb.setState with the raw phase_state value, not the visual
colour name). The persisted state ``'candidate'`` wasn't in
``_STATE_KEY``, so the next paint raised ``KeyError: 'candidate'`` and
crashed the app.

This test pins both keys in the map so a future "tidy duplicate" pass
can't quietly drop the alias and reintroduce the crash.
"""
from __future__ import annotations

from mira.picked.status import STATE_CANDIDATE, STATE_PICKED, STATE_SKIPPED
from mira.ui.design.thumbs import _STATE_KEY


def test_state_key_accepts_every_phase_state_value():
    """``Thumb.setState`` is called from at least one path (Pick cycle in
    DaysGridPage) with the raw phase_state value. ``_STATE_KEY`` MUST
    accept every persisted state, plus the visual-colour synonyms the
    cluster aggregator emits (``"compare"`` / ``"mixed"``) and the
    no-decision sentinel ``None``."""
    for s in (STATE_PICKED, STATE_SKIPPED, STATE_CANDIDATE):
        assert s in _STATE_KEY, f"missing phase_state {s!r} in _STATE_KEY"
    # Visual-colour synonyms used by the cluster aggregator and the
    # cell_color_for_item mapping.
    for s in ("compare", "mixed", None):
        assert s in _STATE_KEY, f"missing visual key {s!r} in _STATE_KEY"


def test_state_key_maps_candidate_to_compare_colour():
    """The raw ``'candidate'`` phase value must paint with the same
    colour as the ``'compare'`` cell-colour synonym (orange — see
    ``mira.picked.status.CellColor.COMPARE``). If a future refactor
    splits them this assertion catches the regression at paint time."""
    assert _STATE_KEY["candidate"] == _STATE_KEY["compare"]
