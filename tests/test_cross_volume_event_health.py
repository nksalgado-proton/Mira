"""spec/105 §7 — `event_is_off_library_volume` flags events whose
root sits on a different volume than `library_root`.

Visibility aid only — the catalog/media split (`event_root_abs` on a
big external drive, `library_root` on the internal SSD) is a
legitimate power-user layout that Mira deliberately supports
(spec/76). The helper just lets a user who *wants* one-drive find
the stragglers.

These tests pin the helper without spinning up a real multi-volume
setup — `_same_volume` is monkeypatched to simulate off-volume,
same as the spec/105 §2 target tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway import originals_health
from mira.gateway.originals_health import event_is_off_library_volume
from mira.shared import cut_export


def test_same_volume_event_is_not_flagged(tmp_path):
    """Default install (event + library on one drive) → False; the
    encouraged shape, no flag."""
    library_root = tmp_path / "lib"
    event_root = library_root / "Costa Rica 2026"
    library_root.mkdir()
    event_root.mkdir()
    assert event_is_off_library_volume(
        event_root=event_root, library_root=library_root) is False


def test_off_volume_event_is_flagged(tmp_path, monkeypatch):
    """Catalog/media split: event on a different drive than the
    library. The helper says True so the UI can show "this event
    lives off-library"."""
    library_root = tmp_path / "lib"
    event_root = tmp_path / "external" / "Foo Event"
    library_root.mkdir()
    event_root.mkdir(parents=True)
    monkeypatch.setattr(
        cut_export, "_same_volume", lambda a, b: False)
    assert event_is_off_library_volume(
        event_root=event_root, library_root=library_root) is True


def test_missing_paths_default_to_false(tmp_path):
    """``None`` for either input → False. Refuses to false-positive
    on the offline path — the STORAGE_OFFLINE signal already covers
    the unmounted-drive case."""
    library_root = tmp_path / "lib"
    library_root.mkdir()
    assert event_is_off_library_volume(
        event_root=None, library_root=library_root) is False
    assert event_is_off_library_volume(
        event_root=library_root, library_root=None) is False
    assert event_is_off_library_volume(
        event_root=None, library_root=None) is False


def test_invalid_path_types_default_to_false():
    """Defensive guard — a non-path object never raises."""
    assert event_is_off_library_volume(
        event_root=42, library_root=None) is False
    assert event_is_off_library_volume(
        event_root=None, library_root="some string") is False
