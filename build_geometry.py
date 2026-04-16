"""Extract station centre coordinates from the SVG and write geometry.json."""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET


SVG_PATH = "map/snake map.svg"
CONNECTIONS_PATH = "map/connections.json"
OUTPUT_PATH = "map/geometry.json"


def _parse_transform(t: str) -> tuple | None:
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
    return ("unknown", t)


def _apply_transform(cx: float, cy: float, parsed: tuple) -> tuple[float, float]:
    kind = parsed[0]
    if kind == "rotate":
        _, angle, ox, oy = parsed
        a = math.radians(angle)
        dx, dy = cx - ox, cy - oy
        return (dx * math.cos(a) - dy * math.sin(a) + ox, dx * math.sin(a) + dy * math.cos(a) + oy)
    if kind == "scale":
        _, sx, sy = parsed
        return (cx * sx, cy * sy)
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
