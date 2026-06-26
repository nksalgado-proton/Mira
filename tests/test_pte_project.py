"""Tests for spec/107 — the PTE `.pte` generator.

Given the bundled skeleton + a member list (photos + video + overlays) +
durations + aspect + audio tracks, verify the generator produces:

  * N `[Slide]` blocks with **unique** per-object GUIDs (photo + video
    bind by GUID, spec/107 §0);
  * photo slides repathed to the new files;
  * video slides emitted as `:Video` blocks with a matching `[Tracks]`
    `VideoClip` (shared `ClipGUID`, `MasterID` = the Cover Video's GUID,
    `StartSlideIdx` 0-based);
  * `[Times]` cumulative milliseconds with **clip-length** entries for
    video slides;
  * `opt_slidescount = N`;
  * `embedded` overlay → populated nested `Text` with style inherited;
  * `burn_in` / `off` overlays → nested `Text` stripped from every slide;
  * dangling `VideoClip` rows stripped from `[Tracks]`;
  * `[Main]` `AspectRatio` / `opt_scr_*` / `DefDuration` overridden from
    the Cut;
  * BOM + CRLF on the written file (the format PTE accepts).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mira.shared.pte_project import (
    DEFAULT_OUTPUT_NAME,
    DEFAULT_TRANSITION_MS,
    OVERLAY_BURN_IN, OVERLAY_EMBEDDED, OVERLAY_OFF,
    PteAudioTrack, PteMember,
    bundled_skeleton_path,
    fresh_guid,
    generate,
    generate_into_folder,
    load_skeleton,
    parse_skeleton,
    slideshow_target,
    write_pte,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def skel() -> str:
    """The bundled skeleton text (BOM stripped, CRLF preserved)."""
    return load_skeleton(bundled_fallback=bundled_skeleton_path())


@pytest.fixture
def members() -> list:
    """A mixed Cut: photo, video, photo-with-overlay."""
    return [
        PteMember(kind="photo", path=Path("C:/cut/001_opener.jpg")),
        PteMember(kind="video", path=Path("C:/cut/002_clip.mp4"),
                  duration_ms=13267),
        PteMember(kind="photo", path=Path("C:/cut/003_camera.jpg"),
                  overlay_text="ZX · 35mm · f/2.8 · 1/250 · ISO 200"),
    ]


@pytest.fixture
def tracks() -> list:
    return [
        PteAudioTrack(path=Path("C:/cut/audio/01_drive.mp3"),
                      duration_ms=60000),
        PteAudioTrack(path=Path("C:/cut/audio/02_chill.mp3"),
                      duration_ms=72500),
    ]


@pytest.fixture
def output(skel, members, tracks) -> str:
    """One canonical generate(): 16:9 / 6.0s, embedded overlays."""
    return generate(
        skel, members, tracks,
        aspect="16:9", photo_seconds=6.0,
        project_path=Path("C:/cut/slideshow.pte"),
        images_folder=Path("C:/cut"),
        overlay_mode=OVERLAY_EMBEDDED,
    )


# ── Helpers ──────────────────────────────────────────────────────


def _section(text: str, name: str) -> str:
    """Return the body of `[<name>]` (between its header and the next)."""
    m = re.search(rf"\[{re.escape(name)}\]\r\n([\s\S]*?)(?=\[[A-Za-z0-9_ ]+\]\r\n|\Z)", text)
    assert m is not None, f"no [{name}] section"
    return m.group(1)


def _kv(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}=([^\r\n]*)", text, re.MULTILINE)
    assert m is not None, f"no {key}= in text"
    return m.group(1)


_GUID = r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-" \
        r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}"


def _music_block(text: str) -> str:
    """Slice out the ``object Music:Music ... end`` block from a
    generated PTE text. Tolerant of the trailing-end-line indent so
    tests don't depend on exact whitespace shape."""
    m = re.search(
        r"object Music:Music\r\n(?:[\s\S]*?)\r\nend\r\n", text)
    assert m is not None, "no Music block"
    return m.group(0)


# ── Skeleton parsing ────────────────────────────────────────────


