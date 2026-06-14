"""spec/63 §8 — the 5d safety net for ``PickPhotoSurface`` (tests-first).

The Picker photo surface is Nelson's primary cull surface and had zero
tests. This module nets the behaviours that must SURVIVE the atomic
MediaCanvas→PhotoViewport rewrite (5d): navigation + edge routing,
decision persistence through the gateway, sharpness compute-once +
persist, and the cluster sweep (Play). Written BEFORE the rewrite, green
against the MediaCanvas implementation; 5d must keep it green.

Deliberately NOT pinned here (5d changes them by design, spec/63 §4):
the Space=cycle / Tab=cycle key bindings (locked map: Space=toggle,
C=cycle, Tab=transport), P=Play (→ Enter), Z/F zoom+peaking keys (→ the
F10 inspection lens). Decision/sweep behaviour is netted at the HANDLER
seams (``_cycle`` / ``_toggle_film`` / ``start_play``) which spec/63 §8
commits to keeping. Key-level tests cover only the stable keys
(arrows, Home/End, Esc, F11).

Fixture shape mirrors ``test_cut_session_page``: real ``event.db`` via
``EventStore.create`` + ``save_document``, real little JPEGs on disk so
decode paths run, ``EventGateway(store, event_root=tmp)`` so FK writes
(``set_phase_state`` / ``set_sharpness``) resolve. EXIF readers are
stubbed (autouse) — exiftool subprocess spawns are ~300-500 ms each on
Windows and irrelevant to the netted behaviours.
"""
from __future__ import annotations

import itertools
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtTest import QTest

from mira.gateway.event_gateway import EventGateway
from mira.picked.model import CullBucket, CullItem
from mira.picked.status import (
    BADGE_UNTOUCHED,
    STATE_CANDIDATE,
    STATE_PICKED,
    STATE_SKIPPED,
    BucketStatus,
)
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.picked.pick_photo_surface import PickPhotoSurface

FIXED_NOW = "2026-06-12T12:00:00+00:00"
N_PHOTOS = 5


def _now() -> str:
    return FIXED_NOW


@pytest.fixture(autouse=True)
def _stub_exif(monkeypatch):
    """No exiftool subprocesses. ``read_exif_single`` / ``read_exif_batch``
    are imported function-locally by the surface, so patching the module
    attributes covers the lazy path, the bulk prefetcher thread, and the
    bracket prewarm. EXIF=None → empty exposure overlay, no AF point,
    genre falls back to the classify-on-empty path — all fine for the
    netted behaviours."""
    import core.exif_reader as er

    monkeypatch.setattr(er, "read_exif_single", lambda path: None)
    monkeypatch.setattr(er, "read_exif_batch", lambda paths: [])


