from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import pytest
from map import Map
from render import _extract_svg_fork_geometry

SVG_PATH = "map/snake map.svg"
MARKER_TAGS = {"circle", "rect"}
LABEL_TAGS = {"text", "g"}
LINE_TAGS = {"path", "g"}

# Fixtures


@pytest.fixture(scope="session")
def tube_map() -> Map:
    return Map("map/connections.json")


@pytest.fixture(scope="session")
def svg_ids() -> dict[str, ET.Element]:
    """Map of id -> element for every element with an id in the SVG."""
    tree = ET.parse(SVG_PATH)
    root = tree.getroot()
    ids: dict[str, ET.Element] = {}

    def collect(el: ET.Element) -> None:
        id_ = el.get("id")
        if id_:
            ids[id_] = el
        for child in el:
            collect(child)

    collect(root)
    return ids


@pytest.fixture(scope="session")
def station_keys(tube_map: Map) -> list[str]:
    return tube_map.station_keys()


@pytest.fixture(scope="session")
def station_display_names() -> dict[str, str]:
    with open("map/connections.json", encoding="utf-8") as f:
        data = json.load(f)
    return {key: info["display_name"] for key, info in data["stations"].items()}


@pytest.fixture(scope="session")
def line_keys(tube_map: Map) -> list[str]:
    return tube_map.line_keys()


# Parametrize helpers


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    # Parametrize station-level tests
    if "station_key" in metafunc.fixturenames:
        m = Map("map/connections.json")
        metafunc.parametrize("station_key", m.station_keys())
    # Parametrize line-level tests
    if "line_key" in metafunc.fixturenames:
        m = Map("map/connections.json")
        metafunc.parametrize("line_key", m.line_keys())


# Station marker tests


def test_station_marker_exists(station_key: str, svg_ids: dict[str, ET.Element]) -> None:
    """Every station has a circle or rect element with id '<station> Marker'."""
    marker_id = f"{station_key} Marker"
    assert marker_id in svg_ids, (
        f"No SVG element with id={marker_id!r}. Add a circle or rect marker in Inkscape and set its id."
    )


def test_station_marker_is_correct_tag(station_key: str, svg_ids: dict[str, ET.Element]) -> None:
    """Station marker must be a <circle> or <rect>."""
    marker_id = f"{station_key} Marker"
    el = svg_ids.get(marker_id)
    if el is None:
        pytest.skip("marker missing — covered by test_station_marker_exists")
    tag = el.tag.split("}")[-1]
    assert tag in MARKER_TAGS, f"{marker_id!r} is a <{tag}>, expected one of {MARKER_TAGS}"


def test_station_marker_has_white_fill(station_key: str, svg_ids: dict[str, ET.Element]) -> None:
    """Station marker should have a white fill (unclaimed default)."""
    marker_id = f"{station_key} Marker"
    el = svg_ids.get(marker_id)
    if el is None:
        pytest.skip("marker missing — covered by test_station_marker_exists")
    style = el.get("style", "").lower()
    fill_attr = (el.get("fill") or "").lower()
    white_values = {"#fff", "#ffffff", "white"}
    has_white = any(v in style for v in white_values) or fill_attr in white_values
    assert has_white, f"{marker_id!r} does not appear to have a white fill. style={style!r}, fill attr={fill_attr!r}"


def test_station_marker_has_stroke(station_key: str, svg_ids: dict[str, ET.Element]) -> None:
    """Station marker should have a stroke (border)."""
    marker_id = f"{station_key} Marker"
    el = svg_ids.get(marker_id)
    if el is None:
        pytest.skip("marker missing — covered by test_station_marker_exists")
    style = el.get("style", "").lower()
    stroke_attr = (el.get("stroke") or "").lower()
    has_stroke = "stroke:" in style or stroke_attr not in ("", "none")
    assert has_stroke, f"{marker_id!r} has no stroke. Add a border stroke in Inkscape Fill and Stroke panel."


# Station label tests


