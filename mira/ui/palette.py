"""Mira design-system palette + token catalog (PyQt6).

Single source of truth for color tokens, sourced from the redesign drop-in
package (``MiraCrafter Redesign/palette.py`` on Nelson's Desktop, copied here
on 2026-06-13 as the foundation install of the "analytics dashboard" redesign).

The palette is consumed by two layers:

1. ``mira/ui/theme.py::resolve_theme_colors()`` builds a single resolved-tokens
   dict that exposes BOTH the new design-system token names (``accent``,
   ``ink``, ``bg``, ``card``, ``card2``, ``line``, ``green``, ``red``,
   ``amber``, ``pink``, ``accent_soft``, ``track``, ``picked``, ``skipped``,
   ``compare``, ``mixed``) AND legacy aliases the existing QSS templates
   reference (``primary`` → ``accent``, ``text`` → ``ink``, ``window`` →
   ``bg``, etc.). This lets the legacy ``dark.qss`` / ``light.qss`` and the
   new ``redesign.qss`` template both substitute from the same dict.

2. ``build_redesign_qss(theme)`` is a focused helper for the
   ``assets/themes/redesign.qss`` file (the dashboard role catalog: Card /
   PageTitle / Primary button / Input / Chip / PillToggle / ProgressBar
   / etc.). It does single-brace ``{token}`` substitution — unlike the
   legacy templates, which use Python ``.format_map()`` and so escape literal
   braces as ``{{`` / ``}}``.

Painters that draw outside QSS (donut slices, progress-bar glow, blurred
thumbnail backdrops) read directly from ``PALETTE[theme][key]``.
"""
from pathlib import Path

PALETTE: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0e1016", "card": "#171a23", "card2": "#1e222d",
        "ink": "#eef1f7", "ink_soft": "#8b94a7", "ink_faint": "#5a6173",
        "line": "#262b38", "accent": "#7c6cff", "accent_soft": "#211f3a",
        "green": "#34d399", "amber": "#fbbf24", "red": "#ef4444",
        "pink": "#ff5da2", "blue": "#5b8def", "track": "#222734",
        # top-corner glow for the app background radial (mockup --bg-grad)
        "bg_glow": "#1a1f2e",
        # photo-state borders — FIXED, never re-map (design-system §5a)
        "picked": "#34d399", "skipped": "#ef4444", "compare": "#fb923c",
        "mixed": "#fbd335",  # cluster cover: some picked + some skipped
        "shadow_alpha": "110",
    },
    "light": {
        "bg": "#eef1f6", "card": "#ffffff", "card2": "#f5f7fb",
        "ink": "#1a1f2b", "ink_soft": "#5e6679", "ink_faint": "#9aa1b1",
        "line": "#e6e9f0", "accent": "#6a5cff", "accent_soft": "#eceaff",
        "green": "#16a34a", "amber": "#d97706", "red": "#dc2626",
        "pink": "#e0468a", "blue": "#3b82f6", "track": "#eceef4",
        "bg_glow": "#ffffff",
        "picked": "#16a34a", "skipped": "#dc2626", "compare": "#ea7317",
        "mixed": "#d4a017",
        "shadow_alpha": "28",
    },
}

RADIUS = {"sm": 8, "md": 11, "lg": 14, "xl": 18}


def _redesign_qss_path() -> Path:
    """Locate ``assets/themes/redesign.qss`` from this file's depth."""
    return (
        Path(__file__).resolve().parents[2] / "assets" / "themes" / "redesign.qss"
    )


def build_redesign_qss(theme: str = "dark") -> str:
    """Substitute design-system tokens into the redesign QSS template.

    Single-brace substitution: every ``{key}`` in the template is replaced by
    the matching value from ``PALETTE[theme]`` or ``RADIUS``. Unlike the
    legacy templates, this is NOT a Python format string — literal CSS braces
    are not escaped.

    Raises ``FileNotFoundError`` if ``assets/themes/redesign.qss`` is missing
    (the foundation install must have run).
    """
    tokens = PALETTE[theme]
    qss = _redesign_qss_path().read_text(encoding="utf-8")
    for key, value in tokens.items():
        qss = qss.replace("{" + key + "}", value)
    for key, value in RADIUS.items():
        qss = qss.replace("{radius_" + key + "}", str(value))
    return qss
