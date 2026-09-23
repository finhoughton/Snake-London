from typing import TYPE_CHECKING, Iterable
from pathlib import Path
from datetime import timedelta
import time, os

if TYPE_CHECKING:
    from game import GameState
from render import render_map, svg_to_png

from discord import Embed, OptionChoice

map_dir = Path() / "generated_maps"
if not map_dir.exists():
    os.mkdir(map_dir)

map_embed = Embed()
map_embed.set_image(url="attachment://map.png")

def generate_new_map(gctx: GameState):
    map_png_path = map_dir / f"{int(time.time())}.{gctx.thread_id}.png"
    map_svg_path = map_png_path.with_suffix(".svg")
    svg_to_png(render_map(gctx, map_svg_path), map_png_path)

    # Maintain the most recent 10 generated map files
    files = sorted(os.listdir(map_dir), reverse=True)
    while len(files) > 10:
        os.unlink(map_dir / files.pop())

    embed = map_embed.copy()
    embed.set_footer(text=f"Time elapsed: {timedelta(seconds=gctx.game_time_now() // 1000)}")

    return embed, map_png_path

def choices(options: Iterable[tuple[str, str]], typed: str) -> list[OptionChoice]:
    """Autocomplete choices that show a name but send the key the engine wants.

    Matches anywhere in either, not just the start: the map calls Bank "Bank / Monument",
    and a team standing in Monument will type that. Discord shows at most 25.
    """
    typed = typed.lower()
    return [OptionChoice(shown, key) for shown, key in sorted(options) if typed in shown.lower() or typed in key.lower()]

class GameError(Exception):
    def __init__(self, message: str, *args: object) -> None:
        super().__init__(*args)
        self.message = message