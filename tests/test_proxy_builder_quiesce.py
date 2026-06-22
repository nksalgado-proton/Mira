"""spec/100 §A — `ProxyBuilder.quiesce` drains queued work AND waits
for the in-flight build to release the source file handle, WITHOUT
permanently stopping the thread.

These tests are the contract under the delete-event quiesce: without
the in-flight wait, the background `Image.open` would survive the
queue clear and the Windows file handle would still be held when
`shutil.rmtree` ran — exactly the spec/100 root cause.

Pure-stdlib stub for `ensure`: a callable whose body parks on an
`Event` so we can observe the builder INSIDE a build (`_building`
True) and verify `quiesce` blocks until we release it.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from core.photo_proxy_cache import ProxyBuilder


def _spin_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_quiesce_returns_after_in_flight_build_completes(tmp_path):
    """The whole point of spec/100 §A: a `quiesce()` call must wait
    for the build already inside `ensure` (which is the one holding
    the source open via `Image.open`) before returning."""
    parked = threading.Event()
    release = threading.Event()
    ensure_calls: list[Path] = []

    def fake_ensure(event_root, source_path, key):
        ensure_calls.append(Path(source_path))
        parked.set()                            # we are now "in `Image.open`"
        release.wait(timeout=5.0)               # caller frees us when ready

    builder = ProxyBuilder(ensure=fake_ensure)
    try:
        builder.seed(tmp_path, [(tmp_path / "a.jpg", "sha-a")])
        assert parked.wait(timeout=2.0)         # builder is inside `ensure`
        assert builder._building is True        # noqa: SLF001 — contract

        # quiesce() must NOT return while `_building` is True. Spin a
        # short window to prove it blocks.
        finished = threading.Event()
        result = {"ok": None}

        def _do_quiesce():
            result["ok"] = builder.quiesce(timeout=2.0)
            finished.set()

        t = threading.Thread(target=_do_quiesce)
        t.start()
        # Give quiesce a chance to NOT return.
        time.sleep(0.1)
        assert not finished.is_set(), (
            "quiesce returned before the in-flight build finished — "
            "the Image.open handle would still be live at rmtree time "
            "and reproduce the spec/100 zombie.")

        # Now release the build; quiesce should return True quickly.
        release.set()
        assert finished.wait(timeout=2.0)
        assert result["ok"] is True
        assert builder._building is False       # noqa: SLF001
    finally:
        release.set()
        builder.stop()


def test_quiesce_clears_queue_without_stopping(tmp_path):
    """Unlike `stop`, `quiesce` does NOT set `_stopping` — a later
    `seed` must still queue work (delete-an-event-then-open-another
    flow). The pending queue is cleared on entry."""
    seen_after: list[Path] = []
    seen_after_evt = threading.Event()

    def fake_ensure(event_root, source_path, key):
        seen_after.append(Path(source_path))
        seen_after_evt.set()

    builder = ProxyBuilder(ensure=fake_ensure)
    try:
        # Quiesce on a cold builder is immediate + fine.
        assert builder.quiesce(timeout=0.1) is True
        # Re-seed must still work after quiesce.
        n = builder.seed(tmp_path, [(tmp_path / "b.jpg", "sha-b")])
        assert n == 1
        assert seen_after_evt.wait(timeout=2.0), (
            "builder did not process after quiesce — it should remain "
            "seedable (unlike stop, which permanently kills it).")
        assert seen_after == [tmp_path / "b.jpg"]
    finally:
        builder.stop()


def test_quiesce_returns_false_on_timeout(tmp_path):
    """If the in-flight build runs longer than the timeout, quiesce
    must return False so the caller can log + fall through to the
    resilient rmtree (§B), which rides out the residual lock."""
    parked = threading.Event()
    release = threading.Event()

    def fake_ensure(event_root, source_path, key):
        parked.set()
        release.wait(timeout=5.0)

    builder = ProxyBuilder(ensure=fake_ensure)
    try:
        builder.seed(tmp_path, [(tmp_path / "c.jpg", "sha-c")])
        assert parked.wait(timeout=2.0)
        ok = builder.quiesce(timeout=0.1)       # too short on purpose
        assert ok is False
    finally:
        release.set()
        builder.stop()


def test_quiesce_drops_queued_jobs_not_yet_started(tmp_path):
    """A backlog of queued (but not yet running) jobs must NOT
    complete after a quiesce — the queue is cleared. Only the one
    in-flight job sees `ensure`."""
    parked = threading.Event()
    release = threading.Event()
    seen: list[Path] = []

    def fake_ensure(event_root, source_path, key):
        seen.append(Path(source_path))
        if not parked.is_set():
            parked.set()
            release.wait(timeout=5.0)

    builder = ProxyBuilder(ensure=fake_ensure)
    try:
        builder.seed(tmp_path, [
            (tmp_path / "a.jpg", "sha-a"),
            (tmp_path / "b.jpg", "sha-b"),
            (tmp_path / "c.jpg", "sha-c"),
        ])
        assert parked.wait(timeout=2.0)
        release.set()
        assert builder.quiesce(timeout=2.0) is True
        # Only the first job ran; the rest were dropped by quiesce
        # before the run loop could pop them.
        assert seen == [tmp_path / "a.jpg"]
        assert builder.pending_count() == 0
    finally:
        release.set()
        builder.stop()
