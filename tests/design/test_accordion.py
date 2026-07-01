"""Unit tests for the spec/162 Slice 2 accordion primitives.

Covers :class:`AccordionSection`, :class:`RecipeContainer`, and the
:class:`StrictAccordionGroup` arbitrator. The primitives have no
callers yet (Slice 3 wires them into ``NewCutDialog``); these tests
are the only consumers.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from mira.ui.design.accordion import (
    AccordionSection,
    RecipeContainer,
    StrictAccordionGroup,
)


# ─────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────
def _make_section(
    title: str = "Section",
    summary: str = "",
    parent: QWidget | None = None,
) -> AccordionSection:
    """Build a section with a labelled content widget for inspection."""
    content = QLabel(f"content-of-{title}")
    return AccordionSection(title, content, summary=summary, parent=parent)


def _click(widget: QWidget) -> None:
    """Simulate a left-click at the widget's centre.

    Uses ``QTest.mouseClick`` (real press + release event pair) so the
    ``mousePressEvent`` / ``mouseReleaseEvent`` overrides on
    ``_AccordionHeader`` run end-to-end.
    """
    QTest.mouseClick(
        widget, Qt.MouseButton.LeftButton, pos=widget.rect().center()
    )


# ─────────────────────────────────────────────────────────────────
# AccordionSection
# ─────────────────────────────────────────────────────────────────
def test_section_defaults_to_collapsed(qapp) -> None:
    s = _make_section()
    assert s.is_expanded() is False
    assert s.property("expanded") == "false"
    assert s.content().isVisibleTo(s) is False


def test_set_expanded_true_flips_property_and_shows_content(qapp) -> None:
    s = _make_section()
    s.show()  # top-level: content visibility flips to True on expand
    s.set_expanded(True)
    assert s.is_expanded() is True
    assert s.property("expanded") == "true"
    assert s.content().isVisibleTo(s) is True


def test_set_expanded_fires_toggled_only_on_change(qapp) -> None:
    s = _make_section()
    fired: list[bool] = []
    s.toggled.connect(fired.append)

    s.set_expanded(True)
    s.set_expanded(True)  # no-op: same state
    s.set_expanded(False)
    s.set_expanded(False)  # no-op

    assert fired == [True, False]


def test_header_click_toggles_bare_section(qapp) -> None:
    s = _make_section()
    s.show()
    fired: list[bool] = []
    s.toggled.connect(fired.append)

    _click(s.header())
    assert s.is_expanded() is True
    _click(s.header())
    assert s.is_expanded() is False
    assert fired == [True, False]


def test_summary_provider_updates_chip(qapp) -> None:
    s = _make_section(summary="initial")
    assert s.summary_text() == "initial"

    s.set_summary("Collection · 137 files")
    assert s.summary_text() == "Collection · 137 files"

    # Empty summary hides the chip; non-empty shows it again.
    s.set_summary("")
    assert s.summary_text() == ""

    s.set_summary("Format · 16:9 · 3 min")
    assert s.summary_text() == "Format · 16:9 · 3 min"


def test_title_text_readable(qapp) -> None:
    s = _make_section(title="① Collection")
    assert s.title_text() == "① Collection"


def test_content_is_reparented_into_section(qapp) -> None:
    content = QLabel("body")
    s = AccordionSection("T", content)
    assert content.parent() is s


# ─────────────────────────────────────────────────────────────────
# RecipeContainer
# ─────────────────────────────────────────────────────────────────
def test_recipe_container_default_name(qapp) -> None:
    c = RecipeContainer()
    assert c.recipe_name() == "(new · unsaved)"


def test_recipe_container_set_recipe_name(qapp) -> None:
    c = RecipeContainer("Sunday best")
    assert c.recipe_name() == "Sunday best"

    c.set_recipe_name("Client preview")
    assert c.recipe_name() == "Client preview"


def test_recipe_container_object_names(qapp) -> None:
    c = RecipeContainer()
    assert c.objectName() == "RecipeContainer"
    assert c.header_widget().objectName() == "RecipeContainerHeader"


def test_recipe_container_add_section(qapp) -> None:
    c = RecipeContainer()
    s1 = _make_section("① Collection")
    s2 = _make_section("② Format")
    c.add_section(s1)
    c.add_section(s2)

    assert c.sections() == [s1, s2]
    assert s1.parent() is c._body
    assert s2.parent() is c._body


def test_recipe_container_injects_load_and_save_buttons(qapp) -> None:
    load = QPushButton("Load Recipe…")
    save = QPushButton("Save as Recipe…")
    c = RecipeContainer(load_button=load, save_button=save)

    assert load.parent() is c.header_widget()
    assert save.parent() is c.header_widget()


def test_recipe_container_bordered_true_paints_the_frame(qapp) -> None:
    """spec/162 relayout D — the default (`bordered=True`) keeps the
    `#RecipeContainer` + `#RecipeContainerHeader` QSS roles so any
    external caller sees the framed treatment unchanged."""
    c = RecipeContainer()
    assert c.objectName() == "RecipeContainer"
    assert c.header_widget().objectName() == "RecipeContainerHeader"


def test_recipe_container_bordered_false_drops_the_frame(qapp) -> None:
    """spec/162 relayout D — `bordered=False` drops the object names
    on the outer frame + the header row so the QSS card2 fill +
    accent_soft border + hairline header divider don't paint. The
    widget still owns the same layout structure + header widgets +
    body layout — only the visual chrome retires."""
    load = QPushButton("Load Recipe…")
    save = QPushButton("Save as Recipe…")
    c = RecipeContainer(
        load_button=load, save_button=save, bordered=False)
    assert c.objectName() == ""
    assert c.header_widget().objectName() == ""
    # Load / Save buttons still land in the header row.
    assert load.parent() is c.header_widget()
    assert save.parent() is c.header_widget()


# ─────────────────────────────────────────────────────────────────
# StrictAccordionGroup
# ─────────────────────────────────────────────────────────────────
def test_group_expands_the_initial_section(qapp) -> None:
    s1 = _make_section("① Collection")
    s2 = _make_section("② Format")
    g = StrictAccordionGroup([s1, s2])

    assert g.expanded_index() == 0
    assert s1.is_expanded() is True
    assert s2.is_expanded() is False


def test_group_respects_initially_expanded_index(qapp) -> None:
    s1 = _make_section("A")
    s2 = _make_section("B")
    s3 = _make_section("C")
    g = StrictAccordionGroup(
        [s1, s2, s3], initially_expanded=2
    )

    assert g.expanded_index() == 2
    assert s3.is_expanded() is True
    assert s1.is_expanded() is False
    assert s2.is_expanded() is False


def test_clicking_collapsed_section_expands_it_and_collapses_others(
    qapp,
) -> None:
    s1 = _make_section("A")
    s2 = _make_section("B")
    g = StrictAccordionGroup([s1, s2])
    s1.show()
    s2.show()

    _click(s2.header())

    assert g.expanded_index() == 1
    assert s1.is_expanded() is False
    assert s2.is_expanded() is True


def test_clicking_expanded_section_is_a_no_op_by_default(qapp) -> None:
    """Spec/162 §4.5: the strict accordion refuses the all-collapsed
    state — clicking the currently-expanded header stays expanded."""
    s1 = _make_section("A")
    s2 = _make_section("B")
    g = StrictAccordionGroup([s1, s2])
    s1.show()
    s2.show()

    _click(s1.header())  # s1 is already the expanded one

    assert g.expanded_index() == 0
    assert s1.is_expanded() is True
    assert s2.is_expanded() is False


def test_allow_all_collapsed_lets_expanded_section_collapse(qapp) -> None:
    """The `allow_all_collapsed=True` opt-in lets the group enter the
    all-collapsed state — clicking the currently-expanded header collapses
    it and no peer takes over."""
    s1 = _make_section("A")
    s2 = _make_section("B")
    g = StrictAccordionGroup(
        [s1, s2], allow_all_collapsed=True
    )
    s1.show()
    s2.show()

    _click(s1.header())

    assert g.expanded_index() == -1
    assert s1.is_expanded() is False
    assert s2.is_expanded() is False


def test_allow_all_collapsed_with_initial_negative_starts_collapsed(
    qapp,
) -> None:
    s1 = _make_section("A")
    s2 = _make_section("B")
    g = StrictAccordionGroup(
        [s1, s2], allow_all_collapsed=True, initially_expanded=-1
    )

    assert g.expanded_index() == -1
    assert s1.is_expanded() is False
    assert s2.is_expanded() is False


def test_group_switches_expansion_when_a_third_section_is_clicked(
    qapp,
) -> None:
    s1 = _make_section("A")
    s2 = _make_section("B")
    s3 = _make_section("C")
    g = StrictAccordionGroup(
        [s1, s2, s3], initially_expanded=1
    )
    for s in (s1, s2, s3):
        s.show()

    _click(s3.header())

    assert g.expanded_index() == 2
    assert s1.is_expanded() is False
    assert s2.is_expanded() is False
    assert s3.is_expanded() is True


def test_group_with_no_sections_is_a_no_op(qapp) -> None:
    g = StrictAccordionGroup([])
    assert g.sections() == []
    assert g.expanded_index() == -1


def test_group_rebalance_does_not_recurse(qapp) -> None:
    """Regression: the guard flag in StrictAccordionGroup must stop the
    `toggled` cascade after one hop — a click that fires peer collapses
    must not re-fire on the original source."""
    s1 = _make_section("A")
    s2 = _make_section("B")
    g = StrictAccordionGroup([s1, s2])

    fired_s1: list[bool] = []
    fired_s2: list[bool] = []
    s1.toggled.connect(fired_s1.append)
    s2.toggled.connect(fired_s2.append)

    s1.show()
    s2.show()
    _click(s2.header())

    assert fired_s1 == [False]  # s1 collapsed once
    assert fired_s2 == [True]   # s2 expanded once
