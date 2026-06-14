"""Tests for core.ingest_session — D1 resume support.

Pure-logic tests against tmp_path. ``MIRA_DATA_DIR`` is
isolated to tmp_path so the test suite never touches the real
user data directory.

**B-017 (2026-05-25): journal layout moved.** Per-bucket journals
now live inside ``source_root`` as ``source_root/ingest_journal.json``
(was: ``<user_data_dir>/ingest_sessions/<sha1-hash>.json``). The
shift is what makes the cull dashboard + silent-sync engines
actually find the journals the user writes. ``pending_ingest_sessions``
was retired in the same fix — no production surface needs a
cross-event scan, and a distributed layout can't support one
without walking every event's ``.cull/`` tree. ``has_pending_ingest``
(single-source) survives.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.ingest_session import (
    INGEST_JOURNAL_FILENAME,
    INGEST_JOURNAL_VERSION,
    discard_ingest_journal,
    has_pending_ingest,
    is_kept,
    journal_path_for,
    kept_count as picked_count,
    load_ingest_journal,
    mark_committed,
    mark_kept,
    save_ingest_journal,
    unmark,
)


@pytest.fixture(autouse=True)
def _isolate_user_data(tmp_path, monkeypatch):
    """Every test gets a fresh user_data_dir under tmp_path."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path / "mira"))


# ── Session key + path helpers ───────────────────────────────────


def test_journal_path_lives_inside_source_root(tmp_path):
    """B-017: the journal file sits inside the directory the caller
    treats as the journal scope. Same place the cull dashboard +
    silent-sync engines rglob for it."""
    source = tmp_path / "card" / "DCIM" / "100PANA"
    source.mkdir(parents=True)
    path = journal_path_for(source)
    assert path.parent == source
    assert path.name == INGEST_JOURNAL_FILENAME


def test_journal_path_stable_for_same_source(tmp_path):
    """Same source → same path. Resume relies on this."""
    source = tmp_path / "card" / "DCIM" / "100PANA"
    source.mkdir(parents=True)
    p1 = journal_path_for(source)
    p2 = journal_path_for(source)
    assert p1 == p2


def test_journal_path_differs_for_different_sources(tmp_path):
    a = tmp_path / "card_a"
    b = tmp_path / "card_b"
    a.mkdir()
    b.mkdir()
    assert journal_path_for(a) != journal_path_for(b)


def test_save_creates_source_root_if_missing(tmp_path):
    """The bucket-cull shell hands us paths like
    ``<event>/.cull/<cam>/<bucket>/`` which are conjured on demand —
    save_ingest_journal must materialise the directory before writing."""
    source = tmp_path / "fresh" / "scope" / "deep"
    assert not source.exists()
    journal = load_ingest_journal(source)
    mark_kept(journal, "x.RW2")
    save_ingest_journal(source, journal)
    assert journal_path_for(source).exists()


# ── Empty journal ────────────────────────────────────────────────


