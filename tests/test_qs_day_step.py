"""spec/104 — Quick Sweep day-navigator chevrons step through the QS
day axis (not the gateway, which a standalone QS doesn't have).

The bug: `_on_days_grid_step_day` early-returned when
`_current_event_id is None` and sourced its day axis from
`eg.trip_days()`. A standalone (paths-mode) Quick Sweep has neither,
so the day-pill ‹/› chevrons were inert.

These tests drive `_on_days_grid_step_day(±1)` directly and check:

* Standalone QS — +1 from day 1 lands on day 2, ‑1 from the first
  and +1 from the last are no-ops, `current_day` updates.
* Regression — non-QS, gateway event still steps through the gateway
  axis exactly as before.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway import Gateway
from mira.store import models as m
from mira.ui.shell.main_window import MainWindow


@pytest.fixture
def main_window(qapp, tmp_path, monkeypatch):
    """Same isolation pattern as the spec/102 / spec/84 §5 tests — a
    MainWindow against a tmp gateway with its own user-data dir."""
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    base = tmp_path / "lib"
    base.mkdir()
    monkeypatch.setattr("mira.paths.user_data_dir", lambda: user_data)
    monkeypatch.setattr("core.settings.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "mira.gateway.index.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "mira.settings.repo.user_data_dir", lambda: user_data)
    gw = Gateway(
        settings=SettingsRepo(user_data / "settings.json"),
        index=EventsIndex(user_data / "events_index.json"),
        user_store_path=user_data / "mira.db",
    )
    _ = gw.user_store
    gw.settings.update(photos_base_path=str(base))
    w = MainWindow(gateway=gw)
    yield w
    w.deleteLater()


def _events_root(w) -> Path:
    return Path(w.gateway.settings.load().photos_base_path)


# ── Standalone QS — the spec/104 happy path ──────────────────────


def _install_standalone_qs(w, current_day: int = 1) -> None:
    """Stand up a minimal standalone-mode QS session with 3 days.
    Patches in just enough to drive ``_on_days_grid_step_day`` without
    a real scan."""
    w._quick_sweep = {
        "mode": "standalone",
        "dest": None,
        "event_id": None,
        "state": {},
        "default": None,
        # 3 days; values are empty SourceItem lists — the step
        # handler only looks at the keys.
        "items_by_day": {1: [], 2: [], 3: []},
        "days": None,
        "current_day": current_day,
        "current_day_items": [],
    }


def test_standalone_qs_step_forward_lands_on_next_day(
    main_window, monkeypatch,
):
    """+1 from day 1 lands on day 2 (and `current_day` is updated by
    the routed `_qs_open_day` call we spy on)."""
    w = main_window
    _install_standalone_qs(w, current_day=1)
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 1)
    opened: list[int] = []

    def fake_qs_open_day(day_number: int) -> None:
        opened.append(day_number)
        # Mirror what `_qs_open_day` does on the standalone branch so
        # the session state matches what a real call would leave
        # behind.
        w._quick_sweep["current_day"] = day_number

    monkeypatch.setattr(w, "_qs_open_day", fake_qs_open_day)

    w._on_days_grid_step_day(+1)
    assert opened == [2]
    assert w._quick_sweep["current_day"] == 2


def test_standalone_qs_step_backward_lands_on_prev_day(
    main_window, monkeypatch,
):
    w = main_window
    _install_standalone_qs(w, current_day=3)
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 3)
    opened: list[int] = []
    monkeypatch.setattr(
        w, "_qs_open_day", lambda d: opened.append(d))
    w._on_days_grid_step_day(-1)
    assert opened == [2]


def test_standalone_qs_step_backward_at_first_day_is_noop(
    main_window, monkeypatch,
):
    w = main_window
    _install_standalone_qs(w, current_day=1)
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 1)
    opened: list[int] = []
    monkeypatch.setattr(
        w, "_qs_open_day", lambda d: opened.append(d))
    w._on_days_grid_step_day(-1)
    assert opened == []
    # And no gateway open attempted on the boundary no-op.
    assert w._quick_sweep["current_day"] == 1


def test_standalone_qs_step_forward_at_last_day_is_noop(
    main_window, monkeypatch,
):
    w = main_window
    _install_standalone_qs(w, current_day=3)
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 3)
    opened: list[int] = []
    monkeypatch.setattr(
        w, "_qs_open_day", lambda d: opened.append(d))
    w._on_days_grid_step_day(+1)
    assert opened == []


def test_standalone_qs_step_with_unknown_current_day_is_noop(
    main_window, monkeypatch,
):
    """Defensive guard — if the grid reports a day not in
    ``items_by_day``, the handler must not crash; it just no-ops."""
    w = main_window
    _install_standalone_qs(w, current_day=1)
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 99)
    opened: list[int] = []
    monkeypatch.setattr(
        w, "_qs_open_day", lambda d: opened.append(d))
    w._on_days_grid_step_day(+1)
    assert opened == []


# ── Regression — non-QS gateway path ─────────────────────────────


def _make_gateway_event(gw, event_id: str, root: Path) -> None:
    doc = m.EventDocument(
        event=m.Event(
            uuid=event_id, name="GW Event",
            created_at="t", updated_at="t",
            start_date="2026-04-01", end_date="2026-04-03"),
        trip_days=[
            m.TripDay(day_number=1, date="2026-04-01", tz_minutes=0),
            m.TripDay(day_number=2, date="2026-04-02", tz_minutes=0),
            m.TripDay(day_number=3, date="2026-04-03", tz_minutes=0),
        ],
        cameras=[m.Camera(camera_id="C1")],
    )
    root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, root)
    eg.close()


def test_non_qs_gateway_event_still_steps_via_gateway(
    main_window, monkeypatch,
):
    """spec/104 must NOT touch the non-QS Pick/Edit/Export path —
    when there's no `_quick_sweep` session, the handler keeps
    sourcing the day axis from the gateway."""
    w = main_window
    event_id = "evt-step-gw"
    _make_gateway_event(
        w.gateway, event_id, _events_root(w) / "gw-event")
    w._current_event_id = event_id
    # No QS session in flight.
    w._quick_sweep = None
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 1)
    routed: list[int] = []
    monkeypatch.setattr(
        w, "_on_days_lists_day_activated",
        lambda d: routed.append(d))
    w._on_days_grid_step_day(+1)
    assert routed == [2], (
        "non-QS gateway path must continue to read trip_days and "
        "route via _on_days_lists_day_activated — spec/104 is "
        "additive only")


def test_per_event_qs_falls_through_to_gateway_path(
    main_window, monkeypatch,
):
    """Per-event QS leaves ``items_by_day`` empty (the gateway IS
    the day-axis source). spec/104's QS branch guards on a populated
    ``items_by_day``, so per-event QS continues through the existing
    gateway path (today's working behaviour). The gateway path then
    routes via ``_on_days_lists_day_activated`` which itself re-checks
    ``_quick_sweep`` and dispatches to ``_qs_open_day``."""
    w = main_window
    event_id = "evt-step-per-event"
    _make_gateway_event(
        w.gateway, event_id, _events_root(w) / "per-event-event")
    w._current_event_id = event_id
    # Per-event QS session: items_by_day is empty by construction.
    w._quick_sweep = {
        "mode": "per_event", "dest": None, "event_id": event_id,
        "state": {}, "default": None,
        "items_by_day": {},                 # ← empty by design
        "days": None, "current_day": None,
        "current_day_items": [],
    }
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 1)
    routed: list[int] = []
    monkeypatch.setattr(
        w, "_on_days_lists_day_activated",
        lambda d: routed.append(d))
    qs_opened: list[int] = []
    monkeypatch.setattr(
        w, "_qs_open_day", lambda d: qs_opened.append(d))

    w._on_days_grid_step_day(+1)
    # The QS branch was skipped (items_by_day empty); the gateway
    # path ran and routed via _on_days_lists_day_activated.
    assert qs_opened == [], (
        "per-event QS must NOT use the standalone branch — its day "
        "axis is the gateway's trip_days")
    assert routed == [2]
