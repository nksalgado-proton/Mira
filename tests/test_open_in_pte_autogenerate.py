"""spec/149 §2.B — ``_on_open_exported_in_pte`` auto-generates the
``.pte`` when none is present in the bundle folder.

Before spec/149 the handler no-op'd on a missing ``.pte``; a folder
that had media but no project (rename / use_pte-was-off / deleted
project) could only be recovered by a full re-export. With the gap
closed, one click on "Open in PTE" now generates the project in place
and launches it.

These tests pin:
  * ``use_pte`` on + folder exists + no ``.pte`` → generator fires →
    launcher fires against the just-written ``.pte``.
  * ``use_pte`` off → no generate, no launch (the button is also
    upstream-gated, but the handler is defensive).
  * ``use_pte`` on + folder missing → no generate (nothing to write
    into), no launch.
  * Cross-event surface honours the same behaviour through its own
    detail dialog.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest


# Re-use the per-event fixture shape from the spec/117 test file so we
# don't duplicate the library/event/cut bootstrap logic.
from tests.test_exported_cut_actions import (        # noqa: E402
    _Cut,
    _Settings,
    _make_minimal_share_page,
    _setup_event_layout,
    fake_pte,                                         # noqa: F401
)


# ── Per-event surface (ShareCutsPage) ───────────────────────────


def _seed_media_only(folder: Path) -> None:
    """A bundle folder with media files but NO ``.pte``. Models the
    rename-broke-the-pte / use_pte-was-off / deleted-pte cases."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "001_a.jpg").write_bytes(b"PHOTO-A")
    (folder / "002_b.jpg").write_bytes(b"PHOTO-B")


def test_open_in_pte_autogenerates_then_launches_when_no_pte(
        tmp_path, monkeypatch, fake_pte):
    """The headline case: ``use_pte`` on, folder exists, no ``.pte``.
    The handler MUST generate the project in place and then hand it to
    the launcher."""
    from mira.ui.pages import share_cuts_page as scp
    library_root, event_root, cut, target = _setup_event_layout(
        tmp_path, with_pte=False)
    # The fixture leaves a stray photo behind; that's fine — the
    # generator needs media to write a project, so seed it explicitly.
    _seed_media_only(target)

    captured_launches: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.open_in_pte",
        lambda exe, project: captured_launches.append(
            (Path(exe), Path(project))))

    page = _make_minimal_share_page(
        scp, monkeypatch, tmp_path, cut, event_root, library_root,
        use_pte=True, pte_path=str(fake_pte))

    # The stub helper doesn't wire the standalone-generate slot — the
    # handler under test reaches into ``_generate_pte_into_resolved_folder``
    # (the wrapper around ``_generate_pte_into_folder``). Bind both as
    # bound-method equivalents the stub can call.
    page._generate_pte_into_resolved_folder = (
        lambda c, folder: scp.ShareCutsPage._generate_pte_into_resolved_folder(
            page, c, folder))
    page._generate_pte_into_folder = (
        lambda c, folder, *, overwrite=False:
        _stub_generate_into_folder(folder, overwrite=overwrite))

    page._on_open_exported_in_pte(cut.tag)

    # Generator ran — a project file showed up in the folder.
    written = list(target.glob("*.pte"))
    assert len(written) == 1
    # Launcher fired against the just-written project.
    assert captured_launches == [(fake_pte, written[0])]


def test_open_in_pte_does_not_autogenerate_when_use_pte_off(
        tmp_path, monkeypatch, fake_pte):
    """Even though the handler is upstream-gated on ``use_pte``, the
    defensive check inside it must also hold: if a stale signal reaches
    the handler with ``use_pte=False`` (a race during a setting flip),
    the generator must NOT fire."""
    from mira.ui.pages import share_cuts_page as scp
    library_root, event_root, cut, target = _setup_event_layout(
        tmp_path, with_pte=False)
    _seed_media_only(target)

    captured_launches: list[tuple] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.open_in_pte",
        lambda exe, project: captured_launches.append((exe, project)))

    page = _make_minimal_share_page(
        scp, monkeypatch, tmp_path, cut, event_root, library_root,
        use_pte=False, pte_path=str(fake_pte))

    generated: list[Path] = []
    page._generate_pte_into_resolved_folder = (
        lambda c, folder: generated.append(folder) or None)

    page._on_open_exported_in_pte(cut.tag)

    assert generated == []
    assert captured_launches == []
    assert not list(target.glob("*.pte"))


