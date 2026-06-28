"""spec/148 — Overwrite vs Keep-both on Cut export.

Per-event Cut re-export used to disambiguate to ``<tag> (2)/`` always,
because :func:`mira.shared.cut_export.export_cut` ran the target through
``_fresh_folder``. spec/148 adds an ``overwrite_existing`` flag so the
caller can pick the destructive replace (write into ``<tag>/``, clear
the prior bundle, regenerate the ``.pte`` with correct ``<tag>/`` paths)
without rebuilding the folder by hand.

These tests pin:
  * Overwrite writes into the base folder — no ``(2)`` suffix.
  * Overwrite replaces the prior bundle's contents (a stale member from
    a smaller prior run does NOT linger).
  * Keep-both (the historical default) still produces ``base (2)/``.
  * The generated ``.pte`` under Overwrite carries the base folder in
    ProjectFilePath / ImagesFolder / per-slide FileName — no ``(2)``
    leaking into the paths a renamed-by-hand workflow would have broken.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_export import export_cut
from mira.shared.pte_project import (
    PteAudioTrack, PteMember,
    bundled_skeleton_path,
    generate_into_folder,
)
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now


MEMBERS = [
    "Exported Media/e1.jpg",
    "Exported Media/e3a.jpg",
    "Exported Media/v1.mp4",
]


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    for ln in ("e1.jpg", "e2.jpg", "e3a.jpg", "e3b.jpg", "v1.mp4"):
        p = tmp_path / "Exported Media" / ln
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"FILE:" + ln.encode())
    counter = itertools.count(1)
    g = EventGateway(store, event_root=tmp_path, now=_now,
                     new_id=lambda: f"id-{next(counter)}")
    g.set_cut_members("cut-s", MEMBERS)
    yield g
    g.close()


def _names(folder: Path) -> list:
    # Ignore the spec/158 ``.mira-cut-export.json`` sidecar manifest.
    return sorted(
        p.name for p in folder.iterdir()
        if p.is_file() and not p.name.startswith("."))


def test_overwrite_writes_into_base_not_2(gw, tmp_path):
    """First export lands at ``Cuts/<tag>/``. A re-export with
    ``overwrite_existing=True`` ALSO lands at ``Cuts/<tag>/`` — no
    ``(2)`` sibling, no accumulation."""
    cut = gw.cut("cut-s")
    first = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    second = export_cut(gw, cut, event_root=tmp_path, separators_on=False,
                        overwrite_existing=True)
    assert first.folder.name == "short_version"
    assert second.folder.name == "short_version"
    assert second.folder == first.folder
    # The sibling folder must NOT exist — spec/148's whole point.
    assert not (tmp_path / "Cuts" / "short_version (2)").exists()


def test_overwrite_replaces_prior_contents(gw, tmp_path):
    """A prior bundle's stragglers (a smaller previous member set, an
    old PTE the user edited, a stale audio/ folder) MUST be cleared
    when Overwrite lands — otherwise the new bundle ships with files
    that don't belong to the current Cut."""
    cut = gw.cut("cut-s")
    # First export: produce ``short_version/`` with the full member set.
    first = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    folder = first.folder
    # Drop a stale marker in the folder (and a subdirectory) — the kind
    # of leftover a re-export with fewer members would otherwise leave
    # behind. The Overwrite contract is "clean slate".
    stale = folder / "999_stale_member.jpg"
    stale.write_bytes(b"OLD")
    stale_dir = folder / "audio"
    stale_dir.mkdir(exist_ok=True)
    (stale_dir / "old.mp3").write_bytes(b"OLDMP3")
    # Re-export with Overwrite — every stale entry should be gone.
    second = export_cut(gw, cut, event_root=tmp_path, separators_on=False,
                        overwrite_existing=True)
    assert second.folder == folder
    assert not stale.exists()
    assert not (stale_dir / "old.mp3").exists()
    # The new bundle's members landed under the same folder, fresh.
    assert _names(second.folder) == [
        "001_e1.jpg", "002_e3a.jpg", "003_v1.mp4"]


def test_keep_both_still_disambiguates(gw, tmp_path):
    """The historical Keep-both default (``overwrite_existing=False``)
    is unchanged: a re-export lands at ``<tag> (2)/`` and the prior
    bundle stays untouched."""
    cut = gw.cut("cut-s")
    first = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    second = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    assert first.folder.name == "short_version"
    assert second.folder.name == "short_version (2)"
    # Both folders survive — the prior bundle is recoverable.
    assert first.folder.exists()
    assert second.folder.exists()
    assert _names(first.folder) == _names(second.folder)


