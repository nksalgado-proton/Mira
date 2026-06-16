"""spec/61 slice 9 — the export engine (the handoff folder).

Real files on a real temp event tree: sequence naming = chronological
sort, hardlinks (copy fallback counted separately), separator slots in
sequence via the injected writer, snapshot collision folders, missing
sources skipped honestly, the audio playlist linked + the short-library
flag, and the last_exported_at stamp.
"""
from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest

from core import audio_library
from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_export import export_cut
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now

MEMBERS = ["Exported Media/e1.jpg", "Exported Media/e3a.jpg", "Exported Media/v1.mp4"]


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    for ln in ("e1.jpg", "e2.jpg", "e3a.jpg", "e3b.jpg", "v1.mp4"):
        p = tmp_path / "Exported Media" / ln
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"FILE:" + ln.encode())
    counter = itertools.count(1)
    g = EventGateway(store, event_root=tmp_path, now=_now,
                     new_id=lambda: f"id-{next(counter)}")
    g.set_cut_members("cut-s", MEMBERS)
    yield g
    g.close()


def _sep_writer(target: Path, day) -> None:
    target.write_bytes(b"SEP:" + str(day).encode())


def _names(folder: Path) -> list:
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def test_export_sequence_names_with_opener_and_separators(gw, tmp_path):
    cut = gw.cut("cut-s")
    result = export_cut(gw, cut, event_root=tmp_path,
                        separators_on=True, separator_writer=_sep_writer,
                        opener_writer=lambda t: t.write_bytes(b"OPENER"))
    assert result.folder == tmp_path / "Cuts" / "short_version"
    assert _names(result.folder) == [
        "001_opener.jpg", "002_day1.jpg", "003_e1.jpg",
        "004_day2.jpg", "005_e3a.jpg", "006_v1.mp4"]
    assert result.linked == 3 and result.copied == 0
    assert result.separators == 3 and result.missing == []
    # linked, not copied: same content, and the source still exists
    assert (result.folder / "003_e1.jpg").read_bytes() == b"FILE:e1.jpg"
    assert (result.folder / "001_opener.jpg").read_bytes() == b"OPENER"
    assert gw.cut("cut-s").last_exported_at == _now()


def test_export_without_separators(gw, tmp_path):
    cut = gw.cut("cut-s")
    result = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert _names(result.folder) == [
        "001_e1.jpg", "002_e3a.jpg", "003_v1.mp4"]
    assert result.separators == 0


def test_second_export_gets_fresh_snapshot_folder(gw, tmp_path):
    cut = gw.cut("cut-s")
    first = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    second = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert first.folder.name == "short_version"
    assert second.folder.name == "short_version (2)"
    assert _names(first.folder) == _names(second.folder)


def test_missing_source_skipped_and_reported(gw, tmp_path):
    (tmp_path / "Exported Media" / "e3a.jpg").unlink()
    cut = gw.cut("cut-s")
    result = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert result.missing == ["Exported Media/e3a.jpg"]
    assert _names(result.folder) == ["001_e1.jpg", "002_v1.mp4"]


def _tracks(tmp_path, *secs) -> list:
    out = []
    for i, s in enumerate(secs):
        p = tmp_path / "lib" / f"song{i}.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MP3" + bytes([i]))
        out.append(audio_library.AudioTrack(
            path=p, kind=audio_library.AudioKind.MUSIC,
            mood="happy", duration_seconds=float(s)))
    return out


def test_audio_playlist_linked_and_covering(gw, tmp_path):
    gw.update_cut_settings("cut-s", music_category="happy")
    cut = gw.cut("cut-s")
    # show = (2 photos + 2 separators) × 6 s + 30 s clip = 54 s
    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=True,
        separator_writer=_sep_writer,
        audio_tracks=_tracks(tmp_path, 40, 50), rng=random.Random(3))
    audio = result.folder / "audio"
    assert result.audio_files == 2 and not result.audio_short
    assert len(_names(audio)) == 2
    assert all(n[:3] in ("01_", "02_") for n in _names(audio))


def test_audio_short_library_flagged(gw, tmp_path):
    gw.update_cut_settings("cut-s", music_category="happy")
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=True,
        separator_writer=_sep_writer,
        audio_tracks=_tracks(tmp_path, 20), rng=random.Random(3))
    assert result.audio_files == 1 and result.audio_short


def test_no_music_category_no_audio_dir(gw, tmp_path):
    cut = gw.cut("cut-s")
    result = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert result.audio_files == 0
    assert not (result.folder / "audio").exists()


# --------------------------------------------------------------------------- #
# spec/81 §5 — target defaulting (no path on the Cut) + overlays (§3.1)
# --------------------------------------------------------------------------- #

