from itertools import groupby
import pathlib
from collections.abc import Iterable
from typing import Any, cast

import discord
from discord import (
    ApplicationContext,
    AutocompleteContext,
    ButtonStyle,
    Embed,
    EmbedField,
    File,
    Interaction,
    Member,
    OptionChoice,
    SelectOption,
    option, # pyright: ignore[reportUnknownVariableType]
)
from discord.ui import Button, Select, View, button

from challenges import Challenge
import jloxgame
from jloxgame.bot import JLOXBot
from jloxgame.state import Status, Team

from config import POWERUP_COMMANDS, POWERUP_COSTS, POWERUP_EMOJIS, POWERUP_NAMES
from game import GameError, GameState
from powerups import NORMAL_POWERUP_HANDLERS, Curse
from util import generate_new_map, choices

with open("TOKEN", "r") as f:
    TOKEN = f.read()

bot = jloxgame.JLOXBot(GameState, pathlib.Path() / "save", member_hostable=False)

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
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return
    
    try:
        gctx.request_challenge(team.role_id, station)
    except GameError as e:
        await dctx.respond(e.message, ephemeral=True)
        return

    challenges = gctx.current_challenges(team)
    if challenges is not None:
        easy, hard = challenges
        fields = [
            EmbedField(name=f"{challenge.name} (difficulty: {challenge.difficulty})", value=challenge.description)
            for challenge in challenges
        ]

        await dctx.respond(embed=Embed(title=f"Your active challenges at {gctx.get_snake(team).front}", fields=fields[easy == hard :]), view=CompleteChallengeView(easy, hard, team, gctx))

    if gctx.thread:
        embed, map_png_path = generate_new_map(gctx)

        await gctx.thread.send(f"{team.name} has extended their neck to {station} from {gctx.get_snake(team).anchor}!", embed=embed, file=File(map_png_path, filename="map.png"))

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

class CompleteChallengeView(View):
    def __init__(self, challenge_1: Challenge, challenge_2: Challenge, team: Team, gctx: GameState):
        super().__init__()
        self.team = team
        self.gctx = gctx

        class ChallengeButton(Button[KeepCurseView]):
            def __init__(self, hard: bool, *args: Any, **kwargs: Any):
                self.hard = hard
                self.challenge = challenge_2 if hard else challenge_1
                super().__init__(*args, **kwargs)

            async def callback(self, interaction: Interaction):               
                await interaction.respond(f"Pick which line to get on next:", view=NextLineView(self.hard, team, gctx))
                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)

        self.add_item(ChallengeButton(False, style=ButtonStyle.primary, label=f"Complete {challenge_1.name}", emoji="🪙"))
        if challenge_1 != challenge_2: self.add_item(ChallengeButton(True, style=ButtonStyle.primary, label=f"Complete {challenge_2.name}", emoji="💰"))

    @button(label="Veto", emoji="🎯", style=ButtonStyle.red)
    async def veto(self, button: Button[PlayPowerupView], interaction: Interaction):
        try:
            was_free = self.gctx.veto_challenges(self.team.role_id)
        except GameError as e:
            await interaction.respond(e.message, ephemeral=True)
            return

        await interaction.respond(
            "Successfully vetoed your team's challenges!" 
            + (f" {POWERUP_NAMES['efficiency']} was consumed!" if was_free else " Your veto period ends in 15 minutes!")
        )
        if self.gctx.thread: await self.gctx.thread.send(f"{self.team.name} vetoed their challenge at {self.gctx.get_snake(self.team).front}!")

        self.disable_all_items()
        if self.message: await self.message.edit(view=self)

