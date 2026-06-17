"""Tests for ``core.ingest_pipeline`` — spec/52 slice E.3.

Filesystem tests use ``tmp_path``. The EXIF-bake step is monkeypatched
in tests that exercise the bake branch — exercising the real
``capture_bake.bake_operations`` requires the bundled ExifTool, which
the CI baseline doesn't include.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import List, Tuple

import pytest

from core import capture_bake
from core.ingest_pipeline import (
    IngestPhotoJob,
    IngestResult,
    IngestWarning,
    day_folder_name,
    destination_for,
    run_ingest,
)
from core.path_builder import (
    CAPTURED_CAMERAS_SUBDIR,
    CAPTURED_NO_TIMESTAMP_SUBDIR,
    CAPTURED_OTHER_SUBDIR,
    CAPTURED_PHONES_SUBDIR,
    captured_dir,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_source(tmp_path: Path, *names: str) -> List[Path]:
    src = tmp_path / "source"
    src.mkdir(exist_ok=True)
    paths = []
    for name in names:
        p = src / name
        p.write_bytes(b"\xff\xd8\xff\xe0FAKEJPG")
        paths.append(p)
    return paths


def _job(
    source: Path,
    *,
    camera_id: str = "DSC-RX100",
    is_phone: bool = False,
    day_number: int = 1,
    day_date: date = date(2026, 4, 12),
    day_description: str = "Lisbon",
    raw: datetime = datetime(2026, 4, 12, 10, 0),
    corrected: datetime | None = None,
) -> IngestPhotoJob:
    return IngestPhotoJob(
        source_path=source,
        camera_id=camera_id,
        is_phone=is_phone,
        day_number=day_number,
        day_date=day_date,
        day_description=day_description,
        capture_time_raw=raw,
        capture_time_corrected=corrected,
    )


# --------------------------------------------------------------------------- #
# day_folder_name
# --------------------------------------------------------------------------- #


def test_day_folder_name_full_shape():
    assert day_folder_name(
        1, date(2026, 4, 12), "Lisbon",
    ) == "Dia 1 - 2026-04-12 - Lisbon"


def test_day_folder_name_strips_invalid_windows_chars():
    """Description with ``/`` or ``:`` gets sanitised."""
    out = day_folder_name(1, date(2026, 4, 12), "Lisbon/Sintra")
    assert "/" not in out


def test_day_folder_name_drops_empty_description():
    assert day_folder_name(
        1, date(2026, 4, 12), "",
    ) == "Dia 1 - 2026-04-12"


def test_day_folder_name_drops_missing_date():
    """No date → ``Dia N - description`` (the legacy two-segment shape)."""
    assert day_folder_name(
        1, None, "Lisbon",
    ) == "Dia 1 - Lisbon"


def test_day_folder_name_minimum_dia_only():
    assert day_folder_name(7, None, "") == "Dia 7"


# --------------------------------------------------------------------------- #
# destination_for — routing
# --------------------------------------------------------------------------- #


def test_destination_for_camera_routes_to_cameras_subdir(tmp_path):
    source = tmp_path / "source" / "IMG.JPG"
    job = _job(source, is_phone=False, camera_id="DSC-RX100")
    dest = destination_for(tmp_path / "event", job)
    assert CAPTURED_CAMERAS_SUBDIR in dest.parts
    assert "DSC-RX100" in dest.parts
    assert dest.name == "IMG.JPG"


def test_destination_for_phone_routes_to_phones_subdir(tmp_path):
    source = tmp_path / "source" / "IMG.HEIC"
    job = _job(source, is_phone=True, camera_id="iPhone 15 Pro")
    dest = destination_for(tmp_path / "event", job)
    assert CAPTURED_PHONES_SUBDIR in dest.parts
    assert "iPhone 15 Pro" in dest.parts


def test_destination_for_empty_camera_id_uses_other(tmp_path):
    source = tmp_path / "source" / "X.JPG"
    job = _job(source, camera_id="")
    dest = destination_for(tmp_path / "event", job)
    assert CAPTURED_OTHER_SUBDIR in dest.parts
    assert "_unknown" in dest.parts


def test_destination_for_no_timestamp_routes_to_quarantine(tmp_path):
    source = tmp_path / "source" / "X.JPG"
    job = _job(source, raw=None)
    dest = destination_for(tmp_path / "event", job)
    assert CAPTURED_NO_TIMESTAMP_SUBDIR in dest.parts
    # Quarantine path is FLAT — no day folder.
    assert "Dia" not in str(dest)


def test_destination_for_day_folder_carries_iso_date_and_description(tmp_path):
    source = tmp_path / "source" / "IMG.JPG"
    job = _job(source, day_number=3, day_date=date(2026, 5, 1),
                 day_description="Porto")
    dest = destination_for(tmp_path / "event", job)
    assert "Dia 3 - 2026-05-01 - Porto" in dest.parts


# --------------------------------------------------------------------------- #
# run_ingest — happy paths
# --------------------------------------------------------------------------- #


def test_empty_jobs_returns_clean_result(tmp_path):
    result = run_ingest([], tmp_path / "event")
    assert isinstance(result, IngestResult)
    assert result.photos_copied == 0
    assert result.warnings == []


def test_run_ingest_creates_event_folder_skeleton(tmp_path):
    """First call mkdirs the event tree — the post-ingest layout is what
    the rest of the workflow expects."""
    event_root = tmp_path / "event"
    run_ingest([], event_root)
    assert event_root.is_dir()
    cap = captured_dir(event_root)
    assert (cap / CAPTURED_CAMERAS_SUBDIR).is_dir()
    assert (cap / CAPTURED_PHONES_SUBDIR).is_dir()
    assert (cap / CAPTURED_OTHER_SUBDIR).is_dir()


def test_run_ingest_copies_one_photo_to_camera_bucket(tmp_path):
    [source] = _make_source(tmp_path, "IMG_0001.JPG")
    event_root = tmp_path / "event"
    result = run_ingest([_job(source)], event_root)
    assert result.photos_copied == 1
    assert result.photos_skipped == 0
    # The destination matches what destination_for predicts.
    expected = destination_for(event_root, _job(source))
    assert expected.is_file()
    assert expected.read_bytes() == source.read_bytes()


def test_run_ingest_copies_phone_to_phone_bucket(tmp_path):
    [source] = _make_source(tmp_path, "IMG.HEIC")
    event_root = tmp_path / "event"
    result = run_ingest(
        [_job(source, is_phone=True, camera_id="iPhone 15 Pro")],
        event_root,
    )
    assert result.photos_copied == 1
    cap = captured_dir(event_root)
    assert any(
        CAPTURED_PHONES_SUBDIR in p.parts and p.name == "IMG.HEIC"
        for p in cap.rglob("*")
    )


def test_run_ingest_source_files_untouched(tmp_path):
    """CLAUDE.md invariant — the source tree is never mutated."""
    [source] = _make_source(tmp_path, "IMG.JPG")
    original_bytes = source.read_bytes()
    original_mtime = source.stat().st_mtime
    run_ingest([_job(source)], tmp_path / "event")
    assert source.read_bytes() == original_bytes
    assert source.stat().st_mtime == original_mtime


def test_run_ingest_multiple_photos_same_day_same_camera(tmp_path):
    sources = _make_source(tmp_path, "A.JPG", "B.JPG", "C.JPG")
    event_root = tmp_path / "event"
    jobs = [_job(s) for s in sources]
    result = run_ingest(jobs, event_root)
    assert result.photos_copied == 3
    # All three land in the SAME leaf folder.
    parents = {destination_for(event_root, j).parent for j in jobs}
    assert len(parents) == 1


def test_run_ingest_multi_day_creates_separate_folders(tmp_path):
    sources = _make_source(tmp_path, "A.JPG", "B.JPG")
    event_root = tmp_path / "event"
    result = run_ingest([
        _job(sources[0], day_number=1, day_date=date(2026, 4, 1),
              day_description="Lisbon"),
        _job(sources[1], day_number=2, day_date=date(2026, 4, 2),
              day_description="Sintra"),
    ], event_root)
    assert result.photos_copied == 2
    # Two distinct day folders.
    day_folders = list(
        (captured_dir(event_root) / CAPTURED_CAMERAS_SUBDIR).iterdir()
    )
    assert len(day_folders) == 2


# --------------------------------------------------------------------------- #
# Quarantine — no capture time
# --------------------------------------------------------------------------- #


def test_no_timestamp_photo_goes_to_quarantine(tmp_path):
    [source] = _make_source(tmp_path, "stripped.JPG")
    event_root = tmp_path / "event"
    result = run_ingest([_job(source, raw=None)], event_root)
    assert result.photos_copied == 0
    assert result.photos_quarantined == 1
    quarantine = captured_dir(event_root) / CAPTURED_NO_TIMESTAMP_SUBDIR
    files = list(quarantine.rglob("*.JPG"))
    assert len(files) == 1


# --------------------------------------------------------------------------- #
# Bake — corrected vs raw timestamp triggers EXIF rewrite
# --------------------------------------------------------------------------- #


def test_corrected_equals_raw_does_not_bake(tmp_path, monkeypatch):
    """When the corrected time matches the raw EXIF reading, no bake
    happens — phones + correctly-clocked cameras skip the cost."""
    bakes: List[Tuple[Path, datetime]] = []

    def stub_bake(ops, *, progress=None):
        bakes.extend(ops)
        return capture_bake.BakeResult()

    monkeypatch.setattr(capture_bake, "bake_operations", stub_bake)

    raw = datetime(2026, 4, 12, 10, 0)
    [source] = _make_source(tmp_path, "IMG.JPG")
    result = run_ingest(
        [_job(source, raw=raw, corrected=raw)],
        tmp_path / "event",
    )
    assert result.photos_copied == 1
    assert result.photos_baked == 0
    assert bakes == []


def test_corrected_differs_from_raw_triggers_bake(tmp_path, monkeypatch):
    """When the user calibrated a camera's TZ, the corrected time
    differs from the raw EXIF reading — that photo's COPY gets its
    EXIF rewritten via the shared bake primitive."""
    captured: List[Tuple[Path, datetime]] = []

    def stub_bake(ops, *, progress=None):
        captured.extend(ops)
        return capture_bake.BakeResult()

    monkeypatch.setattr(capture_bake, "bake_operations", stub_bake)

    raw = datetime(2026, 4, 12, 10, 0)
    corrected = datetime(2026, 4, 12, 13, 0)                 # +3h calibration
    [source] = _make_source(tmp_path, "IMG.JPG")
    result = run_ingest(
        [_job(source, raw=raw, corrected=corrected)],
        tmp_path / "event",
    )
    assert result.photos_copied == 1
    assert result.photos_baked == 1
    assert len(captured) == 1
    _path, ts = captured[0]
    assert ts == corrected


def test_bake_corrections_false_skips_bake_entirely(tmp_path, monkeypatch):
    """``bake_corrections=False`` lets the host skip the EXIF rewrite
    even when corrected != raw — useful for dry runs or live-card
    flows that bake separately."""
    called = []

    def stub_bake(ops, *, progress=None):
        called.append(ops)
        return capture_bake.BakeResult()

    monkeypatch.setattr(capture_bake, "bake_operations", stub_bake)

    raw = datetime(2026, 4, 12, 10, 0)
    corrected = datetime(2026, 4, 12, 13, 0)
    [source] = _make_source(tmp_path, "IMG.JPG")
    result = run_ingest(
        [_job(source, raw=raw, corrected=corrected)],
        tmp_path / "event",
        bake_corrections=False,
    )
    assert result.photos_copied == 1
    assert result.photos_baked == 0
    assert called == []


def test_corrected_none_does_not_bake(tmp_path, monkeypatch):
    """When the host hasn't computed a corrected time
    (``capture_time_corrected=None``), the job copies as-is — no bake."""
    called = []
    monkeypatch.setattr(
        capture_bake, "bake_operations",
        lambda ops, *, progress=None: (called.append(ops), capture_bake.BakeResult())[1],
    )
    raw = datetime(2026, 4, 12, 10, 0)
    [source] = _make_source(tmp_path, "IMG.JPG")
    result = run_ingest(
        [_job(source, raw=raw, corrected=None)],
        tmp_path / "event",
    )
    assert result.photos_baked == 0
    assert called == []


def test_bake_errors_propagate_as_warnings(tmp_path, monkeypatch):
    raw = datetime(2026, 4, 12, 10, 0)
    corrected = datetime(2026, 4, 12, 13, 0)
    [source] = _make_source(tmp_path, "IMG.JPG")
    failing_path = source  # arbitrary

    def stub_bake(ops, *, progress=None):
        return capture_bake.BakeResult(errors=[(failing_path, "exiftool barfed")])

    monkeypatch.setattr(capture_bake, "bake_operations", stub_bake)
    result = run_ingest(
        [_job(source, raw=raw, corrected=corrected)],
        tmp_path / "event",
    )
    # The COPY itself succeeded; the bake error is surfaced as a warning.
    assert result.photos_copied == 1
    assert result.photos_baked == 0
    assert any(
        "EXIF rewrite failed" in w.message for w in result.warnings
    )


# --------------------------------------------------------------------------- #
# Copy failures degrade to warnings, not exceptions
# --------------------------------------------------------------------------- #


def test_missing_source_file_is_skipped_with_warning(tmp_path):
    """A job pointing at a non-existent source file should NOT crash —
    the warning is logged and the run continues. Earlier slices ship
    sources together with metadata so this is defensive."""
    fake = tmp_path / "source" / "missing.JPG"
    # Intentionally don't create the file.
    result = run_ingest([_job(fake)], tmp_path / "event")
    assert result.photos_skipped == 1
    assert result.photos_copied == 0
    assert any(w.severity == "error" for w in result.warnings)


# --------------------------------------------------------------------------- #
# Progress callback
# --------------------------------------------------------------------------- #


def test_progress_callback_invoked(tmp_path):
    [source] = _make_source(tmp_path, "IMG.JPG")
    calls: List[Tuple[str, int, int]] = []
    run_ingest(
        [_job(source)],
        tmp_path / "event",
        progress=lambda msg, cur, tot: calls.append((msg, cur, tot)),
    )
    assert len(calls) >= 1                                  # at least one progress emit


def test_progress_callback_optional(tmp_path):
    """``progress=None`` works silently."""
    [source] = _make_source(tmp_path, "IMG.JPG")
    # No exception, no extra setup.
    run_ingest([_job(source)], tmp_path / "event", progress=None)


# --------------------------------------------------------------------------- #
# run_ingest — same-destination handling (spec/57 backfill, 2026-06-10 fix)
# --------------------------------------------------------------------------- #
#
# Two source files can map to ONE destination (same camera + day +
# filename) — the normal shape of a legacy backfill folder carrying the
# same photo under captured AND selected subtrees. The pre-fix pipeline
# silently overwrote the first copy and then crashed the host's DB write
# on item.origin_relpath UNIQUE.


def _two_sources(tmp_path, name: str, bytes_a: bytes, bytes_b: bytes):
    a_dir = tmp_path / "source" / "a"
    b_dir = tmp_path / "source" / "b"
    a_dir.mkdir(parents=True, exist_ok=True)
    b_dir.mkdir(parents=True, exist_ok=True)
    f_a = a_dir / name
    f_b = b_dir / name
    f_a.write_bytes(bytes_a)
    f_b.write_bytes(bytes_b)
    return f_a, f_b


def test_duplicate_content_same_destination_ingests_once(tmp_path):
    f_a, f_b = _two_sources(tmp_path, "IMG.JPG", b"SAMEBYTES", b"SAMEBYTES")
    result = run_ingest([_job(f_a), _job(f_b)], tmp_path / "event")
    assert result.photos_copied == 1
    assert result.photos_duplicates == 1
    assert f_a in result.per_job_info
    assert f_b not in result.per_job_info               # no item row attempted
    dest = result.per_job_info[f_a].destination
    assert dest.read_bytes() == b"SAMEBYTES"
    assert not dest.with_name("IMG (2).JPG").exists()


def test_same_name_different_content_diverts_to_suffixed_destination(tmp_path):
    f_a, f_b = _two_sources(tmp_path, "IMG.JPG", b"ONE", b"TWO")
    result = run_ingest([_job(f_a), _job(f_b)], tmp_path / "event")
    assert result.photos_copied == 2
    assert result.photos_duplicates == 0
    d_a = result.per_job_info[f_a].destination
    d_b = result.per_job_info[f_b].destination
    assert d_a != d_b
    assert d_b.name == "IMG (2).JPG"
    assert d_a.read_bytes() == b"ONE"                   # first copy SURVIVES
    assert d_b.read_bytes() == b"TWO"


def test_existing_identical_destination_resumes_without_duplicate(tmp_path):
    """Re-running the same ingest (interrupted-run recovery) keeps the
    in-place copy, reports the outcome so the item row can be recorded,
    and never diverts to a suffixed name."""
    [source] = _make_source(tmp_path, "IMG.JPG")
    event_root = tmp_path / "event"
    first = run_ingest([_job(source)], event_root)
    dest = first.per_job_info[source].destination

    second = run_ingest([_job(source)], event_root)
    assert second.photos_copied == 1
    assert second.per_job_info[source].destination == dest
    assert second.per_job_info[source].sha256 == first.per_job_info[source].sha256
    assert not dest.with_name("IMG (2).JPG").exists()


def test_existing_different_content_never_overwritten(tmp_path):
    """A later run whose same-named source carries DIFFERENT bytes must
    not destroy the captured copy (invariant #7) — it diverts."""
    [source] = _make_source(tmp_path, "IMG.JPG")
    event_root = tmp_path / "event"
    first = run_ingest([_job(source)], event_root)
    dest = first.per_job_info[source].destination
    original_bytes = dest.read_bytes()

    source.write_bytes(b"DIFFERENT-CONTENT")
    second = run_ingest([_job(source)], event_root)
    assert dest.read_bytes() == original_bytes          # untouched
    diverted = second.per_job_info[source].destination
    assert diverted.name == "IMG (2).JPG"
    assert diverted.read_bytes() == b"DIFFERENT-CONTENT"


def test_quarantine_name_collisions_divert_too(tmp_path):
    """The flat quarantine layout collides on filename alone — different
    EXIF-less files with one name must both survive."""
    f_a, f_b = _two_sources(tmp_path, "X.JPG", b"AAA", b"BBB")
    result = run_ingest(
        [_job(f_a, raw=None), _job(f_b, raw=None)], tmp_path / "event",
    )
    assert result.photos_quarantined == 2
    dests = {result.per_job_info[f_a].destination.name,
             result.per_job_info[f_b].destination.name}
    assert dests == {"X.JPG", "X (2).JPG"}


# --------------------------------------------------------------------------- #
# spec/84 §6 — cancel mid-run
# --------------------------------------------------------------------------- #


def test_should_cancel_bails_at_next_job_and_keeps_already_copied(tmp_path):
    """``should_cancel=True`` after the first file → the loop breaks
    at the next iteration; the file that already landed survives, and
    the result reports cancellation via a warning + a smaller
    ``per_job_info``."""
    sources = _make_source(tmp_path, "IMG_A.JPG", "IMG_B.JPG", "IMG_C.JPG")
    jobs = [_job(p) for p in sources]
    poll_count = {"n": 0}

    def _cancel_after_first():
        poll_count["n"] += 1
        # First job runs (n == 1 before its check then break check is
        # for the second iteration). We want to cancel BEFORE the
        # second iteration's copy, so return True from the 2nd call.
        return poll_count["n"] >= 2

    result = run_ingest(
        jobs, tmp_path / "event",
        should_cancel=_cancel_after_first,
    )

    # Exactly one file landed; the other two never got copied.
    assert result.photos_copied == 1
    assert len(result.per_job_info) == 1
    # The cancel warning is recorded for the host to pick up.
    cancel_warnings = [
        w for w in result.warnings if "cancelled" in w.message]
    assert len(cancel_warnings) == 1
    # The file that DID land is still on disk (spec/57 §4.3.1 — a
    # re-run will keep it via same-destination handling).
    landed = next(iter(result.per_job_info.values())).destination
    assert landed.is_file()


def test_should_cancel_called_at_top_of_each_iteration(tmp_path):
    """``should_cancel`` is polled at the TOP of each iteration —
    pin the contract so the host can rely on "at most one extra file
    after Cancel was clicked" rather than per-byte stops."""
    sources = _make_source(tmp_path, "IMG_A.JPG", "IMG_B.JPG", "IMG_C.JPG")
    polls = []

    def _record_poll():
        polls.append(len(polls) + 1)
        return False

    run_ingest(
        [_job(p) for p in sources], tmp_path / "event",
        should_cancel=_record_poll,
    )
    # One poll per planned job (3 jobs → 3 polls before the loop body).
    assert polls == [1, 2, 3]


def test_should_cancel_none_is_a_silent_skip(tmp_path):
    """The default ``should_cancel=None`` keeps the legacy contract —
    a full run, no polling, no warning."""
    [source] = _make_source(tmp_path, "IMG.JPG")
    result = run_ingest([_job(source)], tmp_path / "event")
    assert result.photos_copied == 1
    assert not any("cancelled" in w.message for w in result.warnings)


def test_resume_after_cancel_keeps_first_copy_finishes_remainder(tmp_path):
    """spec/57 §4.3.1 — re-running the SAME source after a mid-run
    cancel keeps the already-copied file in place AND finishes the
    rest. Tests the full "Import cancelled — re-run to finish" flow."""
    sources = _make_source(tmp_path, "IMG_A.JPG", "IMG_B.JPG", "IMG_C.JPG")
    jobs = [_job(p) for p in sources]
    event_root = tmp_path / "event"

    poll_count = {"n": 0}

    def _cancel_after_first():
        poll_count["n"] += 1
        return poll_count["n"] >= 2

    first = run_ingest(
        jobs, event_root, should_cancel=_cancel_after_first)
    assert first.photos_copied == 1
    landed_first_run = first.per_job_info[sources[0]].destination

    # Re-run with no cancel — the first file should be detected as
    # already-in-place (kept, no divert), the remaining two should
    # copy fresh.
    second = run_ingest(jobs, event_root)
    assert second.photos_copied == 3                    # all three accounted
    assert second.per_job_info[sources[0]].destination == landed_first_run
    # No suffix-divert (a brand-new "(2)" sibling would be the symptom
    # of a broken resume).
    assert not landed_first_run.with_name(
        landed_first_run.stem + " (2)" + landed_first_run.suffix
    ).exists()
