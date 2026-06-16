"""spec/81 §3.1 — the shared overlay field-composition (pure, no Qt).

The one formatter Play / embedded / burn-in all share: selected provenance
fields → ordered text lines; the *where* IPTC tag map for embedded mode; and
the "does this frame need an embedded write" predicate (so link-pure frames
stay hardlinks).
"""
from core import cut_overlay as ov


def _prov(**k):
    return ov.FrameProvenance(**k)


def test_compose_lines_orders_by_field_catalogue_not_selection():
    p = _prov(when="2026-04-01 08:00", city="Arenal", country="Costa Rica",
              camera="G9", lens_model="100-400", aperture_f=5.6,
              shutter_speed_s=0.002, iso=400, focal_length_mm=300.0)
    # selection order is scrambled; output follows OVERLAY_FIELDS order
    lines = ov.compose_overlay_lines(["how2", "when", "where", "how1"], p)
    assert lines == [
        "2026-04-01 08:00",
        "Arenal, Costa Rica",
        "G9 · 100-400",
        "300mm · f/5.6 · 1/500 · ISO 400",
    ]


def test_compose_lines_omits_missing_fields_no_blank_lines():
    p = _prov(when="2026-04-01")               # only 'when' has data
    assert ov.compose_overlay_lines(["when", "where", "how1", "how2"], p) == [
        "2026-04-01"]


def test_compose_lines_empty_selection_is_off():
    assert ov.compose_overlay_lines([], _prov(when="x")) == []


def test_shutter_formats_fraction_under_a_second():
    p = _prov(shutter_speed_s=0.004)
    assert ov.compose_overlay_lines(["how2"], p) == ["1/250"]
    p2 = _prov(shutter_speed_s=2.0)
    assert ov.compose_overlay_lines(["how2"], p2) == ["2s"]


def test_where_iptc_tags_only_present_fields():
    p = _prov(city="Arenal", country="Costa Rica")   # no sublocation
    tags = ov.where_iptc_tags(p)
    assert tags == {ov.IPTC_CITY: "Arenal", ov.IPTC_COUNTRY: "Costa Rica"}
    assert ov.IPTC_SUBLOCATION not in tags


def test_needs_embedded_write_only_when_where_selected_and_has_data():
    p = _prov(city="Arenal")
    assert ov.needs_embedded_write(["where"], p) is True
    # where NOT selected → no write even with city data
    assert ov.needs_embedded_write(["when"], p) is False
    # where selected but no location data → no write (stays a pure link)
    assert ov.needs_embedded_write(["where"], _prov(when="x")) is False
