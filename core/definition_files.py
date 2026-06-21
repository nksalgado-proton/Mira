"""On-disk model for spec/93 §4 Collection / Recipe JSON files.

Each definition (a Collection or a Recipe — spec/93's
"ingredient / recipe") is **one JSON file** under
``<library_root>/Collections/`` or ``<library_root>/Recipes/`` (the
two folder trees the user organises in their own file manager).
Identity is a stable internal **id** (a UUID); the filename is the
human-readable display name; references between definitions are
``{id, name}`` pairs resolved by id with the name as a fallback for
hand-authored files (§4).

The shape on disk is small and forgiving:

    {
      "schema_version": 1,
      "id":   "<uuid hex>",
      "kind": "collection" | "recipe",
      "name": "<display name>",            // a HINT — the filename rules
      "payload": { ... }                   // the composition body
    }

A hand-authored file may omit ``id`` (the library will backfill one
on next save) or carry the payload keys at the top level (we lift
them under ``payload`` for the in-memory shape). The library never
trusts the in-file ``name``: the FILENAME is the load-bearing
display name, so an OS rename "takes" automatically (§4 last
paragraph).

Atomic write-then-rename (invariant #6). Pure logic + filesystem —
no Qt imports.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


#: Closed enum for the two definition kinds.
KIND_COLLECTION = "collection"
KIND_RECIPE = "recipe"
KINDS = frozenset({KIND_COLLECTION, KIND_RECIPE})

#: On-disk shape version. Bump together with a reader migration when
#: the JSON shape changes; we read older versions tolerantly.
JSON_SCHEMA_VERSION = 1

#: Filesystem-illegal characters on the strictest target (Windows
#: NTFS). We replace these in the filename slug with underscores so
#: the user's display name doesn't crash the save.
_ILLEGAL_FILENAME_CHARS = '/\\<>:|"*?\x00'

#: Cap the filename at a comfortable length so unusually long display
#: names don't run into MAX_PATH on Windows.
MAX_FILENAME_LENGTH = 120


class DefinitionParseError(Exception):
    """A file under ``Collections/`` or ``Recipes/`` couldn't be read
    as a definition. Carries the offending path + reason so the
    library scan can log and skip without crashing."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class DefinitionRef:
    """A typed reference to a stored definition (spec/93 §4).

    Carries ``id`` + ``name`` + ``kind`` so a referrer (a nested
    Collection operand, a Cut's source link) can resolve by id with
    a name fallback. Stored verbatim in JSON payloads — see
    ``operand_to_jsonable`` in the resolver layer.
    """
    id: str
    name: str
    kind: str

    def as_jsonable(self) -> Dict[str, str]:
        return {"id": self.id, "name": self.name, "kind": self.kind}


@dataclass
class DefinitionFile:
    """In-memory view of one definition file on disk.

    ``id`` is the load-bearing key (a UUID hex). ``name`` is the
    display name — derived from the filename, so an OS rename
    propagates here on the next scan. ``payload`` is the composition
    body (Collection: ``expr`` + ``filters``; Recipe: the full
    spec/90 §5.1 ``composition_json``). ``path`` is where the file
    lives on disk; ``schema_version`` is the on-disk shape version.
    """
    id: str
    name: str
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    path: Optional[Path] = None
    schema_version: int = JSON_SCHEMA_VERSION


def new_definition_id() -> str:
    """Generate a fresh definition id (a UUID hex). Centralised so
    tests can monkeypatch the source."""
    return uuid.uuid4().hex


def slugify_filename(display_name: str) -> str:
    """Produce a filesystem-safe filename stem for ``display_name``.

    Behaviour (Nelson 2026-06-21 default): preserve Unicode + case,
    replace only filesystem-illegal characters with underscores,
    strip trailing dots and spaces (Windows refuses them), trim to
    :data:`MAX_FILENAME_LENGTH` chars. An empty result falls back to
    ``"unnamed"`` so we never produce a zero-length filename.
    """
    if not isinstance(display_name, str):
        display_name = str(display_name)
    s = display_name.strip()
    for ch in _ILLEGAL_FILENAME_CHARS:
        s = s.replace(ch, "_")
    s = s.rstrip(". ")
    if len(s) > MAX_FILENAME_LENGTH:
        s = s[:MAX_FILENAME_LENGTH].rstrip(". ")
    if not s:
        s = "unnamed"
    return s