def _write_jpeg(path: Path, idx: int) -> None:
    """A real little JPEG with edges (text + grid) so decodes succeed and
    a sharpness score has gradients to bite on."""
    img = QImage(320, 214, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv((idx * 47) % 360, 120, 200))
    p = QPainter(img)
    p.setPen(QColor(20, 20, 20))
    for x in range(0, 320, 24):
        p.drawLine(x, 0, x, 214)
    p.setFont(QFont("Arial", 48, QFont.Weight.Bold))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, f"P{idx}")
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 90)


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-p", name="Picker net fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i in range(1, N_PHOTOS + 1):
        doc.items.append(m.Item(
            id=f"p{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/p{i}.jpg",
            sha256=f"{i:064d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ))
    return doc


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-p")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=tmp_path,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def items(tmp_path):
    out = []
    for i in range(1, N_PHOTOS + 1):
        p = tmp_path / "Original Media" / f"p{i}.jpg"
        _write_jpeg(p, i)
        out.append(CullItem(
            item_id=f"p{i}", path=p, kind="photo",
            capture_time_corrected=f"2026-04-01T08:0{i}:00"))
    return out


def _bucket(kind: str, items, key: str = "") -> CullBucket:
    n = len(items)
    return CullBucket(
        bucket_key=key or f"1|{kind}|net",
        kind=kind, title=f"{kind} net", items=tuple(items),
        status=BucketStatus(
            total=n, kept=0, candidate=0, discarded=0, untouched=n,
            reviewed=False, browsed=False, badge=BADGE_UNTOUCHED))


def _surface(gw, bucket: CullBucket, **load_kw) -> PickPhotoSurface:
    s = PickPhotoSurface()
    s.load(gw, bucket, "pick", **load_kw)
    return s


def _pump_until(qapp, cond, timeout_ms: int = 5000) -> bool:
    """Spin the event loop until ``cond()`` — lets async decode / queued
    signals land. Today's surface satisfies most conditions instantly;
    after 5d (skip-until-pixels-land sharpness) the pump is what makes
    the same assertions hold."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #


def test_load_shows_first_item_with_position(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items))
    assert s._index == 0
    assert s._position_label.text().endswith(f"1 / {N_PHOTOS}")


def test_go_advances_index_and_position(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items))
    s._go(2)
    assert s._index == 2
    assert s._position_label.text().endswith(f"3 / {N_PHOTOS}")
    s._go(s._index + 1)
    assert s._index == 3


def test_arrow_keys_step_and_home_end_jump(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items))
    QTest.keyClick(s, Qt.Key.Key_Right)
    assert s._index == 1
    QTest.keyClick(s, Qt.Key.Key_Down)
    assert s._index == 2
    QTest.keyClick(s, Qt.Key.Key_Left)
    assert s._index == 1
    QTest.keyClick(s, Qt.Key.Key_Up)
    assert s._index == 0
    QTest.keyClick(s, Qt.Key.Key_End)
    assert s._index == N_PHOTOS - 1
    QTest.keyClick(s, Qt.Key.Key_Home)
    assert s._index == 0


def test_day_grid_edges_emit_navigate_at_edge(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items), nav_context="day_grid")
    got = []
    s.navigate_at_edge.connect(got.append)
    s._go(-1)
    assert got == [-1] and s._index == 0
    s._go(N_PHOTOS - 1)
    s._go(N_PHOTOS)
    assert got == [-1, +1] and s._index == N_PHOTOS - 1


def test_cluster_edges_stop_silently(qapp, gw, items):
    """Nelson 2026-06-04: inside a cluster, navigation never escapes the
    cluster — no edge signal of any kind fires."""
    s = _surface(gw, _bucket("burst", items), nav_context="cluster")
    fired = []
    s.navigate_at_edge.connect(lambda d: fired.append(("grid", d)))
    s.prev_bucket_from_first_photo.connect(lambda: fired.append("prev"))
    s.next_bucket_from_last_photo.connect(lambda: fired.append("next"))
    s._go(-1)
    s._go(N_PHOTOS - 1)
    s._go(N_PHOTOS)
    assert fired == []
    assert s._index == N_PHOTOS - 1


def test_bucket_edges_fire_legacy_signals_gated_by_day_flags(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items),
                 is_first_in_day=False, is_last_in_day=True)
    fired = []
    s.prev_bucket_from_first_photo.connect(lambda: fired.append("prev"))
    s.next_bucket_from_last_photo.connect(lambda: fired.append("next"))
    s._go(-1)                       # not first in day → crossing allowed
    assert fired == ["prev"]
    s._go(N_PHOTOS - 1)
    s._go(N_PHOTOS)                 # last in day → hard stop
    assert fired == ["prev"]


def test_entry_override_lands_first_or_last(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items), entry_override=0)
    assert s._index == 0
    s.load(gw, _bucket("moment", items), "pick", entry_override=-1)
    assert s._index == N_PHOTOS - 1


def test_resume_cursor_restored_on_load_and_saved_on_back(qapp, gw, items):
    bucket = _bucket("moment", items, key="1|moment|cursor")
    gw.set_bucket_current_index(bucket.bucket_key, "pick", 2)
    s = _surface(gw, bucket)
    assert s._index == 2                       # restored
    s._go(3)
    backs = []
    s.back_requested.connect(lambda: backs.append(True))
    QTest.keyClick(s, Qt.Key.Key_Escape)       # Esc = one level back
    assert backs == [True]
    assert gw.bucket(bucket.bucket_key, "pick").current_index == 3


# --------------------------------------------------------------------------- #
# Decision persistence (the gateway seam)
# --------------------------------------------------------------------------- #


def test_cycle_walks_skip_pick_compare_and_persists(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items))   # default = skipped
    iid = items[0].item_id
    assert s._effective(iid) == STATE_SKIPPED    # untouched reads default
    assert iid not in gw.phase_states("pick")    # …but no row yet

    s._cycle()
    assert s._effective(iid) == STATE_PICKED
    assert gw.phase_states("pick")[iid].state == STATE_PICKED
    assert s._state_pill.property("state") == STATE_PICKED

    s._cycle()
    assert s._effective(iid) == STATE_CANDIDATE
    assert gw.phase_states("pick")[iid].state == STATE_CANDIDATE

    s._cycle()                                   # wraps back to Skip
    assert s._effective(iid) == STATE_SKIPPED
    assert gw.phase_states("pick")[iid].state == STATE_SKIPPED
    assert s._state_pill.property("state") == STATE_SKIPPED


def test_load_restores_explicit_marks_from_gateway(qapp, gw, items):
    gw.set_phase_state("p2", "pick", STATE_PICKED)
    gw.set_phase_state("p4", "pick", STATE_CANDIDATE)
    s = _surface(gw, _bucket("moment", items))
    assert s._effective("p1") == STATE_SKIPPED   # untouched → default
    assert s._effective("p2") == STATE_PICKED
    assert s._effective("p4") == STATE_CANDIDATE


def test_default_state_picked_feeds_effective_and_cycle(qapp, gw, items):
    """The per-phase default is wired (feedback_phase_default_state_is_wired):
    untouched items READ as the configured default, and the cycle starts
    from it — never from a hardcoded 'skipped'."""
    s = _surface(gw, _bucket("moment", items), default_state=STATE_PICKED)
    iid = items[0].item_id
    assert s._effective(iid) == STATE_PICKED
    s._cycle()                                   # picked → candidate
    assert gw.phase_states("pick")[iid].state == STATE_CANDIDATE


def test_chrome_follows_the_item_on_navigation(qapp, gw, items):
    gw.set_phase_state("p2", "pick", STATE_PICKED)
    s = _surface(gw, _bucket("moment", items))
    assert s._state_pill.property("state") == STATE_SKIPPED
    s._go(1)                                     # p2 — explicit Pick
    assert s._state_pill.property("state") == STATE_PICKED
    assert "Pick" in s._state_pill.text()
    s._go(2)                                     # p3 — untouched → default
    assert s._state_pill.property("state") == STATE_SKIPPED


# --------------------------------------------------------------------------- #
# Sharpness — compute once, persist via the gateway
# --------------------------------------------------------------------------- #


def test_preseeded_sharpness_is_never_recomputed(qapp, gw, items):
    for it in items:
        gw.set_sharpness(it.item_id, 123.45)
    calls = []
    orig = gw.set_sharpness

    def spy(item_id, score, **kw):
        calls.append(item_id)
        return orig(item_id, score, **kw)

    gw.set_sharpness = spy
    s = _surface(gw, _bucket("moment", items))
    for i in range(N_PHOTOS):                    # visit every photo
        s._go(i)
    assert calls == []                           # preload made every show a hit
    assert s._sharpness_cache["p1"] == 123.45
    assert gw.item("p3").sharpness_score == 123.45


def test_unscored_item_scores_and_persists_exactly_once(qapp, gw, items):
    """First show of an unscored item persists ONE score through
    ``eg.set_sharpness``; re-showing it never recomputes. The pump lets
    pixels land: today the score is taken from whatever the canvas holds
    (the spec/62 score-the-thumb bug — value deliberately NOT asserted);
    after 5d the surface scores the decoded native pixmap, which may
    arrive a beat later. Both worlds satisfy exactly-once + persisted."""
    calls = []
    orig = gw.set_sharpness

    def spy(item_id, score, **kw):
        calls.append(item_id)
        return orig(item_id, score, **kw)

    gw.set_sharpness = spy
    s = _surface(gw, _bucket("moment", items))
    assert _pump_until(
        qapp, lambda: gw.item("p1").sharpness_score is not None)
    assert calls.count("p1") == 1

    s._go(1)
    assert _pump_until(
        qapp, lambda: gw.item("p2").sharpness_score is not None)
    assert calls.count("p2") == 1

    s._go(0)                                     # back — cached, no recompute
    qapp.processEvents()
    assert calls.count("p1") == 1
    assert "p1" in s._sharpness_cache and "p2" in s._sharpness_cache


# --------------------------------------------------------------------------- #
# The cluster sweep (Play) — load-bearing one-click path
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind,playable,combined", [
    ("burst", True, False),
    ("focus_bracket", True, False),
    ("exposure_bracket", True, True),
    ("moment", False, False),
    ("individual", False, False),
])
def test_play_and_combined_visibility_by_kind(
        qapp, gw, items, kind, playable, combined):
    s = _surface(gw, _bucket(kind, items))
    s.show()
    assert s._film_btn.isVisible() == playable
    assert s._combined_btn.isVisible() == combined


def test_start_play_runs_the_sweep_from_the_cursor(qapp, gw, items):
    """PickPage's cluster-sub-grid ▶ Play path
    (design_rule_cluster_slideshow_load_bearing) — one click starts the
    paced sweep."""
    s = _surface(gw, _bucket("burst", items))
    s.show()
    s.start_play()
    assert s._film_btn.isChecked()
    assert s._film_timer.isActive()
    # Nelson 2026-06-12 Play/Pause polish — the transport is icon-only
    # now (⏸ = playing, ▶ = stopped). The behaviour pin is unchanged;
    # the assertion follows the new glyph.
    assert s._film_btn.text() == "⏸"


def test_film_step_skips_explicit_skips_but_plays_untouched(qapp, gw, items):
    """The playable rule (Nelson 2026-06-04): only EXPLICIT Skips drop
    out of the sweep; untouched frames play even though the phase
    default reads as Skip — the user hasn't dismissed them yet."""
    gw.set_phase_state("p3", "pick", STATE_SKIPPED)
    s = _surface(gw, _bucket("burst", items))    # default skipped, p3 explicit
    s.show()
    s.start_play()
    assert s._index == 0
    s._film_step()
    assert s._index == 1                         # p2 untouched → plays
    s._film_step()
    assert s._index == 3                         # p3 explicitly skipped → jumped
    s._film_step()
    assert s._index == 4


def test_film_stops_cleanly_at_the_end_no_wrap(qapp, gw, items):
    s = _surface(gw, _bucket("burst", items))
    s.show()
    s._go(N_PHOTOS - 1)
    s.start_play()                               # at the end → rewinds (below)
    s._film_btn.setChecked(False)
    s._toggle_film()                             # stop; park at the end again
    s._go(N_PHOTOS - 2)
    s._film_btn.setChecked(True)
    s._toggle_film()
    s._film_step()
    assert s._index == N_PHOTOS - 1
    s._film_step()                               # past the last playable
    assert not s._film_timer.isActive()          # …stops, no wrap
    assert not s._film_btn.isChecked()
    assert s._film_btn.text() == "▶"
    assert s._index == N_PHOTOS - 1


def test_play_at_the_end_rewinds_to_first_playable(qapp, gw, items):
    """Nelson 2026-06-06b: Play at/past the last playable frame means
    "start over" — the cursor rewinds to the first playable frame."""
    gw.set_phase_state("p1", "pick", STATE_SKIPPED)
    s = _surface(gw, _bucket("burst", items))
    s.show()
    s._go(N_PHOTOS - 1)
    s.start_play()
    assert s._film_btn.isChecked()
    assert s._index == 1                         # p1 explicitly skipped → p2
    assert s._film_timer.isActive()


def test_start_play_noop_for_unplayable_kind_or_combined(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items))
    s.show()
    s.start_play()                               # button hidden → no-op
    assert not s._film_timer.isActive()
    assert not s._film_btn.isChecked()

    s2 = _surface(gw, _bucket("exposure_bracket", items))
    s2.show()
    s2._combined_btn.setChecked(True)
    s2._toggle_combined()                        # fused preview on
    assert s2._combined_on
    s2.start_play()                              # locked out while Combined
    assert not s2._film_timer.isActive()


def test_combined_pauses_a_running_sweep(qapp, gw, items):
    s = _surface(gw, _bucket("exposure_bracket", items))
    s.show()
    s.start_play()
    assert s._film_timer.isActive()
    s._combined_btn.setChecked(True)
    s._toggle_combined()
    assert s._combined_on
    assert not s._film_timer.isActive()          # mutually exclusive
    assert not s._film_btn.isChecked()


# --------------------------------------------------------------------------- #
# Stable keys (unchanged in the spec/63 §4 locked map) + fullscreen
# --------------------------------------------------------------------------- #


def test_f11_toggles_fullscreen_and_esc_steps_down_one_level(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items))
    flips = []
    backs = []
    s.fullscreen_changed.connect(flips.append)
    s.back_requested.connect(lambda: backs.append(True))
    QTest.keyClick(s, Qt.Key.Key_F11)
    assert s._fullscreen and flips == [True]
    QTest.keyClick(s, Qt.Key.Key_Escape)         # Esc level 1: leave fullscreen
    assert not s._fullscreen and flips == [True, False]
    assert backs == []
    QTest.keyClick(s, Qt.Key.Key_Escape)         # Esc level 2: back
    assert backs == [True]


