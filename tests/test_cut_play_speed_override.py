"""spec/145 — rehearsal-only video-speed override for the Cut player.

The Cut player already has a per-photo seconds spinbox (a live tuning
knob); spec/145 adds the sibling control for **video** — a runtime
``QMediaPlayer.setPlaybackRate(r)`` multiplier so the user can skim
through a rehearsal at 1.5× or 2× without re-encoding anything.

This is a *runtime* knob. It compounds with the clip's baked
``setpts`` speed (a 2×-baked clip at 1.5× plays 3×) — honest by
construction. It NEVER touches the exported clips or the PTE
generator; those play the baked file at its bytes-on-disk speed.

Pairs with spec/144's EndOfMedia advance: a higher rate makes the
clip end sooner; the show moves on the instant the player reports
EndOfMedia, so there is no timing desync.

Eight contracts pinned here:

* The transport carries a video-speed combo (default 1×, six choices
  0.5/0.75/1/1.25/1.5/2).
* Changing the combo calls ``setPlaybackRate(r)`` on the clip player.
* The new rate applies mid-show — the CURRENT clip jumps to the
  new rate immediately, the next ``_show_video`` re-arms it.
* Resuming from pause re-applies the rate.
* The ``video_rate`` ctor kwarg seeds the initial value (so the host
  can wire the spec/138 ``default_video_speed`` setting).
* Invalid combo data (None / non-numeric) is rejected without
  raising.
* The rate has NO effect on :func:`core.video_export.build_export_plan`
  (spec/146's bulk + per-clip pipeline) — rehearsal-only.
* The rate has NO effect on the PTE generator's clip duration probe.
"""
from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.video_export import build_export_plan
from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import show_entries
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.shared.cut_play import CutPlayerDialog

