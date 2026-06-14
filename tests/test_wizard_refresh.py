"""spec/58 §4 — the wizard refresh, pinned.

Two regressions guarded: (1) no stale "Mira" app name anywhere in
the wizard sources (the product is Mira; the lowercase package
name and the literal ``%LOCALAPPDATA%/Mira`` data-dir path are not
app-name prose and live outside this tree's strings); (2) every QSS role
the wizard references exists in BOTH themes (four rendered unstyled for
weeks before 2026-06-10).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD_DIR = REPO / "mira" / "ui" / "wizard"
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


def test_no_stale_app_name_in_wizard_sources():
    stale = []
    for path in sorted(WIZARD_DIR.glob("*.py")):
        for n, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"\bMira\b", line):
                stale.append(f"{path.name}:{n}: {line.strip()}")
    assert not stale, "stale 'Mira' app name:\n" + "\n".join(stale)


def test_wizard_qss_roles_exist_in_both_themes():
    missing = []
    for theme in THEMES:
        text = theme.read_text(encoding="utf-8")
        for role in WIZARD_ROLES:
            if f"#{role}" not in text:
                missing.append(f"{theme.name}: #{role}")
    assert not missing, "QSS roles missing:\n" + "\n".join(missing)
