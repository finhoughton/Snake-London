from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from map import Map

SVG_PATH = "snake map.svg"
MARKER_TAGS = {"circle", "rect"}
LABEL_TAGS = {"text", "g"}
LINE_TAGS = {"path", "g"}

# Fixtures


@pytest.fixture(scope="session")
def tube_map() -> Map:
    return Map("connections.json")


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
def line_keys(tube_map: Map) -> list[str]:
    return tube_map.line_keys()


# Parametrize helpers


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    # Parametrize station-level tests
    if "station_key" in metafunc.fixturenames:
        m = Map("connections.json")
        metafunc.parametrize("station_key", m.station_keys())
    # Parametrize line-level tests
    if "line_key" in metafunc.fixturenames:
        m = Map("connections.json")
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
    stale = []
    for id_, el in svg_ids.items():
        tag = el.tag.split("}")[-1]
        if tag in MARKER_TAGS and id_ not in known_markers and id_ not in reserved_ids:
            if len(id_) > 2 and not id_.isdigit():
                stale.append(id_)
    assert not stale, f"SVG has {len(stale)} marker elements with unknown ids: {stale[:10]}"


def test_no_stale_labels(svg_ids: dict[str, ET.Element], tube_map: Map) -> None:
    """No label elements with ids that don't match '<known station> Label'."""
    known_labels = {f"{k} Label" for k in tube_map.station_keys()}
    stale = []
    for id_, el in svg_ids.items():
        tag = el.tag.split("}")[-1]
        if tag in LABEL_TAGS and id_.endswith(" Label") and id_ not in known_labels:
            stale.append(id_)
    assert not stale, f"SVG has label elements for unknown stations: {stale[:10]}"