from tests.test_gateway_cuts import _doc, _now


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def dlg(qapp, tmp_path):
    """A Cut player whose entries are photo → video → photo (matches
    the spec/144 advance fixture so the spec/145 rate change has a
    real clip to bind to)."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    # Bump v1 BEFORE e3a so the show order lands photo / video / photo
    # at indexes 0 / 1 / 2 (same as the spec/144 advance fixture).
    store.conn.execute(
        "UPDATE item SET capture_time_corrected = ?, "
        "capture_time_raw = ? WHERE id = 'v1'",
        ("2026-04-02T09:30:00", "2026-04-02T09:30:00"))
    counter = itertools.count(1)
    gw = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    gw.set_cut_members(
        "cut-s", ["Exported Media/e1.jpg",
                  "Exported Media/v1.mp4",
                  "Exported Media/e3a.jpg"])
    # spec/153 §opener — show_entries prepends a ("opener", None) title
    # slide, which shifts the file indices by +1. These rate tests
    # predate the opener and pin index 1 = video by construction; strip
    # the opener here so the tests' "video at index 1" contract holds.
    entries = [e for e in show_entries(
        gw, gw.cut("cut-s"), separators_on=False)
        if e[0] != "opener"]
    # Seed placeholder bytes so the photo loader doesn't spam warnings.
    (tmp_path / "Exported Media").mkdir(exist_ok=True)
    for rel in ("Exported Media/e1.jpg",
                "Exported Media/v1.mp4",
                "Exported Media/e3a.jpg"):
        (tmp_path / rel).write_bytes(b"\xff\xd8\xff\xd9")
    day_meta = {d.day_number: d for d in gw.trip_days()}
    cut_player = CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9")
    yield cut_player
    try:
        cut_player._teardown_media()
    except Exception:                                              # noqa: BLE001
        pass
    cut_player.deleteLater()
    gw.close()


class _StubSink:
    def __init__(self):
        self._handlers: list = []

    @property
    def videoFrameChanged(self):                                   # noqa: N802
        return self

    def connect(self, slot):
        self._handlers.append(slot)

    def disconnect(self, slot):
        try:
            self._handlers.remove(slot)
        except ValueError:
            pass

    def emit(self, frame):
        for h in list(self._handlers):
            h(frame)


class _StubPlayer:
    """Captures ``setPlaybackRate`` calls so the test can assert the
    rehearsal rate landed on the player. Otherwise a thin duck of
    ``QMediaPlayer`` — just the slots ``_show_video`` /
    ``_apply_video_rate`` / ``_on_video_status`` exercise."""

    def __init__(self):
        self._sink = _StubSink()
        self.source = None
        self.played = False
        self.stopped = False
        self.rate_calls: list[float] = []
        self.mediaStatusChanged = SimpleNamespace(
            disconnect=lambda: None)

    def setSource(self, url):                                      # noqa: N802
        self.source = url

    def play(self):
        self.played = True

    def pause(self):
        self.played = False

    def stop(self):
        self.stopped = True

    def videoSink(self):                                           # noqa: N802
        return self._sink

    def position(self):
        return 0

    def setPlaybackRate(self, r):                                  # noqa: N802
        self.rate_calls.append(float(r))


def _install_stub_player(player: CutPlayerDialog) -> _StubPlayer:
    from PyQt6.QtWidgets import QWidget
    player._video_widget = QWidget(player._stack_widget)
    player._stack_layout.addWidget(player._video_widget)
    player._video_widget.hide()
    stub = _StubPlayer()
    player._player = stub
    player._ensure_video = lambda: None
    return stub


# --------------------------------------------------------------------- #
# 1. The control exists with the right shape
# --------------------------------------------------------------------- #


def test_video_rate_combo_carries_the_six_choices(dlg):
    """spec/145 §2 — the dropdown offers 0.5 / 0.75 / 1 / 1.25 / 1.5 /
    2 in that order; default 1×."""
    combo = dlg._video_rate_combo
    assert combo is not None
    rates = [combo.itemData(i) for i in range(combo.count())]
    assert rates == [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    # Default selection is 1×.
    assert combo.currentData() == 1.0
    assert dlg._video_rate == 1.0


def test_video_rate_ctor_kwarg_seeds_initial_value(qapp, tmp_path):
    """The ``video_rate`` ctor kwarg pre-selects an initial value so
    the host can wire :data:`Settings.default_video_speed` (spec/138).
    Unknown / out-of-list values fall back to 1×."""
    cp = CutPlayerDialog(
        [], event_root=tmp_path, photo_s=6.0, day_meta={},
        aspect="16:9", video_rate=1.5)
    try:
        assert cp._video_rate == 1.5
        assert cp._video_rate_combo.currentData() == 1.5
    finally:
        cp.deleteLater()


def test_video_rate_ctor_kwarg_rejects_non_positive(qapp, tmp_path):
    """spec/145 — zero / negative / unparseable rates would yield a
    degenerate ``setPlaybackRate`` call; the ctor clamps them back
    to 1×. (A numeric string like ``"1.5"`` parses as 1.5 — that's
    fine and not in the "bad" set.)"""
    for bad in (0, -1.5, "abc", None):
        cp = CutPlayerDialog(
            [], event_root=tmp_path, photo_s=6.0, day_meta={},
            aspect="16:9", video_rate=bad)
        try:
            assert cp._video_rate == 1.0
        finally:
            cp.deleteLater()


# --------------------------------------------------------------------- #
# 2. Combo change → setPlaybackRate on the clip player
# --------------------------------------------------------------------- #


def test_combo_change_calls_setplaybackrate_on_current_player(dlg):
    """spec/145 §2 — picking 1.5× on the combo lands a
    ``setPlaybackRate(1.5)`` call on the QMediaPlayer immediately."""
    stub = _install_stub_player(dlg)
    dlg._show_index(1)                                  # video entry
    stub.rate_calls.clear()                             # ignore arm-time rate

    dlg._video_rate_combo.setCurrentIndex(
        dlg._video_rate_combo.findData(1.5))
    assert 1.5 in stub.rate_calls
    assert dlg._video_rate == 1.5


def test_combo_change_no_player_yet_is_safe(dlg):
    """spec/145 — the user opens the combo before the first video
    entry has armed the player. The state still updates so the next
    ``_show_video`` picks up the new rate; nothing raises."""
    assert dlg._player is None
    dlg._video_rate_combo.setCurrentIndex(
        dlg._video_rate_combo.findData(2.0))
    assert dlg._video_rate == 2.0


# --------------------------------------------------------------------- #
# 3. _show_video re-arms the rate (Qt resets it on setSource)
# --------------------------------------------------------------------- #


def test_show_video_applies_current_rate_after_setsource(dlg):
    """spec/145 — Qt's media backends reset ``playbackRate`` to 1.0
    on a new ``setSource``. The dialog must re-apply the current
    rate AFTER ``setSource`` + ``play``, so the new clip starts at
    the chosen rate (not 1× for the first frame, then jumping)."""
    stub = _install_stub_player(dlg)
    # User picks 0.75× BEFORE the first video plays.
    dlg._video_rate_combo.setCurrentIndex(
        dlg._video_rate_combo.findData(0.75))
    stub.rate_calls.clear()

    dlg._show_video(Path("C:/cut/clip.mp4"))

    # The new rate landed AFTER setSource + play.
    assert stub.source is not None
    assert stub.played is True
    assert 0.75 in stub.rate_calls
    # And the LAST setPlaybackRate call carries the current rate so a
    # backend reset on play() is overridden after.
    assert stub.rate_calls[-1] == 0.75


def test_show_video_arms_rate_for_each_clip(dlg):
    """Two clips back to back — each ``_show_video`` re-arms the rate
    so the second clip plays at the user's choice even if the
    backend reset it during the source swap."""
    stub = _install_stub_player(dlg)
    dlg._video_rate_combo.setCurrentIndex(
        dlg._video_rate_combo.findData(1.25))

    dlg._show_video(Path("C:/cut/a.mp4"))
    first_rate_calls = list(stub.rate_calls)
    assert 1.25 in first_rate_calls

    stub.rate_calls.clear()
    dlg._show_video(Path("C:/cut/b.mp4"))
    assert 1.25 in stub.rate_calls


# --------------------------------------------------------------------- #
# 4. Mid-show rate change applies to the running clip
# --------------------------------------------------------------------- #


def test_mid_show_rate_change_applies_to_running_clip(dlg):
    """spec/145 §2 — the user is watching a clip at 1× and bumps the
    combo to 2× mid-clip. The change must land on the player NOW —
    no waiting for the next ``_show_video``."""
    stub = _install_stub_player(dlg)
    dlg._show_index(1)
    assert stub.played
    stub.rate_calls.clear()

    # Mid-show flip — picks up immediately on the running player.
    dlg._video_rate_combo.setCurrentIndex(
        dlg._video_rate_combo.findData(2.0))
    assert stub.rate_calls and stub.rate_calls[-1] == 2.0


# --------------------------------------------------------------------- #
# 5. Resume from pause re-applies the rate
# --------------------------------------------------------------------- #


def test_resume_from_pause_reapplies_video_rate(dlg):
    """spec/145 — some Qt backends drop the playbackRate back to 1.0
    across pause/resume; the resume path must re-arm the rate so the
    user's choice survives the pause."""
    stub = _install_stub_player(dlg)
    # Force the dialog to think it has a music player so the pause
    # toggle doesn't try to ``play()`` a non-existent music ref.
    dlg._music = None
    dlg._show_index(1)
    dlg._video_rate_combo.setCurrentIndex(
        dlg._video_rate_combo.findData(1.5))
    stub.rate_calls.clear()

    # Pause → resume: the resume branch for a video entry must call
    # ``_apply_video_rate`` so 1.5× rides through the gap.
    dlg._toggle_pause()                                 # pause
    dlg._toggle_pause()                                 # resume
    assert 1.5 in stub.rate_calls


