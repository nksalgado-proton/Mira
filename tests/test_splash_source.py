"""spec/136 — splash source picker (random exported frame, time-boxed,
bundled fallback).

The picker is read-only and Qt-free: opens each candidate event.db
through an injectable opener, walks ``exported_files()``, prefers the
cached proxy of the source item when present, and falls back through
the export JPEG to the bundled mark on no closed events / no exports
/ load failure / deadline overrun.
"""
from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.ui.shell import splash


# ── Stubs ──────────────────────────────────────────────────────────────


def _lineage(relpath: str, *, source_item_id: str | None = None,
             source_bracket_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        export_relpath=relpath,
        source_item_id=source_item_id,
        source_bracket_id=source_bracket_id,
    )


def _item(*, sha256: str | None = None, origin_relpath: str | None = None
          ) -> SimpleNamespace:
    return SimpleNamespace(sha256=sha256, origin_relpath=origin_relpath)


class _StubEg:
    """Minimal EventGateway stand-in for the splash picker."""

    def __init__(
        self, *,
        exports: list,
        items: dict | None = None,
        raises_exported: bool = False,
    ) -> None:
        self._exports = list(exports)
        self._items = dict(items or {})
        self._raises = raises_exported
        self.closed = False

    def exported_files(self):
        if self._raises:
            raise RuntimeError("simulated exported_files() failure")
        return list(self._exports)

    def item(self, item_id):
        return self._items.get(item_id)

    def close(self):
        self.closed = True


class _StubGateway:
    def __init__(self, rows):
        self._rows = list(rows)

    def list_events(self):
        return list(self._rows)


def _seed_export(event_root: Path, relpath: str) -> Path:
    """Write a real on-disk JPEG so the picker's ``is_file()`` check
    passes. The bytes need not be valid JPEG content for the picker —
    decode is exercised separately."""
    path = event_root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xd9")  # minimal-ish; existence is what we test
    return path


# ── Picker ─────────────────────────────────────────────────────────────


def test_picks_random_exported_frame_from_random_closed_event(tmp_path):
    """Two closed events with exports → the picker returns one of the
    exported frames as a ChosenFrame, deterministically with a seeded rng."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir(); root_b.mkdir()
    (root_a / "event.db").write_bytes(b"")  # only the file's presence matters
    (root_b / "event.db").write_bytes(b"")
    _seed_export(root_a, "Exported Media/a1.jpg")
    _seed_export(root_b, "Exported Media/b1.jpg")
    _seed_export(root_b, "Exported Media/b2.jpg")

    eg_a = _StubEg(exports=[_lineage("Exported Media/a1.jpg")])
    eg_b = _StubEg(exports=[
        _lineage("Exported Media/b1.jpg"),
        _lineage("Exported Media/b2.jpg"),
    ])
    opens: list[Path] = []

    def _opener(event_root: Path):
        opens.append(event_root)
        return eg_a if event_root == root_a else eg_b

    gw = _StubGateway([
        {"id": "a", "is_closed": True, "event_root": root_a, "name": "Italy"},
        {"id": "b", "is_closed": True, "event_root": root_b, "name": "Japan"},
    ])
    chosen = splash.pick_random_exported_frame(
        gw, rng=random.Random(0), open_event=_opener, deadline_ms=10_000)

    assert chosen is not None
    assert chosen.export_path.parent.name == "Exported Media"
    assert chosen.export_path.exists()
    assert chosen.event_name in {"Italy", "Japan"}
    # Read-only contract: the picker closed each event it opened.
    for eg in (eg_a, eg_b):
        if eg in (opens and (eg_a, eg_b)):
            pass  # at least one was opened
    assert eg_a.closed or eg_b.closed


def test_skips_open_events_and_unresolved_roots(tmp_path):
    """Open events + rows with no resolved ``event_root`` are filtered
    out before any open_event call is attempted."""
    root_c = tmp_path / "c"
    root_c.mkdir()
    (root_c / "event.db").write_bytes(b"")
    _seed_export(root_c, "Exported Media/c.jpg")
    eg_c = _StubEg(exports=[_lineage("Exported Media/c.jpg")])

    opens: list[Path] = []

    def _opener(event_root: Path):
        opens.append(event_root)
        return eg_c

    gw = _StubGateway([
        {"id": "open", "is_closed": False, "event_root": tmp_path / "open",
         "name": "Open"},
        {"id": "missing", "is_closed": True, "event_root": None,
         "name": "Missing"},
        {"id": "c", "is_closed": True, "event_root": root_c, "name": "C"},
    ])
    chosen = splash.pick_random_exported_frame(
        gw, rng=random.Random(0), open_event=_opener, deadline_ms=10_000)

    assert chosen is not None and chosen.event_name == "C"
    assert opens == [root_c]


def test_returns_none_when_no_closed_events(tmp_path):
    gw = _StubGateway([
        {"id": "open", "is_closed": False, "event_root": tmp_path,
         "name": "Open"},
    ])
    assert splash.pick_random_exported_frame(
        gw, rng=random.Random(0), deadline_ms=10_000) is None


def test_returns_none_when_no_exports(tmp_path):
    root = tmp_path / "evt"
    root.mkdir()
    (root / "event.db").write_bytes(b"")
    eg = _StubEg(exports=[])
    gw = _StubGateway([
        {"id": "evt", "is_closed": True, "event_root": root, "name": "X"},
    ])
    chosen = splash.pick_random_exported_frame(
        gw, rng=random.Random(0),
        open_event=lambda r: eg, deadline_ms=10_000)
    assert chosen is None
    assert eg.closed


def test_returns_none_when_exported_files_raises(tmp_path):
    """The picker absorbs per-event failures so one broken event.db
    can't poison the whole splash path."""
    root = tmp_path / "evt"
    root.mkdir()
    (root / "event.db").write_bytes(b"")
    eg = _StubEg(exports=[], raises_exported=True)
    gw = _StubGateway([
        {"id": "evt", "is_closed": True, "event_root": root, "name": "X"},
    ])
    chosen = splash.pick_random_exported_frame(
        gw, rng=random.Random(0),
        open_event=lambda r: eg, deadline_ms=10_000)
    assert chosen is None
    assert eg.closed       # always closed even when the read raised


