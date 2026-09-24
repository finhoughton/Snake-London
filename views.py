import asyncio
from typing import TYPE_CHECKING, Any, cast

from discord import ButtonStyle, Embed, EmbedField, File, Interaction, SelectOption
from discord.ui import Button, Select, View, button

from challenges import Challenge
from jloxgame.state import Team

from config import POWERUP_COSTS, POWERUP_EMOJIS, POWERUP_NAMES
from powerups import NORMAL_POWERUP_HANDLERS, Curse
from util import GameError, generate_new_map
if TYPE_CHECKING:
    from game import GameState


class BuyPowerupView(View):
    instances: dict[Team, BuyPowerupView] = {}

    def __init__(self, team: Team, gctx: GameState):
        super().__init__(timeout=None)

        if team in BuyPowerupView.instances:
            view = BuyPowerupView.instances[team]
            view.disable_all_items()
            if view.message: asyncio.create_task(view.message.edit(view=view))
        BuyPowerupView.instances[team] = self
        
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
                    curse_embed = Embed()
                    curse_embed.add_field(name=curses[0].name, value=curses[0].description)
                    curse_embed.add_field(name=curses[1].name, value=curses[1].description)
                    await interaction.respond(f"Successfully purchased a Curse! Pick one of these two to keep:", embed=curse_embed, view=KeepCurseView(curses[0], curses[1], team, gctx))
                else:
                    view = None if self.powerup == "jump" else PlayPowerupView(self.powerup, team, gctx)
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

class HandPlayPowerupView(View):
    instances: dict[Team, HandPlayPowerupView] = {}

    def __init__(self, team: Team, gctx: GameState):
        super().__init__(timeout=None)

        if team in HandPlayPowerupView.instances:
            view = HandPlayPowerupView.instances[team]
            view.disable_all_items()
            if view.message: asyncio.create_task(view.message.edit(view=view))
        HandPlayPowerupView.instances[team] = self

        self.snake = gctx.get_snake(team)
        self.team = team
        self.gctx = gctx
        
        class PowerupButton(Button[HandPlayPowerupView]):
            def __init__(self, powerup: str, *args: Any, **kwargs: Any):
                self.powerup = powerup
                super().__init__(*args, **kwargs)

                self.label = f"Play {POWERUP_NAMES[powerup]}!"
                self.emoji = POWERUP_EMOJIS[powerup]
                self.style = ButtonStyle.green
            
            async def callback(self, interaction: Interaction):
                if self.powerup in NORMAL_POWERUP_HANDLERS.keys():
                    try:
                        gctx.play_normal_powerup(team.role_id, powerup)
                    except GameError as e:
                        await interaction.respond(e.message, ephemeral=True)
                        return

                    await interaction.respond(f"Successfully played {POWERUP_NAMES[powerup]}!")
                    if gctx.thread: await gctx.thread.send(f"{team.name} has activated their {POWERUP_NAMES[powerup]}!")

                elif self.powerup == "detour":
                    await interaction.respond("Choose which line to detour to:", view=PlayDetourView(team, gctx))
                elif self.powerup == "curse":
                    if len(gctx.get_snake(team).held_curses) != len([p for p in gctx.get_snake(team).hand if p == "curse"]):
                        await interaction.respond(f"Choose which curse to keep first!")
                    await interaction.respond(f"Choose which curse to play{', and on which team' if len(gctx.teams) != 2 else ''}:", view=PlayCurseView(team, gctx))
                
                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)
        
        for powerup in set(gctx.get_snake(team).hand):
            if powerup != "jump":
                self.add_item(PowerupButton(powerup))
        
    @button(label="Buy powerups!", emoji="🛒", style=ButtonStyle.blurple)
    async def buy_powerups(self, button: Button[HandPlayPowerupView], interaction: Interaction):
        embed = Embed()
        for powerup, name in POWERUP_NAMES.items():
            embed.description = f"**Your coins:** {self.snake.coins}"
            embed.add_field(name=name, value=f"{POWERUP_COSTS[powerup]} coins", inline=False)
        
        await interaction.respond(embed=embed, view=BuyPowerupView(self.team, self.gctx))

        self.disable_all_items()
        if self.message: await self.message.edit(view=self)

class PlayPowerupView(View):
    instances: dict[Team, PlayPowerupView] = {}

    def __init__(self, powerup: str, team: Team, gctx: GameState):
        super().__init__(timeout=None)
        self.team = team
        self.gctx = gctx
        self.powerup = powerup

        if team in PlayPowerupView.instances:
            view = PlayPowerupView.instances[team]
            view.disable_all_items()
            if view.message: asyncio.create_task(view.message.edit(view=view))
        PlayPowerupView.instances[team] = self
    
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
            await interaction.respond(f"Choose which curse to play {', and on which team' if len(self.gctx.teams) != 2 else ''}:", view=PlayCurseView(self.team, self.gctx))

        self.disable_all_items()
        if self.message: await self.message.edit(view=self)