class NextLineView(View):
    def __init__(self, hard: bool, team: Team, gctx: GameState):
        super().__init__()

        class NextLineSelect(Select[KeepCurseView]):
            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)

            async def callback(self, interaction: Interaction):
                if not self.values: return
                next_line = self.values[0]

                challenges = gctx.current_challenges(team)
                snake = gctx.get_snake(team)
                initial = snake.origin == snake.anchor
                coins_before = snake.coins

                try:
                    gctx.complete_challenge(team.role_id, next_line, hard=hard)
                except GameError as e:
                    await interaction.respond(e.message, ephemeral=True)
                    return

                assert challenges is not None
                await interaction.respond(f"Successfully completed {challenges[hard].name}!" + f" Earnt {snake.coins - coins_before} coins!" * (not initial))

                if gctx.thread:
                    embed, map_png_path = generate_new_map(gctx)

                    await gctx.thread.send(
                        ((f"{team.name} has completed the initial challenge at {snake.anchor}!" if initial else f"{team.name} has extended their body to {snake.anchor}!") +
                        f" They are getting on the {gctx.map.get_line(next_line).display_name}."),
                        embed=embed, file=File(map_png_path, filename="map.png")
                    )

                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)

        front = gctx.map.get_station(gctx.get_snake(team).front)
        self.add_item(NextLineSelect(options=[SelectOption(label=gctx.map.get_line(line).display_name, value=line) for line in front.line_keys()]))

@challenge_group.game_command()
@option("next_line", str, autocomplete=challenge_next_line_autocomplete)
async def complete(dctx: ApplicationContext, gctx: GameState, next_line: str, hard: bool):
    assert isinstance(dctx.user, Member)
    team = gctx.get_user_team(dctx.user)

    if team is None:
        await dctx.respond("You have not joined this game!", ephemeral=True)
        return
    
    challenges = gctx.current_challenges(team)
    snake = gctx.get_snake(team)
    initial = snake.origin == snake.anchor
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

class KeepCurseView(View):
    def __init__(self, curse_1: Curse, curse_2: Curse, team: Team, gctx: GameState):
        super().__init__()

        class CurseButton(Button[KeepCurseView]):
            def __init__(self, curse: Curse, *args: Any, **kwargs: Any):
                self.curse = curse
                super().__init__(*args, **kwargs)

            async def callback(self, interaction: Interaction):
                try:
                    gctx.choose_curse(team.role_id, self.curse.id)
                except GameError as e:
                    await interaction.respond(e.message, ephemeral=True)
                    return
                
                await interaction.respond(f"Added {self.curse.name} to your hand!")
                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)

        self.add_item(CurseButton(curse_1, style=ButtonStyle.primary, label=curse_1.name, emoji="1️⃣"))
        self.add_item(CurseButton(curse_2, style=ButtonStyle.primary, label=curse_2.name, emoji="2️⃣"))

class BuyPowerupView(View):
    def __init__(self, team: Team, gctx: GameState):
        super().__init__()
        
        class PowerupButton(Button[BuyPowerupView]):
            def __init__(self, powerup: str, *args: Any, **kwargs: Any):
                self.powerup = powerup
                super().__init__(*args, **kwargs)

                self.label = f"Buy {POWERUP_NAMES[powerup]}"
                self.emoji = POWERUP_EMOJIS[powerup]
                self.style = ButtonStyle.primary
            
            async def callback(self, interaction: Interaction):
                try:
                    curses = gctx.buy_powerup(team.role_id, self.powerup)
                except GameError as e:
                    await interaction.respond(e.message, ephemeral=True)
                    return
                
                if self.powerup == "curse":
                    assert curses is not None
                    curse_embed = discord.Embed()
                    curse_embed.add_field(name=curses[0].name, value=curses[0].description)
                    curse_embed.add_field(name=curses[1].name, value=curses[1].description)
                    await interaction.respond(f"Successfully purchased a Curse! Pick one of these two to keep:", embed=curse_embed, view=KeepCurseView(curses[0], curses[1], team, gctx))
                else:
                    view = PlayPowerupView(self.powerup, team, gctx)
                    await interaction.respond(f"Successfully purchased a {POWERUP_NAMES[self.powerup]}!", view=view)
                
                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)
        
        for powerup in POWERUP_NAMES.keys():
            button = PowerupButton(powerup)
            if gctx.get_snake(team).coins < POWERUP_COSTS[powerup]:
                button.disabled = True
            self.add_item(button)

