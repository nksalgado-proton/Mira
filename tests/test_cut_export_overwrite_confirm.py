"""spec/148 — destructive-replace confirm on Overwrite.

When the user picks Overwrite and the target folder already has
contents (i.e., a prior bundle exists), :meth:`ShareCutsPage._on_export_cut`
must prompt before destroying that bundle. Cancel from the prompt
leaves everything intact; Replace proceeds and the prior contents are
cleared by the exporter. An empty target folder (or none at all) skips
the prompt — there's nothing to destroy.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from PyQt6.QtWidgets import QMessageBox

from mira.gateway.event_gateway import EventGateway
from mira.settings.model import Settings
from mira.store.repo import EventStore
from mira.ui.pages.share_cuts_page import (
    ExportChoices,
    ShareCutsPage,
)

from tests.test_gateway_cuts import _doc, _now


class _FakeAppGateway:
    """Duck-type of the app Gateway: settings + open_event. Mirrors
    ``tests.test_cuts_shell._FakeAppGateway`` — the share-page chassis
    only reads ``self.gateway.settings`` (with ``load()``) and
    ``self.gateway.open_event(event_id)`` from the umbrella."""

    def __init__(self, eg, settings: Settings) -> None:
        self._eg = eg
        self.settings = self  # makes ``gateway.settings.load()`` return settings
        self._settings = settings

    # Repo-shaped seam for the share-page persistence helper.
    def load(self) -> Settings:
        return self._settings

    def save(self, s: Settings) -> None:
        self._settings = s

    def open_event(self, event_id: str):
        return self._eg


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    # Materialise on-disk bytes for every Exported Media/ lineage row
    # so the rescan prune (filesystem = source of truth for the
    # exported tier) keeps them on Share entry.
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


def _teardown_shells(qapp, built):
    """Symmetric teardown: hide → close → schedule deletion → drain.

    ``deleteLater`` queues a ``DeferredDelete`` event; a plain
    ``processEvents`` doesn't drain that event class on its own. The
    explicit ``sendPostedEvents(..., DeferredDelete)`` is what makes
    the C++ destructor actually run while the test owning the widget
    is still on the stack. Without that drain, Python's later GC
    sweep finds the still-alive C++ widget and disposes of it during
    an unrelated test's paint cycle — the cut_play access-violation
    flake (spec/89 §12.6)."""
    from PyQt6.QtCore import QEvent
    for shell in built:
        try:
            shell.hide()
            shell.close()
            shell.deleteLater()
        except RuntimeError:
            # C++ side already gone (a prior test torn it down out of
            # band). Nothing to do.
            pass
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def make_shell(qapp):
    """Build :class:`ShareCutsPage` instances and tear them down at the
    end of the test.

    Without explicit teardown each ``ShareCutsPage`` lingers as a live
    Qt widget under the session-scoped ``QApplication``, and Python's
    garbage collector may dispose of its C++ side mid-paint in a later
    test — surfacing as the cut_play scrubber paintEvent access
    violation flagged on verify.bat."""
    built: list[ShareCutsPage] = []

    def _factory(gw, **settings_over) -> ShareCutsPage:
        settings = Settings(**settings_over)
        shell = ShareCutsPage(_FakeAppGateway(gw, settings))
        assert shell.open_event("evt-c")
        built.append(shell)
        return shell

    yield _factory
    _teardown_shells(qapp, built)


# --------------------------------------------------------------------------- #
# Helper-level: _is_non_empty_folder
# --------------------------------------------------------------------------- #


def test_is_non_empty_folder_predicate(qapp, gw, tmp_path, make_shell):
    shell = make_shell(gw)
    missing = tmp_path / "never_existed"
    empty = tmp_path / "empty"
    empty.mkdir()
    full = tmp_path / "full"
    full.mkdir()
    (full / "x.txt").write_bytes(b"x")
    assert not shell._is_non_empty_folder(missing)   # noqa: SLF001
    assert not shell._is_non_empty_folder(empty)     # noqa: SLF001
    assert shell._is_non_empty_folder(full)          # noqa: SLF001


# --------------------------------------------------------------------------- #
# Page-level: _on_export_cut + confirm
# --------------------------------------------------------------------------- #


def test_overwrite_prompts_when_target_non_empty_and_cancel_leaves_it(
        qapp, gw, tmp_path, monkeypatch, make_shell):
    """A non-empty target folder + Overwrite → confirm fires. Cancel
    leaves the existing bundle intact and no export runs."""
    shell = make_shell(gw)
    cut = next(iter(gw.cuts()))
    custom = tmp_path / "Elsewhere" / "my_export"
    custom.mkdir(parents=True)
    sentinel = custom / "PRIOR_BUNDLE.jpg"
    sentinel.write_bytes(b"PRIOR")

    # The export-target dialog returns Overwrite at the picked folder.
    shell._exec_target_dialog = lambda default, c: ExportChoices(    # noqa: SLF001
        target=custom, overwrite_existing=True)
    # Stub the confirm so we know it fired and choose Cancel.
    confirm_calls = []

    def _stub_confirm(cut_arg, target):
        confirm_calls.append(target)
        return False                                          # Cancel
    shell._confirm_overwrite = _stub_confirm                   # noqa: SLF001
    # Make sure the summary popup never blocks the test session.
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    shell._on_export_cut(cut.id)                              # noqa: SLF001

    # Confirm DID fire — once, against the picked target.
    assert confirm_calls == [custom]
    # The prior bundle survived intact.
    assert sentinel.exists()
    assert sentinel.read_bytes() == b"PRIOR"
    # No new exported member files showed up.
    new_files = [p for p in custom.iterdir() if p.name != sentinel.name]
    assert new_files == []


def test_overwrite_replace_proceeds_and_clears_prior(
        qapp, gw, tmp_path, monkeypatch, make_shell):
    """A non-empty target folder + Overwrite + Replace → the prior
    bundle's stale members are cleared, the new bundle lands."""
    shell = make_shell(gw)
    cut = next(iter(gw.cuts()))
    custom = tmp_path / "Elsewhere" / "my_export"
    custom.mkdir(parents=True)
    sentinel = custom / "PRIOR_BUNDLE.jpg"
    sentinel.write_bytes(b"PRIOR")

    shell._exec_target_dialog = lambda default, c: ExportChoices(    # noqa: SLF001
        target=custom, overwrite_existing=True)
    shell._confirm_overwrite = lambda cut_arg, target: True   # noqa: SLF001 — Replace
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    shell._on_export_cut(cut.id)                              # noqa: SLF001

    assert not sentinel.exists()                              # stale gone
    # New bundle's sequence-prefixed files showed up under the same folder.
    new_names = sorted(p.name for p in custom.iterdir() if p.is_file())
    assert new_names, "expected the new bundle to land in the cleared folder"
    assert any(n.startswith("001_") for n in new_names)


