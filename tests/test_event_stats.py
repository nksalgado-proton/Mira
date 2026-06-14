"""Tests for core.event_stats — F-025 closed-event recap engine."""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path

import pytest

from core.event_stats import (
    ALL_TIME_BEST,
    TIER_LONG,
    TIER_MEDIUM,
    TIER_SHORT,
    ClosedEventStats,
    TierDuration,
    compute_closed_event_stats,
    pick_random_curated_photo,
)
from core.models import DistributionAction, Event, TripDay


def _make_event(tmp_path: Path, *, name: str = "Trip") -> Event:
    root = tmp_path / "2026 - Test"
    root.mkdir(parents=True, exist_ok=True)
    return Event(
        name=name,
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 3),
        photos_base_path=str(root),
        trip_days=[
            TripDay(day_number=1, date=date(2026, 5, 1), description="d1"),
            TripDay(day_number=2, date=date(2026, 5, 2), description="d2"),
            TripDay(day_number=3, date=date(2026, 5, 3), description="d3"),
        ],
    )


def _drop(root: Path, rel: str, name: str) -> Path:
    p = root / rel / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


# ── compute_closed_event_stats — empty paths ────────────────────


def test_compute_returns_empty_when_no_photos_base_path():
    event = Event(name="empty")
    stats = compute_closed_event_stats(event)
    assert stats.funnel == {}
    assert stats.cameras == ()
    assert stats.tier_durations == ()
    assert stats.best_total == 0


def test_compute_returns_empty_when_event_root_missing(tmp_path):
    """photos_base_path set but the folder doesn't exist —
    return an empty bundle, never raise."""
    event = Event(
        name="missing",
        photos_base_path=str(tmp_path / "does-not-exist"),
    )
    stats = compute_closed_event_stats(event)
    assert stats.funnel == {}


# ── Funnel + cameras ────────────────────────────────────────────


def test_compute_pulls_funnel_and_cameras_from_disk(tmp_path):
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    # 2 captured files from G9 + 1 from iPhone, 1 culled, 1 selected.
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/G9", "a.jpg")
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/G9", "b.jpg")
    _drop(root, "00 - Captured/_phones/Dia 2 - d2/iPhone", "c.jpg")
    _drop(root, "01 - Culled/_cameras/Dia 1 - d1/G9/general", "a.jpg")
    _drop(root, "02 - Selected/Dia 1 - d1/general", "a.jpg")
    _drop(root, "03 - Processed/Dia 1 - d1/general", "a.jpg")
    _drop(root, "04 - Curated/All-Time Best", "a.jpg")

    stats = compute_closed_event_stats(event)
    assert stats.funnel["captured"] == 3
    assert stats.funnel["culled"] == 1
    assert stats.funnel["selected"] == 1
    assert stats.funnel["processed"] == 1
    assert stats.funnel["curated"] == 1
    # Cameras sorted by count desc.
    cam_dict = dict(stats.cameras)
    assert cam_dict["G9"] == 2
    assert cam_dict["iPhone"] == 1
    assert stats.cameras[0][0] == "G9"      # most-photos first


# ── Slideshow tier durations ────────────────────────────────────


