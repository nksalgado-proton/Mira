"""Tests for core.atomic_journal — Model 3 v2 companion (journal
protection: history rotation + SHA256 sidecars)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.atomic_journal import (
    DEFAULT_MAX_HISTORY,
    HISTORY_DIR_NAME,
    SHA256_SIDECAR_SUFFIX,
    list_history,
    verify_sidecar,
    write_with_protection,
)


# ── Basic write ────────────────────────────────────────────────


def test_first_write_creates_file_and_sidecar(tmp_path):
    journal = tmp_path / "cull.json"
    out = write_with_protection(journal, {"keep": ["a.rw2"]})

    assert journal.is_file()
    assert json.loads(journal.read_text()) == {"keep": ["a.rw2"]}
    assert out.sha256
    assert out.history_path is None  # first write, nothing to archive

    sidecar = journal.with_suffix(".json.sha256")
    assert sidecar.is_file()
    assert out.sha256 in sidecar.read_text()


def test_history_dir_created_on_demand(tmp_path):
    journal = tmp_path / "cull.json"
    write_with_protection(journal, {"v": 1})
    # First write: no history dir yet (nothing to archive).
    assert not (tmp_path / HISTORY_DIR_NAME).exists()
    # Second write: history dir appears with the prior version.
    out2 = write_with_protection(journal, {"v": 2})
    hist = tmp_path / HISTORY_DIR_NAME
    assert hist.is_dir()
    assert out2.history_path is not None
    assert out2.history_path.parent == hist
    assert json.loads(out2.history_path.read_text()) == {"v": 1}


def test_history_preserves_prior_versions(tmp_path):
    journal = tmp_path / "cull.json"
    for i in range(5):
        write_with_protection(journal, {"v": i})
    # After 5 writes there are 4 history entries (the first write
    # has nothing to archive; subsequent 4 archive the prior).
    versions = list_history(journal)
    assert len(versions) == 4
    # Most-recent-first ordering.
    contents = [json.loads(p.read_text())["v"] for p in versions]
    assert contents == [3, 2, 1, 0]


# ── History rotation ──────────────────────────────────────────


def test_history_prunes_past_max(tmp_path):
    """When more than max_history versions accumulate, the oldest
    get pruned so the dir stays bounded."""
    journal = tmp_path / "cull.json"
    for i in range(25):
        write_with_protection(journal, {"v": i}, max_history=5)
    versions = list_history(journal)
    # Capped at max_history.
    assert len(versions) == 5
    # The 5 newest survived (writes 19, 20, 21, 22, 23 — write 24
    # is the live file, write 23's content lives in the most-
    # recent history entry).
    contents = [json.loads(p.read_text())["v"] for p in versions]
    assert contents == [23, 22, 21, 20, 19]


def test_default_max_history_constant():
    """Sanity that the constant is what we documented (20)."""
    assert DEFAULT_MAX_HISTORY == 20


# ── Sidecar verification ──────────────────────────────────────


def test_verify_passes_after_write(tmp_path):
    journal = tmp_path / "cull.json"
    write_with_protection(journal, {"keep": ["a.rw2"]})

    result = verify_sidecar(journal)
    assert result.valid is True
    assert result.sidecar_missing is False
    assert result.actual_sha256 == result.expected_sha256


def test_verify_detects_external_edit(tmp_path):
    """A user (or bit rot) modifies the live file; the sidecar
    still has the old digest. Verify must return invalid."""
    journal = tmp_path / "cull.json"
    write_with_protection(journal, {"keep": ["a.rw2"]})

    # External edit — bytes change, sidecar stale.
    journal.write_text('{"keep": ["a.rw2", "b.rw2"]}', encoding="utf-8")

    result = verify_sidecar(journal)
    assert result.valid is False
    assert result.sidecar_missing is False
    assert result.actual_sha256 != result.expected_sha256


def test_verify_reports_missing_sidecar(tmp_path):
    """Legacy journals from before this helper was used have no
    sidecar — verify reports that distinctly so the caller knows
    "we can't tell" vs "corrupt"."""
    journal = tmp_path / "legacy.json"
    journal.write_text('{"legacy": true}', encoding="utf-8")

    result = verify_sidecar(journal)
    assert result.valid is False
    assert result.sidecar_missing is True


def test_sidecar_in_standard_sha256sum_format(tmp_path):
    """The sidecar uses the format ``<hex>  <basename>\\n`` so
    external ``sha256sum -c`` can verify it."""
    journal = tmp_path / "cull.json"
    out = write_with_protection(journal, {"k": 1})
    sidecar = journal.with_suffix(".json" + SHA256_SIDECAR_SUFFIX)
    content = sidecar.read_text()
    # "<64-char hex>  <basename>"
    parts = content.strip().split(maxsplit=1)
    assert len(parts) == 2
    assert parts[0] == out.sha256
    assert parts[1] == journal.name


# ── Multi-journal isolation ───────────────────────────────────


def test_two_journals_in_same_dir_dont_share_history(tmp_path):
    """History entries are name-prefixed so cull.json and
    curate.json in the same dir don't collide."""
    cull = tmp_path / "cull.json"
    curate = tmp_path / "curate.json"
    for i in range(3):
        write_with_protection(cull, {"v": i})
        write_with_protection(curate, {"v": i * 10})
    cull_hist = list_history(cull)
    curate_hist = list_history(curate)
    assert len(cull_hist) == 2  # writes 1 and 2 archived 0 and 1
    assert len(curate_hist) == 2
    cull_versions = [json.loads(p.read_text())["v"] for p in cull_hist]
    curate_versions = [json.loads(p.read_text())["v"] for p in curate_hist]
    assert cull_versions == [1, 0]
    assert curate_versions == [10, 0]


# ── Atomicity ──────────────────────────────────────────────────


def test_write_replaces_atomically(tmp_path):
    """After a successful write, no ``.tmp`` file remains alongside
    the journal."""
    journal = tmp_path / "cull.json"
    write_with_protection(journal, {"k": 1})
    tmp_artifact = journal.with_suffix(".json.tmp")
    assert not tmp_artifact.exists()


def test_indent_can_be_overridden(tmp_path):
    """``indent`` controls JSON formatting — pass None for the
    most compact write (saves bytes on huge journals)."""
    journal = tmp_path / "cull.json"
    write_with_protection(
        journal, {"a": 1, "b": 2}, indent=None,
    )
    # Compact JSON has no whitespace between key/value pairs.
    text = journal.read_text()
    assert "  " not in text       # no indentation
    assert "\n" not in text       # no line breaks
