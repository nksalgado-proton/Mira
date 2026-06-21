"""spec/93 vocab rule — user-facing text uses **Collection**, never
"Dynamic Collection" / "DC" / "cross-event collection".

The rule (spec/93 + CLAUDE.md): the nouns are Collection · Recipe · Cut.
Internal model names (``DynamicCollection``, ``dynamic_collection`` /
``saved_filter`` tables) keep their internal identity — no schema
rename — but every user-facing string a dialog / page / window title
ever shows must use **Collection**.

This is a smoke test, not exhaustive: it scans the **specific files**
touched by the Phase 4a cleanup for the call-site shapes that route a
string to the user — :func:`tr`, ``QLabel(...)``, ``setText(...)``,
``setToolTip(...)``, ``setWindowTitle(...)``, ``setPlaceholderText(...)``,
``primary_button(...)``, ``ghost_button(...)``, ``tag(...)``,
``addTab(...)``, etc. — and asserts the banned phrases don't appear.

Docstrings and comments are LEFT ALONE — they're historical commentary
and spec/93 §4 deliberately keeps the internal vocabulary (the model
is still called ``DynamicCollection``; the DDL still says
``dynamic_collection`` / ``saved_filter``).

When a new user-facing call shape arrives that the regex misses, add
it to ``_USER_FACING_CALLS``. When a string legitimately needs a banned
token (rare), append to ``_ALLOWED_OCCURRENCES`` with a one-line
explanation so the test stays honest.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


#: Files swept by Phase 4a — the cross-event Collection / Cuts surface,
#: the share-cuts page (event-scope DC list), and the new recipe dialog.
#: Adding a new user-facing surface? Add it here so future drift gets
#: caught at the test bar, not in a screenshot.
_SCANNED_FILES = (
    "mira/ui/pages/cross_event_cuts_dialog.py",
    "mira/ui/pages/cross_event_dcs_dialog.py",
    "mira/ui/pages/new_cross_event_dc_dialog.py",
    "mira/ui/pages/new_recipe_dialog.py",
    "mira/ui/pages/share_cuts_page.py",
    "mira/ui/shared/dc_detail_page.py",
    "mira/ui/pages/_cross_event_band.py",
)


#: Call-site shapes that route a string to the user. Each entry is a
#: regex matching the call name; the test then captures every string
#: literal inside the call's parenthesised arguments and scans them.
#: Closing `)` is approximated by balancing — see :func:`_extract_args`.
_USER_FACING_CALLS = (
    "tr",
    "QLabel",
    "QPushButton",
    "QMessageBox",
    "primary_button",
    "ghost_button",
    "secondary_button",
    "tag",
    "search_field",
    "line_input",
    "setText",
    "setToolTip",
    "setWindowTitle",
    "setPlaceholderText",
    "setStatusTip",
    "setWhatsThis",
    "addTab",
    "addAction",
    "setLabelText",
    "setInformativeText",
)


#: The forbidden phrases — anything matching these in a user-facing
#: string literal triggers the test.
_FORBIDDEN_PATTERNS = (
    (r"\bDynamic\s+Collection", "Dynamic Collection (use Collection)"),
    (r"\bdynamic\s+collection", "dynamic collection (use Collection)"),
    (r"\bcross-event\s+collection",
     "cross-event collection (use Collection)"),
    (r"\bCross-event\s+collection",
     "Cross-event collection (use Collection)"),
)


#: Whitelisted occurrences. Keep TINY; require a rationale per line.
_ALLOWED_OCCURRENCES: tuple[tuple[str, str, str], ...] = (
    # (relpath, exact substring, rationale)
)


def _strip_strings_and_comments(text: str) -> str:
    """Replace string contents + comments with whitespace so call-name
    matches don't fire inside docstrings or comments. The result has
    the same byte length so line numbers + offsets stay correct."""
    out = list(text)
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            j = text.find("\n", i)
            if j == -1:
                j = n
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if ch in ("'", '"'):
            quote = ch
            triple = text[i:i + 3] in ('"""', "'''")
            close = quote * 3 if triple else quote
            start = i + len(close)
            j = text.find(close, start)
            while j != -1 and text[j - 1] == "\\" and not triple:
                j = text.find(close, j + 1)
            if j == -1:
                j = n
            for k in range(i, j + len(close)):
                if k < n and out[k] != "\n":
                    out[k] = " "
            i = j + len(close)
            continue
        i += 1
    return "".join(out)


def _balanced_args(text: str, open_idx: int) -> tuple[int, int]:
    """Given ``text`` and the index of an opening ``(``, return
    ``(start, end)`` where ``start = open_idx + 1`` and ``end`` is
    one past the matching ``)``. Tracks nested parens but does NOT
    re-enter strings (callers strip them first via
    :func:`_strip_strings_and_comments`)."""
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return (open_idx + 1, i)
        i += 1
    return (open_idx + 1, n)


def _string_literals_in_range(text: str, start: int,
                              end: int) -> list[tuple[int, str]]:
    """All string literals that begin inside ``[start, end)`` of
    ``text``. Returns ``(line_number, literal)`` pairs."""
    pattern = re.compile(
        r'"""(?:[^"\\]|\\.|"(?!""))*"""'
        r"|'''(?:[^'\\]|\\.|'(?!''))*'''"
        r'|"(?:[^"\\\n]|\\.)*"'
        r"|'(?:[^'\\\n]|\\.)*'",
        re.DOTALL,
    )
    out: list[tuple[int, str]] = []
    for match in pattern.finditer(text, pos=start, endpos=end):
        line = text.count("\n", 0, match.start()) + 1
        out.append((line, match.group(0)))
    return out


def _scan_user_facing_strings(path: Path) -> list[tuple[int, str]]:
    """Return every string literal that sits inside a user-facing
    call's parenthesised arguments. Docstrings + comments are
    excluded because :func:`_strip_strings_and_comments` blanks them
    out before the call-name regex runs (the actual literals are
    extracted from the ORIGINAL ``text``, preserving the content)."""
    text = path.read_text(encoding="utf-8")
    scaffold = _strip_strings_and_comments(text)
    call_pattern = re.compile(
        r"\b(" + "|".join(re.escape(c) for c in _USER_FACING_CALLS)
        + r")\s*\(",
    )
    out: list[tuple[int, str]] = []
    seen: set[tuple[int, int]] = set()
    for match in call_pattern.finditer(scaffold):
        open_idx = match.end() - 1
        start, end = _balanced_args(scaffold, open_idx)
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        out.extend(_string_literals_in_range(text, start, end))
    return out


@pytest.mark.parametrize("relpath", _SCANNED_FILES)
def test_no_dynamic_collection_in_user_facing_strings(relpath: str) -> None:
    """No user-facing string in the scanned files contains a banned
    phrase. Docstrings + comments are ignored — spec/93 vocab rule is
    about UI strings, not historical model commentary."""
    path = REPO_ROOT / relpath
    assert path.exists(), f"sweep target gone: {relpath}"
    failures: list[str] = []
    for line, literal in _scan_user_facing_strings(path):
        for pattern, label in _FORBIDDEN_PATTERNS:
            if not re.search(pattern, literal):
                continue
            allowed = any(
                f == relpath and frag in literal
                for f, frag, _ in _ALLOWED_OCCURRENCES
            )
            if allowed:
                continue
            failures.append(
                f"{relpath}:{line} — {label}\n    {literal}")
    assert not failures, (
        "Vocabulary regression — spec/93 hard rule (CLAUDE.md):\n  "
        + "\n  ".join(failures)
    )
