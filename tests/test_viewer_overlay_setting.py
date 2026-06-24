"""spec/134 — the configurable photo-viewer overlay setting
(``Settings.viewer_overlay_fields``).

Pins:
* default is ``["how2"]`` (today's exposure pill).
* keys round-trip through SettingsRepo.save → load with content
  preserved.
* the Settings dialog's overlay-fields control writes the selected
  keys in canonical OVERLAY_FIELDS order regardless of click order.
"""
from __future__ import annotations

import pytest

from core.cut_overlay import (
    FIELD_HOW1, FIELD_HOW2, FIELD_WHEN, FIELD_WHERE, OVERLAY_FIELDS,
)
from mira.settings.model import Settings
from mira.settings.repo import SettingsRepo


# ── default + simple assignment ─────────────────────────────────────────


def test_default_is_exposure_only():
    """spec/134 — default ``["how2"]`` so today's exposure pill is
    unchanged until the user opts in."""
    assert Settings().viewer_overlay_fields == [FIELD_HOW2]


def test_each_default_is_a_fresh_list_per_instance():
    """Mutable default sanity — two Settings instances must not share
    a list (the field uses ``default_factory``, not a literal)."""
    a = Settings()
    b = Settings()
    assert a.viewer_overlay_fields == b.viewer_overlay_fields
    assert a.viewer_overlay_fields is not b.viewer_overlay_fields
    a.viewer_overlay_fields.append(FIELD_WHEN)
    assert b.viewer_overlay_fields == [FIELD_HOW2]


# ── round-trip through SettingsRepo ─────────────────────────────────────


@pytest.fixture
def repo(tmp_path) -> SettingsRepo:
    return SettingsRepo(path=tmp_path / "settings.rebuild.json")


def test_round_trip_preserves_selection(repo):
    s = Settings()
    s.viewer_overlay_fields = [FIELD_WHEN, FIELD_WHERE,
                               FIELD_HOW1, FIELD_HOW2]
    repo.save(s)
    loaded = repo.load()
    assert loaded.viewer_overlay_fields == [
        FIELD_WHEN, FIELD_WHERE, FIELD_HOW1, FIELD_HOW2]


def test_round_trip_preserves_empty(repo):
    s = Settings()
    s.viewer_overlay_fields = []
    repo.save(s)
    assert repo.load().viewer_overlay_fields == []


def test_round_trip_preserves_subset_in_canonical_order(repo):
    """Order canonicalisation is enforced at the *control* layer (the
    settings dialog writes in OVERLAY_FIELDS order); the repo just
    persists the bytes verbatim. Pin the verbatim contract so a
    hand-edited json keeps its content."""
    s = Settings()
    s.viewer_overlay_fields = [FIELD_WHEN, FIELD_HOW2]
    repo.save(s)
    assert repo.load().viewer_overlay_fields == [FIELD_WHEN, FIELD_HOW2]


def test_load_with_no_file_returns_default(repo):
    """Fresh install — no file on disk → defaults seeded; the field
    lands at ``["how2"]``."""
    assert not repo.path.exists()
    loaded = repo.load()
    assert loaded.viewer_overlay_fields == [FIELD_HOW2]


def test_update_writes_just_this_field(repo):
    """``SettingsRepo.update`` is the in-place mutator used by the
    Settings dialog's Apply path. Confirm it lands without disturbing
    the other defaults."""
    result = repo.update(viewer_overlay_fields=[FIELD_WHEN, FIELD_WHERE])
    assert result.viewer_overlay_fields == [FIELD_WHEN, FIELD_WHERE]
    # Reload from disk to ensure the persist landed.
    assert repo.load().viewer_overlay_fields == [FIELD_WHEN, FIELD_WHERE]


# ── canonical order from the dialog control ────────────────────────────


def test_dialog_control_returns_canonical_order(qapp):
    """The Settings dialog's overlay-fields widget reads back the
    selected keys in OVERLAY_FIELDS order regardless of which checkbox
    the user ticked first. spec/119 multi-select pattern."""
    from mira.ui.base.settings_dialog import SettingsDialog
    dlg = SettingsDialog.__new__(SettingsDialog)  # bypass __init__ chrome
    widget, read, write = dlg._build_overlay_fields({})
    try:
        # Write in REVERSE canonical order; expect read to canonicalise.
        write([FIELD_HOW2, FIELD_HOW1, FIELD_WHERE, FIELD_WHEN])
        assert read() == list(OVERLAY_FIELDS)        # [when, where, how1, how2]
        # Subset: only when + how2 ticked.
        write([FIELD_HOW2, FIELD_WHEN])
        assert read() == [FIELD_WHEN, FIELD_HOW2]
        # Empty.
        write([])
        assert read() == []
    finally:
        widget.deleteLater()


def test_dialog_control_handles_garbage_input(qapp):
    """Unknown / extra keys are dropped silently (the bound checkboxes
    are the only valid keys); ``None`` writes as empty."""
    from mira.ui.base.settings_dialog import SettingsDialog
    dlg = SettingsDialog.__new__(SettingsDialog)
    widget, read, write = dlg._build_overlay_fields({})
    try:
        write([FIELD_WHEN, "garbage", "another", FIELD_HOW2])
        assert read() == [FIELD_WHEN, FIELD_HOW2]
        write(None)
        assert read() == []
    finally:
        widget.deleteLater()
