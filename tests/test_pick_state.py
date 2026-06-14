"""Tests for core.cull_state — the 3-state cull model (E1).

Pure-logic, no Qt. Operates on a plain journal dict (same shape as
the ingest_session / cull_session D1 journal).
"""

from __future__ import annotations

import pytest

from core.cull_state import (
    STATE_CANDIDATE,
    STATE_DISCARDED as STATE_SKIPPED,
    STATE_KEPT as STATE_PICKED,
    cycle_state,
    default_state,
    get_state,
    is_kept,
    kept_filenames,
    set_default_state,
    set_state,
    state_counts,
)


def _journal() -> dict:
    return {"marks": {}}


# ── Default state ────────────────────────────────────────────────


def test_default_is_discarded_for_fresh_journal():
    assert default_state(_journal()) == STATE_SKIPPED


def test_default_is_discarded_for_old_2state_journal_without_key():
    # An old D1 journal has no default_state key at all.
    old = {"marks": {"a.RW2": "picked"}}
    assert default_state(old) == STATE_SKIPPED


def test_set_default_state_to_kept_for_brackets():
    j = _journal()
    set_default_state(j, STATE_PICKED)
    assert default_state(j) == STATE_PICKED


def test_set_default_state_rejects_unknown():
    with pytest.raises(ValueError):
        set_default_state(_journal(), "bogus")


def test_default_state_recovers_from_corrupt_value():
    assert default_state({"default_state": "garbage"}) == STATE_SKIPPED


# ── get / set state ──────────────────────────────────────────────


def test_unmarked_file_resolves_to_default_discarded():
    assert get_state(_journal(), "x.RW2") == STATE_SKIPPED


def test_unmarked_file_resolves_to_default_kept_for_brackets():
    j = _journal()
    set_default_state(j, STATE_PICKED)
    assert get_state(j, "x.RW2") == STATE_PICKED


def test_set_state_candidate_then_read_back():
    j = _journal()
    set_state(j, "x.RW2", STATE_CANDIDATE)
    assert get_state(j, "x.RW2") == STATE_CANDIDATE


def test_set_state_rejects_unknown():
    with pytest.raises(ValueError):
        set_state(_journal(), "x.RW2", "bogus")


def test_old_2state_kept_entry_reads_as_kept():
    """Back-compat: an old journal with {f: 'picked'} reads kept under
    the 3-state model with zero migration."""
    old = {"marks": {"a.RW2": "picked"}}
    assert get_state(old, "a.RW2") == STATE_PICKED
    assert is_kept(old, "a.RW2") is True


def test_corrupt_explicit_mark_degrades_to_default():
    j = {"marks": {"a.RW2": "weird"}}
    assert get_state(j, "a.RW2") == STATE_SKIPPED  # falls back


# ── Sparse storage invariant ─────────────────────────────────────


def test_setting_to_default_keeps_journal_sparse_discarded():
    j = _journal()  # default discarded
    set_state(j, "a.RW2", STATE_PICKED)
    assert j["marks"] == {"a.RW2": STATE_PICKED}
    set_state(j, "a.RW2", STATE_SKIPPED)  # == default → entry dropped
    assert j["marks"] == {}


def test_sparse_storage_under_kept_default_brackets():
    """Bracket bucket: default kept. Demoting a frame to discarded
    DIFFERS from default → stored explicitly. Restoring to kept ==
    default → entry dropped."""
    j = _journal()
    set_default_state(j, STATE_PICKED)
    # Un-marked frame is kept by default, journal empty
    assert get_state(j, "f1.RW2") == STATE_PICKED
    assert j["marks"] == {}
    # Demote a bad frame
    set_state(j, "f1.RW2", STATE_SKIPPED)
    assert j["marks"] == {"f1.RW2": STATE_SKIPPED}
    assert get_state(j, "f1.RW2") == STATE_SKIPPED
    # Restore → back to default kept → entry dropped
    set_state(j, "f1.RW2", STATE_PICKED)
    assert j["marks"] == {}
    assert get_state(j, "f1.RW2") == STATE_PICKED


def test_set_state_initialises_marks_when_missing_or_bad():
    j = {}  # no marks key at all
    set_state(j, "a.RW2", STATE_CANDIDATE)
    assert j["marks"] == {"a.RW2": STATE_CANDIDATE}
    j2 = {"marks": "not a dict"}
    set_state(j2, "b.RW2", STATE_PICKED)
    assert j2["marks"] == {"b.RW2": STATE_PICKED}


# ── Cycle ────────────────────────────────────────────────────────


def test_cycle_advances_discarded_kept_candidate_wrap():
    """Click-saver order (Nelson 2026-05-18): discard → KEEP →
    compare → (wrap) discard — Keep is one key from the discard
    default, not two."""
    j = _journal()
    assert cycle_state(j, "x.RW2") == STATE_PICKED
    assert cycle_state(j, "x.RW2") == STATE_CANDIDATE
    assert cycle_state(j, "x.RW2") == STATE_SKIPPED  # wrap
    assert cycle_state(j, "x.RW2") == STATE_PICKED


def test_cycle_from_kept_default_bracket_follows_new_order():
    """Starting state is kept (bracket default). The cycle key now
    advances kept → compare → discard → kept (the single-key *demote*
    of a bad bracket frame is the explicit Discard/Remove key, not
    the cycle wrap)."""
    j = _journal()
    set_default_state(j, STATE_PICKED)
    assert get_state(j, "f.RW2") == STATE_PICKED
    assert cycle_state(j, "f.RW2") == STATE_CANDIDATE
    assert cycle_state(j, "f.RW2") == STATE_SKIPPED
    assert cycle_state(j, "f.RW2") == STATE_PICKED


