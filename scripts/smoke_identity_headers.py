"""Smoke for the spec/71 surface identity headers.

Instantiates the four per-phase :class:`SurfaceIdentityHeader` configs
(Quick Sweep / Pick / Edit / Export) stacked in one window, in both
themes, and writes paste-back-friendly screenshots:

    scripts/smoke_identity_headers_dark.png
    scripts/smoke_identity_headers_light.png

Isolated from gateway / engine so the eyeball is on the header chrome
itself — phase-colour rail, name badge, purpose line, the per-surface
legend wording. Continuity check: the rail colours must match the
matching phase wedge in the events-card 2×2 donut.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


def _build_panel() -> QWidget:
    """One column with all four surface headers stacked + a label per
    block so the screenshot reads like the spec/71 §"Per-surface spec"
    table side by side."""
    from mira.ui.design import SurfaceIdentityHeader, primary_button
    from mira.ui.i18n import tr

    root = QWidget()
    root.setObjectName("RedesignRoot")
    outer = QVBoxLayout(root)
    outer.setContentsMargins(24, 18, 24, 18)
    outer.setSpacing(28)

    cases = [
        dict(
            tag="Quick Sweep (Collect)",
            kwargs=dict(
                phase="collect",
                name=tr("Quick Sweep"),
                purpose=tr("Fast pass — skip the obvious rejects"),
                legend=[
                    ("picked", tr("Keeping")),
                    ("skipped", tr("Skipped")),
                    ("mixed", tr("Mixed")),
                ],
                reminder=tr(
                    "Everything starts kept — press X to skip the "
                    "rejects."),
            ),
        ),
        dict(
            tag="Picker (Pick)",
            kwargs=dict(
                phase="pick",
                name=tr("Pick"),
                purpose=tr("Decide each shot — pick the keepers"),
                legend=[
                    ("picked", tr("Picked")),
                    ("skipped", tr("Skipped")),
                    ("compare", tr("Compare")),
                    ("mixed", tr("Mixed cluster")),
                ],
                reminder=tr(
                    "Border = your pick · P pick · X skip · C compare."),
            ),
        ),
        dict(
            tag="Editor (Edit) — no state legend",
            kwargs=dict(
                phase="edit",
                name=tr("Edit"),
                purpose=tr("Develop your picked keepers"),
                reminder=tr(
                    "\\ compare before/after · F10 full-res preview."),
            ),
        ),
        dict(
            tag="Export (Export)",
            kwargs=dict(
                phase="export",
                name=tr("Export"),
                purpose=tr("Choose what ships"),
                legend=[
                    ("picked", tr("Will export")),
                    ("skipped", tr("Won't export")),
                    ("mixed", tr("Mixed")),
                ],
                reminder=tr(
                    "Everything ships by default — press X to drop what "
                    "you don't want."),
            ),
        ),
    ]

    for case in cases:
        block = QVBoxLayout()
        block.setSpacing(6)
        label = QLabel(case["tag"])
        label.setObjectName("Sub")
        block.addWidget(label)
        kwargs = dict(case["kwargs"])
        if case["kwargs"]["phase"] == "export":
            kwargs["action"] = primary_button(tr("Export green (42)"))
        block.addWidget(SurfaceIdentityHeader(**kwargs))
        outer.addLayout(block)
    outer.addStretch(1)
    root.resize(960, 720)
    return root


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.ui.theme import apply_theme

    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)
        panel = _build_panel()
        panel.show()
        for _ in range(20):
            app.processEvents()
        out = out_dir / f"smoke_identity_headers_{mode}.png"
        panel.grab().save(str(out), "PNG")
        print(f"wrote {out}")
        panel.close()
        panel.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
