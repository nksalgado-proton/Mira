"""spec/58 §4 — the wizard refresh, pinned.

Guards every QSS role the wizard references exists in the canonical
role catalog (four rendered unstyled for weeks before 2026-06-10).

spec/92 §4 Stage 4c collapsed both themes onto a single
``assets/themes/redesign.qss`` (token-substituted per theme by
``build_redesign_qss(theme, tokens=...)``). Previously this test
parameterised over light.qss + dark.qss; now there's one canonical
stylesheet to check.

(The companion "no stale 'Mira' app name" test was retired on
2026-06-14 — the 2026-06-08 Miracraft→Mira fork made Mira the actual
product name, so every wizard string that names the product is now
correct. If a future rename creates a NEW stale name to guard against,
add a fresh test that targets that specific old name.)
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REDESIGN_QSS = REPO / "assets" / "themes" / "redesign.qss"

# Every objectName role the wizard widgets opt into.
WIZARD_ROLES = (
    "WelcomeTitle", "WelcomeSubtitle", "PageHeading",
    "WizardQuestion", "WizardHint",
    "BodyText", "WizardRadio", "WizardRadioHint", "WizardWarning",
)


def test_wizard_qss_roles_exist_in_redesign_qss():
    text = REDESIGN_QSS.read_text(encoding="utf-8")
    missing = [role for role in WIZARD_ROLES if f"#{role}" not in text]
    assert not missing, "QSS roles missing from redesign.qss:\n" + "\n".join(
        f"  #{r}" for r in missing
    )
