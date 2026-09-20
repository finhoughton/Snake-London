"""Extract station centre coordinates from the SVG and write geometry.json."""

import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

SVG_PATH = "map/snake map.svg"
CONNECTIONS_PATH = "map/connections.json"
OUTPUT_PATH = "map/geometry.json"

# Only what a marker in the base map actually carries. render.py has the general
# parser (full affine, composed ops); these two must agree on what the SVG may contain,
# so anything added there that a marker could wear belongs here too.
ParsedTransform = (
    tuple[Literal["unknown"], str]
    | tuple[Literal["translate"], float, float]
    | tuple[Literal["scale"], float, float]
    | tuple[Literal["rotate"], float, float, float]
)


_PLACEHOLDER_ID = re.compile(r"^[a-z]+\d+$")  # Inkscape's own ids: g278, path12, rect3
_G_TAG = re.compile(r"<(/?)g\b[^>]*?(/?)>")


def _overrides_span(svg: str) -> tuple[int, int] | None:
    """Character range of the Path Overrides layer."""
    marker = svg.find('id="Path Overrides"')  # the layer is named by id here, label elsewhere
    if marker == -1:
        marker = svg.find('inkscape:label="Path Overrides"')
    if marker == -1:
        return None
    start = svg.rfind("<g", 0, marker)
    depth, i = 1, svg.index(">", marker) + 1
    while depth:
        tag = _G_TAG.search(svg, i)
        if tag is None:
            return None
        i = tag.end()
        if tag.group(2):  # self-closing
            continue
        depth += -1 if tag.group(1) else 1
    return start, i


_TRANSLATE = re.compile(r"^\s*translate\(\s*(-?[\d.]+)\s*[, ]\s*(-?[\d.]+)\s*\)\s*$")
_NUMBER = re.compile(r"-?\d*\.?\d+")


def _shift_path(d: str, dx: float, dy: float) -> str | None:
    """Move a path by shifting its opening coordinates; None if that isn't safe.

    Only sound when every command after the first is relative, which is how Inkscape
    writes these. An absolute command later on would stay put and tear the shape.
    """
    body = d.strip()
    if not body or body[0] not in "mM":
        return None
    if any(ch.isupper() and ch not in "M" for ch in body[1:]):
        return None
    first = _NUMBER.search(body, 1)
    second = _NUMBER.search(body, first.end()) if first else None
    if not second:
        return None
    x, y = float(first.group()) + dx, float(second.group()) + dy
    return f"{body[: first.start()]}{x:.6g},{y:.6g}{body[second.end() :]}"


def flatten_override_transforms(svg_path: str = SVG_PATH) -> list[tuple[str, str]]:
    """Bake a group's transform into the coordinates inside it.

    Inkscape records a drag as a transform on the group, but the renderer reads each
    circle's cx/cy and each path's data as written and ignores transforms — so a waypoint
    you moved would still render where it was. Groups it can't safely flatten are left
    alone with a warning rather than half-moved.
    """
    svg = Path(svg_path).read_text(encoding="utf-8")
    span = _overrides_span(svg)
    if span is None:
        return []
    start, end = span
    layer, flattened = svg[start:end], []

    while True:
        for tag in re.finditer(r"<g\b[^>]*>", layer):
            found_transform = re.search(r'\stransform="([^"]*)"', tag.group(0))
            if not found_transform:
                continue
            group_id = (re.search(r'\sid="([^"]*)"', tag.group(0)) or re.match("", "")).group(1)
            move = _TRANSLATE.match(found_transform.group(1))
            if not move:
                print(f"  {group_id!r}: transform {found_transform.group(1)!r} is not a translate, left alone")
                continue
            dx, dy = float(move.group(1)), float(move.group(2))
            close = layer.index("</g>", tag.end())
            body, rewritten = layer[tag.end() : close], None

            def shift(match: re.Match[str], dx: float = dx, dy: float = dy) -> str:
                name, value = match.group(1), float(match.group(2))
                return f'{name}="{value + (dx if name in ("cx", "x") else dy):.6g}"'

            rewritten = re.sub(r'\b(cx|cy|x|y)="(-?[\d.]+)"', shift, body)
            safe = True
            for path in re.finditer(r'\sd="([^"]*)"', rewritten):
                moved = _shift_path(path.group(1), dx, dy)
                if moved is None:
                    safe = False
                    break
                rewritten = rewritten.replace(f'd="{path.group(1)}"', f'd="{moved}"', 1)
            if not safe:
                print(f"  {group_id!r}: path data can't be shifted safely, transform left in place")
                continue

            layer = (
                layer[: tag.start()] + tag.group(0).replace(found_transform.group(0), "") + rewritten + layer[close:]
            )
            flattened.append((group_id, found_transform.group(1)))
            break
        else:
            break

    if flattened:
        Path(svg_path).write_text(svg[:start] + layer + svg[end:], encoding="utf-8")
    return flattened


