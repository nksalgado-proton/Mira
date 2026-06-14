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
from mira.ui.design.buttons import (
    danger_ghost_button,
    ghost_button,
    primary_button,
)
from mira.ui.design.cards import Card, Card2, StatTile
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
from mira.ui.design.headers import PageHeader, ThemeToggle
from mira.ui.design.inputs import line_input, search_field, select
from mira.ui.design.media_nav import Filmstrip, nav_arrow
from mira.ui.design.progress import StageProgress
from mira.ui.design.stable_stage import StableMediaStage
from mira.ui.design.thumbs import Thumb
from mira.ui.design.toolbar import toolbar_row

__all__ = [
    "Card",
    "Card2",
    "Carousel",
    "Donut",
    "DonutSlice",
    "Filmstrip",
    "MessageDialog",
    "ProgressDialog",
    "confirm",
    "confirm_destructive",
    "show_error",
    "show_info",
    "show_success",
    "PageHeader",
    "StableMediaStage",
    "StageProgress",
    "StatTile",
    "ThemeToggle",
    "Thumb",
    "chip_closed",
    "chip_done",
    "chip_idle",
    "chip_open",
    "chip_prog",
    "danger_ghost_button",
    "ghost_button",
    "line_input",
    "nav_arrow",
    "pill_toggle",
    "primary_button",
    "search_field",
    "select",
    "tag",
    "toolbar_row",
]
