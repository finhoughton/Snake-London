from __future__ import annotations

import math

import pytest

from render import _clip_shapes_for_segment, _extract_svg_fork_geometry


@pytest.fixture
def centres() -> dict[str, list[float]]:
    return {
        "Canary Wharf": [3297.58, 1387.15],
        "Stratford": [3524.44, 419.06],
        "Whitechapel": [2919.94, 1026.94],
        "Liverpool Street": [2686.24, 1024.50],
    }


@pytest.fixture
def line_segments() -> dict[str, list[list[str]]]:
    return {
        "Elizabeth": [
            ["Whitechapel", "Stratford"],
            ["Whitechapel", "Canary Wharf"],
            ["Liverpool Street", "Whitechapel"],
        ],
    }


def test_straight_segment_without_fork_group_uses_auto_rect(centres: dict[str, list[float]]) -> None:
    shapes = _clip_shapes_for_segment(
        "Elizabeth",
        "Liverpool Street",
        "Whitechapel",
        centres,
        fork_groups={},
        station_markers={},
    )

    assert len(shapes) == 1
    polygon = shapes[0]
    assert isinstance(polygon, list)
    assert len(polygon) == 4
    assert all(isinstance(pt, tuple) and len(pt) == 2 for pt in polygon)


def test_fork_group_shapes_emitted_verbatim(centres: dict[str, list[float]]) -> None:
    fork_groups = {
        ("Elizabeth", "Canary Wharf", "Whitechapel"): {
            "shapes": ['<path d="M 0 0 L 10 0 L 0 10 Z"/>'],
            "tails": [],
            "waypoints": [],
        },
    }

    shapes = _clip_shapes_for_segment(
        "Elizabeth",
        "Canary Wharf",
        "Whitechapel",
        centres,
        fork_groups=fork_groups,
        station_markers={},
    )

    assert shapes == ['<path d="M 0 0 L 10 0 L 0 10 Z"/>']


def test_fork_group_matches_reversed_station_order(centres: dict[str, list[float]]) -> None:
    fork_groups = {
        ("Elizabeth", "Stratford", "Whitechapel"): {
            "shapes": ['<path d="M 1 1 Z"/>'],
            "tails": [],
            "waypoints": [],
        },
    }

    shapes = _clip_shapes_for_segment(
        "Elizabeth",
        "Whitechapel",
        "Stratford",
        centres,
        fork_groups=fork_groups,
        station_markers={},
    )

    assert shapes == ['<path d="M 1 1 Z"/>']


def test_fork_group_tail_emits_auto_rect_toward_station(centres: dict[str, list[float]]) -> None:
    tail_x, tail_y = 3050.0, 923.0
    fork_groups = {
        ("Elizabeth", "Stratford", "Whitechapel"): {
            "shapes": ['<path d="M 1 1 Z"/>'],
            "tails": [("Whitechapel", tail_x, tail_y)],
            "waypoints": [],
        },
    }

    shapes = _clip_shapes_for_segment(
        "Elizabeth",
        "Stratford",
        "Whitechapel",
        centres,
        fork_groups=fork_groups,
        station_markers={},
    )

    assert shapes[0] == '<path d="M 1 1 Z"/>'
    assert len(shapes) == 2
    polygon = shapes[1]
    assert isinstance(polygon, list)
    assert len(polygon) == 4

    station_x, station_y = centres["Whitechapel"]
    dx = station_x - tail_x
    dy = station_y - tail_y
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    projections = [(x - tail_x) * ux + (y - tail_y) * uy for x, y in polygon]
    assert min(projections) == pytest.approx(0.0, abs=1e-6)
    assert max(projections) == pytest.approx(length + 8.0, abs=1e-6)


def test_extract_svg_fork_geometry_parses_shapes_and_tails(
    line_segments: dict[str, list[list[str]]],
) -> None:
    svg_text = """
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">
            <g id="Path Overrides">
                <g id="Elizabeth:Stratford:Whitechapel">
                    <path d="M 1 1 L 2 2 Z"/>
                    <circle id="Elizabeth:Stratford:Whitechapel:tail:Whitechapel" cx="3050" cy="923" r="1"/>
                </g>
                <g id="Elizabeth:Canary Wharf:Whitechapel">
                    <path d="M 3 3 Z"/>
                    <rect x="0" y="0" width="10" height="10"/>
                </g>
            </g>
        </svg>
    """

    groups = _extract_svg_fork_geometry(svg_text, line_segments)

    assert set(groups) == {
        ("Elizabeth", "Stratford", "Whitechapel"),
        ("Elizabeth", "Canary Wharf", "Whitechapel"),
    }

    strat = groups[("Elizabeth", "Stratford", "Whitechapel")]
    assert strat["shapes"] == ['<path d="M 1 1 L 2 2 Z"/>']
    assert strat["tails"] == [("Whitechapel", 3050.0, 923.0)]
    assert strat["waypoints"] == []

    cw = groups[("Elizabeth", "Canary Wharf", "Whitechapel")]
    assert cw["shapes"] == [
        '<path d="M 3 3 Z"/>',
        '<rect x="0" y="0" width="10" height="10"/>',
    ]
    assert cw["tails"] == []
    assert cw["waypoints"] == []