def test_tier_durations_use_per_tier_defaults_when_no_settings(tmp_path):
    """No settings dict passed → falls back to 4/6/6 s defaults
    per tier (docs/27 §6); minutes computed against per-tier seconds."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    for i in range(60):
        _drop(root, "04 - Curated/Short", f"s{i}.jpg")
    for i in range(40):
        _drop(root, "04 - Curated/Medium", f"m{i}.jpg")
    for i in range(15):
        _drop(root, "04 - Curated/Long", f"l{i}.jpg")

    stats = compute_closed_event_stats(event)
    by_tier = {td.tier: td for td in stats.tier_durations}
    # Short: 60 files × 4 s ÷ 60 = 4 min
    assert by_tier[TIER_SHORT].file_count == 60
    assert by_tier[TIER_SHORT].seconds_per_slide == 4.0
    assert by_tier[TIER_SHORT].total_minutes == pytest.approx(4.0)
    # Medium: 40 × 6 s ÷ 60 = 4 min
    assert by_tier[TIER_MEDIUM].file_count == 40
    assert by_tier[TIER_MEDIUM].seconds_per_slide == 6.0
    assert by_tier[TIER_MEDIUM].total_minutes == pytest.approx(4.0)
    # Long: 15 × 6 s ÷ 60 = 1.5 min
    assert by_tier[TIER_LONG].file_count == 15
    assert by_tier[TIER_LONG].seconds_per_slide == 6.0
    assert by_tier[TIER_LONG].total_minutes == pytest.approx(1.5)


def test_tier_durations_honour_user_settings(tmp_path):
    """User-overridden seconds-per-slide values reach the
    duration calc."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    for i in range(30):
        _drop(root, "04 - Curated/Medium", f"m{i}.jpg")

    settings = {
        "slideshow_seconds_per_slide_short": 2.0,
        "slideshow_seconds_per_slide_medium": 5.0,
        "slideshow_seconds_per_slide_long": 4.0,
    }
    stats = compute_closed_event_stats(event, settings=settings)
    by_tier = {td.tier: td for td in stats.tier_durations}
    # Medium: 30 × 5 s ÷ 60 = 2.5 min
    assert by_tier[TIER_MEDIUM].seconds_per_slide == 5.0
    assert by_tier[TIER_MEDIUM].total_minutes == pytest.approx(2.5)


def test_tier_durations_clamp_unreasonable_seconds(tmp_path):
    """Defensive: a 0.0 or 9999 setting clamps to [0.5, 60]."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "04 - Curated/Short", "a.jpg")

    settings = {"slideshow_seconds_per_slide_short": 0.0}
    stats = compute_closed_event_stats(event, settings=settings)
    by_tier = {td.tier: td for td in stats.tier_durations}
    assert by_tier[TIER_SHORT].seconds_per_slide == 0.5

    settings = {"slideshow_seconds_per_slide_short": 9999.0}
    stats = compute_closed_event_stats(event, settings=settings)
    by_tier = {td.tier: td for td in stats.tier_durations}
    assert by_tier[TIER_SHORT].seconds_per_slide == 60.0


# ── Best by preferred style ─────────────────────────────────────


def test_best_by_preferred_stem_intersects_with_theme_folders(tmp_path):
    """Files in All-Time Best whose stem also appears in
    <preferred_genre.title()>/ count toward that theme."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    # 3 best photos: a.jpg + b.jpg from Wildlife, c.jpg from
    # Macro. Two preferred genres: macro, wildlife.
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _drop(root, "04 - Curated/All-Time Best", name)
    _drop(root, "04 - Curated/Wildlife", "a.jpg")
    _drop(root, "04 - Curated/Wildlife", "b.jpg")
    _drop(root, "04 - Curated/Macro", "c.jpg")
    settings = {"preferred_genres": ["macro", "wildlife"]}
    stats = compute_closed_event_stats(event, settings=settings)
    assert stats.best_total == 3
    bd = dict(stats.best_by_preferred)
    assert bd["Macro"] == 1
    assert bd["Wildlife"] == 2


def test_best_by_preferred_zero_when_no_overlap(tmp_path):
    """A preferred genre with no overlap into All-Time Best
    returns zero — the breakdown surfaces it as 0, not absent
    (the user sees their preference scored as zero)."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "04 - Curated/All-Time Best", "a.jpg")
    _drop(root, "04 - Curated/Macro", "a.jpg")
    settings = {"preferred_genres": ["macro", "wildlife"]}
    stats = compute_closed_event_stats(event, settings=settings)
    bd = dict(stats.best_by_preferred)
    assert bd["Macro"] == 1
    assert bd["Wildlife"] == 0


def test_best_breakdown_empty_when_no_best_folder(tmp_path):
    event = _make_event(tmp_path)
    settings = {"preferred_genres": ["macro", "wildlife"]}
    stats = compute_closed_event_stats(event, settings=settings)
    assert stats.best_total == 0
    assert stats.best_by_preferred == ()


# ── Distribution channels ───────────────────────────────────────


def test_distribution_channels_pulled_from_event_log(tmp_path):
    event = _make_event(tmp_path)
    event.distribution_log = [
        DistributionAction(timestamp="t1", channel="google_photos"),
        DistributionAction(timestamp="t2", channel="whatsapp"),
        DistributionAction(timestamp="t3", channel="google_photos"),
    ]
    stats = compute_closed_event_stats(event)
    # Deduped + sorted.
    assert stats.distribution_channels == ("google_photos", "whatsapp")


# ── Cover-photo picker ──────────────────────────────────────────


def test_pick_random_falls_back_short_to_medium_to_long(tmp_path):
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    # Only Long has photos.
    _drop(root, "04 - Curated/Long", "l1.jpg")
    rng = random.Random(42)
    pick = pick_random_curated_photo(event, rng=rng)
    assert pick is not None
    assert pick.name == "l1.jpg"


def test_pick_random_prefers_short_over_medium_over_long(tmp_path):
    """When multiple tiers have photos, Short wins."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "04 - Curated/Short", "s1.jpg")
    _drop(root, "04 - Curated/Medium", "m1.jpg")
    _drop(root, "04 - Curated/Long", "l1.jpg")
    rng = random.Random(42)
    pick = pick_random_curated_photo(event, rng=rng)
    assert pick is not None
    assert pick.name == "s1.jpg"


