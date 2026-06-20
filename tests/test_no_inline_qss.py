"""spec/92 §7 — the inline-style guard runs in the test suite.

Fails if any ``mira/ui`` module gains a ``setStyleSheet(`` call beyond the
recorded baseline (``scripts/qss_guard_baseline.json``). This is the
regression net for the widget-consolidation migration: the count can only
shrink. See ``scripts/qss_guard.py`` for details and the
``# pragma: no-qss`` escape hatch for reviewed exceptions.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

import qss_guard  # noqa: E402


def test_no_new_inline_setstylesheet():
    ok, messages = qss_guard.check()
    assert ok, (
        "Inline styling grew past the spec/92 baseline. Move it into a QSS "
        "role (assets/themes/redesign.qss), or mark a reviewed exception with "
        "`# pragma: no-qss`. Then `python scripts/qss_guard.py --update-baseline`.\n"
        + "\n".join(f"  - {m}" for m in messages)
    )
