"""Tests for cut publishing (spec/76 §B.3 + spec/94 Phase 5d).

Pins the publish-target resolution, the manifest shape, and the
overwrite semantics for re-publish. End-to-end coverage of the
cross-event publish path uses the same umbrella fixture as
test_cross_event_cut_export — the only delta is the publish wrapper
+ the manifest sidecar.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mira.shared import cut_publish
from mira.shared.cut_publish import (
    CROSS_EVENT_SCOPE_DIRNAME,
    EVENT_SCOPE_DIRNAME,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    publish_cross_event_cut,
    publish_root,
)
from mira.store.repo import EventStore


NOW = "2026-06-21T00:00:00+00:00"


# ── publish_root resolution ──────────────────────────────────────


def test_publish_root_defaults_to_library_published(tmp_path):
    """No override → ``<library_root>/Published/``."""
    root = publish_root(tmp_path, "")
    assert root == tmp_path / "Published"


def test_publish_root_honours_override(tmp_path):
    """A non-empty override wins — invariant #2 (no hardcoded path).
    The user can point publish at a dedicated media-server share."""
    override = str(tmp_path / "Media" / "TV")
    root = publish_root(tmp_path, override)
    assert root == Path(override)


def test_publish_root_treats_whitespace_as_empty(tmp_path):
    """A whitespace-only override is the same as empty — defensive
    against a settings file with a stray space."""
    root = publish_root(tmp_path, "   ")
    assert root == tmp_path / "Published"


# ── Manifest classification (no Qt + no gateway needed) ─────────


def test_manifest_classifies_opener_as_separator(tmp_path):
    """Opener slides are sequence 1 with kind=separator."""
    target = tmp_path / "publish"
    target.mkdir()
    (target / "001_opener.jpg").write_bytes(b"x")
    (target / "002_day1.jpg").write_bytes(b"x")
    (target / "003_IMG_4001.JPG").write_bytes(b"x")

    class _Cut:
        id = "cut-1"
        tag = "Italy 2026"
        photo_s = 4.5

    manifest = cut_publish._build_manifest(
        kind="event_cut", cut=_Cut(), target=target,
        library_root_path=tmp_path, event_uuid="evt-1",
    )
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["kind"] == "event_cut"
    assert manifest["cut_id"] == "cut-1"
    assert manifest["tag"] == "Italy 2026"
    assert manifest["source"]["event_id"] == "evt-1"
    frames = manifest["frames"]
    # Three entries, sequence-sorted.
    assert [f["seq"] for f in frames] == [1, 2, 3]
    # Opener: separator with title.
    assert frames[0]["kind"] == "separator"
    assert frames[0]["title"] == "opener"
    # Day-N separator: title + day_number.
    assert frames[1]["kind"] == "separator"
    assert frames[1]["day_number"] == 1
    # Photo frame: media=photo, duration_s from cut.photo_s.
    assert frames[2]["kind"] == "frame"
    assert frames[2]["media"] == "photo"
    assert frames[2]["duration_s"] == 4.5


def test_manifest_classifies_video_without_duration(tmp_path):
    """Video frames skip ``duration_s`` so the media server uses the
    file's native runtime."""
    target = tmp_path / "publish"
    target.mkdir()
    (target / "001_VID_001.mp4").write_bytes(b"x")
    (target / "002_VID_002.MOV").write_bytes(b"x")

    class _Cut:
        id = "c"
        tag = "Clips"
        photo_s = 6.0

    manifest = cut_publish._build_manifest(
        kind="event_cut", cut=_Cut(), target=target,
        library_root_path=tmp_path, event_uuid="evt-1",
    )
    for f in manifest["frames"]:
        assert f["kind"] == "frame"
        assert f["media"] == "video"
        assert "duration_s" not in f


