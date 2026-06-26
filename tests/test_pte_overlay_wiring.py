"""spec/120 — embedded overlays must reach the generated `.pte`.

The bug: ``share_cuts_page._cut_photo_caption`` resolved provenance with
the wrong key — the Cut-folder path (``<tag>/007_IMG.jpg``) instead of
the lineage ``export_relpath`` (``Exported Media/IMG.jpg``). Every
lookup missed → empty provenance → empty overlay text → PTE stripped
every nested ``:Text``. Play looked right because it composes from the
real lineage row directly.

The fix builds a ``{basename → export_relpath}`` lookup once per
generation from ``EventGateway.cut_member_files`` and resolves the
NNN_-stripped photo basename through it.

These tests pin:

* embedded mode + ≥1 field on a Cut with real provenance lands per-slide
  ``:Text`` in the generated PTE with the composed lines;
* the lookup matches by NNN_-stripped basename, not by Cut-folder path;
* members with empty provenance still yield ``None`` (no leftover
  placeholder text on a slide);
* Off / burn_in modes still strip every ``:Text``;
* a regression check on the old folder-relative-key path — pinning that
  it would have produced empty text — so the next refactor can't
  silently revert."""
from __future__ import annotations

import itertools
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import cut_overlay
from mira.gateway.event_gateway import EventGateway
from mira.shared.pte_project import (
    PteAudioTrack, PteMember, bundled_skeleton_path, generate,
    load_skeleton,
)
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.pages.share_cuts_page import ShareCutsPage


# ── Fixtures ────────────────────────────────────────────────────


NOW = "2026-06-23T10:00:00+00:00"


def _now() -> str:
    return NOW


def _photo(item_id: str, day: int, t: str, *,
           camera_id: str = "Panasonic Lumix G9",
           lens_model: str = "LEICA 100-400mm",
           classification: str = "macro",
           aperture: float = 4.0, shutter: float = 1 / 500.0,
           iso: int = 400) -> m.Item:
    return m.Item(
        id=item_id, kind="photo", created_at=NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.jpg",
        sha256="a" * 64, byte_size=1000,
        materialized_at=NOW, materialized_phase="ingest",
        camera_id=camera_id, lens_model=lens_model,
        day_number=day,
        capture_time_raw=t, capture_time_corrected=t,
        classification=classification,
        aperture_f=aperture, shutter_speed_s=shutter, iso=iso,
        focal_length_mm=200.0,
    )


def _bare_item(item_id: str) -> m.Item:
    """An item with no day / no EXIF — the schema requires camera_id +
    capture_time_raw on a ``captured`` row, so we keep those (and a
    valid file-identity quartet), but everything that drives WHERE /
    HOW2 comes back ``None``. A Cut whose selected fields are only
    WHERE / HOW2 then composes no lines for this item — the graceful
    "no provenance" path the resolver returns ``None`` from."""
    return m.Item(
        id=item_id, kind="photo", created_at=NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.jpg",
        sha256="c" * 64, byte_size=1,
        materialized_at=NOW, materialized_phase="ingest",
        camera_id="Panasonic Lumix G9",
        capture_time_raw="2026-06-23T08:45:00",
        # day_number, aperture_f, shutter_speed_s, iso, focal_length_mm,
        # lens_model, flash_fired all default to None — so WHERE and
        # HOW2 contribute no lines (HOW1 would still resolve via
        # camera_id; the test fields exclude it).
    )


