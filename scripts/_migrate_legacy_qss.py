"""spec/92 Stage 4c migration helper (one-shot script, not committed).

Walks dark.qss + light.qss block-by-block. For each rule block whose body
is identical between the two files (i.e. theme-invariant — its only
theme-dependence is via the {token} layer), migrate the block to
redesign.qss with brace unescape (`{{` → `{`, `}}` → `}`), then delete
it from BOTH legacy files.

Run with --dry-run to see counts; without to actually apply.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DARK = ROOT / "assets" / "themes" / "dark.qss"
LIGHT = ROOT / "assets" / "themes" / "light.qss"
REDESIGN = ROOT / "assets" / "themes" / "redesign.qss"

# Marker for the migrated rule section in redesign.qss
MIGRATION_MARKER = "/* ===== spec/92 Stage 4c — migrated from legacy dark.qss / light.qss ===== */"


def _is_real_selector(selectors: str) -> bool:
    """Skip the header docstring's example `{{name}}` and any other
    pseudo-block. A real QSS selector starts with a Qt class name or
    `#Id` or `*` or `.class`."""
    s = selectors.strip()
    if not s:
        return False
    # Strip leading comments
    s = re.sub(r"/\*[\s\S]*?\*/", "", s).strip()
    if not s:
        return False
    # Heuristic: first non-whitespace must look like a selector
    first_char = s[0]
    if first_char == "*":
        return True
    if first_char == "#" and len(s) > 1 and (s[1].isalpha() or s[1] == "_"):
        return True
    # Most legacy selectors start with a Qt class (Q + uppercase letter)
    if first_char == "Q" and len(s) > 1 and s[1].isupper():
        return True
    return False


def _clean_prefix(prefix: str) -> str:
    """Strip orphaned docstring continuation lines from a block's prefix.

    The legacy QSS files open with a file-header docstring that contains
    the example token reference ``{{name}}``. My block parser treats the
    ``{{...}}`` as a rule body, so the SECOND block (the first real rule)
    inherits the docstring tail as its prefix:

        # docstring (fake block "body" is {{name}})
        # blank line
        placeholder is replaced by the palette resolver...   # ← orphan
         * apply time. Literal CSS braces are doubled to escape them.
         */
        QWidget {{ ... }}                                    # ← real

    These orphan lines aren't valid QSS — if they ended up in the
    migrated chunk they'd produce a parser-confusion zone that could
    drop subsequent rules. This helper drops everything up through the
    first ``*/`` we see, then trims leading blanks. If no ``*/`` is
    present, the prefix is left alone (already clean).
    """
    if "*/" not in prefix:
        return prefix
    # Drop everything up to and including the FIRST `*/` — that's where
    # the orphaned docstring tail ends.
    after = prefix.split("*/", 1)[1]
    # Leading whitespace lines after are fine to keep dropping
    return after.lstrip("\n")


def parse_blocks(text: str) -> list[dict]:
    """Yield rule blocks: {prefix, selectors, body, full_span}.

    A block is the smallest sequence comprising:
      - `prefix`: leading comments + blank lines since the previous rule's `}}`
      - `selectors`: the selector list (everything up to `{{`)
      - `body`: `{{ ... }}` (inclusive)

    The block's `full_span` is (start_of_prefix, end_of_body+1_newline).
    """
    blocks: list[dict] = []
    last_end = 0
    L = len(text)
    while True:
        open_idx = text.find("{{", last_end)
        if open_idx < 0:
            break
        close_idx = text.find("}}", open_idx + 2)
        if close_idx < 0:
            break
        # The chunk from last_end to open_idx contains: prefix + selectors.
        chunk = text[last_end:open_idx]
        lines = chunk.split("\n")
        # Walk back from the last line to find where selectors start:
        # the latest line whose preceding line is blank or ends a comment.
        sel_start = 0
        for j in range(len(lines) - 1, -1, -1):
            ln = lines[j].rstrip()
            stripped = ln.strip()
            if not stripped:
                # Blank line - selectors start AFTER this
                sel_start = j + 1
                break
            if stripped.endswith("*/"):
                sel_start = j + 1
                break
        prefix_lines = lines[:sel_start]
        sel_lines = lines[sel_start:]
        prefix = "\n".join(prefix_lines)
        if prefix_lines:
            prefix += "\n"
        sel_text = "\n".join(sel_lines)
        body = text[open_idx:close_idx + 2]
        # Consume one trailing newline so spans align
        end_after_body = close_idx + 2
        if end_after_body < L and text[end_after_body] == "\n":
            end_after_body += 1
        blocks.append({
            "prefix": prefix,
            "selectors": sel_text,
            "body": body,
            "full_span": (last_end, end_after_body),
            "selector_norm": re.sub(r"\s+", " ", sel_text.strip()),
            "body_norm": re.sub(r"\s+", " ", body.strip()),
            "is_real": _is_real_selector(sel_text),
        })
        last_end = end_after_body
    return blocks


def unescape_braces(body: str) -> str:
    """Convert legacy `{{` / `}}` (Python format escapes) to single `{` / `}`
    so the body is ready for redesign.qss's single-brace substitution.

    Token references like `{accent}` use single braces in both styles, so
    only the doubled ones need to change."""
    return body.replace("{{", "{").replace("}}", "}")


def migrate(dry_run: bool) -> None:
    dark_text = DARK.read_text(encoding="utf-8")
    light_text = LIGHT.read_text(encoding="utf-8")
    redesign_text = REDESIGN.read_text(encoding="utf-8")

    dark_blocks = parse_blocks(dark_text)
    light_blocks = parse_blocks(light_text)

    # Index light blocks by normalised selector for lookup
    light_by_sel: dict[str, dict] = {}
    for b in light_blocks:
        if b["is_real"]:
            light_by_sel[b["selector_norm"]] = b

    # Find dark blocks whose selector + body match light
    to_migrate: list[dict] = []
    divergent: list[dict] = []
    dark_only: list[dict] = []
    fake: list[dict] = []
    for b in dark_blocks:
        if not b["is_real"]:
            fake.append(b)
            continue
        light_b = light_by_sel.get(b["selector_norm"])
        if light_b is None:
            dark_only.append(b)
            continue
        if b["body_norm"] == light_b["body_norm"]:
            to_migrate.append((b, light_b))
        else:
            divergent.append((b, light_b))

    print(f"dark blocks: {len(dark_blocks)}; real: {len(dark_blocks) - len(fake)}; fake: {len(fake)}")
    print(f"  to migrate (identical bodies): {len(to_migrate)}")
    print(f"  divergent bodies: {len(divergent)}")
    print(f"  dark-only: {len(dark_only)}")
    print(f"light blocks: {len(light_blocks)}; light-only: {len(light_blocks) - sum(1 for s in (light_by_sel.keys()) if s in {b['selector_norm'] for b in dark_blocks if b['is_real']})}")

    if dry_run:
        print("\n--- dry run ---")
        print(f"Would migrate {len(to_migrate)} blocks into redesign.qss")
        print(f"Would leave {len(divergent)} divergent + {len(dark_only)} dark-only in legacy")
        return

    # Build the migrated chunk for redesign.qss
    migrated_pieces: list[str] = []
    migrated_pieces.append("\n" + MIGRATION_MARKER + "\n")
    migrated_pieces.append(
        "/* The rules below were carried over verbatim from\n"
        " * assets/themes/dark.qss / light.qss as part of the spec/92 §4\n"
        " * Stage 4c migration. Both legacy files had identical bodies for\n"
        " * each of these selectors, so only the {token} substitution layer\n"
        " * needed to be unified — see palette.py::build_redesign_qss + the\n"
        " * resolved-tokens dict passed by theme.py::apply_theme.\n"
        " *\n"
        " * The leading `{{` / `}}` escapes from Python str.format_map have\n"
        " * been converted to single CSS braces. Token references are\n"
        " * unchanged. */\n\n"
    )
    first = True
    for dark_b, light_b in to_migrate:
        # Preserve the prefix comment from light (often more detailed).
        # Special-case the FIRST migrated block: its prefix may contain
        # the file-header docstring's tail (see _clean_prefix). After
        # that, every block's prefix sits cleanly after a `}}` from the
        # previous real block, so no cleanup is needed.
        prefix = light_b["prefix"].strip("\n")
        if first:
            prefix = _clean_prefix(prefix).strip("\n")
            first = False
        if prefix:
            migrated_pieces.append(prefix + "\n")
        migrated_pieces.append(light_b["selectors"].strip() + " ")
        migrated_pieces.append(unescape_braces(light_b["body"]))
        migrated_pieces.append("\n\n")
    migrated_chunk = "".join(migrated_pieces)

    # Build the new dark.qss / light.qss without the migrated blocks
    def rebuild(text: str, blocks: list[dict], drop_spans: set[tuple[int, int]]) -> str:
        out: list[str] = []
        pos = 0
        for b in blocks:
            start, end = b["full_span"]
            if (start, end) in drop_spans:
                # Skip this block (including its prefix). Keep the gap minimal.
                out.append(text[pos:start])
                pos = end
            # else leave it for the next iteration's prefix to consume
        out.append(text[pos:])
        return "".join(out)

    dark_drop = {b["full_span"] for b, _ in to_migrate}
    light_drop = {lb["full_span"] for _, lb in to_migrate}
    new_dark = rebuild(dark_text, dark_blocks, dark_drop)
    new_light = rebuild(light_text, light_blocks, light_drop)

    # Prepend migrated chunk to redesign.qss. The legacy QSS was loaded
    # FIRST in theme.py::apply_theme (legacy then redesign), so the
    # cascade order put legacy rules BEFORE original redesign rules.
    # Putting migrated rules at the top of redesign.qss preserves that
    # order (effective order: remaining-legacy → migrated → original-
    # redesign) — appending at the END instead would put migrated rules
    # AFTER original redesign rules, flipping the cascade winner for
    # same-specificity selectors and producing visible drift.
    # Find the first rule in redesign.qss (the first line that isn't a
    # leading comment or blank) and insert before it.
    redesign_lines = redesign_text.split("\n")
    insert_at = 0
    in_block_comment = False
    for idx, ln in enumerate(redesign_lines):
        stripped = ln.strip()
        if not stripped:
            continue
        if stripped.startswith("/*"):
            in_block_comment = True
            if stripped.endswith("*/"):
                in_block_comment = False
            continue
        if in_block_comment:
            if stripped.endswith("*/"):
                in_block_comment = False
            continue
        # First non-comment, non-blank line — insert above it
        insert_at = idx
        break
    new_redesign = (
        "\n".join(redesign_lines[:insert_at])
        + "\n\n"
        + migrated_chunk
        + "\n"
        + "\n".join(redesign_lines[insert_at:])
    )

    DARK.write_text(new_dark, encoding="utf-8")
    LIGHT.write_text(new_light, encoding="utf-8")
    REDESIGN.write_text(new_redesign, encoding="utf-8")
    print(f"\nMigrated {len(to_migrate)} blocks.")
    print(f"  dark.qss: {len(dark_text)} → {len(new_dark)} bytes")
    print(f"  light.qss: {len(light_text)} → {len(new_light)} bytes")
    print(f"  redesign.qss: {len(redesign_text)} → {len(new_redesign)} bytes")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    migrate(args.dry_run)