class PlayPowerupView(View):
    def __init__(self, powerup: str, team: Team, gctx: GameState):
        super().__init__()
        self.team = team
        self.gctx = gctx
        self.powerup = powerup
    
    @button(label="Play it now!", emoji="🎯", style=ButtonStyle.green)
    async def play(self, button: Button[PlayPowerupView], interaction: Interaction):
        if self.powerup in NORMAL_POWERUP_HANDLERS.keys():
            try:
                self.gctx.play_normal_powerup(self.team.role_id, self.powerup)
            except GameError as e:
                await interaction.respond(e.message, ephemeral=True)
                return
            
            await interaction.respond(f"Successfully played {POWERUP_NAMES[self.powerup]}!")
            if self.gctx.thread: await self.gctx.thread.send(f"{self.team.name} has activated their {POWERUP_NAMES[self.powerup]}!")
        elif self.powerup == "detour":
            await interaction.respond("Choose which line to detour to:", view=PlayDetourView(self.team, self.gctx))
        elif self.powerup == "curse":
            await interaction.respond("Choose which curse to play, and on which team:", view=PlayCurseView(self.team, self.gctx))

        self.disable_all_items()
        if self.message: await self.message.edit(view=self)

@powerup_group.game_command()
@option("powerup", str, autocomplete=powerup_autocomplete)
async def buy(dctx: ApplicationContext, gctx: GameState):
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

class HandPlayPowerupView(View):
    def __init__(self, team: Team, gctx: GameState):
        super().__init__()
        
        class PowerupButton(Button[BuyPowerupView]):
            def __init__(self, powerup: str, *args: Any, **kwargs: Any):
                self.powerup = powerup
                super().__init__(*args, **kwargs)

                self.label = f"Play {POWERUP_NAMES[powerup]}!"
                self.emoji = POWERUP_EMOJIS[powerup]
                self.style = ButtonStyle.primary
            
            async def callback(self, interaction: Interaction):
                if self.powerup in NORMAL_POWERUP_HANDLERS.keys():
                    try:
                        gctx.play_normal_powerup(team.role_id, self.powerup)
                    except GameError as e:
                        await interaction.respond(e.message, ephemeral=True)
                        return

                    await interaction.respond(f"Successfully played {POWERUP_NAMES[self.powerup]}!")
                    if gctx.thread: await gctx.thread.send(f"{team.name} has activated their {POWERUP_NAMES[self.powerup]}!")

                elif self.powerup == "detour":
                    await interaction.respond("Choose which line to detour to:", view=PlayDetourView(team, gctx))
                elif self.powerup == "curse":
                    await interaction.respond("Choose which curse to play, and on which team:", view=PlayCurseView(team, gctx))
                
                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)
        
        for powerup in set(gctx.get_snake(team).hand):
            self.add_item(PowerupButton(powerup))

class PlayDetourView(View):
    def __init__(self, team: Team, gctx: GameState):
        super().__init__()

        class DetourSelect(Select):
            async def callback(self, interaction: Interaction):
                if not self.values: return

                line = self.values[0]

                try:
                    gctx.play_detour(team.role_id, line=line)
                except GameError as e:
                    await interaction.respond(e.message, ephemeral=True)
                    return

                await interaction.respond(f"Successfully played {POWERUP_NAMES['detour']} to {line}!")

                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)
        
        snake = gctx.get_snake(team)
        boarding = snake.front if snake.neck_active else snake.anchor
        lines = gctx.map.get_station(boarding).line_keys()
        self.add_item(DetourSelect(options = [SelectOption(label=gctx.map.get_line(line).display_name, value=line) for line in lines]))

