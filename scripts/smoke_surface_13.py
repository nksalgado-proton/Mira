"""Real-asset screenshot smoke for Surface 13 (the New Cut / New Recipe dialog).

Drives :class:`mira.ui.pages.new_cut_dialog.NewCutDialog` in its
Cut-face configuration (the audience-facing event Cut dialog,
spec/90 §2.1) — the same code path :class:`ShareCutsPage` exercises in
production after spec/90 Phase 4e retired the legacy
``new_cut_dialog_adapter``.

Two passes: a fresh New-Cut shape (no prefill) and an Edit-Cut shape
(seeded source + name) so the smoke covers both call sites in
``share_cuts_page.py`` (``_on_new_cut`` and ``_on_adjust_cut``).

Run::

    python scripts/smoke_surface_13.py

Outputs::

    scripts/smoke_surface_13_dark.png
    scripts/smoke_surface_13_light.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.ui.pages.new_cut_dialog import (
    SCOPE_EVENT,
        INVENTORY_EVENT,
        JOIN_OR,
        NewRecipeContext,
        NewCutDialog,
        OperandOption,
    )
    from mira.ui.theme import apply_theme

    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)
        ctx = NewRecipeContext(
            event_name="Inseto na Varanda",
            available_pools=[
                OperandOption(name="#exported", count=23, kind="base",
                              tag="exported"),
                OperandOption(name="#best_macro", count=8, kind="cut",
                              tag="best_macro"),
                OperandOption(name="#all_time_best_macro", count=3,
                              kind="cut", tag="all_time_best_macro"),
            ],
            available_styles=["macro", "wildlife"],
            selected_source=[
                (JOIN_OR, OperandOption(
                    name="#exported", count=23, kind="base", tag="exported")),
                (JOIN_OR, OperandOption(
                    name="#best_macro", count=8, kind="cut",
                    tag="best_macro")),
            ],
            name="best_macro_shots",
            target_minutes=10, max_minutes=15,
            per_photo_seconds=6.5,
        )
        dlg = NewCutDialog(
            scope=SCOPE_EVENT,
            show_scope=False,
            show_hardware=False,
            inventory_scope=INVENTORY_EVENT,
            ctx=ctx,
            pool_probe=lambda _expr: 23,
        )
        dlg.show()
        for _ in range(3):
            app.processEvents()
        pm = dlg.grab()
        out = out_dir / f"smoke_surface_13_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        dlg.close()
        dlg.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
