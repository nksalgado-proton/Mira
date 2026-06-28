"""Phone-lens normalization for the cross-event projection (gear_normalize)."""
from core.gear_normalize import is_phone_lens, normalize_lens


def test_iphone_lens_variants_collapse_to_camera_name():
    """Every iPhone 11 sensor string normalizes to the phone's camera name,
    so the Lens facet shows one row per phone instead of five."""
    cam = "iPhone 11"
    for lens in [
        "iPhone 11 back dual wide camera 4.25mm f/1.8",
        "iPhone 11 front camera 2.71mm f/2.2",
        "iPhone 11 back dual wide camera 1.54mm f/2.4",
        "iPhone 11 front TrueDepth camera 2.71mm f/2.2",
        "iPhone 11 back camera 4.25mm f/1.8",
    ]:
        assert normalize_lens(lens, cam) == "iPhone 11"


def test_real_lenses_pass_through_unchanged():
    """Interchangeable-camera lenses are meaningful gear and must not be
    collapsed — even when the body is a Lumix (no phone marker)."""
    for lens, cam in [
        ("LEICA DG 100-400/F4.0-6.3", "DC-G9"),
        ("LUMIX G VARIO 35-100/F2.8II", "DMC-GX8"),
        ("OLYMPUS M.60mm F2.8 Macro", "DC-G9M2"),
        ("EF-S18-55mm f/3.5-5.6 IS STM", "Canon EOS REBEL T5i"),
    ]:
        assert normalize_lens(lens, cam) == lens


def test_phone_detection_reads_lens_or_camera():
    assert is_phone_lens("iPhone SE back camera 4.15mm f/2.2", "iPhone SE")
    # Blank lens but phone body still classifies (falls back to camera_id).
    assert is_phone_lens(None, "Pixel 7")
    assert not is_phone_lens("LEICA DG SUMMILUX 25/F1.4", "DC-G9")


def test_distinct_phones_stay_distinct():
    """Different phone models keep their own row — the collapse is per phone,
    not 'all phones into one'."""
    assert normalize_lens("iPhone 11 back camera 4.25mm f/1.8", "iPhone 11") \
        == "iPhone 11"
    assert normalize_lens("iPhone SE back camera 4.15mm f/2.2", "iPhone SE") \
        == "iPhone SE"
    assert normalize_lens("iPhone 6s back camera 4.15mm f/2.2", "iPhone 6s") \
        == "iPhone 6s"


def test_phone_label_recovered_from_lens_when_camera_unknown():
    """A phone shot whose body EXIF is lost (camera_id '_unknown') still
    groups under the phone model parsed from the lens string — not under
    '_unknown' with unrelated bodies."""
    assert normalize_lens(
        "iPhone 6s front camera 2.65mm f/2.2", "_unknown") == "iPhone 6s"
    assert normalize_lens(
        "iPhone 11 back dual wide camera 4.25mm f/1.8", None) == "iPhone 11"


def test_empty_lens_passes_through():
    assert normalize_lens(None, "iPhone 11") is None
    assert normalize_lens("", "iPhone 11") == ""