def test_skeleton_has_three_prototypes(skel):
    """spec/107 §2 — the skeleton carries a photo slide proto, a video
    slide proto and a nested overlay `Text` proto (recognised
    structurally inside the photo's image object)."""
    s = parse_skeleton(skel)
    assert s.sections[s.photo_slide_idx].name.startswith("Slide")
    assert s.video_slide_idx is not None
    photo_body = s.sections[s.photo_slide_idx].body
    # The nested Text overlay prototype is a child of the photo's
    # inner image object.
    text_blocks = re.findall(
        r"    object [^:\r\n]+:Text\r\n(?:[\s\S]*?)\r\n    end\r\n",
        photo_body)
    assert len(text_blocks) == 1
    # Style hints from spec/107 §3.4 — font/alignment/centered overlay.
    assert "FontName=Arial Narrow" in text_blocks[0]
    assert "TextAlign=Center" in text_blocks[0]


# ── Per-slide content ───────────────────────────────────────────


def test_three_slide_blocks_emitted(output, members):
    headers = re.findall(r"\[Slide\d+\]", output)
    assert headers == [f"[Slide{i+1}]" for i in range(len(members))]


def test_photo_slides_repathed(output, members):
    photo_paths = [_windows(m.path) for m in members if m.kind == "photo"]
    for path in photo_paths:
        assert f"ImageName={path}" in output
        assert f"Picture={path}" in output


def test_video_slide_emits_video_objects(output):
    """The video slide's Container objects are `:Video` (not `:Image`),
    carrying `FileName=` / `Duration=` / `ClipGUID=`."""
    slide2 = _section(output, "Slide2")
    assert ":Video\r\n" in slide2
    assert "FileName=C:\\cut\\002_clip.mp4" in slide2
    assert "Duration=13267" in slide2
    # Both Video objects share one ClipGUID (Cover + PlaceInto).
    clip_guids = re.findall(r"ClipGUID=" + _GUID, slide2)
    assert len(clip_guids) == 2
    assert clip_guids[0] == clip_guids[1]


def test_video_slide_has_matching_tracks_clip(output):
    """spec/107 §0 — a video member's `VideoClip` in `[Tracks]` carries
    the same `ClipGUID` AND `MasterID` = the Cover Video object's
    `GUID` AND `StartSlideIdx` = 0-based slide index."""
    slide2 = _section(output, "Slide2")
    # Pull the Cover Video object's GUID (the first :Video object in
    # the slide).
    m = re.search(r"  object [^:\r\n]+:Video\r\n    GUID=(" + _GUID + r")",
                  slide2)
    assert m is not None
    cover_guid = m.group(1)
    slide_clip_guid = re.search(r"ClipGUID=(" + _GUID + r")",
                                slide2).group(1)

    tracks = _section(output, "Tracks")
    # The Tracks block should have exactly one VideoClip — for our
    # one video member.
    clips = re.findall(
        r"object [^:\r\n]+:VideoClip\r\n([\s\S]*?)\r\nend\r\n", tracks)
    assert len(clips) == 1
    clip = clips[0]
    assert f"ClipGUID={slide_clip_guid}" in clip
    assert f"MasterID={cover_guid}" in clip
    # StartSlideIdx is 0-based — slide 2 in 1-based ⇒ index 1.
    assert "StartSlideIdx=1" in clip
    # Path + duration are present in absolute Windows form.
    assert "FileName=C:\\cut\\002_clip.mp4" in clip
    assert "Duration=13267" in clip


def test_dangling_video_clips_in_skeleton_stripped(output):
    """The skeleton's `[Tracks]` block carries one dangling `VideoClip`
    (left over from the example's 1-photo + 1-video project). After
    generation that clip is gone — only the freshly-emitted video
    members live in `[Tracks]`. We had one video member, expect one
    clip."""
    tracks = _section(output, "Tracks")
    clips = re.findall(r"object [^:\r\n]+:VideoClip\r\n", tracks)
    assert len(clips) == 1


def test_video_clip_caption_uses_filename_stem(output):
    tracks = _section(output, "Tracks")
    assert "ClipCaption=002_clip" in tracks


# ── GUID uniqueness ─────────────────────────────────────────────


