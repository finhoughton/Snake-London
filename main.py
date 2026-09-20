import os
import pathlib
import time
from collections.abc import Iterable
from datetime import timedelta
from typing import cast

import discord
from discord import (
    ApplicationContext,
    AutocompleteContext,
    Embed,
    EmbedField,
    File,
    Member,
    OptionChoice,
    Role,
    option, # pyright: ignore[reportUnknownVariableType]
)

import jloxgame
from config import POWERUP_COMMANDS, POWERUP_COSTS, POWERUP_NAMES
from game import GameState
from jloxgame.bot import JLOXBot
from jloxgame.state import Status
from render import render_map, svg_to_png

with open("TOKEN", "r") as f:
    TOKEN = f.read()

bot = jloxgame.JLOXBot(GameState, pathlib.Path() / "save", member_hostable=False)


@bot.event
async def on_ready():
    bot.logger.info(f"{bot.user} is online!")


# Map

map_dir = pathlib.Path() / "generated_maps"
if not map_dir.exists():
    os.mkdir(map_dir)

map_embed = discord.Embed()
map_embed.set_image(url="attachment://map.png")


@bot.game_command()
async def map(dctx: ApplicationContext, gctx: GameState):
    map_png_path = map_dir / f"{gctx.thread_id}.{time.time():.0}.png"
    map_svg_path = map_png_path.with_suffix(".svg")
    svg_to_png(render_map(gctx, map_svg_path), map_png_path)

    embed = map_embed.copy()
    embed.set_footer(text=f"Time elapsed: {timedelta(seconds=gctx.game_time_now() // 1000)}")
    await dctx.respond(embed=embed, file=File(map_png_path, filename="map.png"))


def choices(options: Iterable[tuple[str, str]], typed: str) -> list[OptionChoice]:
    """Autocomplete choices that show a name but send the key the engine wants.

    Matches anywhere in either, not just the start: the map calls Bank "Bank / Monument",
    and a team standing in Monument will type that. Discord shows at most 25.
    """
    typed = typed.lower()
    return [OptionChoice(shown, key) for shown, key in sorted(options) if typed in shown.lower() or typed in key.lower()]


# Get Challenge

challenge_group = bot.create_group("challenge")


def challenge_station_autocomplete(ctx: AutocompleteContext) -> Iterable[OptionChoice]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None:
        return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None:
        return []
    line = gctx.get_snake(team).travel_line
    if line is None:
        return []
    return choices(((gctx.map.get_station(s).display_name, s) for s in gctx.map.get_line(line).stations), ctx.value)


@challenge_group.game_command()
@option("station", str, autocomplete=challenge_station_autocomplete)
async def request(dctx: ApplicationContext, gctx: GameState, station: str):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.crashed or snake.conceded:
        await dctx.respond("Your team is out of the game!", ephemeral=True)
        return

    if snake.neck_active:
        await dctx.respond("Your team already has a challenge!", ephemeral=True)
        return

    line = snake.travel_line

    if line is None:
        await dctx.respond("Your team has no declared line! (something has gone wrong)", ephemeral=True)
        return

    if not gctx.map.get_line(line).contains_station(station):
        await dctx.respond("That station is not on your line or is not on the map!", ephemeral=True)
        return

    if station == snake.anchor:
        await dctx.respond("This is your current anchor! Go somewhere else!", ephemeral=True)
        return

    if station == snake.anchor:
        await dctx.respond("This is your current anchor! Go somewhere else!", ephemeral=True)
        return

    if snake.blocked_station is not None and station == snake.blocked_station:
        await dctx.respond("You just retreated from that station — go somewhere else!")
        return
    
    if gctx.get_snake(team).vetoed:
        await dctx.respond("Your team's veto period is active!", ephemeral=True)
        return
    
    gctx.request_challenge(team.role_id, station)

    if gctx.thread:
        await gctx.thread.send(f"{team.name} has extended their neck to {station}!")
    challenges = gctx.current_challenges(team)
    if challenges is not None:
        easy, hard = challenges
        fields = [
            EmbedField(name=f"{challenge.name} (difficulty: {challenge.difficulty})", value=challenge.description)
            for challenge in challenges
        ]

        await dctx.respond(embed=Embed(title="Your active challenges", fields=fields[easy == hard :]))


