"""Navigation entry-key constants.

Once the home of the persistent global ``Sidebar`` rail, briefly the home of
the rounded-pill sidebar primitives used by ``LibrarySidebar``. As of
2026-06-06 (Nelson eyeball — "make it look like a Windows app") the sidebar
shape was abandoned and every former rail entry became a menu-bar item. This
module is now just the shared **navigation-key constants** that
:meth:`MainWindow._on_entry` dispatches on; the menu spec itself lives next to
the dispatcher in :mod:`mira.ui.shell.main_window`.
"""
from __future__ import annotations

# ── Entry keys — kept stable across UI redesigns so the host's dispatch
# table doesn't need to change when navigation chrome moves around. ────────────
ENTRY_DASHBOARD = "dashboard"
ENTRY_WIZARD = "wizard"
ENTRY_NEW_EVENT = "new_event"
ENTRY_CREATE_FROM_PAST = "create_from_past"
ENTRY_RESTORE_FROM_BACKUP = "restore_from_backup"
ENTRY_BACK_UP_EVENT = "back_up_event"
ENTRY_BACK_UP_CARD = "back_up_card"
ENTRY_PLAN_TEMPLATE = "plan_template"
ENTRY_CULLER_STANDALONE = "culler_standalone"
ENTRY_FAST_CULLER_STANDALONE = "fast_culler_standalone"
ENTRY_PHOTO_PROCESSOR = "photo_processor"
ENTRY_AUDIO = "audio"
ENTRY_HELPERS = "helpers"
ENTRY_SETTINGS = "settings"
