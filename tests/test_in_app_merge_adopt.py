"""Tests for spec/109 — the in-app exposure merge (Mertens) lane.

The Qt-free fusion kernel + the merge-job engine + the producer-aware
adoption all flow into the same target: a picked, Mira-produced
``stack_output`` item under ``Original Media/Merged/`` with the bracket
frames left byte-pristine in the captured tree (reversible). This file
covers:

* the engine → ``adopt_stack_output(producer='mira')`` round trip,
* the producer column on ``stack_bracket``,
* per-bracket reversibility (undo restores the frames),
* the spec/109 §5 origin-wordmark badge resolver.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image
import numpy as np

import pytest

from core.export_provenance import (
    EXTERNAL, MIRA, stack_output_origin_label,
)
from core.exposure_merge import (
    ExposureMergeRequest, ExposureMergeResult, run_exposure_merge,
)
from mira.gateway.event_gateway import EventGateway
from mira.picked.exposure_merge_job import (
    adopt_merge_results, build_requests_for_brackets, make_merge_work,
)
from mira.store import models as m
from mira.store.repo import EventStore


# --------------------------------------------------------------------- #
# Event fixture — one picked exposure bracket with three full-res
# captured JPEGs (real bytes the decoder can read).
# --------------------------------------------------------------------- #


def _write_solid_jpeg(path: Path, value: int, size=(64, 48)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(
        np.full((size[1], size[0], 3), value, dtype=np.uint8), mode="RGB",
    )
    img.save(str(path), format="JPEG", quality=92)


def _make_event(tmp_path) -> EventGateway:
    """An event with one picked exposure bracket of three JPEG frames
    (dark/mid/bright) — the fusion target."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-merge")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-merge", name="Merge", created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="G9"))
    store.upsert(m.TripDay(day_number=2, date="2026-04-02"))

    frames = [
        ("i-dark", "dark.jpg", 40, "2026-04-02T08:00:00"),
        ("i-mid",  "mid.jpg",  128, "2026-04-02T08:00:01"),
        ("i-brt",  "bright.jpg", 220, "2026-04-02T08:00:02"),
    ]
    for iid, fname, value, ts in frames:
        rel = f"Original Media/_cameras/d2/G9/{fname}"
        full = tmp_path / rel
        _write_solid_jpeg(full, value)
        store.upsert(m.Item(
            id=iid, kind="photo", created_at="t", provenance="captured",
            origin_relpath=rel, sha256=f"sha-{iid}",
            byte_size=full.stat().st_size,
            materialized_at="t", materialized_phase="ingest",
            camera_id="G9", day_number=2,
            capture_time_raw=ts, capture_time_corrected=ts,
        ))
        store.upsert(m.PhaseState(
            item_id=iid, phase="pick", state="picked"))

    # The cached scanner cluster — the bracket the user saw in the grid.
    store.upsert(m.BucketCache(
        bucket_key="d2|exp|001", phase="pick", kind="exposure_bracket",
        day_number=2))
    for ord_, (iid, _, _, _) in enumerate(frames):
        store.upsert(m.BucketMember(
            bucket_key="d2|exp|001", phase="pick",
            item_id=iid, ordinal=ord_))

    return EventGateway(
        store, event_root=tmp_path,
        now=lambda: "2026-06-22T15:00:00+00:00")


# --------------------------------------------------------------------- #
# 1. The engine produces a scratch TIFF the adopter can ingest.
# --------------------------------------------------------------------- #


def test_engine_produces_scratch_tiff_from_picked_bracket(tmp_path):
    eg = _make_event(tmp_path)
    try:
        requests = build_requests_for_brackets(eg, ["d2|exp|001"])
        assert len(requests) == 1
        req = requests[0]
        assert req.bracket_key == "d2|exp|001"
        assert req.bracket_kind == "exposure_bracket"
        assert len(req.member_paths) == 3
        assert len(req.member_item_ids) == 3
        # member_item_ids order matches member_paths order (chronological).
        assert req.member_item_ids == ["i-dark", "i-mid", "i-brt"]

        scratch_dir = tmp_path / ".scratch"
        results = run_exposure_merge(
            requests, scratch_dir=scratch_dir, align=False)
        assert len(results) == 1
        r = results[0]
        assert r.error is None and not r.cancelled
        assert r.scratch_path is not None and r.scratch_path.is_file()
        assert r.scratch_path.suffix.lower() == ".tif"

        # The TIFF is a valid image; its mean tone sits between the
        # bracket extremes (Mertens blend, not a copy of one frame).
        with Image.open(r.scratch_path) as im:
            arr = np.array(im)
        assert arr.shape == (48, 64, 3)
        m_val = float(arr.mean())
        assert 40.0 < m_val < 220.0
    finally:
        eg.close()


