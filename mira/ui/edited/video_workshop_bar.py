"""Back-compat re-export — the widget moved to ``mira.ui.media.transport_bar``
per spec/130 (one shared transport widget for the Picker AND the Editor).

The Editor's existing ``from mira.ui.edited.video_workshop_bar import …``
imports keep resolving via this shim; new code should import from the
new location.
"""
from mira.ui.media.transport_bar import (    # noqa: F401
    VideoWorkshopBar,
    WORKSHOP_REVEAL_HEIGHT,
)


__all__ = ["VideoWorkshopBar", "WORKSHOP_REVEAL_HEIGHT"]