def test_per_object_guids_are_unique(output):
    """spec/107 §0 — photo + video slides bind their bytes by GUID.
    Every clone gets fresh GUIDs so the new paths take effect. The
    expected duplicates are the ones the format requires:
      * the video slide's ClipGUID is shared by Cover + PlaceInto +
        `[Tracks]` VideoClip → 2 duplicates;
      * the `[Tracks]` VideoClip's MasterID is = the Cover Video's
        GUID → 1 duplicate.
    Three duplicates total; everything else must be unique."""
    guids = re.findall(_GUID, output)
    n_total = len(guids)
    n_unique = len(set(guids))
    duplicates = n_total - n_unique
    assert duplicates == 3, (n_total, n_unique, duplicates)


def test_no_skeleton_guids_leak_into_output(output, skel):
    """A fresh export must regenerate every object GUID — finding a
    skeleton GUID verbatim in the output would mean the new paths
    don't take effect. The only exception is the `StyleOptions=
    [{...}]` GUID, which is the style identifier and intentionally
    persistent (spec/107 §0)."""
    style_guids = set(re.findall(
        r"StyleOptions=\[(" + _GUID + r")\]", skel))
    skel_guids = set(re.findall(_GUID, skel)) - style_guids
    out_guids = set(re.findall(_GUID, output))
    # The output's GUIDs and the skeleton's GUIDs share at most the
    # StyleOptions one (already filtered).
    leaks = skel_guids & out_guids
    assert not leaks, leaks


# ── Overlays ────────────────────────────────────────────────────


def test_embedded_overlay_populates_nested_text(output):
    """Slide 3 has overlay text — the nested `Text="…"` line is set to
    the supplied string and the style around it is inherited verbatim
    from the skeleton."""
    slide3 = _section(output, "Slide3")
    assert 'Text="ZX · 35mm · f/2.8 · 1/250 · ISO 200"' in slide3
    # The skeleton style around the Text is preserved (size +
    # position + font + shadow — spec/107 §3.4).
    assert "FontName=Arial Narrow" in slide3
    assert "TextAlign=Center" in slide3
    assert "ScaleX=3.901931" in slide3
    assert "ShadowEnable=1" in slide3


def test_embedded_overlay_strips_text_when_member_has_none(output):
    """Slide 1 has no overlay text — its nested Text block is stripped
    so the slide doesn't carry leftover placeholder content."""
    slide1 = _section(output, "Slide1")
    assert ":Text\r\n" not in slide1


def test_burn_in_mode_strips_every_nested_text(skel, members, tracks):
    """burn_in mode — pixels carry the overlay; the slide must NOT
    layer a duplicate `Text` on top."""
    text = generate(skel, members, tracks,
                    aspect="16:9", photo_seconds=6.0,
                    project_path=Path("C:/cut/slideshow.pte"),
                    images_folder=Path("C:/cut"),
                    overlay_mode=OVERLAY_BURN_IN)
    for name in ("Slide1", "Slide2", "Slide3"):
        body = _section(text, name)
        assert ":Text\r\n" not in body


def test_off_mode_strips_every_nested_text(skel, members):
    """off mode — no overlay anywhere. Even when members carry overlay
    strings, off wins."""
    text = generate(skel, members, [],
                    aspect="16:9", photo_seconds=6.0,
                    project_path=Path("C:/cut/slideshow.pte"),
                    images_folder=Path("C:/cut"),
                    overlay_mode=OVERLAY_OFF)
    for name in ("Slide1", "Slide2", "Slide3"):
        body = _section(text, name)
        assert ":Text\r\n" not in body


# ── [Times] ─────────────────────────────────────────────────────


def test_times_block_cumulative_with_clip_length_videos(output):
    """spec/107 §0 + spec/150 §1 — photo slides count for the Cut's
    per-slide ms PLUS the transition time; video slides count for
    the clip's own Duration with NO transition added (the incoming
    slide's dissolve overlaps the clip's tail instead of waiting on
    a frozen last frame). Realigns with ``core.cut_budget`` and
    spec/61 ("clips at their TRUE length")."""
    times = _section(output, "Times")
    # 6.0s photo + 2.0s transition = 8000.
    assert "opt_synchpos1=8000" in times
    # + 13267 (clip, no transition) = 21267.
    assert "opt_synchpos2=21267" in times
    # + 6000 photo + 2000 transition = 29267.
    assert "opt_synchpos3=29267" in times
    assert "opt_slidescount=3" in times


