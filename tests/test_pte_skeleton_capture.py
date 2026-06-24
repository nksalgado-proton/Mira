"""Tests for spec/107 — skeleton capture flow.

The user customises the slideshow's style in PTE (one photo + one video
+ a small bottom overlay), saves it, and asks Mira to "use my template".
Mira reads the saved `.pte` and captures a content-void skeleton:

  * KEEPS the `[Main]` style + the three prototypes (photo slide, video
    slide, overlay `Text`);
  * STRIPS every real media path to placeholder markers, drops baked
    overlay `Text=` content, and removes dangling `VideoClip` rows
    (they'd point at nothing after regeneration).

The captured file is NOT a valid openable `.pte` — it's just a template.
The generator (`mira.shared.pte_project.generate`) consumes it and emits
a complete `.pte` per Cut export.
"""
from __future__ import annotations

import re
from pathlib import Path

from mira.shared.pte_project import (
    bundled_skeleton_path,
    capture_skeleton,
    load_skeleton,
    parse_skeleton,
)


def _example_pte() -> str:
    """The 1-photo + 1-video + overlay validation artefact (spec/107 §10).
    Loaded with the same loader the generator uses so CRLF is preserved."""
    src = Path(__file__).resolve().parent.parent / "PTE example" \
        / "photo and video example.pte"
    return load_skeleton(bundled_fallback=src)


# ── What the capture KEEPS ──────────────────────────────────────


def test_capture_preserves_main_style_block():
    """The `[Main]` section's style options (dissolve, navigation, font
    defaults, shadow) ride to the skeleton unchanged. Only the personal
    paths get sanitized."""
    captured = capture_skeleton(_example_pte())
    assert "AspectRatio=16-9" in captured
    assert "DefDuration=5000" in captured
    # The dissolve effect carries through.
    assert "object dissolve:dissolve" in captured
    # Global shadow + comment defaults.
    assert "Comment_FontName=Arial" in captured


def test_capture_keeps_photo_and_video_prototypes():
    """spec/107 §2 — the skeleton has a `:Image` slide and a `:Video`
    slide. Capture must leave both."""
    captured = capture_skeleton(_example_pte())
    parsed = parse_skeleton(captured)
    assert parsed.video_slide_idx is not None
    photo = parsed.sections[parsed.photo_slide_idx].body
    assert ":Image\r\n" in photo
    video = parsed.sections[parsed.video_slide_idx].body
    assert ":Video\r\n" in video


def test_capture_keeps_nested_text_overlay_prototype():
    """spec/107 §3.4 — the overlay style is authored, not hardcoded. The
    capture keeps the nested `:Text` object (size, position, font,
    shadow) so the generator can clone it onto every embedded-overlay
    slide."""
    captured = capture_skeleton(_example_pte())
    parsed = parse_skeleton(captured)
    photo = parsed.sections[parsed.photo_slide_idx].body
    text_blocks = re.findall(
        r"    object [^:\r\n]+:Text\r\n(?:[\s\S]*?)\r\n    end\r\n",
        photo)
    assert len(text_blocks) == 1
    block = text_blocks[0]
    # Style hints — these are the validated reference values
    # (spec/107 §3.4): small + bottom-anchored + centered.
    assert "FontName=Arial Narrow" in block
    assert "TextAlign=Center" in block
    assert "ShadowEnable=1" in block
    assert re.search(r"ScaleX=3\.9\d+", block)
    assert "Position=" in block
    # Y position ≈ 91.6 (bottom of canvas).
    m = re.search(r"Position=-?[\d.]+,([\d.]+)", block)
    assert m is not None
    assert 80 < float(m.group(1)) < 100, m.group(1)


def test_capture_keeps_one_music_item_template():
    """The user's fade-in/fade-out + volume settings ride into the
    skeleton. ONE item is kept — the generator emits N copies, one
    per audio track, at export time."""
    captured = capture_skeleton(_example_pte())
    items = re.findall(r"object Item\d+:TMusicItem", captured)
    assert len(items) == 1
    # Fade values (the user's authored ones from the example: 4000ms).
    assert "FadeIn=4000" in captured
    assert "FadeOut=4000" in captured


# ── What the capture STRIPS ────────────────────────────────────


def test_capture_strips_real_image_paths():
    """No leftover `D:\\Photos\\…` paths — the photo prototype's
    `ImageName=` / `Picture=` lines are reduced to placeholder
    markers."""
    captured = capture_skeleton(_example_pte())
    # No personal photo path leaks through.
    assert "D:\\Photos" not in captured
    # The placeholders carry through.
    assert "ImageName={photo_path}" in captured
    assert "Picture={photo_path}" in captured


