"""Real-asset smoke for the redesigned ExportPage with the spec/71
identity header applied.

Opens the live Gateway, finds an event with picked keepers, drives
:class:`ExportPage.open_event`, screenshots in both themes. Verifies
the Export phase chrome (green rail + EXPORT badge + the will/won't
ship legend) sits cleanly above the toolbar + grid.

Outputs::

    scripts/smoke_export_page_dark.png
    scripts/smoke_export_page_light.png
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget


def _find_event_with_picked(gw) -> str | None:
    """First event id whose pick-phase ledger has at least one picked
    keeper — the Export grid needs that pool to be non-empty."""
    rows = gw.list_events()
    for row in rows:
        event_id = str(row["id"])
        try:
            eg = gw.open_event(event_id)
        except Exception:
            continue
        try:
            picked = eg.items(
                phase="pick", state="picked", kind="photo",
                provenance="captured",
            )
            if picked:
                return event_id
        finally:
            try:
                eg.close()
            except Exception:
                pass
    return None


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.gateway import Gateway
    from mira.ui.exported.export_page import ExportPage
    from mira.ui.theme import apply_theme

    gw = Gateway()
    if gw.photos_base_path() is None:
        print("photos_base_path not set; aborting")
        return 1
    event_id = _find_event_with_picked(gw)
    if event_id is None:
        print("no event with picked keepers found; aborting")
        return 1

    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1280, 800)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        page = ExportPage(gw, parent=root)
        ok = page.open_event(event_id)
        if not ok:
            print(f"open_event failed for {event_id}; aborting")
            return 1
        rl.addWidget(page)
        root.show()
        for _ in range(40):
            app.processEvents()
        out = out_dir / f"smoke_export_page_{mode}.png"
        root.grab().save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
