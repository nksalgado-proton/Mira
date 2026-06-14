"""The settings repository — typed access to ``settings.json`` (spec/04 §6).

Load is tolerant by contract (spec/04 §1): the app must never fail to boot on a bad
settings file. Missing → seed defaults; corrupt → preserve the bad bytes and fall
back to defaults; sidecar mismatch → log + load anyway (settings are recoverable).
Save goes through :mod:`mira.protect` (atomic write-then-rename + SHA-256
sidecar + history rotation).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from mira import protect
from mira.paths import user_data_dir
from mira.settings.model import (
    MIGRATIONS,
    SETTINGS_SCHEMA_VERSION,
    Settings,
)

log = logging.getLogger(__name__)

# Coexistence isolation (Nelson 2026-05-30): the legacy app owns ``settings.json`` in the
# same user-data dir and writes it in its own flat format — sharing it means whichever app
# ran last clobbers ``photos_base_path`` (legacy points at the old library root, the new app
# at the rebuild library). The new app therefore keeps its **own** settings file. At the
# §4-step-8 cutover, when the legacy is archived, this can reclaim ``settings.json``.
SETTINGS_FILENAME = "settings.rebuild.json"


class SettingsRepo:
    """Reads/writes the one ``Settings`` document. The only sanctioned access to
    the settings bytes — nothing else touches the file directly (spec/02 §1)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else user_data_dir() / SETTINGS_FILENAME

    # ── load ──────────────────────────────────────────────────────────────

    def load(self) -> Settings:
        """Return a ``Settings``. Never raises on a bad file."""
        if not self.path.exists():
            settings = Settings()
            self._seed(settings)
            return settings

        try:
            raw = self.path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("settings.json top-level value is not an object")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.warning("settings.json failed to load (%s); using defaults", exc)
            self._backup_bad_file()
            settings = Settings()
            self._seed(settings)
            return settings

        # Non-blocking integrity check — corruption is surfaced, not fatal.
        outcome = protect.verify(self.path)
        if not outcome.valid and not outcome.sidecar_missing:
            log.warning(
                "settings.json sidecar mismatch (expected %s, got %s); loading anyway",
                outcome.expected_sha256[:12], outcome.actual_sha256[:12],
            )

        parsed = self._migrate(parsed)
        return Settings.from_dict(parsed)

    # ── save ──────────────────────────────────────────────────────────────

    def save(self, settings: Settings) -> None:
        """Persist with the full protection contract (atomic + sidecar + history)."""
        payload: Dict[str, Any] = {"schema_version": SETTINGS_SCHEMA_VERSION}
        payload.update(settings.to_dict())
        protect.write_protected(self.path, payload)

    def update(self, **changes: Any) -> Settings:
        """Load-mutate-save one or more keys; return the updated ``Settings``."""
        settings = self.load()
        for key, value in changes.items():
            setattr(settings, key, value)
        self.save(settings)
        return settings

    # ── internals ─────────────────────────────────────────────────────────

    def _seed(self, settings: Settings) -> None:
        """Write defaults on first launch so a real file exists on disk. Best
        effort — a write failure leaves the app running in-memory on defaults."""
        try:
            self.save(settings)
            log.info("Seeded settings.json with defaults at %s", self.path)
        except OSError as exc:
            log.warning("Could not seed settings.json at %s (%s)", self.path, exc)

    def _backup_bad_file(self) -> None:
        """Move the unreadable file aside as ``settings.json.bak`` for forensics.
        Replaces any prior ``.bak`` so chronic corruption doesn't accumulate."""
        backup = self.path.with_suffix(self.path.suffix + ".bak")
        try:
            if backup.exists():
                backup.unlink()
            self.path.rename(backup)
            log.warning("Bad settings.json backed up to %s", backup)
        except OSError as exc:
            log.warning("Could not back up bad settings file: %s", exc)

    @staticmethod
    def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
        """Run ordered migrations from the file's version up to current. A file
        with no ``schema_version`` is treated as version 1 (the new app starts
        fresh — the legacy flat shape is not migrated, charter §3). A newer-than-
        current file loads best-effort with a warning (forward-compat)."""
        version = data.get("schema_version", 1)
        if version > SETTINGS_SCHEMA_VERSION:
            log.warning(
                "settings.json schema_version %s newer than supported %s; "
                "loading best-effort", version, SETTINGS_SCHEMA_VERSION,
            )
            return data
        for from_version, migrate_fn in MIGRATIONS:
            if version == from_version:
                data = migrate_fn(data)
                version += 1
        return data