def test_pick_random_returns_none_when_no_curated_tree(tmp_path):
    """A closed event whose Curate phase never ran → no cover
    photo. Caller renders a placeholder."""
    event = _make_event(tmp_path)
    pick = pick_random_curated_photo(event)
    assert pick is None


def test_pick_random_returns_none_with_no_photos_base_path():
    event = Event(name="empty")
    assert pick_random_curated_photo(event) is None


def test_pick_random_ignores_non_photo_files(tmp_path):
    """Stray readmes / .DS_Store don't count as cover-photo
    candidates."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "04 - Curated/Short", "notes.txt")
    _drop(root, "04 - Curated/Short", ".DS_Store")
    _drop(root, "04 - Curated/Medium", "m.jpg")

    rng = random.Random(0)
    pick = pick_random_curated_photo(event, rng=rng)
    assert pick is not None
    # Short had no photos → fell through to Medium.
    assert pick.name == "m.jpg"


# ── F-026 — last-completed-phase helpers ───────────────────────


from core.event_stats import (                       # noqa: E402
    last_completed_phase,
    phase_funnel_breakdown,
    pick_random_last_phase_photo,
    style_breakdown_last_phase,
)


def test_last_completed_phase_returns_none_when_no_base_path():
    event = Event(name="empty")
    assert last_completed_phase(event) is None


def test_last_completed_phase_picks_curated_when_present(tmp_path):
    """Curated is DONE (phase_progress marks every day complete)
    → the walk picks Curated even though earlier phases also
    have files."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/G9", "a.jpg")
    _drop(root, "01 - Culled/_cameras/Dia 1 - d1/G9/general", "a.jpg")
    _drop(root, "04 - Curated/Wildlife", "a.jpg")
    _mark_phase_done(event, "share")

    result = last_completed_phase(event)
    assert result is not None
    phase_key, label, phase_root = result
    assert phase_key == "curated"
    assert label == "Curated"
    assert phase_root.name == "04 - Curated"


def test_last_completed_phase_falls_back_to_earliest(tmp_path):
    """Only Captured is DONE → the walk picks Captured."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _mark_captured_done(event, root)

    result = last_completed_phase(event)
    assert result is not None
    phase_key, label, _root = result
    assert phase_key == "captured"
    assert label == "Captured"


def test_last_completed_phase_returns_none_when_nothing_yet(tmp_path):
    """An event whose folder exists but has no phase progress + no
    captured files → None."""
    event = _make_event(tmp_path)
    assert last_completed_phase(event) is None


def test_last_completed_phase_skips_in_progress_phase(tmp_path):
    """**Nelson 2026-05-26 (the regression that prompted using
    phase status):** a phase with files on disk but
    ``phase_progress`` status = IN_PROGRESS / READY must be
    skipped. The walk continues to earlier phases looking for one
    that's genuinely DONE."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    # Files exist for Curated but the phase is IN_PROGRESS (only
    # day 1 has progress recorded, days 2-3 don't).
    _drop(root, "04 - Curated/Wildlife", "a.jpg")
    from core.phase_progress import PhaseProgress, write_phase_progress
    write_phase_progress(
        event, "share", 1,
        PhaseProgress(total_buckets=2, exported_buckets=1, kept_buckets=1),
    )
    # Cull is DONE on every day.
    _drop(root, "01 - Culled/_cameras/Dia 1 - d1/G9/general", "k.jpg")
    _mark_phase_done(event, "pick")

    result = last_completed_phase(event)
    assert result is not None
    phase_key, _label, _root = result
    # Curated has files but isn't DONE → skipped. Cull IS done →
    # picked.
    assert phase_key == "culled"


