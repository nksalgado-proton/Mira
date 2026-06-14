"""One-shot import from the legacy JSON files to ``mira.db`` (spec/53 §4).

Runs on the first launch where the new user-level store doesn't exist yet:

  1. Apply the **installation profile** (``XMC`` / ``MC`` / ``custom``) so the
     feature_flag row layer + the code-side defaults have a profile to key on.
  2. Import ``settings.rebuild.json`` → ``setting`` rows (top-level keys become
     rows; ``value_json`` carries the JSON-encoded value). If the legacy file
     contains a ``wizard`` sub-tree, its keys become ``wizard_answer`` rows
     instead (per spec/53 §2.2 — wizard concern separated from regular
     preferences).
  3. Import ``events_index.json`` → one ``event_index`` row per entry.
  4. **Retire** the legacy files by renaming them with an
     ``.imported-<timestamp>`` suffix. They stay on disk as a safety net for
     one or two app versions, then get deleted by a later cleanup pass.

After this runs, the app reads exclusively from ``mira.db`` for
user-level state — no two-source-of-truth period.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from mira.user_store import models as m

if TYPE_CHECKING:
    from mira.user_store.repo import UserStore

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Outcome shape
# --------------------------------------------------------------------------- #


@dataclass
class ImportOutcome:
    """What the one-shot import did, for caller logging + test assertions.

    Counts reflect rows actually written; ``retired_files`` lists the legacy
    files renamed in step 4 (could be ``None`` for any file that didn't exist —
    a truly fresh install hits no retire step)."""

    profile_name: str
    settings_count: int = 0
    wizard_answers_count: int = 0
    event_index_count: int = 0
    flags_seeded_count: int = 0
    retired_files: List[Path] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retire_timestamp() -> str:
    """Filename-safe timestamp for the ``.imported-<ts>`` suffix."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------- #
# Step 1 — installation profile
# --------------------------------------------------------------------------- #


def _apply_installation_profile(
    store: "UserStore",
    profile_name: str,
    *,
    now: str,
) -> int:
    """Stamp the installation_profile singleton + write the per-profile default
    feature_flag rows (``source='install_profile'``).

    Returns the count of feature_flag rows written. Idempotent: rerunning
    overwrites the singleton + the per-profile flag rows (the user-level
    ``source='user'`` overrides survive because of the PK + source filter)."""
    from core.feature_flags import DEFAULTS_BY_PROFILE, FLAG_KEYS

    store.upsert(m.InstallationProfile(name=profile_name, created_at=now))

    defaults = DEFAULTS_BY_PROFILE.get(profile_name, DEFAULTS_BY_PROFILE["XMC"])
    count = 0
    for key in sorted(FLAG_KEYS):
        store.upsert(m.FeatureFlag(
            key=key,
            enabled=defaults[key],
            source="install_profile",
            set_at=now,
        ))
        count += 1
    return count


# --------------------------------------------------------------------------- #
# Step 2 — settings.rebuild.json → setting + wizard_answer
# --------------------------------------------------------------------------- #