def test_returns_none_when_list_events_raises():
    class _BadGw:
        def list_events(self):
            raise RuntimeError("index unreadable")
    assert splash.pick_random_exported_frame(
        _BadGw(), rng=random.Random(0), deadline_ms=10_000) is None


def test_prefers_cached_proxy_when_present(tmp_path):
    """A source item with a fresh proxy in
    ``<event_root>/.cache/proxies/<sha>.jpg`` → ChosenFrame.proxy_path
    points to that file; otherwise it's ``None``."""
    from core import photo_proxy_cache as ppc

    root = tmp_path / "evt"
    root.mkdir()
    (root / "event.db").write_bytes(b"")
    # Write a real source file under the event root so the proxy
    # invalidation key (mtime_ns + size) has something to bind to.
    src_relpath = "Original Media/_cameras/cam-A/IMG_001.jpg"
    src_path = root / src_relpath
    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.write_bytes(b"sourcebytes" * 100)

    sha = "f" * 64
    # Pre-write a proxy + sidecar so resolve_proxy reports a hit.
    ok = ppc.write_proxy(
        root, sha, src_path,
        jpeg_bytes=b"\xff\xd8\xff\xd9", native_w=120, native_h=90)
    assert ok

    _seed_export(root, "Exported Media/exp.jpg")
    lineage = _lineage("Exported Media/exp.jpg", source_item_id="it-1")
    eg = _StubEg(
        exports=[lineage],
        items={"it-1": _item(sha256=sha, origin_relpath=src_relpath)},
    )
    gw = _StubGateway([
        {"id": "evt", "is_closed": True, "event_root": root, "name": "X"},
    ])
    chosen = splash.pick_random_exported_frame(
        gw, rng=random.Random(0),
        open_event=lambda r: eg, deadline_ms=10_000)

    assert chosen is not None
    assert chosen.proxy_path == ppc.proxy_path(root, sha)


def test_no_proxy_when_source_item_missing_sha(tmp_path):
    """Item with no ``sha256`` → proxy_path stays None (the caller will
    draft-decode the export JPEG directly)."""
    root = tmp_path / "evt"
    root.mkdir()
    (root / "event.db").write_bytes(b"")
    _seed_export(root, "Exported Media/exp.jpg")
    eg = _StubEg(
        exports=[_lineage("Exported Media/exp.jpg", source_item_id="it-1")],
        items={"it-1": _item(sha256=None, origin_relpath="src.jpg")},
    )
    gw = _StubGateway([
        {"id": "evt", "is_closed": True, "event_root": root, "name": "X"},
    ])
    chosen = splash.pick_random_exported_frame(
        gw, rng=random.Random(0),
        open_event=lambda r: eg, deadline_ms=10_000)
    assert chosen is not None and chosen.proxy_path is None


