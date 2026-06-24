"""spec/132 — closed-event tile's PhotoCycler source is exported-only.

Pre-spec/132 ``_sample_pixmap_paths`` fell through to picked photos
then to any captured photo when ``exported_files()`` returned empty
(or raised). That defeats the point of a closed event's "highlight
reel": it would parade frames the user **explicitly did not export**.

After spec/132 the function returns ONLY ``eg.exported_files()``:
empty (or an exception) returns ``[]``, and PhotoCycler renders its
built-in "no photos" placeholder — never a non-exported capture.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.ui.base.event_card import EventCardData
from mira.ui.pages._event_card_data import (
    _sample_pixmap_paths,
    _populate_body_data,
)


def _lineage(relpath: str) -> SimpleNamespace:
    """A stand-in for a ``Lineage`` row — only ``export_relpath`` is
    read by ``_sample_pixmap_paths``."""
    return SimpleNamespace(export_relpath=relpath)


def _item(relpath: str) -> SimpleNamespace:
    """Stand-in for a captured ``Item`` row — only ``relpath`` is read
    by the (now-retired) any-capture fallback."""
    return SimpleNamespace(relpath=relpath, item_id="i_" + relpath)


class _StubEg:
    """Minimal EventGateway stand-in. Wires the two surfaces
    ``_sample_pixmap_paths`` reads (``event_root``, ``exported_files``)
    and one (``items``) only to prove the spec/132 contract — that the
    function NEVER reaches for picked/any-capture even when they exist."""

    def __init__(
        self, root: Path, *,
        exported: list | None = None,
        exported_raises: bool = False,
        items_should_not_be_called: bool = False,
    ) -> None:
        self.event_root = root
        self._exported = list(exported or [])
        self._exported_raises = exported_raises
        self._items_calls: list = []
        self._items_should_not_be_called = items_should_not_be_called

    def exported_files(self):
        if self._exported_raises:
            raise RuntimeError("simulated exported_files() failure")
        return list(self._exported)

    def items(self, **kw):
        self._items_calls.append(kw)
        if self._items_should_not_be_called:
            raise AssertionError(
                "spec/132: _sample_pixmap_paths must NOT fall back to "
                "eg.items() — the closed carousel is exported-only "
                f"(got items(**{kw!r}))")
        return []


# ── Exported present → exactly the exported set ────────────────────────


def test_returns_exactly_the_exported_set(tmp_path):
    root = tmp_path / "evt"
    root.mkdir()
    eg = _StubEg(root, exported=[
        _lineage("Exported Media/a.jpg"),
        _lineage("Exported Media/b.jpg"),
    ])
    paths = _sample_pixmap_paths(eg, collected=None)
    assert paths == [
        root / "Exported Media/a.jpg",
        root / "Exported Media/b.jpg",
    ]


def test_drops_lineage_rows_without_export_relpath(tmp_path):
    """A lineage row without an ``export_relpath`` (defensive — should
    not exist in practice) is filtered out, not raised on."""
    root = tmp_path / "evt"
    root.mkdir()
    eg = _StubEg(root, exported=[
        _lineage("Exported Media/a.jpg"),
        _lineage(""),                  # empty string → falsy → dropped
        _lineage("Exported Media/c.jpg"),
    ])
    paths = _sample_pixmap_paths(eg, collected=None)
    assert paths == [
        root / "Exported Media/a.jpg",
        root / "Exported Media/c.jpg",
    ]


def test_caps_at_sample_pixmap_cap(tmp_path):
    """Bounded memory footprint: even with 100 exports we return at
    most ``_SAMPLE_PIXMAP_CAP`` (12 today)."""
    from mira.ui.pages._event_card_data import _SAMPLE_PIXMAP_CAP

    root = tmp_path / "evt"
    root.mkdir()
    eg = _StubEg(root, exported=[
        _lineage(f"Exported Media/f{i:03d}.jpg") for i in range(100)])
    paths = _sample_pixmap_paths(eg, collected=None)
    assert len(paths) == _SAMPLE_PIXMAP_CAP


# ── Empty / failure paths return EMPTY (no fallthrough) ────────────────


def test_no_exports_returns_empty_even_with_picked_items(tmp_path):
    """spec/132 — the picked fallback is retired. A closed event with
    picked-but-not-exported photos and no exports returns empty
    (carousel renders the placeholder)."""
    root = tmp_path / "evt"
    root.mkdir()
    eg = _StubEg(
        root, exported=[],
        items_should_not_be_called=True,
    )
    # The legacy any-capture fallback used to read ``collected`` —
    # passing a non-empty list proves we ignore it.
    collected = [_item("Original Media/p1.jpg"), _item("Original Media/p2.jpg")]
    paths = _sample_pixmap_paths(eg, collected=collected)
    assert paths == []
    # And the gateway's items() seam was never touched.
    assert eg._items_calls == []


def test_only_captures_returns_empty(tmp_path):
    """spec/132 — only captures (no picks, no exports) returns empty.
    The legacy any-capture tier is gone."""
    root = tmp_path / "evt"
    root.mkdir()
    eg = _StubEg(root, exported=[], items_should_not_be_called=True)
    collected = [_item(f"Original Media/c{i}.jpg") for i in range(5)]
    paths = _sample_pixmap_paths(eg, collected=collected)
    assert paths == []


def test_exported_files_exception_returns_empty(tmp_path):
    """spec/132 — when ``exported_files()`` raises, we return empty.
    No silent fallthrough to picked / any-capture."""
    root = tmp_path / "evt"
    root.mkdir()
    eg = _StubEg(
        root, exported_raises=True,
        items_should_not_be_called=True,
    )
    collected = [_item("Original Media/p1.jpg")]
    paths = _sample_pixmap_paths(eg, collected=collected)
    assert paths == []


def test_no_event_root_returns_empty(tmp_path):
    """Defensive — an event without a resolvable root returns empty
    without ever calling exported_files()."""
    eg = _StubEg(None, exported=[_lineage("Exported Media/x.jpg")])
    paths = _sample_pixmap_paths(eg, collected=None)
    assert paths == []


# ── Closed-tile render regression: empty source → placeholder ──────────


def test_photo_cycler_renders_placeholder_for_empty_source(qapp):
    """spec/132 acceptance — when ``sample_pixmap_paths`` is empty the
    PhotoCycler doesn't crash; it draws its built-in 'no photos'
    placeholder. The closed tile reuses this behaviour."""
    from PyQt6.QtGui import QPixmap

    from mira.ui.design.photo_cycler import PhotoCycler

    cycler = PhotoCycler([], caption="", sub_caption="")
    try:
        # paintEvent must succeed against a 320×240 surface even with
        # an empty pixmap list — the cycler degrades to a static
        # placeholder instead of crashing.
        cycler.resize(320, 240)
        target = QPixmap(cycler.size())
        cycler.render(target)
        # Nothing to assert on the pixels; the regression is "no
        # exception, no crash, count==0".
        assert cycler.count() == 0
    finally:
        cycler.deleteLater()
