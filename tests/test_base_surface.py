"""Tests for the two unified base surfaces (spec/42 two-base pivot).

Covers the region API contract on BasePickSurface + BaseEditSurface
+ the canonical affordances. Behavioural tests for individual surfaces
(after refactor) live in their own files."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from mira.ui.base.surface import (
    BasePickSurface,
    BaseEditSurface,
    NavRow,
    back_button,
    feature_toggle,
    help_button,
    info_label,
    kd_pill,
    populate_nav_row,
    primary_action,
    state_chip,
)


# ── Test fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def app(qapp):
    """Use the shared QApplication from conftest. ``qapp`` is auto-provided
    by pytest-qt; aliased so the test name reads naturally."""
    return qapp


# ── BasePickSurface region API ───────────────────────────────────────────────


class TestBaseSelectSurfaceRegions:

    def test_constructs_with_all_cull_regions_plus_media(self, app):
        # Cull regions: top_bar · state_bar · media · compact_row · tools · nav
        s = BasePickSurface()
        assert s.top_bar.isHidden() is False
        assert s.state_bar.isHidden() is False
        assert s.compact_row.isHidden() is False
        assert s.tools.isHidden() is False
        assert s.nav.isHidden() is False
        # Media host exists too (not directly addressable as a region).
        assert s._media_host.isHidden() is False

    def test_outer_layout_has_small_card_margins_and_inter_region_spacing(self, app):
        # spec/42 Nelson 2026-06-04 — 4 px outer margins + 4 px between
        # regions so each region renders as a light-bordered card.
        s = BasePickSurface()
        outer = s.layout()
        assert outer.contentsMargins().top() == 4
        assert outer.contentsMargins().left() == 4
        assert outer.contentsMargins().right() == 4
        assert outer.contentsMargins().bottom() == 4
        assert outer.spacing() == 4

    def test_horizontal_region_margins_are_canonical(self, app):
        s = BasePickSurface()
        # TOP_BAR / STATE_BAR / NAV use the canonical 12/6/12/6 margin.
        for w in (s.top_bar, s.state_bar, s.nav):
            m = w.layout().contentsMargins()
            assert (m.left(), m.top(), m.right(), m.bottom()) == (12, 6, 12, 6)

    def test_compact_row_uses_tighter_vertical_margins(self, app):
        # COMPACT_ROW compresses vertical margin (4/4 instead of 6/6) —
        # the timeline is a self-padded widget so the row doesn't need
        # extra space. (Renamed from SCRUB 2026-06-06.)
        s = BasePickSurface()
        m = s.compact_row.layout().contentsMargins()
        assert (m.left(), m.top(), m.right(), m.bottom()) == (12, 4, 12, 4)

    def test_set_region_visible_collapses_normal_region(self, app):
        """Most regions collapse to 0 when hidden (state_bar, tools, …)."""
        s = BasePickSurface()
        assert s.state_bar.isHidden() is False
        s.set_region_visible("state_bar", False)
        assert s.state_bar.isHidden() is True
        # And back.
        s.set_region_visible("state_bar", True)
        assert s.state_bar.isHidden() is False

    def test_compact_row_is_reserved_not_collapsed_when_hidden(self, app):
        """compact_row uses the reservation pattern (spec/42 §"Region
        reservation"): set_region_visible('compact_row', False) keeps the
        48 px slot in the layout and only toggles the CHILDREN's
        visibility. This anchors the MEDIA position across surfaces that
        do vs don't populate the row (photo ↔ video)."""
        s = BasePickSurface()
        # Add some children (the surface would normally populate these).
        child_a = QLabel("a")
        child_b = QLabel("b")
        s.compact_row.layout().addWidget(child_a)
        s.compact_row.layout().addWidget(child_b)
        # Both children visible by default.
        assert child_a.isHidden() is False
        assert child_b.isHidden() is False
        # Hiding compact_row hides the children but NOT the region itself.
        s.set_region_visible("compact_row", False)
        assert s.compact_row.isHidden() is False   # region stays in layout
        assert child_a.isHidden() is True
        assert child_b.isHidden() is True
        # And back.
        s.set_region_visible("compact_row", True)
        assert child_a.isHidden() is False
        assert child_b.isHidden() is False

    def test_set_region_visible_rejects_unknown_name(self, app):
        s = BasePickSurface()
        with pytest.raises(ValueError, match="unknown region"):
            s.set_region_visible("media", False)
        with pytest.raises(ValueError):
            s.set_region_visible("nonsense", False)
        # Process-only regions aren't valid on a cull surface.
        with pytest.raises(ValueError):
            s.set_region_visible("tools_panel", False)

    def test_region_returns_the_named_container(self, app):
        s = BasePickSurface()
        assert s.region("top_bar") is s.top_bar
        assert s.region("state_bar") is s.state_bar
        assert s.region("compact_row") is s.compact_row
        assert s.region("tools") is s.tools
        assert s.region("nav") is s.nav
        with pytest.raises(ValueError):
            s.region("media")


# ── BasePickSurface MEDIA handling ───────────────────────────────────────────


class TestBaseSelectSurfaceMedia:

    def test_set_media_installs_widget_with_stretch(self, app):
        s = BasePickSurface()
        photo = QLabel("photo")
        s.set_media(photo)
        # The widget is parented into the media host and added with stretch.
        assert photo.parent() is s._media_host
        assert s._media_widget is photo

    def test_set_media_replaces_prior_widget(self, app):
        s = BasePickSurface()
        old = QLabel("old")
        new = QLabel("new")
        s.set_media(old)
        s.set_media(new)
        assert s._media_widget is new
        # Old is detached.
        assert old.parent() is None


# ── BaseEditSurface region API ────────────────────────────────────────────


class TestBaseEditSurfaceRegions:
    """BaseEditSurface — the Process-family base. TOOLS_PANEL above
    MEDIA (the AdjustmentSurface shape); no STATE_BAR; no TOOLS-below."""

    def test_constructs_with_process_regions_plus_media(self, app):
        # Process regions: top_bar · tools_panel · media · compact_row · nav
        s = BaseEditSurface()
        assert s.top_bar.isHidden() is False
        assert s.tools_panel.isHidden() is False
        assert s.compact_row.isHidden() is False
        assert s.nav.isHidden() is False
        assert s._media_host.isHidden() is False

    def test_has_no_state_bar_or_tools(self, app):
        # Cull-only regions don't exist on the Process base.
        s = BaseEditSurface()
        assert not hasattr(s, "state_bar")
        assert not hasattr(s, "tools")

    def test_layout_order_is_top_then_tools_panel_then_media(self, app):
        """The Process base puts TOOLS_PANEL ABOVE MEDIA (unlike Cull
        which puts TOOLS BELOW). Verify by walking the outer layout."""
        s = BaseEditSurface()
        outer = s.layout()
        widgets_in_order = []
        for i in range(outer.count()):
            item = outer.itemAt(i)
            if item is not None and item.widget() is not None:
                widgets_in_order.append(item.widget())
        # Expected: TOP_BAR · TOOLS_PANEL · MEDIA · COMPACT_ROW · NAV
        assert widgets_in_order == [
            s.top_bar, s.tools_panel, s._media_host, s.compact_row, s.nav]

    def test_set_region_visible_rejects_cull_only_regions(self, app):
        s = BaseEditSurface()
        with pytest.raises(ValueError):
            s.set_region_visible("state_bar", False)
        with pytest.raises(ValueError):
            s.set_region_visible("tools", False)

    def test_set_media_replaces_prior_widget(self, app):
        # Same API shape as BasePickSurface — inherited from _SurfaceCore.
        s = BaseEditSurface()
        old = QLabel("old")
        new = QLabel("new")
        s.set_media(old)
        s.set_media(new)
        assert s._media_widget is new
        assert old.parent() is None

    def test_set_media_state_re_polishes(self, app):
        s = BaseEditSurface()
        # Process surfaces use the border for status only — "picked" =
        # exported, None = not exported.
        s.set_media_state("picked")
        assert s._media_host.property("state") == "picked"
        s.set_media_state(None)
        assert s._media_host.property("state") in (None, "")


# ── BasePickSurface layout order ─────────────────────────────────────────────


class TestBaseSelectSurfaceLayout:

    def test_layout_order_is_canonical(self, app):
        """Cull base: TOP_BAR · STATE_BAR · MEDIA · COMPACT_ROW · TOOLS · NAV."""
        s = BasePickSurface()
        outer = s.layout()
        widgets_in_order = []
        for i in range(outer.count()):
            item = outer.itemAt(i)
            if item is not None and item.widget() is not None:
                widgets_in_order.append(item.widget())
        assert widgets_in_order == [
            s.top_bar, s.state_bar, s._media_host,
            s.compact_row, s.tools, s.nav]


# ── Canonical affordances ────────────────────────────────────────────────────


class TestCanonicalAffordances:

    def test_back_button_canonical_label_and_role(self, app):
        # Nelson 2026-06-12: plain "Back" everywhere, no glyph. The
        # earlier "← Back" arrow audit landed BackButton role; the
        # second audit dropped the arrow from the label.
        b = back_button()
        assert b.text() == "Back"
        assert b.objectName() == "BackButton"
        assert b.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert b.cursor().shape() == Qt.CursorShape.PointingHandCursor

    def test_back_button_text_can_be_overridden_but_role_is_fixed(self, app):
        # The text override stays available — the bucket navigator's
        # leave-the-phase button still threads its label through the
        # config — but new call sites take the default.
        b = back_button("Leave editor")
        assert b.text() == "Leave editor"
        assert b.objectName() == "BackButton"   # role is fixed

    def test_info_label_role_and_default(self, app):
        lbl = info_label()
        assert lbl.objectName() == "InfoLabel"
        assert lbl.text() == ""
        lbl2 = info_label("Day 2 · 4/12")
        assert lbl2.text() == "Day 2 · 4/12"

    def test_kd_pill_is_checkable_and_canonical(self, app):
        pill = kd_pill()
        assert pill.objectName() == "PDPill"
        assert pill.isCheckable() is True
        assert pill.focusPolicy() == Qt.FocusPolicy.NoFocus

    def test_primary_action_canonical_role(self, app):
        b = primary_action("Save → copy kept")
        assert b.objectName() == "PrimaryAction"
        assert b.text() == "Save → copy kept"
        assert b.cursor().shape() == Qt.CursorShape.PointingHandCursor

    def test_help_button_canonical(self, app):
        h = help_button()
        assert h.text() == "?"
        assert h.objectName() == "HelpButton"

    def test_feature_toggle_canonical_and_checked_param(self, app):
        t = feature_toggle("Peaking", checked=True)
        assert t.objectName() == "FeatureToggle"
        assert t.isCheckable() is True
        assert t.isChecked() is True
        # Default unchecked.
        t2 = feature_toggle("Crop")
        assert t2.isChecked() is False

    def test_state_chip_canonical(self, app):
        c = state_chip("✓ Exported")
        assert c.objectName() == "StateChip"
        assert c.text() == "✓ Exported"


# ── populate_nav_row ─────────────────────────────────────────────────────────


class TestPopulateNavRow:

    def test_with_buckets_returns_four_buttons(self, app):
        s = BasePickSurface()
        row = populate_nav_row(s)
        assert isinstance(row, NavRow)
        assert row.prev_bucket is not None
        assert row.prev_bucket.text() == "⏮ Bucket"
        assert row.prev.text() == "← Previous"
        assert row.next.text() == "Next →"
        assert row.next_bucket is not None
        assert row.next_bucket.text() == "Bucket ⏭"

    def test_without_buckets_returns_none_for_edge_buttons(self, app):
        s = BasePickSurface()
        row = populate_nav_row(s, with_buckets=False)
        assert row.prev_bucket is None
        assert row.next_bucket is None
        assert row.prev.text() == "← Previous"
        assert row.next.text() == "Next →"

    def test_canonical_object_names(self, app):
        s = BasePickSurface()
        row = populate_nav_row(s)
        assert row.prev_bucket.objectName() == "NavEdgeButton"
        assert row.prev.objectName() == "NavStepButton"
        assert row.next.objectName() == "NavStepButton"
        assert row.next_bucket.objectName() == "NavEdgeButton"

    def test_centre_widget_is_added_between_stretches(self, app):
        s = BasePickSurface()
        centre = feature_toggle("Grid")
        populate_nav_row(s, centre_widget=centre)
        # The centre widget is in the NAV layout.
        layout = s.nav.layout()
        found = False
        for i in range(layout.count()):
            it = layout.itemAt(i)
            if it is not None and it.widget() is centre:
                found = True
                break
        assert found, "centre_widget should be added to the nav layout"

    def test_nav_layout_contains_expected_widgets_in_order(self, app):
        s = BasePickSurface()
        row = populate_nav_row(s)
        layout = s.nav.layout()
        widget_seq = []
        for i in range(layout.count()):
            it = layout.itemAt(i)
            if it is None:
                continue
            w = it.widget()
            if w is not None:
                widget_seq.append(w)
        # Expected order: prev_bucket, prev, next, next_bucket (the stretches
        # in between aren't widgets).
        assert widget_seq == [
            row.prev_bucket, row.prev, row.next, row.next_bucket]

    def test_populate_nav_row_works_on_process_surface_too(self, app):
        """The standard nav row builds on BaseEditSurface — same nav
        contract across both bases."""
        s = BaseEditSurface()
        row = populate_nav_row(s, with_buckets=False)
        assert row.prev.text() == "← Previous"
        assert row.next.text() == "Next →"


# ── Surface composition smoke test ───────────────────────────────────────────


class TestComposition:

    def test_a_minimal_cull_surface_can_be_built_with_only_top_media_nav(self, app):
        """QuickSweepPage-shape: TOP + MEDIA + NAV; STATE / TOOLS hidden;
        COMPACT_ROW reserved-but-empty (visible region, no children)."""
        s = BasePickSurface()
        s.set_region_visible("state_bar", False)
        s.set_region_visible("compact_row", False)   # reservation: no-op when empty
        s.set_region_visible("tools", False)
        # Populate.
        s.top_bar.layout().addWidget(back_button())
        s.top_bar.layout().addStretch(1)
        s.top_bar.layout().addWidget(primary_action("Save"))
        s.set_media(QLabel("photo"))
        populate_nav_row(s, with_buckets=False)
        # Truly hidden regions (state_bar, tools) are hidden.
        assert s.state_bar.isHidden() is True
        assert s.tools.isHidden() is True
        # COMPACT_ROW stays in the layout (reservation pattern) — it's the
        # MEDIA-anchoring slot. With no children, it renders no chrome.
        assert s.compact_row.isHidden() is False
        # Used regions are NOT hidden + populated.
        assert s.top_bar.isHidden() is False
        assert s.top_bar.layout().count() >= 3
        assert s.nav.isHidden() is False
        assert s.nav.layout().count() >= 2

    def test_a_full_cull_surface_uses_all_regions(self, app):
        """VideoPickPage-shape: every region visible."""
        s = BasePickSurface()
        s.top_bar.layout().addWidget(back_button())
        s.state_bar.layout().addWidget(kd_pill())
        s.set_media(QLabel("video"))
        s.compact_row.layout().addWidget(QLabel("timeline"))
        # TOOLS gets a row.
        tool_row = QWidget()
        from PyQt6.QtWidgets import QHBoxLayout
        h = QHBoxLayout(tool_row)
        h.addWidget(feature_toggle("Audio"))
        s.tools.layout().addWidget(tool_row)
        populate_nav_row(s)
        # All regions not hidden, all populated.
        for name in ("top_bar", "state_bar", "compact_row", "tools", "nav"):
            assert s.region(name).isHidden() is False
            assert s.region(name).layout().count() >= 1

    def test_a_minimal_process_surface_can_be_built(self, app):
        """ProcessPhoto-shape: TOP + TOOLS_PANEL + MEDIA + NAV."""
        s = BaseEditSurface()
        s.top_bar.layout().addWidget(back_button())
        s.top_bar.layout().addStretch(1)
        s.top_bar.layout().addWidget(primary_action("Export →"))
        # Tools panel populated (just a placeholder for the smoke test).
        s.tools_panel.layout().addWidget(QLabel("adjustments"))
        s.set_media(QLabel("photo"))
        populate_nav_row(s, with_buckets=False)
        # All Process regions exist + are visible.
        for name in ("top_bar", "tools_panel", "compact_row", "nav"):
            assert s.region(name).isHidden() is False
        assert s.top_bar.layout().count() >= 3
        assert s.tools_panel.layout().count() >= 1