def test_overwrite_on_fresh_target_just_writes_there(gw, tmp_path):
    """When the base folder doesn't yet exist, ``overwrite_existing=True``
    behaves identically to a Keep-both first export — there is nothing
    to clear, so we just write."""
    cut = gw.cut("cut-s")
    target = tmp_path / "Cuts" / "short_version"
    assert not target.exists()
    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=False,
        overwrite_existing=True)
    assert result.folder == target
    assert _names(result.folder) == [
        "001_e1.jpg", "002_e3a.jpg", "003_v1.mp4"]


def test_only_new_appends_unexported_members(gw, tmp_path):
    """spec/158 — "Only new files" adds just the members not already in
    the folder (per its sidecar manifest), leaving the prior bundle
    untouched and continuing the sequence numbering. A re-run with no
    further changes is a pure no-op."""
    cut = gw.cut("cut-s")
    first = export_cut(gw, cut, event_root=tmp_path, separators_on=False)
    folder = first.folder
    assert _names(folder) == ["001_e1.jpg", "002_e3a.jpg", "003_v1.mp4"]

    # Grow the Cut with a member that was never exported here.
    gw.set_cut_members("cut-s", MEMBERS + ["Exported Media/e2.jpg"])
    second = export_cut(
        gw, gw.cut("cut-s"), event_root=tmp_path, separators_on=False,
        only_new=True)
    assert second.folder == folder                 # same folder, additive
    assert second.skipped == 3                      # e1/e3a/v1 already here
    assert second.linked + second.copied == 1       # only e2 written
    assert _names(folder) == [
        "001_e1.jpg", "002_e3a.jpg", "003_v1.mp4", "004_e2.jpg"]

    # No further changes → every member is already present.
    third = export_cut(
        gw, gw.cut("cut-s"), event_root=tmp_path, separators_on=False,
        only_new=True)
    assert third.skipped == 4
    assert third.linked + third.copied == 0
    assert _names(folder) == [
        "001_e1.jpg", "002_e3a.jpg", "003_v1.mp4", "004_e2.jpg"]


def test_only_new_on_fresh_folder_writes_everything(gw, tmp_path):
    """spec/158 — "Only new files" with no prior manifest (folder never
    exported / deleted) degenerates to a full export: every member is
    new, nothing skipped."""
    cut = gw.cut("cut-s")
    result = export_cut(
        gw, cut, event_root=tmp_path, separators_on=False, only_new=True)
    assert result.skipped == 0
    assert _names(result.folder) == [
        "001_e1.jpg", "002_e3a.jpg", "003_v1.mp4"]


def test_only_new_skips_existing_files_without_manifest(gw, tmp_path):
    """spec/158 regression — an OLD-build export folder has no manifest.
    "Only new files" must recognise the files already on disk (by their
    ``NNN_<name>`` names) and SKIP them rather than try to re-link /
    overwrite — the latter raised WinError 32 ('file in use') when a
    member was open in PTE. Only genuinely-new members are written."""
    cut = gw.cut("cut-s")
    folder = tmp_path / "Cuts" / "short_version"
    folder.mkdir(parents=True)
    # Simulate a prior (manifest-less) export: the show files are here,
    # named with the sequence prefix, but NO .mira-cut-export.json.
    (folder / "001_e1.jpg").write_bytes(b"OLD1")
    (folder / "002_e3a.jpg").write_bytes(b"OLD2")
    (folder / "003_v1.mp4").write_bytes(b"OLD3")
    assert not (folder / ".mira-cut-export.json").exists()

    # Grow the Cut with a member that was never exported here.
    gw.set_cut_members("cut-s", MEMBERS + ["Exported Media/e2.jpg"])
    result = export_cut(
        gw, gw.cut("cut-s"), event_root=tmp_path, target=folder,
        separators_on=False, only_new=True)

    # The three pre-existing files are skipped (never re-written); only
    # e2 is added, numbered AFTER the highest existing sequence (003).
    assert result.skipped == 3
    assert result.linked + result.copied == 1
    assert (folder / "001_e1.jpg").read_bytes() == b"OLD1"   # untouched
    assert _names(folder) == [
        "001_e1.jpg", "002_e3a.jpg", "003_v1.mp4", "004_e2.jpg"]


