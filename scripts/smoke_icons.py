"""spec/69 real-asset screenshot smoke.

Renders every spec/69 surface fragment that now carries an SVG glyph,
on BOTH the dark and the light themes, into two side-by-side PNGs.
Eyeball the output to confirm:

* the visited eye chip (picker / editor / video picker) reads as a
  proper eye outline + pupil instead of `◉`;
* the day-grid visited tick reads as a check glyph in a translucent pill;
* the Thumb mixed-cluster split chip shows "3 ✓ · 2 ✗" with line-icon
  check + cross (not the Unicode placeholders);
* the cluster cover badges keep working (Thumb already shipped them);
* the search field's magnifier (existing) and cross-event tile
  (existing) still render — they share the helper now.

Run:
    python scripts/smoke_icons.py
Outputs:
    scripts/smoke_icons_dark.png
    scripts/smoke_icons_light.png

Spec/65 §6 verify pattern: real assets, not the gradient placeholders
the unit-test fixtures use.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


def _real_photo_pixmap(w: int, h: int, label: str) -> QPixmap:
    """A gradient stand-in that *looks* like a photo (not a placeholder
    block). Real bytes would be better, but the gradient + label still
    exercises the SVG-tint colour against varying backdrops."""
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    grad = QLinearGradient(0, 0, w, h)
    grad.setColorAt(0.0, QColor("#3a5a8b"))
    grad.setColorAt(0.5, QColor("#7c6cff"))
    grad.setColorAt(1.0, QColor("#ff7aa9"))
    p.fillRect(img.rect(), grad)
    f = p.font()
    f.setPointSize(14)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, label)
    p.end()
    return QPixmap.fromImage(img)


def _build_smoke(mode: str) -> QWidget:
    """One column showcasing every spec/69 site."""
    from mira.ui.design import (
        GLYPH_CHECK,
        GLYPH_CROSS,
        GLYPH_EYE,
        GLYPH_SEARCH,
        Thumb,
        search_field,
        tinted_svg_pixmap,
    )
    from mira.ui.palette import PALETTE
    from mira.ui.pages._cross_event_band import CrossEventCutsBand

    app = QApplication.instance()
    if app is not None:
        app.setProperty("theme", mode)

    bg = PALETTE[mode]["bg"]
    fg = PALETTE[mode]["ink"]
    accent = PALETTE[mode]["accent"]

    panel = QWidget()
    panel.setObjectName(f"SmokePanel_{mode}")
    panel.setStyleSheet(
        f"#SmokePanel_{mode} {{ background: {bg}; }}"
        f" QLabel {{ color: {fg}; }}"
    )
    v = QVBoxLayout(panel)
    v.setContentsMargins(28, 24, 28, 24)
    v.setSpacing(20)

    title = QLabel(f"Mira icon wiring — {mode} theme")
    f = title.font(); f.setPointSize(15); f.setBold(True)
    title.setFont(f)
    v.addWidget(title)

    # ── Row 1: the bare glyphs, white + accent tints ─────────────────
    row1_label = QLabel("Line-icon family glyphs (eye / check / cross):")
    v.addWidget(row1_label)
    row1 = QHBoxLayout(); row1.setSpacing(20)
    for glyph_path, name in (
        (GLYPH_EYE, "eye"),
        (GLYPH_CHECK, "check"),
        (GLYPH_CROSS, "cross"),
    ):
        for tint, tint_name in ((QColor(fg), "ink"), (QColor(accent), "accent")):
            chip = QWidget()
            cv = QVBoxLayout(chip); cv.setSpacing(4)
            pic = QLabel()
            pic.setFixedSize(40, 40)
            pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pic.setPixmap(tinted_svg_pixmap(glyph_path, 28, tint))
            cv.addWidget(pic, alignment=Qt.AlignmentFlag.AlignCenter)
            lab = QLabel(f"{name}/{tint_name}")
            f = lab.font(); f.setPointSize(8); lab.setFont(f)
            cv.addWidget(lab, alignment=Qt.AlignmentFlag.AlignCenter)
            row1.addWidget(chip)
    row1.addStretch()
    v.addLayout(row1)

    # ── Row 2: the visited eye chip on a photo backdrop ─────────────
    row2_label = QLabel("Visited eye chip (picker / editor / video picker):")
    v.addWidget(row2_label)
    eye_host = QLabel()
    eye_host.setFixedSize(360, 100)
    eye_host.setPixmap(_real_photo_pixmap(360, 100, "photo backdrop"))
    eye = QLabel(eye_host)
    eye.setObjectName("StageEyeChip")
    eye.setStyleSheet(
        "background: rgba(8,10,16,0.74);"
        " border: 1px solid rgba(255,255,255,0.18); border-radius: 10px;"
        " padding: 4px 8px;"
    )
    eye.setPixmap(tinted_svg_pixmap(GLYPH_EYE, 14, "#ffffff"))
    eye.adjustSize()
    eye.move(310, 12)
    v.addWidget(eye_host)

    # ── Row 3: visited-tick + state border on Thumb (the redesigned
    # cell that replaced DayGridCell — same painted contract). ───────
    from PyQt6.QtCore import QSize
    row3_label = QLabel("Thumb visited eye + 3px state border (small + large):")
    v.addWidget(row3_label)
    row3 = QHBoxLayout(); row3.setSpacing(20)
    small_thumb = _real_photo_pixmap(80, 80, "")
    small_cell = Thumb(
        small_thumb, state="picked", visited=True, size=QSize(80, 80))
    big_thumb = _real_photo_pixmap(220, 220, "")
    big_cell = Thumb(
        big_thumb, state="picked", visited=True, size=QSize(220, 220))
    row3.addWidget(small_cell)
    row3.addWidget(big_cell)
    row3.addStretch()
    v.addLayout(row3)

    # ── Row 4: Thumb mixed-cluster split chip ──────────────────────
    row4_label = QLabel("Thumb split chip — 3✓·2✗ via line icons:")
    v.addWidget(row4_label)
    split_thumb = Thumb(
        _real_photo_pixmap(220, 165, "cluster cover"),
        state="mixed",
        size=QSize(220, 165),
        cluster_type="burst",
        cluster_split=(3, 2),
    )
    v.addWidget(split_thumb)

    # ── Row 5: search field + cross-event glyph (existing, factored) ─
    row5_label = QLabel("Search field magnifier + Cross-Event tile glyph:")
    v.addWidget(row5_label)
    sf = search_field("Search events…")
    sf.setFixedWidth(360)
    v.addWidget(sf)
    band = CrossEventCutsBand()
    v.addWidget(band)

    v.addStretch()
    return panel


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "scripts"

    app = QApplication.instance() or QApplication(sys.argv)
    from mira.ui.theme import apply_theme

    for mode in ("dark", "light"):
        apply_theme(app, mode)
        panel = _build_smoke(mode)
        panel.resize(560, 920)
        panel.show()
        app.processEvents()
        pm = panel.grab()
        out_path = out_dir / f"smoke_icons_{mode}.png"
        pm.save(str(out_path), "PNG")
        print(f"wrote {out_path}")
        panel.close()
        panel.deleteLater()

    return 0


if __name__ == "__main__":
    sys.exit(main())
