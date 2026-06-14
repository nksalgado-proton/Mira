"""Tests for core.phase_progress — the per-day per-phase progress
cache that drives the Event-Plan status table (Nelson 2026-05-20 v5).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.event_service import create_event
from core.phase_progress import (
    DAY_STATUS_OVERRIDES_KEY,
    KNOWN_PHASES,
    PHASE_CULL,
    PHASE_PROGRESS_KEY,
    PHASE_PICK,
    PhaseProgress,
    all_phase_progress,
    clear_phase_progress,
    freeze,
    frozen_pairs,
    is_frozen,
    read_phase_progress,
    reopen,
    write_phase_progress,
    write_phase_progress_bulk,
)


def _fresh_event():
    return create_event(
        name="Test",
        start_date=date(2026, 5, 19),
        end_date=date(2026, 5, 21),
    )


# ── PhaseProgress dataclass semantics ──────────────────────────


def test_phase_progress_empty_is_default():
    p = PhaseProgress()
    assert p.is_empty
    assert not p.is_complete
    assert p.exported_fraction == 0.0


def test_phase_progress_complete_when_all_exported():
    p = PhaseProgress(total_buckets=4, exported_buckets=4, kept_buckets=3)
    assert not p.is_empty
    assert p.is_complete
    assert p.exported_fraction == 1.0


def test_phase_progress_partial_fraction():
    p = PhaseProgress(total_buckets=4, exported_buckets=2, kept_buckets=2)
    assert not p.is_empty
    assert not p.is_complete
    assert p.exported_fraction == 0.5


# ── Read / write round-trip ────────────────────────────────────


def test_read_returns_empty_when_unset():
    event = _fresh_event()
    p = read_phase_progress(event, PHASE_CULL, 1)
    assert p.is_empty
    assert p.exported_fraction == 0.0


def test_write_then_read_round_trip():
    event = _fresh_event()
    write_phase_progress(
        event, PHASE_CULL, 1,
        PhaseProgress(total_buckets=5, exported_buckets=3, kept_buckets=4),
    )
    back = read_phase_progress(event, PHASE_CULL, 1)
    assert back.total_buckets == 5
    assert back.exported_buckets == 3
    assert back.kept_buckets == 4


def test_write_unknown_phase_raises():
    event = _fresh_event()
    with pytest.raises(ValueError, match="unknown phase"):
        write_phase_progress(
            event, "not_a_phase", 1, PhaseProgress(total_buckets=1))


def test_write_phase_progress_bulk_writes_all_days():
    event = _fresh_event()
    write_phase_progress_bulk(event, PHASE_CULL, {
        1: PhaseProgress(total_buckets=4, exported_buckets=4),
        2: PhaseProgress(total_buckets=3, exported_buckets=1),
        3: PhaseProgress(total_buckets=2, exported_buckets=0),
    })
    assert read_phase_progress(event, PHASE_CULL, 1).is_complete
    assert read_phase_progress(event, PHASE_CULL, 2).exported_fraction \
        == pytest.approx(1.0 / 3.0)
    assert read_phase_progress(event, PHASE_CULL, 3).is_empty is False
    assert read_phase_progress(event, PHASE_CULL, 3).exported_buckets == 0


def test_phases_are_independent():
    """Writing one phase doesn't leak into another."""
    event = _fresh_event()
    write_phase_progress(
        event, PHASE_CULL, 1,
        PhaseProgress(total_buckets=4, exported_buckets=4))
    # Select for the same day is independent — still empty.
    assert read_phase_progress(event, PHASE_PICK, 1).is_empty


def test_all_phase_progress_returns_every_recorded_day():
    event = _fresh_event()
    write_phase_progress_bulk(event, PHASE_CULL, {
        1: PhaseProgress(total_buckets=4),
        2: PhaseProgress(total_buckets=3),
    })
    out = all_phase_progress(event, PHASE_CULL)
    assert set(out.keys()) == {1, 2}
    assert out[1].total_buckets == 4


def test_clear_phase_progress_drops_only_target_phase():
    event = _fresh_event()
    write_phase_progress(
        event, PHASE_CULL, 1, PhaseProgress(total_buckets=4))
    write_phase_progress(
        event, PHASE_PICK, 1, PhaseProgress(total_buckets=2))
    clear_phase_progress(event, PHASE_CULL)
    assert read_phase_progress(event, PHASE_CULL, 1).is_empty
    # Select untouched.
    assert read_phase_progress(event, PHASE_PICK, 1).total_buckets == 2


def test_known_phases_set_locks_expected_phase_keys():
    """Lock the public phase taxonomy so a typo in caller code surfaces at
    write time rather than as a silent dashboard zero. core.phase_progress
    is legacy plumbing (the production path uses event.db / gateway) and
    keeps the pre-spec/48 vocabulary intact."""
    assert KNOWN_PHASES == frozenset({
        "cull", "pick", "process", "curate", "distribute",
    })


