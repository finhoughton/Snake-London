from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal, TypedDict

import resvg_py

from game import GameState

SVG_SOURCE = Path("map/snake map.svg")
GEOMETRY_PATH = Path("map/geometry.json")
UNCLAIMED_COLOR = "#ffffff"
NECK_STROKE_DASHARRAY = "4 2"
_BORDER_W = 4.5  # visible border width in SVG units — controls body outline and neck dashed stroke
NECK_TINT_FACTOR = 0.55  # 0 = team color, 1 = white

_DOT_PERIOD = 8.0  # SVG units — nearest-neighbour distance in hex dot grid
_DOT_RADIUS = 2.5  # radius of each dot in SVG units

# SVG-unit padding beyond station centres along the segment direction
_SEGMENT_PAD = 8.0
# Half-width of the oriented clip rectangle perpendicular to the segment
_SEGMENT_HALF_W = 80.0

# Marker to find where line elements end and labels begin
_OVERLAY_INSERTION_MARKER = 'id="Wansted Park Label"'


def render_map(game: GameState, output_path: str | Path, *, debug: bool = False) -> Path:
    with open(SVG_SOURCE, "r", encoding="utf-8") as f:
        svg = f.read()

    # Build a map of station -> (color, mode) where mode is "body" or "neck"
    overrides: dict[str, tuple[str, str]] = {}

    for team, snake in game.snakes.items():
        if snake.crashed:
            continue
        color = snake.color
        for station in game.body_stations(team):
            overrides[station] = (color, "body")
        if snake.neck_active:
            for station in game.neck(team):
                # Don't overwrite a body claim with a neck highlight
                if station not in overrides:
                    overrides[station] = (color, "neck")

    for station, (color, mode) in overrides.items():
        marker_id = f"{station} Marker"
        svg = _set_marker_style(svg, marker_id, color, mode)

    # Inject segment highlight overlays between line elements and labels
    overlay_svg = _build_segment_overlays(game, debug=debug)
    if overlay_svg:
        insertion_marker = _OVERLAY_INSERTION_MARKER
        idx = svg.find(insertion_marker)
        if idx != -1:
            # Walk back to the start of the <g tag
            tag_start = svg.rfind("<g", 0, idx)
            # Walk further back past any preceding whitespace
            insert_at = tag_start
            while insert_at > 0 and svg[insert_at - 1] in " \t\n":
                insert_at -= 1
            svg = svg[:insert_at] + "\n  " + overlay_svg + "\n  " + svg[insert_at:]

    dest = Path(output_path)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(svg)

    return dest