def test_only_new_copies_member_absent_from_disk_despite_manifest(gw, tmp_path):
    """spec/158 data-loss regression (Nelson 2026-06-28) — a member the
    manifest CLAIMS is present but whose file is NOT actually on disk
    MUST be copied, never silently skipped. The old loose ``endswith``
    match marked Repeated-cluster members 'present' against a different
    same-suffix file and never copied them. Skip is now driven by an
    EXACT, 1:1 on-disk check, so a decoy can't satisfy it and a poisoned
    manifest self-heals."""
    cut = gw.cut("cut-s")          # members: e1, e3a, v1
    folder = tmp_path / "Cuts" / "short_version"
    folder.mkdir(parents=True)
    # On disk: e1 genuinely present, plus a DECOY whose name merely ends
    # with "_e3a.jpg" (the loose-match trap). e3a + v1 are NOT here.
    (folder / "001_e1.jpg").write_bytes(b"E1")
    (folder / "002_decoy_e3a.jpg").write_bytes(b"DECOY")
    # A poisoned manifest that wrongly lists every member as present.
    import json
    (folder / ".mira-cut-export.json").write_text(
        json.dumps({
            "version": 1, "cut_id": cut.id,
            "members": [
                "Exported Media/e1.jpg",
                "Exported Media/e3a.jpg",
                "Exported Media/v1.mp4",
            ],
            "max_seq": 2,
        }),
        encoding="utf-8")

    result = export_cut(
        gw, gw.cut("cut-s"), event_root=tmp_path, target=folder,
        separators_on=False, only_new=True)

    names = _names(folder)
    # e1 (really on disk) skipped; e3a + v1 copied (the decoy must NOT
    # count as e3a). Both real files now exist; nothing was lost.
    assert result.skipped == 1
    assert result.linked + result.copied == 2
    assert any(n.endswith("_e3a.jpg") and "decoy" not in n for n in names)
    assert any(n.endswith("_v1.mp4") for n in names)
    assert (folder / "002_decoy_e3a.jpg").read_bytes() == b"DECOY"  # untouched


def test_overwrite_pte_carries_base_paths_no_2(tmp_path):
    """The ``.pte`` written under Overwrite carries the base folder in
    ProjectFilePath / ImagesFolder / per-slide FileName — no ``(2)``
    leaking into the paths. That's the load-bearing piece: before
    spec/148, a manual "delete old + rename new" workaround left the
    ``.pte`` pointing at ``<tag> (2)/`` and PTE couldn't find the
    media after the rename."""
    folder = tmp_path / "Cuts" / "short_version"
    folder.mkdir(parents=True)
    # Plant a stale prior PTE the way a real re-export would leave it.
    (folder / "short_version.pte").write_bytes(b"OLD PTE")

    # Build a tiny Cut bundle by hand — the data layer test stays
    # isolated from the gateway / export pipeline.
    photo_a = folder / "001_a.jpg"
    photo_b = folder / "002_b.jpg"
    photo_a.write_bytes(b"A")
    photo_b.write_bytes(b"B")
    members = [
        PteMember(kind="photo", path=photo_a),
        PteMember(kind="photo", path=photo_b),
    ]

    out = generate_into_folder(
        folder, members, [],
        aspect="16:9", photo_seconds=6.0,
        library_root=None,
        bundled_fallback=bundled_skeleton_path(),
        stem="short_version",
        overwrite=True,
    )
    # No `(2)` in the on-disk filename...
    assert out == folder / "short_version.pte"
    assert " (2)" not in out.name
    text = out.read_bytes().decode("utf-8-sig")
    # ...nor in the baked absolute paths inside.
    assert " (2)" not in text
    # The base folder shows up in ProjectFilePath / ImagesFolder.
    assert f"ProjectFilePath={folder}".replace("/", "\\") in text
    assert f"ImagesFolder={folder}".replace("/", "\\") + "\\" in text
    # And each slide's FileName / ImageName uses the base folder.
    assert str(photo_a).replace("/", "\\") in text
    assert str(photo_b).replace("/", "\\") in text


def test_overwrite_pte_replaces_prior_file_in_place(tmp_path):
    """Two back-to-back PTE writes with ``overwrite=True`` land on the
    same on-disk path — no ``slideshow (2).pte`` sibling. The second
    write supplants the first."""
    photo = tmp_path / "001_a.jpg"
    photo.write_bytes(b"A")
    members = [PteMember(kind="photo", path=photo)]
    first = generate_into_folder(
        tmp_path, members, [],
        aspect="16:9", photo_seconds=6.0,
        library_root=None,
        bundled_fallback=bundled_skeleton_path(),
        stem="slideshow",
        overwrite=True,
    )
    second = generate_into_folder(
        tmp_path, members, [],
        aspect="16:9", photo_seconds=6.0,
        library_root=None,
        bundled_fallback=bundled_skeleton_path(),
        stem="slideshow",
        overwrite=True,
    )
    assert first == second == tmp_path / "slideshow.pte"
    assert not (tmp_path / "slideshow (2).pte").exists()