def test_capture_strips_real_video_paths():
    """Video slide's `FileName=` + `Duration=` reduce to placeholders.
    Hex backslash + lowercase drive letter variants also get
    swallowed (the example uses both `D:` and `d:` forms)."""
    captured = capture_skeleton(_example_pte())
    assert "d:\\photos" not in captured.lower()
    assert "FileName={video_path}" in captured
    assert "Duration={video_duration}" in captured


def test_capture_strips_real_music_path():
    captured = capture_skeleton(_example_pte())
    # No leftover audio path.
    assert "chill-travel" not in captured
    assert ".mp3" not in captured
    assert "FileName={audio_path}" in captured
    assert "Duration={audio_duration}" in captured


def test_capture_drops_dangling_video_clips():
    """The example's `[Tracks]` block carries one `VideoClip` (matches
    its single video slide). Capture KEEPS that one — the generator
    will strip it again at export time. A captured project with no
    video at all keeps no clips."""
    captured = capture_skeleton(_example_pte())
    parsed = parse_skeleton(captured)
    for s in parsed.sections:
        if s.name != "Tracks":
            continue
        clips = re.findall(
            r"object [^:\r\n]+:VideoClip\r\n", s.body)
        # The example has one video slide → one clip survives capture.
        assert len(clips) == 1
        break
    else:
        raise AssertionError("no [Tracks] section in captured skeleton")


def test_capture_strips_personal_overlay_text_content():
    """spec/107 §2 — baked `Text=` content is replaced with a marker so
    a hand-read skeleton has no personal data. (The recognition is
    structural, so the marker text doesn't matter — but a clean
    skeleton is a feature, not a bug.)"""
    captured = capture_skeleton(_example_pte())
    assert "Placeholder for the Camera, Exposure overlays" not in captured
    # The marker carries through.
    assert 'Text="{overlay}"' in captured


def test_capture_drops_project_paths_and_personal_metadata():
    """`ImagesFolder`, `ProjectFilePath`, `projectname` and the leftover
    `opt_vidmp4fn` shouldn't carry the user's home directory into a
    bundled / shared skeleton."""
    captured = capture_skeleton(_example_pte())
    assert "D:\\Projetos_Nelson" not in captured
    assert "ImagesFolder={images_folder}" in captured
    assert "ProjectFilePath={project_file_path}" in captured
    assert "projectname=mira_skeleton" in captured
    # opt_vidmp4fn is cleared to empty (the `\r` between the value
    # and the line break means the anchor needs to allow the `\r`).
    assert re.search(r"^opt_vidmp4fn=\r?\n", captured, re.MULTILINE)


# ── End-to-end: captured skeleton drives the generator ────────


def test_captured_skeleton_is_parsable(tmp_path):
    """The capture's output is consumable by parse_skeleton — i.e. the
    generator can read what capture wrote. This is the round-trip
    that backs the "Customize my PTE template" feature."""
    captured = capture_skeleton(_example_pte())
    parsed = parse_skeleton(captured)
    assert parsed.photo_slide_idx is not None
    assert parsed.video_slide_idx is not None


def test_bundled_skeleton_matches_capture_shape():
    """spec/107 §2 — the shipped default is a hand-authored skeleton
    built from the example by the same stripping rules. Capture the
    example, parse the bundled skeleton, and confirm the same
    prototype shape (one image proto + one video proto + one nested
    Text overlay)."""
    captured = parse_skeleton(capture_skeleton(_example_pte()))
    bundled = parse_skeleton(load_skeleton(
        bundled_fallback=bundled_skeleton_path()))
    assert captured.video_slide_idx is not None
    assert bundled.video_slide_idx is not None
    # The bundled and captured skeletons agree on which prototype is
    # which (image first, video second after the [Main] + [Tracks]
    # headers).
    assert (captured.sections[captured.photo_slide_idx].name
            == bundled.sections[bundled.photo_slide_idx].name)
    assert (captured.sections[captured.video_slide_idx].name
            == bundled.sections[bundled.video_slide_idx].name)
    # Both expose a single nested Text overlay on the photo prototype.
    for skel in (captured, bundled):
        body = skel.sections[skel.photo_slide_idx].body
        texts = re.findall(
            r"    object [^:\r\n]+:Text\r\n(?:[\s\S]*?)\r\n    end\r\n",
            body)
        assert len(texts) == 1
