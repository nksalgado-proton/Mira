"""F-011 — `core/standalone_select_copy.py` tests.

Pure engine work — no Qt, no real EXIF, just synthetic files +
synthetic journals. Verifies the load-bearing contracts:

* Only KEPT files are copied (Discarded / Candidate skipped).
* Style resolution: override > cached-auto > "uncategorized".
* Collision suffix: monotonically increments, handles re-runs.
* shutil.copy2 preserves the file content (never hardlinks).
* Per-file errors don't kill the run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.bucket_navigator_model import BucketNode
from core.cull_state import STATE_DISCARDED as STATE_SKIPPED, STATE_KEPT as STATE_PICKED
from core.standalone_cull_copy import (
    CopyItem,
    _resolve_collision,
    _split_suffix,
    build_copy_items,
    copy_kept,
)


def _make_file(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _photo_node(bucket_id: str, files: list[Path]) -> BucketNode:
    return BucketNode(
        kind="individual",
        bucket_id=bucket_id,
        title="t",
        files=tuple(files),
        default_state=STATE_SKIPPED,
        camera="DC-G9M2",
    )


# ── build_copy_items ────────────────────────────────────────────────


def test_build_copy_items_picks_only_kept_files(tmp_path):
    a = _make_file(tmp_path / "a.jpg")
    b = _make_file(tmp_path / "b.jpg")
    c = _make_file(tmp_path / "c.jpg")
    node = _photo_node("k", [a, b, c])
    journal_root = tmp_path / "_journal"
    journal_root.mkdir()
    # Set up the journal: a=kept, b=discarded, c=kept.
    from core.ingest_session import save_ingest_journal
    journal = {
        "marks": {
            "a.jpg": STATE_PICKED,
            "b.jpg": STATE_SKIPPED,
            "c.jpg": STATE_PICKED,
        },
    }
    save_ingest_journal(journal_root, journal)

    items = build_copy_items([(node, journal_root)])
    sources = sorted(it.source.name for it in items)
    assert sources == ["a.jpg", "c.jpg"]


def test_build_copy_items_routes_to_uncategorized_when_no_classification(
    tmp_path,
):
    a = _make_file(tmp_path / "a.jpg")
    node = _photo_node("k", [a])
    jr = tmp_path / "_journal"
    jr.mkdir()
    from core.ingest_session import save_ingest_journal
    save_ingest_journal(jr, {"marks": {"a.jpg": STATE_PICKED}})

    items = build_copy_items([(node, jr)])
    assert len(items) == 1
    assert items[0].style == "uncategorized"
    assert items[0].rel_dest == Path("uncategorized/a.jpg")


def test_build_copy_items_uses_per_photo_override(tmp_path):
    a = _make_file(tmp_path / "a.jpg")
    node = _photo_node("k", [a])
    jr = tmp_path / "_journal"
    jr.mkdir()
    from core.ingest_session import save_ingest_journal
    journal = {
        "marks": {"a.jpg": STATE_PICKED},
        "genre": {"a.jpg": "portrait"},        # _OVERRIDE_KEY
        "genre_auto": {"a.jpg": {"s": "macro", "r": True}},
    }
    save_ingest_journal(jr, journal)

    items = build_copy_items([(node, jr)])
    assert items[0].style == "portrait"


def test_build_copy_items_falls_back_to_cached_auto_when_no_override(
    tmp_path,
):
    a = _make_file(tmp_path / "a.jpg")
    node = _photo_node("k", [a])
    jr = tmp_path / "_journal"
    jr.mkdir()
    from core.ingest_session import save_ingest_journal
    from core.genre import _rules_version  # noqa: PLC2701

    # 00.090: cache entries must carry v + src stamps to be considered
    # current. Without them peek_auto_genre returns None and the
    # build_copy_items resolver falls back to "uncategorized".
    journal = {
        "marks": {"a.jpg": STATE_PICKED},
        "genre_auto": {"a.jpg": {
            "s": "wildlife", "r": False,
            "v": _rules_version("camera"), "src": "camera",
        }},
    }
    save_ingest_journal(jr, journal)

    items = build_copy_items([(node, jr)])
    assert items[0].style == "wildlife"


def test_build_copy_items_skips_unreadable_journal(tmp_path):
    """No journal at all → bucket contributes zero items, no
    crash. Caller might pass a journal_root that's never been
    written to; we treat that as 'nothing kept'."""
    a = _make_file(tmp_path / "a.jpg")
    node = _photo_node("k", [a])
    items = build_copy_items([(node, tmp_path / "nonexistent")])
    # Empty journal defaults to "all discarded" → no kept files.
    assert items == []


# ── copy_kept happy path ────────────────────────────────────────────


def test_copy_kept_writes_files_with_correct_style_subfolders(tmp_path):
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    a = _make_file(src_dir / "a.jpg", b"a-content")
    b = _make_file(src_dir / "b.jpg", b"b-content")
    items = [
        CopyItem(source=a, style="wildlife",
                 rel_dest=Path("wildlife/a.jpg")),
        CopyItem(source=b, style="landscape",
                 rel_dest=Path("landscape/b.jpg")),
    ]
    dest = tmp_path / "dest"
    result = copy_kept(items, dest)
    assert result.ok_count == 2
    assert (dest / "wildlife" / "a.jpg").exists()
    assert (dest / "wildlife" / "a.jpg").read_bytes() == b"a-content"
    assert (dest / "landscape" / "b.jpg").exists()
    assert result.errors == []
    assert result.skipped == []


def test_copy_kept_creates_destination_directories(tmp_path):
    src = _make_file(tmp_path / "src" / "a.jpg")
    items = [CopyItem(source=src, style="portrait",
                       rel_dest=Path("portrait/a.jpg"))]
    dest = tmp_path / "dest" / "nested" / "path"     # doesn't exist
    result = copy_kept(items, dest)
    assert result.ok_count == 1
    assert (dest / "portrait" / "a.jpg").exists()


def test_copy_kept_is_a_real_copy_not_a_hardlink(tmp_path):
    """Standalone-cull is cross-volume by spec; mutate the source
    afterwards and verify the destination is independent."""
    src = _make_file(tmp_path / "src" / "a.jpg", b"original")
    items = [CopyItem(source=src, style="wildlife",
                       rel_dest=Path("wildlife/a.jpg"))]
    dest = tmp_path / "dest"
    copy_kept(items, dest)
    # Modify source AFTER copying.
    src.write_bytes(b"mutated")
    # Destination should still have the original bytes.
    assert (dest / "wildlife" / "a.jpg").read_bytes() == b"original"


# ── Collision policy ────────────────────────────────────────────────


def test_copy_kept_appends_n_suffix_on_collision(tmp_path):
    src = _make_file(tmp_path / "src" / "a.jpg", b"new")
    dest_dir = tmp_path / "dest" / "wildlife"
    _make_file(dest_dir / "a.jpg", b"existing")
    items = [CopyItem(source=src, style="wildlife",
                       rel_dest=Path("wildlife/a.jpg"))]
    result = copy_kept(items, tmp_path / "dest")
    assert result.ok_count == 1
    assert (dest_dir / "a.jpg").read_bytes() == b"existing"
    assert (dest_dir / "a (1).jpg").read_bytes() == b"new"


def test_copy_kept_collision_chain_continues_monotonically(tmp_path):
    """Pre-populate ``a.jpg`` AND ``a (1).jpg``; the next copy
    should land at ``a (2).jpg``, not ``a (1) (1).jpg``."""
    src = _make_file(tmp_path / "src" / "a.jpg", b"new")
    dest_dir = tmp_path / "dest" / "wildlife"
    _make_file(dest_dir / "a.jpg", b"original")
    _make_file(dest_dir / "a (1).jpg", b"prior-rerun")
    items = [CopyItem(source=src, style="wildlife",
                       rel_dest=Path("wildlife/a.jpg"))]
    copy_kept(items, tmp_path / "dest")
    assert (dest_dir / "a (2).jpg").read_bytes() == b"new"
    # Make sure we didn't accidentally write to the wrong place.
    assert (dest_dir / "a.jpg").read_bytes() == b"original"
    assert (dest_dir / "a (1).jpg").read_bytes() == b"prior-rerun"


def test_resolve_collision_returns_input_when_free(tmp_path):
    target = tmp_path / "wildlife" / "a.jpg"        # doesn't exist
    assert _resolve_collision(target) == target


def test_split_suffix_handles_existing_n_marker():
    assert _split_suffix("IMG (2)") == ("IMG", 2)
    assert _split_suffix("IMG") == ("IMG", None)
    assert _split_suffix("IMG (12)") == ("IMG", 12)


# ── Failure handling ───────────────────────────────────────────────


def test_copy_kept_records_missing_source_in_skipped(tmp_path):
    """Source file vanished between build_copy_items and the copy.
    The engine records it in ``skipped``, doesn't raise."""
    src = tmp_path / "src" / "vanished.jpg"          # never created
    items = [CopyItem(source=src, style="wildlife",
                       rel_dest=Path("wildlife/vanished.jpg"))]
    result = copy_kept(items, tmp_path / "dest")
    assert result.ok_count == 0
    assert len(result.skipped) == 1
    assert result.skipped[0][0] == src