def test_times_block_has_one_entry_per_slide(output, members):
    times = _section(output, "Times")
    entries = re.findall(r"^opt_synchpos\d+=\d+", times, re.MULTILINE)
    assert len(entries) == len(members)


# ── [Main] overrides ────────────────────────────────────────────


def test_main_aspect_and_canvas_overridden(skel, members, tracks):
    """spec/107 §3.1 — the Cut's aspect drives `AspectRatio` and the
    pixel dimensions; the skeleton's prior aspect is replaced."""
    text = generate(skel, members, tracks,
                    aspect="4:3", photo_seconds=5.0,
                    project_path=Path("C:/cut/slideshow.pte"),
                    images_folder=Path("C:/cut"),
                    overlay_mode=OVERLAY_EMBEDDED)
    main = _section(text, "Main")
    assert "AspectRatio=4-3" in main
    assert "opt_scr_width=1024" in main
    assert "opt_scr_height=768" in main
    # DefDuration is in milliseconds.
    assert "DefDuration=5000" in main


def test_main_project_path_and_images_folder_overridden(output):
    main = _section(output, "Main")
    assert "ProjectFilePath=C:\\cut\\slideshow.pte" in main
    assert "ImagesFolder=C:\\cut\\" in main


# ── Audio ───────────────────────────────────────────────────────


def test_music_block_one_item_per_track(output, tracks):
    music = _music_block(output)
    items = re.findall(r"object Item\d+:TMusicItem", music)
    assert len(items) == len(tracks)
    for track in tracks:
        windows = _windows(track.path)
        assert f"FileName={windows}" in music


def test_music_items_carry_real_durations(output, tracks):
    music = _music_block(output)
    for track in tracks:
        assert f"Duration={track.duration_ms}" in music


def test_music_items_use_fresh_guids(output, tracks):
    music = _music_block(output)
    item_guids = re.findall(
        r"object Item\d+:TMusicItem\r\n      GUID=(" + _GUID + r")",
        music)
    assert len(item_guids) == len(tracks)
    assert len(set(item_guids)) == len(tracks)


def test_music_block_empty_when_no_audio(skel, members):
    text = generate(skel, members, [],
                    aspect="16:9", photo_seconds=6.0,
                    project_path=Path("C:/cut/slideshow.pte"),
                    images_folder=Path("C:/cut"),
                    overlay_mode=OVERLAY_EMBEDDED)
    music = re.search(
        r"object Music:Music\r\n([\s\S]*?)end\r\nend\r\n", text)
    assert music is not None
    assert "TMusicItem" not in music.group(0)


def test_music_block_emits_is_repeat_flag(output):
    """PTE AV Studio 11 stores the "Repeat tracks" option as a
    nested ``object Options:TMusicOptions`` block with ``IsRepeat=1``
    inside ``object Music:Music`` (confirmed by diffing two
    identical projects exported with the option ON vs OFF). We
    emit it unconditionally so the soundtrack loops to the end of
    the visual show even when the audio total is shorter than the
    slide total."""
    music = _music_block(output)
    assert "object Options:TMusicOptions" in music
    assert "IsRepeat=1" in music
    # The Options block must precede Track0 — PTE writes it that way
    # and we want byte-shape parity for a clean round-trip.
    opt_pos = music.find("object Options:TMusicOptions")
    track_pos = music.find("object Track0:TMusicTrack")
    assert 0 <= opt_pos < track_pos


def test_music_block_emits_is_repeat_flag_with_no_audio(skel, members):
    text = generate(skel, members, [],
                    aspect="16:9", photo_seconds=6.0,
                    project_path=Path("C:/cut/slideshow.pte"),
                    images_folder=Path("C:/cut"),
                    overlay_mode=OVERLAY_EMBEDDED)
    music = _music_block(text)
    assert "IsRepeat=1" in music


# ── Write path: BOM + CRLF ─────────────────────────────────────


