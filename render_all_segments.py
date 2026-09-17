from __future__ import annotations

import contextlib
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor


@contextlib.contextmanager
def quiet():
    """The engine and renderer print on every call, which buries the progress line."""
    with open(os.devnull, "w") as null, contextlib.redirect_stdout(null):
        yield


with quiet():
    from new_game import new_game
    from render import load_geometry, render_map, svg_to_png

TEAM = "test"
COLOR = "#FF1493"  # deep pink — distinct from all line colours
OUTPUT_DIR = "segment_debug"

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def _label(line: str, a: str, b: str) -> str:
    """Sanitise a segment into a safe filename stem."""
    return _UNSAFE.sub("_", f"{line}__{a}__{b}")


def render_segment(segment: tuple[str, str, str]) -> str | None:
    """Render one segment; returns an error description, or None if it worked.

    A whole-map render takes a couple of seconds, so the sweep runs these across every
    core — that is the only thing that makes rendering all 249 bearable.
    """
    line, a, b = segment
    stem = os.path.join(OUTPUT_DIR, _label(line, a, b))
    try:
        with quiet():
            game = new_game(start_positions={TEAM: a}, team_colors={TEAM: COLOR})
            team = game.teams[0]
            game.complete_challenge(team.role_id, line)
            game.request_challenge(team.role_id, b)
            game.complete_challenge(team.role_id, line)

            render_map(game, f"{stem}.svg", debug=True)
            svg_to_png(f"{stem}.svg", f"{stem}.png")
        os.unlink(f"{stem}.svg")
    except Exception as exc:  # noqa: BLE001 - a debug sweep reports every failure and continues
        return f"{line} {a}→{b}: {exc}"
    return None


def main() -> None:
    geometry = load_geometry()
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

    todo = [(line, seg[0], seg[1]) for line, segments in sorted(work.items()) for seg in segments]
    total = len(todo)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Rendering {total} segment(s) to {OUTPUT_DIR}/ across {os.cpu_count()} cores")

    done = 0
    errors: list[str] = []
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        for segment, error in zip(todo, pool.map(render_segment, todo)):
            done += 1
            print(f"\r  [{done}/{total}] {_label(*segment)}".ljust(76), end="", flush=True)
            if error:
                print(f"\n  ERROR: {error}")
                errors.append(error)

    print(f"\r{done - len(errors)} rendered, {len(errors)} error(s)".ljust(76))
    if errors:
        for e in errors:
            print(f"  {e}")


if __name__ == "__main__":
    main()
