from __future__ import annotations

import os
import re
import sys

from game import new_game
from render import _load_geometry, render_map, svg_to_png

TEAM = "test"
COLOR = "#FF1493"  # deep pink — distinct from all line colours
OUTPUT_DIR = "segment_debug"

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def _label(line: str, a: str, b: str) -> str:
    """Sanitise a segment into a safe filename stem."""
    return _UNSAFE.sub("_", f"{line}__{a}__{b}")


def render_segment(line: str, a: str, b: str) -> None:
    game = new_game(start_positions={TEAM: a}, team_colors={TEAM: COLOR})
    game.initial_request_challenge(TEAM)
    game.complete_challenge(TEAM, line)
    game.request_challenge(TEAM, b)
    game.complete_challenge(TEAM, line)

    stem = os.path.join(OUTPUT_DIR, _label(line, a, b))
    render_map(game, f"{stem}.svg", debug=True)
    svg_to_png(f"{stem}.svg", f"{stem}.png")
    os.unlink(f"{stem}.svg")


def main() -> None:
    geometry = _load_geometry()
    line_segments: dict[str, list[list[str]]] = geometry["line_segments"]

    filter_lines = set(sys.argv[1:])
    if filter_lines:
        unknown = filter_lines - set(line_segments)
        if unknown:
            print(f"Unknown line(s): {sorted(unknown)}")
            print(f"Available: {sorted(line_segments)}")
            sys.exit(1)
        work = {k: v for k, v in line_segments.items() if k in filter_lines}
    else:
        work = line_segments

    total = sum(len(segs) for segs in work.values())
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Rendering {total} segment(s) to {OUTPUT_DIR}/")

    done = 0
    errors: list[str] = []
    for line, segments in sorted(work.items()):
        for seg in segments:
            a, b = seg[0], seg[1]
            done += 1
            label = _label(line, a, b)
            print(f"  [{done}/{total}] {label}", end="", flush=True)
            try:
                render_segment(line, a, b)
                print()
            except Exception as exc:
                print(f"  ERROR: {exc}")
                errors.append(f"{line} {a}→{b}: {exc}")

    print(f"\n{done - len(errors)} rendered, {len(errors)} error(s)")
    if errors:
        for e in errors:
            print(f"  {e}")


if __name__ == "__main__":
    main()
