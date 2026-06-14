"""``tr()`` — the translation chokepoint for the new UI (charter §5.8 / spec/05).

Every user-visible string in ``mira/ui/`` passes through here, exactly as the
legacy ``ui.i18n.tr`` did, so the Phase 5 translation pass has clean extraction
targets. Identity fall-through (returns the English source) until a translator is
installed. Self-contained so the new UI never reaches back into the legacy ``ui/``
package.
"""
from __future__ import annotations

from PyQt6.QtCore import QCoreApplication

_DEFAULT_CONTEXT = "Mira"


def tr(source: str, context: str = _DEFAULT_CONTEXT, n: int = -1) -> str:
    """Translate ``source`` in the active locale; returns it unchanged when no
    translator is active (English / pre-install)."""
    return QCoreApplication.translate(context, source, None, n)