def test_overwrite_skips_prompt_when_target_empty(
        qapp, gw, tmp_path, monkeypatch, make_shell):
    """An empty target folder under Overwrite has nothing to destroy,
    so the confirm prompt MUST be skipped — the user has only one
    click to make, not two."""
    shell = make_shell(gw)
    cut = next(iter(gw.cuts()))
    custom = tmp_path / "Elsewhere" / "new_export"
    custom.mkdir(parents=True)                                # empty
    shell._exec_target_dialog = lambda default, c: ExportChoices(    # noqa: SLF001
        target=custom, overwrite_existing=True)
    confirm_calls = []

    def _stub_confirm(cut_arg, target):
        confirm_calls.append(target)
        return False
    shell._confirm_overwrite = _stub_confirm                   # noqa: SLF001
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    shell._on_export_cut(cut.id)                              # noqa: SLF001

    # No prompt — nothing to destroy.
    assert confirm_calls == []
    # The export still ran.
    assert any(p.is_file() for p in custom.iterdir())


def test_keep_both_skips_prompt_even_on_non_empty(
        qapp, gw, tmp_path, monkeypatch, make_shell):
    """Keep-both never destroys anything (it disambiguates to ``(2)/``),
    so the confirm prompt must be skipped regardless of the target's
    contents. The prior bundle stays addressable."""
    shell = make_shell(gw)
    cut = next(iter(gw.cuts()))
    custom = tmp_path / "Elsewhere" / "kept_both"
    custom.mkdir(parents=True)
    sentinel = custom / "PRIOR_BUNDLE.jpg"
    sentinel.write_bytes(b"PRIOR")

    shell._exec_target_dialog = lambda default, c: ExportChoices(    # noqa: SLF001
        target=custom, overwrite_existing=False)              # Keep-both
    confirm_calls = []

    def _stub_confirm(cut_arg, target):
        confirm_calls.append(target)
        return False
    shell._confirm_overwrite = _stub_confirm                   # noqa: SLF001
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    shell._on_export_cut(cut.id)                              # noqa: SLF001

    assert confirm_calls == []
    # The prior bundle's sentinel survives.
    assert sentinel.exists()
    # The new bundle landed at the disambiguated sibling.
    sibling = custom.with_name(custom.name + " (2)")
    assert sibling.exists()
    assert any(p.is_file() for p in sibling.iterdir())


