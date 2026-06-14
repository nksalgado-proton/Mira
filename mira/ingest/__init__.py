"""Create-event-from-photos ingest (spec/10, charter §5.6 — assembly starts at ingest).

The production path for the rebuild-fresh plan: scan a folder, calibrate each camera's
clock + timezone, copy the originals verbatim into the event tree (virtual EXIF — no
bake), and materialise one authoritative ``event.db`` through the gateway. Pure-logic
calibration / scan / day-assignment / filename-recovery are reused from legacy ``core/``;
only the commit is rebuilt against the gateway.
"""
from mira.ingest.model import (  # noqa: F401
    CameraPlan,
    DayPlan,
    IngestPlan,
    IngestResult,
)
from mira.ingest.engine import run_ingest  # noqa: F401
from mira.ingest.plan import ProposedPlan, plan_from_reconcile, propose_plan  # noqa: F401
from mira.ingest.offload_record import record_offload  # noqa: F401
