"""Screenshot smoke for the New-Cut / New-Recipe dialog (spec/90, spec/92).

Renders ``NewRecipeDialog`` in both flavours (Cut + Collection) and both
themes. The Collection flavour exercises every section box in the
spec/92 §2.3 SectionBox collapse (Recipe toolbar, Name, Scope, Which
items? band + Source + Filters, What to do? band + Rules + Otherwise +
Runtime + Metrics); the Cut flavour exercises the trimmed subset (no
Scope). With both rendered side-by-side, the dialog covers every
SectionBox instance the spec/92 §2.3 collapse touches.

Run::

    python scripts/smoke_new_recipe_dialog.py

Outputs::

    scripts/smoke_new_recipe_dialog_cut_{dark,light}.png
    scripts/smoke_new_recipe_dialog_collection_{dark,light}.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _ctx(cameras=(), lenses=(), styles=("macro", "wildlife")):
    from mira.ui.pages.new_recipe_dialog import NewRecipeContext, OperandOption
    return NewRecipeContext(
        event_name="Costa Rica 2026",
        available_pools=[
            OperandOption(name="#exported", count=12, kind="base"),
            OperandOption(name="#long", count=200, kind="cut", tag="long"),
        ],
        available_styles=list(styles),
        available_cameras=list(cameras),
        available_lenses=list(lenses),
    )


def _render(app, dlg, name: str, mode: str) -> None:
    out = _REPO / "scripts" / f"smoke_new_recipe_dialog_{name}_{mode}.png"
    dlg.resize(900, 1100)
    dlg.show()
    for _ in range(3):
        app.processEvents()
    pm = dlg.grab()
    pm.save(str(out), "PNG")
    print(f"wrote {out}")
    dlg.close()
    dlg.deleteLater()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.ui.pages.new_recipe_dialog import (
        FLAVOUR_COLLECTION,
        FLAVOUR_CUT,
        INVENTORY_EVENT,
        INVENTORY_LIBRARY,
        NewRecipeDialog,
    )
    from mira.ui.theme import apply_theme

    for mode in ("dark", "light"):
        apply_theme(app, mode)

        cut = NewRecipeDialog(
            flavour=FLAVOUR_CUT,
            show_scope=False,
            show_hardware=False,
            inventory_scope=INVENTORY_EVENT,
            ctx=_ctx(),
        )
        _render(app, cut, "cut", mode)

        coll = NewRecipeDialog(
            flavour=FLAVOUR_COLLECTION,
            show_scope=True,
            show_hardware=True,
            inventory_scope=INVENTORY_LIBRARY,
            ctx=_ctx(
                cameras=("Pana+G9M2", "Sony+A7R5"),
                lenses=("100-500mm", "24-70mm"),
            ),
        )
        _render(app, coll, "collection", mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