# ── User-freeze (F overlay) ────────────────────────────────────


def test_freeze_and_reopen_round_trip():
    event = _fresh_event()
    assert not is_frozen(event, 1, PHASE_CULL)
    freeze(event, 1, PHASE_CULL)
    assert is_frozen(event, 1, PHASE_CULL)
    # Reopen clears the F.
    reopen(event, 1, PHASE_CULL)
    assert not is_frozen(event, 1, PHASE_CULL)


def test_freeze_is_per_phase_and_per_day():
    """Freezing one (day, phase) doesn't bleed into others."""
    event = _fresh_event()
    freeze(event, 1, PHASE_CULL)
    assert is_frozen(event, 1, PHASE_CULL) is True
    assert is_frozen(event, 1, PHASE_PICK) is False
    assert is_frozen(event, 2, PHASE_CULL) is False


def test_frozen_pairs_returns_all():
    event = _fresh_event()
    freeze(event, 1, PHASE_CULL)
    freeze(event, 2, PHASE_CULL)
    freeze(event, 1, PHASE_PICK)
    assert frozen_pairs(event) == {
        (1, PHASE_CULL), (2, PHASE_CULL), (1, PHASE_PICK),
    }


def test_freeze_is_independent_of_progress():
    """The auto-progress cache and the user-freeze override are
    independent signals — freezing doesn't change the cache."""
    event = _fresh_event()
    write_phase_progress(
        event, PHASE_CULL, 1,
        PhaseProgress(total_buckets=4, exported_buckets=2))
    freeze(event, 1, PHASE_CULL)
    p = read_phase_progress(event, PHASE_CULL, 1)
    # Cache reads back exactly what was written — freeze doesn't
    # mutate it.
    assert p.exported_buckets == 2
    assert is_frozen(event, 1, PHASE_CULL)


def test_reopen_when_not_frozen_is_noop():
    event = _fresh_event()
    reopen(event, 1, PHASE_CULL)             # never frozen
    assert not is_frozen(event, 1, PHASE_CULL)


# ── Storage shape inspection (catches silent schema drift) ────


def test_event_settings_schema_after_write():
    """The on-disk shape uses string day-number keys (JSON object
    keys are strings). Tests catch any silent shift to int keys
    that would survive in-memory but break across save / load."""
    event = _fresh_event()
    write_phase_progress(
        event, PHASE_CULL, 7,
        PhaseProgress(total_buckets=3, exported_buckets=1, kept_buckets=2))
    block = event.event_settings[PHASE_PROGRESS_KEY]
    assert PHASE_CULL in block
    assert "7" in block[PHASE_CULL]              # string key
    assert block[PHASE_CULL]["7"] == {
        "total_buckets": 3,
        "exported_buckets": 1,
        "kept_buckets": 2,
    }


def test_event_settings_schema_after_freeze():
    event = _fresh_event()
    freeze(event, 5, PHASE_PICK)
    overrides = event.event_settings[DAY_STATUS_OVERRIDES_KEY]
    # PHASE_PICK = "pick" in the legacy core module (post-rename of the
    # then-PHASE_SELECT). The freeze key shape is "<day>:<phase>".
    assert overrides == {f"5:{PHASE_PICK}": "frozen"}


# ── recompute_from_disk (Tier 1 fallback) ─────────────────────