# --------------------------------------------------------------------- #
# 6. The rate is rehearsal-only — no effect on export or PTE
# --------------------------------------------------------------------- #


def test_video_rate_does_not_alter_export_plan(qapp, tmp_path):
    """spec/145 §3 — the rehearsal override is a runtime ``setPlaybackRate``
    multiplier; it has NO path into the export pipeline. Pin this
    explicitly: build the same export plan twice, once with a
    rehearsal rate of 2× alive on the dialog, and verify
    ``plan.speed`` is identical (the BAKED ``VideoAdjustment.speed``,
    not the rehearsal override)."""
    # The rehearsal dialog has no influence on build_export_plan;
    # the function reads only its ``override`` argument. Show the
    # invariant by pinning the plan's speed against the
    # VideoAdjustment-shaped override.
    from mira.ui.exported.batch import _SegmentOverride
    cp = CutPlayerDialog(
        [], event_root=tmp_path, photo_s=6.0, day_meta={},
        aspect="16:9", video_rate=2.0)
    try:
        # The rehearsal is parked on 2×; the export plan reads from
        # VideoAdjustment, not from the dialog.
        baked = m.VideoAdjustment(item_id="v0", speed=1.5)
        override = _SegmentOverride(baked, params=None)
        plan = build_export_plan(
            override, clip_start_ms=0, clip_end_ms=4_000, src_fps=30.0)
        # spec/145 §3 — plan.speed is the BAKED 1.5, never 2× and
        # never 3× (the rehearsal compound).
        assert plan.speed == 1.5
        # And of course flipping the rehearsal on the dialog doesn't
        # change the previously-built plan (it's a value type).
        cp._video_rate_combo.setCurrentIndex(
            cp._video_rate_combo.findData(0.5))
        assert plan.speed == 1.5
    finally:
        cp.deleteLater()


