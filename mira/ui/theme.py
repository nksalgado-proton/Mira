"""Theme system — palette resolver + QPalette + QSS template application.

2026-06-13 — foundation install of the Mira redesign (the "analytics dashboard"
visual system from ``MiraCrafter Redesign/`` on Nelson's Desktop). The previous
Gulf-livery palette (Gulf blue ``#3AA5D9`` + Gulf orange ``#F37021``) was retired
here; the new identity is indigo accent (``#7c6cff`` dark / ``#6a5cff`` light) on
charcoal/white surfaces. Photo-state border colors are LOCKED per design-system
§5a and live in :mod:`mira.ui.palette` (``picked`` / ``skipped`` / ``compare``
/ ``mixed``).

Integration shape ("option B — layer over the legacy templates"):

1. The new tokens come from :mod:`mira.ui.palette` — single source of truth.
2. :func:`resolve_theme_colors` returns a single flat dict that exposes BOTH
   the new design-system tokens (``accent`` / ``ink`` / ``bg`` / ``card`` /
   etc.) AND legacy aliases (``primary`` → ``accent``, ``text`` → ``ink``,
   ``window`` → ``bg``, ``warning`` → ``amber``, ``success`` → ``green``,
   ``error`` → ``red``, ``focus_border`` → ``accent``, hover/pressed/disabled
   variants computed from the accent). Both the legacy ``dark.qss`` /
   ``light.qss`` and the new ``redesign.qss`` substitute from this dict.
3. :func:`apply_theme` formats the legacy template (Python ``.format_map``)
   AND the new ``redesign.qss`` (single-brace substitution), concatenates
   them — legacy first, redesign second so its role rules can override
   legacy fall-through — and sets both as the app stylesheet. Fusion +
   ``QPalette`` + clickable-cursor filter + the explicit-font-scale knob
   from ``mira.ui.app`` are unchanged.

This preserves every existing surface's styling (their legacy ObjectNames
still match) while making new design-system roles (``#Card``, ``#Primary``,
``#PageTitle``, etc.) work without touching each surface yet. Surfaces are
migrated to the new role catalog one at a time by spec/40-style sweep.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QStyleFactory

from mira.ui.palette import PALETTE, RADIUS, build_redesign_qss

log = logging.getLogger(__name__)

Mode = Literal["light", "dark"]


def _is_dark(color: QColor) -> bool:
    return color.lightness() < 128


def _tint(base: QColor, accent: QColor, factor: float) -> QColor:
    r = base.red() * (1 - factor) + accent.red() * factor
    g = base.green() * (1 - factor) + accent.green() * factor
    b = base.blue() * (1 - factor) + accent.blue() * factor
    return QColor(int(r), int(g), int(b))


def _rgba(color: QColor, alpha: float) -> str:
    return f"rgba({color.red()},{color.green()},{color.blue()},{alpha:.2f})"


def resolve_theme_colors(
    palette_name: str = "Mira", mode: Mode = "dark"
) -> dict[str, str]:
    """Resolve the active mode into a flat dict of QSS-placeholder strings.

    Returns BOTH the new design-system tokens AND the legacy aliases the old
    QSS templates expect, so a single ``.format_map`` call against this dict
    fills every ``{token}`` in either template family.

    ``palette_name`` is accepted for backwards compatibility (callers in
    ``mira.ui.picked.list_button`` and ``mira.ui.picked.pick_stats_chart``
    still pass ``"Mira"``); the value is ignored — there is one palette now,
    held in :mod:`mira.ui.palette`. Switch via ``mode`` only.
    """
    if mode not in ("light", "dark"):
        raise ValueError(f"unknown mode: {mode!r}")

    p = PALETTE[mode]
    Bg = QColor(p["bg"])
    Card = QColor(p["card"])
    Card2 = QColor(p["card2"])
    Line = QColor(p["line"])
    Accent = QColor(p["accent"])
    AccentSoft = QColor(p["accent_soft"])
    Amber = QColor(p["amber"])
    win_is_dark = _is_dark(Bg)

    if win_is_dark:
        hover_bg = Card2.lighter(115)
        pressed_bg = Card2.darker(110)
        accent_hover = Accent.lighter(115)
        accent_pressed = Accent.darker(115)
        accent_disabled = _tint(Bg, Accent, 0.30)
        border_strong = Line.lighter(140)
        card_bg_hover = Card.lighter(115)
        card_bg_disabled = Bg.lighter(105)
        disabled_bg = Bg
    else:
        hover_bg = Card2.darker(105)
        pressed_bg = Card2.darker(115)
        accent_hover = Accent.darker(110)
        accent_pressed = Accent.darker(125)
        accent_disabled = _tint(QColor("#FFFFFF"), Accent, 0.45)
        border_strong = Line.darker(120)
        card_bg_hover = Card.darker(103)
        card_bg_disabled = Bg
        disabled_bg = QColor("#F0EDE7")

    disabled_text = QColor("#555555") if win_is_dark else QColor("#BBBBBB")
    sidebar_bg = Card2 if win_is_dark else Card2
    sidebar_item_hover_bg = Card.lighter(115) if win_is_dark else Card.darker(103)

    photo_canvas_bg = "#0A0A0A" if win_is_dark else "#252525"
    status_unavailable_bg = p["card2"]

    # New design-system tokens, verbatim from palette.PALETTE[mode]
    new_tokens = dict(p)

    # Legacy aliases — every key the old dark.qss / light.qss templates
    # reference (Gulf primary / accent / window / text / hover_bg / etc.)
    # now maps onto the new indigo identity. Unknown legacy keys fall back
    # to sensible neighbours; if a legacy template references something
    # missing here, ``.format_map`` raises KeyError and we'll spot it in
    # the apply_theme log line.
    legacy_aliases = {
        "window": p["bg"],
        "base": p["card"],
        "subtle": p["card2"],
        "button": p["card2"],
        "card_bg": p["card"],
        "card_bg_hover": card_bg_hover.name(),
        "card_bg_disabled": card_bg_disabled.name(),
        "sidebar_bg": sidebar_bg.name(),
        "sidebar_item_hover_bg": sidebar_item_hover_bg.name(),
        "text": p["ink"],
        "text_on_base": p["ink"],
        "text_on_button": p["ink"],
        "text_secondary": p["ink_soft"],
        "text_muted": p["ink_faint"],
        "text_inverse": "#FFFFFF",
        "border_subtle": p["line"],
        "border_strong": border_strong.name(),
        "hover_bg": hover_bg.name(),
        "hover_border": p["accent"],
        "pressed_bg": pressed_bg.name(),
        "pressed_border": accent_pressed.name(),
        "disabled_bg": disabled_bg.name(),
        "disabled_text": disabled_text.name(),
        "primary": p["accent"],
        "primary_hover": accent_hover.name(),
        "primary_pressed": accent_pressed.name(),
        "primary_disabled": accent_disabled.name(),
        "primary_subtle": _rgba(Accent, 0.15 if win_is_dark else 0.12),
        "primary_text": "#FFFFFF",
        # Legacy "accent" was the Gulf orange CTA tone; under the design
        # system the single CTA color is the indigo accent. So accent and
        # primary now resolve to the same hex.
        "accent_hover": accent_hover.name(),
        "accent_pressed": accent_pressed.name(),
        "accent_disabled": accent_disabled.name(),
        "accent_subtle": _rgba(Accent, 0.15 if win_is_dark else 0.12),
        "accent_text": "#FFFFFF",
        "warning": p["amber"],
        "warning_bg": _rgba(Amber, 0.18 if win_is_dark else 0.15),
        "warning_fg": p["amber"],
        "success": p["green"],
        "error": p["red"],
        "status_unavailable_bg": status_unavailable_bg,
        "photo_canvas_bg": photo_canvas_bg,
        "focus_border": p["accent"],
        "menu_border": p["line"],
    }

    # new_tokens contains `accent` + `accent_soft`; legacy_aliases would also
    # write `accent` (mapped to the same value). Order: legacy first, new
    # tokens second so the canonical design-system value wins on any overlap.
    return {**legacy_aliases, **new_tokens}


def build_qpalette(resolved: dict[str, str]) -> QPalette:
    """Build a QPalette from a resolved-color dict.

    ``Highlight`` = the design-system accent (indigo). ``Link`` = accent too
    (legacy was Gulf orange; the new system has one CTA tone). ``PlaceholderText``
    is set from ``ink_faint`` so QLineEdit placeholders read correctly — QSS
    has no usable pseudo-element for placeholder color, so the QPalette role
    is the only working lever.
    """
    p = QPalette()

    Window = QColor(resolved["window"])
    Base = QColor(resolved["base"])
    Button = QColor(resolved["button"])
    Primary = QColor(resolved["primary"])

    win_text = QColor(resolved["text"])
    base_text = QColor(resolved["text_on_base"])
    button_text = QColor(resolved["text_on_button"])
    disabled_text = QColor(resolved["disabled_text"])
    ink_faint = QColor(resolved["text_muted"])

    p.setColor(QPalette.ColorRole.Window, Window)
    p.setColor(QPalette.ColorRole.WindowText, win_text)
    p.setColor(QPalette.ColorRole.Base, Base)
    p.setColor(QPalette.ColorRole.AlternateBase, Base.darker(105))
    p.setColor(QPalette.ColorRole.Text, base_text)
    p.setColor(QPalette.ColorRole.ToolTipBase, Base)
    p.setColor(QPalette.ColorRole.ToolTipText, base_text)
    p.setColor(QPalette.ColorRole.Button, Button)
    p.setColor(QPalette.ColorRole.ButtonText, button_text)
    p.setColor(QPalette.ColorRole.Light, Window.lighter(120))
    p.setColor(QPalette.ColorRole.Midlight, Window.lighter(110))
    p.setColor(QPalette.ColorRole.Mid, Window.darker(120))
    p.setColor(QPalette.ColorRole.Dark, Window.darker(150))
    p.setColor(QPalette.ColorRole.Shadow, QColor(0, 0, 0, 100))
    p.setColor(QPalette.ColorRole.Highlight, Primary)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    p.setColor(QPalette.ColorRole.Link, Primary)
    p.setColor(QPalette.ColorRole.LinkVisited, Primary.darker(110))
    p.setColor(QPalette.ColorRole.PlaceholderText, ink_faint)

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled_text)

    return p


def _themes_dir() -> Path:
    """Locate the shared ``assets/themes/`` (project root) from this package's depth."""
    return Path(__file__).resolve().parents[2] / "assets" / "themes"


