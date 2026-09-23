from itertools import groupby
import pathlib
from collections.abc import Iterable
from typing import cast

from discord import (
    ApplicationContext,
    AutocompleteContext,
    Embed,
    EmbedField,
    File,
    Member,
    OptionChoice,
    option, # pyright: ignore[reportUnknownVariableType]
)

from challenges import get_difficulty
import jloxgame
from jloxgame.bot import JLOXBot
from jloxgame.state import Status

from config import POWERUP_COMMANDS, POWERUP_COSTS, POWERUP_NAMES
from game import GameError, GameState
from util import generate_new_map, choices
from views import *

with open("TOKEN", "r") as f:
    TOKEN = f.read()

bot = jloxgame.JLOXBot(GameState, pathlib.Path() / "save", member_hostable=False, joinable=False)

@bot.event
async def on_ready():
    bot.logger.info(f"{bot.user} is online!")

@bot.game_command()
async def map(dctx: ApplicationContext, gctx: GameState):
    embed, map_png_path = generate_new_map(gctx)
    await dctx.respond(embed=embed, file=File(map_png_path, filename="map.png"))


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
    """Extend your neck by requesting a challenge at a new station."""

    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return
    snake = gctx.get_snake(team)
    if snake.travel_line is None:
        await dctx.respond("You do not have a current line!", ephemeral=True)
        return
    if not gctx.map.has_station(station):
        await dctx.respond(f"Unknown station: {station!r}", ephemeral=True)
        return
    if not gctx.map.get_station(station).has_line(snake.travel_line):
        await dctx.respond(f"{station!r} is not on line {snake.travel_line!r}")
        return
    if station == snake.anchor:
        await dctx.respond(f"{station!r} is the current Anchor — travel to a different interchange")
        return
    if snake.neck_active:
        await dctx.respond(f"{team!r} already has an active challenge request")
        return
    if snake.blocked_station is not None and station == snake.blocked_station:
        await dctx.respond(f"{station!r} was just retreated from — request a different interchange")
        return
    if snake.vetoed:
        await dctx.respond(f"{team!r} is in their veto period")
        return
    
    neck = gctx.map.path_between_on_line(snake.travel_line, snake.anchor, station)[1:]
    fatal = bool([s for s in neck if gctx.map.is_claimed(s) and s not in gctx.jumped_stations])

    await dctx.respond(
        f"You are extending your neck to **{gctx.map.get_station(station).display_name}**."
        + (f"\nYour neck will pass through *{", ".join([gctx.map.get_station(st).display_name for st in neck if st != station])}*." if len(neck) > 1 else "")
        + ("\n**This WILL crash your snake!**" if fatal else "")
        + f"\nThe expected difficulty is {get_difficulty([gctx.map.get_station(st).weight for st in neck]):.02}. Are you sure you want to do this?",
        view=ConfirmRequestView(station, fatal, team, gctx)
    )

