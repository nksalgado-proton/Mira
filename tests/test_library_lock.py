"""Tests for ``core.library_lock`` — spec/76 §A single-writer lock.

The lock is filesystem-only (no Qt, no sockets, no network) so every
test runs against a real temp directory. Staleness is enforced
against the lock file's filesystem mtime, which is the seam the
"stale lock taken over" and "fresh lock blocks acquire" cases drive
through.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from core import library_lock
from core.library_lock import (
    LOCK_DIRNAME,
    LOCK_FILENAME,
    STALENESS_TIMEOUT_SECONDS,
    LockInfo,
    acquire,
    is_stale,
    read_holder,
    refresh,
    release,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A fresh library root for each test."""
    return tmp_path


def _lock_file(root: Path) -> Path:
    return root / LOCK_DIRNAME / LOCK_FILENAME


def _backdate(path: Path, *, seconds_ago: int) -> None:
    """Push the file's mtime ``seconds_ago`` into the past. Used to
    simulate a stale lock without sleeping for 5 minutes."""
    when = time.time() - seconds_ago
    os.utime(str(path), (when, when))


# ── Fresh acquire ──────────────────────────────────────────────────


def test_fresh_acquire_creates_lock_file(root):
    """No lock file → acquire succeeds and writes the lock."""
    assert not _lock_file(root).exists()
    result = acquire(root)
    assert result.acquired is True
    assert result.holder is not None
    assert _lock_file(root).exists()
    # Holder reflects THIS process.
    import socket
    assert result.holder.hostname == socket.gethostname()
    assert result.holder.pid == os.getpid()


def test_fresh_acquire_writes_json_payload(root):
    """The lock file is valid JSON with the documented fields."""
    acquire(root)
    blob = json.loads(_lock_file(root).read_text(encoding="utf-8"))
    assert {"hostname", "pid", "app_version", "acquired_at", "heartbeat_at"} \
        <= set(blob)


# ── Second acquire blocked when fresh ─────────────────────────────


def test_second_acquire_sees_holder_and_does_not_acquire(root, monkeypatch):
    """Fresh lock from another writer → acquire returns acquired=False
    with the holder's info. We simulate the other writer by stubbing
    the local identity for the second call."""
    first = acquire(root)
    assert first.acquired is True

    # Pretend the second acquire is a different host / pid.
    monkeypatch.setattr(library_lock.socket, "gethostname",
                        lambda: "other-host")
    monkeypatch.setattr(library_lock.os, "getpid", lambda: 99999)

    second = acquire(root)
    assert second.acquired is False
    assert second.holder is not None
    # Holder still names the first writer, not the impostor.
    assert second.holder.hostname == first.holder.hostname
    assert second.holder.pid == first.holder.pid


# ── Stale takeover ────────────────────────────────────────────────


def test_stale_lock_is_taken_over(root):
    """An old lock file (mtime past the timeout) is taken over: the
    new acquire wins and the lock now names THIS process."""
    acquire(root)
    _backdate(_lock_file(root), seconds_ago=STALENESS_TIMEOUT_SECONDS + 30)

    # The mtime backdate makes the prior holder stale to any subsequent
    # caller. Simulate "different process took it over" by pretending
    # to be a different pid — the lock should still be acquirable.
    result = acquire(root)
    assert result.acquired is True


def test_is_stale_respects_timeout(root):
    """is_stale comparison is mtime-based and respects the timeout."""
    acquire(root)
    info = read_holder(root)
    assert info is not None
    # Fresh lock — not stale.
    assert is_stale(info, now=info.mtime + 1) is False
    # One second past the timeout — stale.
    assert is_stale(
        info, now=info.mtime + STALENESS_TIMEOUT_SECONDS + 1) is True


def test_is_stale_same_host_dead_pid_is_stale_immediately(root, monkeypatch):
    """Spec/76 §A.2 (Nelson 2026-06-18 follow-up): when the lock claims
    THIS host but the listed pid is no longer running, the lock is
    treated as stale immediately — no 5-minute wait. Same-host crash
    leaves a fresh heartbeat, which would otherwise block the next
    launch."""
    import socket
    acquire(root)
    info = read_holder(root)
    assert info is not None
    # Synthesize a holder claiming this host but a long-dead pid.
    dead_pid = 0x7fffffff      # absurdly large, almost certainly unused
    crashed = LockInfo(
        hostname=socket.gethostname(),
        pid=dead_pid,
        app_version=info.app_version,
        acquired_at=info.acquired_at,
        heartbeat_at=info.heartbeat_at,
        mtime=info.mtime,
    )
    assert is_stale(crashed, now=crashed.mtime + 1) is True