# ── Journal-driven style breakdown (Nelson 2026-05-26 v8) ──────


import json as _json


def _write_curate_journal(
    root: Path, entries: list[tuple[str, str]],
) -> None:
    """Drop a minimal ``04 - Curated/_share_tags.json`` with the
    given ``[(filename, tag_str), ...]`` pairs. Mirrors the shape
    written by ``core.curate_session.ShareSession.write_journal``."""
    j = root / "04 - Curated" / "_share_tags.json"
    j.parent.mkdir(parents=True, exist_ok=True)
    j.write_text(_json.dumps({
        "version": 1,
        "tags": [{"path": str(p), "tag": t} for p, t in entries],
        "skipped": [],
    }), encoding="utf-8")


def _write_cull_journal(
    root: Path,
    *,
    rel: str,
    marks: dict[str, str],
    genre: dict[str, str] | None = None,
    genre_auto: dict[str, dict] | None = None,
    genre_bucket: str | None = None,
) -> None:
    """Drop a minimal ``ingest_journal.json`` under
    ``<root>/<rel>/``. ``marks`` carries the per-file state
    (``"picked"`` / ``"skipped"`` / ...); the rest are optional
    style-classification metadata."""
    j = root / rel / "ingest_journal.json"
    j.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"marks": marks}
    if genre:
        payload["genre"] = genre
    if genre_auto:
        payload["_genre_auto"] = genre_auto
    if genre_bucket:
        payload["genre_bucket"] = genre_bucket
    j.write_text(_json.dumps(payload), encoding="utf-8")


def _mark_phase_done(event, phase_key: str) -> None:
    """Test helper — write a phase_progress entry for every trip
    day that marks the phase complete. Mirrors what Export commits
    do at runtime (1 bucket total, 1 exported). After this call,
    ``phase_status_for(event, phase_key) == STATUS_DONE`` and
    ``last_completed_phase`` will accept this phase.

    Captured phase has no cache; the test instead drops files for
    every planned day so the filesystem walk reports DONE."""
    from core.phase_progress import PhaseProgress, write_phase_progress
    for day in event.trip_days:
        write_phase_progress(
            event, phase_key, day.day_number,
            PhaseProgress(
                total_buckets=1, exported_buckets=1, kept_buckets=1,
            ),
        )


def _mark_captured_done(event, root: Path) -> None:
    """Drop one capture-anchor file per trip day so the Captured
    phase reports DONE (days_with_files == day_count). Uses
    ``core.path_builder.day_folder_name`` so the on-disk name
    matches what ``DayStatusTable._captured_camera_counts_by_day``
    walks for (includes the ISO date prefix added in task #107)."""
    from core.path_builder import day_folder_name
    for day in event.trip_days:
        _drop(
            root, f"00 - Captured/_cameras/{day_folder_name(day)}/G9",
            f"anchor-d{day.day_number}.jpg",
        )