from core import cut_overlay
from mira.shared.cut_export import default_target


def test_target_defaults_under_event_root_and_is_not_stored(gw, tmp_path):
    cut = gw.cut("cut-s")
    result = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert result.folder == tmp_path / "Cuts" / "short_version"
    assert default_target(tmp_path, "short_version") == tmp_path / "Cuts" / "short_version"
    # no absolute target persisted on the Cut (charter invariant #2)
    assert not hasattr(cut, "target_path")
    cols = {f for f in type(cut).__dataclass_fields__}
    assert "target" not in cols and "target_path" not in cols


def test_explicit_target_parameter_overrides_default(gw, tmp_path):
    cut = gw.cut("cut-s")
    elsewhere = tmp_path / "Desktop" / "MyCut"
    result = export_cut(gw, cut, event_root=tmp_path, target=elsewhere,
                        separators_on=False)
    assert result.folder == elsewhere
    assert (elsewhere / "001_e1.jpg").exists()


def test_embedded_overlay_writes_where_iptc_keeps_links(gw, tmp_path):
    gw.update_cut_settings("cut-s", overlay_fields_json='["when", "where"]',
                           overlay_mode="embedded")
    gw.set_cut_members("cut-s", ["Exported Media/e1.jpg"])
    cut = gw.cut("cut-s")
    written = []

    def prov(relpath):
        return cut_overlay.FrameProvenance(when="2026", city="Arenal",
                                           country="Costa Rica")

    def iptc(path, tags):
        written.append((path.name, tags))
        return True

    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=False,
        provenance_resolver=prov, iptc_writer=iptc)
    # members stayed hardlinks (no burn-in copies), and where-IPTC was written
    assert result.linked == 1 and result.copied == 0 and result.burned_in == 0
    assert result.iptc_written == 1
    assert written and written[0][1] == {
        cut_overlay.IPTC_CITY: "Arenal",
        cut_overlay.IPTC_COUNTRY: "Costa Rica"}


def test_embedded_overlay_no_where_data_stays_pure_link(gw, tmp_path):
    gw.update_cut_settings("cut-s", overlay_fields_json='["when"]',
                           overlay_mode="embedded")
    gw.set_cut_members("cut-s", ["Exported Media/e1.jpg"])
    cut = gw.cut("cut-s")
    calls = []
    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=False,
        provenance_resolver=lambda r: cut_overlay.FrameProvenance(when="2026"),
        iptc_writer=lambda p, t: calls.append(p) or True)
    # 'where' not selected → no IPTC write, frame stays a pure link
    assert result.iptc_written == 0 and calls == []
    assert result.linked == 1


def test_burn_in_overlay_emits_copies_not_links(gw, tmp_path):
    gw.update_cut_settings("cut-s", overlay_fields_json='["where"]',
                           overlay_mode="burn_in")
    gw.set_cut_members("cut-s", ["Exported Media/e1.jpg", "Exported Media/e2.jpg"])
    cut = gw.cut("cut-s")
    rendered = []

    def render(src, dst, fields, prov):
        rendered.append(dst.name)
        dst.write_bytes(b"BURNED:" + src.name.encode())

    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=False,
        provenance_resolver=lambda r: cut_overlay.FrameProvenance(city="Arenal"),
        overlay_renderer=render)
    assert result.burned_in == 2 and result.linked == 0
    assert result.copied == 2          # burned-in members are copies, not links
    assert len(rendered) == 2
    assert (result.folder / "001_e1.jpg").read_bytes().startswith(b"BURNED:")


def test_overlays_cost_no_budget(gw, tmp_path):
    """Overlays never add a slide / second — the export's photo+separator+clip
    accounting is identical with overlays on vs off (spec/81 §3.1)."""
    gw.update_cut_settings("cut-s", music_category="happy",
                           overlay_fields_json='["where"]', overlay_mode="embedded")
    gw.set_cut_members("cut-s", ["Exported Media/e1.jpg", "Exported Media/e3a.jpg"])
    cut = gw.cut("cut-s")
    # With overlays embedded, audio length back-solves from the SAME show
    # composition as without overlays (2 photos + separators), so a 13 s track
    # covers a (2 photos + 2 seps) × 6 s = 24 s show identically either way.
    res = export_cut(
        gw, cut, event_root=tmp_path, separators_on=True,
        separator_writer=_sep_writer,
        provenance_resolver=lambda r: cut_overlay.FrameProvenance(city="A"),
        iptc_writer=lambda p, t: True,
        audio_tracks=_tracks(tmp_path, 13, 13, 13), rng=random.Random(1))
    # 24 s show → playlist sums to ≥ 24 s including the crossing file (13+13)
    assert res.audio_files == 2
