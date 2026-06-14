"""Aspect-ratio constants for the Process Culler crop tool.

Single source of truth for the crop ratio choices the user can pick. Each
event stores a project-wide default in ``event_settings["default_aspect_ratio"]``,
which the Process Culler uses on entry; the user can override per-photo
from the toolbar combo.

The ratios are stored as ``(label, w, h)`` tuples — ``w / h`` gives the
numeric ratio. ``"Original"`` means "no crop", and is the fallback when
an event has no default set or carries an unknown value.
"""

from __future__ import annotations

from dataclasses import dataclass


# The string value stored in event_settings + serialized to JSON.
ORIGINAL_LABEL = "Original"


@dataclass(frozen=True)
class AspectRatio:
    label: str
    w: int  # 0 for "Original"
    h: int  # 0 for "Original"

    @property
    def is_original(self) -> bool:
        return self.w == 0 or self.h == 0

    @property
    def value(self) -> float:
        """Numeric w/h. Raises ZeroDivisionError for Original — callers
        must check ``is_original`` first."""
        return self.w / self.h


# Order matches the toolbar combo box. Keep "Original" first so it's
# the no-op default for new events that haven't picked a ratio yet.
ASPECT_RATIOS: tuple[AspectRatio, ...] = (
    AspectRatio(ORIGINAL_LABEL, 0, 0),
    AspectRatio("4:3", 4, 3),
    AspectRatio("3:2", 3, 2),
    AspectRatio("16:9", 16, 9),
    AspectRatio("1:1", 1, 1),
    AspectRatio("5:4", 5, 4),
)


def get_aspect_ratio(label: str) -> AspectRatio:
    """Look up an aspect ratio by label. Falls back to Original when the
    label is missing, empty, or unknown — keeps the Process Culler safe
    against legacy event JSONs that predate this feature."""
    if not label:
        return ASPECT_RATIOS[0]
    for ar in ASPECT_RATIOS:
        if ar.label == label:
            return ar
    return ASPECT_RATIOS[0]


def aspect_ratio_labels() -> list[str]:
    """Toolbar combo + wizard picker share this list."""
    return [ar.label for ar in ASPECT_RATIOS]


def zoom_quarter_size(source_w: int, source_h: int) -> tuple[int, int]:
    """Pixel size of a 1×4 zoom crop in source-image coords.

    The 1×4 zoom is a fixed-size crop that captures one quarter of the
    photo's area: half its width and half its height. Used when the
    user wants to extract a sub-region for downstream multi-photo
    slide layouts (e.g. four photos in a 2×2 grid on a 4K slide) —
    a phone shot at native 4032×3024 lands at 2016×1512 inside its
    quarter-slot without any upscaling.

    The frame preserves the source's aspect ratio (since both
    dimensions halve equally). The toolbar's aspect-ratio combo is
    independent of this zoom mode — when the zoom toggle is on, the
    rect's shape comes from the source, and the user only positions
    it inside the photo.
    """
    return max(1, source_w // 2), max(1, source_h // 2)
