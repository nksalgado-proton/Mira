"""spec/117 — persistent post-export actions on an exported Cut.

The resolver re-runs ``resolve_event_cut_target`` /
``resolve_cross_event_cut_target`` (the same call the exporter used) and
probes disk. ``ExportedCutLocation`` carries the folder + ``.pte`` + a
``folder_exists`` flag that drives the UI's PTE visibility.

These tests pin:
  * a never-exported Cut hides both actions;
  * an exported Cut with the exact folder + a ``.pte`` enables both;
  * Open in PTE is gated additionally on ``use_pte`` +
    ``pte_launch_available`` (so the Settings toggle still controls the
    button);
  * a missing/disambiguated folder degrades to the parent ``Cuts/…``
    and hides Open in PTE — no crash;
  * the cross-event resolver routes the location under
    ``<library_root>/Cuts/Cross-event/<cut>/`` and the same gating
    applies.
  * the ``.pte`` discovery prefers ``slideshow*.pte`` over other names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest

from mira.shared import pte_launch
from mira.shared.exported_cut_actions import (
    ExportedCutLocation,
    find_pte_in,
    is_exported,
    resolve_cross_event_cut_location,
    resolve_event_cut_location,
)


# ── Fixtures ────────────────────────────────────────────────────


@dataclass
class _Cut:
    """Lightweight stand-in for the Cut row. The resolver only reads
    ``tag`` + ``last_exported_at`` — no need to spin up a real store."""

    tag: str = "best_macro_shots"
    last_exported_at: Optional[str] = None


@dataclass
class _CrossCut:
    tag: str = "best_macro_shots"
    last_exported_at: Optional[str] = None


@pytest.fixture
def fake_pte(tmp_path: Path) -> Path:
    """A real file on disk that ``pte_launch_available`` will accept
    as a valid executable — saves stubbing the predicate."""
    exe = tmp_path / "FakePTE.exe"
    exe.write_bytes(b"PTE")
    return exe


# ── is_exported ────────────────────────────────────────────────


def test_is_exported_only_true_when_last_exported_at_is_set():
    """The Cut is the source of truth: stamping ``last_exported_at``
    is what makes the persistent actions appear."""
    assert is_exported(_Cut(last_exported_at=None)) is False
    assert is_exported(_Cut(last_exported_at="")) is False
    assert is_exported(_Cut(last_exported_at="2026-06-23T10:00:00Z"))


# ── find_pte_in ───────────────────────────────────────────────


def test_find_pte_in_prefers_slideshow_named_files(tmp_path):
    """Generator output is named ``slideshow.pte`` (and disambiguated
    ``slideshow (2).pte``); the discovery prefers them over other
    names (a user-imported ``other.pte`` shouldn't beat the bundled
    project)."""
    folder = tmp_path / "bundle"
    folder.mkdir()
    (folder / "other.pte").write_text("other")
    (folder / "slideshow.pte").write_text("ok")
    assert find_pte_in(folder).name == "slideshow.pte"


def test_find_pte_in_falls_back_to_any_pte(tmp_path):
    folder = tmp_path / "bundle"
    folder.mkdir()
    (folder / "my_project.pte").write_text("custom")
    assert find_pte_in(folder).name == "my_project.pte"


def test_find_pte_in_returns_none_when_folder_missing(tmp_path):
    assert find_pte_in(tmp_path / "no_such_folder") is None


def test_find_pte_in_picks_undisambiguated_first(tmp_path):
    """``slideshow.pte`` (the first export) sorts before
    ``slideshow (2).pte`` so re-exports are NOT preferred over the
    original — the user might have edited the original in PTE
    already."""
    folder = tmp_path / "bundle"
    folder.mkdir()
    (folder / "slideshow (2).pte").write_text("re-export")
    (folder / "slideshow.pte").write_text("first")
    assert find_pte_in(folder).name == "slideshow.pte"


# ── Per-event resolver ────────────────────────────────────────


def _setup_event_layout(tmp_path: Path, *, with_pte: bool = True):
    """Build a library + event + exported Cut bundle on disk.

    Mirrors :func:`resolve_event_cut_target`'s same-volume layout:
    ``<library_root>/Cuts/<event slug>/<cut tag>/``.
    Returns ``(library_root, event_root, cut, target_folder)``."""
    library_root = tmp_path / "lib"
    event_root = library_root / "Costa Rica 2026"
    event_root.mkdir(parents=True)
    cut = _Cut(tag="best_macro_shots",
               last_exported_at="2026-06-23T10:00:00Z")
    target_folder = (
        library_root / "Cuts" / "costa_rica_2026" / "best_macro_shots")
    target_folder.mkdir(parents=True)
    (target_folder / "001_p1.jpg").write_bytes(b"frame")
    if with_pte:
        (target_folder / "slideshow.pte").write_bytes(
            b"\xef\xbb\xbf[Main]\r\n")
    return library_root, event_root, cut, target_folder


def test_resolve_event_cut_location_exact_hit(tmp_path):
    """The common case: one export, exact folder still on disk, a
    ``.pte`` discoverable. ``folder_exists`` AND ``pte_available``."""
    library_root, event_root, cut, target = _setup_event_layout(tmp_path)
    loc = resolve_event_cut_location(
        cut=cut, event_root=event_root, event_name="Costa Rica 2026",
        library_root=library_root, cuts_export_root=None)
    assert loc.folder == target
    assert loc.folder_exists is True
    assert loc.pte_file is not None
    assert loc.pte_file.name == "slideshow.pte"
    assert loc.pte_available is True


def test_resolve_event_cut_location_no_pte_in_folder(tmp_path):
    """Folder is there but the ``.pte`` is gone (user deleted it,
    legacy export). The folder action still resolves; Open in PTE
    hides."""
    library_root, event_root, cut, target = _setup_event_layout(
        tmp_path, with_pte=False)
    loc = resolve_event_cut_location(
        cut=cut, event_root=event_root, event_name="Costa Rica 2026",
        library_root=library_root, cuts_export_root=None)
    assert loc.folder_exists is True
    assert loc.pte_file is None
    assert loc.pte_available is False


def test_resolve_event_cut_location_missing_folder_falls_back_to_parent(
        tmp_path):
    """Folder deleted / disambiguated to ``… (2)``. The resolver
    returns the parent ``Cuts/<event slug>/`` so the user can still
    find the bundle — and hides Open in PTE (no project to point at)."""
    library_root, event_root, cut, _target = _setup_event_layout(tmp_path)
    # Remove the disk bundle entirely.
    bundle = (library_root / "Cuts" / "costa_rica_2026"
              / "best_macro_shots")
    (bundle / "slideshow.pte").unlink()
    (bundle / "001_p1.jpg").unlink()
    bundle.rmdir()
    loc = resolve_event_cut_location(
        cut=cut, event_root=event_root, event_name="Costa Rica 2026",
        library_root=library_root, cuts_export_root=None)
    assert loc.folder_exists is False
    assert loc.folder == library_root / "Cuts" / "costa_rica_2026"
    assert loc.pte_file is None
    assert loc.pte_available is False


def test_resolve_event_cut_location_walks_up_when_parent_also_missing(
        tmp_path):
    """The parent chain might be gone too (the entire ``Cuts/`` tree
    was rm-rf'd). The resolver walks up until it lands on a real
    directory — never returns a non-existent folder for Explorer."""
    library_root, event_root, cut, target = _setup_event_layout(tmp_path)
    import shutil
    shutil.rmtree(library_root / "Cuts")
    loc = resolve_event_cut_location(
        cut=cut, event_root=event_root, event_name="Costa Rica 2026",
        library_root=library_root, cuts_export_root=None)
    assert loc.folder_exists is False
    assert loc.folder.is_dir()


# ── Cross-event resolver ──────────────────────────────────────


def test_resolve_cross_event_cut_location_routes_under_cross_event(tmp_path):
    """spec/105 §2 — the cross-event home is
    ``<library_root>/Cuts/Cross-event/<cut>/``."""
    library_root = tmp_path / "lib"
    target = library_root / "Cuts" / "Cross-event" / "best_of_2026"
    target.mkdir(parents=True)
    (target / "001_p1.jpg").write_bytes(b"frame")
    (target / "slideshow.pte").write_bytes(b"\xef\xbb\xbf[Main]\r\n")
    cut = _CrossCut(tag="best_of_2026",
                    last_exported_at="2026-06-23T10:00:00Z")
    loc = resolve_cross_event_cut_location(
        cut_tag=cut.tag,
        library_root=library_root,
        cuts_export_root=None,
    )
    assert loc.folder == target
    assert loc.folder_exists is True
    assert loc.pte_file is not None


def test_resolve_cross_event_cut_location_missing_folder_falls_back(tmp_path):
    library_root = tmp_path / "lib"
    (library_root / "Cuts" / "Cross-event").mkdir(parents=True)
    cut = _CrossCut(tag="best_of_2026",
                    last_exported_at="2026-06-23T10:00:00Z")
    loc = resolve_cross_event_cut_location(
        cut_tag=cut.tag,
        library_root=library_root,
        cuts_export_root=None,
    )
    assert loc.folder_exists is False
    assert loc.folder == library_root / "Cuts" / "Cross-event"


# ── Action wiring on CutDetailPage ────────────────────────────


@dataclass
class _Settings:
    use_pte: bool = False
    pte_path: str = ""
    cuts_export_root: str = ""


@pytest.fixture
def cut_detail_page(qapp):
    """A construction-only :class:`CutDetailPage` — no need to bind
    a Cut to test the visibility setter + signal wiring."""
    from mira.ui.shared.cut_detail_page import CutDetailPage
    page = CutDetailPage(show_export=True, show_play=True)
    return page


def test_detail_page_hides_actions_by_default(cut_detail_page):
    """No call to ``set_exported_actions`` (e.g. a never-exported Cut
    just opened) → both buttons stay hidden."""
    assert cut_detail_page._open_folder_btn.isVisible() is False
    assert cut_detail_page._open_pte_btn.isVisible() is False


def test_detail_page_set_exported_actions_toggles_buttons(
        cut_detail_page, qapp):
    """The host's resolved gates flow through ``set_exported_actions``.
    Buttons are off-screen until the page is shown, so check visibility
    via the property setter rather than ``isVisible()``."""
    cut_detail_page.show()
    qapp.processEvents()
    cut_detail_page.set_exported_actions(show_folder=True, show_pte=True)
    assert cut_detail_page._open_folder_btn.isVisibleTo(cut_detail_page)
    assert cut_detail_page._open_pte_btn.isVisibleTo(cut_detail_page)
    cut_detail_page.set_exported_actions(show_folder=True, show_pte=False)
    assert cut_detail_page._open_folder_btn.isVisibleTo(cut_detail_page)
    assert not cut_detail_page._open_pte_btn.isVisibleTo(cut_detail_page)
    cut_detail_page.set_exported_actions(show_folder=False, show_pte=False)
    assert not cut_detail_page._open_folder_btn.isVisibleTo(cut_detail_page)
    assert not cut_detail_page._open_pte_btn.isVisibleTo(cut_detail_page)


def test_detail_page_emits_open_folder_with_cut_id(cut_detail_page):
    received: list[str] = []
    cut_detail_page.open_folder_requested.connect(received.append)
    cut_detail_page._cut_id = "cut-1"
    cut_detail_page._open_folder_btn.click()
    assert received == ["cut-1"]


def test_detail_page_emits_open_in_pte_with_cut_id(cut_detail_page):
    received: list[str] = []
    cut_detail_page.open_in_pte_requested.connect(received.append)
    cut_detail_page._cut_id = "cut-1"
    cut_detail_page._open_pte_btn.click()
    assert received == ["cut-1"]


def test_detail_page_no_emit_when_no_cut_id(cut_detail_page):
    """Defensive: clicking before a cut is loaded never fires the
    signal (the lambdas short-circuit on the falsy ``_cut_id``)."""
    received: list[str] = []
    cut_detail_page.open_folder_requested.connect(received.append)
    cut_detail_page.open_in_pte_requested.connect(received.append)
    cut_detail_page._cut_id = None
    cut_detail_page._open_folder_btn.click()
    cut_detail_page._open_pte_btn.click()
    assert received == []


# ── Host-level gating ──────────────────────────────────────────


def test_pte_gate_requires_use_pte_setting(tmp_path, fake_pte):
    """The Open-in-PTE gate is the intersection of:
      * the resolver finding a ``.pte`` (``pte_available``);
      * the settings toggle ``use_pte`` on;
      * ``pte_launch_available(pte_path)`` accepting the executable.
    Turn any one off → no PTE button."""
    library_root, event_root, cut, target = _setup_event_layout(tmp_path)
    loc = resolve_event_cut_location(
        cut=cut, event_root=event_root, event_name="Costa Rica 2026",
        library_root=library_root, cuts_export_root=None)
    assert loc.pte_available is True

    s = _Settings(use_pte=False, pte_path=str(fake_pte))
    pte_ok = (loc.pte_available and s.use_pte
              and pte_launch.pte_launch_available(s.pte_path))
    assert pte_ok is False

    s = _Settings(use_pte=True, pte_path="")
    pte_ok = (loc.pte_available and s.use_pte
              and pte_launch.pte_launch_available(s.pte_path))
    assert pte_ok is False

    s = _Settings(use_pte=True, pte_path=str(fake_pte))
    pte_ok = (loc.pte_available and s.use_pte
              and pte_launch.pte_launch_available(s.pte_path))
    assert pte_ok is True


def test_pte_gate_blocks_when_folder_missing(tmp_path, fake_pte):
    """Folder gone → ``pte_available`` False → the PTE button hides
    even with ``use_pte=True``."""
    library_root, event_root, cut, _ = _setup_event_layout(tmp_path)
    import shutil
    shutil.rmtree(library_root / "Cuts")
    loc = resolve_event_cut_location(
        cut=cut, event_root=event_root, event_name="Costa Rica 2026",
        library_root=library_root, cuts_export_root=None)
    s = _Settings(use_pte=True, pte_path=str(fake_pte))
    pte_ok = (loc.pte_available and s.use_pte
              and pte_launch.pte_launch_available(s.pte_path))
    assert pte_ok is False


# ── Host wiring: share_cuts_page handlers call the launchers ──


def test_share_cuts_page_open_folder_handler_calls_reveal(
        tmp_path, monkeypatch):
    """The handler resolves the location and hands ``folder`` to
    :func:`mira.shared.pte_launch.reveal_in_explorer`. We spy on the
    helper to avoid spawning Explorer in a headless test."""
    from mira.ui.pages import share_cuts_page as scp
    library_root, event_root, cut, target = _setup_event_layout(tmp_path)
    captured: list[Path] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.reveal_in_explorer",
        lambda folder: captured.append(Path(folder)))
    page = _make_minimal_share_page(
        scp, monkeypatch, tmp_path, cut, event_root, library_root)
    page._on_open_exported_folder(cut.tag)               # tag stands in for cut_id
    assert captured == [target]


def test_share_cuts_page_open_in_pte_handler_calls_launcher(
        tmp_path, monkeypatch, fake_pte):
    from mira.ui.pages import share_cuts_page as scp
    library_root, event_root, cut, target = _setup_event_layout(tmp_path)
    captured: list[tuple] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.open_in_pte",
        lambda exe, project: captured.append((Path(exe), Path(project))))
    page = _make_minimal_share_page(
        scp, monkeypatch, tmp_path, cut, event_root, library_root,
        use_pte=True, pte_path=str(fake_pte))
    page._on_open_exported_in_pte(cut.tag)
    assert captured == [(fake_pte, target / "slideshow.pte")]


def test_share_cuts_page_open_in_pte_handler_skips_when_use_pte_off(
        tmp_path, monkeypatch, fake_pte):
    """Bundle exists but has no ``.pte`` AND ``use_pte`` is off. The
    handler bails silently — auto-generation (spec/149 §2.B) needs
    ``use_pte`` on to run, and without a project the launcher has
    nothing to open. (When ``use_pte=True`` the contract flips to
    auto-generate; ``tests/test_open_in_pte_autogenerate.py`` covers
    that path.)"""
    from mira.ui.pages import share_cuts_page as scp
    library_root, event_root, cut, target = _setup_event_layout(
        tmp_path, with_pte=False)
    captured: list[tuple] = []
    monkeypatch.setattr(
        "mira.shared.pte_launch.open_in_pte",
        lambda exe, project: captured.append((exe, project)))
    page = _make_minimal_share_page(
        scp, monkeypatch, tmp_path, cut, event_root, library_root,
        use_pte=False, pte_path=str(fake_pte))
    page._on_open_exported_in_pte(cut.tag)
    assert captured == []
    # Nothing was written into the folder either.
    assert not list(target.glob("*.pte"))


# ── Helpers ──────────────────────────────────────────────────────


class _FakeEvent:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEG:
    def __init__(self, event_root: Path, cut: _Cut) -> None:
        self.event_root = event_root
        self._cut = cut
        self._event = _FakeEvent("Costa Rica 2026")

    def cut(self, _cut_id: str):
        return self._cut

    def event(self):
        return self._event


def _make_minimal_share_page(
    scp_module, monkeypatch, tmp_path: Path, cut: _Cut, event_root: Path,
    library_root: Path, *, use_pte: bool = False, pte_path: str = "",
):
    """Construct a bare :class:`ShareCutsPage`-shaped object that
    carries enough state for the spec/117 handlers to run. We DO NOT
    spin up the full page (avoids the gateway / Qt-host churn); the
    handlers we test only read ``_eg`` + ``_settings()``.

    Uses pytest's ``monkeypatch`` fixture to scope the
    ``mira.paths.library_root`` override to the calling test — a
    global override would leak into ``test_paths`` / ``test_first_run_library``."""
    settings = _Settings(use_pte=use_pte, pte_path=pte_path,
                         cuts_export_root="")
    eg = _FakeEG(event_root=event_root, cut=cut)

    class _Stub:
        pass

    stub = _Stub()
    stub._eg = eg
    stub._settings = lambda: settings
    stub._resolve_event_cut_location = (
        lambda c: scp_module.ShareCutsPage._resolve_event_cut_location(
            stub, c))
    stub._on_open_exported_folder = (
        lambda cid: scp_module.ShareCutsPage._on_open_exported_folder(
            stub, cid))
    stub._on_open_exported_in_pte = (
        lambda cid: scp_module.ShareCutsPage._on_open_exported_in_pte(
            stub, cid))

    # Pin the library_root resolver via ``monkeypatch`` so it
    # auto-reverts at test teardown — a global swap would corrupt
    # later tests that exercise the real first-run / pointer logic.
    monkeypatch.setattr("mira.paths.library_root",
                        lambda: library_root)
    return stub