def test_recompute_from_disk_rebuilds_cull_from_per_camera_layout(
    qapp, tmp_path,
):
    """Plant a per-camera Cull tree on disk and call
    recompute_from_disk — the cache should reflect the distinct
    camera count per day."""
    from core.event_service import create_event
    from core.models import TripDay
    from core.path_builder import (
        CAPTURED_CAMERAS_SUBDIR,
        CAPTURED_PHONES_SUBDIR,
        culled_dir,
        day_folder_name,
        event_root_path,
    )
    from core.phase_progress import (
        PHASE_CULL,
        read_phase_progress,
        recompute_from_disk,
    )

    event = create_event(
        name="DiskScan",
        start_date=date(2026, 5, 19),
        end_date=date(2026, 5, 20),
    )
    event.trip_days = [
        TripDay(day_number=1, date=date(2026, 5, 19), description="L"),
        TripDay(day_number=2, date=date(2026, 5, 20), description="P"),
    ]
    event.photos_base_path = str(tmp_path / "event")
    event_root = Path(event_root_path(tmp_path / "event", event))
    cull = culled_dir(event_root)

    # Dia 1: two cameras (G9 + iPhone).
    (cull / CAPTURED_CAMERAS_SUBDIR
     / day_folder_name(event.trip_days[0])
     / "DC-G9M2" / "Individual" / "a.rw2").parent.mkdir(parents=True)
    (cull / CAPTURED_CAMERAS_SUBDIR
     / day_folder_name(event.trip_days[0])
     / "DC-G9M2" / "Individual" / "a.rw2").write_bytes(b"x")
    (cull / CAPTURED_PHONES_SUBDIR
     / day_folder_name(event.trip_days[0])
     / "iPhone 13" / "Individual" / "b.heic").parent.mkdir(parents=True)
    (cull / CAPTURED_PHONES_SUBDIR
     / day_folder_name(event.trip_days[0])
     / "iPhone 13" / "Individual" / "b.heic").write_bytes(b"x")

    # Dia 2: one camera only.
    (cull / CAPTURED_CAMERAS_SUBDIR
     / day_folder_name(event.trip_days[1])
     / "DC-G9M2" / "Action" / "c.rw2").parent.mkdir(parents=True)
    (cull / CAPTURED_CAMERAS_SUBDIR
     / day_folder_name(event.trip_days[1])
     / "DC-G9M2" / "Action" / "c.rw2").write_bytes(b"x")

    recompute_from_disk(event)
    p1 = read_phase_progress(event, PHASE_CULL, 1)
    p2 = read_phase_progress(event, PHASE_CULL, 2)
    assert p1.total_buckets == 2
    assert p1.exported_buckets == 2
    assert p1.is_complete
    assert p2.total_buckets == 1
    assert p2.is_complete


def test_recompute_from_disk_marks_consolidated_phases_when_files_exist(
    qapp, tmp_path,
):
    """Select/Process/Curate use the consolidated layout (no
    buckets). recompute should write 1/1 when any file exists for a
    day, leave the day absent otherwise."""
    from core.event_service import create_event
    from core.models import TripDay
    from core.path_builder import (
        day_folder_name,
        event_root_path,
        selected_dir,
    )
    from core.phase_progress import (
        PHASE_PICK,
        read_phase_progress,
        recompute_from_disk,
    )

    event = create_event(
        name="ConsolidatedScan",
        start_date=date(2026, 5, 19),
        end_date=date(2026, 5, 20),
    )
    event.trip_days = [
        TripDay(day_number=1, date=date(2026, 5, 19), description="L"),
        TripDay(day_number=2, date=date(2026, 5, 20), description="P"),
    ]
    event.photos_base_path = str(tmp_path / "event")
    event_root = Path(event_root_path(tmp_path / "event", event))
    sel = selected_dir(event_root)
    # Day 1 has Selected output; day 2 doesn't.
    (sel / day_folder_name(event.trip_days[0])
     / "Individual" / "a.jpg").parent.mkdir(parents=True)
    (sel / day_folder_name(event.trip_days[0])
     / "Individual" / "a.jpg").write_bytes(b"x")

    recompute_from_disk(event)
    p1 = read_phase_progress(event, PHASE_PICK, 1)
    p2 = read_phase_progress(event, PHASE_PICK, 2)
    assert p1.is_complete
    assert p2.is_empty


def test_recompute_from_disk_clears_stale_entries(qapp, tmp_path):
    """When files are removed from disk, recompute drops their
    cache entries — the dashboard reflects current reality."""
    from core.event_service import create_event
    from core.models import TripDay
    from core.path_builder import event_root_path
    from core.phase_progress import (
        PHASE_CULL,
        PhaseProgress,
        read_phase_progress,
        recompute_from_disk,
        write_phase_progress,
    )

    event = create_event(
        name="Cleanup",
        start_date=date(2026, 5, 19),
        end_date=date(2026, 5, 19),
    )
    event.trip_days = [
        TripDay(day_number=1, date=date(2026, 5, 19), description="L"),
    ]
    event.photos_base_path = str(tmp_path / "event")
    # Pre-populate the cache as if Cull had once run for Dia 1.
    write_phase_progress(
        event, PHASE_CULL, 1,
        PhaseProgress(total_buckets=3, exported_buckets=3, kept_buckets=3))
    assert read_phase_progress(event, PHASE_CULL, 1).is_complete

    # No files on disk. recompute should clear the stale entry.
    Path(event_root_path(tmp_path / "event", event)).mkdir(parents=True)
    recompute_from_disk(event)
    assert read_phase_progress(event, PHASE_CULL, 1).is_empty


def test_recompute_from_disk_noop_when_no_photos_base_path(qapp):
    """An event with no photos_base_path returns cleanly without
    touching event_settings."""
    from core.event_service import create_event
    from core.phase_progress import recompute_from_disk

    event = create_event(
        name="NoPath",
        start_date=date(2026, 5, 19),
        end_date=date(2026, 5, 19),
    )
    event.trip_days = []
    event.photos_base_path = ""
    # Should not raise.
    recompute_from_disk(event)
