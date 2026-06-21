"""The 2026-06-21 Help consolidation (Nelson): the per-surface "?" help
buttons were retired in favour of ONE shared Help button on the title
bar, in the same spot on every surface. F1 and the title-bar click both
route through MainWindow's ``_on_titlebar_help`` to the current
surface's ``show_help()`` callable (chassis pages forward to their
sub-page), falling back to the global locked-keymap reference.

This file pins the new contract:

* The :class:`TitleBar` carries the canonical ``help_button`` (object
  name ``"HelpButton"``) — one entry point, app-wide.
* Surfaces that used to host their own ``_help_btn`` (QuickSweepPage,
  CutSessionPage, CutDetailPage) no longer do — the regression-check
  asserts the attribute is gone so a future round can't silently
  reintroduce a per-surface helper.
* The raw-``QPushButton(tr("?"))`` regression guard stays — the legacy
  pattern must never re-appear.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QWidget


def _help_buttons(w: QWidget) -> list[QPushButton]:
    return [b for b in w.findChildren(QPushButton)
            if b.objectName() == "HelpButton"]


def test_title_bar_carries_canonical_help_button(qapp):
    """The TitleBar widget hosts the ONE help button used app-wide
    (Nelson 2026-06-21). Exposed as the ``help_button`` attribute so
    MainWindow wires its click + F1 to ``_on_titlebar_help``; styled
    via the shared ghost_button factory (role ``Ghost``)."""
    from PyQt6.QtWidgets import QPushButton
    from mira.ui.design.title_bar import TitleBar
    bar = TitleBar()
    assert isinstance(bar.help_button, QPushButton)
    # The label carries the "?" glyph + the F1 shortcut hint.
    assert "?" in bar.help_button.text()
    assert "F1" in bar.help_button.text()


def test_quick_sweep_page_has_no_per_surface_help_button(qapp):
    """spec/63 §4 / Nelson 2026-06-21 — Quick Sweep's viewer chrome no
    longer hosts its own "?" button; Help is in the title bar. Regression
    guard: any future reintroduction of a per-surface help button on this
    page trips this check."""
    from mira.ui.pages.quick_sweep_page import QuickSweepPage
    page = QuickSweepPage()
    assert _help_buttons(page) == []
    assert not hasattr(page, "_help_btn")


def test_cut_session_page_has_no_per_surface_help_button(qapp, tmp_path):
    """Cut session's chrome doesn't carry a "?" anymore (Nelson
    2026-06-21). The chassis exposes ``show_help`` so the title-bar
    Help routes here, but the click target itself lives on the title
    bar, not the page."""
    import itertools
    from mira.gateway.event_gateway import EventGateway
    from mira.shared.cut_session import CutSession
    from mira.store.repo import EventStore
    from mira.ui.shared.cut_session_page import CutSessionPage
    from tests.test_cut_session import _draft
    from tests.test_gateway_cuts import _doc, _now

    store = EventStore.create(tmp_path / "event.db", event_id="evt-h")
    store.save_document(_doc())
    counter = itertools.count(1)
    gw = EventGateway(store, now=_now,
                      new_id=lambda: f"id-{next(counter)}")
    try:
        session = CutSession.from_draft(gw, _draft())
        page = CutSessionPage(gw, session, event_root=tmp_path)
        assert _help_buttons(page) == []
        assert not hasattr(page, "_help_btn")
    finally:
        gw.close()


def test_cut_detail_page_has_no_per_surface_help_button(qapp):
    """Same as the session page: Help moved to the title bar; the cut
    detail page no longer carries its own button."""
    from mira.ui.shared.cut_detail_page import CutDetailPage
    page = CutDetailPage()
    assert _help_buttons(page) == []
    assert not hasattr(page, "_help_btn")


def test_every_known_help_button_is_the_canonical_role(qapp):
    """If a contributor reintroduces a raw `QPushButton(tr("?"))` with
    a different objectName, this catches it — every "?" labelled
    button in the surface modules must wear the HelpButton role."""
    import pathlib
    import mira.ui as ui_pkg
    root = pathlib.Path(ui_pkg.__file__).resolve().parent

    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        # A raw QPushButton(tr("?")) without immediate help_button() use
        # is the regression signature.
        if 'QPushButton(tr("?"))' in text:
            offenders.append(str(path))
    assert offenders == [], (
        "Raw QPushButton(tr(\"?\")) reintroduced — use help_button() "
        f"from mira.ui.base.surface instead. Offenders: "
        f"{offenders}")
