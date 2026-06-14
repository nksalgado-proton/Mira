"""Per-photo Process decisions — sidecar-style persistence in the
ingest journal.

A "decision" is the user's manual tweak for one photo: which crop
rectangle they dragged, which slider values they set, recorded so
the next time the bucket opens the photo comes back exactly as
they left it. Stored under the ``process_decisions`` key of the
existing ingest journal (one file per bucket, atomic-write
already handled by :mod:`core.ingest_session`) so no new persistence
layer is needed.

Two scopes:

* **Per-bucket** — ``process_aspect_label``: the AspectRatioCombo
  choice. Saved on combo change, restored on bucket open; falls
  back to ``settings.preferred_aspect_ratio`` for a fresh bucket
  with no journaled choice.

* **Per-photo** — ``process_decisions[<filename>]``:

  .. code-block:: json

      {
          "params": {
              "exposure": 0.4, "contrast": 12.0,
              "highlights": -15.0, "shadows": 8.0,
              "whites": 0.0, "blacks": 0.0,
              "sharpness": 25.0, "saturation": 0.0,
              "vibrance": 10.0
          },
          "crop_norm": [0.0, 0.125, 1.0, 0.75],
          "rotation": 90
      }

  Either key may be missing — the journal only records what the
  user touched. A photo with no entry → fresh AUTO + centred crop
  on every load (same behaviour as before this module existed).

Pure-logic + Qt-free; the page reads/writes via these helpers and
:func:`core.ingest_session.save_ingest_journal` persists atomically.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Iterable, Optional

from core.photo_render import Params

log = logging.getLogger(__name__)


# Top-level journal keys reserved for Process. Both optional — the
# journal is forward-compatible with the older shape that doesn't
# carry them (see core.ingest_session._empty_journal).
PROCESS_DECISIONS_KEY = "process_decisions"
PROCESS_ASPECT_LABEL_KEY = "process_aspect_label"
# docs/25 §9 — Process has no Keep/Compare/Discard. The set of
# filenames already materialised by a Process Export drives the
# EXPORTED chip + the phase rollup (in place of a Kept gate).
# Top-level, optional; forward-compatible with older journals.
PROCESS_EXPORTED_KEY = "process_exported"


# Sub-keys inside one photo's decision entry. Kept as module-level
# constants so tests can pin the on-disk shape without re-deriving
# it from string literals scattered across the codebase.
_KEY_PARAMS = "params"
_KEY_CROP = "crop_norm"
# Task #117 — free-angle tilt for the crop (degrees, clockwise).
# Optional just like ``crop_norm`` / ``params``; absence ⇒ 0.0
# (no tilt). Forward-compat: older journals that don't carry it
# load unchanged.
_KEY_CROP_ANGLE = "crop_angle"
# docs/25 §4 — rotation 90° (clockwise), one of {0, 90, 180, 270}.
# Optional like the others; absence ⇒ 0 (no rotation). Forward-compat:
# older journals without it load unchanged.
_KEY_ROTATION = "rotation"


# ── Per-bucket aspect label ─────────────────────────────────────


def get_process_aspect_label(journal: dict) -> Optional[str]:
    """Return the bucket's saved aspect label, or ``None`` if none
    was journaled yet (callers fall back to the user's settings
    default)."""
    value = journal.get(PROCESS_ASPECT_LABEL_KEY)
    return str(value) if isinstance(value, str) and value else None


def set_process_aspect_label(journal: dict, label: str) -> None:
    """Update the bucket's aspect label. Caller persists with
    ``save_ingest_journal``; this only mutates the in-memory dict so
    the page can batch a save after the user's drag settles."""
    journal[PROCESS_ASPECT_LABEL_KEY] = str(label)


# ── Per-photo decisions ─────────────────────────────────────────


def _decisions_dict(journal: dict) -> dict:
    """Lazy-create the per-photo decisions sub-dict. The journal's
    skeleton omits it (older journals don't carry it); calling this
    once before reading/writing keeps the rest of the API simple."""
    d = journal.get(PROCESS_DECISIONS_KEY)
    if not isinstance(d, dict):
        d = {}
        journal[PROCESS_DECISIONS_KEY] = d
    return d


