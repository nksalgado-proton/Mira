"""App settings — Domain 5 (spec/04 / charter §5.7).

The one application Settings class + its JSON repo, built before the UI rebuild so
every module reads its defaults from one typed place. Public surface:

- :class:`~mira.settings.model.Settings` — the typed default catalog.
- :class:`~mira.settings.repo.SettingsRepo` — the only sanctioned access to
  ``settings.json`` (tolerant load, protected save).
- ``user_keys()`` / ``app_keys()`` — tier introspection for the future dialog.
"""
from mira.settings.model import (
    SETTINGS_SCHEMA_VERSION,
    Settings,
    app_keys,
    user_keys,
)
from mira.settings.repo import SettingsRepo

__all__ = [
    "Settings",
    "SettingsRepo",
    "SETTINGS_SCHEMA_VERSION",
    "user_keys",
    "app_keys",
]