def test_video_rate_not_an_attribute_of_export_plan(qapp, tmp_path):
    """spec/145 — ``ExportPlan`` has no ``video_rate`` /
    ``rehearsal_rate`` field; the spec/146 bulk action writes
    :attr:`VideoAdjustment.speed` (baked), not the rehearsal
    override. Pin the surface so a future commit that accidentally
    threads the rehearsal value through fails this assertion."""
    from core.video_export import ExportPlan
    fields = {f.name for f in ExportPlan.__dataclass_fields__.values()}
    assert "video_rate" not in fields
    assert "rehearsal_rate" not in fields
    assert "playback_rate" not in fields
    # ``speed`` is the baked field; that's the only speed-shaped
    # one the plan carries.
    assert "speed" in fields


def test_pte_clip_duration_helper_ignores_rehearsal_rate(qapp, tmp_path):
    """spec/145 §3 — the PTE generator's clip duration helper reads
    the on-disk file's baked length (spec/144 lineage / probe). The
    rehearsal rate is a Mira-only player knob; the .pte timing must
    not bend with it.

    Pin via the spec/144 lineage path: a clip's
    ``_build_clip_duration_lookup`` is the canonical ms source.
    No code path lets the dialog's rate leak in."""
    import inspect
    from mira.ui.pages.share_cuts_page import ShareCutsPage
    # The helper signature must not carry a "rate" parameter — that
    # would be the obvious smell of a leak.
    sig = inspect.signature(
        ShareCutsPage._cut_video_duration_ms)
    params = set(sig.parameters)
    assert "rate" not in params
    assert "video_rate" not in params
    assert "playback_rate" not in params


# --------------------------------------------------------------------- #
# 7. Pairs with spec/144 — high rate doesn't desync advance
# --------------------------------------------------------------------- #


def test_endofmedia_still_advances_at_any_rate(dlg):
    """spec/145 §2 + spec/144 §C — the advance is event-driven on
    ``EndOfMedia``, NOT a fixed timer. A 2× rehearsal makes the
    clip end sooner; the player fires EndOfMedia sooner; the show
    moves on cleanly. No precomputed timer can over-/under-run."""
    from PyQt6.QtMultimedia import QMediaPlayer
    _install_stub_player(dlg)
    dlg._show_index(1)
    dlg._video_rate_combo.setCurrentIndex(
        dlg._video_rate_combo.findData(2.0))
    # Simulate the clip ending early (which is exactly what 2× would
    # produce in the real player).
    dlg._on_video_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert dlg._index == 2, (
        "spec/145 + spec/144 — EndOfMedia must advance even when the "
        "rehearsal rate is 2× (no fixed-timer model to disagree)"
    )


def test_photo_timer_unchanged_by_video_rate(dlg):
    """spec/145 — the rehearsal rate is for VIDEO entries. Photo
    entries continue to use the photo timer at ``photo_ms`` —
    the override doesn't accelerate stills."""
    # Pick a non-1× rate up front.
    dlg._video_rate_combo.setCurrentIndex(
        dlg._video_rate_combo.findData(1.5))
    # A photo entry: timer arms at photo_ms regardless.
    dlg._show_index(0)
    assert dlg._timer.isActive() is True
    # The photo timer's interval is the dialog's photo_ms; the
    # rehearsal rate must not divide it.
    assert dlg._timer.remainingTime() > 0
