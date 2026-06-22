"""spec/100 §B + §C — `delete_event` survives a stuck file and never
leaves a zombie.

The bug being pinned: a Windows-locked file inside the event root used
to make `shutil.rmtree` raise *before* the index row was removed, so
both the file AND the event survived (zombie). The fix makes the wipe
resilient (§B: retry + clear-read-only) and reorders the row drop to
ALWAYS run after the best-effort wipe (§C). The UI then surfaces the
residue list so the user can clean up by hand.

These tests pin the contract without needing a real Windows lock:
monkeypatch `shutil.rmtree` to raise `PermissionError`, then check the
index row is gone and the residue list comes back to the caller.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway import EventsIndex, Gateway, make_entry
from mira.settings.repo import SettingsRepo


def _gateway(tmp_path: Path, base: Path) -> Gateway:
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index, now=lambda: "2026-06-22T00:00:00+00:00")
    gw.set_photos_base_path(str(base))
    return gw


def _make_event(gw: Gateway, base: Path, name: str = "2026 - Test") -> Path:
    """Create a minimal event root with a couple of files so a wipe
    has something to walk."""
    root = base / name
    (root / "Original Media").mkdir(parents=True, exist_ok=True)
    (root / "Original Media" / "a.jpg").write_bytes(b"\x00\x01\x02")
    (root / "event.db").write_bytes(b"")        # placeholder
    entry = make_entry(
        event_id="evt-1", name="Test", start_date=None, end_date=None,
        is_closed=False, event_root=root, photos_base_path=base,
    )
    gw.index.upsert(entry)
    return root


# ── §C — never leave a zombie ────────────────────────────────────


def test_delete_event_drops_index_row_even_when_rmtree_raises(
    tmp_path, monkeypatch,
):
    """Pin the spec/100 §C invariant: a file that resists every retry
    must NOT leave the event in Mira's list. The user's deliberate
    "delete this event" choice is honoured for the record either way."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    root = _make_event(gw, base)
    assert gw.index.get("evt-1") is not None

    # Force the underlying ``os.unlink`` to refuse the victim file so
    # both the rmtree walk's first attempt AND the resilient retry
    # fail — simulates a Windows file held by something outside the
    # app (antivirus, indexer, anything the UI-layer quiesce can't
    # reach). The directory rmdir and other files behave normally.
    import os
    real_unlink = os.unlink
    victim = root / "Original Media" / "a.jpg"

    def stuck_unlink(p, *a, **kw):
        if Path(p) == victim:
            raise PermissionError(32, "stuck", str(p))
        return real_unlink(p, *a, **kw)

    monkeypatch.setattr(os, "unlink", stuck_unlink)

    was_present, residue = gw.delete_event("evt-1", delete_files=True)

    # spec/100 §C — the row is gone REGARDLESS of residue.
    assert was_present is True
    assert gw.index.get("evt-1") is None, (
        "spec/100 §C — index row must drop even when files resist; "
        "otherwise the event re-appears on next launch as a zombie")
    # And the UI gets the residue so it can name the survivors.
    assert any(
        str(p).endswith("a.jpg") for p in residue
    ), f"expected residue list to name the stuck file, got {residue!r}"
    # The genuinely stuck file is still there; the rest of the tree
    # was cleaned up by the best-effort walk.
    assert victim.exists()


def test_delete_event_clean_wipe_returns_empty_residue(tmp_path):
    """Sanity: the fast path (no stuck file) returns an empty residue
    list — today's behaviour unchanged."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    root = _make_event(gw, base)
    was_present, residue = gw.delete_event("evt-1", delete_files=True)
    assert was_present is True
    assert residue == []
    assert not root.exists()
    assert gw.index.get("evt-1") is None


def test_delete_event_index_only_does_not_touch_disk(tmp_path):
    """`delete_files=False` stays untouched by spec/100 — disk is not
    walked, residue is empty, and the index row drops cleanly."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    root = _make_event(gw, base)
    was_present, residue = gw.delete_event("evt-1", delete_files=False)
    assert was_present is True
    assert residue == []
    # Folder + files still there (index-only).
    assert (root / "Original Media" / "a.jpg").exists()
    assert gw.index.get("evt-1") is None


def test_delete_event_unknown_id_returns_false_empty(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    was_present, residue = gw.delete_event("ghost", delete_files=True)
    assert was_present is False
    assert residue == []


# ── §B — resilient rmtree retries before giving up ────────────────


def test_resilient_rmtree_retries_before_recording_residue(tmp_path):
    """The helper retries each unlink before adding it to residue;
    pinning the retry shape so a flaky lock doesn't show up in the
    user-facing dialog when a re-try would have won."""
    from mira.gateway.gateway import (
        _RMTREE_RETRIES,
        _resilient_rmtree,
    )
    root = tmp_path / "evt"
    (root / "sub").mkdir(parents=True)
    target = root / "sub" / "x.bin"
    target.write_bytes(b"data")

    # Track unlink attempts: fail the first two, succeed on the third.
    attempts = {"n": 0}
    import os
    real_unlink = os.unlink

    def flaky_unlink(p, *a, **kw):
        if Path(p) == target:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise PermissionError(32, "transient", str(p))
        return real_unlink(p, *a, **kw)

    import mira.gateway.gateway as gw_mod
    # Patch os.unlink that the helper imports inside the function.
    # We can't easily patch the import-local `os` — patch the global
    # one (the helper does `import os` inside, fetching the module).
    import os as os_mod
    monkey = pytest.MonkeyPatch()
    monkey.setattr(os_mod, "unlink", flaky_unlink)
    try:
        residue = _resilient_rmtree(root)
    finally:
        monkey.undo()

    assert _RMTREE_RETRIES >= 3
    assert attempts["n"] >= 3, (
        "the helper must retry — not give up on the first transient lock")
    assert residue == [], (
        f"a file that becomes free on retry should NOT show up in "
        f"residue — got {residue!r}")
