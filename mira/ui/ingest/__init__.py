"""UI-side ingest support (spec/84).

The ingest copy ride the shared :class:`~mira.ui.shell.batch_queue
.BatchJobQueue` like exports do — :class:`IngestJob` is the Qt-thread
adapter around the Qt-free ``run_ingest`` copy engine (route → copy →
bake). The DB write does NOT happen on the worker thread (spec/84 §3 —
one SQLite connection per thread); the queue's ``on_finished`` callback
writes ``item`` rows on the UI thread against the gateway.
"""

from mira.ui.ingest.ingest_job import IngestJob, IngestJobResult  # noqa: F401

__all__ = ["IngestJob", "IngestJobResult"]