# --------------------------------------------------------------------- #
# 2. adopt_merge_results yields a picked, Mira-produced stack output.
# --------------------------------------------------------------------- #


def test_adopt_yields_mira_produced_stack_output(tmp_path):
    eg = _make_event(tmp_path)
    try:
        requests = build_requests_for_brackets(eg, ["d2|exp|001"])
        results = run_exposure_merge(
            requests, scratch_dir=tmp_path / ".scratch", align=False)
        # Frames must still be byte-pristine BEFORE the adoption tail
        # (the captured tree is sacred — charter inv. #7).
        bracket_dir = tmp_path / "Original Media" / "_cameras" / "d2" / "G9"
        frame_paths = list(bracket_dir.iterdir())
        original_bytes = {p.name: p.read_bytes() for p in frame_paths}
        assert original_bytes  # frames exist

        adopted = adopt_merge_results(eg, results)
        assert len(adopted) == 1
        a = adopted[0]
        assert a.error is None
        assert a.new_item_id is not None

        # The bracket's master is recorded picked-by-construction under
        # Original Media/Merged/, with producer='mira'.
        stacks = eg.stacks()
        assert len(stacks) == 1
        sb = stacks[0]
        assert sb.kind == "exposure" and sb.action == "stacked"
        assert sb.producer == "mira"
        master = eg.item(sb.output_item_id)
        assert master is not None
        assert master.provenance == "stack_output"
        assert master.origin_relpath.startswith("Original Media/Merged/")
        assert eg.phase_state(master.id, "pick").state == "picked"
        assert master.day_number == 2 and master.camera_id == "G9"

        # stack_member rows for every bracket frame (ordinal-stable).
        members = eg.stack_members(sb.bracket_id)
        assert {sm.item_id for sm in members} == {
            "i-dark", "i-mid", "i-brt"}

        # The bracket frames remain untouched in the captured tree
        # (reversible — spec/109 §4).
        for p in frame_paths:
            assert p.exists()
            assert p.read_bytes() == original_bytes[p.name]

        # The scratch file was consumed by the adoption (the engine's
        # copy → sha-verify → delete-source pattern).
        assert results[0].scratch_path is not None
        assert not results[0].scratch_path.exists()

        # producer='mira' → 'Mira' wordmark via the badge resolver.
        producers = eg.stack_producers_by_output()
        assert producers == {sb.output_item_id: "mira"}
        assert stack_output_origin_label(
            producers[sb.output_item_id]) == MIRA
    finally:
        eg.close()


# --------------------------------------------------------------------- #
# 3. Undo: removing the master + bracket rows restores reversibility.
# --------------------------------------------------------------------- #


def test_undo_restores_picked_frames(tmp_path):
    """spec/109 §4 — the frames stay in the captured tree, so removing
    the master + its stack rows surfaces them again as the picked
    bracket members. Mirrors the external-stacker undo path."""
    eg = _make_event(tmp_path)
    try:
        requests = build_requests_for_brackets(eg, ["d2|exp|001"])
        results = run_exposure_merge(
            requests, scratch_dir=tmp_path / ".scratch", align=False)
        adopted = adopt_merge_results(eg, results)
        master_id = adopted[0].new_item_id
        assert master_id is not None

        # Before undo: bracket has its master + the action='stacked' row.
        assert eg.stacks()[0].output_item_id == master_id
        assert eg.stacks()[0].action == "stacked"

        # Undo: delete the master item; the stack_bracket
        # output_item_id is ON DELETE SET NULL and the row survives
        # without its master, action stays for audit but the bracket
        # surfaces again as unresolved.
        with eg.store.transaction() as conn:
            conn.execute("DELETE FROM item WHERE id = ?", (master_id,))

        # Frames remain picked + readable — the captured tree is sacred.
        for iid in ("i-dark", "i-mid", "i-brt"):
            it = eg.item(iid)
            assert it is not None
            assert (tmp_path / it.origin_relpath).exists()
            assert eg.phase_state(iid, "pick").state == "picked"

        # The bracket now has no output → looks unresolved to the scan
        # again (the spec/57 §3.4 reminder fact picks it up).
        stacks = eg.stacks()
        assert len(stacks) == 1
        assert stacks[0].output_item_id is None
    finally:
        eg.close()


