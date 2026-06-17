"""Shared filter-family components (spec/83 §2 — family consistency).

The cross-event Dynamic Collection dialog (spec/81 §2.1) and the event-
scope Cut dialog speak the same Add-filter grammar — a grouped menu of
opt-in dimensions, active rows with an ✕ to remove, a wrapping
:class:`FlowLayout` for the inline multi-selects (spec/83 §3). The
catalogue of dimensions differs by scope (full at cross-event, thin at
event-scope per spec/81 §2.1: ``#exported`` + Style + media type), but
the shell is identical.

This module owns:

* :class:`FilterDimension` — the dataclass dialogs read.
* :class:`_ActiveFilterRow` — the one-row container with the ✕ button.
* The four group constants + :func:`group_label` (translated display).
* The two catalogue builders — :func:`build_cross_event_catalogue` (the
  full spec/32 §2 set used by the cross-event DC dialog) and
  :func:`build_event_scope_catalogue` (the thin Style + media type set,
  ready for a future :class:`NewCutDialog` adoption).

The host provides the editor-factory primitives (``make_multi`` /
``make_single`` / etc.) — the catalogue captures them in lambdas so the
inventory read is lazy (spec/83 §5). The actual menu / Add-button
widgets stay in their respective dialogs for now; a follow-up may wrap
them in a :class:`FilterFamilyWidget`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Tuple

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from mira.ui.design import ghost_button
from mira.ui.i18n import tr


# spec/83 §2 / spec/32 §2 — the groups in the Add-filter menu. spec/86 §6
# slots GROUP_EVENT between Curatorial and Camera & lens — event-level
# predicates are a natural narrowing pass before the EXIF / hardware
# facets, and they prune whole events first (spec/86 §1 efficiency).
GROUP_CURATORIAL  = "curatorial"
GROUP_EVENT       = "event"
GROUP_CAMERA_LENS = "camera_lens"
GROUP_SETTINGS    = "settings"
GROUP_WHEN_WHERE  = "when_where"

GROUP_ORDER: Tuple[str, ...] = (
    GROUP_CURATORIAL, GROUP_EVENT,
    GROUP_CAMERA_LENS, GROUP_SETTINGS, GROUP_WHEN_WHERE,
)


def group_label(group_id: str) -> str:
    return {
        GROUP_CURATORIAL:  tr("Curatorial"),
        GROUP_EVENT:       tr("Event"),
        GROUP_CAMERA_LENS: tr("Camera & lens"),
        GROUP_SETTINGS:    tr("Settings"),
        GROUP_WHEN_WHERE:  tr("When & where"),
    }.get(group_id, group_id)


@dataclass(frozen=True)
class FilterDimension:
    """One opt-in dimension a dialog offers (spec/83 §2).

    ``dim_id`` is the stable handle (also the menu key); ``filter_keys``
    are the ``filters_json`` keys the dimension owns (the rehydrate scan
    matches any of them, and the catalogue cross-checks them at dialog
    construction). ``factory`` builds the editor widget on demand — the
    closure carries the lazy inventory read (spec/83 §5)."""

    dim_id: str
    label: str
    group: str
    filter_keys: Tuple[str, ...]
    factory: Callable[[], QWidget]


class _ActiveFilterRow(QFrame):
    """One filter row in a dialog's active-filters stack (spec/83 §2).

    Holds the dimension's editor widget plus an ✕ button that emits
    :attr:`remove_requested` carrying the ``dim_id`` so the host can
    clean up its bookkeeping."""

    remove_requested = pyqtSignal(str)

    def __init__(self, dim: FilterDimension, facet: QWidget,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dim = dim
        self.facet = facet
        self.setObjectName("CrossEventDcActiveFilter")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        header = QLabel(dim.label, self)
        header.setObjectName("CrossEventDcActiveFilterLabel")
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        header.setMinimumWidth(120)
        layout.addWidget(header)

        layout.addWidget(facet, 1)

        # ✕ button — labelled "Remove" via tooltip so screen readers + the
        # spec/05 hint-on-every-control rule are honoured.
        self._remove_btn = ghost_button(tr("✕"))
        self._remove_btn.setObjectName("CrossEventDcRemoveFilter")
        self._remove_btn.setToolTip(tr("Remove this filter"))
        self._remove_btn.clicked.connect(
            lambda: self.remove_requested.emit(self._dim.dim_id))
        layout.addWidget(self._remove_btn)

    def dim_id(self) -> str:
        return self._dim.dim_id


# --------------------------------------------------------------------------- #
# Catalogue builders — each takes a ``host`` exposing the editor factories
# the dimensions need (``_make_multi`` / ``_make_single`` / etc.). The
# returned dict is indexed by ``dim_id`` in spec/32 §2 display order.
# --------------------------------------------------------------------------- #


def build_cross_event_catalogue(host: Any) -> Dict[str, FilterDimension]:
    """The full 15-dimension catalogue used by the cross-event DC dialog
    (spec/81 §2.1 + spec/32 §2). The host supplies the editor-factory
    primitives via duck-typed methods (``_make_multi(key)`` etc.); the
    register helper wraps each in ``host._register_facet``."""

    out: Dict[str, FilterDimension] = {}

    def reg(dim: FilterDimension) -> None:
        out[dim.dim_id] = dim

    # Curatorial
    reg(FilterDimension(
        "styles", tr("Style"), GROUP_CURATORIAL, ("styles",),
        lambda: host._register_facet(host._make_multi("styles"))))
    reg(FilterDimension(
        "media_type", tr("Media type"),
        GROUP_CURATORIAL, ("media_type",),
        lambda: host._register_facet(host._make_single("media_type", [
            (tr("Both"), "both"),
            (tr("Photos only"), "photo"),
            (tr("Videos only"), "video"),
        ]))))
    reg(FilterDimension(
        "stars", tr("Rating (stars)"),
        GROUP_CURATORIAL, ("stars_min",),
        lambda: host._register_facet(host._make_stars_min())))
    reg(FilterDimension(
        "color_labels", tr("Color label"),
        GROUP_CURATORIAL, ("color_labels",),
        lambda: host._register_facet(host._make_multi("color_labels"))))
    reg(FilterDimension(
        "flag", tr("Portfolio flag"),
        GROUP_CURATORIAL, ("flag",),
        lambda: host._register_facet(host._make_single("flag", [
            (tr("Any"), None),
            (tr("Flagged"), True),
            (tr("Not flagged"), False),
        ]))))

    # Event-level qualifiers (spec/86). Inventory comes from
    # ``available_event_types`` etc. (slice 2) — fixed-vocab dims still go
    # through the adaptive editor so a vocabulary that grows past
    # INLINE_PICKER_THRESHOLD picks up the picker automatically.
    # Filter-key naming is plural (event_types) to match the
    # camera_ids / lens_models / country_codes pattern.
    reg(FilterDimension(
        "event_type", tr("Event type"),
        GROUP_EVENT, ("event_types",),
        lambda: host._register_facet(host._make_multi("event_types"))))
    reg(FilterDimension(
        "event_subtype", tr("Event subtype"),
        GROUP_EVENT, ("event_subtypes",),
        lambda: host._register_facet(host._make_multi("event_subtypes"))))
    reg(FilterDimension(
        "scope", tr("Scope"),
        GROUP_EVENT, ("experience_types",),
        lambda: host._register_facet(host._make_multi("experience_types"))))
    reg(FilterDimension(
        "participants", tr("Participants"),
        GROUP_EVENT, ("participants",),
        lambda: host._register_facet(host._make_multi("participants"))))
    # spec/86 §5 — event-date overlap range. Kept BESIDE the existing
    # spec/32 §2b capture-date facet (they answer different questions);
    # the parameterized _make_date_range writes the right key pair.
    reg(FilterDimension(
        "event_date", tr("Event date"),
        GROUP_EVENT, ("event_from", "event_to"),
        lambda: host._register_facet(
            host._make_date_range("event_from", "event_to"))))

    # Camera & lens
    reg(FilterDimension(
        "camera_ids", tr("Camera"),
        GROUP_CAMERA_LENS, ("camera_ids",),
        lambda: host._register_facet(host._make_multi("camera_ids"))))
    reg(FilterDimension(
        "lens_models", tr("Lens"),
        GROUP_CAMERA_LENS, ("lens_models",),
        lambda: host._register_facet(host._make_multi("lens_models"))))
    reg(FilterDimension(
        "flash", tr("Flash"),
        GROUP_CAMERA_LENS, ("flash_fired",),
        lambda: host._register_facet(host._make_single("flash_fired", [
            (tr("Any"), None),
            (tr("Flash fired"), True),
            (tr("No flash"), False),
        ]))))

    # Settings (exposure triangle + focal).
    reg(FilterDimension(
        "iso", tr("ISO"),
        GROUP_SETTINGS, ("iso_min", "iso_max"),
        lambda: host._register_facet(host._make_range(
            "iso_min", "iso_max",
            integer=True, lo=50, hi=409600, step=100))))
    reg(FilterDimension(
        "aperture", tr("Aperture (f/)"),
        GROUP_SETTINGS, ("aperture_min", "aperture_max"),
        lambda: host._register_facet(host._make_range(
            "aperture_min", "aperture_max",
            integer=False, lo=0.7, hi=64.0,
            step=0.1, decimals=1))))
    reg(FilterDimension(
        "shutter", tr("Shutter (s)"),
        GROUP_SETTINGS, ("shutter_min", "shutter_max"),
        lambda: host._register_facet(host._make_range(
            "shutter_min", "shutter_max",
            integer=False, lo=0.00001, hi=3600.0,
            step=0.1, decimals=3))))
    reg(FilterDimension(
        "focal", tr("Focal length (mm)"),
        GROUP_SETTINGS, ("focal_min", "focal_max"),
        lambda: host._register_facet(host._make_range(
            "focal_min", "focal_max",
            integer=False, lo=4.0, hi=2000.0,
            step=1.0, decimals=1))))

    # When & where
    reg(FilterDimension(
        "capture_date", tr("Capture date"),
        GROUP_WHEN_WHERE, ("capture_from", "capture_to"),
        lambda: host._register_facet(host._make_date_range())))
    reg(FilterDimension(
        "country_codes", tr("Country"),
        GROUP_WHEN_WHERE, ("country_codes",),
        lambda: host._register_facet(host._make_multi("country_codes"))))
    reg(FilterDimension(
        "cities", tr("City"),
        GROUP_WHEN_WHERE, ("cities",),
        lambda: host._register_facet(host._make_multi("cities"))))

    return out


# The dim_ids of the cross-event catalogue, in display order — useful for
# tests + future widgets to assert / iterate. spec/86 adds the Event group
# after Curatorial.
CROSS_EVENT_DIM_IDS: Tuple[str, ...] = (
    "styles", "media_type", "stars", "color_labels", "flag",
    "event_type", "event_subtype", "scope", "participants", "event_date",
    "camera_ids", "lens_models", "flash",
    "iso", "aperture", "shutter", "focal",
    "capture_date", "country_codes", "cities",
)


# spec/81 §2.1: the event-scope surface stays deliberately thin. The
# Cut dialog still pins ``#exported`` only; the user gets Style + media
# type as their facet narrowings — no city, no camera, no ISO range.
EVENT_SCOPE_DIM_IDS: Tuple[str, ...] = ("styles", "media_type")


def build_event_scope_catalogue(host: Any) -> Dict[str, FilterDimension]:
    """The thin event-scope catalogue (spec/81 §2.1): Style + media type
    only. Same factory shape as the cross-event catalogue so the same
    Add-filter shell + active row machinery applies; just fewer dims in
    the menu."""
    full = build_cross_event_catalogue(host)
    return {dim_id: full[dim_id] for dim_id in EVENT_SCOPE_DIM_IDS}


def build_catalogue_subset(
    host: Any, dim_ids: Iterable[str],
) -> Dict[str, FilterDimension]:
    """Generic helper — any dialog can pick the subset of dimensions
    relevant to it. Currently used by the event-scope shape but written
    to suit future trims (e.g. a tag-only sub-dialog)."""
    full = build_cross_event_catalogue(host)
    return {d: full[d] for d in dim_ids if d in full}


__all__ = [
    "FilterDimension",
    "GROUP_CURATORIAL",
    "GROUP_CAMERA_LENS",
    "GROUP_SETTINGS",
    "GROUP_WHEN_WHERE",
    "GROUP_ORDER",
    "group_label",
    "CROSS_EVENT_DIM_IDS",
    "EVENT_SCOPE_DIM_IDS",
    "build_cross_event_catalogue",
    "build_event_scope_catalogue",
    "build_catalogue_subset",
]