def test_is_stale_other_host_fresh_lock_is_not_stale(root):
    """A lock owned by a different host is NEVER probed for liveness —
    we can't see processes on other machines. The mtime gate is the
    only signal, so a fresh remote lock stays held."""
    acquire(root)
    info = read_holder(root)
    assert info is not None
    remote = LockInfo(
        hostname="some-other-host",
        pid=12345,
        app_version=info.app_version,
        acquired_at=info.acquired_at,
        heartbeat_at=info.heartbeat_at,
        mtime=info.mtime,
    )
    assert is_stale(remote, now=remote.mtime + 1) is False


def test_is_stale_own_pid_is_not_stale(root):
    """A lock claiming THIS pid is the live writer's own row — never
    treat it as stale (Nelson 2026-06-18 — defensive: refresh() handles
    the live case, but a stray re-entrant call to is_stale on our own
    payload should agree)."""
    acquire(root)
    info = read_holder(root)
    assert info is not None
    # ``acquire`` writes our pid into the lock; is_stale should agree.
    assert info.pid == os.getpid()
    assert is_stale(info, now=info.mtime + 1) is False


def test_acquire_takes_over_immediately_when_prior_pid_is_dead(
    root, monkeypatch,
):
    """End-to-end: a fresh-mtime lock with a dead same-host pid is
    taken over on the next ``acquire`` without waiting the timeout.
    This is the failure mode Nelson hit on 2026-06-18 after a crash."""
    import json
    # Plant a "previous writer" lock with a same-host dead pid and a
    # fresh mtime so the legacy timeout-only rule would refuse takeover.
    import socket
    lock_file = _lock_file(root)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps({
        "hostname": socket.gethostname(),
        "pid": 0x7fffffff,
        "app_version": "0.1.0",
        "acquired_at": "2026-06-18T15:23:05Z",
        "heartbeat_at": "2026-06-18T15:23:35Z",
    }))
    # mtime is "now" — non-stale by timeout, but dead pid takeover wins.
    result = acquire(root)
    assert result.acquired is True
    assert result.holder is not None
    assert result.holder.pid == os.getpid()


# ── Release ───────────────────────────────────────────────────────


def test_release_removes_the_file(root):
    acquire(root)
    assert _lock_file(root).exists()
    assert release(root) is True
    assert not _lock_file(root).exists()


def test_release_when_no_lock_returns_false(root):
    assert release(root) is False


def test_release_does_not_remove_other_holder(root, monkeypatch):
    """If the lock has been taken over by a different host / pid, our
    release() must NOT delete it — we'd silently boot the new owner."""
    acquire(root)
    monkeypatch.setattr(library_lock.socket, "gethostname",
                        lambda: "other-host")
    assert release(root) is False
    assert _lock_file(root).exists()


# ── Heartbeat (refresh) ───────────────────────────────────────────


def test_refresh_updates_heartbeat_and_mtime(root):
    """refresh() rewrites the lock so heartbeat_at advances and the
    filesystem mtime moves forward (the staleness clock resets)."""
    acquire(root)
    before = read_holder(root)
    assert before is not None
    # Backdate so we can detect that refresh actually advanced the
    # mtime (which is what staleness is measured against).
    _backdate(_lock_file(root), seconds_ago=120)
    refreshed_before = read_holder(root)
    assert refreshed_before is not None
    assert refreshed_before.mtime < time.time() - 60

    assert refresh(root) is True
    after = read_holder(root)
    assert after is not None
    assert after.mtime > refreshed_before.mtime
    # acquired_at is preserved through heartbeats — only heartbeat_at
    # advances (informational; staleness still uses mtime).
    assert after.acquired_at == before.acquired_at


def test_refresh_returns_false_when_lock_taken_over(root, monkeypatch):
    """If another host / pid now owns the lock, refresh() must report
    the loss so the caller can drop to read-only."""
    acquire(root)
    monkeypatch.setattr(library_lock.socket, "gethostname",
                        lambda: "other-host")
    assert refresh(root) is False


def test_refresh_returns_false_when_lock_missing(root):
    acquire(root)
    os.remove(str(_lock_file(root)))
    assert refresh(root) is False


# ── Corrupt / half-written lock file ──────────────────────────────