def _load_qss_template(mode: Mode) -> str:
    """Load the legacy ``{light,dark}.qss`` template — the rich role catalog
    every existing surface still relies on. Falls back silently if missing."""
    themes = _themes_dir()
    candidate = themes / f"{mode}.qss"
    if not candidate.exists():
        candidate = themes / "light.qss"
    if not candidate.exists():
        return ""
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Failed to read QSS template %s: %s", candidate, exc)
        return ""


def apply_theme(
    app: QApplication, mode: Mode = "dark", *, palette_name: str = "Mira"
) -> None:
    """Apply a theme end-to-end. Idempotent.

    Sequence: clickable-cursor filter → resolve tokens → Fusion + ``QPalette`` →
    legacy QSS (``.format_map``'d) + design-system QSS (single-brace
    substituted), concatenated and set as the app stylesheet.

    Default mode is now ``"dark"`` (was ``"light"`` under the Gulf livery) —
    the design system's primary identity. ``palette_name`` is accepted but
    ignored; see :func:`resolve_theme_colors`.
    """
    from mira.ui.base.clickable_cursor import install_clickable_cursor_filter

    install_clickable_cursor_filter(app)

    # Painted widgets (Thumb, StageProgress, Donut, Card shadows, MiraMark /
    # _Wordmark, the cross-event glyph, …) pick their palette colours from
    # QApplication.property("theme"). It must be set here or those widgets
    # render dark regardless of mode — the bug behind "the logo text stays
    # white in light theme". Set BEFORE the repaint nudge below.
    app.setProperty("theme", mode)

    resolved = resolve_theme_colors(palette_name, mode)
    icon_path = (
        Path(__file__).resolve().parents[2] / "assets" / "icons" / "check.svg"
    )
    resolved["check_icon_url"] = icon_path.as_posix()

    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(build_qpalette(resolved))

    # Legacy template — Python format string with `{{ }}` escapes
    legacy_template = _load_qss_template(mode)
    if legacy_template:
        try:
            legacy_qss = legacy_template.format_map(resolved)
        except KeyError as exc:
            log.error(
                "Legacy QSS template references missing key %s in mode=%r; "
                "falling back to unformatted template", exc, mode
            )
            legacy_qss = ""
    else:
        legacy_qss = ""
        log.warning("No legacy QSS template found for mode=%r", mode)

    # Design-system template — single-brace substitution via palette helper
    try:
        redesign_qss = build_redesign_qss(mode)
    except FileNotFoundError:
        log.warning(
            "assets/themes/redesign.qss missing; new design-system roles will "
            "fall through to Qt defaults"
        )
        redesign_qss = ""
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to build redesign QSS: %s", exc)
        redesign_qss = ""

    app.setStyleSheet(
        legacy_qss
        + "\n\n/* ===== Mira design-system roles (redesign.qss) ===== */\n\n"
        + redesign_qss
    )

    # QSS-styled widgets restyle automatically on setStyleSheet, but custom
    # paintEvent widgets do not — nudge every widget to repaint so painted
    # colours follow the new theme immediately (otherwise a light/dark toggle
    # leaves the logo, thumbs, progress bars, etc. showing the old colours).
    for widget in app.allWidgets():
        widget.update()

    log.info("Applied theme: mode=%s (Mira design system)", mode)
