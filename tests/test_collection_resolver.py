"""spec/81 §2 — the pure DC resolution engine (no Qt, no DB).

Drives ``core.collection_resolver`` with injected accessors over a tiny
in-memory universe: set algebra (+/−/&) left-to-right, nested-DC grouping,
operands resolving recursively or terminally, filter application, and cycle
safety (both the resolution-time guard and the cheap write-seam ``reaches``).
"""
from __future__ import annotations

import pytest

from core import collection_resolver as cr


# A toy universe: exported = {a,b,c,d}, two cuts + two DCs.
UNIVERSE = {"exported": {"a", "b", "c", "d"}}
CUT_MEMBERS = {"cut-x": {"a"}, "cut-y": {"d"}}
DC_TABLE = {}   # filled per-test


def _base(token):
    return set(UNIVERSE.get(token, set()))


def _dc_by_ref(ref):
    dc = DC_TABLE.get(ref.get("id"))
    if dc is None:
        return None
    return cr.DCExpr(id=dc["id"], expr=dc["expr"], filters=dc.get("filters", {}))


def _cut_members(ref):
    return set(CUT_MEMBERS.get(ref.get("id"), set()))


def _apply_filters(keys, filters):
    """Order keys alphabetically; a "styles" filter keeps only listed keys
    (the toy stand-in for classification narrowing)."""
    keys = set(keys)
    keep = filters.get("only")
    if keep is not None:
        keys = {k for k in keys if k in set(keep)}
    return sorted(keys)


def _resolve(expr, filters=None):
    return cr.resolve(
        expr, filters or {},
        base_universe=_base, dc_by_ref=_dc_by_ref,
        cut_members=_cut_members, apply_filters=_apply_filters)


def setup_function(_):
    DC_TABLE.clear()


def test_union_difference_intersection_left_to_right():
    # exported − cut-x ({a}) + cut-y ({d}) → {b,c,d}
    assert _resolve([["+", "exported"],
                     ["-", {"kind": "cut", "id": "cut-x"}],
                     ["+", {"kind": "cut", "id": "cut-y"}]]) == ["b", "c", "d"]
    # exported & cut-x → {a}
    assert _resolve([["+", "exported"],
                     ["&", {"kind": "cut", "id": "cut-x"}]]) == ["a"]


def test_nested_dc_operand_is_grouping():
    DC_TABLE["sub"] = {"id": "sub", "expr": [["+", "exported"],
                                             ["-", {"kind": "cut", "id": "cut-x"}]]}
    # outer = sub  →  exported − cut-x = {b,c,d}
    assert _resolve([["+", {"kind": "dc", "id": "sub"}]]) == ["b", "c", "d"]


def test_nested_dc_own_filters_apply_before_composing():
    DC_TABLE["m"] = {"id": "m", "expr": [["+", "exported"]],
                     "filters": {"only": ["a", "b"]}}
    # the nested DC narrows to {a,b} before it unions upward
    assert _resolve([["+", {"kind": "dc", "id": "m"}]]) == ["a", "b"]


def test_top_level_filters_apply():
    assert _resolve([["+", "exported"]], {"only": ["b", "c"]}) == ["b", "c"]


def test_unknown_operand_and_bad_operator():
    # a missing DC ref contributes nothing (graceful shrink)
    assert _resolve([["+", {"kind": "dc", "id": "gone"}]]) == []
    with pytest.raises(ValueError):
        _resolve([["?", "exported"]])


def test_resolution_time_cycle_guard():
    # a → b → a must raise, not loop
    DC_TABLE["a"] = {"id": "a", "expr": [["+", {"kind": "dc", "id": "b"}]]}
    DC_TABLE["b"] = {"id": "b", "expr": [["+", {"kind": "dc", "id": "a"}]]}
    with pytest.raises(cr.CycleError):
        _resolve([["+", {"kind": "dc", "id": "a"}]])


def test_memoised_within_one_pass():
    # a referenced twice resolves once (no error, correct union)
    DC_TABLE["leaf"] = {"id": "leaf", "expr": [["+", "exported"]],
                        "filters": {"only": ["a"]}}
    out = _resolve([["+", {"kind": "dc", "id": "leaf"}],
                    ["+", {"kind": "dc", "id": "leaf"}]])
    assert out == ["a"]


# --------------------------------------------------------------------------- #
# reaches — the cheap write-seam cycle probe
# --------------------------------------------------------------------------- #


def test_reaches_self_and_transitive():
    exprs = {
        "a": [["+", {"kind": "dc", "id": "b"}]],
        "b": [["+", {"kind": "dc", "id": "a"}]],
    }
    # writing a's expr that reaches a (via b) → cycle
    assert cr.reaches("a", exprs["a"], dc_expr_by_id=exprs.get) is True
    # a direct self-reference
    assert cr.reaches("z", [["+", {"kind": "dc", "id": "z"}]],
                      dc_expr_by_id=lambda i: None) is True


def test_reaches_terminal_operands_are_safe():
    # base token + a cut ref are terminal — never a cycle
    assert cr.reaches("a", [["+", "exported"],
                            ["-", {"kind": "cut", "id": "a"}]],
                      dc_expr_by_id=lambda i: None) is False