def display_name_from_path(path: Path) -> str:
    """Display name == filename stem (the part before ``.json``).
    Centralised so a future format change (e.g. ``.json5``) updates
    here alone."""
    return path.stem


def file_path_for(folder: Path, display_name: str) -> Path:
    """Build the on-disk path for a definition with the given display
    name, inside ``folder`` (typically a subfolder of Collections/
    or Recipes/)."""
    return folder / (slugify_filename(display_name) + ".json")


def read_definition(path: Path) -> DefinitionFile:
    """Read one definition file from disk.

    Raises :class:`DefinitionParseError` on malformed JSON, missing
    ``kind``, or shape mismatches the reader can't recover from.
    Tolerates:

    * Missing ``id`` — returns a :class:`DefinitionFile` with
      ``id=""`` so the caller (library scan) can backfill a UUID
      and write the file back (§4 hand-authoring path).
    * Top-level payload keys vs. nested ``"payload"`` — both are
      accepted so a hand-authored file doesn't have to learn the
      wrapper.

    Display name is derived from the FILENAME, never from the
    in-file ``name`` field — that's just a hint for hand-editing
    (§4 "filename is just the display name").
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DefinitionParseError(path, f"unreadable: {exc}") from exc
    try:
        blob = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DefinitionParseError(path, f"malformed JSON: {exc}") from exc
    if not isinstance(blob, dict):
        raise DefinitionParseError(path, "payload is not an object")

    raw_id = blob.get("id", "")
    file_id = raw_id if isinstance(raw_id, str) else ""
    kind = blob.get("kind")
    if kind not in KINDS:
        raise DefinitionParseError(
            path, f"missing or invalid kind: {kind!r}")
    schema_version = blob.get("schema_version", JSON_SCHEMA_VERSION)
    try:
        schema_version = int(schema_version)
    except (TypeError, ValueError):
        schema_version = JSON_SCHEMA_VERSION

    payload = blob.get("payload")
    if not isinstance(payload, dict):
        # Hand-authored file with composition keys at the top level —
        # lift them under payload so callers always see one shape.
        payload = {
            k: v for k, v in blob.items()
            if k not in {"id", "kind", "name", "schema_version", "payload"}
        }

    return DefinitionFile(
        id=file_id,
        name=display_name_from_path(path),
        kind=kind,
        payload=payload,
        path=path,
        schema_version=schema_version,
    )


def write_definition(df: DefinitionFile) -> None:
    """Atomic write-then-rename (invariant #6) of ``df.path``.

    Preserves ``df.id`` — that's the load-bearing key. The in-file
    ``name`` is written as a HINT matching the current filename so
    hand-editors see something readable; on next read the display
    name is recomputed from the filename, which is the truth (§4
    last paragraph). The caller must ensure ``df.path`` is set
    (typically via :func:`file_path_for`) before calling.
    """
    if df.path is None:
        raise ValueError("write_definition: df.path is unset")
    if df.kind not in KINDS:
        raise ValueError(f"write_definition: invalid kind {df.kind!r}")
    if not df.id:
        raise ValueError("write_definition: df.id is unset")
    df.path.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        "schema_version": df.schema_version,
        "id": df.id,
        "kind": df.kind,
        "name": df.name,
        "payload": df.payload,
    }
    data = json.dumps(blob, indent=2, ensure_ascii=False).encode("utf-8")
    tmp = df.path.with_suffix(df.path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    os.replace(str(tmp), str(df.path))


def to_ref(df: DefinitionFile) -> DefinitionRef:
    """Build a typed reference from a loaded file."""
    return DefinitionRef(id=df.id, name=df.name, kind=df.kind)


__all__ = [
    "JSON_SCHEMA_VERSION",
    "KINDS",
    "KIND_COLLECTION",
    "KIND_RECIPE",
    "MAX_FILENAME_LENGTH",
    "DefinitionFile",
    "DefinitionParseError",
    "DefinitionRef",
    "display_name_from_path",
    "file_path_for",
    "new_definition_id",
    "read_definition",
    "slugify_filename",
    "to_ref",
    "write_definition",
]
