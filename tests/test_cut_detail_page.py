"""spec/61 slice 7 — the flat WYSIWYG grid + generated separator tiles.

The detail surface over the real event.db fixture: separators
interleaved at day boundaries (real rendered tiles), neutral rings
(nothing is decided here), read-only single view stepping across files
only, and the separator-card renderer itself (aspect, content, derived
freshness).
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store.repo import EventStore
from mira.ui.shared.cut_detail_page import CutDetailPage
from mira.ui.shared.separator_card import (
    parse_aspect,
    render_separator_image,
)

from tests.test_gateway_cuts import _doc, _now


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, event_root=tmp_path, now=_now,
                     new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


def _detail(gw, *, members, separators_on=True) -> CutDetailPage:
    gw.set_cut_members("cut-s", members)
    page = CutDetailPage()
    page.show_cut(gw, gw.cut("cut-s"),
                  separators_on=separators_on, aspect="16:9")
    return page


# --------------------------------------------------------------------------- #
# The separator card renderer
# --------------------------------------------------------------------------- #


def test_parse_aspect_forms_and_fallback():
    assert parse_aspect("16:9") == pytest.approx(16 / 9)
    assert parse_aspect("4:3") == pytest.approx(4 / 3)
    assert parse_aspect("9x16") == pytest.approx(9 / 16)
    assert parse_aspect("junk") == pytest.approx(16 / 9)
    assert parse_aspect(None) == pytest.approx(16 / 9)


def test_card_colors_deterministic_and_styled(qapp):
    """Nelson round 3: random-LOOKING colours, deterministic from the
    seed — the grid tile, the rehearsal and the export always agree."""
    from mira.ui.shared.separator_card import card_colors
    black = card_colors("black", "anything")
    assert black[0].name() == "#15171b"
    a1 = card_colors("multi", "cut1:1")
    a2 = card_colors("multi", "cut1:1")
    b = card_colors("multi", "cut1:2")
    assert a1[0].name() == a2[0].name()           # stable per seed
    assert a1[0].name() != b[0].name()            # days differ
    single = card_colors("single", "cut1")
    assert single[0].name() != black[0].name()    # actually colourful
    # backgrounds stay dark enough for the light text
    assert a1[0].value() <= 150 and single[0].value() <= 150


def test_render_separator_image_shape(qapp):
    img = render_separator_image(
        day_number=3, date="2026-04-03", location="Monteverde",
        description="Cloud forest hike", aspect="16:9", height=360)
    assert img.height() == 360
    assert img.width() == round(360 * 16 / 9)
    img2 = render_separator_image(day_number=None, aspect="4:3", height=240)
    assert img2.width() == 320


# --------------------------------------------------------------------------- #
# The flat grid — separators interleaved, neutral rings
# --------------------------------------------------------------------------- #


def test_entries_interleave_opener_and_separators(qapp, gw, tmp_path):
    page = _detail(gw, members=[
        "Exported Media/e1.jpg",                       # day 1
        "Exported Media/e3a.jpg", "Exported Media/v1.mp4",   # day 2
    ])
    kinds = [k for k, _ in page._entries]
    payloads = [it.payload for it in page._grid.items()]
    assert kinds == ["opener", "sep", "file", "sep", "file", "file"]
    # The grid carries the (kind, payload) tuples in show order; the
    # opener + day separators sit at indices 0/1/3 (day-boundary tiles).
    assert payloads[0] == ("opener", None)
    assert payloads[1] == ("sep", 1)
    assert payloads[3] == ("sep", 2)
    assert payloads[2] == ("file", "Exported Media/e1.jpg")
    # Nothing is being decided here — every cell carries the neutral
    # ring (no state token assigned).
    assert all(it.state is None for it in page._grid.items())
    # The opener + separator tiles carry their rendered cards already
    # (synchronous render at show_cut() time).
    assert page._grid.items()[0].pixmap is not None
    assert page._grid.items()[1].pixmap is not None
    assert "#short_version" in page._tag_lbl.text()


def test_separators_off_means_files_only(qapp, gw, tmp_path):
    page = _detail(gw, members=[
        "Exported Media/e1.jpg", "Exported Media/e3a.jpg"], separators_on=False)
    assert [k for k, _ in page._entries] == ["file", "file"]


def test_single_view_opens_cards_and_steps_in_show_order(qapp, gw, tmp_path):
    """Nelson eyeball 2026-06-12: the opener + separators open big like
    any slide, and stepping walks the SHOW order — cards included
    (arrows live in the embedded viewport since spec/63 slice 2)."""
    page = _detail(gw, members=[
        "Exported Media/e1.jpg", "Exported Media/e3a.jpg"])
    vp = page._single._viewport
    # entries: opener, sep:1, e1, sep:2, e3a
    page._open_single(0)                             # the opener card
    assert page._stack.currentIndex() == 1
    assert "Opener" in page._single._title.text()
    assert vp.sharp_pixmap_info() is not None        # the card IS the pixels
    vp._go(+1)                                       # the day-1 card
    assert "Day 1" in page._single._title.text()
    vp._go(+1)
    assert (page._single.current_file().export_relpath
            == "Exported Media/e1.jpg")
    vp._go(+1)                                       # the day-2 card
    assert "Day 2" in page._single._title.text()
    assert page._single.current_file() is None       # cards aren't files
    vp._go(+1)
    assert (page._single.current_file().export_relpath
            == "Exported Media/e3a.jpg")
    vp._go(-1)
    assert "Day 2" in page._single._title.text()


def test_play_button_visible_on_the_detail_grid(qapp, gw, tmp_path):
    """Nelson eyeball 2026-06-12 ("could not play"): the Play/Export
    chrome on the cut-detail surface stays HIDDEN by default and
    appears when the host passes ``show_play=True`` / ``show_export=
    True`` to the page constructor."""
    page = CutDetailPage(show_play=True, show_export=True)
    assert page._play_btn.isVisibleTo(page)
    assert "rehearsal" in page._play_btn.toolTip()
    assert page._export_btn.isVisibleTo(page)


def test_adjust_emits_cut_id(qapp, gw, tmp_path):
    page = _detail(gw, members=["Exported Media/e1.jpg"])
    got = []
    page.adjust_requested.connect(got.append)
    page.adjust_requested.emit(page._cut_id)
    assert got == ["cut-s"]
