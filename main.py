from datetime import timedelta
import time
from typing import Iterable, cast
import discord

import jloxgame
from jloxgame.bot import JLOXBot
from jloxgame.state import Status

from game import GameState
from map import Map
from render import render_map, svg_to_png
from events import BuyPowerup, Complete, PlayPowerup, Request, Unveto, Veto
from config import POWERUP_COSTS

from discord import ApplicationContext, AutocompleteContext, Embed, EmbedField, File, Member, Role, option # pyright: ignore[reportUnknownVariableType]

import pathlib
import os

with open("TOKEN", "r") as f:
    TOKEN = f.read()

bot = jloxgame.JLOXBot(GameState, pathlib.Path() / "save", member_hostable=False)

@bot.event
async def on_ready():
    # await bot.sync_commands(force=True)
    print(f"[snake london | info] {bot.user} is online!")

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
    embed.set_footer(text=f"Time elapsed: {timedelta(seconds=gctx.game_time_now() // 1000000)}")
    await dctx.respond(embed=embed, file=File(map_png_path, filename="map.png"))

# Get Challenge

challenge_group = bot.create_group("challenge")

def challenge_station_autocomplete(ctx: AutocompleteContext) -> Iterable[str]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None: return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None: return []
    line = gctx.get_snake(team).travel_line
    if line is None: return []
    return filter(lambda station: station.lower().startswith(ctx.value.lower()), gctx.map.get_line(line).stations)

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
        await dctx.respond(f"You just retreated from that station — go somewhere else!")
        return
    
    gctx.add_event(Request(team.role_id, station))

    if gctx.thread: await gctx.thread.send(f"{team.name} has extended their neck to {station}!")
    challenges = gctx.current_challenges(team)
    if challenges is not None:
        easy, hard = challenges
        fields = [EmbedField(name=f"{challenge.name} (difficulty: {challenge.difficulty})", value=challenge.description) for challenge in challenges]

        await dctx.respond(embed=Embed(title="Your active challenges", fields=fields[easy == hard:]))
    
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
    fields = [EmbedField(name=f"{challenge.name} (difficulty: {challenge.difficulty})", value=challenge.description) for challenge in challenges]

    await dctx.respond(embed=Embed(title="Your active challenges", fields=fields[easy == hard:]))

def challenge_next_line_autocomplete(ctx: AutocompleteContext) -> Iterable[str]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None: return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None: return []
    challenges = gctx.current_challenges(team)
    if challenges is None: return []
    return filter(lambda line: line.lower().startswith(ctx.value.lower()), gctx.map.get_station(gctx.get_snake(team).front).line_keys())

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
    
    gctx.add_event(Complete(team.role_id, next_line, hard))

    if gctx.thread: await gctx.thread.send(f"{team.name} has extended their body to {snake.anchor}, they are getting on the {next_line}!")
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
    
    gctx.add_event(Veto(team.role_id))
    # if snake.vetoed: gctx.schedule_event(Unveto(team.role_id), 0, 1, 0)

    if gctx.thread: await gctx.thread.send(f"{team.name} vetoed their challenge at {snake.front}!")
    await dctx.respond("Successfully vetoed your team's challenges!" + (" Your veto period ends in 15 minutes!" if snake.vetoed else " Efficiency was consumed!"))

    gctx.add_event(Unveto(team.role_id))

@bot.game_command()
async def winner(dctx: ApplicationContext, gctx: GameState):
    await dctx.respond(gctx.winner())

powerup_group = bot.create_group("powerup")

def powerup_autocomplete(ctx: AutocompleteContext) -> Iterable[str]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None: return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None: return []
    return filter(lambda powerup: powerup.lower().startswith(ctx.value.lower()), gctx.enabled_powerups)

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
    
    gctx.add_event(BuyPowerup(team.role_id, powerup))

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

    await dctx.respond(f"coins: {snake.coins}\nhand: {snake.hand}\ncurses: {snake.held_curses}")

powerup_play_group = powerup_group.create_subgroup("play")

def normal(powerup: str):
    @powerup_play_group.game_command(name=powerup)
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

        gctx.add_event(PlayPowerup(team.role_id, powerup))
    
        if gctx.thread: await gctx.thread.send(f"{team.name} has activated their {powerup}!")
        await dctx.respond(f"Successfully played {powerup}!")

    return command

normal("efficiency")
normal("double_up")
normal("retreat")

stations = Map().station_keys()

@powerup_play_group.game_command()
@option("station", str, autocomplete=lambda ctx: filter(lambda station: station.lower().startswith(ctx.value.lower()), stations))
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

    gctx.add_event(PlayPowerup(team.role_id, "jump", target_station=station))

    if gctx.thread: await gctx.thread.send(f"{team.name} has activated jump on {station}!")
    await dctx.respond(f"Successfully played jump on {station}!")

def detour_autocomplete(ctx: AutocompleteContext) -> Iterable[str]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None: return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None: return []
    snake = gctx.get_snake(team)
    boarding = snake.front if snake.neck_active else snake.anchor
    return filter(lambda line: line.lower().startswith(ctx.value.lower()), gctx.map.get_station(boarding).line_keys())

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

    gctx.add_event(PlayPowerup(team.role_id, "detour", target_line=line))

    await dctx.respond(f"Successfully played detour to {line}!")

def curse_autocomplete(ctx: AutocompleteContext) -> Iterable[str]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None: return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None: return []
    snake = gctx.get_snake(team)
    return filter(lambda curse: curse.lower().startswith(ctx.value.lower()), [curse.id for curse in snake.held_curses])

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
    
    if curse not in [curse.id for curse in snake.held_curses]:
        await dctx.respond("You do not have that curse!", ephemeral=True)
        return

    gctx.add_event(PlayPowerup(team.role_id, "curse", target_team_id=_target_team.role_id, curse=curse))

    if gctx.thread: await gctx.thread.send(f"{team.name} has cursed {_target_team.name} with {curse}!")
    await dctx.respond(f"Successfully played curse!")

bot.run(TOKEN)