# --------------------------------------------------------------------------- #
# Genre chrome — RETIRED 2026-06-13 (Nelson): photographic classification
# (Macro / Wildlife / Birds / Landscape / Urban-Street / None) drives Edit
# Auto correction recipes — so it only surfaces in the Edit phase. The
# Pick top bar no longer carries a genre readout or Reclassify dropdown;
# the R-key for "open Reclassify menu" is gone too. The data layer
# (gateway set_classification + the background classify_pass) stays.
# --------------------------------------------------------------------------- #


def test_genre_readout_widget_retired(qapp, gw, items):
    """Regression guard: the surface no longer exposes the genre label
    or the Reclassify button. Tests that referenced them updated in
    the same commit."""
    s = _surface(gw, _bucket("moment", items))
    assert not hasattr(s, "_genre_label")
    assert not hasattr(s, "_reclassify_btn")
    assert not hasattr(s, "_genre_cache")
    assert not hasattr(s, "_genre_review")
    # And no widget under the surface still wears the old roles.
    from PyQt6.QtWidgets import QLabel, QPushButton
    misrouted_labels = [w for w in s.findChildren(QLabel)
                        if w.objectName() == "GenreReadout"]
    misrouted_buttons = [w for w in s.findChildren(QPushButton)
                         if w.objectName() == "ReclassifyButton"]
    assert misrouted_labels == []
    assert misrouted_buttons == []