@pytest.mark.parametrize(
    "mode",
    [
        dict(overwrite_existing=True),                  # Overwrite
        dict(overwrite_existing=False),                 # Keep both
        dict(overwrite_existing=False, only_new=True),  # Only new
    ],
)
def test_export_never_writes_pte(
        qapp, gw, tmp_path, monkeypatch, make_shell, mode):
    """spec/158 regression — the export NEVER writes the ``.pte`` in any
    mode. The project is written only via the explicit "Create PTE
    project" / "Generate PTE" button. (A bug once made the export
    overwrite a hand-edited project — never again.) ``use_pte`` is on so
    the old auto-generate path would have fired."""
    shell = make_shell(gw, use_pte=True)
    cut = next(iter(gw.cuts()))
    custom = tmp_path / "Elsewhere" / "pte_mode"
    custom.mkdir(parents=True)
    shell._exec_target_dialog = lambda default, c: ExportChoices(  # noqa: SLF001
        target=custom, **mode)
    shell._confirm_overwrite = lambda cut_arg, target: True       # noqa: SLF001
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    gen_calls = []
    shell._generate_pte_into_folder = (                           # noqa: SLF001
        lambda c, f, **kw: gen_calls.append((c, f, kw)))

    shell._on_export_cut(cut.id)                                  # noqa: SLF001

    # The export produced files but touched no .pte at all.
    assert gen_calls == []
    assert not list(custom.glob("*.pte"))


def test_generate_pte_asks_before_overwriting_existing(
        qapp, gw, tmp_path, monkeypatch, make_shell):
    """spec/158 — the explicit Generate-PTE flow must PROMPT before
    replacing an existing ``.pte`` and leave it untouched on Cancel."""
    shell = make_shell(gw, use_pte=True)
    cut = next(iter(gw.cuts()))
    folder = tmp_path / "bundle"
    folder.mkdir()
    # A hand-edited project the user must not lose. The canonical name
    # is "<cut.tag>.pte".
    existing = folder / f"{cut.tag}.pte"
    existing.write_text("MY 2 HOURS OF EDITS", encoding="utf-8")

    # If generation runs it would clobber the file — fail loudly if so.
    shell._generate_pte_into_folder = (                           # noqa: SLF001
        lambda c, f, **kw: pytest.fail("must not regenerate on Cancel"))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    # Cancel the overwrite prompt.
    shell._confirm_pte_overwrite = lambda p: False                # noqa: SLF001
    shell._generate_pte_for_folder(cut, folder)                   # noqa: SLF001

    # The user's project is byte-for-byte intact.
    assert existing.read_text(encoding="utf-8") == "MY 2 HOURS OF EDITS"


# --------------------------------------------------------------------------- #
# Last-choice persistence
# --------------------------------------------------------------------------- #


def test_remember_overwrite_choice_persists_to_settings(
        qapp, gw, tmp_path, make_shell):
    """The radio choice is sticky: after picking Overwrite once, the
    settings field flips to True so the next dialog opens with the
    Overwrite radio pre-selected."""
    shell = make_shell(gw)
    # Sanity — fresh install defaults to Keep-both.
    assert shell._settings().cut_export_overwrite_default is False   # noqa: SLF001
    shell._remember_overwrite_choice(True)                    # noqa: SLF001
    assert shell._settings().cut_export_overwrite_default is True    # noqa: SLF001
    # Flipping back stays sticky.
    shell._remember_overwrite_choice(False)                   # noqa: SLF001
    assert shell._settings().cut_export_overwrite_default is False   # noqa: SLF001


# --------------------------------------------------------------------------- #
# Fixture hygiene — the cleanup itself
# --------------------------------------------------------------------------- #


def test_make_shell_fixture_tears_down_built_widgets(qapp, gw, tmp_path):
    """The ``make_shell`` finalizer MUST hide → close → deleteLater
    → drain so the underlying C++ widget is gone by test exit. Without
    this, the leaked :class:`ShareCutsPage` instances accumulate under
    the session-scoped QApplication and a later test's paint cycle can
    crash on their zombie children (the cut_play scrubber paintEvent
    access violation, spec/89 §12.6).

    This is the meta-test: we manually exercise the same teardown body
    and then verify the C++ wrapper is gone. ``sip.isdeleted`` returns
    True only after Qt's destructor has actually run."""
    from PyQt6 import sip
    a = ShareCutsPage(_FakeAppGateway(gw, Settings()))
    assert a.open_event("evt-c")
    b = ShareCutsPage(_FakeAppGateway(gw, Settings()))
    assert b.open_event("evt-c")
    assert not sip.isdeleted(a)
    assert not sip.isdeleted(b)

    # The body the fixture's yield runs at teardown.
    _teardown_shells(qapp, [a, b])

    # Both widgets' C++ sides are gone. A leaked widget would still
    # be ``not sip.isdeleted`` here, and would later wake up during
    # someone else's paint cycle.
    assert sip.isdeleted(a)
    assert sip.isdeleted(b)
