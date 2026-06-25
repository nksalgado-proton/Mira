"""The app-level batch-job queue + its progress line (spec/59 §8, spec/84 §2).

A user can launch as many batch jobs as they like (day-scope export,
event-scope export, ingest of an event); they QUEUE and run strictly
ONE at a time, app-level — the user keeps working anywhere in the app,
dashboard included. The one progress line lives directly below the
menubar and shows the running job (verb + label · per-file progress ·
how many wait · Cancel); hidden when idle.

A *job* is a prepared worker object exposing the ``_ExportWorker``
contract — ``progress(int, int, str)`` + ``finished_result(object)``
signals, ``start()`` and ``cancel()`` — plus a display label, a
``job_type`` (``"export"`` / ``"import"``) the line uses to render the
verb prefix, and an ``on_finished(result)`` callback the queue runs on
the UI thread. Each job type's worker is its own concern; the queue
and the line don't care which lane the work is in.

History — spec/84 generalised the queue so ingest can ride it too
(rename ``BatchExportQueue`` → ``BatchJobQueue``, ``job_type`` arg on
``enqueue``). A thin ``BatchExportQueue`` alias stays so existing test +
docstring sites that named the class verbatim keep working.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mira.ui.i18n import tr

log = logging.getLogger(__name__)

#: Poll cadence (ms) for the previews-pending count (spec/96 §1).
#: The proxy builder is Qt-free and emits no signal; the activity line
#: re-syncs on this timer so a long preview run drains visibly. ~400 ms
#: is fast enough for the number to feel responsive while keeping the
#: tick cost negligible (one int read under the builder's lock).
_PREVIEWS_POLL_MS = 400


#: Job-type tag passed to :meth:`BatchJobQueue.enqueue` — picks the verb
#: prefix the progress line draws in front of the caller's label.
JOB_TYPE_EXPORT = "export"
JOB_TYPE_IMPORT = "import"


@dataclass
class _Job:
    worker: object
    label: str
    on_finished: Optional[Callable[[object], None]]
    job_type: str = JOB_TYPE_EXPORT


class BatchJobQueue(QObject):
    """Strictly-serial job runner. ``changed`` fires on every state or
    progress tick — the progress line re-syncs from the properties."""

    changed = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pending: List[_Job] = []
        self._current: Optional[_Job] = None
        self._done = 0
        self._total = 0
        self._name = ""
        # spec/139 §3 — per-file fraction (0.0..1.0) of the currently-
        # encoding clip; ``None`` means "no per-file signal yet for
        # this batch" so the progress line can hide the second bar.
        # Reset to 0.0 every time the aggregate ``done`` ticks
        # forward (new file starting); set to 1.0 when a unit message
        # lands; updated continuously for video clips.
        self._file_fraction: Optional[float] = None

    # ── state the line reads ─────────────────────────────────────────
    @property
    def running_label(self) -> str:
        return self._current.label if self._current is not None else ""

    @property
    def running_job_type(self) -> str:
        return self._current.job_type if self._current is not None else ""

    @property
    def queued_count(self) -> int:
        return len(self._pending)

    @property
    def progress(self) -> tuple:
        return (self._done, self._total, self._name)

    @property
    def file_fraction(self) -> Optional[float]:
        """spec/139 §3 — the current file's encode fraction (0..1)
        for the second per-file progress bar; ``None`` while no
        signal has landed for this batch (the line hides the bar)."""
        return self._file_fraction

    @property
    def idle(self) -> bool:
        return self._current is None and not self._pending

    # ── API ──────────────────────────────────────────────────────────
    def enqueue(self, worker, label: str,
                on_finished: Optional[Callable] = None,
                *, job_type: str = JOB_TYPE_EXPORT) -> None:
        self._pending.append(_Job(worker, label, on_finished, job_type))
        log.info("batch queue: +1 %s job (%s); %d pending",
                 job_type, label, len(self._pending))
        self.changed.emit()
        self._maybe_start()

    def cancel_current(self) -> None:
        if self._current is not None:
            try:
                self._current.worker.cancel()
            except Exception:  # noqa: BLE001
                log.exception("batch queue: cancel failed")

    # ── runner ───────────────────────────────────────────────────────
    def _maybe_start(self) -> None:
        if self._current is not None or not self._pending:
            return
        job = self._pending.pop(0)
        self._current = job
        self._done = self._total = 0
        self._name = ""
        self._file_fraction = None
        job.worker.progress.connect(self._on_progress)
        # spec/139 §3 — the worker may not have a ``file_fraction``
        # signal (older jobs / ingest workers, or test stubs that
        # explicitly leave it ``None``). Connect lazily so those
        # callers don't blow up on ``NoneType.connect``.
        frac_sig = getattr(job.worker, "file_fraction", None)
        if frac_sig is not None and hasattr(frac_sig, "connect"):
            frac_sig.connect(self._on_file_fraction)
        job.worker.finished_result.connect(
            lambda result, j=job: self._on_finished(j, result))
        log.info("batch queue: starting %s (%s)", job.job_type, job.label)
        self.changed.emit()
        job.worker.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        # spec/139 §3 — aggregate tick. When we already have a
        # per-file signal AND ``done`` is advancing (a file just
        # completed), reset the fraction back to 0 for the upcoming
        # file — its first ``file_fraction`` emit will re-fill the
        # bar. We do NOT bump fraction OUT of ``None`` here: a pure-
        # photo batch never emits a fraction, so the second bar
        # should stay hidden ("for pure-photo batches it can stay
        # hidden", spec §3).
        if int(done) > self._done and self._file_fraction is not None:
            self._file_fraction = 0.0
        self._done, self._total, self._name = int(done), int(total), name
        self.changed.emit()

    def _on_file_fraction(self, fraction: float) -> None:
        """spec/139 §3 — clip-encode tick from
        :class:`BatchExportJob`. Stores the value so
        :class:`BatchProgressLine` can paint the per-file bar at the
        next ``changed`` signal."""
        self._file_fraction = max(0.0, min(1.0, float(fraction)))
        self.changed.emit()

    def _on_finished(self, job: _Job, result) -> None:
        if job.on_finished is not None:
            try:
                job.on_finished(result)
            except Exception:  # noqa: BLE001
                # A lost commit self-heals via the Edited Media return
                # scan on the next Edit entry (spec/57 §3).
                log.exception(
                    "batch queue: commit failed for %s", job.label)
        log.info("batch queue: finished %s (%s)", job.job_type, job.label)
        self._current = None
        self.changed.emit()
        self._maybe_start()


#: Back-compat alias — spec/84 renamed the class but several call sites
#: + tests still import the old name. New code uses ``BatchJobQueue``.
BatchExportQueue = BatchJobQueue


class BatchProgressLine(QWidget):
    """The one app-level activity line — sits directly below the
    menubar and spans the window. Always visible (spec/96 §1): when
    nothing is running it shows a muted "Ready"; when a batch job is
    in flight (export / ingest, spec/84 §2) it shows the job's head
    + per-file progress + a Cancel; when no batch job runs but the
    background proxy builder has work pending (spec/63 §5,
    spec/95 §B), it shows the previews count so the user knows the
    UI is busy. Message priority in :meth:`_sync` is **batch job >
    previews > Ready**; the determinate progress bar drives only the
    batch case.

    Decoupling: the previews count is supplied by a host-injected
    ``Callable[[], int]`` (``set_previews_source``) so this widget
    has no dependency on the photo cache. The ``QTimer`` poll lives
    here because the proxy builder is Qt-free (charter inv. 8) and
    emits no signal."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("BatchProgressLine")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # spec/139 §3 — TWO bars stacked: aggregate "N of M" on top,
        # per-file 0..100% below. Both share the right-hand queued
        # chip + Cancel column so the chrome footprint is the same.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 3, 10, 3)
        outer.setSpacing(10)
        bars_col = QVBoxLayout()
        bars_col.setContentsMargins(0, 0, 0, 0)
        bars_col.setSpacing(2)
        # Aggregate row: "Exporting N of M" + bar.
        agg_row = QHBoxLayout()
        agg_row.setContentsMargins(0, 0, 0, 0)
        agg_row.setSpacing(10)
        self._label = QLabel("")
        self._label.setObjectName("BatchProgressLabel")
        agg_row.addWidget(self._label)
        self._bar = QProgressBar()
        self._bar.setMaximumHeight(14)
        self._bar.setTextVisible(False)
        agg_row.addWidget(self._bar, stretch=1)
        bars_col.addLayout(agg_row)
        # Per-file row (spec/139 §3): tiny "encoding…" hint + a
        # 0..100% bar that paints the active clip's frame fraction.
        # Hidden until the first file_fraction lands; reset to 0 on
        # each new file (BatchJobQueue handles the reset).
        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(10)
        self._file_label = QLabel("")
        self._file_label.setObjectName("BatchProgressFileLabel")
        file_row.addWidget(self._file_label)
        self._file_bar = QProgressBar()
        self._file_bar.setObjectName("BatchProgressFileBar")
        self._file_bar.setMaximumHeight(8)
        self._file_bar.setTextVisible(False)
        self._file_bar.setRange(0, 1000)        # 0.1% resolution
        file_row.addWidget(self._file_bar, stretch=1)
        bars_col.addLayout(file_row)
        outer.addLayout(bars_col, stretch=1)
        self._queued = QLabel("")
        self._queued.setObjectName("BatchProgressQueued")
        self._queued.setToolTip(tr(
            "Batch jobs run one at a time; the rest wait in line."))
        outer.addWidget(self._queued)
        self._cancel = QPushButton(tr("Cancel"))
        self._cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        outer.addWidget(self._cancel)
        self._queue: Optional[BatchJobQueue] = None
        #: Host-supplied callable returning the proxy builder's
        #: ``pending_count``. ``None`` while unwired (tests / early
        #: startup); :meth:`_sync` treats that as zero previews
        #: pending so the line still renders "Ready" without an
        #: import-time dependency on the photo cache.
        self._previews_source: Optional[Callable[[], int]] = None
        #: Polls :attr:`_previews_source` on a steady cadence — the
        #: proxy builder doesn't emit Qt signals (charter inv. 8) so
        #: this is the seam that drives the previews display.
        self._previews_poll = QTimer(self)
        self._previews_poll.setInterval(_PREVIEWS_POLL_MS)
        self._previews_poll.timeout.connect(self._sync)
        # spec/96 §1 — always visible; the idle state is a quiet
        # "Ready". The first ``_sync`` runs from ``bind`` (so the
        # poll cadence has the queue + previews source in hand).
        self._idle_label = tr("Ready")
        self._sync()

    def bind(self, queue: BatchJobQueue) -> None:
        """Connect the line to the batch queue (the one source of
        export / ingest jobs). Starts the previews poll on the first
        bind so the line picks up background proxy work even before
        any batch job has run."""
        self._queue = queue
        queue.changed.connect(self._sync)
        self._cancel.clicked.connect(queue.cancel_current)
        if not self._previews_poll.isActive():
            self._previews_poll.start()
        self._sync()

    def set_previews_source(
        self, source: Optional[Callable[[], int]],
    ) -> None:
        """Inject the previews-pending source (spec/96 §1) — the
        host hands in ``photo_cache().proxy_pending_count``. ``None``
        clears it (test fixtures + headless boot). The poll keeps
        running either way; with no source it just reads zero."""
        self._previews_source = source
        self._sync()

    def _previews_pending(self) -> int:
        """Safe read of the host-supplied previews source. Any
        exception (the callable disappearing during teardown, a
        Qt-thread mishap) collapses to zero — the activity line
        never blocks the GUI on a status read."""
        src = self._previews_source
        if src is None:
            return 0
        try:
            return max(0, int(src()))
        except Exception:                                          # noqa: BLE001
            log.exception("previews pending source raised")
            return 0

    def _sync(self) -> None:
        q = self._queue
        # ── (1) batch job running → today's full readout. ────────
        if q is not None and not q.idle:
            done, total, _name = q.progress
            # spec/139 §3 — aggregate label is "{Verb} N of M" with
            # NO filename. The filename was clutter (and long names
            # overflowed); the count + the moving per-file bar
            # below convey progress. ``_name`` is still emitted by
            # the worker for logs, but the UI never displays it.
            verb = self._verb_for(q.running_job_type)
            self._label.setText(
                tr("{verb} {done} of {total}")
                .replace("{verb}", verb)
                .replace("{done}", str(done))
                .replace("{total}", str(max(1, total)))
            )
            self._cancel.setToolTip(
                self._cancel_tooltip(q.running_job_type))
            self._bar.setMaximum(max(1, total))
            self._bar.setValue(min(done, max(1, total)))
            self._bar.setVisible(True)
            self._bar.setProperty("idle", False)
            # spec/139 §3 — per-file bar. Visible only while we have
            # a fraction signal for the current batch (the second
            # bar collapses for pure-photo batches that snap to 1.0
            # so fast there's nothing to show).
            frac = q.file_fraction
            if frac is not None:
                self._file_bar.setValue(int(round(frac * 1000)))
                self._file_bar.setVisible(True)
                self._file_label.setText(
                    tr("encoding…") if frac < 1.0 else "")
                self._file_label.setVisible(frac < 1.0)
            else:
                self._file_bar.setVisible(False)
                self._file_label.setVisible(False)
            self._queued.setText(
                tr("+{n} waiting").replace("{n}", str(q.queued_count))
                if q.queued_count else "")
            self._cancel.setVisible(True)
            self._set_idle_styling(False)
            self.setVisible(True)
            return
        # ── (2) previews pending → previews message wins over Ready.
        pending = self._previews_pending()
        if pending > 0:
            self._label.setText(
                tr("Creating previews — responses may be slower "
                   "({n} left)").replace("{n}", str(pending)))
            # No determinate progress for previews — the count is in
            # the text. Clear the bar so a leftover batch fill from
            # the previous job doesn't read like progress.
            self._bar.setMaximum(1)
            self._bar.setValue(0)
            self._bar.setVisible(False)
            self._file_bar.setVisible(False)
            self._file_label.setVisible(False)
            self._queued.setText("")
            self._cancel.setVisible(False)
            self._set_idle_styling(True)
            self.setVisible(True)
            return
        # ── (3) idle → quiet "Ready". ────────────────────────────
        self._label.setText(self._idle_label)
        self._bar.setMaximum(1)
        self._bar.setValue(0)
        self._bar.setVisible(False)
        self._file_bar.setVisible(False)
        self._file_label.setVisible(False)
        self._queued.setText("")
        self._cancel.setVisible(False)
        self._set_idle_styling(True)
        self.setVisible(True)

    def _set_idle_styling(self, idle: bool) -> None:
        """Flip the muted-look property on the line + label so QSS
        can render a quieter "Ready" / previews state vs the active
        batch-job readout. No-op when the property is already at the
        target value (re-polishing is cheap but pointless)."""
        for widget in (self, self._label):
            if widget.property("idle") != idle:
                widget.setProperty("idle", idle)
                style = widget.style()
                if style is not None:
                    style.unpolish(widget)
                    style.polish(widget)

    @staticmethod
    def _format_head(job_type: str, label: str) -> str:
        """Compose the head of the progress line as ``"{verb} {label}"``.

        The verb is chosen from ``job_type`` (spec/84 §2 — the queue +
        line now serve ingest as well as export). Unknown / empty types
        fall back to the bare label so a future job type renders
        usefully even before this map learns the verb.

        spec/139 §3 retired the per-line use of this label form (the
        aggregate now reads ``{verb} N of M`` via :meth:`_verb_for`);
        the helper stays for the legacy import-line callers and tests.
        """
        if not label:
            return ""
        if job_type == JOB_TYPE_IMPORT:
            return tr("Importing {label}").replace("{label}", label)
        if job_type == JOB_TYPE_EXPORT:
            return tr("Exporting {label}").replace("{label}", label)
        return label

    @staticmethod
    def _verb_for(job_type: str) -> str:
        """spec/139 §3 — the verb token for the aggregate line. The
        line text is ``{verb} N of M``; no label, no filename."""
        if job_type == JOB_TYPE_IMPORT:
            return tr("Importing")
        return tr("Exporting")

    @staticmethod
    def _cancel_tooltip(job_type: str) -> str:
        if job_type == JOB_TYPE_IMPORT:
            return tr("Stop the running import.")
        if job_type == JOB_TYPE_EXPORT:
            return tr("Stop the running export.")
        return tr("Stop the running batch job.")


__all__ = [
    "BatchJobQueue",
    "BatchExportQueue",
    "BatchProgressLine",
    "JOB_TYPE_EXPORT",
    "JOB_TYPE_IMPORT",
]