def test_station_label_exists(station_key: str, svg_ids: dict[str, ET.Element]) -> None:
    """Every station has a label element with id '<station> Label'."""
    label_id = f"{station_key} Label"
    assert label_id in svg_ids, (
        f"No SVG element with id={label_id!r}. Set id on the station's text or group label in Inkscape."
    )


def test_station_label_is_correct_tag(station_key: str, svg_ids: dict[str, ET.Element]) -> None:
    """Station label must be a <g> containing two <text> children (halo + clean)."""
    label_id = f"{station_key} Label"
    el = svg_ids.get(label_id)
    if el is None:
        pytest.skip("label missing — covered by test_station_label_exists")
    tag = el.tag.split("}")[-1]
    assert tag == "g", f"{label_id!r} is a <{tag}>, expected <g>"

    texts = [child for child in el if child.tag.split("}")[-1] == "text"]
    assert len(texts) == 2, f"{label_id!r} group has {len(texts)} <text> children, expected 2 (halo + clean)"

    halo, clean = texts
    halo_style = halo.get("style", "").lower()
    clean_style = clean.get("style", "").lower()

    assert "stroke:#fff" in halo_style or "stroke:white" in halo_style, (
        f"{label_id!r} halo text missing white stroke. style={halo_style!r}"
    )
    assert re.search(r"stroke-width\s*:\s*5", halo_style), (
        f"{label_id!r} halo text missing stroke-width:5. style={halo_style!r}"
    )
    assert re.search(r"stroke-width\s*:\s*0", clean_style), (
        f"{label_id!r} clean text missing stroke-width:0. style={clean_style!r}"
    )


def _text_content(text_el: ET.Element) -> str:
    """Join tspan text parts with a space and normalise whitespace.

    Inkscape splits multi-line labels into separate <tspan> elements with no
    separator, so naively concatenating gives e.g. 'Tooting Broadway /Tooting'.
    Joining with a space and collapsing runs of whitespace gives the expected
    'Tooting Broadway / Tooting'.
    """
    return re.sub(r"\s+", " ", " ".join(text_el.itertext())).strip()


def test_station_label_text_matches_display_name(
    station_key: str,
    svg_ids: dict[str, ET.Element],
    station_display_names: dict[str, str],
) -> None:
    """Both text elements in the label group must contain the station's display name."""
    label_id = f"{station_key} Label"
    el = svg_ids.get(label_id)
    if el is None:
        pytest.skip("label missing — covered by test_station_label_exists")

    texts = [child for child in el if child.tag.split("}")[-1] == "text"]
    if len(texts) != 2:
        pytest.skip("wrong number of text children — covered by test_station_label_is_correct_tag")

    expected = station_display_names.get(station_key, station_key)
    for i, text_el in enumerate(texts):
        actual = _text_content(text_el)
        assert actual == expected, (
            f"{label_id!r} text[{i}] is {actual!r}, expected {expected!r}"
        )


# Line tests


def test_line_element_exists(line_key: str, svg_ids: dict[str, ET.Element]) -> None:
    """Every line has an element with its exact key as id."""
    assert line_key in svg_ids, (
        f"No SVG element with id={line_key!r}. Add a path/group for the line and set its id in Inkscape."
    )


def test_line_element_is_correct_tag(line_key: str, svg_ids: dict[str, ET.Element]) -> None:
    """Line element must be a <path>, or <g>."""
    el = svg_ids.get(line_key)
    if el is None:
        pytest.skip("line missing — covered by test_line_element_exists")
    tag = el.tag.split("}")[-1]
    assert tag in LINE_TAGS, f"{line_key!r} line is a <{tag}>, expected one of {LINE_TAGS}"


# Global SVG checks


def test_no_duplicate_ids(svg_ids: dict[str, ET.Element]) -> None:
    """IDs in the SVG should be unique (svg_ids fixture already deduplicates; detect via re-parse)."""
    tree = ET.parse(SVG_PATH)
    root = tree.getroot()
    seen: list[str] = []

    def collect(el: ET.Element) -> None:
        id_ = el.get("id")
        if id_:
            seen.append(id_)
        for child in el:
            collect(child)

    collect(root)
    duplicates = {i for i in seen if seen.count(i) > 1}
    assert not duplicates, f"Duplicate SVG ids: {sorted(duplicates)}"