def get_process_decision(
    journal: dict, name: str,
) -> Optional[dict]:
    """Return the raw decision dict for ``name`` or ``None``. The dict
    may contain ``"params"`` and/or ``"crop_norm"`` (either, both, or
    neither — callers check independently)."""
    d = journal.get(PROCESS_DECISIONS_KEY)
    if not isinstance(d, dict):
        return None
    entry = d.get(name)
    return entry if isinstance(entry, dict) else None


def get_process_params(journal: dict, name: str) -> Optional[Params]:
    """Pull a journaled :class:`Params` for ``name`` and rehydrate
    it. Returns ``None`` if no decision is recorded or the recorded
    payload is malformed (defensive — a hand-edited journal must not
    crash the page)."""
    entry = get_process_decision(journal, name)
    if entry is None:
        return None
    payload = entry.get(_KEY_PARAMS)
    if not isinstance(payload, dict):
        return None
    # Use only the fields that match Params's signature. Extra keys
    # are silently dropped, missing keys fall back to Params's own
    # field defaults (zero, the identity).
    fields = {
        "exposure", "contrast", "highlights", "shadows",
        "whites", "blacks", "sharpness", "saturation", "vibrance",
    }
    try:
        kwargs = {
            k: float(v) for k, v in payload.items()
            if k in fields and isinstance(v, (int, float))
        }
        return Params(**kwargs)
    except (TypeError, ValueError):
        return None


def get_process_look(journal: dict, name: str) -> Optional[dict]:
    """Pull the journaled Look CHOICE for ``name`` (spec/54 §6):
    ``{"look": str, "style": Optional[str], "creative_filter":
    Optional[str]}``. Returns ``None`` when no choice is recorded
    (legacy journals / untouched photos) — callers fall back to fresh
    AUTO, which under the routed engine IS Natural."""
    entry = get_process_decision(journal, name)
    if entry is None:
        return None
    look = entry.get("look")
    if not isinstance(look, str) or not look:
        return None
    style = entry.get("style")
    creative_filter = entry.get("creative_filter")
    # Nelson 2026-06-13 Look Strength — the journal entry may carry
    # ``strength`` (default 1.0 = legacy semantics). Clamped on the
    # read so a malformed entry never crashes the engine.
    raw_strength = entry.get("strength", 1.0)
    try:
        strength = max(0.0, min(2.0, float(raw_strength)))
    except (TypeError, ValueError):
        strength = 1.0
    return {
        "look": look,
        "style": style if isinstance(style, str) and style else None,
        "creative_filter": (
            creative_filter
            if isinstance(creative_filter, str) and creative_filter
            else None),
        "strength": strength,
    }


def get_process_crop(
    journal: dict, name: str,
) -> Optional[tuple[float, float, float, float]]:
    """Pull a journaled crop_norm rect for ``name``. Returns
    ``None`` when no rect is recorded; clamps malformed values to
    sensible bounds rather than raising."""
    entry = get_process_decision(journal, name)
    if entry is None:
        return None
    payload = entry.get(_KEY_CROP)
    if not isinstance(payload, (list, tuple)) or len(payload) != 4:
        return None
    try:
        rect = tuple(float(v) for v in payload)
    except (TypeError, ValueError):
        return None
    x, y, w, h = rect
    # Defensive clamp — a hand-edited journal shouldn't be able to
    # crash the overlay later.
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.0, min(1.0, w))
    h = max(0.0, min(1.0, h))
    return (x, y, w, h)


def set_process_params(
    journal: dict, name: str, params: Params,
) -> None:
    """Write the user's manual slider state for ``name`` into the
    journal. Caller saves via :func:`save_ingest_journal`."""
    decisions = _decisions_dict(journal)
    entry = decisions.setdefault(name, {})
    entry[_KEY_PARAMS] = asdict(params)


def set_process_crop(
    journal: dict, name: str,
    crop_norm: tuple[float, float, float, float],
) -> None:
    """Write the user's crop rect for ``name`` into the journal."""
    decisions = _decisions_dict(journal)
    entry = decisions.setdefault(name, {})
    entry[_KEY_CROP] = [float(v) for v in crop_norm]