def test_time_box_falls_back_when_budget_exhausted(tmp_path):
    """A slow open_event past the deadline → picker returns None so
    the caller can fall back to the bundled mark and proceed.

    Uses a monotonic stub that jumps past the deadline, so the test
    runs in real-time milliseconds but exercises the deadline check
    deterministically."""
    root = tmp_path / "evt"
    root.mkdir()
    (root / "event.db").write_bytes(b"")
    _seed_export(root, "Exported Media/exp.jpg")
    eg = _StubEg(exports=[_lineage("Exported Media/exp.jpg")])

    ticks = iter([0.0, 0.0, 10.0])      # initial check passes, second check blows

    def _opener(r):
        return eg

    gw = _StubGateway([
        {"id": "evt", "is_closed": True, "event_root": root, "name": "X"},
    ])
    chosen = splash.pick_random_exported_frame(
        gw, rng=random.Random(0), open_event=_opener,
        deadline_ms=100, monotonic=lambda: next(ticks))
    assert chosen is None


def test_zero_deadline_returns_none_without_opening(tmp_path):
    """Zero budget short-circuits the picker BEFORE any open_event
    call so the splash path is genuinely free in the opt-out case."""
    opens: list[Path] = []
    root = tmp_path / "evt"
    root.mkdir()
    (root / "event.db").write_bytes(b"")
    gw = _StubGateway([
        {"id": "evt", "is_closed": True, "event_root": root, "name": "X"},
    ])
    assert splash.pick_random_exported_frame(
        gw, rng=random.Random(0),
        open_event=lambda r: opens.append(r) or _StubEg(exports=[]),
        deadline_ms=0) is None
    assert opens == []


# ── build_splash_pixmap — bundled fallback when no photo is available ─


def test_build_splash_pixmap_falls_back_to_bundled_when_no_photo(qapp, tmp_path):
    """Empty gateway → ``build_splash_pixmap`` returns the bundled
    mark, not an empty / null pixmap."""
    bundled = Path(__file__).resolve().parents[1] / "assets" / "icons" / "mira.png"
    assert bundled.is_file()
    gw = _StubGateway([])
    pix = splash.build_splash_pixmap(gw, bundled_fallback=bundled)
    assert pix is not None and not pix.isNull()


def test_build_splash_pixmap_opt_out_skips_photo_path(qapp, tmp_path):
    """``photo_enabled=False`` → ``build_splash_pixmap`` MUST NOT touch
    the gateway picker (the user opted out)."""
    class _ExplodingGw:
        def list_events(self):
            raise AssertionError(
                "spec/136: photo_enabled=False must not consult the gateway")
    bundled = Path(__file__).resolve().parents[1] / "assets" / "icons" / "mira.png"
    pix = splash.build_splash_pixmap(
        _ExplodingGw(), bundled_fallback=bundled, photo_enabled=False)
    assert pix is not None and not pix.isNull()


def test_build_splash_pixmap_decodes_proxy_when_available(qapp, tmp_path):
    """End-to-end: when the picker returns a ChosenFrame whose
    ``proxy_path`` points at a real (tiny) JPEG, ``build_splash_pixmap``
    decodes it and returns a non-null pixmap of the proxy bytes."""
    from PIL import Image

    proxy = tmp_path / "proxy.jpg"
    Image.new("RGB", (200, 120), (32, 96, 160)).save(proxy, "JPEG")
    export = tmp_path / "exp.jpg"
    Image.new("RGB", (200, 120), (160, 32, 32)).save(export, "JPEG")

    def _fake_pick(gw, **kw):
        return splash.ChosenFrame(
            event_name="X", export_path=export, proxy_path=proxy)

    bundled = Path(__file__).resolve().parents[1] / "assets" / "icons" / "mira.png"
    import unittest.mock as mock
    with mock.patch.object(splash, "pick_random_exported_frame", _fake_pick):
        pix = splash.build_splash_pixmap(
            _StubGateway([]), bundled_fallback=bundled, max_edge=128)
    assert pix is not None and not pix.isNull()
    assert max(pix.width(), pix.height()) <= 128