def test_style_breakdown_curated_reads_theme_from_tag_and_upstream(tmp_path):
    """Nelson 2026-05-26 (post-00.048 fix): the curate journal's
    **theme** drives the style when present; everything else
    resolves to the upstream Select journal's effective genre,
    NOT the slideshow tier. Short/Medium/Long/Best/Composition
    aren't styles — they're tiers."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "04 - Curated/All-Time Best", "anchor.jpg")
    _write_curate_journal(root, [
        ("p1.jpg", "theme:macro"),         # theme wins → Macro
        ("p2.jpg", "theme:macro"),         # theme wins → Macro
        ("p3.jpg", "best+theme:wildlife"), # theme wins → Wildlife
        ("p4.jpg", "theme:wildlife"),      # theme wins → Wildlife
        ("p5.jpg", "best"),                # no theme → upstream → Portrait
        ("p6.jpg", "short"),               # no theme → upstream → Landscape
        ("p7.jpg", "long"),                # no theme, no upstream → General
    ])
    # Upstream Select journal pins styles for p5 + p6 (mirrors
    # what the user assigned during Cull/Select).
    _write_cull_journal(
        root, rel=".cull/select/bucket-a",
        marks={"p5.jpg": "picked", "p6.jpg": "picked"},
        genre={"p5.jpg": "portrait", "p6.jpg": "landscape"},
    )
    _mark_phase_done(event, "share")

    slices, label = style_breakdown_last_phase(event)
    assert label == "Curated"
    by_name = dict(slices)
    assert by_name == {
        "Wildlife": 2, "Macro": 2,
        "Portrait": 1, "Landscape": 1,
        "General": 1,
    }


def test_style_breakdown_curated_all_general_when_no_theme(tmp_path):
    """Nelson's regression case: every tag is a tier (no theme),
    no upstream Select journal exists → every entry resolves to
    'General'. Confirms tier names never leak into the pie."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "04 - Curated/Short", "x.jpg")
    _write_curate_journal(root, [
        ("a.jpg", "short"),
        ("b.jpg", "short"),
        ("c.jpg", "medium"),
        ("d.jpg", "long"),
        ("e.jpg", "best"),
    ])
    _mark_phase_done(event, "share")

    slices, label = style_breakdown_last_phase(event)
    assert label == "Curated"
    # Tier names MUST NOT appear as styles. Without upstream
    # classifications, everything falls back to General.
    assert dict(slices) == {"General": 5}


def test_style_breakdown_curated_excludes_discarded(tmp_path):
    """Files in the curate journal's ``discarded`` list don't
    contribute to the pie."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "04 - Curated/All-Time Best", "anchor.jpg")
    journal_path = root / "04 - Curated" / "_share_tags.json"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(_json.dumps({
        "version": 1,
        "tags": [
            {"path": "a.jpg", "tag": "theme:wildlife"},
            {"path": "b.jpg", "tag": "theme:macro"},
        ],
        "skipped": ["b.jpg"],
    }), encoding="utf-8")
    _mark_phase_done(event, "share")

    slices, label = style_breakdown_last_phase(event)
    assert label == "Curated"
    # b.jpg was discarded → only a.jpg counts.
    assert dict(slices) == {"Wildlife": 1}


def test_style_breakdown_selected_reads_cull_shaped_journal(tmp_path):
    """The Select pie reads ``<event>/.cull/select/**/ingest_journal.
    json``. Effective genre = per-file override → auto cache →
    bucket override → "general". KEPT marks count; everything
    else is ignored."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "02 - Selected/Dia 1 - d1/general", "anchor.jpg")
    _write_cull_journal(
        root,
        rel=".cull/select/bucket-a",
        marks={
            "a.jpg": "picked",
            "b.jpg": "picked",
            "c.jpg": "picked",
            "d.jpg": "skipped",
        },
        genre={"a.jpg": "wildlife"},
        genre_auto={"b.jpg": {"s": "landscape", "r": False}},
        genre_bucket="macro",
    )
    _mark_phase_done(event, "pick")

    slices, label = style_breakdown_last_phase(event)
    assert label == "Selected"
    by_name = dict(slices)
    assert by_name == {"Wildlife": 1, "Landscape": 1, "Macro": 1}


def test_style_breakdown_processed_inherits_from_select(tmp_path):
    """Process inherits styles from the upstream Select journal;
    the engine reads the Select journal when the last completed
    phase is Processed."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "03 - Processed/Dia 1 - d1/general", "anchor.jpg")
    _write_cull_journal(
        root,
        rel=".cull/select/bucket-a",
        marks={"a.jpg": "picked", "b.jpg": "picked", "c.jpg": "picked"},
        genre={"a.jpg": "wildlife", "b.jpg": "wildlife",
               "c.jpg": "landscape"},
    )
    _mark_phase_done(event, "edit")

    slices, label = style_breakdown_last_phase(event)
    assert label == "Processed"
    by_name = dict(slices)
    assert by_name == {"Wildlife": 2, "Landscape": 1}


def test_style_breakdown_culled_walks_per_camera_journals(tmp_path):
    """Cull-phase walk reads every camera's journal under
    ``<event>/.cull/<cam>/`` but SKIPS ``.cull/select/`` so the
    cull phase doesn't double-count Select's data."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "01 - Culled/_cameras/Dia 1 - d1/G9/general",
          "anchor.jpg")
    _write_cull_journal(
        root, rel=".cull/G9/bucket-a",
        marks={"a.jpg": "picked", "b.jpg": "picked"},
        genre={"a.jpg": "wildlife", "b.jpg": "wildlife"},
    )
    _write_cull_journal(
        root, rel=".cull/iPhone/bucket-b",
        marks={"x.jpg": "picked"},
        genre={"x.jpg": "portrait"},
    )
    _write_cull_journal(
        root, rel=".cull/select/bucket-c",
        marks={"z.jpg": "picked"},
        genre={"z.jpg": "macro"},
    )
    _mark_phase_done(event, "pick")

    slices, label = style_breakdown_last_phase(event)
    assert label == "Culled"
    by_name = dict(slices)
    assert by_name == {"Wildlife": 2, "Portrait": 1}


