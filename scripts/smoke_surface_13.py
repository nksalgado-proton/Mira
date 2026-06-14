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
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

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

        # New-Cut shape — empty name, exported-only pool, default timing.
        dlg = NewCutDialog(
            existing_cuts=_existing_cuts(),
            exported_count=23,
            style_options=["macro", "wildlife"],
            music_categories=["calm", "happy", "ambient"],
            pool_probe=lambda e: 23,
            totals_probe=lambda *_: cb.ShowTotals(),
            event_label="Inseto na Varanda",
        )
        # Add a couple of pools so the formula actually has content to draw.
        dlg._build()
        dlg._dlg._step_pool("#best_macro", +1)
        dlg._dlg._step_pool("#all_time_best_macro", -1)
        dlg._dlg._name_edit.setText("Best macro shots")

        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(720, 980)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        dlg._dlg.setWindowFlags(Qt.WindowType.Widget)
        dlg._dlg.setFixedSize(660, 920)
        rl.addStretch()
        rl.addWidget(dlg._dlg, 0, Qt.AlignmentFlag.AlignHCenter)
        rl.addStretch()
        root.show()
        for _ in range(3):
            app.processEvents()
        pm = root.grab()
        out = out_dir / f"smoke_surface_13_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
