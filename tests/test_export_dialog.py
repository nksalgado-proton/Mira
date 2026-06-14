"""Tests for :class:`mira.ui.edited.export_dialog.ExportDialog` —
the 2026-06-10 port of the ancestor's export dialog (both Edit export
paths had been lazily importing the legacy ``ui.culler`` tree, which
does not exist in MC; the first real export click crashed), reshaped
the same evening to the house form grammar:

* titled QGroupBoxes (``FormFieldGroup``), never label-beside-input;
* every interactive control hinted;
* file type = JPEG | TIFF only — the rendered, edited photo. ORIGINAL
  is not offered here (grabbing originals belongs to Share).

Per its design the dialog is driven without ``exec()``: construct, poke
widgets, read accessors.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QGroupBox, QRadioButton

from core.cull_export import CollisionPolicy, ExportFileType
from mira.ui.edited.export_dialog import ExportChoice, ExportDialog


def test_defaults_round_trip(qapp, tmp_path):
    dlg = ExportDialog(tmp_path)
    c = dlg.choice()
    assert c.destination == tmp_path
    assert c.file_type == ExportFileType.JPEG
    assert c.jpeg_quality == 90
    assert c.collision == CollisionPolicy.UNIQUE      # non-destructive default


def test_original_is_not_offered(qapp, tmp_path):
    """Grabbing the original file belongs to Share (Nelson 2026-06-10) —
    the Edit export renders the edited photo, JPEG or TIFF."""
    dlg = ExportDialog(tmp_path)
    assert set(dlg._ft_buttons) == {ExportFileType.JPEG, ExportFileType.TIFF}
    # A caller passing ORIGINAL as the default falls back to JPEG.
    dlg2 = ExportDialog(tmp_path, default_file_type=ExportFileType.ORIGINAL)
    assert dlg2.choice().file_type == ExportFileType.JPEG


def test_form_grammar_titled_groups_and_hints(qapp, tmp_path):
    """The house rule the first port violated: inputs live in titled
    QGroupBoxes (FormFieldGroup), and every interactive control carries
    a tooltip."""
    dlg = ExportDialog(tmp_path, collision_probe=lambda dest: 1)
    groups = dlg.findChildren(QGroupBox)
    titles = {g.title() for g in groups}
    assert len(groups) == 3
    assert all(g.objectName() == "FormFieldGroup" for g in groups)
    assert titles == {"Destination", "File type", "Name collisions"}
    for rb in dlg.findChildren(QRadioButton):
        assert rb.toolTip(), rb.text()
    assert dlg._dest_edit.toolTip() and dlg._q_spin.toolTip()


def test_quality_spin_follows_jpeg_radio(qapp, tmp_path):
    dlg = ExportDialog(tmp_path)
    assert dlg._q_spin.isEnabled()
    dlg._ft_buttons[ExportFileType.TIFF].setChecked(True)
    assert not dlg._q_spin.isEnabled()
    assert dlg.choice().file_type == ExportFileType.TIFF


def test_collision_section_appears_only_on_real_collisions(qapp, tmp_path):
    dlg = ExportDialog(tmp_path, collision_probe=lambda dest: 0)
    assert not dlg._coll_box.isVisibleTo(dlg)
    dlg2 = ExportDialog(tmp_path, collision_probe=lambda dest: 3)
    assert dlg2._coll_box.isVisibleTo(dlg2)
    assert "3" in dlg2._coll_label.text()
    dlg2._rb_override.setChecked(True)
    assert dlg2.choice().collision == CollisionPolicy.OVERRIDE


def test_ok_disabled_on_empty_destination(qapp, tmp_path):
    dlg = ExportDialog(tmp_path)
    assert dlg._ok.isEnabled()
    dlg._dest_edit.setText("")
    assert not dlg._ok.isEnabled()
    # Accept with an empty destination is a no-op (no snapshot taken).
    dlg._on_accept()
    assert dlg._snapshot is None


def test_accept_snapshots_the_choice(qapp, tmp_path):
    dlg = ExportDialog(tmp_path)
    dlg._q_spin.setValue(77)
    dlg._on_accept()
    snap = dlg.choice()
    assert isinstance(snap, ExportChoice)
    assert snap.jpeg_quality == 77
    # The snapshot is frozen — later widget churn doesn't leak in.
    dlg._q_spin.setValue(99)
    assert dlg.choice().jpeg_quality == 77


def test_no_stale_legacy_import_remains():
    """The crash regression pin: no Edit module reaches into the
    ancestor's ``ui.`` tree."""
    edited = Path(__file__).resolve().parent.parent / (
        "mira/ui/edited")
    offenders = [
        p.name for p in edited.glob("*.py")
        if "from ui." in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
