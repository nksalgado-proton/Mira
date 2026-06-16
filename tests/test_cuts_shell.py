"""spec/61 slice 6 + spec/70 Phase 3 §5 — Surface 09 ShareCutsPage chassis
(Share landing) + audio-library feeds.

The chassis is driven with a duck-typed app gateway over the real
event.db fixture: rows render from live gateway reads, the New Cut
dialog kwargs wire the real probes, sessions mount/unmount in the
stack, and Back closes the per-event gateway.

History: pre-spec/70 these tests pointed at ``mira.ui.shared.cuts_shell``
when the chassis was a separate host wrapping the redesigned list. The
route swap (Phase 3 §5) folded the chassis into the redesigned page; the
class names map ``CutsShellPage`` → :class:`ShareCutsPage`. The test
file name stays so the test history is continuous.
"""
from __future__ import annotations

import itertools
import random

import pytest

from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy

from core import audio_library
from mira.gateway.event_gateway import EventGateway
from mira.settings.model import Settings
from mira.shared.cut_session import CutSession
from mira.store.repo import EventStore
from mira.ui.pages.share_cuts_page import (
    CutRow,
    CutSnapshot,
    ShareCutsPage,
    _RenameCutDialog,
)

from tests.test_cut_session import _draft
from tests.test_gateway_cuts import _doc, _now