def test_write_pte_has_bom_and_crlf(tmp_path, output):
    target = tmp_path / "slideshow.pte"
    write_pte(output, target)
    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "missing UTF-8 BOM"
    # Every line ends with CRLF, no bare LF.
    body = raw[3:]
    bare_lf = body.count(b"\n") - body.count(b"\r\n")
    assert bare_lf == 0, f"{bare_lf} bare LF line(s) in output"


def test_slideshow_target_disambiguates(tmp_path):
    """spec/107 §4 — a re-export NEVER silently clobbers an edited
    project. The first call lands on slideshow.pte; subsequent ones
    on slideshow (2).pte, slideshow (3).pte, ..."""
    (tmp_path / "slideshow.pte").write_text("first")
    second = slideshow_target(tmp_path)
    assert second.name == "slideshow (2).pte"
    second.write_text("second")
    third = slideshow_target(tmp_path)
    assert third.name == "slideshow (3).pte"


def test_slideshow_target_overwrites_when_asked(tmp_path):
    (tmp_path / "slideshow.pte").write_text("first")
    same = slideshow_target(tmp_path, overwrite=True)
    assert same.name == "slideshow.pte"


# ── spec/121 §2 — per-Cut filename via the stem parameter ──────


def test_slideshow_target_honours_stem(tmp_path):
    """spec/121 §2 — passing ``stem=cut.tag`` names the project after
    the Cut. A folder of exported Cuts then carries distinct
    project filenames instead of identical ``slideshow.pte`` files."""
    out = slideshow_target(tmp_path, stem="iceland-highlights")
    assert out.name == "iceland-highlights.pte"
    # Distinct Cuts in the same folder don't collide.
    out.write_text("first")
    other = slideshow_target(tmp_path, stem="alaska-day1")
    assert other.name == "alaska-day1.pte"


def test_slideshow_target_collision_uses_the_cut_stem(tmp_path):
    """Re-export of the SAME Cut into a folder that already holds its
    project disambiguates using the Cut's stem, not the legacy
    ``slideshow`` stem."""
    (tmp_path / "iceland-highlights.pte").write_text("first")
    second = slideshow_target(tmp_path, stem="iceland-highlights")
    assert second.name == "iceland-highlights (2).pte"
    second.write_text("second")
    third = slideshow_target(tmp_path, stem="iceland-highlights")
    assert third.name == "iceland-highlights (3).pte"


def test_slideshow_target_default_stem_unchanged(tmp_path):
    """Call sites that don't pass ``stem`` keep the legacy
    ``slideshow.pte`` shape (back-compat for non-share_cuts callers
    + the empty-cut-tag fallback)."""
    out = slideshow_target(tmp_path)
    assert out.name == "slideshow.pte"


def test_slideshow_target_empty_stem_falls_back_to_slideshow(tmp_path):
    """spec/121 §2 acceptance — an empty / whitespace-only stem still
    yields ``slideshow.pte`` so a Cut with a missing name doesn't
    produce a filename like ``.pte``."""
    assert slideshow_target(tmp_path, stem="").name == "slideshow.pte"
    assert slideshow_target(tmp_path, stem="   ").name == "slideshow.pte"


def test_generate_into_folder_writes_named_file(
        tmp_path, members, tracks):
    """generate_into_folder threads ``stem`` through to
    slideshow_target so the on-disk filename matches the Cut."""
    out = generate_into_folder(
        tmp_path, members, tracks,
        aspect="16:9", photo_seconds=6.0,
        library_root=None,
        bundled_fallback=bundled_skeleton_path(),
        overlay_mode=OVERLAY_EMBEDDED,
        stem="my-cut",
    )
    assert out == tmp_path / "my-cut.pte"
    assert out.is_file()


def test_generate_into_folder_writes_named_file_project_path_internal(
        tmp_path, members, tracks):
    """The chosen filename also lands on ``[Main] ProjectFilePath`` so
    the internal path stays consistent with the on-disk name."""
    out = generate_into_folder(
        tmp_path, members, tracks,
        aspect="16:9", photo_seconds=6.0,
        library_root=None,
        bundled_fallback=bundled_skeleton_path(),
        overlay_mode=OVERLAY_EMBEDDED,
        stem="iceland-highlights",
    )
    text = out.read_bytes().decode("utf-8-sig")
    # The PTE [Main] section's ProjectFilePath= line carries the absolute
    # path of the file just written, with backslashes (Windows shape).
    assert "iceland-highlights.pte" in text


