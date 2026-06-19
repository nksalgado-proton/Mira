"""One-shot: append the 3 backfilled Alaska clips to the 'long' Cut.

Context — Nelson 2026-06-19:

The Alaska 'long' Cut was created BEFORE the 3 video clip lineage rows
existed (the export-job-orphan bug, since fixed). The Cut's frozen
membership doesn't know about the clips. This script rebuilds the
membership = old picks + the 3 new clip relpaths, preserving show order
by capture time.

Idempotent: re-running after the clips are already in the Cut is a no-op
(the set union is the same).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mira.gateway.event_gateway import EventGateway
from mira.store.repo import EventStore

EVENT_ROOT = Path(r"D:\Photos\_mira_events\Alaska")
CUT_TAG = "long"


def main() -> int:
    store = EventStore.open(EVENT_ROOT / "event.db")
    gw = EventGateway(store, event_root=EVENT_ROOT)
    try:
        cut = gw.cut_by_tag(CUT_TAG)
        if cut is None:
            print(f"error: no Cut tagged '{CUT_TAG}' in this event")
            return 2

        current = [cm.export_relpath
                   for cm in gw.store.query_by(
                       __import__("mira.store.models", fromlist=["CutMember"]).CutMember,
                       cut_id=cut.id)]
        current_set = set(current)
        print(f"current 'long' membership: {len(current)} file(s)")

        # The 3 backfilled clips, identified by extension under Exported Media/
        clip_rows = [
            ln for ln in gw.exported_files()
            if ln.export_relpath.lower().endswith(
                ('.mp4', '.mov', '.m4v', '.webm'))
        ]
        clips = [ln.export_relpath for ln in clip_rows]
        print(f"clips found in #exported: {len(clips)}")
        for c in clips:
            print(f"  {c}")

        to_add = [c for c in clips if c not in current_set]
        if not to_add:
            print("\nnothing to add — clips already in the Cut.")
            return 0

        new_membership = current + to_add
        gw.set_cut_members(cut.id, new_membership)
        print(f"\nadded {len(to_add)} clip(s); Cut membership now "
              f"{len(new_membership)} file(s).")
    finally:
        gw.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