# --------------------------------------------------------------------- #
# 4. The producer column distinguishes the two adoption lanes.
# --------------------------------------------------------------------- #


def test_external_adoption_defaults_producer_to_external(tmp_path):
    """The spec/57 external-stacker round trip calls
    ``adopt_stack_output`` with ``producer='external'`` (the default).
    The badge resolver then renders ``ext`` — flattened per spec/108
    so every third-party stacker reads the same wordmark."""
    eg = _make_event(tmp_path)
    try:
        scratch = tmp_path / "external_merge.tif"
        # Solid-grey TIFF — bytes are inert to the adoption (it only
        # cares about copy + sha verify).
        Image.fromarray(
            np.full((32, 32, 3), 128, dtype=np.uint8), mode="RGB",
        ).save(str(scratch), format="TIFF")
        new_id = eg.adopt_stack_output(
            scratch,
            bracket_key="d2|exp|001",
            bracket_kind="exposure_bracket",
            member_item_ids=["i-dark", "i-mid", "i-brt"],
        )
        sb = eg.stacks()[0]
        assert sb.output_item_id == new_id
        assert sb.producer == "external"
        producers = eg.stack_producers_by_output()
        assert stack_output_origin_label(producers[new_id]) == EXTERNAL
    finally:
        eg.close()


def test_in_app_producer_rejected_for_focus_brackets(tmp_path):
    """spec/109 §4 — focus brackets stay external-only (no built-in
    focus stacker). The gateway refuses ``producer='mira'`` for them so
    a UI bug can't silently bypass the principle."""
    eg = _make_event(tmp_path)
    try:
        scratch = tmp_path / "focus_attempt.tif"
        Image.fromarray(
            np.full((32, 32, 3), 100, dtype=np.uint8), mode="RGB",
        ).save(str(scratch), format="TIFF")
        with pytest.raises(ValueError, match="exposure"):
            eg.adopt_stack_output(
                scratch,
                bracket_key="d2|focus|x",
                bracket_kind="focus_bracket",
                member_item_ids=["i-dark", "i-mid"],
                producer="mira",
            )
    finally:
        eg.close()


# --------------------------------------------------------------------- #
# 5. Engine integration — cancel between brackets is honored.
# --------------------------------------------------------------------- #


def test_engine_cancels_at_bracket_boundary(tmp_path):
    """spec/109 §3 — cancel polls between brackets so the abort point
    is deterministic. The in-flight bracket finishes; subsequent ones
    return cancelled=True with no scratch path."""
    eg = _make_event(tmp_path)
    try:
        requests = build_requests_for_brackets(eg, ["d2|exp|001"])
        # Synthesize a second request pointing at the same files so we
        # have something to cancel into. (Real callers build one request
        # per unmerged bracket.)
        requests.append(ExposureMergeRequest(
            bracket_key="d2|exp|002",
            bracket_kind="exposure_bracket",
            member_paths=requests[0].member_paths,
            member_item_ids=requests[0].member_item_ids,
            label="second",
        ))
        # Cancel triggers after the first bracket. We use a list with
        # one entry to flip after the engine pulls it for the second
        # request.
        cancel_after = {"flag": False}
        progress = []

        def _progress(done, total, msg):
            progress.append((done, total, msg))
            if done >= 1:
                cancel_after["flag"] = True

        def _should_cancel():
            return cancel_after["flag"]

        results = run_exposure_merge(
            requests, scratch_dir=tmp_path / ".scratch", align=False,
            progress_cb=_progress, should_cancel=_should_cancel,
        )
        assert len(results) == 2
        # First bracket completed before the cancel flag flipped.
        assert results[0].scratch_path is not None
        assert results[0].cancelled is False
        # Second bracket bailed at the boundary check.
        assert results[1].cancelled is True
        assert results[1].scratch_path is None
    finally:
        eg.close()
