"""Three-state cull model — **Discard / Compare / Keep**.

Frozen design: `docs/18-culler-spec.md` §"Three-state model".

**Display vs. wire (Nelson 2026-05-16).** The user-facing names are
present-tense *actions* — **Discard**, **Compare**, **Keep** —
because nothing has actually been discarded or kept until commit;
the past tense ("discarded"/"kept") asserted a fact that wasn't true
yet. Only the words the user reads changed. The **serialized state
values are deliberately unchanged** — ``"discarded"`` /
``"candidate"`` / ``"kept"`` — they are the journal wire format, the
QSS ``[state="…"]`` keys, and the back-compat bridge to old 2-state
journals. Renaming the values would force a journal migration + a
lockstep QSS edit for zero user benefit; the display-vs-wire split
is the standard, low-risk discipline. The ``STATE_*`` constant
*names* likewise stay (wire-level identifiers).

This is the spine of the new culler. Every photo is in one of three
states (wire value — display word — colour — meaning):

* ``discarded`` — **Discard** — red — the default, never touched.
* ``candidate`` — **Compare** — orange — undecided; tie-break in
  the grid (the comparison tool — hence the name).
* ``kept``      — **Keep**    — green — chosen.

Storage is **sparse** and lives in the same ``journal["marks"]`` dict
the 2-state model used (``core.cull_session`` / ``core.ingest_session``
D1 journal), so old 2-state journals stay readable: an entry of
``"kept"`` means kept under both models; entries are only written when
the state **differs from the journal's default state**.

The default state is **data on the journal**, not materialised rows:
normal buckets default to ``discarded`` (absence = discarded, journal
stays tiny); Bracket buckets default to ``kept`` (the new culler page
sets ``default_state`` to ``kept`` for focus/exposure brackets per
the frozen spec — focus-stack frames are presumed wanted until the
user demotes the bad ones). ``get_state`` falls back to the default,
so no per-frame initialisation pass is needed and the journal never
bloats for a big bracket.

This module is pure-logic (no Qt, no I/O). It does **not** modify
``core.cull_session`` — the legacy 2-state page keeps working on the
old functions until it is retired (E11). The new culler imports the
3-state API from here (re-exported via ``core.ingest_session``).
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional


# Canonical state names. Stored verbatim in ``journal["marks"]``.
STATE_DISCARDED = "discarded"
STATE_CANDIDATE = "candidate"
STATE_KEPT = "kept"

# Cycle order: discard → KEEP → compare → (wrap) discard
# (red → green → orange). Click-saver (Nelson 2026-05-18, revises
# the original docs/18 red→orange→green): from the discard default
# the common next intent is Keep, not Compare — one key press lands
# it instead of two. Compare (the rarer "undecided, tie-break in
# the grid") sits after Keep; the wrap still demotes back to
# discard with a single key, no separate binding.
STATE_CYCLE: tuple[str, ...] = (
    STATE_DISCARDED,
    STATE_KEPT,
    STATE_CANDIDATE,
)

VALID_STATES: frozenset[str] = frozenset(STATE_CYCLE)

# Journal key holding the per-bucket default state. Absent → discarded
# (back-compatible with old journals + normal buckets).
_DEFAULT_STATE_KEY = "default_state"


def default_state(journal: dict) -> str:
    """The journal's default state for un-marked files.

    ``discarded`` unless the page set it (Bracket buckets → ``kept``).
    Malformed / unknown values fall back to ``discarded`` so a hand-
    edited journal can't poison the read path.
    """
    value = journal.get(_DEFAULT_STATE_KEY, STATE_DISCARDED)
    return value if value in VALID_STATES else STATE_DISCARDED


def set_default_state(journal: dict, state: str) -> None:
    """Set the per-bucket default. The new culler page calls this once
    on session init: ``kept`` for Bracket buckets, ``discarded``
    otherwise. Changing it does not rewrite existing explicit marks —
    only the fallback for un-marked files moves.

    Raises ``ValueError`` on an unknown state (programmer error, not
    user input — fail loud here)."""
    if state not in VALID_STATES:
        raise ValueError(f"unknown cull state: {state!r}")
    journal[_DEFAULT_STATE_KEY] = state


def get_state(journal: dict, filename: str) -> str:
    """Return the state of ``filename``.

    Explicit mark if present and valid; otherwise the journal's
    default state. An explicit-but-invalid value (corrupt journal)
    degrades to the default rather than raising.
    """
    marks = journal.get("marks")
    if not isinstance(marks, dict):     # hand-edited / garbled journal
        marks = {}                      # can't poison the read path
    raw = marks.get(filename)
    if raw in VALID_STATES:
        return raw
    return default_state(journal)


def set_state(journal: dict, filename: str, state: str) -> None:
    """Set ``filename`` to ``state``, keeping the journal sparse.

    If ``state`` equals the journal's default, the explicit entry is
    removed (absence == default); otherwise it is written. This keeps
    the journal minimal under any default and stays correct when the
    default is non-``discarded`` (a Bracket frame demoted to
    ``discarded`` differs from the ``kept`` default → stored
    explicitly; restored to ``kept`` → entry dropped).

    Raises ``ValueError`` on an unknown state.
    """
    if state not in VALID_STATES:
        raise ValueError(f"unknown cull state: {state!r}")
    marks = journal.get("marks")
    if not isinstance(marks, dict):
        marks = {}
        journal["marks"] = marks
    if state == default_state(journal):
        marks.pop(filename, None)
    else:
        marks[filename] = state


def cycle_state(journal: dict, filename: str) -> str:
    """Advance ``filename`` one step along the cycle and return the
    new state.

    ``discarded`` → ``candidate`` → ``kept`` → ``discarded`` (wrap).
    Drives the single Tab-like key + the equivalent button.
    """
    current = get_state(journal, filename)
    try:
        idx = STATE_CYCLE.index(current)
    except ValueError:  # pragma: no cover — get_state never returns this
        idx = 0
    nxt = STATE_CYCLE[(idx + 1) % len(STATE_CYCLE)]
    set_state(journal, filename, nxt)
    return nxt


def state_counts(
    journal: dict,
    all_filenames: Iterable[str],
) -> dict[str, int]:
    """Count files in each state across the full population.

    ``all_filenames`` is the universe (the page has it from the
    folder scan). Required because, with a non-``discarded`` default,
    absence does not imply ``discarded`` — counting needs the
    universe, not just the explicit marks. Returns a dict with all
    three state keys present (zeros included).
    """
    counts = {
        STATE_DISCARDED: 0,
        STATE_CANDIDATE: 0,
        STATE_KEPT: 0,
    }
    for name in all_filenames:
        counts[get_state(journal, name)] += 1
    return counts


def is_kept(journal: dict, filename: str) -> bool:
    """Convenience: True iff ``filename`` resolves to ``kept``.

    Matches the old ``cull_session.is_kept`` signature so the commit
    path (``commit_from_session`` filtering kept files) reads the
    same against a 3-state journal."""
    return get_state(journal, filename) == STATE_KEPT


def kept_filenames(
    journal: dict,
    all_filenames: Iterable[str],
) -> list[str]:
    """The subset of ``all_filenames`` resolving to ``kept``, order
    preserved. This is what the commit path materialises."""
    return [n for n in all_filenames if get_state(journal, n) == STATE_KEPT]


# ── Per-bucket "reviewed / done" flag (docs/18 §"Culling contexts")
#
# The resume-map badge's "done" is **user-declared and reversible**,
# never inferred (the sparse journal cannot tell "explicitly set to
# default" from "never touched", and inferring completion would
# contradict the frozen "the user decides, the system never infers"
# principle). One explicit journal key, the same shape as
# ``default_state``: absent ⇒ not done; the user toggles it and may
# un-declare it; revisiting the bucket never auto-clears it.
_REVIEWED_KEY = "reviewed"
_BROWSED_KEY = "browsed"
# Per-bucket soft-state sub-map for a SHARED journal (the frozen
# day-scoped journal — docs/18 "journal-scope" design). When several
# transient buckets (Moment clusters + the residual Individuals)
# share ONE day journal so per-file *marks* survive a re-cluster
# losslessly, their `browsed`/`reviewed` soft-state cannot be one
# global flag — it lives here, keyed by a CONTENT-STABLE bucket key.
_BUCKETS_KEY = "buckets"


def bucket_content_key(filenames: Iterable[str]) -> str:
    """Content-stable id for a transient bucket whose marks live in
    a SHARED journal (e.g. a Moment cluster). Stable across scans for
    the same file set; it changes only when the grouping's
    membership changes — a different grouping is a different
    soft-state scope, while the per-file *marks* are untouched
    (that is what makes 're-cluster is lossless' true). docs/18
    journal-scope freeze, 2026-05-18."""
    names = sorted({str(n) for n in filenames})
    return hashlib.sha1(
        "\n".join(names).encode("utf-8")
    ).hexdigest()[:16]


def _soft_get(journal: dict, key: Optional[str], flag: str) -> bool:
    """``key=None`` → the legacy global flag (own-journal buckets:
    brackets / bursts / video). A key → the per-bucket sub-map (the
    shared day journal). Only an exact ``True`` counts."""
    if key is None:
        return journal.get(flag) is True
    sub = journal.get(_BUCKETS_KEY)
    return (isinstance(sub, dict)
            and isinstance(sub.get(key), dict)
            and sub[key].get(flag) is True)


def _soft_set(journal: dict, key: Optional[str], flag: str,
               value: bool) -> None:
    """Sparse + reversible: clears drop the key/entry/sub-map."""
    if key is None:
        if value:
            journal[flag] = True
        else:
            journal.pop(flag, None)
        return
    sub = journal.get(_BUCKETS_KEY)
    if value:
        if not isinstance(sub, dict):
            sub = {}
            journal[_BUCKETS_KEY] = sub
        sub.setdefault(key, {})[flag] = True
        return
    if isinstance(sub, dict) and isinstance(sub.get(key), dict):
        sub[key].pop(flag, None)
        if not sub[key]:
            sub.pop(key, None)
        if not sub:
            journal.pop(_BUCKETS_KEY, None)


def is_bucket_reviewed(journal: dict,
                       key: Optional[str] = None) -> bool:
    """True iff the user declared this bucket done (user-declared,
    reversible — never inferred). ``key`` selects the per-bucket
    sub-map of a shared day journal; ``None`` = the legacy global
    flag (a bucket with its own journal)."""
    return _soft_get(journal, key, _REVIEWED_KEY)


def set_bucket_reviewed(journal: dict, reviewed: bool,
                        key: Optional[str] = None) -> None:
    """Declare / un-declare done (reversible, sparse)."""
    _soft_set(journal, key, _REVIEWED_KEY, reviewed)


def is_bucket_browsed(journal: dict,
                      key: Optional[str] = None) -> bool:
    """True iff the user opened this bucket but made no mark
    (Nelson 2026-05-18). ``key`` as for :func:`is_bucket_reviewed`."""
    return _soft_get(journal, key, _BROWSED_KEY)


def set_bucket_browsed(journal: dict, browsed: bool,
                       key: Optional[str] = None) -> None:
    """Mark / unmark opened (reversible, sparse)."""
    _soft_set(journal, key, _BROWSED_KEY, browsed)


def has_explicit_marks(journal: dict,
                        filenames: Optional[Iterable[str]] = None
                        ) -> bool:
    """True iff ≥1 explicit cull mark exists. ``filenames=None`` →
    any mark in the journal (a bucket with its own journal). A
    filename set → scoped to THOSE files only — needed when many
    buckets share one day journal, so a Moment bucket's
    'in progress' is judged on its own files, not the whole day."""
    marks = journal.get("marks")
    if not isinstance(marks, dict) or not marks:
        return False
    if filenames is None:
        return True
    want = {str(n) for n in filenames}
    return any(name in want for name in marks)


__all__ = [
    "STATE_DISCARDED",
    "STATE_CANDIDATE",
    "STATE_KEPT",
    "STATE_CYCLE",
    "VALID_STATES",
    "default_state",
    "set_default_state",
    "get_state",
    "set_state",
    "cycle_state",
    "state_counts",
    "is_kept",
    "kept_filenames",
    "bucket_content_key",
    "is_bucket_reviewed",
    "set_bucket_reviewed",
    "is_bucket_browsed",
    "set_bucket_browsed",
    "has_explicit_marks",
]