def test_manifest_lists_audio_in_sequence(tmp_path):
    """Audio files in ``audio/`` are listed with sequence prefix."""
    target = tmp_path / "publish"
    (target / "audio").mkdir(parents=True)
    (target / "audio" / "02_second.mp3").write_bytes(b"x")
    (target / "audio" / "01_first.mp3").write_bytes(b"x")

    class _Cut:
        id = "c"
        tag = "t"
        photo_s = 6.0

    manifest = cut_publish._build_manifest(
        kind="event_cut", cut=_Cut(), target=target,
        library_root_path=tmp_path, event_uuid="evt-1",
    )
    audio = manifest["audio"]
    assert [a["seq"] for a in audio] == [1, 2]
    assert audio[0]["file"] == "audio/01_first.mp3"


def test_manifest_skips_self(tmp_path):
    """The manifest sidecar shouldn't appear in its own ``frames``
    list — _scan_frames must skip it."""
    target = tmp_path / "publish"
    target.mkdir()
    (target / "001_IMG.jpg").write_bytes(b"x")
    (target / MANIFEST_FILENAME).write_text("{}")

    class _Cut:
        id = "c"
        tag = "t"
        photo_s = 6.0

    manifest = cut_publish._build_manifest(
        kind="event_cut", cut=_Cut(), target=target,
        library_root_path=tmp_path, event_uuid="evt-1",
    )
    files = [f["file"] for f in manifest["frames"]]
    assert MANIFEST_FILENAME not in files


# ── _prepare_publish_target (overwrite semantics) ───────────────


def test_prepare_publish_target_clears_existing_directory(tmp_path):
    """Re-publish overwrites the slot — the previous payload is gone."""
    target = tmp_path / "publish"
    target.mkdir()
    (target / "stale.txt").write_text("old")
    cut_publish._prepare_publish_target(target)
    assert target.is_dir()
    assert not (target / "stale.txt").exists()


def test_prepare_publish_target_creates_when_missing(tmp_path):
    target = tmp_path / "publish_new"
    assert not target.exists()
    cut_publish._prepare_publish_target(target)
    assert target.is_dir()


def test_prepare_publish_target_refuses_file(tmp_path):
    target = tmp_path / "not-a-dir"
    target.write_text("oops")
    with pytest.raises(cut_publish.CutPublishError):
        cut_publish._prepare_publish_target(target)


# ── End-to-end: cross-event publish ─────────────────────────────


def _make_umbrella(tmp_path):
    from mira.gateway.gateway import Gateway
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo

    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    photos_base = tmp_path / "photos"
    photos_base.mkdir()
    gw = Gateway(
        settings=settings, index=index,
        user_store_path=tmp_path / "mira.db",
        now=lambda: NOW, installation_profile="XMC")
    _ = gw.user_store
    settings.update(photos_base_path=str(photos_base))
    return gw, photos_base, settings


def _build_event(photos_base: Path, *, eid: str, name: str,
                 exported: dict) -> Path:
    root = photos_base / name
    root.mkdir(exist_ok=True)
    store = EventStore.create(
        root / "event.db", event_id=eid, app_version="test",
        created_at=NOW)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, name, NOW, NOW))
    store.close()
    exp = root / "Exported Media"
    exp.mkdir(exist_ok=True)
    for relpath, content in exported.items():
        full = exp / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
    return root


def _register(gw, photos_base: Path, root: Path, *, eid: str, name: str) -> None:
    from mira.gateway.index import make_entry
    gw.index.upsert(make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root, photos_base_path=photos_base))


def _seed_cross_event_cut(gw, cut_id: str, members: list) -> None:
    lg = gw.library_gateway()
    with lg.user_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, photo_s, "
            "                 created_at, updated_at) "
            "VALUES (?, ?, 'user', 6.0, ?, ?)",
            (cut_id, "italy_2026", NOW, NOW))
    lg.set_cross_event_cut_members(cut_id, members)