class PlayDetourView(View):
    instances: dict[Team, PlayDetourView] = {}

    def __init__(self, team: Team, gctx: GameState):
        super().__init__(timeout=None)

        if team in PlayDetourView.instances:
            view = PlayDetourView.instances[team]
            view.disable_all_items()
            if view.message: asyncio.create_task(view.message.edit(view=view))
        PlayDetourView.instances[team] = self

        class DetourSelect(Select):
            async def callback(self, interaction: Interaction):
                if not self.values: return

                line = self.values[0]

                try:
                    gctx.play_detour(team.role_id, line=line)
                except GameError as e:
                    await interaction.respond(e.message, ephemeral=True)
                    return

                await interaction.respond(f"Successfully played {POWERUP_NAMES['detour']} to {gctx.map.get_line(line).display_name}!")

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
    instances: dict[Team, PlayCurseView] = {}

    def __init__(self, team: Team, gctx: GameState):
        super().__init__(timeout=None)

        self.curse_chosen: Curse | None = None
        self.team_chosen: Team | None = None
        self.team = team
        self.gctx = gctx

        if team in PlayCurseView.instances:
            view = PlayCurseView.instances[team]
            view.disable_all_items()
            if view.message: asyncio.create_task(view.message.edit(view=view))
        PlayCurseView.instances[team] = self

        class TeamSelect(Select):
            async def callback(self, interaction: Interaction):
                if not self.values: return

                assert isinstance(self.parent, PlayCurseView) # pyright: ignore[reportUnknownMemberType]
                self.parent.team_chosen = gctx.get_team(int(self.values[0]))

                await interaction.response.defer(invisible=True)

                if self.parent.curse_chosen is not None:
                    await self.parent.finish(interaction)
        
        class CurseSelect(Select):
            async def callback(self, interaction: Interaction):
                if not self.values: return

                assert isinstance(self.parent, PlayCurseView) # pyright: ignore[reportUnknownMemberType]
                self.parent.curse_chosen = next((c for c in snake.held_curses if c.id == self.values[0]))

                await interaction.response.defer(invisible=True)

                if self.parent.team_chosen is not None:
                    await self.parent.finish(interaction)
        
        snake = gctx.get_snake(team)
        if len(gctx.teams) == 2:
            self.team_chosen = next(t for t in gctx.teams if t.role_id != team.role_id)
        else:
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
        if self.gctx.thread and self.team_chosen.thread and self.team_chosen.role:
            curse_embed = Embed()
            curse_embed.title = played_curse.name
            curse_embed.description = played_curse.description
            await self.gctx.thread.send(f"{self.team.name} has cursed {self.team_chosen.role.mention} with {played_curse.name}!", embed=curse_embed)
            await self.team_chosen.thread.send(f"{self.team.name} has cursed you with {played_curse.name}!", embed=curse_embed)
        
        self.disable_all_items()
        if self.message: await self.message.edit(view=self)


