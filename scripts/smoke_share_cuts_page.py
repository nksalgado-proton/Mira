"""Smoke for the redesigned ShareCutsPage (Surface 09) with the spec/71
share-state identity header applied (spec/70 Phase 3 §5).

Drives :class:`_CutsListView.setForPreview` with synthetic snapshots and
screenshots in both themes. Verifies the Share identity header (pink
rail + SHARE badge — the closed-card treatment, *not* a phase colour)
sits cleanly above the ``#exported`` pool card and the Cut rows (Open
primary · Adjust ghost · kebab for Rename/Delete).

The smoke is preview-only because today's library has no closed event
with exported finals; the chassis lifecycle (``open_event`` ↔ gateway)
is exercised by ``tests/test_cuts_shell.py``. When a real closed +
exported event lands, swap in the live ``ShareCutsPage.open_event``
path the way ``smoke_export_page.py`` does.

Outputs::

    scripts/smoke_share_cuts_page_dark.png
    scripts/smoke_share_cuts_page_light.png
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget


def _synthetic_snapshots():
    """A pool with a believable file count + a small set of Cuts that
    exercise the row chrome (Open / Adjust / kebab) and the meta bits
    (count · duration · description · exported date)."""
    from mira.ui.pages.share_cuts_page import CutSnapshot, PoolSnapshot

    pool = PoolSnapshot(
        exported_count=142,
        sub_line=(
            "142 exported files — the universe every cut starts from."
        ),
    )
    cuts = [
        CutSnapshot(
            cut_id="cut-best",
            name="best_of",
            item_count=36,
            duration_seconds=216,         # 3:36
            description="ambient",
            exported_date="2026-06-12",
        ),
        CutSnapshot(
            cut_id="cut-fam",
            name="family_share",
            item_count=18,
            duration_seconds=108,         # 1:48
            description="happy",
            exported_date="2026-06-10",
        ),
        CutSnapshot(
            cut_id="cut-macro",
            name="macro_close_ups",
            item_count=24,
            duration_seconds=144,         # 2:24
            description="calm",
            exported_date="",
        ),
    ]
    return pool, cuts


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.ui.pages.share_cuts_page import _CutsListView
    from mira.ui.theme import apply_theme

    pool, cuts = _synthetic_snapshots()
    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1280, 800)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        view = _CutsListView(parent=root)
        view.setForPreview(pool, cuts)
        rl.addWidget(view)
        root.show()
        for _ in range(40):
            app.processEvents()
        out = out_dir / f"smoke_share_cuts_page_{mode}.png"
        root.grab().save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