class _FakeAppGateway:
    """Duck-type of the app Gateway: settings + open_event."""

    def __init__(self, eg, settings: Settings) -> None:
        self._eg = eg
        self.settings = settings

    def open_event(self, event_id: str):
        return self._eg


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    # Materialise the on-disk bytes for every Exported Media/ lineage row so
    # the rescan prune (filesystem is the source of truth for the exported
    # tier) keeps them on Share entry instead of reconciling the pool to empty.
    for (rel,) in store.conn.execute(
            "SELECT export_relpath FROM lineage "
            "WHERE export_relpath LIKE 'Exported Media/%'").fetchall():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\xff\xd8\xff\xd9")
    counter = itertools.count(1)
    g = EventGateway(store, event_root=tmp_path, now=_now,
                     new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


def _shell(gw, **settings_over) -> ShareCutsPage:
    settings = Settings(**settings_over)
    shell = ShareCutsPage(_FakeAppGateway(gw, settings))
    assert shell.open_event("evt-c")
    return shell


# --------------------------------------------------------------------------- #
# The list — Surface 09 redesign: probes the ShareCutsPage snapshots
# --------------------------------------------------------------------------- #


def test_open_event_builds_exported_plus_cut_snapshots(qapp, gw, tmp_path):
    shell = _shell(gw)
    # The shell feeds the redesigned list page via setForPreview; assert
    # the snapshots carry the right shape.
    pool = shell.list_page._pool          # noqa: SLF001 — test introspection
    cuts = shell.list_page._cuts          # noqa: SLF001
    assert pool.exported_count == 5
    assert len(cuts) == 1
    cut = cuts[0]
    assert cut.name == "short_version"
    assert cut.item_count == 1
    # 1 photo + 1 separator × 6 s = 12 s
    assert cut.duration_seconds == 12
    # `created_at` is None for never-exported in the fake gateway
    assert cut.exported_date == ""


def test_no_user_cuts_yields_empty_cuts_list(qapp, gw, tmp_path):
    shell = _shell(gw)
    gw.delete_cut("cut-s")
    shell.refresh()
    assert shell.list_page._cuts == []     # noqa: SLF001
    assert shell.list_page._pool.exported_count == 5   # noqa: SLF001


def test_separators_setting_off_changes_duration(qapp, gw, tmp_path):
    shell = _shell(gw, use_separators=False)
    cut = shell.list_page._cuts[0]         # noqa: SLF001
    # 1 photo only, no separator card -> 6 s
    assert cut.duration_seconds == 6


def test_cut_row_is_fixed_height_and_list_scrolls(qapp, gw):
    """spec/61 §3 + Nelson 2026-06-15: rows are a fixed height so the
    list scrolls when it overflows — without this the rows balloon to
    fill the viewport and there is no scrolling. The cuts layout sits
    inside a ``QScrollArea`` and is the cuts-list's load-bearing seam."""
    row = CutRow(CutSnapshot(cut_id="c1", name="anything", item_count=3))
    # Fixed height = setFixedHeight collapses min/max to the same value;
    # vertical size policy refuses to grow beyond sizeHint.
    assert row.minimumHeight() == CutRow.ROW_HEIGHT
    assert row.maximumHeight() == CutRow.ROW_HEIGHT
    assert row.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed

    shell = _shell(gw)
    list_view = shell.list_page                # noqa: SLF001
    # The cuts area is wrapped in a QScrollArea; without it overflow
    # would have nowhere to go.
    scrolls = list_view.findChildren(QScrollArea)
    assert any(
        s.widget() is not None
        and s.widget().layout() is list_view._cuts_layout   # noqa: SLF001
        for s in scrolls
    )


# --------------------------------------------------------------------------- #
# Dialog kwargs — the real probes
# --------------------------------------------------------------------------- #


def test_dialog_kwargs_wire_gateway_feeds(qapp, gw, tmp_path):
    shell = _shell(gw)
    kw = shell._dialog_kwargs()
    assert kw["exported_count"] == 5
    assert kw["existing_cuts"] == [("short_version", 1)]
    assert kw["style_options"] == ["macro", "wildlife"]
    assert kw["event_label"] == "Cuts fixture"
    assert kw["pool_probe"]([("+", "exported")]) == 5
    totals = kw["totals_probe"]([("+", "exported")], [], "both")
    assert totals.photo_count == 4 and totals.video_count == 1
    # no audio path set → the pointer-to-Settings hint
    assert "Settings" in kw["music_hint"]


def test_settings_repo_is_loaded_not_attribute_read(qapp, gw, tmp_path):
    """Nelson eyeball 2026-06-12 finding #1: gateway.settings is the
    REPO — attribute reads silently defaulted (audio path always empty).
    The shell must load() it; a set path now reaches the dialog."""
    class _RepoDuck:
        def __init__(self, settings): self._s = settings
        def load(self): return self._s

    lib = tmp_path / "lib"
    (lib / "happy").mkdir(parents=True)
    (lib / "calm").mkdir()
    settings = Settings(audio_library_path=str(lib))
    shell = ShareCutsPage(_FakeAppGateway(gw, None))
    shell.gateway.settings = _RepoDuck(settings)
    assert shell.open_event("evt-c")
    kw = shell._dialog_kwargs()
    assert kw["music_categories"] == ["calm", "happy"]
    assert kw["music_hint"] is None
    # path set but NO category subfolders → the diagnostic hint
    empty = tmp_path / "empty_lib"
    empty.mkdir()
    shell.gateway.settings = _RepoDuck(Settings(audio_library_path=str(empty)))
    kw2 = shell._dialog_kwargs()
    assert kw2["music_categories"] == []
    assert str(empty) in kw2["music_hint"]


# --------------------------------------------------------------------------- #
# Session mount / unmount
# --------------------------------------------------------------------------- #


def test_session_flow_commits_and_returns_to_list(qapp, gw, tmp_path):
    shell = _shell(gw)
    session = CutSession.from_draft(gw, _draft())
    shell._start_session(session)
    assert shell._stack.currentWidget() is shell._session_page
    shell._session_page._session.set_state("Exported Media/e2.jpg", True)
    shell._session_page._on_create()
    assert shell._session_page is None
    assert shell._stack.currentWidget() is shell.list_page
    assert any(c.name == "passaros_2026" for c in shell.list_page._cuts)   # noqa: SLF001


def test_adjust_goes_dialog_first_then_session(qapp, gw, tmp_path):
    """Adjust (round 3) routes through the EDIT dialog — stubbed here
    (tests must never exec a real modal) — then the session seeds from
    membership and carries the dialog's draft."""
    shell = _shell(gw)
    seen = {}

    def fake_dialog(prefill, kwargs):
        seen["prefill"] = prefill
        seen["existing"] = kwargs["existing_cuts"]
        from tests.test_cut_session import _draft
        return _draft(name="short_version", tag="short_version",
                      target_s=240, card_style="single")

    shell._exec_edit_dialog = fake_dialog
    shell._on_adjust_cut("cut-s")
    # the dialog saw the cut's recipe, with itself excluded from "taken"
    assert seen["prefill"].name == "short_version"
    assert seen["existing"] == []
    page = shell._session_page
    assert page is not None and page._session.cut_id == "cut-s"
    assert page._session.is_picked("Exported Media/e1.jpg")
    assert page._session.target_s == 240            # the dialog's new value
    page._on_cancel()                               # no decisions → no confirm
    assert shell._session_page is None
    assert shell._stack.currentWidget() is shell.list_page


def test_back_emits_closed(qapp, gw, tmp_path):
    shell = _shell(gw)
    got = []
    shell.closed.connect(lambda: got.append(True))
    shell._on_back()
    assert got == [True]


# --------------------------------------------------------------------------- #
# Rename dialog (the form grammar travels)
# --------------------------------------------------------------------------- #


def test_rename_dialog_preview_and_gating(qapp):
    dlg = _RenameCutDialog("short_version", ["short_version", "family"])
    dlg._edit.setText("Exported")
    assert "reserved" in dlg._preview.text() and not dlg._ok.isEnabled()
    dlg._edit.setText("Family")
    assert "taken" in dlg._preview.text() and not dlg._ok.isEnabled()
    dlg._edit.setText("Nova Versão")
    assert "#nova_versao" in dlg._preview.text() and dlg._ok.isEnabled()
    assert dlg.new_name() == "Nova Versão"


# --------------------------------------------------------------------------- #
# Audio library feeds (spec/61 §5.3)
# --------------------------------------------------------------------------- #


def test_list_moods_flat_and_nested_forms(tmp_path):
    assert audio_library.list_moods(None) == []
    assert audio_library.list_moods(tmp_path / "missing") == []
    flat = tmp_path / "flat"
    for mood in ("happy", "calm"):
        (flat / mood).mkdir(parents=True)
    (flat / "sfx").mkdir()                         # excluded by name
    assert audio_library.list_moods(flat) == ["calm", "happy"]
    nested = tmp_path / "nested"
    for mood in ("samba", "80s"):
        (nested / "music" / mood).mkdir(parents=True)
    (nested / "sfx" / "nature").mkdir(parents=True)
    assert audio_library.list_moods(nested) == ["80s", "samba"]


def _track(name: str, secs: float) -> audio_library.AudioTrack:
    from pathlib import Path
    return audio_library.AudioTrack(
        path=Path(name), kind=audio_library.AudioKind.MUSIC,
        mood="happy", duration_seconds=secs)


def test_build_playlist_covers_and_includes_crossing_file():
    tracks = [_track("a.mp3", 60), _track("b.mp3", 90), _track("c.mp3", 120)]
    out = audio_library.build_playlist(tracks, 100, rng=random.Random(1))
    total = sum(t.duration_seconds for t in tracks[:0] or out)
    assert total >= 100                            # always "a bit more"
    # the file that crossed the threshold is INCLUDED (trim room in PTE)
    assert sum(t.duration_seconds for t in out[:-1]) < 100


def test_build_playlist_short_library_returns_all():
    tracks = [_track("a.mp3", 30), _track("b.mp3", 40)]
    out = audio_library.build_playlist(tracks, 600, rng=random.Random(7))
    assert len(out) == 2
    assert audio_library.build_playlist([], 600) == []
    assert audio_library.build_playlist(tracks, 0) == []
