from __future__ import annotations

import re
import shutil
from pathlib import Path

import resvg_py

from game import GameState

SVG_SOURCE = Path("snake map.svg")
UNCLAIMED_COLOR = "#ffffff"
NECK_STROKE_DASHARRAY = "4 2"
NECK_STROKE_WIDTH = "3.75"
NECK_TINT_FACTOR = 0.55  # 0 = team color, 1 = white


def init_map(output_path: str | Path) -> Path:
    """Copy the base SVG to output_path as a clean working copy.

    Call this once at game start.  Returns the output path.
    """
    dest = Path(output_path)
    shutil.copy(SVG_SOURCE, dest)
    return dest


def render_map(game: GameState, output_path: str | Path) -> Path:
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
    """Update the style of a station marker element for body or neck display.

    body: solid team-color fill, black stroke.
    neck: team colour tinted fill, dashed team-color stroke.
    """
    pattern = re.compile(
        r'(<(?:circle|rect)\b[^>]*\bid="' + re.escape(marker_id) + r'"[^>]*>)',
        re.DOTALL,
    )

    if mode == "body":
        new_style = f"fill:{color};stroke:#000000;stroke-width:3.75"
    else:  # neck
        tint = _tint_color(color, NECK_TINT_FACTOR)
        new_style = (
            f"fill:{tint};stroke:{color};stroke-width:{NECK_STROKE_WIDTH};stroke-dasharray:{NECK_STROKE_DASHARRAY}"
        )

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
    """Convert an SVG file to PNG using resvg_py. Returns the output path."""
    svg_str = Path(svg_path).read_text(encoding="utf-8")
    png_bytes = resvg_py.svg_to_bytes(svg_str)
    dest = Path(png_path)
    dest.write_bytes(png_bytes)
    return dest