def test_corrupt_lock_file_is_treated_as_absent(root):
    """A half-written / garbage lock file must not crash read_holder;
    spec/76 §A.5 says it's treated as stale, not a crash. acquire()
    then takes it over."""
    p = _lock_file(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json", encoding="utf-8")
    assert read_holder(root) is None
    # And acquire() proceeds — the corrupt file is overwritten.
    result = acquire(root)
    assert result.acquired is True
    # The replaced file is valid JSON again.
    json.loads(p.read_text(encoding="utf-8"))


def test_lock_file_with_non_object_payload_is_absent(root):
    """A JSON array (or string, or number) where an object was
    expected is also treated as absent."""
    p = _lock_file(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert read_holder(root) is None


# ── Post-acquire verify (spec/76 §A.2 race tightener) ────────────


def test_post_acquire_verify_drops_to_read_only_when_we_lost_the_race(
    root, monkeypatch,
):
    """Spec/76 §A.2 race tightener (Nelson 2026-06-21): two writers can
    both see a stale holder, both write atomically within the SMB caching
    window, and `os.replace` succeeds for both. The post-acquire re-read
    catches the loser by finding a holder that doesn't match THIS process
    and returns ``acquired=False`` with the winner's identity.

    Simulate by patching ``_atomic_write_lock`` to plant a different
    writer's payload on top of ours — what a successful race would
    look like from our perspective."""
    import socket

    other_payload = {
        "hostname": "racer-host",
        "pid": 99999,
        "app_version": "dev",
        "acquired_at": "2026-06-21T17:30:00Z",
        "heartbeat_at": "2026-06-21T17:30:00Z",
    }

    real_write = library_lock._atomic_write_lock

    def racy_write(path, payload):
        # Pretend our atomic write succeeded, but the other writer wrote
        # AFTER us with no visible error — exactly the SMB cache window.
        real_write(path, other_payload)

    monkeypatch.setattr(library_lock, "_atomic_write_lock", racy_write)

    result = library_lock.acquire(root)
    assert result.acquired is False
    assert result.holder is not None
    assert result.holder.hostname == "racer-host"
    assert result.holder.pid == 99999
    # And we didn't claim ownership of a lock we don't own.
    assert result.holder.hostname != socket.gethostname()


def test_post_acquire_verify_does_not_fire_on_normal_acquire(root):
    """Sanity: a happy-path acquire still returns ``acquired=True`` — the
    post-acquire verify only intervenes when the re-read holder isn't us.
    """
    result = library_lock.acquire(root)
    assert result.acquired is True
    assert result.holder is not None
    assert result.holder.pid == os.getpid()


# ── NAS-shaped contention ─────────────────────────────────────────


def test_two_writers_alternating_one_share(root, monkeypatch):
    """Two PCs share one library on a NAS. Writer A acquires; writer B
    is told to open read-only (the holder names A). When A's lock goes
    stale (5 min later, mtime-backdated), B acquires successfully and the
    holder now names B. Mimics what spec/76 §A.2 promises for the
    multi-PC path without an actual share."""
    # First writer A acquires.
    result_a = acquire(root)
    assert result_a.acquired is True
    holder_a = result_a.holder

    # Pretend "we" are now a different machine (writer B) probing the lock.
    monkeypatch.setattr(library_lock.socket, "gethostname",
                        lambda: "machine-b")
    monkeypatch.setattr(library_lock.os, "getpid", lambda: 70000)

    # A fresh lock → B is denied and the holder still names A.
    denied = acquire(root)
    assert denied.acquired is False
    assert denied.holder is not None
    assert denied.holder.hostname == holder_a.hostname
    assert denied.holder.pid == holder_a.pid

    # A goes away (crash, network blip): mtime backdates past the timeout.
    _backdate(_lock_file(root), seconds_ago=STALENESS_TIMEOUT_SECONDS + 30)

    # B retries — the stale lock is taken over and the holder now names B.
    taken = acquire(root)
    assert taken.acquired is True
    assert taken.holder is not None
    assert taken.holder.hostname == "machine-b"
    assert taken.holder.pid == 70000


# ── Pure-logic / module-shape assertions ──────────────────────────


def test_lock_filename_is_inside_dot_mira(root):
    """The lock lives at ``<root>/.mira/writer.lock`` per spec/76 §B.4
    (refining §A.1) — inside the hidden machinery folder so the
    user-data, user store, and lock all relocate as one unit."""
    acquire(root)
    assert (root / ".mira" / "writer.lock").exists()
    # And the legacy root-level path is gone.
    assert not (root / ".mira-writer.lock").exists()


def test_lockinfo_carries_filesystem_mtime(root):
    acquire(root)
    info = read_holder(root)
    assert info is not None
    # Should be approximately now (within 5 seconds).
    assert abs(info.mtime - time.time()) < 5


def test_no_qt_imports_in_library_lock():
    """spec/76 §A.3 — core/library_lock.py is pure-logic, no Qt."""
    import core.library_lock as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "PyQt6" not in src
    assert "QtCore" not in src