class PlayCurseView(View):
    def __init__(self, team: Team, gctx: GameState):
        super().__init__()

        self.curse_chosen: Curse | None = None
        self.team_chosen: Team | None = None
        self.team = team
        self.gctx = gctx

        class TeamSelect(Select):
            async def callback(self, interaction: Interaction):
                if not self.values: return

                assert isinstance(self.parent, PlayCurseView) # pyright: ignore[reportUnknownMemberType]
                self.parent.team_chosen = gctx.get_team(int(self.values[0]))

                await interaction.respond(f"Picked {self.parent.team_chosen.name}!")

                if self.parent.curse_chosen is not None:
                    await self.parent.finish(interaction)

                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    self.disabled = True
                    if message: await message.edit(view=parent)
        
        class CurseSelect(Select):
            async def callback(self, interaction: Interaction):
                if not self.values: return

                assert isinstance(self.parent, PlayCurseView) # pyright: ignore[reportUnknownMemberType]
                self.parent.curse_chosen = next((c for c in snake.held_curses if c.id == self.values[0]))

                await interaction.respond(f"Picked {self.parent.curse_chosen.name}!")

                if self.parent.team_chosen is not None:
                    await self.parent.finish(interaction)

                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    self.disabled = True
                    if message: await message.edit(view=parent)
        
        snake = gctx.get_snake(team)
        self.add_item(TeamSelect(options = [SelectOption(label=team.name, value=str(team.role_id)) for team in gctx.teams if team.role_id != self.team.role_id]))
        self.add_item(CurseSelect(options = [SelectOption(label=curse.name, value=curse.id) for curse in snake.held_curses]))
    
    async def finish(self, interaction: Interaction):
        assert self.team_chosen is not None
        assert self.curse_chosen is not None

        try:
            played_curse = self.gctx.play_curse(self.team.role_id, target_team_id=self.team_chosen.role_id, curse_id=self.curse_chosen.id)
        except GameError as e:
            await interaction.respond(e.message, ephemeral=True)
            return

        await interaction.respond(f"Successfully played {played_curse.name} on {self.team_chosen.name}!")
        if self.gctx.thread:
            curse_embed = Embed()
            curse_embed.title = played_curse.name
            curse_embed.description = played_curse.description
            await self.gctx.thread.send(f"{self.team.name} has cursed {self.team_chosen.name} with {played_curse.name}!", embed=curse_embed)

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

    hand_str = [f"{POWERUP_NAMES[powerup]} ×{len(list(g))}" for powerup, g in groupby(sorted(snake.hand))] or ["None"]
    
    hand_embed = Embed()
    hand_embed.description = f"**Coins**: {snake.coins}\n**Powerups**: {"; ".join(hand_str)}"

    for curse in snake.held_curses:
        hand_embed.add_field(name=curse.name, value=curse.description)
    
    await dctx.respond("Here is your hand!", embed=hand_embed, view=HandPlayPowerupView(team, gctx))

@bot.game_command()
async def curses(dctx: ApplicationContext, gctx: GameState):
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

    hand_embed = Embed()

    for curse in snake.curses:
        hand_embed.add_field(name=curse.name, value=curse.description)
    
    await dctx.respond("Here are all the curses that have been played on you!", embed=hand_embed, view=HandPlayPowerupView(team, gctx))

powerup_play_group = powerup_group.create_subgroup("play")

def normal(powerup: str):
    @powerup_play_group.game_command(name=POWERUP_COMMANDS[powerup])
    async def command(dctx: ApplicationContext, gctx: GameState):
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


@powerup_play_group.game_command()
@option("curse", autocomplete=curse_autocomplete)
@option("target_team", autocomplete=bot.team_autocomplete)
async def curse(dctx: ApplicationContext, gctx: GameState, target_team: str, curse: str):
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

bot.run(TOKEN)