def _load_legacy_json(path: Path) -> Optional[Dict]:
    """Tolerant load: missing file is ``None``; unparseable file logs a warning
    and returns ``None`` (we don't want to wedge first-launch on a bad legacy
    file — the new store is created empty and the user starts fresh)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("legacy file %s failed to parse (%s); skipping", path, exc)
        return None
    if not isinstance(data, dict):
        log.warning("legacy file %s top-level value is not an object; skipping", path)
        return None
    return data


def _import_settings(
    store: "UserStore",
    settings_path: Path,
    *,
    now: str,
) -> tuple[int, int]:
    """Import ``settings.rebuild.json`` into the ``setting`` + ``wizard_answer``
    tables. Returns ``(settings_count, wizard_answers_count)``.

    Top-level keys become ``setting`` rows; ``schema_version`` is dropped
    (it's the legacy file's version, not the new store's). If the legacy file
    contains a top-level ``wizard`` object, its keys become ``wizard_answer``
    rows instead — this matches spec/53 §2.2 (wizard concern separated from
    regular preferences)."""
    data = _load_legacy_json(settings_path)
    if data is None:
        return 0, 0

    settings_count = 0
    wizard_count = 0

    for key, value in data.items():
        if key == "schema_version":
            continue
        if key == "wizard" and isinstance(value, dict):
            for q_id, answer in value.items():
                store.upsert(m.WizardAnswer(
                    question_id=q_id,
                    answer_json=json.dumps(answer, ensure_ascii=False),
                    answered_at=now,
                ))
                wizard_count += 1
            continue
        store.upsert(m.Setting(
            key=key,
            value_json=json.dumps(value, ensure_ascii=False),
            updated_at=now,
        ))
        settings_count += 1

    return settings_count, wizard_count


# --------------------------------------------------------------------------- #
# Step 3 — events_index.json → event_index
# --------------------------------------------------------------------------- #


def _import_events_index(
    store: "UserStore",
    events_index_path: Path,
) -> int:
    """Import ``events_index.json`` into the ``event_index`` table. Returns
    the row count written.

    The legacy ``photos_base_path`` mirror at the top of events_index.json is
    NOT imported here — it's already in ``settings.rebuild.json`` and lands in
    the ``setting`` table via step 2. Single source of truth in the new store.
    """
    data = _load_legacy_json(events_index_path)
    if data is None:
        return 0

    events = data.get("events")
    if not isinstance(events, list):
        log.warning("%s has no 'events' list; skipping", events_index_path)
        return 0

    count = 0
    for entry in events:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        store.upsert(m.EventIndex(
            event_uuid=str(entry["id"]),
            relpath_to_base=str(entry.get("event_relpath") or ""),
            abs_path=entry.get("event_root_abs"),
            name_cached=str(entry.get("name") or ""),
            type_cached=entry.get("event_type"),
            country_cached=entry.get("country_code"),       # tolerant — may not be present
            start_date_cached=entry.get("start_date"),
            end_date_cached=entry.get("end_date"),
            is_closed_cached=bool(entry.get("is_closed", False)),
        ))
        count += 1
    return count


# --------------------------------------------------------------------------- #
# Step 4 — retire the legacy files (rename with .imported-<ts> suffix)
# --------------------------------------------------------------------------- #


def _retire_file(path: Path, *, stamp: str) -> Optional[Path]:
    """Rename ``foo.json`` → ``foo.json.imported-<stamp>``. ``None`` if the file
    didn't exist (a truly fresh install). Errors log + return ``None`` so a
    file-system hiccup doesn't undo the import."""
    if not path.is_file():
        return None
    retired = path.with_suffix(path.suffix + f".imported-{stamp}")
    try:
        os.replace(str(path), str(retired))
    except OSError as exc:
        log.warning("could not retire %s: %s", path, exc)
        return None
    return retired


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def import_legacy_state(
    store: "UserStore",
    *,
    settings_path: Path,
    events_index_path: Path,
    profile_name: str = "XMC",
    now: Optional[str] = None,
    retire: bool = True,
) -> ImportOutcome:
    """Run the four-step first-launch import per spec/53 §4. Idempotent —
    re-running on an already-imported store overwrites the installation
    profile + the install-profile flag rows (user toggles survive) and
    re-upserts every legacy row. The retire step is the only side effect that
    DOESN'T re-run (the legacy files are gone after the first call).

    ``profile_name`` defaults to ``'XMC'`` (the conservative dev default —
    same fallback :mod:`core.feature_flags` uses on unknown profiles). The
    real first-launch call site reads this from an installer-written side
    channel (a registry key or an install-time marker file) per spec/53 §4
    step 2; in source-run dev it stays ``'XMC'``.

    ``retire=False`` skips step 4 — used by tests that want to assert on the
    file state without consuming the legacy fixtures.
    """
    stamp = now or _utc_now_iso()
    retire_stamp = _retire_timestamp()

    outcome = ImportOutcome(profile_name=profile_name)
    with store.transaction():
        outcome.flags_seeded_count = _apply_installation_profile(
            store, profile_name, now=stamp,
        )
        s_count, w_count = _import_settings(store, settings_path, now=stamp)
        outcome.settings_count = s_count
        outcome.wizard_answers_count = w_count
        outcome.event_index_count = _import_events_index(store, events_index_path)

    if retire:
        for legacy in (settings_path, events_index_path):
            retired = _retire_file(legacy, stamp=retire_stamp)
            if retired is not None:
                outcome.retired_files.append(retired)

    log.info(
        "imported legacy state: profile=%s settings=%d wizard=%d events=%d flags=%d retired=%d",
        outcome.profile_name, outcome.settings_count, outcome.wizard_answers_count,
        outcome.event_index_count, outcome.flags_seeded_count, len(outcome.retired_files),
    )
    return outcome
