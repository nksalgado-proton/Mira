"""Shared table helpers — the app-wide standard for any ``QTableWidget`` (spec/05 §4b).

Nelson 2026-05-30: *"apply [make all columns resizable] always — it is a very good
addition."* Every table the new UI shows routes through :func:`make_columns_resizable` so
the behaviour is identical everywhere and never re-implemented per surface.
"""
from __future__ import annotations

from typing import Sequence

from PyQt6.QtWidgets import QHeaderView, QTableWidget


def make_columns_resizable(table: QTableWidget, *, widths: Sequence[int] = ()) -> None:
    """Make every column user-draggable (``QHeaderView.Interactive``); the trailing column
    stretches to fill the remaining width so there's no dead gap and content is never
    unreadably clipped with no way to widen it. ``widths`` seeds initial pixel widths for
    the leading columns (the last one stretches regardless)."""
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(True)
    for i, w in enumerate(widths):
        table.setColumnWidth(i, w)