def get_process_crop_angle(journal: dict, name: str) -> Optional[float]:
    """Pull the journaled **Box Rotation** angle for ``name`` (docs/25
    §4; the field formerly held the ±45° tilt — now it's the box
    rotation, any angle). Returns ``None`` when nothing is recorded,
    else the float clamped to ``[-360, 360]`` (defensive against a
    hand-edited journal; the box-rotation render handles any angle).

    Returning ``None`` rather than 0.0 for "not recorded" lets callers
    distinguish "explicitly 0°" from "never touched"."""
    entry = get_process_decision(journal, name)
    if entry is None:
        return None
    payload = entry.get(_KEY_CROP_ANGLE)
    if not isinstance(payload, (int, float)):
        return None
    try:
        v = float(payload)
    except (TypeError, ValueError):
        return None
    return max(-360.0, min(360.0, v))


def set_process_crop_angle(
    journal: dict, name: str, angle_degrees: float,
) -> None:
    """Write the crop tilt for ``name`` into the journal (task #117)."""
    decisions = _decisions_dict(journal)
    entry = decisions.setdefault(name, {})
    entry[_KEY_CROP_ANGLE] = float(angle_degrees)


def get_process_rotation(journal: dict, name: str) -> Optional[int]:
    """Pull the journaled rotation for ``name`` (docs/25 §4).

    Returns ``None`` when nothing is recorded, else one of
    ``{0, 90, 180, 270}`` (any other value is normalised to the
    nearest 90 and wrapped — defensive against a hand-edited journal).
    ``None`` rather than 0 lets callers tell "never touched" from
    "explicitly un-rotated"."""
    entry = get_process_decision(journal, name)
    if entry is None:
        return None
    payload = entry.get(_KEY_ROTATION)
    if not isinstance(payload, (int, float)):
        return None
    return (int(round(float(payload) / 90.0)) * 90) % 360


def set_process_rotation(
    journal: dict, name: str, degrees: int,
) -> None:
    """Write the rotation for ``name`` into the journal, normalised to
    ``{0, 90, 180, 270}`` (docs/25 §4)."""
    decisions = _decisions_dict(journal)
    entry = decisions.setdefault(name, {})
    entry[_KEY_ROTATION] = (int(round(float(degrees) / 90.0)) * 90) % 360


# ── Exported tracking (docs/25 §9) ──────────────────────────────


def get_process_exported(journal: dict) -> set[str]:
    """Set of filenames already materialised by a Process Export.
    Empty set when nothing has been exported (or on an older journal
    that doesn't carry the key)."""
    value = journal.get(PROCESS_EXPORTED_KEY)
    if not isinstance(value, list):
        return set()
    return {str(v) for v in value if isinstance(v, str)}


def is_process_exported(journal: dict, name: str) -> bool:
    """True iff ``name`` has been materialised by a Process Export."""
    return name in get_process_exported(journal)


def mark_process_exported(journal: dict, names: Iterable[str]) -> None:
    """Record ``names`` as materialised. Idempotent — re-exporting a
    photo doesn't duplicate it. Stored as a sorted list so the journal
    diff stays stable across saves. Caller persists via
    :func:`save_ingest_journal`."""
    current = get_process_exported(journal)
    current.update(str(n) for n in names)
    journal[PROCESS_EXPORTED_KEY] = sorted(current)


def clear_process_exported(journal: dict, name: str) -> None:
    """Drop ``name`` from the exported set (e.g. the user re-edits a
    photo and the EXPORTED chip should clear). No-op when absent."""
    current = get_process_exported(journal)
    if name in current:
        current.discard(name)
        journal[PROCESS_EXPORTED_KEY] = sorted(current)


def clear_process_decision(journal: dict, name: str) -> None:
    """Drop the entire decision entry for ``name`` (both params and
    crop). Useful for a per-photo Reset button; current page never
    invokes this but the API completes the round-trip and tests pin
    it so future surfaces can rely on it.

    No-op when the entry doesn't exist — never raises."""
    decisions = journal.get(PROCESS_DECISIONS_KEY)
    if isinstance(decisions, dict) and name in decisions:
        del decisions[name]


def all_decision_names(journal: dict) -> set[str]:
    """Set of filenames that have a Process decision recorded. The
    page uses this to populate its in-memory caches in one pass on
    bucket open."""
    d = journal.get(PROCESS_DECISIONS_KEY)
    if not isinstance(d, dict):
        return set()
    return set(d.keys())
