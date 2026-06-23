"""Wire the spec/109 in-app exposure-merge engine to the spec/84 batch
queue.

The Qt-free engine (:mod:`core.exposure_merge`) decodes + fuses + writes
scratch TIFFs; this module builds the bracket request list from the
gateway, wraps the engine as the ``WorkCallable`` an
:class:`mira.ui.ingest.ingest_job.IngestJob` expects, and provides the
UI-thread ``on_finished`` adoption tail that turns each successful
scratch into a ``stack_output`` item via
``EventGateway.adopt_stack_output(producer='mira')``.

The split mirrors the Collect-OK ingest path (spec/84 §3 — engine
in core/, QThread wrapper in mira/ui/ingest/, UI-thread tail in
main_window). Charter inv. #7: the only writes are into
``Original Media/Merged/`` (via ``adopt_stack_output``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from core.exposure_merge import (
    ExposureMergeRequest,
    ExposureMergeResult,
    run_exposure_merge,
)

log = logging.getLogger(__name__)


@dataclass
class ExposureMergeAdoption:
    """One bracket's outcome AFTER the UI-thread adoption tail.

    ``new_item_id`` is the picked stack_output master id when the
    bracket merged + adopted cleanly; ``error`` carries the message
    otherwise (decode failure, fusion blow-up, byte-mismatch on the
    adoption copy)."""

    request: ExposureMergeRequest
    new_item_id: Optional[str] = None
    error: Optional[str] = None
    cancelled: bool = False


def build_requests_for_brackets(
    gateway,
    bracket_keys: Sequence[str],
) -> List[ExposureMergeRequest]:
    """Translate a list of bracket keys into engine requests by reading
    the gateway: member item rows give the full-res ``Original Media/``
    paths the engine decodes.

    Only **exposure** brackets are eligible (spec/109 §4 — focus
    brackets stay external-only). Brackets whose members lack
    ``origin_relpath`` (un-materialized virtual rows) are skipped with a
    log warning so the batch doesn't try to decode missing files."""
    if gateway.event_root is None:
        raise RuntimeError(
            "in-app exposure merge needs a resolvable event_root")
    event_root = Path(gateway.event_root)
    memberships = gateway.bracket_memberships("pick")
    members_by_bracket: dict[str, List[Tuple[str, int]]] = {}
    for item_id, (bucket_key, kind) in memberships.items():
        if kind != "exposure_bracket":
            continue
        if bucket_key not in bracket_keys:
            continue
        members_by_bracket.setdefault(bucket_key, []).append(
            (item_id, 0))   # ordinal fixed below by item capture time

    requests: List[ExposureMergeRequest] = []
    for bracket_key, pairs in members_by_bracket.items():
        items = [gateway.item(iid) for iid, _ in pairs]
        items = [it for it in items if it is not None]
        if not items:
            log.warning("exposure merge: bracket %s has no members",
                        bracket_key)
            continue
        items.sort(key=lambda it: it.capture_time_corrected or "")
        member_paths: List[Path] = []
        member_ids: List[str] = []
        skip = False
        for it in items:
            if not it.origin_relpath:
                log.warning(
                    "exposure merge: bracket %s member %s has no "
                    "origin_relpath — skipping bracket",
                    bracket_key, it.id)
                skip = True
                break
            member_paths.append(event_root / it.origin_relpath)
            member_ids.append(it.id)
        if skip or not member_paths:
            continue
        label = items[0].origin_relpath or bracket_key
        requests.append(ExposureMergeRequest(
            bracket_key=bracket_key,
            bracket_kind="exposure_bracket",
            member_paths=member_paths,
            member_item_ids=member_ids,
            label=Path(label).name,
        ))
    return requests


def make_merge_work(
    requests: List[ExposureMergeRequest],
    *,
    align: bool = True,
    scratch_dir: Optional[Path] = None,
) -> Callable:
    """Return a ``WorkCallable`` the :class:`IngestJob` can run on its
    own thread. The callable's payload is the engine's
    ``List[ExposureMergeResult]`` — the UI-thread adopter consumes it."""
    def _work(progress_cb, should_cancel) -> List[ExposureMergeResult]:
        return run_exposure_merge(
            requests,
            scratch_dir=scratch_dir,
            align=align,
            progress_cb=progress_cb,
            should_cancel=should_cancel,
        )
    return _work


def adopt_merge_results(
    gateway,
    results: Sequence[ExposureMergeResult],
) -> List[ExposureMergeAdoption]:
    """The UI-thread tail (spec/84 §3 pattern: one SQLite connection per
    thread — only the UI thread writes event.db). For each successful
    engine result, call ``gateway.adopt_stack_output`` with
    ``producer='mira'`` — that moves the scratch TIFF into
    ``Original Media/Merged/`` (copy → sha-verify → delete source),
    writes the ``stack_bracket`` / ``stack_member`` rows + a
    picked-by-construction ``stack_output`` item, and tags the bracket
    as Mira-produced for the spec/109 §5 origin wordmark.

    Per-bracket adoption failures stay on the bracket's
    :class:`ExposureMergeAdoption` so the caller can surface a
    summary; one bad bracket never aborts the batch."""
    adopted: List[ExposureMergeAdoption] = []
    for r in results:
        if r.cancelled:
            adopted.append(ExposureMergeAdoption(
                request=r.request, cancelled=True))
            continue
        if r.error is not None or r.scratch_path is None:
            adopted.append(ExposureMergeAdoption(
                request=r.request,
                error=r.error or "engine returned no scratch path"))
            continue
        try:
            new_id = gateway.adopt_stack_output(
                r.scratch_path,
                bracket_key=r.request.bracket_key,
                bracket_kind=r.request.bracket_kind,
                member_item_ids=r.request.member_item_ids,
                producer="mira",
            )
            adopted.append(ExposureMergeAdoption(
                request=r.request, new_item_id=new_id))
            log.info("exposure merge: adopted %s → item %s",
                     r.scratch_path.name, new_id)
        except Exception as exc:  # noqa: BLE001 — one failure never stops the batch
            log.exception(
                "exposure merge: adopt_stack_output failed for bracket %s",
                r.request.bracket_key)
            adopted.append(ExposureMergeAdoption(
                request=r.request, error=str(exc)))
    return adopted


__all__ = [
    "ExposureMergeAdoption",
    "build_requests_for_brackets",
    "make_merge_work",
    "adopt_merge_results",
]
