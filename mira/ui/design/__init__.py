"""Mira design-system component catalog.

Reusable PyQt6 widgets + factory helpers that map onto the role rules in
``assets/themes/redesign.qss`` (via setObjectName) plus a few painted-from-tokens
widgets where QSS can't carry the look (drop-shadow on cards, glow on hero
progress, donut slices, blurred-fill thumb backdrop).

Build once here; every redesigned surface composes from this catalog without
re-deriving styles. See ``MiraCrafter Redesign/00-design-system.md`` on
Nelson's Desktop for the canonical spec.

Module map:
    cards     Card / Card2 / StatTile
    headers   PageHeader / ThemeToggle
    buttons   primary_button / ghost_button / danger_ghost_button
    inputs    line_input / select / search_field
    chips     chip (open/done/prog/closed/idle) / tag / pill_toggle
    toolbar   toolbar_row helper

Later commits add: progress (StageBar / hero glow), donut, thumbs, media_nav,
stable_stage, carousel, dialog templates.
"""
from mira.ui.design.accordion import (
    AccordionSection,
    RecipeContainer,
    StrictAccordionGroup,
)
from mira.ui.design.blurred_photo_canvas import BlurredPhotoCanvas
from mira.ui.design.brand import MiraLogo, MiraMark
from mira.ui.design.buttons import (
    danger_ghost_button,
    ghost_button,
    primary_button,
)
from mira.ui.design.cards import Card, Card2, StatTile
from mira.ui.design.density import apply_density
from mira.ui.design.carousel import Carousel
from mira.ui.design.dialogs import (
    MessageDialog,
    ProgressDialog,
    confirm,
    confirm_destructive,
    show_error,
    show_info,
    show_success,
)
from mira.ui.design.donut import Donut, DonutSlice
from mira.ui.design.chips import (
    chip_closed,
    chip_done,
    chip_idle,
    chip_open,
    chip_prog,
    pill_toggle,
    tag,
)
from mira.ui.design.headers import (
    PageHeader,
    SurfaceIdentityHeader,
    ThemeToggle,
)
from mira.ui.design.icons import (
    CATEGORIES_DIR,
    CLUSTERS_DIR,
    GLYPHS_DIR,
    GLYPH_CHECK,
    GLYPH_CROSS,
    GLYPH_CROSS_EVENT,
    GLYPH_CUT,
    GLYPH_EVENT,
    GLYPH_EYE,
    GLYPH_MAP,
    GLYPH_PAUSE,
    GLYPH_PLAY,
    GLYPH_SEARCH,
    GLYPH_TO_END,
    GLYPH_TO_START,
    GLYPH_VOLUME,
    GLYPH_VOLUME_MUTED,
    PHASE_GLYPH,
    PHASES_DIR,
    paint_tinted_svg,
    tinted_svg_pixmap,
)
from mira.ui.design.inputs import line_input, search_field, select
from mira.ui.design.media_nav import Filmstrip, nav_button
from mira.ui.design.photo_cycler import PhotoCycler
from mira.ui.design.progress import StageProgress
from mira.ui.design.stable_stage import StableMediaStage
from mira.ui.design.thumb_grid import (
    DEFAULT_CELL_SIZE as THUMB_GRID_DEFAULT_CELL_SIZE,
    ThumbGrid,
    ThumbGridItem,
)
from mira.ui.design.thumbs import Thumb
from mira.ui.design.title_bar import TitleBar
from mira.ui.design.toolbar import toolbar_row

__all__ = [
    "AccordionSection",
    "BlurredPhotoCanvas",
    "CATEGORIES_DIR",
    "CLUSTERS_DIR",
    "Card",
    "Card2",
    "Carousel",
    "Donut",
    "apply_density",
    "DonutSlice",
    "Filmstrip",
    "GLYPHS_DIR",
    "GLYPH_CHECK",
    "GLYPH_CROSS",
    "GLYPH_CROSS_EVENT",
    "GLYPH_CUT",
    "GLYPH_EVENT",
    "GLYPH_EYE",
    "GLYPH_MAP",
    "GLYPH_PAUSE",
    "GLYPH_PLAY",
    "GLYPH_SEARCH",
    "GLYPH_TO_END",
    "GLYPH_TO_START",
    "GLYPH_VOLUME",
    "GLYPH_VOLUME_MUTED",
    "MessageDialog",
    "PHASES_DIR",
    "PHASE_GLYPH",
    "MiraLogo",
    "MiraMark",
    "PageHeader",
    "PhotoCycler",
    "ProgressDialog",
    "RecipeContainer",
    "StableMediaStage",
    "StageProgress",
    "StatTile",
    "StrictAccordionGroup",
    "SurfaceIdentityHeader",
    "ThemeToggle",
    "THUMB_GRID_DEFAULT_CELL_SIZE",
    "Thumb",
    "ThumbGrid",
    "ThumbGridItem",
    "TitleBar",
    "chip_closed",
    "chip_done",
    "chip_idle",
    "chip_open",
    "chip_prog",
    "confirm",
    "confirm_destructive",
    "danger_ghost_button",
    "ghost_button",
    "line_input",
    "nav_button",
    "paint_tinted_svg",
    "pill_toggle",
    "primary_button",
    "search_field",
    "select",
    "show_error",
    "show_info",
    "show_success",
    "tag",
    "tinted_svg_pixmap",
    "toolbar_row",
]