class CompleteChallengeView(View):
    instances: dict[Team, CompleteChallengeView] = {}

    def __init__(self, challenge_1: Challenge, challenge_2: Challenge, team: Team, gctx: GameState):
        super().__init__(timeout=None)
        self.team = team
        self.gctx = gctx

        if team in CompleteChallengeView.instances:
            view = CompleteChallengeView.instances[team]
            view.disable_all_items()
            if view.message: asyncio.create_task(view.message.edit(view=view))
        CompleteChallengeView.instances[team] = self

        class ChallengeButton(Button[CompleteChallengeView]):
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
    async def veto(self, button: Button[CompleteChallengeView], interaction: Interaction):
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
    instances: dict[Team, NextLineView] = {}

    def __init__(self, hard: bool, team: Team, gctx: GameState):
        super().__init__(timeout=None)

        if team in NextLineView.instances:
            view = NextLineView.instances[team]
            view.disable_all_items()
            if view.message: asyncio.create_task(view.message.edit(view=view))
        NextLineView.instances[team] = self

        class NextLineSelect(Select[NextLineView]):
            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)

            async def callback(self, interaction: Interaction):
                if not self.values: return
                next_line = self.values[0]

                challenges = gctx.current_challenges(team)
                snake = gctx.get_snake(team)
                initial = snake.travel_line is None
                coins_before = snake.coins

                try:
                    _, crashed_teams = gctx.complete_challenge(team.role_id, next_line, hard=hard)
                except GameError as e:
                    await interaction.respond(e.message, ephemeral=True)
                    return

                assert challenges is not None
                earnt = snake.coins - coins_before
                await interaction.respond(
                    f"Successfully completed {challenges[hard].name}!" 
                    + f" Earnt {earnt} coin{'s' * (earnt != 1)}!" * (not initial)
                    + f"\nGetting on the {gctx.map.get_line(next_line).display_name}."
                )


                if gctx.thread:
                    embed, map_png_path = generate_new_map(gctx)

                    await gctx.thread.send(
                        ((f"{team.name} has completed the initial challenge at {snake.anchor}!" if initial else f"{team.name} has extended their body to {snake.anchor}!") +
                        f" They are getting on the {gctx.map.get_line(next_line).display_name}."),
                        embed=embed, file=File(map_png_path, filename="map.png")
                    )

                for crashed_team in crashed_teams:
                    if crashed_team.thread:
                        await crashed_team.thread.send("# Your snake has crashed! You are out of the game.")
                    if gctx.thread:
                        await gctx.thread.send(f"# {crashed_team.name}'s snake has crashed! They are out of the game.")
                
                winner = gctx.winner()
                if winner is not None:
                    gctx.won_game()
                    if winner.thread:
                        await winner.thread.send("# You have won the game!")
                    if gctx.thread:
                        await gctx.thread.send(f"# {winner.name} has won the game!")

                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)

        front = gctx.map.get_station(gctx.get_snake(team).front)
        self.add_item(NextLineSelect(options=[SelectOption(label=gctx.map.get_line(line).display_name, value=line) for line in front.line_keys()]))


class KeepCurseView(View):
    instances: dict[Team, KeepCurseView] = {}

    def __init__(self, curse_1: Curse, curse_2: Curse, team: Team, gctx: GameState):
        super().__init__(timeout=None)

        if team in KeepCurseView.instances:
            view = KeepCurseView.instances[team]
            view.disable_all_items()
            if view.message: asyncio.create_task(view.message.edit(view=view))
        KeepCurseView.instances[team] = self

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
                
                await interaction.respond(f"Added {self.curse.name} to your hand!", view=PlayPowerupView("curse", team, gctx))
                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)

        self.add_item(CurseButton(curse_1, style=ButtonStyle.primary, label=curse_1.name, emoji="1️⃣"))
        self.add_item(CurseButton(curse_2, style=ButtonStyle.primary, label=curse_2.name, emoji="2️⃣"))


class ConfirmRequestView(View):
    instances: dict[Team, ConfirmRequestView] = {}

    def __init__(self, station: str, fatal: bool, team: Team, gctx: GameState):
        super().__init__(timeout=None)

        if team in ConfirmRequestView.instances:
            view = ConfirmRequestView.instances[team]
            view.disable_all_items()
            if view.message: asyncio.create_task(view.message.edit(view=view))
        ConfirmRequestView.instances[team] = self

        class ConfirmButton(Button[KeepCurseView]):
            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)

            async def callback(self, interaction: Interaction):
                try:
                    gctx.request_challenge(team.role_id, station)
                except GameError as e:
                    await interaction.respond(e.message, ephemeral=True)
                    return

                challenges = gctx.current_challenges(team)
                if challenges is not None:
                    easy, hard = challenges
                    fields = [
                        EmbedField(name=f"{challenge.name} (difficulty: {challenge.difficulty})", value=challenge.description)
                        for challenge in challenges
                    ]

                    await interaction.respond(embed=Embed(title=f"Your active challenges at {gctx.get_snake(team).front}", fields=fields[easy == hard :]), view=CompleteChallengeView(easy, hard, team, gctx))

                if gctx.thread:
                    embed, map_png_path = generate_new_map(gctx)

                    await gctx.thread.send(f"{team.name} has extended their neck to {station} from {gctx.get_snake(team).anchor}!", embed=embed, file=File(map_png_path, filename="map.png"))

                if fatal:
                    await interaction.respond("# Your snake has crashed! You are out of the game.")
                    if gctx.thread:
                        await gctx.thread.send(f"# {team.name}'s snake has crashed! They are out of the game.")
                
                winner = gctx.winner()
                if winner is not None:
                    gctx.won_game()
                    if winner.thread:
                        await winner.thread.send("# You have won the game!")
                    if gctx.thread:
                        await gctx.thread.send(f"# {winner.name} has won the game!")

                if self.parent: # pyright: ignore[reportUnknownMemberType]
                    parent = cast(View, self.parent) # pyright: ignore[reportUnknownMemberType]
                    message = parent.message
                    parent.disable_all_items()
                    if message: await message.edit(view=parent)

        self.add_item(ConfirmButton(label="Confirm", style=ButtonStyle.green, emoji="✅"))