def _known_segment_ids(conn: dict) -> set[str]:
    stations = conn["stations"]
    return {
        f"{line_key}:{station}:{neighbour}"
        for line_key, line_data in conn["lines"].items()
        for station in line_data["stations"]
        for neighbour in stations[station].get(line_key, [])
    }


def normalise_override_ids(svg_path: str = SVG_PATH, connections_path: str = CONNECTIONS_PATH) -> list[tuple[str, str]]:
    """Give override groups the id the renderer looks them up by.

    Inkscape makes this awkward from both directions: Object Properties writes the name you
    type into `inkscape:label` and leaves the id as `g278`, and its ID field replaces spaces
    and apostrophes with underscores, so "Central:Bank:St Paul's" becomes an id no segment
    matches. Both are repaired here — a label naming a real segment becomes the id, and an
    underscored id is matched back to the one real segment it can mean. The file is edited as
    text so the rest of the hand-designed map stays byte for byte.
    """
    svg = Path(svg_path).read_text(encoding="utf-8")
    span = _overrides_span(svg)
    if span is None:
        return []
    with open(connections_path) as f:
        conn = json.load(f)
    known = _known_segment_ids(conn)
    renamed: list[tuple[str, str]] = []

    def fix(match: re.Match[str]) -> str:
        tag = match.group(0)
        found_id = re.search(r'\sid="([^"]*)"', tag)
        if not found_id:
            return tag
        group_id = found_id.group(1)
        found_label = re.search(r'\sinkscape:label="([^"]*)"', tag)

        wanted = None
        if found_label and _PLACEHOLDER_ID.match(group_id) and found_label.group(1) in known:
            wanted = found_label.group(1)
        elif group_id not in known and "_" in group_id and group_id.count(":") == 2:
            pattern = "".join("." if ch == "_" else re.escape(ch) for ch in group_id)
            candidates = [name for name in known if re.fullmatch(pattern, name)]
            if len(candidates) == 1:
                wanted = candidates[0]
            else:
                print(f"  {group_id!r}: matches {len(candidates)} segments, left alone")
        if wanted is None:
            return tag

        renamed.append((group_id, wanted))
        tag = tag.replace(found_id.group(0), f' id="{wanted}"')
        return tag.replace(found_label.group(0), "") if found_label else tag

    start, end = span
    fixed = re.sub(r"<g\b[^>]*>", fix, svg[start:end])
    if renamed:
        Path(svg_path).write_text(svg[:start] + fixed + svg[end:], encoding="utf-8")
    return renamed


def _parse_transform(t: str) -> ParsedTransform | None:
    if not t:
        return None
    t = t.strip()
    m = re.match(r"rotate\(([^,)]+)(?:,([^,)]+),([^)]+))?\)", t)
    if m:
        angle = float(m.group(1))
        cx = float(m.group(2)) if m.group(2) else 0.0
        cy = float(m.group(3)) if m.group(3) else 0.0
        return ("rotate", angle, cx, cy)
    m = re.match(r"scale\(([^)]+)\)", t)
    if m:
        parts = [float(v) for v in m.group(1).split(",")]
        sx = parts[0]
        sy = parts[1] if len(parts) > 1 else sx
        return ("scale", sx, sy)
    m = re.match(r"translate\(([^)]+)\)", t)
    if m:
        parts = [float(v) for v in m.group(1).replace(",", " ").split()]
        return ("translate", parts[0], parts[1] if len(parts) > 1 else 0.0)
    return ("unknown", t)


