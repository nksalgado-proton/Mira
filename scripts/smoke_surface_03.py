"""Real-asset screenshot smoke for Surface 03 (Phases).

Drives ``PhasesPage`` against the live Gateway pointing at
``D:\\Photos\\_mira_events`` and opens **Pousada Salve Floresta** — a
real wildlife/trip event with 244 captures, 186 decided, 185 picked,
0 edited, 0 exported across 2 cameras (DC-G9M2 + DC-G9), 3 trip days.
That gives every donut a real shape:

* Collect — multi-camera slices (legend exercised, blurred-fill
  intent visible against actual per-camera time shares);
* Pick   — 76% in-progress (filled accent vs. track);
* Edit   — empty ("Not started" ring);
* Export — empty ("Not started" ring).

Spec/65 §6 wants real assets in the smoke — placeholder gradient cards
hide the texture the design carries. Run::

    python scripts/smoke_surface_03.py

Outputs::

    scripts/smoke_surface_03_dark.png
    scripts/smoke_surface_03_light.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


_FALLBACK_EVENT_NAME = "Pousada Salve Floresta"


def _pick_event_id(gw) -> str:
    """Find the Pousada Salve Floresta event id (or fall back to the
    first event with captured data so the smoke still produces output
    if the photos library moves)."""
    for row in gw.list_events():
        if _FALLBACK_EVENT_NAME in str(row.get("name", "")):
            return str(row["id"])
    # Fallback: first event in the index — keeps the smoke runnable on
    # any machine even if Pousada isn't there.
    rows = gw.list_events()
    if rows:
        return str(rows[0]["id"])
    raise RuntimeError("no events available")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.gateway import Gateway
    from mira.ui.pages.phases_page import PhasesPage
    from mira.ui.theme import apply_theme

    gw = Gateway()
    if gw.photos_base_path() is None:
        print("photos_base_path is not set; aborting smoke")
        return 1
    event_id = _pick_event_id(gw)

    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1100, 900)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        page = PhasesPage(gateway=gw, parent=root)
        if not page.set_event(event_id):
            print(f"set_event({event_id!r}) failed; aborting smoke")
            return 1
        rl.addWidget(page)
        root.show()
        for _ in range(3):
            app.processEvents()
        pm = root.grab()
        out = out_dir / f"smoke_surface_03_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
