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
        # `line` is the faint hairline between sub-sections; `card_border`
        # is the stronger weight used to make a Card read as a distinct
        # surface (spec/77 §7.2 — must be CLEARLY visible against the
        # dark card background, not a hairline that disappears). The
        # previous #4f566f still read as faint; bumped to #5d6580.
        "line": "#262b38", "card_border": "#5d6580",
        "accent": "#7c6cff", "accent_soft": "#211f3a",
        "green": "#34d399", "amber": "#fbbf24", "red": "#ef4444",
        # `blue` carries the Collect phase identity (events-card pipeline
        # bar + closed-card stat tile + Phases hero chip). Bumped to a
        # cyan-leaning blue (~60° hue separation from `accent`) — the
        # prior #5b8def sat right next to the accent purple #7c6cff and
        # the two read as the same hue at the bar widths the event cards
        # use. The cyan also reads cleanly as "intake/input" alongside
        # Pick=accent / Edit=amber / Export=green.
        "pink": "#ff5da2", "blue": "#22d3ee", "track": "#222734",
        # top-corner glow for the app background radial (mockup --bg-grad)
        "bg_glow": "#1a1f2e",
        # photo-state borders — FIXED, never re-map (design-system §5a)
        "picked": "#34d399", "skipped": "#ef4444", "compare": "#fb923c",
        "mixed": "#fbd335",  # cluster cover: some picked + some skipped
        "shadow_alpha": "110",
    },
    "light": {
        "bg": "#eef1f6", "card": "#ffffff", "card2": "#e9edf4",
        "ink": "#1a1f2b", "ink_soft": "#5e6679", "ink_faint": "#9aa1b1",
        # `line` darkened from #e6e9f0 → #d3d9e3 so hairline borders register
        # against white cards + the grey page in light mode (the old value sat
        # ~1.2:1 on white — invisible). `card2` darkened #f5f7fb → #e9edf4 so
        # input wells + nested tiles separate from white cards instead of
        # blending in (Nelson 2026, light-surface contrast pass).
        "line": "#d3d9e3", "card_border": "#a8aebf",
        "accent": "#6a5cff", "accent_soft": "#eceaff",
        "green": "#16a34a", "amber": "#d97706", "red": "#dc2626",
        # Light-theme companion to dark's #22d3ee — darker so the
        # Collect tokens (bar / stat tile / chip) stay legible on the
        # white card2 background.
        "pink": "#e0468a", "blue": "#0891b2", "track": "#d3d7df",
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


def _glyph_url(name: str) -> str:
    """Absolute, forward-slashed path to ``assets/icons/glyphs/<name>``.
    Qt's QSS ``url(...)`` is happiest with absolute paths in POSIX form,
    so the same string works on Windows + the Nuitka onefile build."""
    return (
        Path(__file__).resolve().parents[2]
        / "assets" / "icons" / "glyphs" / name
    ).as_posix()


def build_redesign_qss(
    theme: str = "dark",
    *,
    tokens: dict[str, str] | None = None,
) -> str:
    """Substitute design-system tokens into the redesign QSS template.

    Single-brace substitution: every ``{key}`` in the template is replaced by
    the matching value from ``tokens`` (defaulting to ``PALETTE[theme]``) or
    ``RADIUS``. Unlike the legacy templates, this is NOT a Python format
    string — literal CSS braces are not escaped.

    Args:
        theme: which palette to use as the default token source AND for
            the ``RADIUS`` lookup (radius keys are theme-independent but the
            arg is retained so the no-tokens path stays backwards-compatible).
        tokens: optional override. When the caller passes the full
            resolved-color dict from ``theme.py::resolve_theme_colors`` (the
            ~60-key vocabulary covering canonical tokens + legacy aliases +
            computed hover/pressed/disabled variants), every legacy alias
            used by a migrated rule resolves correctly. The default
            (``PALETTE[theme]`` alone) only substitutes the canonical tokens
            — sufficient for the pre-Stage-4 redesign.qss state.

    Asset paths exposed: ``{chevron_down_icon_url}`` resolves to the absolute
    POSIX path of ``assets/icons/glyphs/chevron_down.svg`` so the QSS rules
    that draw QComboBox's dropdown chevron find the file from any working
    directory (it ships next to the bundled binaries in the Nuitka onefile).
    The caller may pre-populate this key in ``tokens``; otherwise it is
    substituted as the last step.

    Raises ``FileNotFoundError`` if ``assets/themes/redesign.qss`` is missing
    (the foundation install must have run).
    """
    if tokens is None:
        tokens = PALETTE[theme]
    qss = _redesign_qss_path().read_text(encoding="utf-8")
    for key, value in tokens.items():
        qss = qss.replace("{" + key + "}", value)
    for key, value in RADIUS.items():
        qss = qss.replace("{radius_" + key + "}", str(value))
    if "chevron_down_icon_url" not in tokens:
        qss = qss.replace(
            "{chevron_down_icon_url}", _glyph_url("chevron_down.svg"))
    return qss