def _apply_transform(cx: float, cy: float, parsed: ParsedTransform) -> tuple[float, float]:
    if parsed[0] == "rotate":
        _, angle, ox, oy = parsed
        a = math.radians(angle)
        dx, dy = cx - ox, cy - oy
        return (dx * math.cos(a) - dy * math.sin(a) + ox, dx * math.sin(a) + dy * math.cos(a) + oy)
    if parsed[0] == "scale":
        _, sx, sy = parsed
        return (cx * sx, cy * sy)
    if parsed[0] == "translate":
        _, tx, ty = parsed
        return (cx + tx, cy + ty)
    raise ValueError(f"Unknown transform: {parsed}")


def extract_centres(svg_path: str, connections_path: str) -> dict[str, list[float]]:
    tree = ET.parse(svg_path)
    root = tree.getroot()

    with open(connections_path) as f:
        conn = json.load(f)

    all_stations: set[str] = set()
    for line_data in conn["lines"].values():
        for s in line_data["stations"]:
            all_stations.add(s)

    id_map: dict[str, ET.Element] = {}
    for el in root.iter():
        eid = el.get("id")
        if eid:
            id_map[eid] = el

    centres: dict[str, list[float]] = {}
    for station in sorted(all_stations):
        marker_id = f"{station} Marker"
        el = id_map.get(marker_id)
        if el is None:
            raise ValueError(f"Missing marker for {station!r}")

        tag = el.tag.split("}")[-1]
        transform = el.get("transform", "")
        parsed = _parse_transform(transform)

        if tag == "circle":
            cx = float(el.get("cx", 0))
            cy = float(el.get("cy", 0))
        elif tag == "rect":
            x = float(el.get("x", 0))
            y = float(el.get("y", 0))
            w = float(el.get("width", 0))
            h = float(el.get("height", 0))
            cx = x + w / 2
            cy = y + h / 2
        else:
            raise ValueError(f"Unexpected tag <{tag}> for {marker_id!r}")

        if parsed:
            cx, cy = _apply_transform(cx, cy, parsed)

        centres[station] = [round(cx, 2), round(cy, 2)]

    return centres


def build_segments(connections_path: str) -> dict[str, list[list[str]]]:
    """Return {line_key: [[stationA, stationB], ...]} for adjacent pairs."""
    with open(connections_path) as f:
        conn = json.load(f)

    segments: dict[str, list[list[str]]] = {}
    stations_data = conn["stations"]

    for line_key, line_data in conn["lines"].items():
        pairs: list[list[str]] = []
        seen: set[tuple[str, str]] = set()
        for station_key in line_data["stations"]:
            neighbours = stations_data[station_key].get(line_key, [])
            for neighbour in neighbours:
                pair = tuple(sorted([station_key, neighbour]))
                if pair not in seen:
                    seen.add(pair)
                    pairs.append([station_key, neighbour])
        segments[line_key] = pairs
    return segments


if __name__ == "__main__":
    for group_id, transform in flatten_override_transforms():
        print(f"Override group {group_id!r}: baked {transform} into its coordinates")

    for old_id, new_id in normalise_override_ids():
        print(f"Override group {old_id} renamed to {new_id!r} (the renderer looks these up by id)")

    centres = extract_centres(SVG_PATH, CONNECTIONS_PATH)
    segments = build_segments(CONNECTIONS_PATH)

    geometry = {
        "station_centres": centres,
        "line_segments": segments,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(geometry, f, indent=2)

    total_segments = sum(len(v) for v in segments.values())
    print(f"Extracted {len(centres)} station centres and {total_segments} line segments.")
    print(f"Written to {OUTPUT_PATH}")