def test_open_in_pte_does_not_autogenerate_when_folder_missing(
        tmp_path, monkeypatch, fake_pte):
    """Folder is gone (deleted, moved out-of-band). The resolver
    degrades ``folder_exists=False``; the handler must skip generation
    rather than scribble a project into the fallback parent."""
    from mira.ui.pages import share_cuts_page as scp
    library_root, event_root, cut, target = _setup_event_layout(
        tmp_path, with_pte=False)
    # Wipe the bundle entirely so the resolver falls back to a parent.
    import shutil
    shutil.rmtree(target)

    captured_launches: list[tuple] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.open_in_pte",
        lambda exe, project: captured_launches.append((exe, project)))

    page = _make_minimal_share_page(
        scp, monkeypatch, tmp_path, cut, event_root, library_root,
        use_pte=True, pte_path=str(fake_pte))

    generated: list[Path] = []
    page._generate_pte_into_resolved_folder = (
        lambda c, folder: generated.append(folder) or None)

    page._on_open_exported_in_pte(cut.tag)

    assert generated == []
    assert captured_launches == []


def test_open_in_pte_uses_existing_pte_when_present(
        tmp_path, monkeypatch, fake_pte):
    """spec/117 — when the bundle already has a ``.pte``, the auto-
    generate branch must NOT fire. We launch the existing project."""
    from mira.ui.pages import share_cuts_page as scp
    library_root, event_root, cut, target = _setup_event_layout(
        tmp_path, with_pte=True)

    captured_launches: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.open_in_pte",
        lambda exe, project: captured_launches.append(
            (Path(exe), Path(project))))

    page = _make_minimal_share_page(
        scp, monkeypatch, tmp_path, cut, event_root, library_root,
        use_pte=True, pte_path=str(fake_pte))

    generated: list[Path] = []
    page._generate_pte_into_resolved_folder = (
        lambda c, folder: generated.append(folder) or None)

    page._on_open_exported_in_pte(cut.tag)

    assert generated == []
    assert captured_launches == [(fake_pte, target / "slideshow.pte")]


# ── Cross-event surface (CrossEventCutDetailDialog) ─────────────


@dataclass
class _CrossRow:
    cut_id: str = "cut-x"
    tag: str = "best_of_2026"
    anchor_event_id: str = "anchor"
    anchor_event_name: str = "Anchor"
    member_count: int = 0
    last_exported_at: Optional[str] = "2026-06-25T10:00:00Z"
    aspect: str = "16:9"
    photo_s: float = 6.0
    overlay_mode: str = "embedded"


class _FakeUmbrella:
    def __init__(self, settings) -> None:
        self.settings = self
        self._settings = settings

    def load(self):
        return self._settings

    def library_gateway(self):                                 # noqa: D401
        class _LG:
            def cross_event_cut_members(self, _cid):
                return []
        return _LG()

    @property
    def index(self):                                           # noqa: D401
        class _Idx:
            def get(self, _eid):
                return None
        return _Idx()


def _stub_generate_into_folder(folder: Path, *, overwrite: bool) -> Path:
    """Cheap drop-in for ``_generate_pte_into_folder`` that just writes
    a placeholder ``.pte`` so the test can assert generator-fired
    without paying the cost of the real generator + skeleton load."""
    target = folder / "slideshow.pte"
    target.write_bytes(b"\xef\xbb\xbf[Main]\r\nGENERATED\r\n")
    return target


def test_cross_event_open_in_pte_autogenerates_when_missing(
        qapp, tmp_path, monkeypatch, fake_pte):
    """Cross-event sibling of the per-event auto-generate test. The
    dialog's own ``_generate_pte_into_folder`` is the seam — we stub
    it so the assertion stays focused on the dispatch path."""
    from mira.ui.pages.cross_event_cut_detail_dialog import (
        CrossEventCutDetailDialog,
    )
    library_root = tmp_path / "lib"
    target = library_root / "Cuts" / "Cross-event" / "best_of_2026"
    target.mkdir(parents=True)
    (target / "001_a.jpg").write_bytes(b"PHOTO")        # media only, no .pte
    monkeypatch.setattr("mira.paths.library_root",
                        lambda: library_root)

    captured_launches: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.open_in_pte",
        lambda exe, project: captured_launches.append(
            (Path(exe), Path(project))))

    settings = _Settings(use_pte=True, pte_path=str(fake_pte),
                         cuts_export_root="")
    gw = _FakeUmbrella(settings)
    row = _CrossRow()

    dlg = CrossEventCutDetailDialog(gw, row)
    # Override the dialog's generator with a cheap stub so we assert
    # the dispatch path without running the real skeleton-load.
    dlg._generate_pte_into_folder = (
        lambda folder: _stub_generate_into_folder(folder, overwrite=True))

    dlg._on_open_in_pte()

    written = list(target.glob("*.pte"))
    assert len(written) == 1
    assert captured_launches == [(fake_pte, written[0])]


