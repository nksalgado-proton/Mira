"""Name-collision guard — ported from legacy ``ui/base/name_collision.py`` (charter §4
step 7), with its one data tendril severed: the caller now passes the matching events
(from ``Gateway.list_events()``) instead of this module querying the legacy event store.

Two events with the same name resolve to the same on-disk folder
(``<photos_base>/<event_folder_name>``), so a same-named ingest would merge into the
existing folder. Every new-event surface calls :func:`confirm_name_collision` before
materialising. Returns True when the caller may proceed (no collision, or the user opted
in), False when the user cancelled.
"""
from __future__ import annotations

from typing import Any, Dict, List

from PyQt6.QtWidgets import QMessageBox, QWidget

from mira.ui.i18n import tr


def confirm_name_collision(
    parent: QWidget, name: str, matches: List[Dict[str, Any]],
) -> bool:
    """Return True iff the caller may proceed creating an event named ``name``.

    ``matches`` is the list of existing index rows (``Gateway.list_events()`` shape:
    ``name`` / ``start_date`` / ``event_root``) whose name equals ``name``. Empty → no
    dialog, proceed. Otherwise a modal warning explains the shared-folder consequence;
    default is No (the safer choice)."""
    if not matches:
        return True

    lines = [
        tr("<b>An event named '{n}' already exists.</b>").replace("{n}", name),
        "",
        tr(
            "Creating another one with the same name means both events will share a "
            "single folder on disk (<code>&lt;Photos&gt;/{n}</code>). New files will be "
            "added to that folder. If any filenames match files from the existing event, "
            "the new ingest will OVERWRITE them."
        ).replace("{n}", name),
        "",
        tr("Existing event(s) with this name:"),
    ]
    for ev in matches:
        bits = [str(ev.get("name") or name)]
        if ev.get("start_date"):
            bits.append(str(ev["start_date"]))
        if ev.get("event_root"):
            bits.append(f"<code>{ev['event_root']}</code>")
        lines.append("  • " + " — ".join(bits))
    lines.extend(["", tr("Proceed and create anyway?")])

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(tr("Event name already exists"))
    box.setTextFormat(2)  # Qt.TextFormat.RichText
    box.setText("<br>".join(lines))
    yes_btn = box.addButton(tr("Yes — create anyway"), QMessageBox.ButtonRole.YesRole)
    no_btn = box.addButton(tr("No — cancel"), QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(no_btn)
    box.exec()
    return box.clickedButton() is yes_btn
