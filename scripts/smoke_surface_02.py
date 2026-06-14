"""Real-asset screenshot smoke for Surface 02 (Event Header dialog).

Builds an ``EventHeaderDialog`` pre-populated against the live gateway's
**Pousada Salve Floresta** event — every field carries real values so the
dialog reads against a typical edit-existing-event flow (the usual entry
point: Surface 01 title click).

Two passes, one per theme. The dialog is dropped onto a faux backdrop so
the modal's drop-shadow + border read the way they do in production.

Run::

    python scripts/smoke_surface_02.py

Outputs::

    scripts/smoke_surface_02_dark.png
    scripts/smoke_surface_02_light.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _existing_info_for_pousada() -> dict:
    """Hand-curated existing_info that mirrors what the live event has —
    keeps the smoke pure (no DB write needed) while still showing every
    field populated."""
    return {
        "name": "Pousada Salve Floresta",
        "event_type": "trip",
        "event_subtype": "Wildlife",
        "description": "Salve Floresta sanctuary — tapirs, capuchins, "
                       "and macros across three forest days.",
        "duration_value": 3,
        "duration_unit": "days",
        "context": "leisure",
        "experience_type": "documentary",
        "creative_focus": ["wildlife", "macro"],
        "participants": ["Couple"],
    }


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.ui.pages.event_header_dialog import EventHeaderDialog
    from mira.ui.theme import apply_theme

    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)

        # Backdrop host so the dialog's modal-frame styling is visible.
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(820, 980)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        dlg = EventHeaderDialog(
            existing_info=_existing_info_for_pousada(),
            parent=root,
        )
        # Render as a widget (no exec()), inserted into the host layout so
        # the smoke captures the same composition the user sees.
        dlg.setWindowFlags(Qt.WindowType.Widget)
        dlg.setFixedSize(720, 880)
        rl.addStretch()
        rl.addWidget(dlg, 0, Qt.AlignmentFlag.AlignHCenter)
        rl.addStretch()

        root.show()
        for _ in range(3):
            app.processEvents()
        pm = root.grab()
        out = out_dir / f"smoke_surface_02_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