@challenge_group.game_command()
async def get(dctx: ApplicationContext, gctx: GameState):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.crashed or snake.conceded:
        await dctx.respond("Your team is out of the game!", ephemeral=True)
        return

    challenges = gctx.current_challenges(team)
    if challenges == None:
        await dctx.respond("Your team has no challenge active!", ephemeral=True)
        return

    if gctx.get_snake(team).vetoed:
        await dctx.respond("Your team's veto period is active!", ephemeral=True)
        return

    easy, hard = challenges
    fields = [
        EmbedField(name=f"{challenge.name} (difficulty: {challenge.difficulty})", value=challenge.description)
        for challenge in challenges
    ]

    await dctx.respond(embed=Embed(title="Your active challenges", fields=fields[easy == hard :]))


def challenge_next_line_autocomplete(ctx: AutocompleteContext) -> Iterable[OptionChoice]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None:
        return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None:
        return []
    challenges = gctx.current_challenges(team)
    if challenges is None:
        return []
    front = gctx.map.get_station(gctx.get_snake(team).front)
    return choices(((gctx.map.get_line(line).display_name, line) for line in front.line_keys()), ctx.value)


@challenge_group.game_command()
@option("next_line", str, autocomplete=challenge_next_line_autocomplete)
async def complete(dctx: ApplicationContext, gctx: GameState, next_line: str, hard: bool):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.crashed or snake.conceded:
        await dctx.respond("Your team is out of the game!", ephemeral=True)
        return

    if not gctx.map.get_station(snake.front).line_keys():
        await dctx.respond("That line is not at your station or is not on the map!", ephemeral=True)
        return

    challenges = gctx.current_challenges(team)
    if challenges == None:
        await dctx.respond("Your team has no challenge active!", ephemeral=True)
        return

    if gctx.get_snake(team).vetoed:
        await dctx.respond("Your team's veto period is active!", ephemeral=True)
        return
    
    gctx.complete_challenge(team.role_id, next_line, hard=hard)

    if gctx.thread:
        await gctx.thread.send(
            f"{team.name} has extended their body to {snake.anchor}, they are getting on the {next_line}!"
        )
    await dctx.respond(f"Successfully completed {challenges[hard]}!")


@challenge_group.game_command()
async def veto(dctx: ApplicationContext, gctx: GameState):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.crashed or snake.conceded:
        await dctx.respond("Your team is out of the game!", ephemeral=True)
        return

    challenges = gctx.current_challenges(team)
    if challenges == None:
        await dctx.respond("Your team has no challenge active!", ephemeral=True)
        return

    if gctx.get_snake(team).vetoed:
        await dctx.respond("Your team's veto period is active!", ephemeral=True)
        return
    
    was_free = gctx.veto_challenges(team.role_id)
    if not was_free: gctx.schedule_event(0, 15, 0, gctx.unveto, team.role_id)

    if gctx.thread: await gctx.thread.send(f"{team.name} vetoed their challenge at {snake.front}!")
    await dctx.respond(
        "Successfully vetoed your team's challenges!" 
        + (f" {POWERUP_NAMES['efficiency']} was consumed!" if was_free else " Your veto period ends in 15 minutes!")
    )


@bot.game_command()
async def winner(dctx: ApplicationContext, gctx: GameState):
    await dctx.respond(gctx.winner())


powerup_group = bot.create_group("powerup")


def powerup_autocomplete(ctx: AutocompleteContext) -> Iterable[OptionChoice]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None:
        return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None:
        return []
    return choices(((POWERUP_NAMES[powerup], powerup) for powerup in gctx.enabled_powerups), ctx.value)