def test_load_returns_empty_journal_when_no_file(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    journal = load_ingest_journal(source)
    assert journal["version"] == INGEST_JOURNAL_VERSION
    assert journal["marks"] == {}
    assert journal["committed_at"] is None
    assert journal["source_root"] == str(source.resolve())
    assert journal["session_key"]   # non-empty hash


# ── Round-trip ───────────────────────────────────────────────────


def test_round_trip_persists_marks(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    journal = load_ingest_journal(source)
    mark_kept(journal, "DSC_0001.RW2")
    mark_kept(journal, "DSC_0003.RW2")
    save_ingest_journal(source, journal)

    reloaded = load_ingest_journal(source)
    assert is_kept(reloaded, "DSC_0001.RW2") is True
    assert is_kept(reloaded, "DSC_0002.RW2") is False
    assert is_kept(reloaded, "DSC_0003.RW2") is True
    assert picked_count(reloaded) == 2


def test_round_trip_preserves_current_index(tmp_path):
    from core.ingest_session import set_current_index, current_index
    source = tmp_path / "card"
    source.mkdir()
    journal = load_ingest_journal(source)
    set_current_index(journal, 42)
    save_ingest_journal(source, journal)
    reloaded = load_ingest_journal(source)
    assert current_index(reloaded) == 42


def test_unmark_removes_kept_state(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    journal = load_ingest_journal(source)
    mark_kept(journal, "DSC_0001.RW2")
    unmark(journal, "DSC_0001.RW2")
    save_ingest_journal(source, journal)
    reloaded = load_ingest_journal(source)
    assert is_kept(reloaded, "DSC_0001.RW2") is False
    assert picked_count(reloaded) == 0


# ── Defensive load ───────────────────────────────────────────────


def test_load_recovers_from_corrupt_json(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    path = journal_path_for(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json", encoding="utf-8")
    # Should not crash — returns empty journal
    journal = load_ingest_journal(source)
    assert journal["marks"] == {}


def test_load_recovers_from_non_object_json(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    path = journal_path_for(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('"just a string"', encoding="utf-8")
    journal = load_ingest_journal(source)
    assert journal["marks"] == {}


def test_load_fills_in_missing_keys(tmp_path):
    """Partial journal on disk (e.g. wrote during a crash) gets
    completed from the skeleton."""
    source = tmp_path / "card"
    source.mkdir()
    path = journal_path_for(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Only 'marks' present
    path.write_text(
        json.dumps({"marks": {"DSC_0001.RW2": "picked"}}),
        encoding="utf-8",
    )
    journal = load_ingest_journal(source)
    assert journal["marks"] == {"DSC_0001.RW2": "picked"}
    assert journal["committed_at"] is None
    assert journal["version"] == INGEST_JOURNAL_VERSION


# ── Discard ──────────────────────────────────────────────────────


def test_discard_removes_journal_file(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    journal = load_ingest_journal(source)
    mark_kept(journal, "x.RW2")
    save_ingest_journal(source, journal)

    assert journal_path_for(source).exists()
    assert discard_ingest_journal(source) is True
    assert not journal_path_for(source).exists()


def test_discard_returns_false_when_no_journal(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    assert discard_ingest_journal(source) is False


# ── Commit stamping ─────────────────────────────────────────────


def test_mark_committed_stamps_iso_timestamp(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    journal = load_ingest_journal(source)
    mark_kept(journal, "x.RW2")
    save_ingest_journal(source, journal)

    mark_committed(source, journal)
    reloaded = load_ingest_journal(source)
    assert reloaded["committed_at"]
    # Parseable ISO
    from datetime import datetime
    datetime.fromisoformat(reloaded["committed_at"])


# ── Pending discovery ────────────────────────────────────────────


def test_has_pending_false_for_empty_journal(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    # No marks yet
    assert has_pending_ingest(source) is False


def test_has_pending_true_for_marked_uncommitted_journal(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    journal = load_ingest_journal(source)
    mark_kept(journal, "x.RW2")
    save_ingest_journal(source, journal)
    assert has_pending_ingest(source) is True


def test_has_pending_false_after_commit(tmp_path):
    source = tmp_path / "card"
    source.mkdir()
    journal = load_ingest_journal(source)
    mark_kept(journal, "x.RW2")
    save_ingest_journal(source, journal)
    mark_committed(source, journal)
    assert has_pending_ingest(source) is False


# ── Integration: dashboard + silent-sync see the journal ─────────


def test_journal_at_path_dashboard_and_sync_can_find(tmp_path):
    """B-017 architectural assertion: the per-bucket journal lives
    where ``rglob("ingest_journal.json")`` from above will find it.
    This is the contract that makes the cull-dashboard status read
    AND the silent-sync materialise hardlinks. If either consumer
    changes its discovery glob this test will catch the drift."""
    # Mimic the bucket-cull shell's layout (post-sanitisation):
    # <event>/.cull/<safe-cam>/<safe-bucket-id>/
    journal_root = tmp_path / "event" / ".cull" / "DC-G9M2" / "dia1_individual"
    journal = load_ingest_journal(journal_root)
    mark_kept(journal, "P1000123.RW2")
    save_ingest_journal(journal_root, journal)

    # Both consumers walk from .cull/<cam>/ via rglob.
    cam_root = tmp_path / "event" / ".cull" / "DC-G9M2"
    found = list(cam_root.rglob("ingest_journal.json"))
    assert len(found) == 1
    data = json.loads(found[0].read_text(encoding="utf-8"))
    # Legacy core.cull_session.mark_kept writes the literal "kept" — the
    # vocabulary rename to "picked" only landed in the gateway / event.db
    # path. core/ is legacy plumbing intentionally kept as-is.
    assert data["marks"]["P1000123.RW2"] == "kept"


# spec/52 retirement: core.cull_ingest.commit_ingest_cull was removed with
# the legacy Cull engine. The tests that exercised the journal-stamp side
# effect retired with it; the surviving journal contract is covered above
# in test_journal_at_path_dashboard_and_sync_can_find.
