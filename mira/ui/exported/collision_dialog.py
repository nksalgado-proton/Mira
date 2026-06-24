"""spec/118 §3 — the Overwrite / Keep both / Cancel dialogs.

LRC-style collision choice surfaced at export time when a Mira render
already exists for the item being re-shipped:

* **Overwrite** (:data:`OVERWRITE`) → ``CollisionPolicy.OVERRIDE``;
  atomic replace at the same ``export_relpath``. The lineage row is
  upserted (recipe_json + exported_at refresh) and the file's identity
  is unchanged, so any Cut containing the export keeps it — just with
  new pixels. No new version, no membership change, no re-pick.
* **Keep both** (:data:`KEEP_BOTH`) → ``CollisionPolicy.UNIQUE``;
  today's default. The new render lands as ``stem (2).jpg`` with its
  own lineage row. The item becomes a versions cluster (spec/89 §1)
  and any Cut now shows BOTH versions until the user re-picks.
* **Cancel** (``None``) → no render.

The single-item dialog (:func:`ask_overwrite_or_keep_both`) is the
``Export this`` re-render path (spec/118 §3 single-item). The batch
dialog (:func:`ask_batch_collision_policy`) covers a multi-cell
``↑ Export now`` run that includes at least one edited-since-export
item — the choice applies run-wide (per-item is a v2 enhancement).
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QDialog, QWidget

from mira.ui.design.dialogs import MessageDialog
from mira.ui.i18n import tr


OVERWRITE = "override"
KEEP_BOTH = "unique"


def ask_overwrite_or_keep_both(
    parent: Optional[QWidget],
) -> Optional[str]:
    """Single-item three-way ask. Returns :data:`OVERWRITE` /
    :data:`KEEP_BOTH` / ``None`` (cancel). The body copy warns the
    user that "Keep both" makes the containing Cut show both versions
    until they re-pick — :data:`OVERWRITE` leaves the Cut untouched."""
    dlg = MessageDialog(
        intent="confirm",
        title=tr("An export already exists."),
        message=tr(
            "Overwrite the existing file (recommended — any Cut "
            "containing this frame keeps pointing at the same file, "
            "just with new pixels), or keep both as separate versions "
            "(today's behaviour — the Cut will show BOTH until you "
            "re-pick)?"
        ),
        primary_text=tr("Overwrite"),
        secondary_text=tr("Keep both"),
        ghost_text=tr("Cancel"),
        parent=parent,
    )
    dlg.exec()
    kind = dlg.result_kind()
    if kind == "primary":
        return OVERWRITE
    if kind == "secondary":
        return KEEP_BOTH
    return None


def ask_batch_collision_policy(
    parent: Optional[QWidget],
    *,
    n_render: int,
    m_delete: int,
    n_stale: int,
    default: str = KEEP_BOTH,
) -> Optional[str]:
    """Batch ``Export now`` confirm + run-level collision switch.
    Fires only when the run includes at least one edited-since-export
    item (``n_stale >= 1``). Returns :data:`OVERWRITE` / :data:`KEEP_BOTH`
    / ``None`` (cancel).

    ``default`` flips which button is presented as primary so the
    user's last choice rides as the default (sticks across the
    session — the caller persists it in memory; out of scope for this
    helper)."""
    bits: list = []
    if n_render > 0:
        bits.append(
            tr("{n} item(s) render to Exported Media/.")
            .replace("{n}", str(n_render))
        )
    if m_delete > 0:
        bits.append(
            tr(
                "{m} file(s) drop from Exported Media/ "
                "(Original Media/ stays untouched)."
            ).replace("{m}", str(m_delete))
        )
    bits.append(
        tr(
            "{k} of these already have a Mira render on disk that has "
            "since been re-edited — choose how to ship them."
        ).replace("{k}", str(n_stale))
    )
    bits.append(
        tr(
            "Overwrite (recommended): replaces each file in place — any "
            "Cut keeps pointing at it. Keep both: a new \"(2)\" version "
            "lands alongside (the Cut will show BOTH until re-picked)."
        )
    )
    body = "\n\n".join(bits)
    if default == OVERWRITE:
        primary_text = tr("Overwrite all")
        secondary_text = tr("Keep both")
    else:
        primary_text = tr("Keep both")
        secondary_text = tr("Overwrite all")
    dlg = MessageDialog(
        intent="confirm",
        title=(
            tr("Render {n} · Delete {m} files. Proceed?")
            .replace("{n}", str(n_render))
            .replace("{m}", str(m_delete))
        ),
        message=body,
        primary_text=primary_text,
        secondary_text=secondary_text,
        ghost_text=tr("Cancel"),
        parent=parent,
    )
    dlg.exec()
    kind = dlg.result_kind()
    if kind == "primary":
        return OVERWRITE if default == OVERWRITE else KEEP_BOTH
    if kind == "secondary":
        return KEEP_BOTH if default == OVERWRITE else OVERWRITE
    return None


__all__ = [
    "KEEP_BOTH",
    "OVERWRITE",
    "ask_batch_collision_policy",
    "ask_overwrite_or_keep_both",
]