def _doc(overlay_mode: str = "embedded",
         overlay_fields=(cut_overlay.FIELD_WHEN, cut_overlay.FIELD_WHERE,
                         cut_overlay.FIELD_HOW1, cut_overlay.FIELD_HOW2),
         include_empty_provenance: bool = False) -> m.EventDocument:
    """Build a minimal event with two photo lineage rows + one Cut
    that ships them with embedded overlays + every field selected.
    With ``include_empty_provenance=True`` a third lineage row points
    at a bare item with no camera / day / EXIF — the gateway returns
    a near-empty :class:`FrameProvenance` and the composer (when fed
    WHERE / HOW1 / HOW2 only) produces no lines, so the resolver
    short-circuits to ``None`` (the graceful "no provenance" path)."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-o", name="Overlay fixture",
        created_at=NOW, updated_at=NOW))
    doc.trip_days = [
        m.TripDay(day_number=1, date="2026-06-23",
                  location="Tokyo",
                  extras_json='{"country": "Japan"}'),
    ]
    doc.cameras = [m.Camera(camera_id="Panasonic Lumix G9")]
    doc.items = [
        _photo("p1", 1, "2026-06-23T08:00:00"),
        _photo("p2", 1, "2026-06-23T08:30:00"),
    ]
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/IMG_001.jpg", phase="edit",
                  source_kind="item", source_item_id="p1", exported_at=NOW),
        m.Lineage(export_relpath="Exported Media/IMG_002.jpg", phase="edit",
                  source_kind="item", source_item_id="p2", exported_at=NOW),
    ]
    members = [
        m.CutMember(cut_id="cut-o", export_relpath="Exported Media/IMG_001.jpg",
                    added_at=NOW),
        m.CutMember(cut_id="cut-o", export_relpath="Exported Media/IMG_002.jpg",
                    added_at=NOW),
    ]
    if include_empty_provenance:
        doc.items.append(_bare_item("p3"))
        doc.lineage.append(m.Lineage(
            export_relpath="Exported Media/IMG_003.jpg", phase="edit",
            source_kind="item", source_item_id="p3", exported_at=NOW))
        members.append(m.CutMember(
            cut_id="cut-o",
            export_relpath="Exported Media/IMG_003.jpg",
            added_at=NOW))
    import json as _json
    doc.cuts = [m.Cut(
        id="cut-o", tag="show", created_at=NOW, updated_at=NOW,
        overlay_mode=overlay_mode,
        overlay_fields_json=_json.dumps(list(overlay_fields)),
    )]
    doc.cut_members = members
    return doc


@pytest.fixture
def gw(tmp_path):
    """Per-event gateway with a Cut that has embedded overlays seeded."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-o")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, event_root=tmp_path, now=_now,
                     new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


def _stub_page(gw) -> ShareCutsPage:
    """Bind a real :class:`ShareCutsPage` method to a stub carrying the
    gateway — avoids constructing the full page (a Qt-heavy host)."""
    stub = SimpleNamespace()
    stub._eg = gw
    # Method handles we exercise — bound to ``stub`` so ``self._eg`` works.
    stub._build_overlay_member_lookup = (
        lambda cut: ShareCutsPage._build_overlay_member_lookup(stub, cut))
    stub._strip_seq_prefix = ShareCutsPage._strip_seq_prefix.__get__(stub)
    stub._cut_photo_caption = (
        lambda cut, photo, member_lookup=None:
        ShareCutsPage._cut_photo_caption(stub, cut, photo, member_lookup))
    return stub


# ── Lookup construction ────────────────────────────────────────


def test_lookup_maps_basename_to_lineage_export_relpath(gw):
    """spec/120 — the lookup keyed by ``Path(export_relpath).name``
    is exactly what :meth:`_cut_photo_caption` needs to translate a
    Cut-folder filename (``001_IMG_001.jpg``) back into the lineage
    relpath (``Exported Media/IMG_001.jpg``) the gateway joins on."""
    page = _stub_page(gw)
    cut = gw.cut("cut-o")
    lookup = page._build_overlay_member_lookup(cut)
    assert lookup == {
        "IMG_001.jpg": "Exported Media/IMG_001.jpg",
        "IMG_002.jpg": "Exported Media/IMG_002.jpg",
    }


def test_strip_seq_prefix_handles_sequence_only(gw):
    """``NNN_<rest>`` → ``<rest>``; a non-numeric prefix or no
    underscore returns the name unchanged so a non-prefixed filename
    (e.g. a third-party export) still gets a chance to match by
    basename."""
    page = _stub_page(gw)
    assert page._strip_seq_prefix("007_IMG_1234.jpg") == "IMG_1234.jpg"
    # A double-underscore name still strips a single NNN_ prefix.
    assert page._strip_seq_prefix("014_day3.jpg") == "day3.jpg"
    # Non-numeric prefix → pass through.
    assert page._strip_seq_prefix("foo_bar.jpg") == "foo_bar.jpg"
    # No underscore → pass through.
    assert page._strip_seq_prefix("plain.jpg") == "plain.jpg"


# ── PTE inclusion gate (spec/152 §X) ────────────────────────────


def test_is_card_slide_name_matches_opener_separators_undated():
    """Card slides are rendered by the exporter (not pulled from the
    member list) and must always be included in the regenerated PTE.
    Recognised by name pattern after stripping the NNN_ prefix."""
    assert ShareCutsPage._is_card_slide_name("opener.jpg") is True
    assert ShareCutsPage._is_card_slide_name("OPENER.JPG") is True
    assert ShareCutsPage._is_card_slide_name("day1.jpg") is True
    assert ShareCutsPage._is_card_slide_name("day42.jpg") is True
    assert ShareCutsPage._is_card_slide_name("undated.jpg") is True
    # Real cut members are not card slides.
    assert ShareCutsPage._is_card_slide_name("IMG_1234.jpg") is False
    assert ShareCutsPage._is_card_slide_name("VID_5678.mp4") is False
    # Defensive: things that look almost like a card slide.
    assert ShareCutsPage._is_card_slide_name("day.jpg") is False
    assert ShareCutsPage._is_card_slide_name("dayX.jpg") is False
    assert ShareCutsPage._is_card_slide_name("day1.png") is False


