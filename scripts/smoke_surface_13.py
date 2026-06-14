"""Real-asset screenshot smoke for Surface 13 (New Cut dialog).

Drives the **adapter** (``mira/ui/shared/new_cut_dialog_adapter.py``) the
same way ``cuts_shell.py`` calls it — i.e. via the legacy 7-key ctor —
so the smoke walks the exact code path the redesigned dialog gets in
production. The cut context is Pousada Salve Floresta–shaped:
``#exported`` baseline + two existing cuts (``#best_macro`` / ``#all
_time_best_macro``) so the formula display has real content to render.

Two passes: New-Cut shape (no prefill) and Edit-Cut shape (prefill +
heading_text override) so the smoke covers both call sites in
cuts_shell.py (``_on_new_cut`` and ``_on_adjust_cut``).

Run::

    python scripts/smoke_surface_13.py

Outputs::

    scripts/smoke_surface_13_dark.png
    scripts/smoke_surface_13_light.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _existing_cuts():
    return [("best_macro", 8), ("all_time_best_macro", 3)]


def _edit_prefill():
    return SimpleNamespace(
        name="best_macro_shots",
        pool_expr_json=json.dumps([
            ["+", "exported"],
            ["+", "best_macro"],
            ["-", "all_time_best_macro"],
        ]),
        style_filter_json=json.dumps(["macro"]),
        type_filter="both",
        default_state="picked",
        target_s=600, max_s=900,
        photo_s=6.5,
        music_category="calm",
        card_style="multi",
    )


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    import core.cut_budget as cb
    from mira.ui.shared.new_cut_dialog_adapter import NewCutDialog
    from mira.ui.theme import apply_theme

    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)

        # Edit-Cut shape via prefill — name + composed pool come through
        # the context cleanly (no post-build mutation). This is the same
        # path cuts_shell._on_adjust_cut takes for an existing cut.
        dlg = NewCutDialog(
            existing_cuts=_existing_cuts(),
            exported_count=23,
            style_options=["macro", "wildlife"],
            music_categories=["calm", "happy", "ambient"],
            pool_probe=lambda e: 23,
            totals_probe=lambda *_: cb.ShowTotals(),
            event_label="Inseto na Varanda",
            prefill=_edit_prefill(),
        )
        dlg._build()

        # IMPORTANT: render the dialog as a top-level QDialog (its
        # natural shape) and grab it directly. Wrapping it in a parent
        # QWidget with addStretch + AlignHCenter caused several styled
        # descendants (the add: chips + pool count line in the Pool
        # box) to drop out of the paint pass — a Qt rendering oddity
        # specific to nested-styled-frames-under-a-parent-layout. The
        # production path (exec() as a modal) always uses the
        # top-level shape, so this is what the user actually sees.
        dlg._dlg.show()
        for _ in range(3):
            app.processEvents()
        pm = dlg._dlg.grab()
        out = out_dir / f"smoke_surface_13_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        dlg._dlg.close()
        dlg._dlg.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