def test_generate_into_folder_writes_into_target(
        tmp_path, members, tracks):
    """End-to-end: generate_into_folder reads the bundled skeleton,
    picks a non-clobbering name, and writes BOM+CRLF — the same
    contract as write_pte + slideshow_target."""
    out = generate_into_folder(
        tmp_path, members, tracks,
        aspect="16:9", photo_seconds=6.0,
        library_root=None,
        bundled_fallback=bundled_skeleton_path(),
        overlay_mode=OVERLAY_EMBEDDED,
    )
    assert out == tmp_path / DEFAULT_OUTPUT_NAME
    assert out.is_file()
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")


# ── Round-trip: regenerated reference matches itself ───────────


def test_generate_is_deterministic_except_for_guids(
        skel, members, tracks):
    """Two back-to-back generations differ ONLY in GUIDs — everything
    else (paths, durations, times, sections) is byte-identical. This
    is the golden-file check spec/107 §8 calls for: the generator's
    behaviour is fully determined by its inputs."""
    a = generate(skel, members, tracks,
                 aspect="16:9", photo_seconds=6.0,
                 project_path=Path("C:/cut/slideshow.pte"),
                 images_folder=Path("C:/cut"),
                 overlay_mode=OVERLAY_EMBEDDED)
    b = generate(skel, members, tracks,
                 aspect="16:9", photo_seconds=6.0,
                 project_path=Path("C:/cut/slideshow.pte"),
                 images_folder=Path("C:/cut"),
                 overlay_mode=OVERLAY_EMBEDDED)
    # Strip every GUID to a placeholder before comparing.
    norm_a = re.sub(_GUID, "{GUID}", a)
    norm_b = re.sub(_GUID, "{GUID}", b)
    assert norm_a == norm_b


def test_video_member_without_skeleton_video_proto_raises(members, tracks):
    """spec/107 §3 — a Cut with a video member needs a skeleton that
    contains a video prototype. A photos-only skeleton (no video
    proto) raises with a clear hint."""
    photo_only_skel = """[Main]
DefDuration=6000
AspectRatio=16-9
opt_scr_width=1920
opt_scr_height=1080
ImagesFolder=
ProjectFilePath=
object Music:Music
  object Track0:TMusicTrack
    object Item0:TMusicItem
      GUID={FADCFEDE-2745-4898-8C6B-9F085B5D8A49}
      FileName=
      Duration=0
      FadeIn=3000
      FadeOut=3000
    end
  end
end

[Tracks]

[Slide1]
StyleOptions=[{6F704CA1-6413-4110-BBBC-5FBFF88B0C69}]\\n\\n
object Container:Root
  object Photo:Image
    GUID={00000000-0000-0000-0000-000000000001}
    ImageName=
  end
end
Picture=
[Effects]
object global:group
end
object local:group
end

[Times]
opt_synchpos1=0
opt_slidescount=1
""".replace("\n", "\r\n")
    members = [PteMember(kind="video",
                         path=Path("C:/cut/001.mp4"), duration_ms=5000)]
    with pytest.raises(ValueError, match="video prototype"):
        generate(photo_only_skel, members, [],
                 aspect="16:9", photo_seconds=6.0,
                 project_path=Path("C:/cut/slideshow.pte"),
                 images_folder=Path("C:/cut"),
                 overlay_mode=OVERLAY_OFF)


# ── Small utils ─────────────────────────────────────────────────


def _windows(p: Path) -> str:
    return str(Path(p).resolve()).replace("/", "\\")


def test_fresh_guid_is_well_formed():
    g = fresh_guid()
    assert re.fullmatch(_GUID, g)
    # Two calls produce different values.
    assert g != fresh_guid()


def test_default_transition_ms_is_two_seconds():
    """Validation tools assumed 2s transition; the constant must match."""
    assert DEFAULT_TRANSITION_MS == 2000