def test_is_cut_member_file_admits_cut_members_and_card_slides(gw):
    """Members in the overlay lookup pass the gate; card slides pass
    by name pattern; everything else (a stale file in the folder
    from a prior export) is filtered out. This is what makes the
    regenerated PTE match the current Cut selection."""
    page = _stub_page(gw)
    page._is_card_slide_name = ShareCutsPage._is_card_slide_name
    page._is_cut_member_file = (
        lambda stripped, lookup:
        ShareCutsPage._is_cut_member_file(page, stripped, lookup))
    cut = gw.cut("cut-o")
    lookup = page._build_overlay_member_lookup(cut)
    # Current members → admitted.
    assert page._is_cut_member_file("IMG_001.jpg", lookup) is True
    assert page._is_cut_member_file("IMG_002.jpg", lookup) is True
    # Card slides → admitted regardless of the member list.
    assert page._is_cut_member_file("opener.jpg", lookup) is True
    assert page._is_cut_member_file("day1.jpg", lookup) is True
    # Stale file from a prior export — NOT a member, NOT a card → out.
    assert page._is_cut_member_file("IMG_999.jpg", lookup) is False
    assert page._is_cut_member_file("OLD_VIDEO.mp4", lookup) is False


# ── Per-photo resolution ───────────────────────────────────────


def test_cut_photo_caption_uses_member_lookup_to_compose_lines(gw, tmp_path):
    """The headline fix: a Cut-folder filename resolves through the
    lookup to its lineage relpath; ``frame_provenance`` returns a
    populated record; the composer produces real overlay text."""
    page = _stub_page(gw)
    cut = gw.cut("cut-o")
    lookup = page._build_overlay_member_lookup(cut)
    photo = tmp_path / "show" / "001_IMG_001.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"frame")
    text = page._cut_photo_caption(cut, photo, lookup)
    assert text is not None
    # All four overlay fields contribute on a fully-seeded fixture.
    assert "2026-06-23T08:00:00" in text                  # WHEN
    assert "Tokyo" in text and "Japan" in text            # WHERE
    assert "Lumix G9" in text                             # HOW1 — camera
    assert "f/4" in text and "1/500" in text              # HOW2 — exposure
    assert "ISO 400" in text


def test_cut_photo_caption_returns_none_for_separator_filename(gw, tmp_path):
    """Separators / opener cards (``002_day1.jpg`` etc.) are NOT in
    the Cut's member list; the lookup misses → ``None`` → PTE strips
    the nested ``:Text`` so the separator slide is clean."""
    page = _stub_page(gw)
    cut = gw.cut("cut-o")
    lookup = page._build_overlay_member_lookup(cut)
    sep = tmp_path / "show" / "002_day1.jpg"
    sep.parent.mkdir(parents=True, exist_ok=True)
    sep.write_bytes(b"sep")
    assert page._cut_photo_caption(cut, sep, lookup) is None


def test_cut_photo_caption_returns_none_when_provenance_empty(tmp_path):
    """A bare item with no camera / day / EXIF yields a
    near-empty :class:`FrameProvenance`; the composer (fed
    WHERE / HOW1 / HOW2 only — every one needs structured data) returns
    no lines; the resolver returns ``None`` so PTE strips the slide's
    ``:Text``. Avoids a bare ``Text=""`` on every minimally-known
    photo."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-o")
    # WHEN always resolves (lineage.exported_at is the floor) so we
    # exclude it here — leaving fields that need real provenance.
    store.save_document(_doc(
        overlay_fields=(cut_overlay.FIELD_WHERE, cut_overlay.FIELD_HOW2),
        include_empty_provenance=True))
    counter = itertools.count(1)
    gw = EventGateway(store, event_root=tmp_path, now=_now,
                      new_id=lambda: f"id-{next(counter)}")
    try:
        page = _stub_page(gw)
        cut = gw.cut("cut-o")
        lookup = page._build_overlay_member_lookup(cut)
        # The good two yield text.
        good = tmp_path / "show" / "001_IMG_001.jpg"
        good.parent.mkdir(parents=True, exist_ok=True)
        good.write_bytes(b"x")
        assert page._cut_photo_caption(cut, good, lookup) is not None
        # The bare-provenance third yields None.
        ghost = tmp_path / "show" / "003_IMG_003.jpg"
        ghost.write_bytes(b"x")
        assert page._cut_photo_caption(cut, ghost, lookup) is None
    finally:
        gw.close()




def test_cut_photo_caption_returns_none_when_no_overlay_fields(tmp_path):
    """No overlay fields selected → no composed lines either; same
    early-out as the embedded-without-fields case."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-o")
    store.save_document(_doc(overlay_fields=()))
    counter = itertools.count(1)
    gw = EventGateway(store, event_root=tmp_path, now=_now,
                      new_id=lambda: f"id-{next(counter)}")
    try:
        page = _stub_page(gw)
        cut = gw.cut("cut-o")
        photo = tmp_path / "show" / "001_IMG_001.jpg"
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"x")
        assert page._cut_photo_caption(cut, photo, {}) is None
    finally:
        gw.close()


