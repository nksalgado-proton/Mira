"""Nelson 2026 — DaysGridPage whole-event proxy seed is memoised and
filters cached items.

Before: every ``open_for_day`` re-queued every photo in the event to
the background proxy builder, so the BatchProgressLine ticked through
"Creating previews — N left" on every grid reopen — even when every
proxy was already on disk.

After:
  1. The whole-event seed is a no-op for any event root already
     seeded in this DaysGridPage instance (switching between days,
     and even leaving the event and coming back).
  2. The FIRST seed for an event pre-filters cached items via
     ``resolve_proxy``; only honest cache misses reach the builder,
     so the visible counter only reflects real work to do.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage

from core import photo_proxy_cache as ppc
from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.pages.days_grid_page import DaysGridPage

FIXED_NOW = "2026-06-15T12:00:00+00:00"
N_PHOTOS = 4


def _now() -> str:
    return FIXED_NOW


def _write_jpeg(path: Path, idx: int) -> None:
    img = QImage(80, 60, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv((idx * 47) % 360, 120, 200))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 80)


def _doc(uuid: str, name: str) -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid=uuid, name=name,
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i in range(1, N_PHOTOS + 1):
        doc.items.append(m.Item(
            id=f"{uuid}-{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/x{i}.jpg",
            sha256=f"{uuid[-1]}{i:063d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ))
        doc.phase_states.append(m.PhaseState(
            item_id=f"{uuid}-{i}", phase="pick", state="picked"))
    return doc


@pytest.fixture
def event_dirs(tmp_path):
    """Two real on-disk event roots so ``resolve_proxy`` can stat the
    source files and the proxy sidecars."""
    a = tmp_path / "event_a"
    b = tmp_path / "event_b"
    for root in (a, b):
        for i in range(1, N_PHOTOS + 1):
            _write_jpeg(root / "Original Media" / f"x{i}.jpg", i)
    return a, b


@pytest.fixture
def app_gateway(event_dirs, tmp_path, monkeypatch):
    a, b = event_dirs
    EventStore.create(a / "event.db", event_id="evt-a").save_document(
        _doc("evt-a", "A"))
    EventStore.create(b / "event.db", event_id="evt-b").save_document(
        _doc("evt-b", "B"))
    gw = Gateway(settings=SettingsRepo(tmp_path / "settings.json"))
    counter = itertools.count(1)
    roots = {"evt-a": a, "evt-b": b}

    def _open_event(event_id):
        root = roots[event_id]
        store = EventStore.open(root / "event.db")
        return EventGateway(
            store, event_root=root, now=_now,
            new_id=lambda: f"id-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    return gw


def _seed_spy(monkeypatch):
    """Patch ``photo_cache().seed_proxies`` to record the calls so
    tests can assert memoization at the seed boundary."""
    from mira.ui.media import photo_cache as pc_mod
    calls: list[tuple[Path, list[tuple[Path, str]]]] = []
    real = pc_mod.photo_cache().seed_proxies

    def _spy(event_root, pairs):
        pairs = list(pairs)
        calls.append((Path(event_root), pairs))
        return real(event_root, pairs)
    monkeypatch.setattr(pc_mod.photo_cache(), "seed_proxies", _spy)
    return calls


def test_seed_runs_once_per_event_across_day_switches(
        qapp, app_gateway, monkeypatch):
    """Opening multiple days of the SAME event seeds only once — the
    user no longer sees the builder churn on every day switch."""
    calls = _seed_spy(monkeypatch)
    page = DaysGridPage(app_gateway)
    try:
        assert page.open_for_day(
            "evt-a", 1, title="Day 1", date_iso="2026-04-01")
        assert page.open_for_day(
            "evt-a", 1, title="Day 1 again", date_iso="2026-04-01")
        assert page.open_for_day(
            "evt-a", 1, title="Day 1 yet again", date_iso="2026-04-01")
        assert len(calls) == 1, (
            "DaysGridPage must memoise the whole-event proxy seed per "
            f"event root; got {len(calls)} seed calls for the same event")
    finally:
        page.close_event()
        page.deleteLater()


def test_returning_to_an_already_seeded_event_is_silent(
        qapp, app_gateway, event_dirs, monkeypatch):
    """A → B → A: the second visit to A must NOT re-seed (the
    builder already covered A once this session)."""
    calls = _seed_spy(monkeypatch)
    page = DaysGridPage(app_gateway)
    try:
        assert page.open_for_day("evt-a", 1, title="A", date_iso="2026-04-01")
        assert page.open_for_day("evt-b", 1, title="B", date_iso="2026-04-01")
        assert page.open_for_day("evt-a", 1, title="A again", date_iso="2026-04-01")

        # Exactly two seed calls: one per distinct root.
        a, b = event_dirs
        roots_called = [r for r, _ in calls]
        assert len(calls) == 2, (
            f"expected one seed per distinct event root; got {len(calls)}")
        assert set(roots_called) == {a, b}
    finally:
        page.close_event()
        page.deleteLater()


def test_cached_items_are_filtered_out_at_seed_time(
        qapp, app_gateway, event_dirs, monkeypatch):
    """When ``resolve_proxy`` reports hits for some items, those items
    must NOT reach the builder — the visible "Creating previews — N
    left" counter only reflects honest cache misses."""
    calls = _seed_spy(monkeypatch)
    a, _ = event_dirs
    # Pre-build proxies for items 1 and 3 so resolve_proxy hits them
    # (items 2 and 4 stay cache misses).
    for i in (1, 3):
        src = a / "Original Media" / f"x{i}.jpg"
        # Real sha256 must match what _doc wrote — the suffix encodes i.
        sha = f"a{i:063d}"
        ok = ppc.write_proxy(
            a, sha, src,
            jpeg_bytes=b"\xff\xd8\xff\xd9", native_w=80, native_h=60)
        assert ok

    page = DaysGridPage(app_gateway)
    try:
        assert page.open_for_day("evt-a", 1, title="A", date_iso="2026-04-01")
        assert len(calls) == 1
        _root, pairs = calls[0]
        seeded_shas = {sha for _path, sha in pairs}
        assert seeded_shas == {f"a{2:063d}", f"a{4:063d}"}, (
            "seed must filter out items whose proxy already resolves "
            "on disk so the BatchProgressLine counter only reflects "
            f"real work; got seeded shas {seeded_shas}")
    finally:
        page.close_event()
        page.deleteLater()


def test_fully_cached_event_seeds_zero_items(
        qapp, app_gateway, event_dirs, monkeypatch):
    """When EVERY proxy is already on disk the seed runs but submits
    NO pairs to the builder — the user sees no "Creating previews"
    flicker at all."""
    calls = _seed_spy(monkeypatch)
    a, _ = event_dirs
    for i in range(1, N_PHOTOS + 1):
        src = a / "Original Media" / f"x{i}.jpg"
        sha = f"a{i:063d}"
        ppc.write_proxy(
            a, sha, src,
            jpeg_bytes=b"\xff\xd8\xff\xd9", native_w=80, native_h=60)

    page = DaysGridPage(app_gateway)
    try:
        assert page.open_for_day("evt-a", 1, title="A", date_iso="2026-04-01")
        # Either no seed call at all OR a seed call with an empty pair
        # list — both satisfy "no work for the user-visible counter to
        # tick through." The implementation skips the call entirely
        # when the filtered list is empty.
        for _root, pairs in calls:
            assert pairs == [], (
                "fully-cached event must submit zero pairs to the "
                f"builder; got {len(pairs)} pairs")
    finally:
        page.close_event()
        page.deleteLater()