@challenge_group.game_command()
async def get(dctx: ApplicationContext, gctx: GameState):
    """Get your currently active challenges!"""

    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return
    
    if gctx.status != Status.RUNNING:
        await dctx.respond("This game is not running!", ephemeral=True)
        return
    
    challenges = gctx.current_challenges(team)
    if challenges == None:
        travel_line = gctx.get_snake(team).travel_line
        await dctx.respond(
            f"Your team has no challenge active!" + 
            (f" You are on the {gctx.map.get_line(travel_line).display_name}." if travel_line else ""
        ), ephemeral=True)
        return

    if gctx.get_snake(team).vetoed:
        await dctx.respond("Your team's veto period is active!", ephemeral=True)
        return

    easy, hard = challenges
    fields = [
        EmbedField(name=f"{challenge.name} (difficulty: {challenge.difficulty})", value=challenge.description)
        for challenge in challenges
    ]

    await dctx.respond(embed=Embed(title=f"Your active challenges at {gctx.get_snake(team).front}", fields=fields[easy == hard :]), view=CompleteChallengeView(easy, hard, team, gctx))


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
    """Complete one of your current challenges and declare your next line!"""

    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return
    
    challenges = gctx.current_challenges(team)
    snake = gctx.get_snake(team)
    initial = snake.travel_line is None
    coins_before = snake.coins

    try:
        gctx.complete_challenge(team.role_id, next_line, hard=hard)
    except GameError as e:
        await dctx.respond(e.message, ephemeral=True)
        return

    assert challenges is not None
    await dctx.respond(f"Successfully completed {challenges[hard].name}!" + f" Earnt {snake.coins - coins_before} coins!" * (not initial))

    if gctx.thread:
        embed, map_png_path = generate_new_map(gctx)

        await gctx.thread.send(
            ((f"{team.name} has completed the initial challenge at {snake.anchor}!" if initial else f"{team.name} has extended their body to {snake.anchor}!") +
            f" They are getting on the {gctx.map.get_line(next_line).display_name}."),
            embed=embed, file=File(map_png_path, filename="map.png")
        )

@challenge_group.game_command()
async def veto(dctx: ApplicationContext, gctx: GameState):
    """Veto your current challenges!"""

    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return
    
    try:
        was_free = gctx.veto_challenges(team.role_id)
    except GameError as e:
        await dctx.respond(e.message, ephemeral=True)
        return

    await dctx.respond(
        "Successfully vetoed your team's challenges!" 
        + (f" {POWERUP_NAMES['efficiency']} was consumed!" if was_free else " Your veto period ends in 15 minutes!")
    )
    if gctx.thread: await gctx.thread.send(f"{team.name} vetoed their challenge at {gctx.get_snake(team).front}!")

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
async def buy(dctx: ApplicationContext, gctx: GameState):
    """Buy a powerup!"""

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
    
    embed = Embed()
    for powerup, name in POWERUP_NAMES.items():
        embed.description = f"**Your coins:** {snake.coins}"
        embed.add_field(name=name, value=f"{POWERUP_COSTS[powerup]} coins", inline=False)
    
    await dctx.respond(embed=embed, view=BuyPowerupView(team, gctx))

def choose_curse_autocomplete(ctx: AutocompleteContext) -> Iterable[OptionChoice]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None:
        return []
    team = gctx.get_user_team(ctx.interaction.user)
    if team is None:
        return []
    return choices(((curse.name, curse.id) for curse in gctx.get_snake(team).curse_choice), ctx.value)

@powerup_group.game_command()
@option("curse", str, autocomplete=choose_curse_autocomplete)
async def choose_curse(dctx: ApplicationContext, gctx: GameState, curse: str):
    """Choose one of the two offered curses!"""

    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user) 

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    try:
        chosen_curse = gctx.choose_curse(team.role_id, curse)
    except GameError as e:
        await dctx.respond(e.message, ephemeral=True)
        return
    
    await dctx.respond(f"Added {chosen_curse.name} to your hand!")

@powerup_group.game_command()
async def hand(dctx: ApplicationContext, gctx: GameState):
    """See your current held coins and powerups, including curses."""

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

    hand_str = [f"{POWERUP_NAMES[powerup]} ×{len(list(g))}" for powerup, g in groupby(sorted(snake.hand))] or ["None"]
    
    hand_embed = Embed()
    hand_embed.description = f"**Coins**: {snake.coins}\n**Powerups**: {"; ".join(hand_str)}"

    for curse in snake.held_curses:
        hand_embed.add_field(name=curse.name, value=curse.description)
    
    await dctx.respond("Here is your hand!", embed=hand_embed, view=HandPlayPowerupView(team, gctx))