# ── Regression: the OLD folder-relative key would have missed ──


def test_regression_old_folder_relative_key_misses_gateway(gw):
    """spec/120 root cause pin: the pre-fix code passed
    ``str(photo.relative_to(photo.parent.parent))`` to
    ``frame_provenance`` — e.g. ``"show/001_IMG_001.jpg"``. The
    gateway joins ``lineage.export_relpath = ?``, which carries the
    Export-phase path (``Exported Media/IMG_001.jpg``). Asserting
    that lookup MISSES locks the fix in: a future refactor that
    re-introduces the folder-relative key will fail this test before
    a user notices missing overlays."""
    bad_key = "show/001_IMG_001.jpg"
    prov = gw.frame_provenance(bad_key)
    # Empty FrameProvenance: no source item, no facts → the composer
    # produces no lines.
    fields = (cut_overlay.FIELD_WHEN, cut_overlay.FIELD_WHERE,
              cut_overlay.FIELD_HOW1, cut_overlay.FIELD_HOW2)
    assert cut_overlay.compose_overlay_lines(fields, prov) == []


# ── End-to-end: generator embeds the :Text ─────────────────────


_GUID = r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-" \
        r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}"


def _slide_section(text: str, name: str) -> str:
    m = re.search(rf"\[{re.escape(name)}\]\r\n([\s\S]*?)(?=\[[A-Za-z0-9_ ]+\]\r\n|\Z)", text)
    assert m is not None, f"no [{name}] section"
    return m.group(1)


def test_generated_pte_carries_embedded_overlay_text(gw, tmp_path):
    """End-to-end: the resolver produces overlay text → the generator's
    `embedded` branch writes a per-slide ``:Text`` with that string.
    The two-photo Cut produces two ``:Text`` blocks; each contains the
    composed lines."""
    page = _stub_page(gw)
    cut = gw.cut("cut-o")
    folder = tmp_path / "show"
    folder.mkdir(parents=True, exist_ok=True)
    # Materialise the exporter's filenames.
    photos = []
    for seq, src in enumerate([
        "Exported Media/IMG_001.jpg",
        "Exported Media/IMG_002.jpg",
    ], start=1):
        dst = folder / f"{seq:03d}_{Path(src).name}"
        dst.write_bytes(b"x")
        photos.append(dst)
    lookup = page._build_overlay_member_lookup(cut)
    members = [
        PteMember(kind="photo", path=p,
                  overlay_text=page._cut_photo_caption(cut, p, lookup))
        for p in photos
    ]
    # Every member has a composed string (no empty/None).
    assert all(m.overlay_text for m in members), \
        [(m.path.name, m.overlay_text) for m in members]
    text = generate(
        load_skeleton(bundled_fallback=bundled_skeleton_path()),
        members, [],
        aspect="16:9", photo_seconds=6.0,
        project_path=folder / "slideshow.pte",
        images_folder=folder,
    )
    for i, member in enumerate(members, start=1):
        body = _slide_section(text, f"Slide{i}")
        # The slide carries a populated nested Text block.
        text_lines = re.findall(r'^      Text="([^"]*)"', body, re.MULTILINE)
        assert text_lines, f"slide {i} has no :Text"
        # ``compose_overlay_lines`` joins on ``\n`` so the populated
        # Text= string survives intact — pin the first line as the
        # discriminator (Tokyo + Japan is in WHERE).
        assert "Tokyo" in text_lines[0]


def test_generated_pte_strips_text_when_overlay_text_is_none(gw, tmp_path):
    """A None overlay_text (separator card, missing provenance,
    burn_in mode) → the spec/107 generator strips the nested
    ``:Text``. Pin the inverse contract so the regression direction
    is tight: missing text means no slide-level Text= line."""
    folder = tmp_path / "show"
    folder.mkdir(parents=True, exist_ok=True)
    photo = folder / "001_IMG_001.jpg"
    photo.write_bytes(b"x")
    text = generate(
        load_skeleton(bundled_fallback=bundled_skeleton_path()),
        [PteMember(kind="photo", path=photo, overlay_text=None)],
        [], aspect="16:9", photo_seconds=6.0,
        project_path=folder / "slideshow.pte",
        images_folder=folder,
    )
    body = _slide_section(text, "Slide1")
    assert ":Text\r\n" not in body
