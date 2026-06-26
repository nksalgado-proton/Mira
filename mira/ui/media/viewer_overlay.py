"""spec/134 — Picker / Editor viewer-overlay orchestration helper.

The pure-logic layer (:mod:`core.cut_overlay`,
:mod:`core.viewer_overlay`) owns the field vocabulary, the
``FrameProvenance`` resolver, and the text composer. This module is
the thin UI-side glue that reads the configurable selection from
:class:`~mira.settings.repo.SettingsRepo`, asks the gateway for the
item's provenance, and returns the HTML the
:class:`~mira.ui.media.photo_overlay.PhotoExposureOverlay` pill expects
(one line per selected field, joined with ``<br>``).

Both surfaces — Picker (surface 07) and Editor (surface 08) —
import :func:`compose_viewer_overlay_html` so they paint identical
text from the same setting + same item.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

#: Separator between field GROUPS (When / Where / Camera / Exposure) on the
#: single-line pill. Heavier than the middot ``·`` the cut-overlay uses
#: *within* a group (camera·lens·flash, focal·f·shutter·ISO) so the eye can
#: still tell the groups apart. Non-breaking spaces keep the line intact.
_FIELD_SEPARATOR = "&nbsp;&nbsp;•&nbsp;&nbsp;"


def viewer_overlay_fields_from_settings() -> list:
    """spec/134 — read ``viewer_overlay_fields`` from the roaming
    Settings repo at call time. Picker / Editor call this on each
    item landing so the Settings dialog's Apply path updates the
    overlay without a relaunch. Defaults to ``["how2"]`` (today's
    exposure pill) on a load failure so the early-boot path keeps
    the overlay live."""
    try:
        from mira.settings.repo import SettingsRepo
        return list(SettingsRepo().load().viewer_overlay_fields)
    except Exception:                                              # noqa: BLE001
        log.debug(
            "viewer_overlay_fields: settings load failed; using default",
            exc_info=True)
        return ["how2"]


def compose_viewer_overlay_html(
    eg: Any, item_id: Optional[str], *,
    fields: Optional[list] = None,
) -> str:
    """spec/134 — orchestrate the viewer overlay's HTML for one item.

    Steps:
      1. Resolve the selected ``fields`` (from the setting unless the
         caller passes them explicitly — useful for tests).
      2. Build the item's :class:`~core.cut_overlay.FrameProvenance`
         via :meth:`EventGateway.item_provenance` (empty when ``eg`` /
         ``item_id`` is missing).
      3. Compose lines via :func:`core.cut_overlay.compose_overlay_lines`.
      4. Join the field groups onto ONE line with :data:`_FIELD_SEPARATOR`
         so the pill reads as a single strip along the photo's bottom edge.

    Returns ``""`` (the pill's "hidden" sentinel) when the selection
    is empty OR every selected field is data-free for this item.
    """
    from core.cut_overlay import FrameProvenance, compose_overlay_lines

    if fields is None:
        fields = viewer_overlay_fields_from_settings()
    if not fields:
        return ""
    prov = FrameProvenance()
    if eg is not None and item_id:
        try:
            prov = eg.item_provenance(item_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "item_provenance failed for %s", item_id)
    lines = compose_overlay_lines(fields, prov)
    return _FIELD_SEPARATOR.join(lines) if lines else ""


__all__ = [
    "viewer_overlay_fields_from_settings",
    "compose_viewer_overlay_html",
]