@powerup_group.game_command()
@option("powerup", str, autocomplete=powerup_autocomplete)
async def buy(dctx: ApplicationContext, gctx: GameState, powerup: str):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.crashed or snake.conceded:
        await dctx.respond("Your team is out of the game!", ephemeral=True)
        return

    if powerup not in gctx.enabled_powerups:
        await dctx.respond("That powerup does not exist or is not enabled!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.coins < POWERUP_COSTS[powerup]:
        await dctx.respond("Your team cannot afford that powerup!", ephemeral=True)
        return
    
    gctx.buy_powerup(team.role_id, powerup)

    await dctx.respond("Successfully purchased that powerup! Use /powerup hand to see it.")


@powerup_group.game_command()
async def hand(dctx: ApplicationContext, gctx: GameState):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.crashed or snake.conceded:
        await dctx.respond("Your team is out of the game!", ephemeral=True)
        return

    hand = ", ".join(POWERUP_NAMES[powerup] for powerup in snake.hand) or "-"
    curses = ", ".join(curse.name for curse in snake.held_curses) or "-"
    await dctx.respond(f"coins: {snake.coins}\nhand: {hand}\ncurses: {curses}")


powerup_play_group = powerup_group.create_subgroup("play")


def normal(powerup: str):
    @powerup_play_group.game_command(name=POWERUP_COMMANDS[powerup])
    async def command(dctx: ApplicationContext, gctx: GameState):
        assert isinstance(dctx.user, Member)
        team = gctx.get_user_team(dctx.user)

        if team is None:
            await dctx.respond("You have not joined this game!", ephemeral=True)
            return

        if gctx.status != Status.RUNNING:
            await dctx.respond("This game is not running!", ephemeral=True)
            return

        snake = gctx.get_snake(team)
        if snake.crashed or snake.conceded:
            await dctx.respond("Your team is out of the game!", ephemeral=True)
            return

        gctx.play_normal_powerup(team.role_id, powerup)
    
        if gctx.thread: await gctx.thread.send(f"{team.name} has activated their {POWERUP_NAMES[powerup]}!")
        await dctx.respond(f"Successfully played {POWERUP_NAMES[powerup]}!")

    return command


normal("efficiency")
normal("retreat")

def jump_station_autocomplete(ctx: AutocompleteContext) -> Iterable[OptionChoice]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None: return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None: return []
    return choices(((gctx.map.get_station(s).display_name, s) for s in gctx.map.station_keys()), ctx.value)



@powerup_play_group.game_command()
@option("station", str, autocomplete=jump_station_autocomplete)
async def jump(dctx: ApplicationContext, gctx: GameState, station: str):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.crashed or snake.conceded:
        await dctx.respond("Your team is out of the game!", ephemeral=True)
        return

    if not gctx.map.has_station(station):
        await dctx.respond("Invalid station entered!", ephemeral=True)
        return

    gctx.play_jump(team.role_id, station=station)

    if gctx.thread:
        await gctx.thread.send(f"{team.name} has activated {POWERUP_NAMES['jump']} on {station}!")
    await dctx.respond(f"Successfully played {POWERUP_NAMES['jump']} on {station}!")


def detour_autocomplete(ctx: AutocompleteContext) -> Iterable[OptionChoice]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None:
        return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None:
        return []
    snake = gctx.get_snake(team)
    boarding = snake.front if snake.neck_active else snake.anchor
    lines = gctx.map.get_station(boarding).line_keys()
    return choices(((gctx.map.get_line(line).display_name, line) for line in lines), ctx.value)


@powerup_play_group.game_command()
@option("line", str, autocomplete=detour_autocomplete)
async def detour(dctx: ApplicationContext, gctx: GameState, line: str):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.crashed or snake.conceded:
        await dctx.respond("Your team is out of the game!", ephemeral=True)
        return

    boarding = snake.front if snake.neck_active else snake.anchor

    if not gctx.map.get_station(boarding).has_line(line):
        await dctx.respond("That line is not at your station or is not on the map!", ephemeral=True)
        return

    gctx.play_detour(team.role_id, line=line)

    await dctx.respond(f"Successfully played {POWERUP_NAMES['detour']} to {line}!")


def curse_autocomplete(ctx: AutocompleteContext) -> Iterable[OptionChoice]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None:
        return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None:
        return []
    snake = gctx.get_snake(team)
    return choices(((curse.name, curse.id) for curse in snake.held_curses), ctx.value)


@powerup_play_group.game_command()
@option("curse", str, autocomplete=curse_autocomplete)
async def curse(dctx: ApplicationContext, gctx: GameState, target_team: Role, curse: str):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return

    snake = gctx.get_snake(team)
    if snake.crashed or snake.conceded:
        await dctx.respond("Your team is out of the game!", ephemeral=True)
        return

    _target_team = next((_team for _team in gctx.teams if _team.role_id == target_team.id), None)
    if _target_team is None:
        await dctx.respond("Invalid team entered!", ephemeral=True)
        return

    _curse = next((_c for _c in snake.held_curses if _c.id == curse), None)
    if _curse is None:
        await dctx.respond("You do not have that curse!", ephemeral=True)
        return

    gctx.play_curse(team.role_id, target_team_id=_target_team.role_id, curse_id=curse)

    if gctx.thread:
        await gctx.thread.send(f"{team.name} has cursed {_target_team.name} with {_curse.name}!")
    await dctx.respond(f"Successfully played {POWERUP_NAMES['curse']}!")


bot.run(TOKEN)