def test_extract_svg_fork_geometry_ignores_legacy_markers(
    line_segments: dict[str, list[list[str]]],
) -> None:
    svg_text = """
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">
            <g id="Path Overrides">
                <g id="Elizabeth:Stratford:Whitechapel">
                    <path d="M 1 1 Z"/>
                    <circle id="Elizabeth:Stratford:Whitechapel:branch" cx="100" cy="100" r="1"/>
                    <circle id="Elizabeth:Stratford:Whitechapel:stem" cx="200" cy="200" r="1"/>
                    <circle id="Elizabeth:Stratford:Whitechapel:waypoint0" cx="150" cy="150" r="1"/>
                </g>
            </g>
        </svg>
    """

    groups = _extract_svg_fork_geometry(svg_text, line_segments)

    assert groups[("Elizabeth", "Stratford", "Whitechapel")]["shapes"] == ['<path d="M 1 1 Z"/>']
    assert groups[("Elizabeth", "Stratford", "Whitechapel")]["tails"] == []
    assert groups[("Elizabeth", "Stratford", "Whitechapel")]["waypoints"] == []


def test_extract_svg_fork_geometry_rejects_unknown_segment(
    line_segments: dict[str, list[list[str]]],
) -> None:
    svg_text = """
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">
            <g id="Path Overrides">
                <g id="Elizabeth:Nowhere:Whitechapel">
                    <path d="M 0 0 Z"/>
                </g>
            </g>
        </svg>
    """

    with pytest.raises(ValueError, match="unknown segment"):
        _extract_svg_fork_geometry(svg_text, line_segments)


def test_extract_svg_fork_geometry_rejects_tail_station_not_in_group(
    line_segments: dict[str, list[list[str]]],
) -> None:
    svg_text = """
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">
            <g id="Path Overrides">
                <g id="Elizabeth:Stratford:Whitechapel">
                    <path d="M 1 1 Z"/>
                    <circle id="Elizabeth:Stratford:Whitechapel:tail:Canary Wharf" cx="3000" cy="1000" r="1"/>
                </g>
            </g>
        </svg>
    """

    with pytest.raises(ValueError, match="Tail circle references station not in group"):
        _extract_svg_fork_geometry(svg_text, line_segments)


def test_waypoint_only_group_chains_rectangles(centres: dict[str, list[float]]) -> None:
    fork_groups = {
        ("Elizabeth", "Liverpool Street", "Whitechapel"): {
            "shapes": [],
            "tails": [],
            "waypoints": [
                (0, 2750.0, 1024.0),
                (1, 2850.0, 1024.0),
            ],
        },
    }

    shapes = _clip_shapes_for_segment(
        "Elizabeth",
        "Liverpool Street",
        "Whitechapel",
        centres,
        fork_groups=fork_groups,
        station_markers={},
    )

    assert len(shapes) == 3
    assert all(isinstance(poly, list) and len(poly) == 4 for poly in shapes)


def test_waypoint_chain_sorted_by_index(centres: dict[str, list[float]]) -> None:
    fork_groups = {
        ("Elizabeth", "Liverpool Street", "Whitechapel"): {
            "shapes": [],
            "tails": [],
            "waypoints": [
                (5, 2850.0, 1024.0),
                (1, 2750.0, 1024.0),
            ],
        },
    }

    fork_groups[("Elizabeth", "Liverpool Street", "Whitechapel")]["waypoints"].sort(key=lambda w: w[0])

    shapes = _clip_shapes_for_segment(
        "Elizabeth",
        "Liverpool Street",
        "Whitechapel",
        centres,
        fork_groups=fork_groups,
        station_markers={},
    )

    lib_x, lib_y = centres["Liverpool Street"]
    first_rect = shapes[0]
    distances_from_lib = [math.hypot(x - lib_x, y - lib_y) for x, y in first_rect]
    assert min(distances_from_lib) < 120.0


def test_extract_svg_fork_geometry_parses_waypoints(
    line_segments: dict[str, list[list[str]]],
) -> None:
    svg_text = """
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">
            <g id="Path Overrides">
                <g id="Elizabeth:Liverpool Street:Whitechapel">
                    <circle id="Elizabeth:Liverpool Street:Whitechapel:waypoint:1" cx="2850" cy="1024" r="1"/>
                    <circle id="Elizabeth:Liverpool Street:Whitechapel:waypoint:0" cx="2750" cy="1024" r="1"/>
                </g>
            </g>
        </svg>
    """

    groups = _extract_svg_fork_geometry(svg_text, line_segments)
    group = groups[("Elizabeth", "Liverpool Street", "Whitechapel")]
    assert group["shapes"] == []
    assert group["tails"] == []
    assert group["waypoints"] == [(0, 2750.0, 1024.0), (1, 2850.0, 1024.0)]


def test_extract_svg_fork_geometry_rejects_duplicate_waypoint_index(
    line_segments: dict[str, list[list[str]]],
) -> None:
    svg_text = """
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">
            <g id="Path Overrides">
                <g id="Elizabeth:Liverpool Street:Whitechapel">
                    <circle id="Elizabeth:Liverpool Street:Whitechapel:waypoint:0" cx="2750" cy="1024" r="1"/>
                    <circle id="Elizabeth:Liverpool Street:Whitechapel:waypoint:0" cx="2850" cy="1024" r="1"/>
                </g>
            </g>
        </svg>
    """

    with pytest.raises(ValueError, match="Duplicate waypoint index"):
        _extract_svg_fork_geometry(svg_text, line_segments)