def test_copy_kept_continues_after_single_file_error(tmp_path):
    """One unreadable file shouldn't kill a multi-file batch."""
    ok_src = _make_file(tmp_path / "src" / "a.jpg", b"ok")
    bad_src = tmp_path / "src" / "missing.jpg"       # not created
    items = [
        CopyItem(source=bad_src, style="x",
                 rel_dest=Path("x/missing.jpg")),
        CopyItem(source=ok_src, style="wildlife",
                 rel_dest=Path("wildlife/a.jpg")),
    ]
    result = copy_kept(items, tmp_path / "dest")
    assert result.ok_count == 1
    assert (tmp_path / "dest" / "wildlife" / "a.jpg").exists()


# ── Progress callback ─────────────────────────────────────────────


def test_progress_callback_fires_per_item(tmp_path):
    src_dir = tmp_path / "src"
    sources = [
        _make_file(src_dir / f"{i}.jpg", b"data") for i in range(3)
    ]
    items = [
        CopyItem(source=s, style="x",
                 rel_dest=Path(f"x/{s.name}"))
        for s in sources
    ]
    calls: list[tuple[str, int, int]] = []
    copy_kept(items, tmp_path / "dest",
              progress=lambda msg, cur, tot: calls.append((msg, cur, tot)))
    assert len(calls) == 3
    assert [c[1] for c in calls] == [1, 2, 3]
    assert all(c[2] == 3 for c in calls)