def test_publish_cross_event_cut_writes_manifest_and_files(tmp_path):
    """End-to-end: build a cross-event Cut with two members, publish,
    confirm the manifest + files land at the expected publish slot."""
    gw, photos_base, settings = _make_umbrella(tmp_path)
    try:
        r1 = _build_event(
            photos_base, eid="ev-1", name="E1",
            exported={"IMG_4001.JPG": b"PHOTO-1"})
        _register(gw, photos_base, r1, eid="ev-1", name="E1")
        r2 = _build_event(
            photos_base, eid="ev-2", name="E2",
            exported={"IMG_5001.JPG": b"PHOTO-2"})
        _register(gw, photos_base, r2, eid="ev-2", name="E2")

        _seed_cross_event_cut(gw, "cut-x", [
            {"event_id": "ev-1", "kind": "export",
             "export_relpath": "Exported Media/IMG_4001.JPG"},
            {"event_id": "ev-2", "kind": "export",
             "export_relpath": "Exported Media/IMG_5001.JPG"},
        ])

        # Use the default publish root (under tmp_path/Published).
        settings_obj = settings.load()
        result = publish_cross_event_cut(
            gw, "cut-x",
            library_root_path=tmp_path,
            settings=settings_obj,
        )

        # Files land in <library>/Published/Cross-event/italy_2026/.
        expected_dir = (
            tmp_path / "Published" / CROSS_EVENT_SCOPE_DIRNAME / "italy_2026")
        assert result.target == expected_dir
        assert expected_dir.is_dir()
        # The manifest is beside the files.
        assert result.manifest_path == expected_dir / MANIFEST_FILENAME
        manifest = json.loads(
            result.manifest_path.read_text(encoding="utf-8"))
        assert manifest["kind"] == "cross_event_cut"
        assert manifest["cut_id"] == "cut-x"
        assert manifest["tag"] == "italy_2026"
        # Two frames + no audio for this simple Cut.
        assert len(manifest["frames"]) == 2
        for f in manifest["frames"]:
            assert f["kind"] == "frame"
    finally:
        gw.close()


def test_republish_overwrites_previous_slot(tmp_path):
    """Re-publish replaces the slot — stale artefacts from a previous
    publish (e.g. members deleted from the Cut) do NOT survive."""
    gw, photos_base, settings = _make_umbrella(tmp_path)
    try:
        r1 = _build_event(
            photos_base, eid="ev-1", name="E1",
            exported={
                "IMG_4001.JPG": b"PHOTO-1",
                "IMG_4002.JPG": b"PHOTO-2",
            })
        _register(gw, photos_base, r1, eid="ev-1", name="E1")
        _seed_cross_event_cut(gw, "cut-x", [
            {"event_id": "ev-1", "kind": "export",
             "export_relpath": "Exported Media/IMG_4001.JPG"},
            {"event_id": "ev-1", "kind": "export",
             "export_relpath": "Exported Media/IMG_4002.JPG"},
        ])

        settings_obj = settings.load()
        first = publish_cross_event_cut(
            gw, "cut-x",
            library_root_path=tmp_path,
            settings=settings_obj,
        )
        first_files = sorted(
            p.name for p in first.target.iterdir() if p.is_file())
        assert len(first_files) == 3  # 2 frames + manifest

        # Drop one member, re-publish. The dropped file must not
        # linger in the publish slot.
        gw.library_gateway().set_cross_event_cut_members("cut-x", [
            {"event_id": "ev-1", "kind": "export",
             "export_relpath": "Exported Media/IMG_4001.JPG"},
        ])
        second = publish_cross_event_cut(
            gw, "cut-x",
            library_root_path=tmp_path,
            settings=settings_obj,
        )
        second_files = sorted(
            p.name for p in second.target.iterdir() if p.is_file())
        # Manifest + one frame; the dropped member is GONE.
        assert len(second_files) == 2
        assert all("4002" not in n for n in second_files)
    finally:
        gw.close()
