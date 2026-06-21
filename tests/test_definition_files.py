"""Tests for ``core.definition_files`` — spec/93 §4 on-disk model."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.definition_files import (
    JSON_SCHEMA_VERSION,
    KIND_COLLECTION,
    KIND_RECIPE,
    MAX_FILENAME_LENGTH,
    DefinitionFile,
    DefinitionParseError,
    DefinitionRef,
    display_name_from_path,
    file_path_for,
    new_definition_id,
    read_definition,
    slugify_filename,
    to_ref,
    write_definition,
)


# ── slugify_filename ──────────────────────────────────────────────


def test_slugify_preserves_case_and_unicode():
    """Display names keep their case and any Unicode characters."""
    assert slugify_filename("Best Wildlife") == "Best Wildlife"
    assert slugify_filename("Café 2024") == "Café 2024"


def test_slugify_replaces_filesystem_illegal_chars():
    """``/ \\ < > : | " * ?`` → underscores; nothing else touched."""
    assert "_" in slugify_filename("a/b")
    assert "/" not in slugify_filename("a/b")
    assert "\\" not in slugify_filename("a\\b")
    assert "*" not in slugify_filename("a*b")
    assert "?" not in slugify_filename("a?b")


def test_slugify_strips_trailing_dots_and_spaces():
    """Windows refuses ``foo.`` / ``foo `` — strip them."""
    assert slugify_filename("name. ") == "name"
    assert slugify_filename("name...") == "name"


def test_slugify_caps_at_max_length():
    """Long names cap at :data:`MAX_FILENAME_LENGTH` to stay under
    MAX_PATH on Windows."""
    long_name = "x" * (MAX_FILENAME_LENGTH + 50)
    out = slugify_filename(long_name)
    assert len(out) <= MAX_FILENAME_LENGTH


def test_slugify_empty_input_becomes_unnamed():
    """Empty / whitespace-only / illegal-only inputs don't produce a
    zero-length filename."""
    assert slugify_filename("") == "unnamed"
    assert slugify_filename("   ") == "unnamed"


# ── file_path_for ─────────────────────────────────────────────────


def test_file_path_for_appends_json(tmp_path):
    """The on-disk path is ``<folder>/<slug>.json``."""
    p = file_path_for(tmp_path, "Best Wildlife")
    assert p == tmp_path / "Best Wildlife.json"


# ── new_definition_id ─────────────────────────────────────────────


def test_new_definition_id_is_unique():
    """IDs are unique UUID hex strings."""
    a = new_definition_id()
    b = new_definition_id()
    assert a != b
    assert len(a) == 32 and all(c in "0123456789abcdef" for c in a)


# ── write + read round-trip ───────────────────────────────────────


def test_write_then_read_round_trip(tmp_path):
    """A definition written then read returns the same id, kind, and
    payload."""
    df = DefinitionFile(
        id=new_definition_id(),
        name="Best Wildlife",
        kind=KIND_COLLECTION,
        payload={"expr": [["+", "exported"]], "filters": {"styles": []}},
        path=tmp_path / "Best Wildlife.json",
    )
    write_definition(df)
    back = read_definition(df.path)
    assert back.id == df.id
    assert back.kind == df.kind
    assert back.name == df.name
    assert back.payload == df.payload


def test_write_is_atomic(tmp_path):
    """The tmp file from the write-then-rename is gone after the call."""
    df = DefinitionFile(
        id=new_definition_id(), name="x", kind=KIND_RECIPE,
        payload={}, path=tmp_path / "x.json",
    )
    write_definition(df)
    assert not df.path.with_suffix(".json.tmp").exists()


def test_write_requires_path():
    """``write_definition`` insists on a path — callers go through
    :func:`file_path_for` first."""
    df = DefinitionFile(
        id=new_definition_id(), name="x", kind=KIND_RECIPE, payload={},
    )
    with pytest.raises(ValueError):
        write_definition(df)


def test_write_requires_id(tmp_path):
    """The id is load-bearing; refuse to write without one. Library
    layer is responsible for backfilling before calling write."""
    df = DefinitionFile(
        id="", name="x", kind=KIND_RECIPE, payload={},
        path=tmp_path / "x.json",
    )
    with pytest.raises(ValueError):
        write_definition(df)


def test_write_rejects_unknown_kind(tmp_path):
    df = DefinitionFile(
        id=new_definition_id(), name="x", kind="bogus", payload={},
        path=tmp_path / "x.json",
    )
    with pytest.raises(ValueError):
        write_definition(df)


# ── read tolerances ───────────────────────────────────────────────


def test_read_tolerates_missing_id(tmp_path):
    """A hand-authored file without ``id`` parses; the caller can
    backfill on next save (§4)."""
    path = tmp_path / "hand.json"
    path.write_text(json.dumps({
        "kind": KIND_COLLECTION,
        "payload": {"expr": [["+", "exported"]]},
    }), encoding="utf-8")
    df = read_definition(path)
    assert df.id == ""
    assert df.kind == KIND_COLLECTION


def test_read_lifts_top_level_payload_keys(tmp_path):
    """A hand-authored file with composition keys at the top level
    (no ``payload`` wrapper) still loads — the reader lifts them."""
    path = tmp_path / "flat.json"
    path.write_text(json.dumps({
        "id": "abc",
        "kind": KIND_COLLECTION,
        "expr": [["+", "exported"]],
        "filters": {"styles": ["macro"]},
    }), encoding="utf-8")
    df = read_definition(path)
    assert df.payload["expr"] == [["+", "exported"]]
    assert df.payload["filters"]["styles"] == ["macro"]


def test_read_uses_filename_as_display_name(tmp_path):
    """The in-file ``name`` is a HINT; the filename is the truth (an
    OS rename takes — spec/93 §4 last paragraph)."""
    path = tmp_path / "actual filename.json"
    path.write_text(json.dumps({
        "id": "x",
        "kind": KIND_RECIPE,
        "name": "obsolete in-file name",
        "payload": {},
    }), encoding="utf-8")
    df = read_definition(path)
    assert df.name == "actual filename"


def test_read_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DefinitionParseError):
        read_definition(path)


def test_read_rejects_unknown_kind(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "id": "x", "kind": "bogus", "payload": {},
    }), encoding="utf-8")
    with pytest.raises(DefinitionParseError):
        read_definition(path)


def test_read_rejects_non_object_payload(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(DefinitionParseError):
        read_definition(path)


# ── to_ref ────────────────────────────────────────────────────────


def test_to_ref_carries_id_name_kind(tmp_path):
    df = DefinitionFile(
        id="abc", name="Best Wildlife", kind=KIND_COLLECTION,
        payload={}, path=tmp_path / "Best Wildlife.json",
    )
    r = to_ref(df)
    assert isinstance(r, DefinitionRef)
    assert r.id == "abc"
    assert r.name == "Best Wildlife"
    assert r.kind == KIND_COLLECTION
    # ``as_jsonable`` shape — what gets stored in a Cut's frozen
    # source_link / a referrer operand.
    assert r.as_jsonable() == {
        "id": "abc", "name": "Best Wildlife", "kind": KIND_COLLECTION}


# ── schema_version round-trip ─────────────────────────────────────


def test_schema_version_round_trips(tmp_path):
    df = DefinitionFile(
        id="x", name="x", kind=KIND_RECIPE, payload={},
        path=tmp_path / "x.json",
    )
    write_definition(df)
    blob = json.loads(df.path.read_text(encoding="utf-8"))
    assert blob["schema_version"] == JSON_SCHEMA_VERSION