def test_no_stale_station_markers(svg_ids: dict[str, ET.Element], tube_map: Map) -> None:
    """No circle/rect markers with ids that don't match '<known station> Marker'."""
    known_markers = {f"{k} Marker" for k in tube_map.station_keys()}
    # Non-station rects/circles with reserved ids that are not station markers
    reserved_ids = {"background"}
    helper_marker_ids: set[str] = set()

    tree = ET.parse(SVG_PATH)
    root = tree.getroot()
    label_attr = "{http://www.inkscape.org/namespaces/inkscape}label"
    fork_geometry_layer = next(
        (el for el in root.iter() if el.get(label_attr) == "Path Overrides" or el.get("id") == "Path Overrides"),
        None,
    )
    if fork_geometry_layer is not None:
        for helper in fork_geometry_layer.iter():
            helper_id = helper.get("id")
            helper_tag = helper.tag.split("}")[-1]
            if helper_id and helper_tag in MARKER_TAGS:
                helper_marker_ids.add(helper_id)

    stale = []
    for id_, el in svg_ids.items():
        tag = el.tag.split("}")[-1]
        if tag in MARKER_TAGS and id_ not in known_markers and id_ not in reserved_ids and id_ not in helper_marker_ids:
            if len(id_) > 2 and not id_.isdigit():
                stale.append(id_)
    assert not stale, f"SVG has {len(stale)} marker elements with unknown ids: {stale[:10]}"


def _has_display_none(el: ET.Element) -> bool:
    style = el.get("style", "")
    return any(
        part.strip().startswith("display") and part.strip().split(":")[-1].strip() == "none"
        for part in style.split(";")
    )


def test_path_overrides_layer_hidden() -> None:
    """The Path Overrides layer or every segment group inside it must be hidden (display:none).

    The layer must not render visibly — either the layer itself is hidden, or
    every direct child <g> is individually hidden.
    """
    tree = ET.parse(SVG_PATH)
    root = tree.getroot()
    label_attr = "{http://www.inkscape.org/namespaces/inkscape}label"
    layer = next(
        (el for el in root.iter() if el.get(label_attr) == "Path Overrides" or el.get("id") == "Path Overrides"),
        None,
    )
    if layer is None:
        pytest.skip("Path Overrides layer not present")

    if _has_display_none(layer):
        return  # whole layer hidden — fine

    visible_groups = [
        child.get("id", "<no id>")
        for child in layer
        if child.tag.split("}")[-1] == "g" and not _has_display_none(child)
    ]
    assert not visible_groups, (
        f"Path Overrides layer is visible and {len(visible_groups)} group(s) lack display:none: "
        f"{visible_groups[:5]}"
    )


def test_path_overrides_all_groups_recognised() -> None:
    """Every group in the Path Overrides layer must reference a known line and segment."""
    svg_text = open(SVG_PATH, encoding="utf-8").read()
    with open("map/geometry.json", encoding="utf-8") as f:
        line_segments: dict[str, list[list[str]]] = json.load(f)["line_segments"]
    # Raises ValueError if any group id references an unknown line or segment.
    _extract_svg_fork_geometry(svg_text, line_segments)


def test_no_stale_labels(svg_ids: dict[str, ET.Element], tube_map: Map) -> None:
    """No label elements with ids that don't match '<known station> Label'."""
    known_labels = {f"{k} Label" for k in tube_map.station_keys()}
    stale = []
    for id_, el in svg_ids.items():
        tag = el.tag.split("}")[-1]
        if tag in LABEL_TAGS and id_.endswith(" Label") and id_ not in known_labels:
            stale.append(id_)
    assert not stale, f"SVG has label elements for unknown stations: {stale[:10]}"
