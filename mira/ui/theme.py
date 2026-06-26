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

    ``palette_name`` is accepted for backwards compatibility (legacy
    Picker chrome callers still pass ``"Mira"``); the value is ignored —
    there is one palette now, held in :mod:`mira.ui.palette`. Switch via
    ``mode`` only.
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
        # spec/92 §4 Stage 4c slice 2 — per-theme aliases for what used
        # to be theme-divergent literal colours in dark.qss / light.qss.
        # Each token below picks the theme-specific value so the rule
        # body can be IDENTICAL between modes (and migrate into the
        # single redesign.qss model with no per-theme branching).
        "primary_disabled_text": (
            disabled_text.name() if win_is_dark else "#FFFFFF"
        ),
        "statusbreakdown_bg": (
            p["card2"] if win_is_dark else p["line"]
        ),
        # spec/44 §3 — EventCardStatusBadge[state="open"] is the green
        # "live event" pill. Per-theme tweak: dark gets a brighter
        # green + higher alpha; light gets the desaturated green + a
        # lower alpha so the badge reads as a soft chip on the white card.
        "status_open_bg": (
            "rgba(63, 157, 95, 0.22)" if win_is_dark
            else "rgba(63, 157, 95, 0.18)"
        ),
        "status_open_color": "#6DC587" if win_is_dark else "#3F9D5F",
        # spec/44 §3 — EventCardTypeBadge[type=...] base + per-type
        # colours. Dark mode uses brighter saturated values; light uses
        # darker desaturated ones so contrast against the white card
        # stays readable.
        "type_default_bg": "#4B5563" if win_is_dark else "#888888",
        "type_trip":      "#3B82F6" if win_is_dark else "#2563EB",
        "type_session":   "#22C55E" if win_is_dark else "#16A34A",
        "type_occasion":  "#EC4899" if win_is_dark else "#DB2777",
        "type_project":   "#A78BFA" if win_is_dark else "#7C3AED",
        # F-032 — InfoCardRow[variant] border + title colours. Three
        # named variants (bucket=warm amber, camera=steel blue,
        # day=teal). Each tracks a dark-mode brighter / light-mode
        # darker shift; hover bumps the same hue.
        "info_bucket":        "#D49560" if win_is_dark else "#B07A3F",
        "info_bucket_hover":  "#E5AC78" if win_is_dark else "#C99155",
        "info_camera":        "#6088C0" if win_is_dark else "#4A6FA5",
        "info_camera_hover":  "#7BA3D8" if win_is_dark else "#6088C0",
        "info_day":           "#4AA3B0" if win_is_dark else "#2A7F8A",
        "info_day_hover":     "#62BDC9" if win_is_dark else "#3A9EAE",
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


def _overlay_exif_font_px() -> int:
    """The on-photo overlay pill's text size (px) from the roaming Settings
    repo, clamped to a sane range. Falls back to the design default on any
    load failure so a settings-less boot path still themes cleanly."""
    from mira.ui.palette import DEFAULT_OVERLAY_EXIF_FONT_PX
    try:
        from mira.settings.repo import SettingsRepo
        px = int(SettingsRepo().load().overlay_exif_font_px)
    except Exception:                                              # noqa: BLE001
        return DEFAULT_OVERLAY_EXIF_FONT_PX
    return max(6, min(28, px))


def apply_theme(
    app: QApplication, mode: Mode = "dark", *, palette_name: str = "Mira"
) -> None:
    """Apply a theme end-to-end. Idempotent.

    Sequence: clickable-cursor filter → resolve tokens → Fusion + ``QPalette`` →
    the single design-system QSS template (``assets/themes/redesign.qss``,
    single-brace substituted via :func:`mira.ui.palette.build_redesign_qss`)
    set as the app stylesheet.

    Spec/92 §4 Stage 4d (commit pending): the legacy ``dark.qss`` / ``light.qss``
    templates and their ``str.format_map`` substitution branch have been
    retired; every role-bearing rule now lives in ``redesign.qss``. The
    ``resolve_theme_colors()`` legacy-aliases shim stays as the compatibility
    layer so callers (and the migrated rules themselves) can keep using
    ``{window}`` / ``{text}`` / ``{primary_hover}`` etc.

    Default mode is ``"dark"`` (was ``"light"`` under the Gulf livery) —
    the design system's primary identity. ``palette_name`` is accepted but
    ignored; see :func:`resolve_theme_colors`.
    """
    from mira.ui.base.clickable_cursor import install_clickable_cursor_filter
    from mira.ui.base.focus_keeper import install_focus_keeper
    from mira.ui.base.wheel_guard import install_wheel_guard

    install_clickable_cursor_filter(app)
    # App-wide focus guard — stops focus from following the mouse when
    # tooltips / popups deactivate-reactivate the active window. See
    # ``mira/ui/base/focus_keeper.py`` for the full rationale.
    install_focus_keeper(app)
    # App-wide wheel guard — stops mouse wheel over an unfocused
    # QComboBox / QAbstractSpinBox from silently changing its value
    # (and from grabbing focus via the default WheelFocus policy).
    # See ``mira/ui/base/wheel_guard.py`` for the full rationale.
    install_wheel_guard(app)

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
    # build_redesign_qss substitutes whatever's in ``resolved`` (the
    # full resolve_theme_colors() shim — canonical tokens + legacy aliases
    # + computed hover/pressed/disabled variants), so pre-populate the
    # chevron URL on the resolved dict alongside check_icon_url instead of
    # re-deriving it inside palette.py.
    resolved["chevron_down_icon_url"] = (
        Path(__file__).resolve().parents[2]
        / "assets" / "icons" / "glyphs" / "chevron_down.svg"
    ).as_posix()
    # spec/134 — the on-photo overlay pill's text size is a user setting
    # (GridTileExif / CutPlayOverlay QSS roles). Read defensively so an
    # early-boot / settings-less path (smoke scripts, tests) falls back to
    # the design default rather than failing the whole theme apply.
    resolved["overlay_exif_font_px"] = str(_overlay_exif_font_px())

    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(build_qpalette(resolved))

    # Single design-system template (spec/92 §4 Stage 4d — legacy
    # dark.qss / light.qss retired; every rule now lives here). The
    # `resolved` dict carries the full token vocabulary so any rule
    # migrated out of the legacy templates substitutes with the same
    # values.
    try:
        redesign_qss = build_redesign_qss(mode, tokens=resolved)
    except FileNotFoundError:
        log.warning(
            "assets/themes/redesign.qss missing; design-system roles will "
            "fall through to Qt defaults"
        )
        redesign_qss = ""
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to build redesign QSS: %s", exc)
        redesign_qss = ""

    app.setStyleSheet(redesign_qss)

    # QSS-styled widgets restyle automatically on setStyleSheet, but custom
    # paintEvent widgets do not — nudge every widget to repaint so painted
    # colours follow the new theme immediately (otherwise a light/dark toggle
    # leaves the logo, thumbs, progress bars, etc. showing the old colours).
    for widget in app.allWidgets():
        widget.update()

    log.info("Applied theme: mode=%s (Mira design system)", mode)
