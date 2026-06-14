"""spec/58 §4 — the wizard refresh, pinned.

Guards every QSS role the wizard references exists in BOTH themes
(four rendered unstyled for weeks before 2026-06-10).

(The companion "no stale 'Mira' app name" test was retired on
2026-06-14 — the 2026-06-08 Miracraft→Mira fork made Mira the actual
product name, so every wizard string that names the product is now
correct. If a future rename creates a NEW stale name to guard against,
add a fresh test that targets that specific old name.)
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
THEMES = (
    REPO / "assets" / "themes" / "light.qss",
    REPO / "assets" / "themes" / "dark.qss",
)

# Every objectName role the wizard widgets opt into.
WIZARD_ROLES = (
    "WelcomeTitle", "WelcomeSubtitle", "PageHeading",
    "WizardQuestion", "WizardHint",
    "BodyText", "WizardRadio", "WizardRadioHint", "WizardWarning",
)


def test_wizard_qss_roles_exist_in_both_themes():
    missing = []
    for theme in THEMES:
        text = theme.read_text(encoding="utf-8")
        for role in WIZARD_ROLES:
            if f"#{role}" not in text:
                missing.append(f"{theme.name}: #{role}")
    assert not missing, "QSS roles missing:\n" + "\n".join(missing)