def _tint_color(hex_color: str, factor: float) -> str:
    """Blend hex_color toward white by factor (0 = original, 1 = white)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    r = round(r + (255 - r) * factor)
    g = round(g + (255 - g) * factor)
    b = round(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _set_marker_style(svg: str, marker_id: str, color: str, mode: str) -> str:
    pattern = re.compile(
        r'(<(?:circle|rect)\b[^>]*\bid="' + re.escape(marker_id) + r'"[^>]*>)',
        re.DOTALL,
    )

    if mode == "body":
        new_style = f"fill:{color};stroke:#000000;stroke-width:3.75"
    else:  # neck
        tint = _tint_color(color, NECK_TINT_FACTOR)
        new_style = f"fill:{tint};stroke:{color};stroke-width:{_BORDER_W};stroke-dasharray:{NECK_STROKE_DASHARRAY}"

    def replace_tag(m: re.Match) -> str:
        tag = m.group(1)
        if 'style="' in tag:
            # Replace the entire style value
            tag = re.sub(r'style="[^"]*"', f'style="{new_style}"', tag)
        else:
            tag = tag[:-1] + f' style="{new_style}">'
        return tag

    return pattern.sub(replace_tag, svg)


def svg_to_png(svg_path: str | Path, png_path: str | Path) -> Path:
    svg_str = Path(svg_path).read_text(encoding="utf-8")
    png_bytes = resvg_py.svg_to_bytes(svg_str)
    dest = Path(png_path)
    dest.write_bytes(png_bytes)
    return dest


# Segment highlighting

_geometry_cache: dict | None = None
_line_paths_cache: dict[str, tuple[list[str], list[str]]] | None = None
_svg_fork_geometry_cache: dict[tuple[str, str, str], "ForkGroup"] | None = None
_station_markers_cache: dict[str, "StationMarker"] | None = None
AffineTransform = tuple[float, float, float, float, float, float]
ClipPolygon = list[tuple[float, float]]
ClipShape = str | ClipPolygon


class ForkGroup(TypedDict):
    shapes: list[str]  # raw SVG fragments, emitted verbatim into the clipPath
    tails: list[tuple[str, float, float]]  # (station_name, cx, cy)
    waypoints: list[tuple[int, float, float]]  # (index, cx, cy), chain sorted ascending


class CircleMarker(TypedDict):
    kind: Literal["circle"]
    cx: float
    cy: float
    radius: float


class RectMarker(TypedDict):
    kind: Literal["rect"]
    x: float
    y: float
    width: float
    height: float
    rx: float
    ry: float
    transform: AffineTransform
    inverse_transform: AffineTransform


StationMarker = CircleMarker | RectMarker


def _parse_style_number(style: str, key: str) -> float | None:
    match = re.search(rf"(?:^|;)\s*{re.escape(key)}\s*:\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", style)
    if match:
        return float(match.group(1))
    return None


def _element_stroke_width(el: ET.Element) -> float:
    stroke_width = el.get("stroke-width")
    if stroke_width:
        return float(stroke_width)
    style = el.get("style", "")
    parsed = _parse_style_number(style, "stroke-width")
    return parsed if parsed is not None else 0.0


def _affine_identity() -> AffineTransform:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _affine_multiply(lhs: AffineTransform, rhs: AffineTransform) -> AffineTransform:
    a1, b1, c1, d1, e1, f1 = lhs
    a2, b2, c2, d2, e2, f2 = rhs
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _apply_affine(transform: AffineTransform, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    return (a * x + c * y + e, b * x + d * y + f)


def _invert_affine(transform: AffineTransform) -> AffineTransform:
    a, b, c, d, e, f = transform
    det = a * d - b * c
    if math.isclose(det, 0.0, abs_tol=1e-12):
        raise ValueError("Non-invertible marker transform")
    return (
        d / det,
        -b / det,
        -c / det,
        a / det,
        (c * f - d * e) / det,
        (b * e - a * f) / det,
    )


def _parse_transform(transform: str) -> AffineTransform:
    if not transform.strip():
        return _affine_identity()

    combined = _affine_identity()
    for name, raw_args in re.findall(r"([A-Za-z]+)\(([^)]*)\)", transform):
        args = [float(value) for value in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", raw_args)]
        if name == "matrix" and len(args) == 6:
            op: AffineTransform = (args[0], args[1], args[2], args[3], args[4], args[5])
        elif name == "translate" and args:
            tx = args[0]
            ty = args[1] if len(args) >= 2 else 0.0
            op = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale" and args:
            sx = args[0]
            sy = args[1] if len(args) >= 2 else sx
            op = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and args:
            angle = math.radians(args[0])
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            rotate: AffineTransform = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(args) >= 3:
                cx = args[1]
                cy = args[2]
                op = _affine_multiply(
                    (1.0, 0.0, 0.0, 1.0, cx, cy),
                    _affine_multiply(rotate, (1.0, 0.0, 0.0, 1.0, -cx, -cy)),
                )
            else:
                op = rotate
        else:
            raise ValueError(f"Unsupported marker transform: {transform}")

        combined = _affine_multiply(op, combined)

    return combined


def _extract_station_markers(svg_text: str) -> dict[str, StationMarker]:
    root = ET.fromstring(svg_text)
    markers: dict[str, StationMarker] = {}

    for el in root.iter():
        marker_id = el.get("id", "")
        if not marker_id.endswith(" Marker"):
            continue

        tag = el.tag.split("}")[-1]
        station = marker_id[: -len(" Marker")]
        stroke_width = _element_stroke_width(el)

        if tag == "circle":
            cx = el.get("cx")
            cy = el.get("cy")
            radius = el.get("r")
            if cx is None or cy is None or radius is None:
                continue
            markers[station] = {
                "kind": "circle",
                "cx": float(cx),
                "cy": float(cy),
                "radius": float(radius) + stroke_width / 2.0,
            }
            continue

        if tag != "rect":
            continue

        x = el.get("x")
        y = el.get("y")
        width = el.get("width")
        height = el.get("height")
        if x is None or y is None or width is None or height is None:
            continue

        rx_attr = el.get("rx")
        ry_attr = el.get("ry")
        rx = float(rx_attr) if rx_attr is not None else 0.0
        ry = float(ry_attr) if ry_attr is not None else rx
        x_val = float(x) - stroke_width / 2.0
        y_val = float(y) - stroke_width / 2.0
        width_val = float(width) + stroke_width
        height_val = float(height) + stroke_width
        rx = min(rx + stroke_width / 2.0, width_val / 2.0)
        ry = min(ry + stroke_width / 2.0, height_val / 2.0)
        transform = _parse_transform(el.get("transform", ""))

        markers[station] = {
            "kind": "rect",
            "x": x_val,
            "y": y_val,
            "width": width_val,
            "height": height_val,
            "rx": rx,
            "ry": ry,
            "transform": transform,
            "inverse_transform": _invert_affine(transform),
        }

    return markers


def _get_station_markers() -> dict[str, StationMarker]:
    global _station_markers_cache
    if _station_markers_cache is not None:
        return _station_markers_cache
    _station_markers_cache = _extract_station_markers(SVG_SOURCE.read_text(encoding="utf-8"))
    return _station_markers_cache


def _point_in_station_marker(marker: StationMarker, x: float, y: float) -> bool:
    if marker["kind"] == "circle":
        dx = x - marker["cx"]
        dy = y - marker["cy"]
        return dx * dx + dy * dy <= marker["radius"] * marker["radius"] + 1e-6

    local_x, local_y = _apply_affine(marker["inverse_transform"], x, y)
    left = marker["x"]
    top = marker["y"]
    right = left + marker["width"]
    bottom = top + marker["height"]
    if local_x < left or local_x > right or local_y < top or local_y > bottom:
        return False

    rx = marker["rx"]
    ry = marker["ry"]
    inner_left = left + rx
    inner_right = right - rx
    inner_top = top + ry
    inner_bottom = bottom - ry
    if inner_left <= local_x <= inner_right or inner_top <= local_y <= inner_bottom:
        return True

    corner_x = inner_left if local_x < inner_left else inner_right
    corner_y = inner_top if local_y < inner_top else inner_bottom
    if math.isclose(rx, 0.0, abs_tol=1e-9) or math.isclose(ry, 0.0, abs_tol=1e-9):
        return True
    dx = (local_x - corner_x) / rx
    dy = (local_y - corner_y) / ry
    return dx * dx + dy * dy <= 1.0 + 1e-6


def _marker_exit_point(
    station: str,
    origin_x: float,
    origin_y: float,
    toward_x: float,
    toward_y: float,
    station_markers: dict[str, StationMarker] | None,
) -> tuple[float, float]:
    marker = (station_markers or {}).get(station)
    if marker is None:
        return origin_x, origin_y

    dx = toward_x - origin_x
    dy = toward_y - origin_y
    length = math.hypot(dx, dy)
    if length == 0:
        return origin_x, origin_y
    if not _point_in_station_marker(marker, origin_x, origin_y):
        return origin_x, origin_y

    ux = dx / length
    uy = dy / length
    low = 0.0
    high = 1.0
    while high < 1024.0 and _point_in_station_marker(marker, origin_x + ux * high, origin_y + uy * high):
        low = high
        high *= 2.0
    if _point_in_station_marker(marker, origin_x + ux * high, origin_y + uy * high):
        return origin_x, origin_y

    for _ in range(24):
        mid = (low + high) / 2.0
        if _point_in_station_marker(marker, origin_x + ux * mid, origin_y + uy * mid):
            low = mid
        else:
            high = mid

    return origin_x + ux * high, origin_y + uy * high


def _split_segment_identifier(identifier: str) -> tuple[str | None, str | None, str | None]:
    if not identifier:
        return None, None, None
    parts = identifier.split(":", 2)
    if len(parts) != 3:
        return None, None, None
    return parts[0], parts[1], parts[2]


def _sorted_station_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _escape_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _format_shape_element(el: ET.Element) -> str | None:
    """Serialise a path/rect/polygon to minimal SVG, keeping only geometry attributes."""
    tag = el.tag.split("}")[-1]
    if tag == "path":
        d = el.get("d")
        if not d:
            return None
        return f'<path d="{_escape_attr(d)}"/>'
    if tag == "rect":
        width = el.get("width")
        height = el.get("height")
        if width is None or height is None:
            return None
        x = el.get("x", "0")
        y = el.get("y", "0")
        attrs = [
            f'x="{_escape_attr(x)}"',
            f'y="{_escape_attr(y)}"',
            f'width="{_escape_attr(width)}"',
            f'height="{_escape_attr(height)}"',
        ]
        for name in ("rx", "ry", "transform"):
            val = el.get(name)
            if val:
                attrs.append(f'{name}="{_escape_attr(val)}"')
        return f"<rect {' '.join(attrs)}/>"
    if tag == "polygon":
        points = el.get("points")
        if not points:
            return None
        return f'<polygon points="{_escape_attr(points)}"/>'
    return None


def _extract_svg_fork_geometry(
    svg_text: str,
    line_segments: dict[str, list[list[str]]],
) -> dict[tuple[str, str, str], ForkGroup]:
    """Parse the Path Overrides layer into per-segment ForkGroups."""
    root = ET.fromstring(svg_text)
    label_attr = "{http://www.inkscape.org/namespaces/inkscape}label"
    layer = next(
        (el for el in root.iter() if el.get(label_attr) == "Path Overrides" or el.get("id") == "Path Overrides"),
        None,
    )
    if layer is None:
        return {}

    segment_pairs: dict[str, set[tuple[str, str]]] = {}
    for line, segments in line_segments.items():
        pairs = segment_pairs.setdefault(line, set())
        for seg in segments:
            if len(seg) == 2:
                pairs.add(_sorted_station_pair(seg[0], seg[1]))

    groups: dict[tuple[str, str, str], ForkGroup] = {}
    for group_el in layer:
        if group_el.tag.split("}")[-1] != "g":
            continue

        group_id = group_el.get("id", "")
        line, raw_a, raw_b = _split_segment_identifier(group_id)
        if line is None or raw_a is None or raw_b is None:
            continue
        if line not in segment_pairs:
            raise ValueError(f"Path Overrides group references unknown line: {group_id}")
        sorted_pair = _sorted_station_pair(raw_a, raw_b)
        if sorted_pair not in segment_pairs[line]:
            raise ValueError(f"Path Overrides group references unknown segment: {group_id}")

        shapes: list[str] = []
        tails: list[tuple[str, float, float]] = []
        waypoints: list[tuple[int, float, float]] = []
        seen_waypoint_indices: set[int] = set()
        stations_in_group = (raw_a, raw_b)

        for child in group_el:
            tag = child.tag.split("}")[-1]
            if tag == "circle":
                child_id = child.get("id", "")
                tail_match = re.search(r":tail:(.+)$", child_id)
                waypoint_match = re.search(r":waypoint:(\d+)$", child_id)
                if tail_match:
                    station = tail_match.group(1).strip()
                    if station not in stations_in_group:
                        raise ValueError(f"Tail circle references station not in group {group_id}: {station}")
                    cx = child.get("cx")
                    cy = child.get("cy")
                    if cx is None or cy is None:
                        continue
                    tails.append((station, float(cx), float(cy)))
                    continue
                if waypoint_match:
                    index = int(waypoint_match.group(1))
                    if index in seen_waypoint_indices:
                        raise ValueError(f"Duplicate waypoint index in {group_id}: {index}")
                    seen_waypoint_indices.add(index)
                    cx = child.get("cx")
                    cy = child.get("cy")
                    if cx is None or cy is None:
                        continue
                    waypoints.append((index, float(cx), float(cy)))
                    continue
                continue  # unrecognised circle id — ignored

            shape_svg = _format_shape_element(child)
            if shape_svg is not None:
                shapes.append(shape_svg)

        if not shapes and not tails and not waypoints:
            continue

        waypoints.sort(key=lambda w: w[0])

        key = (line, raw_a, raw_b)
        if key in groups:
            raise ValueError(f"Duplicate Path Overrides group: {group_id}")
        groups[key] = {"shapes": shapes, "tails": tails, "waypoints": waypoints}

    return groups


def _get_svg_fork_geometry(
    line_segments: dict[str, list[list[str]]],
) -> dict[tuple[str, str, str], ForkGroup]:
    global _svg_fork_geometry_cache
    if _svg_fork_geometry_cache is not None:
        return _svg_fork_geometry_cache
    _svg_fork_geometry_cache = _extract_svg_fork_geometry(
        SVG_SOURCE.read_text(encoding="utf-8"),
        line_segments,
    )
    return _svg_fork_geometry_cache


def _load_geometry() -> dict:
    global _geometry_cache
    if _geometry_cache is not None:
        return _geometry_cache
    with open(GEOMETRY_PATH) as f:
        data = json.load(f)
    _geometry_cache = data
    return data


def _get_line_paths() -> dict[str, tuple[list[str], list[str]]]:
    """Return ``{line_id: (colored_d_attrs, white_d_attrs)}``, parsed once and cached."""
    global _line_paths_cache
    if _line_paths_cache is not None:
        return _line_paths_cache
    _line_paths_cache = _extract_line_paths(SVG_SOURCE.read_text(encoding="utf-8"))
    return _line_paths_cache


def _clip_rect(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    hw_a: float = _SEGMENT_HALF_W,
    hw_b: float = _SEGMENT_HALF_W,
    pad_a: float = _SEGMENT_PAD,
    pad_b: float = _SEGMENT_PAD,
) -> list[tuple[float, float]]:
    dx = bx - ax
    dy = by - ay
    length = math.hypot(dx, dy)
    if length == 0:
        return []

    ux, uy = dx / length, dy / length
    px, py = -uy, ux  # 90° CCW

    return [
        (ax - ux * pad_a + px * hw_a, ay - uy * pad_a + py * hw_a),
        (bx + ux * pad_b + px * hw_b, by + uy * pad_b + py * hw_b),
        (bx + ux * pad_b - px * hw_b, by + uy * pad_b - py * hw_b),
        (ax - ux * pad_a - px * hw_a, ay - uy * pad_a - py * hw_a),
    ]


def _clip_shapes_for_segment(
    line_key: str,
    a: str,
    b: str,
    centres: dict[str, list[float]],
    fork_groups: dict[tuple[str, str, str], ForkGroup] | None = None,
    station_markers: dict[str, StationMarker] | None = None,
) -> list[ClipShape]:
    fork_groups = fork_groups or {}
    reversed_group = False
    group = fork_groups.get((line_key, a, b))
    if group is None:
        group = fork_groups.get((line_key, b, a))
        reversed_group = group is not None

    shapes: list[ClipShape] = []

    if group is not None:
        shapes.extend(group["shapes"])
        for station, cx, cy in group["tails"]:
            sx, sy = centres[station]
            end_x, end_y = _marker_exit_point(station, sx, sy, cx, cy, station_markers)
            poly = _clip_rect(cx, cy, end_x, end_y, pad_a=0.0, pad_b=_SEGMENT_PAD)
            if len(poly) >= 3:
                shapes.append(poly)
        waypoints = group["waypoints"]
        if waypoints:
            # Waypoints are indexed in the group's declared station order (A→B).
            # If the segment was looked up reversed, flip them so the chain runs
            # from `a` to `b` correctly.
            ordered_wps = waypoints[::-1] if reversed_group else waypoints
            ax, ay = centres[a]
            bx, by = centres[b]
            chain: list[tuple[float, float]] = [(ax, ay)]
            chain.extend((x, y) for _, x, y in ordered_wps)
            chain.append((bx, by))
            for i in range(len(chain) - 1):
                x1, y1 = chain[i]
                x2, y2 = chain[i + 1]
                poly = _clip_rect(x1, y1, x2, y2)
                if len(poly) >= 3:
                    shapes.append(poly)
        return shapes

    ax, ay = centres[a]
    bx, by = centres[b]
    start_x, start_y = _marker_exit_point(a, ax, ay, bx, by, station_markers)
    end_x, end_y = _marker_exit_point(b, bx, by, ax, ay, station_markers)
    poly = _clip_rect(start_x, start_y, end_x, end_y)
    if len(poly) >= 3:
        shapes.append(poly)
    return shapes


def _get_fill(el: ET.Element) -> str:
    fill = el.get("fill", "")
    if fill:
        return fill
    style = el.get("style", "")
    m = re.search(r"fill:([^;]+)", style)
    return m.group(1) if m else ""


def _is_white(color: str) -> bool:
    return color.strip().lower() in ("#fff", "#ffffff", "white")


def _extract_line_paths(svg_text: str) -> dict[str, tuple[list[str], list[str]]]:
    root = ET.fromstring(svg_text)
    line_paths: dict[str, tuple[list[str], list[str]]] = {}

    for el in root:
        eid = el.get("id", "")
        tag = el.tag.split("}")[-1]

        if tag == "path":
            fill = _get_fill(el)
            d = el.get("d", "")
            if fill and d:
                if _is_white(fill):
                    line_paths.setdefault(eid, ([], []))[1].append(d)
                else:
                    line_paths.setdefault(eid, ([], []))[0].append(d)

        elif tag == "g":
            colored: list[str] = []
            white: list[str] = []
            for child in el.iter():
                if child is el:
                    continue
                if child.tag.split("}")[-1] != "path":
                    continue
                fill = _get_fill(child)
                d = child.get("d", "")
                if fill and d:
                    if _is_white(fill):
                        white.append(d)
                    else:
                        colored.append(d)
            if colored or white:
                line_paths[eid] = (colored, white)

    return line_paths


def _build_segment_overlays(game: GameState, *, debug: bool = False) -> str:
    """Return SVG markup for body/neck segment highlights.

    When *debug* is True, an extra layer shows every clip shape at 50% opacity.
    """
    geometry = _load_geometry()
    centres: dict[str, list[float]] = geometry["station_centres"]
    line_segments: dict[str, list[list[str]]] = geometry["line_segments"]
    line_paths = _get_line_paths()
    station_markers = _get_station_markers()
    fork_groups = _get_svg_fork_geometry(line_segments)

    highlights: dict[tuple[str, str, str], tuple[str, str]] = {}

    for team, snake in game.snakes.items():
        if snake.crashed:
            continue
        color = snake.color
        for seg in game.map.segments_claimed_by(team):
            highlights[seg] = (color, "body")
        if snake.neck_active and snake.declared_line:
            neck_path = [snake.anchor] + game.neck(team)
            for i in range(len(neck_path) - 1):
                station_a, station_b = sorted((neck_path[i], neck_path[i + 1]))
                key = (snake.declared_line, station_a, station_b)
                if key not in highlights:
                    highlights[key] = (color, "neck")

    if not highlights:
        return ""

    clip_groups: dict[tuple[str, str, str], list[ClipShape]] = {}

    for (line_key, a, b), (color, mode) in highlights.items():
        shapes = _clip_shapes_for_segment(
            line_key,
            a,
            b,
            centres,
            fork_groups,
            station_markers,
        )
        gkey = (line_key, color, mode)
        clip_groups.setdefault(gkey, []).extend(shapes)

    defs_parts: list[str] = []
    overlay_parts: list[str] = []
    debug_parts: list[str] = []

    pattern_ids: dict[tuple[str, str], str] = {}

    def _ensure_pattern(color: str, mode: str) -> str:
        key = (color, mode)
        if key in pattern_ids:
            return pattern_ids[key]
        pat_id = f"stripe-{len(pattern_ids)}"
        pattern_ids[key] = pat_id
        dot_color = color if mode == "body" else _tint_color(color, NECK_TINT_FACTOR)
        # Hex close-packing tile: width=d, height=d·√3, corner dots + offset centre dot.
        w = _DOT_PERIOD
        h = _DOT_PERIOD * math.sqrt(3)
        cx, cy = w / 2, h / 2
        defs_parts.append(
            f'<pattern id="{pat_id}" x="0" y="0" width="{w:.3f}" height="{h:.3f}"'
            f' patternUnits="userSpaceOnUse">'
            f'<rect x="0" y="0" width="{w:.3f}" height="{h:.3f}" fill="#ffffff"/>'
            f'<circle cx="0"       cy="0"       r="{_DOT_RADIUS:.2f}" fill="{dot_color}"/>'
            f'<circle cx="{w:.3f}" cy="0"       r="{_DOT_RADIUS:.2f}" fill="{dot_color}"/>'
            f'<circle cx="0"       cy="{h:.3f}" r="{_DOT_RADIUS:.2f}" fill="{dot_color}"/>'
            f'<circle cx="{w:.3f}" cy="{h:.3f}" r="{_DOT_RADIUS:.2f}" fill="{dot_color}"/>'
            f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{_DOT_RADIUS:.2f}" fill="{dot_color}"/>'
            f"</pattern>"
        )
        return pat_id

    for i, ((line_key, color, mode), shapes) in enumerate(clip_groups.items()):
        if not shapes:
            continue
        clip_id = f"seg-clip-{i}"
        shapes_svg = "".join(
            shape
            if isinstance(shape, str)
            else '<polygon points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in shape) + '"/>'
            for shape in shapes
        )
        defs_parts.append(f'<clipPath id="{clip_id}">{shapes_svg}</clipPath>')

        colored_paths, white_paths = line_paths.get(line_key, ([], []))
        if not colored_paths:
            continue

        pat_id = _ensure_pattern(color, mode)
        all_paths = colored_paths + white_paths

        if mode == "body":
            border_w = _BORDER_W * 2
            # Pass 1 — white outer ring
            for shape in shapes:
                if isinstance(shape, str):
                    overlay_parts.append(
                        re.sub(
                            r"/>$",
                            f' fill="none" stroke="#ffffff" stroke-width="{border_w:.2f}"/>',
                            shape,
                        )
                    )
            for d_attr in colored_paths:
                overlay_parts.append(
                    f'<path d="{d_attr}" style="fill:none;stroke:#ffffff;stroke-width:{border_w:.2f}"'
                    f' clip-path="url(#{clip_id})"/>'
                )
            # Pass 2 — team-colour outline (sits on top of white ring)
            for shape in shapes:
                if isinstance(shape, str):
                    overlay_parts.append(
                        re.sub(
                            r"/>$",
                            f' fill="none" stroke="{color}" stroke-width="{_BORDER_W:.2f}"/>',
                            shape,
                        )
                    )
            for d_attr in colored_paths:
                overlay_parts.append(
                    f'<path d="{d_attr}" style="fill:none;stroke:{color};stroke-width:{_BORDER_W:.2f}"'
                    f' clip-path="url(#{clip_id})"/>'
                )
            # Pass 3 — dot fill covers interior and inner stroke halves
            for shape in shapes:
                if isinstance(shape, str):
                    overlay_parts.append(
                        re.sub(
                            r"/>$",
                            f' fill="url(#{pat_id})" stroke="none"/>',
                            shape,
                        )
                    )
            for d_attr in all_paths:
                overlay_parts.append(
                    f'<path d="{d_attr}" style="fill:url(#{pat_id});stroke:none" clip-path="url(#{clip_id})"/>'
                )
        else:
            double_w = _BORDER_W * 2
            # Pass 1 — dashed strokes at doubled width (inner half covered by fill)
            for shape in shapes:
                if isinstance(shape, str):
                    stroked = re.sub(
                        r"/>$",
                        f' fill="none" stroke="{color}" stroke-width="{double_w:.2f}"'
                        f' stroke-dasharray="{NECK_STROKE_DASHARRAY}"/>',
                        shape,
                    )
                    overlay_parts.append(stroked)
            for d_attr in colored_paths:
                overlay_parts.append(
                    f'<path d="{d_attr}" style="fill:none;stroke:{color}'
                    f';stroke-width:{double_w:.2f};stroke-dasharray:{NECK_STROKE_DASHARRAY}"'
                    f' clip-path="url(#{clip_id})"/>'
                )
            # Pass 2 — dot fills cover inner stroke halves
            for shape in shapes:
                if isinstance(shape, str):
                    filled = re.sub(r"/>$", f' fill="url(#{pat_id})" stroke="none"/>', shape)
                    overlay_parts.append(filled)
            for d_attr in all_paths:
                overlay_parts.append(
                    f'<path d="{d_attr}" style="fill:url(#{pat_id});stroke:none" clip-path="url(#{clip_id})"/>'
                )

        if debug:
            for shape in shapes:
                if isinstance(shape, str):
                    # String shapes have no fill attribute — inject one before the closing />
                    debug_shape = re.sub(r"/>$", f' fill="{color}" fill-opacity="0.5" stroke="none"/>', shape)
                else:
                    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in shape)
                    debug_shape = f'<polygon points="{pts}" fill="{color}" fill-opacity="0.5" stroke="none"/>'
                debug_parts.append(debug_shape)

    if not defs_parts and not overlay_parts:
        return ""

    parts: list[str] = []
    if defs_parts:
        parts.append("<defs>\n" + "\n".join(defs_parts) + "\n</defs>")
    parts.extend(overlay_parts)
    if debug_parts:
        parts.append('<g id="debug-clip-shapes">' + "".join(debug_parts) + "</g>")
    return "\n".join(parts)