def test_style_breakdown_empty_for_captured_only(tmp_path):
    """Captured has no style data → empty slices, phase label
    still reads 'Captured' so the pie's contextual empty-state
    hint can show 'no per-style breakdown in Captured'."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _mark_captured_done(event, root)

    slices, label = style_breakdown_last_phase(event)
    assert slices == ()
    assert label == "Captured"


def test_style_breakdown_falls_back_when_journal_missing(tmp_path):
    """Curated tree exists on disk + Curated is DONE per
    phase_progress, but the curate journal is missing → fall
    back to Select. Demonstrates the journal-driven walk's
    robustness against a phase-done-but-missing-journal state."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "04 - Curated/Short", "x.jpg")        # no journal
    _drop(root, "02 - Selected/Dia 1 - d1/general", "anchor.jpg")
    _write_cull_journal(
        root, rel=".cull/select/bucket-a",
        marks={"a.jpg": "picked", "b.jpg": "picked"},
        genre={"a.jpg": "wildlife", "b.jpg": "landscape"},
    )
    # Both phases are DONE; Curated is preferred but its journal
    # is missing.
    _mark_phase_done(event, "share")
    _mark_phase_done(event, "pick")

    slices, label = style_breakdown_last_phase(event)
    # Curated had no journal → walk fell through to Selected.
    assert label == "Selected"
    by_name = dict(slices)
    assert by_name == {"Wildlife": 1, "Landscape": 1}


def test_style_breakdown_ignores_filesystem_folder_names(tmp_path):
    """**Nelson 2026-05-26 (the regression that prompted the
    rewrite):** the chart used to read folder names. Confirm
    the new engine ignores Curated bucket folder names entirely
    — only the journal entries + upstream Select journal matter."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "04 - Curated/Macro", "a.jpg")
    _drop(root, "04 - Curated/Wildlife", "b.jpg")
    _write_curate_journal(root, [
        ("a.jpg", "best"),
        ("b.jpg", "best"),
    ])
    # Upstream pin so the lookup has data; folder names "Macro"
    # and "Wildlife" must STILL be ignored.
    _write_cull_journal(
        root, rel=".cull/select/bucket-a",
        marks={"a.jpg": "picked", "b.jpg": "picked"},
        genre={"a.jpg": "portrait", "b.jpg": "portrait"},
    )
    _mark_phase_done(event, "share")

    slices, label = style_breakdown_last_phase(event)
    assert label == "Curated"
    # Tag was "best" (no theme) → upstream "portrait" used.
    # Curated folder names "Macro" / "Wildlife" ignored.
    assert dict(slices) == {"Portrait": 2}


def test_pick_random_last_phase_photo_falls_back_through_chain(tmp_path):
    """Selected is DONE; Process / Curate are not → the random
    pick comes from the Selected phase folder."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/G9", "ignored.jpg")
    _drop(root, "01 - Culled/_cameras/Dia 1 - d1/G9/general", "lo.jpg")
    _drop(root, "02 - Selected/Dia 1 - d1/general", "se.jpg")
    _mark_phase_done(event, "pick")

    rng = random.Random(0)
    pick = pick_random_last_phase_photo(event, rng=rng)
    assert pick is not None
    assert pick.name == "se.jpg"


def test_pick_random_last_phase_photo_none_when_no_phase(tmp_path):
    event = _make_event(tmp_path)
    pick = pick_random_last_phase_photo(event)
    assert pick is None


