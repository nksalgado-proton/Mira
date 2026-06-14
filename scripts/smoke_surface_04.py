"""Real-asset screenshot smoke for Surface 04 (Event Days Table dialog).

Builds an ``EventDaysTableDialog`` with a fleet of ``ScanDayRow`` that
mirror the mockup deck (Argentine road trip across 7 days, mixed
countries, varying TZs, descriptive day notes). Row 2 is selected to
exercise the 3px accent left-edge + accent-soft row wash the redesign
specifies; day 6 is *un*checked so the include-toggle visual reads
against both states.

Spec/65 §6 verifies with real assets — placeholder rows would miss the
day-strip footer + the row chrome the redesign carries. Run::

    python scripts/smoke_surface_04.py

Outputs::

    scripts/smoke_surface_04_dark.png
    scripts/smoke_surface_04_light.png
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _build_rows():
    from core.scan_source import ScanDayRow
    # tz_minutes is the column unit; -180 = UTC-03:00 (BR/AR).
    return [
        ScanDayRow(
            date=date(2025, 9, 27), checked=True, country_code="AR",
            tz_minutes=-180,
            location="Salta > Tilcara",
            description="Viagem a Tilcara e Passeio a Pumamarca",
        ),
        ScanDayRow(
            date=date(2025, 9, 28), checked=True, country_code="AR",
            tz_minutes=-180,
            location="Tilcara",
            description="Passeio a los 14 Colores del Hornocal",
        ),
        ScanDayRow(
            date=date(2025, 9, 29), checked=True, country_code="AR",
            tz_minutes=-180,
            location="Tilcara > Pumamarca",
            description="Passeio a Salinas Grandes",
        ),
        ScanDayRow(
            date=date(2025, 9, 30), checked=True, country_code="AR",
            tz_minutes=-180,
            location="Pumamarca > Cachi",
            description="Dia de Longa Viagem",
        ),
        ScanDayRow(
            date=date(2025, 10, 1), checked=True, country_code="AR",
            tz_minutes=-180,
            location="Cachi > Cafayate",
            description="Passando por Quebrada de las Flechas",
        ),
        ScanDayRow(
            date=date(2025, 10, 2), checked=False, country_code="AR",
            tz_minutes=-180,
            location="Cafayate",
            description="Colorados, Anfiteatro e outras formacoes",
        ),
        ScanDayRow(
            date=date(2025, 10, 3), checked=True, country_code="AR",
            tz_minutes=-180,
            location="Cafayate > Salta",
            description="Viagem a Salta e volta ao Brasil",
        ),
    ]


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.ui.pages.event_days_table_dialog import EventDaysTableDialog
    from mira.ui.theme import apply_theme

    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)

        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1280, 880)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        dlg = EventDaysTableDialog(
            _build_rows(),
            can_save_load_csv=True,
            parent=root,
        )
        dlg.setWindowFlags(Qt.WindowType.Widget)
        dlg.setFixedSize(1200, 780)
        # Select row 1 (day 2) so the accent left-edge + accent-soft row
        # wash are visible against the other rows' default chrome.
        dlg._table.selectRow(1)
        rl.addStretch()
        rl.addWidget(dlg, 0, Qt.AlignmentFlag.AlignHCenter)
        rl.addStretch()

        root.show()
        for _ in range(3):
            app.processEvents()
        pm = root.grab()
        out = out_dir / f"smoke_surface_04_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