def test_r_key_no_longer_triggers_reclassify_on_pick_surface(qapp, gw, items):
    """Old binding R = open the Reclassify menu (genre override). Now R
    does nothing on the Pick photo surface (the menu is gone)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent

    s = _surface(gw, _bucket("moment", items))
    # Just verify the surface doesn't blow up + carries no Reclassify
    # method — the key handler can't reach the deleted dropdown.
    assert not hasattr(s, "_on_reclassify")
    # Type R; nothing should crash. (We don't assert state here — the R
    # key is now unbound on this surface and the event falls through.)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_R, Qt.KeyboardModifier.NoModifier)
    s.keyPressEvent(event)


# --------------------------------------------------------------------------- #
# 5d — the NEW key map (spec/63 §4), written WITH the migration.
# Everything above this line is the pre-rewrite net and passes unedited;
# this section pins the locked grammar the viewport translates to verbs.
# --------------------------------------------------------------------------- #


def test_focus_proxy_routes_surface_focus_to_the_viewport(qapp, gw, items):
    """PickPage calls ``photo.setFocus()`` — the proxy hands it to the
    viewport, the one place the key grammar lives."""
    s = _surface(gw, _bucket("moment", items))
    assert s.focusProxy() is s.viewport


def test_p_x_space_c_speak_the_locked_map(qapp, gw, items):
    """spec/63 §4 on the Picker: P picks (SET), X skips (SET), Space is
    the binary toggle, C the full Pick→Skip→Compare cycle."""
    s = _surface(gw, _bucket("moment", items))
    vp = s.viewport
    QTest.keyClick(vp, Qt.Key.Key_P)
    assert gw.phase_states("pick")["p1"].state == STATE_PICKED
    QTest.keyClick(vp, Qt.Key.Key_P)             # P is SET, not toggle
    assert gw.phase_states("pick")["p1"].state == STATE_PICKED
    QTest.keyClick(vp, Qt.Key.Key_X)
    assert gw.phase_states("pick")["p1"].state == STATE_SKIPPED
    assert s._state_pill.property("state") == STATE_SKIPPED
    QTest.keyClick(vp, Qt.Key.Key_Space)         # toggle → picked
    assert gw.phase_states("pick")["p1"].state == STATE_PICKED
    QTest.keyClick(vp, Qt.Key.Key_Space)         # toggle → skipped
    assert gw.phase_states("pick")["p1"].state == STATE_SKIPPED
    QTest.keyClick(vp, Qt.Key.Key_C)             # cycle: skip → pick
    assert gw.phase_states("pick")["p1"].state == STATE_PICKED
    QTest.keyClick(vp, Qt.Key.Key_C)             # → compare
    assert gw.phase_states("pick")["p1"].state == STATE_CANDIDATE
    QTest.keyClick(vp, Qt.Key.Key_Space)         # Compare toggles to Skip
    assert gw.phase_states("pick")["p1"].state == STATE_SKIPPED


def test_enter_runs_the_sweep_with_peaking_and_p_no_longer_plays(qapp, gw, items):
    """The legacy P-sweep moved to Enter (spec/63 §4). The Sweep carries
    the viewport's FAST stack-film peaking; pausing returns the browse
    to clean (no peaking)."""
    s = _surface(gw, _bucket("burst", items))
    s.show()
    QTest.keyClick(s.viewport, Qt.Key.Key_Return)
    assert s._film_btn.isChecked() and s._film_timer.isActive()
    assert s.viewport.is_peaking_enabled()       # sweep-with-peaking
    assert s.viewport.is_stack_film_peaking()
    QTest.keyClick(s.viewport, Qt.Key.Key_Return)
    assert not s._film_timer.isActive()
    assert not s.viewport.is_peaking_enabled()   # everyday browse: clean
    QTest.keyClick(s.viewport, Qt.Key.Key_P)     # P now PICKS, never plays
    assert not s._film_timer.isActive()
    cur = s._items[s._index].item_id
    assert gw.phase_states("pick")[cur].state == STATE_PICKED


def test_enter_inert_on_unplayable_kinds(qapp, gw, items):
    s = _surface(gw, _bucket("moment", items))
    s.show()
    QTest.keyClick(s.viewport, Qt.Key.Key_Return)
    assert not s._film_timer.isActive()
    assert not s._film_btn.isChecked()


def test_sweep_end_turns_peaking_off(qapp, gw, items):
    s = _surface(gw, _bucket("burst", items))
    s.show()
    s._go(N_PHOTOS - 2)
    s.start_play()
    assert s.viewport.is_peaking_enabled()
    s._film_step()                               # lands on the last frame
    s._film_step()                               # past the end → stops
    assert not s._film_timer.isActive()
    assert not s.viewport.is_peaking_enabled()


def test_viewport_arrows_and_wheel_navigate_with_chrome(qapp, gw, items):
    from PyQt6.QtCore import QPoint, QPointF
    from PyQt6.QtGui import QWheelEvent

    def _wheel(widget, dy):
        widget.wheelEvent(QWheelEvent(
            QPointF(1, 1), QPointF(1, 1), QPoint(0, 0), QPoint(0, dy),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False))

    s = _surface(gw, _bucket("moment", items))
    QTest.keyClick(s.viewport, Qt.Key.Key_Right)
    assert s._index == 1
    QTest.keyClick(s.viewport, Qt.Key.Key_Left)
    assert s._index == 0
    _wheel(s.viewport, -120)                     # wheel down = next
    assert s._index == 1
    _wheel(s.viewport, +120)                     # wheel up = previous
    assert s._index == 0
    assert s._position_label.text().endswith(f"1 / {N_PHOTOS}")


def test_viewport_esc_and_f11_mirror_the_stable_keys(qapp, gw, items):
    """Esc/F/F11 arrive as viewport verbs on the normal (focused) path —
    same one-level-back / fullscreen handlers as the surface fallback."""
    s = _surface(gw, _bucket("moment", items))
    flips, backs = [], []
    s.fullscreen_changed.connect(flips.append)
    s.back_requested.connect(lambda: backs.append(True))
    QTest.keyClick(s.viewport, Qt.Key.Key_F11)
    assert s._fullscreen and flips == [True]
    QTest.keyClick(s.viewport, Qt.Key.Key_Escape)
    assert not s._fullscreen and backs == []     # level 1: leave fullscreen
    QTest.keyClick(s.viewport, Qt.Key.Key_Escape)
    assert backs == [True]                       # level 2: back


def test_f10_opens_the_inspection_lens(qapp, gw, items):
    """The lens affordance on the Picker is the labelled nav-centre
    button (Nelson 2026-06-12 UI round) — the viewport's corner 🔍 is
    hidden here; F10 and the button are the same action."""
    s = _surface(gw, _bucket("moment", items))
    s.show()
    assert not s.viewport._inspect_btn.isVisible()   # corner replaced…
    assert s._fullres_btn.isVisible()                # …by the button
    QTest.keyClick(s.viewport, Qt.Key.Key_F10)
    lens = s.viewport._truth_window
    assert lens is not None and lens.isVisible()
    QTest.keyClick(lens, Qt.Key.Key_F10)         # F10 closes it again
    assert not lens.isVisible()
    s._fullres_btn.click()                       # the button = F10
    lens = s.viewport._truth_window
    assert lens is not None and lens.isVisible()
    lens.close()


def test_two_lines_under_the_canvas_nav_centre_carries_the_affordances(
        qapp, gw, items):
    """Nelson 2026-06-12 UI round: the middle TOOLS line died (it sat
    empty for plain photos); Play · Combined · Full Resolution View
    live in the nav row's centre, position stays on the compact row."""
    s = _surface(gw, _bucket("moment", items))
    s.show()
    assert not s._surface.tools.isVisibleTo(s)       # the middle line died
    assert s._position_label.isVisibleTo(s)          # line 1: position
    nav_kids = (s._film_btn, s._combined_btn,
                s._fullscreen_btn, s._fullres_btn)
    for w in nav_kids:                               # line 2: nav centre
        assert s._surface.nav.isAncestorOf(w)
    assert not s._film_btn.isVisible()               # moment: no Play…
    assert s._fullres_btn.isVisible()                # …but the lens button
    # The standard pair (Nelson 2026-06-12): same labels everywhere.
    assert s._fullscreen_btn.text() == "Full Screen"
    assert s._fullres_btn.text() == "Full Resolution"
    assert s._fullscreen_btn.isVisible()

    s2 = _surface(gw, _bucket("burst", items))
    s2.show()
    assert s2._film_btn.isVisible()                  # burst: Play joins it
    assert s2._fullres_btn.isVisible()


def test_combined_locks_navigation_and_restores_at_the_cursor(qapp, gw, items):
    """The fused preview is ONE synthetic image — per-frame nav is
    genuinely locked while it shows; toggling off restores the real
    item list at the cursor frame."""
    s = _surface(gw, _bucket("exposure_bracket", items))
    s.show()
    s._go(1)
    s._combined_btn.setChecked(True)
    s._toggle_combined()
    assert s._combined_on
    s._go(2)                                     # surface nav — locked
    assert s._index == 1
    QTest.keyClick(s.viewport, Qt.Key.Key_Right)  # viewport edge — guarded
    assert s._index == 1
    s._combined_btn.setChecked(False)
    s._toggle_combined()
    assert s.viewport.current_item().payload is s._items[1]
    s._go(2)
    assert s._index == 2
