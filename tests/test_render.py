from __future__ import annotations

import math
import re

import pytest

import render
from config import BONUS_AT_FRONT
from game import new_game
from render import _clip_shapes_for_segment, _extract_svg_fork_geometry, render_map


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


# --- render_map overlay injection ------------------------------------------------


def _game_with_active_neck():
    """A game whose snake has an active neck, so render_map produces overlays."""
    game = new_game(start_positions={"A": "Wembley Park"}, bonus_interchanges=set())
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Bond Street")
    return game


@pytest.fixture(scope="module")
def rendered_overlay(tmp_path_factory):
    """Render a game with an active neck once; shared across the overlay assertions."""
    out = tmp_path_factory.mktemp("render") / "map.svg"
    result = render_map(_game_with_active_neck(), out)
    return out, result, out.read_text(encoding="utf-8")


def test_render_map_writes_file_and_returns_path(rendered_overlay) -> None:
    out, result, svg = rendered_overlay

    assert result == out
    assert svg.lstrip().startswith("<")


def test_render_map_injects_overlays_before_first_label_group(rendered_overlay) -> None:
    _, _, svg = rendered_overlay

    # Overlay clip groups are only emitted by the segment-overlay injection.
    overlay_idx = svg.find("seg-clip-")
    assert overlay_idx != -1, "expected segment overlays to be injected"

    label_idx = render._LABEL_GROUP_RE.search(svg).start()
    assert overlay_idx < label_idx, "overlays must paint before (under) the station labels"


def test_render_map_includes_team_legend(rendered_overlay) -> None:
    _, _, svg = rendered_overlay

    legend_idx = svg.find('<g id="Legend">')
    assert legend_idx != -1, "expected a legend group"
    # Legend is the HUD: it must paint last, after the labels, before </svg>.
    assert legend_idx > render._LABEL_GROUP_RE.search(svg).start()
    assert legend_idx < svg.rfind("</svg>")

    # Header (placeholder clock) and per-team stats for the fixture's snake. The
    # snake has an active neck, so the row shows its Front station rather than its
    # declared line. Coins/cards are private and must not appear.
    for needle in ("Time elapsed", "00:00:00", "Score: 1", "Bond Street"):
        assert needle in svg, f"legend missing {needle!r}"
    for hidden in ("Coins", "Cards"):
        assert hidden not in svg, f"legend should not expose {hidden!r}"


def test_render_map_raises_when_no_label_anchor(tmp_path, monkeypatch) -> None:
    # Simulate the anchor disappearing (e.g. labels renamed/removed): the renderer
    # must fail loudly rather than silently dropping the overlays.
    monkeypatch.setattr(render, "_LABEL_GROUP_RE", re.compile(r'id="__missing__ Label"'))

    with pytest.raises(ValueError, match="label group"):
        render_map(_game_with_active_neck(), tmp_path / "map.svg")


def test_render_map_draws_bonus_badges(tmp_path) -> None:
    game = new_game(start_positions={"A": "Wembley Park"}, bonus_interchanges={"Stratford"})
    svg = render_map(game, tmp_path / "map.svg").read_text(encoding="utf-8")

    assert '<g id="Bonus Coins">' in svg
    assert f"+{BONUS_AT_FRONT}" in svg


def test_render_map_skips_badge_on_claimed_bonus(tmp_path) -> None:
    # The only bonus interchange gets claimed, so its bonus is spent — no badge.
    game = new_game(start_positions={"A": "Wembley Park"}, bonus_interchanges={"Bond Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Bond Street")
    game.complete_challenge("A", "Jubilee")  # claims Bond Street (the bonus)
    svg = render_map(game, tmp_path / "map.svg").read_text(encoding="utf-8")

    assert '<g id="Bonus Coins">' not in svg


def test_render_greys_out_eliminated_snakes(tmp_path) -> None:
    # A requests through B's claimed Bond Street and crashes; its body is greyed.
    game = new_game({"A": "Baker Street", "B": "Bond Street"}, bonus_interchanges=set())
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.initial_request_challenge("B")
    game.complete_challenge("B", "Jubilee")
    game.request_challenge("A", "Green Park")
    assert game.get_snake("A").crashed

    svg = render_map(game, tmp_path / "map.svg").read_text(encoding="utf-8")
    assert render._CRASHED_COLOR in svg  # A's crashed body/segments rendered grey
    assert "(crashed)" in svg  # and marked in the legend


def test_eliminated_ghost_neck_yields_to_a_live_claim(tmp_path) -> None:
    # A crashes with a neck running through B's claimed Bond Street. A's grey ghost
    # neck must NOT recolour Bond Street — B owns it, so it stays B's colour.
    game = new_game({"A": "Baker Street", "B": "Bond Street"}, bonus_interchanges=set())
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.initial_request_challenge("B")
    game.complete_challenge("B", "Jubilee")
    game.request_challenge("A", "Green Park")  # A's neck = [Bond Street (B's), Green Park]
    assert game.get_snake("A").crashed

    svg = render_map(game, tmp_path / "map.svg").read_text(encoding="utf-8")

    def marker_tag(station: str) -> str:
        return re.search(rf'<(?:circle|rect)\b[^>]*\bid="{station} Marker"[^>]*>', svg).group(0)

    # Bond Street (B's claim, under A's ghost neck) renders in B's colour, not grey.
    bond = marker_tag("Bond Street")
    assert game.get_snake("B").color in bond
    assert render._CRASHED_COLOR not in bond
    # Green Park (A's unclaimed neck tip) is the grey ghost neck.
    assert render._CRASHED_COLOR in marker_tag("Green Park")
