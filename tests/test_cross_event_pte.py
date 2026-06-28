"""spec/154 slice B — the cross-event PTE member composition.

``LibraryPage._cross_event_pte_members`` replays the SAME chronological
entries the in-app Play builds (opener + per-(event, day) separators +
files) and composes each slide's separate ``:Text`` objects from the SAME
helpers Play feeds its live overlays — provenance captions, the origin
label, and the opener summary. These tests pin that the right text rides
each slide and that missing card / member files are dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from mira.gateway.library_gateway import LibraryGateway
from mira.shared.pte_project import (
    TEXT_OPENER_TITLE, TEXT_ORIGIN, TEXT_PHOTO_CAPTION, TEXT_SEP_TITLE,
)
from mira.ui.pages.library_page import LibraryPage
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-26T00:00:00+00:00"


@dataclass
class _FakeUmbrella:
    """Minimal umbrella surface LibraryPage construction + the PTE helper
    read. ``library_gateway`` hands back the real store-backed gateway."""

    lg: object
    cuts: List[object] = field(default_factory=list)

    def library_gateway(self):
        return self.lg

    def cross_event_cuts(self):
        return list(self.cuts)

    def recipe_store(self):
        class _RS:
            def list(self):
                return []
        return _RS()


def _open_lg(tmp_path) -> LibraryGateway:
    store = UserStore.create(
        tmp_path / "mira.db", app_version="test", created_at=NOW)
    return LibraryGateway(store, now=lambda: NOW, new_id=lambda: "cut-x")


def _seed(lg, *, source_label=True, separators=True,
          overlay_fields=("when", "where")):
    """Two events' worth of items + a cross-event Cut spanning two days."""
    lg.user_store.upsert(um.EventIndex(
        event_uuid="src", relpath_to_base="Source", name_cached="Source"))
    lg.user_store.upsert(um.GlobalItem(
        event_uuid="src", item_id="a", synced_at=NOW,
        event_name="Source",
        export_relpath="Exported Media/a.jpg",
        capture_time="2026-04-01T10:00:00", kind="photo", has_export=True,
        day_city="Salta", country="Argentina"))
    lg.user_store.upsert(um.GlobalItem(
        event_uuid="src", item_id="b", synced_at=NOW,
        event_name="Source",
        export_relpath="Exported Media/b.jpg",
        capture_time="2026-04-02T10:00:00", kind="photo", has_export=True,
        day_city="Salta", country="Argentina"))
    cut = lg.create_cross_event_cut(
        "the_trip",
        separators=separators,
        overlay_fields=list(overlay_fields),
        overlay_mode="embedded",
        source_label=source_label)
    lg.set_cross_event_cut_members(cut.id, [
        {"event_id": "src", "kind": "export",
         "export_relpath": "Exported Media/a.jpg"},
        {"event_id": "src", "kind": "export",
         "export_relpath": "Exported Media/b.jpg"},
    ])
    return lg.cross_event_cut(cut.id)


def _write_cards(folder):
    """Stand in for the export: every card + member file present on disk
    so the PTE helper's ``is_file()`` gate passes."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("000_opener.jpg",
                 "_sep_src_2026-04-01.jpg", "_sep_src_2026-04-02.jpg",
                 "Exported Media_a.jpg", "Exported Media_b.jpg"):
        (folder / name).write_bytes(b"x")


def test_pte_members_carry_opener_separator_caption_origin(qapp, tmp_path):
    """The full overlay vocabulary lands on the right slides: opener title,
    per-(event, day) separator titled by source event, photo caption, and
    the per-slide origin label."""
    lg = _open_lg(tmp_path)
    cut = _seed(lg)
    folder = tmp_path / "out"
    _write_cards(folder)
    page = LibraryPage(_FakeUmbrella(lg))
    try:
        members = page._cross_event_pte_members(lg, cut, folder)
        # opener + 2 separators + 2 files (chronological).
        assert [m.path.name for m in members] == [
            "000_opener.jpg",
            "_sep_src_2026-04-01.jpg", "Exported Media_a.jpg",
            "_sep_src_2026-04-02.jpg", "Exported Media_b.jpg",
        ]
        roles = lambda m: [t.role for t in m.texts]
        # Opener title.
        assert TEXT_OPENER_TITLE in roles(members[0])
        assert any("trip" in t.text.lower() for t in members[0].texts)
        # Separator titled by SOURCE EVENT name.
        assert TEXT_SEP_TITLE in roles(members[1])
        assert members[1].texts[0].text == "Source"
        # Photo slide: caption (where) + origin label.
        file_roles = roles(members[2])
        assert TEXT_PHOTO_CAPTION in file_roles
        assert TEXT_ORIGIN in file_roles
        caption = next(t.text for t in members[2].texts
                       if t.role == TEXT_PHOTO_CAPTION)
        assert "Salta" in caption
        origin = next(t.text for t in members[2].texts
                      if t.role == TEXT_ORIGIN)
        assert origin == "Source · 1 Apr 2026"
    finally:
        page.deleteLater()
        lg.user_store.close()


def test_pte_members_origin_off_omits_origin_text(qapp, tmp_path):
    """With the Source-label flag OFF, no origin :Text rides the slides
    (the caption still does)."""
    lg = _open_lg(tmp_path)
    cut = _seed(lg, source_label=False)
    folder = tmp_path / "out"
    _write_cards(folder)
    page = LibraryPage(_FakeUmbrella(lg))
    try:
        members = page._cross_event_pte_members(lg, cut, folder)
        for m in members:
            assert all(t.role != TEXT_ORIGIN for t in m.texts)
        # Caption is unaffected.
        file_member = next(
            m for m in members if m.path.name == "Exported Media_a.jpg")
        assert any(t.role == TEXT_PHOTO_CAPTION for t in file_member.texts)
    finally:
        page.deleteLater()
        lg.user_store.close()


def test_pte_members_drop_missing_files(qapp, tmp_path):
    """A member whose bytes never landed (export skipped it) is dropped so
    the generated .pte never points at a non-existent file."""
    lg = _open_lg(tmp_path)
    cut = _seed(lg, separators=False, source_label=False, overlay_fields=())
    folder = tmp_path / "out"
    folder.mkdir()
    # Only the opener + the first member exist on disk.
    (folder / "000_opener.jpg").write_bytes(b"x")
    (folder / "Exported Media_a.jpg").write_bytes(b"x")
    page = LibraryPage(_FakeUmbrella(lg))
    try:
        members = page._cross_event_pte_members(lg, cut, folder)
        names = [m.path.name for m in members]
        assert "Exported Media_a.jpg" in names
        assert "Exported Media_b.jpg" not in names      # bytes absent
    finally:
        page.deleteLater()
        lg.user_store.close()