@bot.game_command()
async def curses(dctx: ApplicationContext, gctx: GameState):
    """Get all the curses that have been played on you this game."""

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

    if not snake.curses:
        await dctx.respond("No curses have been played on your team!")
        return

    hand_embed = Embed()

    for curse in snake.curses:
        hand_embed.add_field(name=curse.name, value=curse.description)
    
    await dctx.respond("Here are all the curses that have been played on you!", embed=hand_embed, view=HandPlayPowerupView(team, gctx))

powerup_play_group = powerup_group.create_subgroup("play")

def normal(powerup: str):
    @powerup_play_group.game_command(name=POWERUP_COMMANDS[powerup])
    async def command(dctx: ApplicationContext, gctx: GameState):
        """Play a powerup!"""

        assert isinstance(dctx.user, Member)
        team = gctx.get_user_team(dctx.user)

        if team is None:
            await dctx.respond("You have not joined this game!", ephemeral=True)
            return

        try:
            gctx.play_normal_powerup(team.role_id, powerup)
        except GameError as e:
            await dctx.respond(e.message, ephemeral=True)
            return

        await dctx.respond(f"Successfully played {POWERUP_NAMES[powerup]}!")
        if gctx.thread: await gctx.thread.send(f"{team.name} has activated their {POWERUP_NAMES[powerup]}!")

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
    """Play a jump on a station!"""

    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    try:
        gctx.play_jump(team.role_id, station=station)
    except GameError as e:
        await dctx.respond(e.message, ephemeral=True)
        return

    await dctx.respond(f"Successfully played {POWERUP_NAMES['jump']} on {station}!")
    if gctx.thread:
        embed, map_png_path = generate_new_map(gctx)

        await gctx.thread.send(f"{team.name} has played their {POWERUP_NAMES['jump']} on {station}!", embed=embed, file=File(map_png_path, filename="map.png"))


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
    """Play a detour to a line!"""

    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    try:
        gctx.play_detour(team.role_id, line=line)
    except GameError as e:
        await dctx.respond(e.message, ephemeral=True)
        return

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

async def curse_team_autocomplete(ctx: AutocompleteContext) -> list[OptionChoice]:
    assert isinstance(ctx.interaction.user, Member)
    bot = cast(JLOXBot[GameState], ctx.bot)
    gctx = bot.get_game_ctx(ctx)
    if gctx is None:
        return []
    user_team = gctx.get_user_team(ctx.interaction.user)
    return [OptionChoice(team.name, str(team.role_id)) for team in gctx.teams if team != user_team]

@powerup_play_group.game_command()
@option("curse", autocomplete=curse_autocomplete)
@option("target_team", autocomplete=bot.team_autocomplete)
async def curse(dctx: ApplicationContext, gctx: GameState, target_team: str, curse: str):
    """Play a curse on a team!"""

    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    _target_team = next((_team for _team in gctx.teams if _team.role_id == int(target_team)), None)
    if _target_team is None:
        await dctx.respond("Invalid team entered!", ephemeral=True)
        return

    try:
        played_curse = gctx.play_curse(team.role_id, target_team_id=_target_team.role_id, curse_id=curse)
    except GameError as e:
        await dctx.respond(e.message, ephemeral=True)
        return

    await dctx.respond(f"Successfully played {played_curse.name} on {_target_team.name}!")
    if gctx.thread:
        curse_embed = Embed()
        curse_embed.title = played_curse.name
        curse_embed.description = played_curse.description
        await gctx.thread.send(f"{team.name} has cursed {_target_team.name} with {played_curse.name}!", embed=curse_embed)

@bot.game_command()
async def declare_win(dctx: ApplicationContext, gctx: GameState):
    """Declare your intention to win the game on score!"""

    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return

    try:
        gctx.declare_win(team.role_id)
    except GameError as e:
        await dctx.respond(e.message, ephemeral=True)
        return
    
    await dctx.respond(f"Successfully declared your intention to win!")

    if gctx.thread: await gctx.thread.send(f"# {team.name} has declared their intention to win in 20 minutes!")

bot.run(TOKEN)