def test_phase_funnel_breakdown_pipeline_order_and_percent(tmp_path):
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    # 4 captured.
    for i in range(4):
        _drop(root, "00 - Captured/_cameras/Dia 1 - d1/G9", f"c{i}.jpg")
    # 2 culled, 1 selected, 1 processed, 0 curated.
    _drop(root, "01 - Culled/_cameras/Dia 1 - d1/G9/general", "k0.jpg")
    _drop(root, "01 - Culled/_cameras/Dia 1 - d1/G9/general", "k1.jpg")
    _drop(root, "02 - Selected/Dia 1 - d1/general", "s0.jpg")
    _drop(root, "03 - Processed/Dia 1 - d1/general", "p0.jpg")

    bars = phase_funnel_breakdown(event)
    # 5 bars in pipeline order.
    labels = [b[0] for b in bars]
    assert labels == [
        "Captured", "Culled", "Selected", "Processed", "Curated",
    ]
    counts = {b[0]: b[1] for b in bars}
    assert counts == {
        "Captured":  4, "Culled":    2, "Selected":  1,
        "Processed": 1, "Curated":   0,
    }
    pcts = {b[0]: b[2] for b in bars}
    assert pcts["Captured"] == pytest.approx(100.0)
    assert pcts["Culled"] == pytest.approx(50.0)
    assert pcts["Selected"] == pytest.approx(25.0)
    assert pcts["Curated"] == pytest.approx(0.0)


def test_phase_funnel_breakdown_empty_when_no_captured(tmp_path):
    """No 00 - Captured tree → no baseline → empty tuple (the bar
    quadrant paints its empty-state hint)."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "01 - Culled/_cameras/Dia 1 - d1/G9/general", "k.jpg")
    bars = phase_funnel_breakdown(event)
    assert bars == ()


# ── F-030 — captured-per-camera fallback ───────────────────────


from core.event_stats import (                       # noqa: E402
    captured_per_camera_counts,
)


def test_captured_per_camera_counts_sorted_by_count_desc(tmp_path):
    """Two cameras with different file counts → row order is the
    most-shot camera first, mirroring the cull dashboard's sort
    so the user's mental ranking stays consistent across surfaces."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    # DC-G9: 5 files across two days.
    for i in range(3):
        _drop(root, "00 - Captured/_cameras/Dia 1 - d1/DC-G9", f"a{i}.jpg")
    for i in range(2):
        _drop(root, "00 - Captured/_cameras/Dia 2 - d2/DC-G9", f"b{i}.jpg")
    # HERO12: 2 files.
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/HERO12", "v1.mp4")
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/HERO12", "v2.mp4")
    # iPhone: 4 files (phones bucket).
    for i in range(4):
        _drop(root, "00 - Captured/_phones/Dia 1 - d1/iPhone", f"p{i}.jpg")

    rows = captured_per_camera_counts(event)
    # Sorted by descending count: G9 (5) > iPhone (4) > HERO12 (2).
    assert rows == (("DC-G9", 5), ("iPhone", 4), ("HERO12", 2))


def test_captured_per_camera_counts_counts_photos_and_videos(tmp_path):
    """Nelson's framing: 'total file count (photos + videos) per
    camera'. The helper should count BOTH and not filter by
    extension."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/G9", "a.jpg")
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/G9", "b.rw2")
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/G9", "c.mp4")

    rows = captured_per_camera_counts(event)
    assert rows == (("G9", 3),)


def test_captured_per_camera_counts_empty_when_no_captured(tmp_path):
    """Event with no Captured tree yields empty tuple — the cell
    falls through to the generic 'no photos captured yet' hint."""
    event = _make_event(tmp_path)
    rows = captured_per_camera_counts(event)
    assert rows == ()


def test_captured_per_camera_counts_stable_order_on_tie(tmp_path):
    """When two cameras have identical counts the alphabetic
    camera_id breaks the tie — pins deterministic UI ordering
    across reloads."""
    event = _make_event(tmp_path)
    root = Path(event.photos_base_path)
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/Zebra", "z.jpg")
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/Apple", "a.jpg")
    _drop(root, "00 - Captured/_cameras/Dia 1 - d1/Mango", "m.jpg")

    rows = captured_per_camera_counts(event)
    assert [name for name, _n in rows] == ["Apple", "Mango", "Zebra"]