# ── Counts + kept extraction ─────────────────────────────────────


def test_state_counts_full_population_default_discarded():
    j = _journal()
    set_state(j, "a", STATE_PICKED)
    set_state(j, "b", STATE_CANDIDATE)
    # c, d untouched → discarded
    counts = state_counts(j, ["a", "b", "c", "d"])
    assert counts == {
        STATE_SKIPPED: 2,
        STATE_CANDIDATE: 1,
        STATE_PICKED: 1,
    }


def test_state_counts_full_population_default_kept():
    """The reason state_counts needs the universe: with default kept,
    absence ≠ discarded."""
    j = _journal()
    set_default_state(j, STATE_PICKED)
    set_state(j, "bad", STATE_SKIPPED)
    counts = state_counts(j, ["f1", "f2", "f3", "bad"])
    assert counts == {
        STATE_SKIPPED: 1,
        STATE_CANDIDATE: 0,
        STATE_PICKED: 3,
    }


def test_kept_filenames_preserves_order_and_filters():
    j = _journal()
    set_state(j, "b", STATE_PICKED)
    set_state(j, "d", STATE_PICKED)
    out = kept_filenames(j, ["a", "b", "c", "d", "e"])
    assert out == ["b", "d"]


def test_kept_filenames_under_kept_default_includes_untouched():
    j = _journal()
    set_default_state(j, STATE_PICKED)
    set_state(j, "bad", STATE_SKIPPED)
    out = kept_filenames(j, ["f1", "bad", "f2"])
    assert out == ["f1", "f2"]


def test_is_kept_matches_old_signature():
    j = _journal()
    set_state(j, "a", STATE_PICKED)
    assert is_kept(j, "a") is True
    assert is_kept(j, "b") is False


# ── Per-bucket reviewed flag + marks presence (docs/18) ──────────


def test_bucket_reviewed_is_user_declared_and_reversible():
    from core.cull_state import is_bucket_reviewed, set_bucket_reviewed
    j = _journal()
    assert is_bucket_reviewed(j) is False          # absent ⇒ not done
    set_bucket_reviewed(j, True)
    assert is_bucket_reviewed(j) is True
    set_bucket_reviewed(j, False)                  # reversible
    assert is_bucket_reviewed(j) is False
    assert "reviewed" not in j                     # kept sparse
    # Tolerant of a hand-edited journal — only an exact True counts.
    assert is_bucket_reviewed({"reviewed": "yes"}) is False


def test_bucket_browsed_is_sparse_and_reversible():
    from core.cull_state import is_bucket_browsed, set_bucket_browsed
    j = _journal()
    assert is_bucket_browsed(j) is False           # absent ⇒ not opened
    set_bucket_browsed(j, True)
    assert is_bucket_browsed(j) is True
    set_bucket_browsed(j, False)                   # reversible
    assert is_bucket_browsed(j) is False
    assert "browsed" not in j                      # kept sparse
    assert is_bucket_browsed({"browsed": 1}) is False  # only exact True


def test_bucket_content_key_is_stable_and_membership_sensitive():
    from core.cull_state import bucket_content_key as k
    # Order-independent, dedup, stable across calls.
    assert k(["b.rw2", "a.rw2"]) == k(["a.rw2", "a.rw2", "b.rw2"])
    # Membership change → different scope (intended).
    assert k(["a.rw2", "b.rw2"]) != k(["a.rw2", "c.rw2"])
    assert len(k(["a.rw2"])) == 16


def test_shared_journal_per_bucket_soft_state_isolated():
    """A SHARED day journal: per-file marks are flat, but
    browsed/reviewed are per-bucket (content-keyed) and isolated —
    opening Moment A must NOT flip Moment B's badge."""
    from core.cull_state import (
        bucket_content_key, has_explicit_marks, is_bucket_browsed,
        is_bucket_reviewed, set_bucket_browsed, set_bucket_reviewed,
        set_state,
    )
    j = _journal()
    a = bucket_content_key(["P1.rw2", "P2.rw2"])
    b = bucket_content_key(["P9.rw2"])
    set_bucket_browsed(j, True, a)
    assert is_bucket_browsed(j, a) is True
    assert is_bucket_browsed(j, b) is False          # isolated
    assert is_bucket_browsed(j) is False             # not the global flag
    set_bucket_reviewed(j, True, b)
    assert is_bucket_reviewed(j, b) is True
    assert is_bucket_reviewed(j, a) is False
    # Sparse + reversible: clearing drops the sub-map entirely.
    set_bucket_browsed(j, False, a)
    set_bucket_reviewed(j, False, b)
    assert "buckets" not in j
    # Scoped has_explicit_marks: a mark on P1 makes bucket A
    # in-progress but not bucket B (different files).
    set_state(j, "P1.rw2", STATE_PICKED)
    assert has_explicit_marks(j, ["P1.rw2", "P2.rw2"]) is True
    assert has_explicit_marks(j, ["P9.rw2"]) is False
    assert has_explicit_marks(j) is True             # legacy: any mark


def test_has_explicit_marks_tracks_sparse_storage():
    from core.cull_state import has_explicit_marks
    j = _journal()
    assert has_explicit_marks(j) is False
    set_state(j, "a", STATE_PICKED)                  # non-default → stored
    assert has_explicit_marks(j) is True
    # Sparse: setting back to the default drops the entry → no marks.
    set_state(j, "a", STATE_SKIPPED)             # == default
    assert has_explicit_marks(j) is False
    assert has_explicit_marks({}) is False         # never raises