def test_cross_event_open_in_pte_does_not_autogenerate_when_use_pte_off(
        qapp, tmp_path, monkeypatch, fake_pte):
    """``use_pte`` off → no generation, no launch (cross-event)."""
    from mira.ui.pages.cross_event_cut_detail_dialog import (
        CrossEventCutDetailDialog,
    )
    library_root = tmp_path / "lib"
    target = library_root / "Cuts" / "Cross-event" / "best_of_2026"
    target.mkdir(parents=True)
    (target / "001_a.jpg").write_bytes(b"PHOTO")
    monkeypatch.setattr("mira.paths.library_root",
                        lambda: library_root)

    captured_launches: list[tuple] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.open_in_pte",
        lambda exe, project: captured_launches.append((exe, project)))

    settings = _Settings(use_pte=False, pte_path=str(fake_pte),
                         cuts_export_root="")
    gw = _FakeUmbrella(settings)
    row = _CrossRow()

    dlg = CrossEventCutDetailDialog(gw, row)
    generated: list[Path] = []
    dlg._generate_pte_into_folder = (
        lambda folder: generated.append(folder) or None)

    dlg._on_open_in_pte()

    assert generated == []
    assert captured_launches == []
    assert not list(target.glob("*.pte"))


# ── Generate PTE button slot (per-event) ────────────────────────


class _FakeMessageBox:
    """Drop-in for QMessageBox that absorbs construction + setters +
    exec() so a non-QWidget parent (our test ``_Stub``) doesn't trip
    Qt's overload check. The slot only uses informational popups so
    we don't need to track return values."""

    Icon = type("Icon", (), {"NoIcon": 0, "Warning": 1})

    class ButtonRole:
        ActionRole = 0
        RejectRole = 1
        DestructiveRole = 2

    def __init__(self, *a, **kw) -> None:
        self.text = ""

    def setIcon(self, *_):
        pass

    def setWindowTitle(self, *_):
        pass

    def setText(self, t):
        self.text = t

    def exec(self):
        return 0

    def addButton(self, *_a, **_kw):
        class _Btn:
            def clicked(self):                                # noqa: D401
                return self
            def connect(self, *_a, **_kw):                    # noqa: D401
                return None
        return _Btn()


def test_generate_pte_slot_writes_pte_into_resolved_folder(
        tmp_path, monkeypatch, fake_pte):
    """The Generate PTE button calls ``_on_generate_pte_for_cut``,
    which resolves the location and writes a ``.pte`` even when none
    was present. The launcher is NOT invoked — Generate ≠ Launch."""
    from mira.ui.pages import share_cuts_page as scp
    library_root, event_root, cut, target = _setup_event_layout(
        tmp_path, with_pte=False)
    _seed_media_only(target)

    captured_launches: list[tuple] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.open_in_pte",
        lambda exe, project: captured_launches.append((exe, project)))

    # Replace QMessageBox in the share_cuts_page module so the slot's
    # ``QMessageBox(self)`` call doesn't trip on our non-QWidget Stub.
    monkeypatch.setattr(scp, "QMessageBox", _FakeMessageBox)

    page = _make_minimal_share_page(
        scp, monkeypatch, tmp_path, cut, event_root, library_root,
        use_pte=True, pte_path=str(fake_pte))
    page._add_open_buttons = lambda box, folder, pte_file: None
    page._sync_exported_actions = lambda c: None
    page._generate_pte_into_folder = (
        lambda c, folder, *, overwrite=False:
        _stub_generate_into_folder(folder, overwrite=overwrite))
    page._generate_pte_into_resolved_folder = (
        lambda c, folder: scp.ShareCutsPage._generate_pte_into_resolved_folder(
            page, c, folder))
    page._on_generate_pte_for_cut = (
        lambda cid: scp.ShareCutsPage._on_generate_pte_for_cut(page, cid))

    page._on_generate_pte_for_cut(cut.tag)

    # A .pte landed in the folder; the launcher was NOT called.
    assert (target / "slideshow.pte").is_file()
    assert captured_launches == []
